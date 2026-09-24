"""
Roz ka kaam yahin hota hai:
  RSS -> transcript -> English -> summary -> PDF -> Drive -> email -> Sheet
"""

import os
import re
import io
import time
import html
import threading
import datetime as dt
import xml.etree.ElementTree as ET

import requests

import gc
import sys
import traceback

import gapi
import fonts

BUILD = "31"

# -------- API keys: yahan paste kar sakte hain, ya Settings page se bhi chalega
OPENROUTER_API_KEY = ""     # <-- apni OpenRouter key yahan daal sakte hain
SUPADATA_API_KEY = ""       # <-- apni Supadata key yahan daal sakte hain

OPENROUTER_URL = "https://openrouter.ai/api/v1/chat/completions"
MODEL = "openai/gpt-4o-mini"

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/125.0 Safari/537.36")

CELL_LIMIT = 45000          # Google Sheet ki 50,000 wali seema se thoda kam
MAX_ATTEMPTS = 16           # ~2 din tak koshish, phir haath khada

DEFAULTS = {
    "summary_length": "medium",       # short | medium | detailed
    "lookback_days": "5",
    "pdf_public": "yes",
    "email_subject": "{title}",
    "output_instruction": "",
    "output_title": "Summary",
    "output_style": "timeline",     # timeline | summary
    "sms_provider": "",          # "" | fast2sms | twilio
    "fast2sms_key": "",
    "twilio_sid": "",
    "twilio_token": "",
    "twilio_from": "",
    "openrouter_key": "",
    "supadata_key": "",
    "paused": "no",
    "check_every_hours": "3",
    "keep_awake": "yes",
    "supadata_blocked_until": "",
    "proxy_url": "",
    "gemini_key": "",
    "gemini_model": "",
    "attach_pdf": "yes",
    "whatsapp_length": "1200",
}

STATUS = {
    "running": False,
    "step": "",
    "started": "",
    "last_run": "",
    "last_result": "Not run yet.",
    "did_work": False,
}
_run_lock = threading.Lock()


# ------------------------------------------------------------------ helpers

def now_str():
    return dt.datetime.now().strftime("%d %b %Y, %I:%M %p")


def settings(force=False):
    s = dict(DEFAULTS)
    try:
        s.update(gapi.get_settings(force=force))
    except Exception:
        pass
    return s


def openrouter_key(s=None):
    s = s or settings()
    return (os.environ.get("OPENROUTER_API_KEY")
            or s.get("openrouter_key") or OPENROUTER_API_KEY or "").strip()


def supadata_key(s=None):
    s = s or settings()
    return (os.environ.get("SUPADATA_API_KEY")
            or s.get("supadata_key") or SUPADATA_API_KEY or "").strip()


SECRET_RE = re.compile(r"(sk-or-v1-|sk-|sd_|Bearer\s+)[A-Za-z0-9_\-]{8,}")


def hide_keys(text: str) -> str:
    """Koi bhi key galti se log me na chali jaye."""
    return SECRET_RE.sub(lambda m: m.group(1) + "…hidden…", str(text))


def log(level, message):
    try:
        gapi.append_row("Log", [now_str(), level, hide_keys(message)[:2000]])
    except Exception:
        pass
    print(f"[{level}] {hide_keys(message)}", flush=True)


def is_devanagari(text: str) -> bool:
    sample = text[:4000]
    hits = sum(1 for ch in sample if "\u0900" <= ch <= "\u097F")
    return hits > len(sample) * 0.08


def split_text(text: str, max_chars: int):
    """Vaakya tode bina tukde."""
    parts, buf = [], ""
    for piece in re.split(r"(?<=[।.!?\n])\s+", text):
        if len(buf) + len(piece) + 1 > max_chars and buf:
            parts.append(buf.strip())
            buf = piece
        else:
            buf = f"{buf} {piece}".strip()
    if buf.strip():
        parts.append(buf.strip())
    return parts or [""]


# ------------------------------------------------------- Gemini se transcript

GEMINI_API = "https://generativelanguage.googleapis.com/v1beta"
GEMINI_WINDOW = 1200          # 20 minute ke tukde — muft dariye me aa jaate hain
GEMINI_MAX = 5 * 3600         # itne lambe video tak koshish
_gemini_model = {"name": ""}


def gemini_key(s=None):
    s = s or settings()
    return (os.environ.get("GEMINI_API_KEY") or s.get("gemini_key") or "").strip()


def gemini_pick_model(key: str, s=None) -> str:
    """Jo flash model aaj uplabdh ho, wahi chun lo."""
    s = s or {}
    chosen = (s.get("gemini_model") or "").strip()
    if chosen:
        return chosen
    if _gemini_model["name"]:
        return _gemini_model["name"]
    try:
        r = requests.get(f"{GEMINI_API}/models", params={"key": key}, timeout=40)
        r.raise_for_status()
        names = [m.get("name", "").replace("models/", "")
                 for m in r.json().get("models", [])
                 if "generateContent" in (m.get("supportedGenerationMethods") or [])]
        flash = [n for n in names if "flash" in n and "lite" not in n
                 and "thinking" not in n and "image" not in n]
        flash.sort(reverse=True)
        _gemini_model["name"] = flash[0] if flash else (names[0] if names else "")
    except Exception as ex:
        log("wait", f"Gemini: could not list models — {ex}")
    return _gemini_model["name"] or "gemini-2.5-flash"


def transcript_gemini(video_id: str, key: str, s=None):
    """Google ki apni sevaa video ka link seedhe le leti hai — isliye
    YouTube ka rokna yahan lagta hi nahi. Video ko 20-20 minute ke
    tukdon me padha jaata hai, taaki muft dariya paar na ho."""
    if not key:
        raise RuntimeError("No Gemini key set.")
    s = s or settings()
    model = gemini_pick_model(key, s)
    watch = f"https://www.youtube.com/watch?v={video_id}"
    pieces, start, empty = [], 0, 0

    while start < GEMINI_MAX:
        end = start + GEMINI_WINDOW
        prompt = (
            "Transcribe this part of the video word for word, in the language "
            "that is actually spoken. Begin each paragraph with its absolute "
            "timestamp in the whole video, written as [mm:ss] or [h:mm:ss]. "
            f"This part begins {stamp(start)} into the video, so your first "
            "timestamp must be that or later. Start a new paragraph when the "
            "speaker pauses or the subject turns. If this part is past the end "
            "of the video, reply with the single word NOTHING. Output the "
            "transcript only.")
        body = {
            "contents": [{"role": "user", "parts": [
                {"fileData": {"fileUri": watch, "mimeType": "video/*"},
                 "videoMetadata": {"startOffset": f"{start}s",
                                   "endOffset": f"{end}s"}},
                {"text": prompt}]}],
            "generationConfig": {"mediaResolution": "MEDIA_RESOLUTION_LOW",
                                 "temperature": 0, "maxOutputTokens": 8192},
        }
        r = requests.post(f"{GEMINI_API}/models/{model}:generateContent",
                          params={"key": key}, json=body, timeout=600)
        if r.status_code in (401, 403):
            raise RuntimeError(f"Gemini refused ({r.status_code}). Check the key.")
        if r.status_code == 429:
            raise NoCredits("Gemini's free limit for now is used up.")
        if r.status_code >= 400:
            detail = r.text[:200]
            if start == 0:
                raise RuntimeError(f"Gemini {r.status_code}: {detail}")
            break                       # video shayad yahin khatm ho gaya

        try:
            cand = r.json()["candidates"][0]
            text = "".join(p.get("text", "")
                           for p in cand.get("content", {}).get("parts", []))
        except Exception:
            text = ""
        text = text.strip()

        if not text or text.upper().startswith("NOTHING") or len(text) < 80:
            empty += 1
            if empty >= 1:
                break
        else:
            pieces.append(text)
        start = end
        time.sleep(4)

    full = re.sub(r"\n{3,}", "\n\n", "\n".join(pieces)).strip()
    if len(full) < 200:
        raise RuntimeError("Gemini returned almost nothing for this video.")
    return full


# ------------------------------------------------------------ samay ke nishan

# Samay ka nishan wahan lagta hai jahan baat sachmuch badalti hai —
# gine hue tees-tees second par nahi.
SHORT_MIN = 14           # chuppi ke baad tootne ke liye itna to bole
SOFT_BLOCK = 95          # chuppi na mile to itni der baad vaakya par tod do
MAX_BLOCK = 240          # itne se bada tukda kabhi nahi
PAUSE = 1.2              # itni der ki chuppi = baat badalne ka ishaara
BLOCK_SECONDS = 30       # purane tareeke ke liye


def stamp(seconds) -> str:
    try:
        sec = int(float(seconds))
    except Exception:
        sec = 0
    h, rest = divmod(max(sec, 0), 3600)
    m, sc = divmod(rest, 60)
    return f"[{h}:{m:02d}:{sc:02d}]" if h else f"[{m:02d}:{sc:02d}]"


ENDS = ("।", "॥", ".", "?", "!", "|")


def _ends_sentence(text: str) -> bool:
    t = (text or "").rstrip().rstrip('"\'”’)')
    return bool(t) and t[-1] in ENDS


def natural_blocks(segments) -> str:
    """[(shuru, avadhi, baat), ...] -> paragraph, har ek apne samay ke saath.

    Tukda wahin tootta hai jahan vaakya poora hua ho aur ya to kaafi der
    beet chuki ho, ya bolne wale ne saans li ho. Isse har paragraph ek
    poori baat banta hai, aadha-adhoora nahi."""
    rows = []
    for seg in segments:
        if len(seg) == 3:
            st, dur, text = seg
        else:
            st, text = seg
            dur = 0
        text = re.sub(r"\s+", " ", (text or "")).strip()
        if text:
            rows.append((float(st or 0), float(dur or 0), text))
    if not rows:
        return ""

    out, cur, start = [], [], rows[0][0]
    for i, (st, dur, text) in enumerate(rows):
        cur.append(text)
        spent = st + dur - start
        nxt = rows[i + 1][0] if i + 1 < len(rows) else None
        gap = (nxt - (st + dur)) if (nxt is not None and dur) else 0

        done = False
        if _ends_sentence(text):
            if gap >= PAUSE and spent >= SHORT_MIN:
                done = True            # bolne wale ne saans li — baat badli
            elif spent >= SOFT_BLOCK:
                done = True            # chuppi nahi mili, par kaafi der ho gayi
        if spent >= MAX_BLOCK and (_ends_sentence(text) or gap >= 0.5):
            done = True                # itna lamba tukda padhne me bhaari

        if done and nxt is not None:
            out.append(f"{stamp(start)} {' '.join(cur)}")
            cur, start = [], nxt
    if cur:
        out.append(f"{stamp(start)} {' '.join(cur)}")
    return "\n".join(out)


def blocks_from_segments(segments, every=BLOCK_SECONDS) -> str:
    """[(second, text), ...] -> '[00:30] baat...' wali panktiyan."""
    out, cur, start = [], [], None
    for sec, text in segments:
        text = re.sub(r"\s+", " ", (text or "")).strip()
        if not text:
            continue
        if start is None:
            start = sec
        if sec - start >= every and cur:
            out.append(f"{stamp(start)} {' '.join(cur)}")
            cur, start = [], sec
        cur.append(text)
    if cur:
        out.append(f"{stamp(start or 0)} {' '.join(cur)}")
    return "\n".join(out)


STAMP_RE = re.compile(r"^(\[\d{1,2}:\d{2}(?::\d{2})?\])\s*(.*)$")


def split_stamped(text):
    """Nishan wali panktiyon ko (nishan, baat) me alag karo."""
    rows = []
    for line in (text or "").split("\n"):
        line = line.strip()
        if not line:
            continue
        m = STAMP_RE.match(line)
        if m:
            rows.append([m.group(1), m.group(2)])
        elif rows:
            rows[-1][1] += " " + line
        else:
            rows.append(["", line])
    return rows


def has_stamps(text) -> bool:
    return bool(STAMP_RE.match((text or "").strip().split("\n")[0] or ""))


# ------------------------------------------------------------------ SMS

def clean_phone(phone: str) -> dict:
    """+91 98xx-xxxx -> {'local': '98xxxxxxxx', 'e164': '+9198xxxxxxxx'}"""
    raw = re.sub(r"[^\d+]", "", phone or "")
    plus = raw.startswith("+")
    digits = re.sub(r"\D", "", raw)
    if not plus and len(digits) == 11 and digits.startswith("0"):
        digits = digits[1:]
    if not plus and len(digits) == 10:
        digits = "91" + digits
    if plus and digits.startswith("91") and len(digits) == 12:
        pass
    local = digits[-10:] if digits.startswith("91") else digits
    return {"local": local, "e164": "+" + digits, "digits": digits}


def sms_ready(s=None) -> bool:
    s = s or settings()
    p = (s.get("sms_provider") or "").lower()
    if p == "fast2sms":
        return bool(s.get("fast2sms_key"))
    if p == "twilio":
        return all(s.get(k) for k in ("twilio_sid", "twilio_token", "twilio_from"))
    return False


def send_sms(phone: str, text: str, s=None):
    s = s or settings()
    p = (s.get("sms_provider") or "").lower()
    num = clean_phone(phone)
    if not num["digits"] or len(num["digits"]) < 10:
        raise RuntimeError("That mobile number does not look right.")

    if p == "fast2sms":
        key = s.get("fast2sms_key", "").strip()
        if not num["digits"].startswith("91") and len(num["local"]) != 10:
            raise RuntimeError("Fast2SMS only sends to Indian numbers.")
        r = requests.get("https://www.fast2sms.com/dev/bulkV2",
                         params={"authorization": key, "route": "q",
                                 "message": text, "numbers": num["local"],
                                 "flash": "0"}, timeout=40)
        ok = False
        try:
            ok = bool(r.json().get("return"))
        except Exception:
            ok = r.status_code < 400
        if not ok:
            raise RuntimeError(f"Fast2SMS: {r.text[:180]}")
        return

    if p == "twilio":
        sid = s.get("twilio_sid", "").strip()
        r = requests.post(
            f"https://api.twilio.com/2010-04-01/Accounts/{sid}/Messages.json",
            auth=(sid, s.get("twilio_token", "").strip()),
            data={"From": s.get("twilio_from", "").strip(),
                  "To": num["e164"], "Body": text}, timeout=40)
        if r.status_code >= 400:
            raise RuntimeError(f"Twilio: {r.text[:180]}")
        return

    raise RuntimeError("SMS is not set up. Add a provider in Settings.")


# ------------------------------------------------------------------ channel

def resolve_channel(text: str):
    """Channel ka link, @handle ya ID -> (channel_id, naam)."""
    text = (text or "").strip()
    m = re.search(r"(UC[\w-]{22})", text)
    if m:
        cid = m.group(1)
        return cid, channel_title(cid) or cid

    url = text
    if text.startswith("@"):
        url = f"https://www.youtube.com/{text}"
    elif not text.startswith("http"):
        url = f"https://www.youtube.com/@{text.lstrip('@')}"

    try:
        r = requests.get(url, headers={"User-Agent": UA,
                                       "Accept-Language": "en-US,en;q=0.9"}, timeout=25)
        r.raise_for_status()
    except Exception:
        return resolve_channel_supadata(url, supadata_key())
    m = (re.search(r'"channelId":"(UC[\w-]{22})"', r.text)
         or re.search(r'"externalId":"(UC[\w-]{22})"', r.text)
         or re.search(r'channel/(UC[\w-]{22})', r.text))
    if not m:
        try:
            return resolve_channel_supadata(url, supadata_key())
        except Exception:
            raise RuntimeError("Could not find the channel ID from that link. Open the channel on YouTube, "
                               "use 'Share channel' > 'Copy channel ID' to get the "
                               "UC... id, and paste that here.")
    cid = m.group(1)
    name = ""
    t = re.search(r'<meta property="og:title" content="([^"]+)"', r.text)
    if t:
        name = html.unescape(t.group(1))
    return cid, name or channel_title(cid) or cid


EP_PATTERNS = [
    r"(?:episode|epi|ep|part|pt)\s*[-–—:.#]?\s*(\d{1,4})\b",
    r"(?:\u090f\u092a\u093f\u0938\u094b\u0921|\u092d\u093e\u0917|\u0905\u0902\u0915|"
    r"\u0916\u0902\u0921|\u0905\u0927\u094d\u092f\u093e\u092f)\s*[-–—:.#]?\s*(\d{1,4})\b",
    r"#\s*(\d{1,4})\b",
    r"\|\s*(\d{1,4})\s*\|",
    r"\b(\d{1,4})\s*(?:\u0935\u093e\u0901|\u0935\u093e\u0902)\b",
]


def episode_from_title(title: str) -> str:
    """Video ke naam me jo episode number ho, wahi. Na mile to khaali."""
    t = (title or "").strip()
    for pat in EP_PATTERNS:
        m = re.search(pat, t, re.IGNORECASE)
        if m:
            return m.group(1)
    return ""


def channel_title(cid: str) -> str:
    try:
        feed = fetch_feed(cid)
        return feed.get("channel", "")
    except Exception:
        return ""


def fetch_feed(cid: str) -> dict:
    """Pehle YouTube ka muft RSS. Wo mana kar de to Supadata se poochh lenge."""
    try:
        return fetch_feed_rss(cid)
    except Exception as rss_error:
        try:
            return fetch_feed_supadata(cid, supadata_key())
        except Exception as sup_error:
            raise RuntimeError(f"{rss_error} | Supadata: {sup_error}")


def fetch_feed_rss(cid: str) -> dict:
    url = f"https://www.youtube.com/feeds/videos.xml?channel_id={cid}"
    r = requests.get(url, headers={"User-Agent": UA}, timeout=25)
    if r.status_code == 404:
        raise RuntimeError(
            f"YouTube does not know the channel id {cid}. On YouTube open the "
            "channel, use Share channel > Copy channel ID, and add it again.")
    r.raise_for_status()
    root = ET.fromstring(r.content)
    ns = {"a": "http://www.w3.org/2005/Atom",
          "yt": "http://www.youtube.com/xml/schemas/2015",
          "media": "http://search.yahoo.com/mrss/"}
    channel = (root.findtext("a:title", default="", namespaces=ns) or "").strip()
    videos = []
    for e in root.findall("a:entry", ns):
        vid = e.findtext("yt:videoId", default="", namespaces=ns)
        if not vid:
            continue
        published = e.findtext("a:published", default="", namespaces=ns)
        try:
            when = dt.datetime.fromisoformat(published.replace("Z", "+00:00"))
        except Exception:
            when = dt.datetime.now(dt.timezone.utc)
        videos.append({
            "video_id": vid,
            "title": (e.findtext("a:title", default="", namespaces=ns) or "").strip(),
            "published": when,
            "link": f"https://www.youtube.com/watch?v={vid}",
        })
    return {"channel": channel, "videos": videos}


def _sup_get(path, params, key, timeout=60):
    if not key:
        raise RuntimeError("No Supadata key set.")
    r = requests.get(f"https://api.supadata.ai{path}", params=params,
                     headers={"x-api-key": key}, timeout=timeout)
    if r.status_code >= 400:
        raise RuntimeError(f"Supadata {r.status_code}: {r.text[:160]}")
    return r.json()


def fetch_feed_supadata(cid: str, key: str) -> dict:
    data = _sup_get("/v1/youtube/channel/videos", {"id": cid, "limit": 10}, key)
    ids = data.get("videoIds") or data.get("videos") or data.get("shortVideoIds") or []
    ids = [v.get("id") if isinstance(v, dict) else v for v in ids][:10]
    channel_name = ""
    try:
        ch = _sup_get("/v1/youtube/channel", {"id": cid}, key)
        channel_name = ch.get("name") or ch.get("title") or ""
    except Exception:
        pass
    videos = []
    for vid in ids:
        try:
            v = _sup_get("/v1/youtube/video", {"id": vid}, key)
        except Exception:
            continue
        published = v.get("uploadDate") or v.get("publishedAt") or ""
        try:
            when = dt.datetime.fromisoformat(str(published).replace("Z", "+00:00"))
        except Exception:
            when = dt.datetime.now(dt.timezone.utc)
        if when.tzinfo is None:
            when = when.replace(tzinfo=dt.timezone.utc)
        videos.append({
            "video_id": vid,
            "title": v.get("title", "") or vid,
            "published": when,
            "link": f"https://www.youtube.com/watch?v={vid}",
        })
    if not videos:
        raise RuntimeError("Supadata returned no videos either.")
    videos.sort(key=lambda x: x["published"], reverse=True)
    return {"channel": channel_name, "videos": videos}


def resolve_channel_supadata(text: str, key: str):
    data = _sup_get("/v1/youtube/channel", {"id": text}, key)
    cid = data.get("id") or data.get("channelId") or ""
    if not cid.startswith("UC"):
        raise RuntimeError("Channel ID not found.")
    return cid, data.get("name") or data.get("title") or cid


def extract_video_id(text: str) -> str:
    """Kisi bhi YouTube link se video id."""
    text = (text or "").strip()
    if re.fullmatch(r"[\w-]{11}", text):
        return text
    m = (re.search(r"[?&]v=([\w-]{11})", text)
         or re.search(r"youtu\.be/([\w-]{11})", text)
         or re.search(r"/(?:shorts|live|embed)/([\w-]{11})", text))
    return m.group(1) if m else ""


def video_meta(video_id: str) -> dict:
    """Title aur channel — pehle YouTube ka muft oEmbed, phir Supadata."""
    title, channel = "", ""
    try:
        r = requests.get("https://www.youtube.com/oembed",
                         params={"url": f"https://www.youtube.com/watch?v={video_id}",
                                 "format": "json"},
                         headers={"User-Agent": UA}, timeout=20)
        if r.status_code < 400:
            j = r.json()
            title = j.get("title", "")
            channel = j.get("author_name", "")
    except Exception:
        pass
    if not title:
        try:
            v = _sup_get("/v1/youtube/video", {"id": video_id}, supadata_key())
            title = v.get("title", "")
            channel = (v.get("channel") or {}).get("name", "") if isinstance(
                v.get("channel"), dict) else v.get("channelName", "")
        except Exception:
            pass
    return {
        "video_id": video_id,
        "title": title or video_id,
        "channel": channel or "Added by link",
        "published": dt.datetime.now(dt.timezone.utc),
        "link": f"https://www.youtube.com/watch?v={video_id}",
    }


# ------------------------------------------------------------------ transcript

def transcript_direct(video_id: str, proxy: str = ""):
    """Muft koshish. Proxy diya ho to uske raaste, warna seedhe.
    Cloud se YouTube aksar rok deta hai — isliye chup-chaap fail hone denge."""
    try:
        from youtube_transcript_api import YouTubeTranscriptApi
        try:
            if proxy:
                from youtube_transcript_api.proxies import GenericProxyConfig
                api = YouTubeTranscriptApi(
                    proxy_config=GenericProxyConfig(http_url=proxy, https_url=proxy))
            else:
                api = YouTubeTranscriptApi()
            fetched = api.fetch(video_id)
            snippets = getattr(fetched, "snippets", fetched)
            segs = []
            for sn in snippets:
                if isinstance(sn, dict):
                    segs.append((sn.get("start", 0), sn.get("duration", 0),
                                 sn.get("text", "")))
                else:
                    segs.append((getattr(sn, "start", 0),
                                 getattr(sn, "duration", 0),
                                 getattr(sn, "text", "")))
            text = natural_blocks(segs)
        except Exception:
            data = YouTubeTranscriptApi.get_transcript(video_id)
            text = natural_blocks([(d.get("start", 0), d.get("duration", 0),
                                    d["text"]) for d in data])
        return text if len(text) > 200 else None
    except Exception:
        return None


class NoCredits(RuntimeError):
    """Supadata ke credits khatam — ab is baar koshish ka koi fayda nahi."""


class NotReady(RuntimeError):
    """Video abhi live chal raha hai. Prasaran khatam hone par hi
    transcript banega — isliye ye nakami ginati me nahi aati."""


def credits_blocked_until(s=None):
    s = s or settings()
    raw = (s.get("supadata_blocked_until") or "").strip()
    if not raw:
        return None
    try:
        when = dt.datetime.fromisoformat(raw)
    except Exception:
        return None
    return when if when > dt.datetime.now() else None


def block_credits(hours=12):
    until = dt.datetime.now() + dt.timedelta(hours=hours)
    try:
        gapi.set_settings({"supadata_blocked_until": until.isoformat(timespec="minutes")})
    except Exception:
        pass
    return until


def transcript_supadata(video_id: str, key: str):
    """Caption ho to caption, na ho to Supadata khud audio se bana deta hai."""
    if not key:
        raise RuntimeError("No Supadata key in Settings.")
    headers = {"x-api-key": key}
    watch = f"https://www.youtube.com/watch?v={video_id}"
    attempts = [
        ("https://api.supadata.ai/v1/transcript", {"url": watch, "mode": "auto"}),
        ("https://api.supadata.ai/v1/youtube/transcript", {"videoId": video_id}),
        ("https://api.supadata.ai/v1/transcript", {"url": watch, "text": "true",
                                                  "mode": "auto"}),
    ]
    last_error = ""
    for url, params in attempts:
        try:
            r = requests.get(url, headers=headers, params=params, timeout=90)
        except Exception as e:
            last_error = str(e)
            continue
        if r.status_code in (401, 403):
            raise RuntimeError(f"Supadata refused ({r.status_code}). Check the key.")
        if r.status_code == 429:
            raise NoCredits("Supadata credits for this month are used up.")
        if r.status_code >= 400:
            body = r.text[:300]
            if "live stream" in body.lower() or "currently live" in body.lower():
                raise NotReady("This video is still live. Waiting for the stream "
                               "to end.")
            last_error = f"{r.status_code} {body[:200]}"
            continue
        data = r.json()
        job = data.get("jobId") or data.get("job_id")
        if job:
            data = supadata_wait(job, headers)
        text = supadata_text(data)
        if text:
            return text
        last_error = str(data)[:200]
    raise RuntimeError(f"No transcript. {last_error}")


def supadata_wait(job_id: str, headers, max_wait=600):
    url = f"https://api.supadata.ai/v1/transcript/{job_id}"
    waited = 0
    while waited < max_wait:
        time.sleep(10)
        waited += 10
        r = requests.get(url, headers=headers, timeout=60)
        if r.status_code >= 400:
            continue
        data = r.json()
        status = (data.get("status") or "").lower()
        if status in ("completed", "complete", "succeeded", "done") or data.get("content"):
            return data
        if status in ("failed", "error"):
            raise RuntimeError(f"Supadata job failed: {data.get('error', '')}")
    raise RuntimeError("Supadata took too long to answer.")


def supadata_text(data) -> str:
    """Tukde milen to samay ke nishan ke saath, warna saada text."""
    if isinstance(data, str):
        return re.sub(r"\s+", " ", data).strip()
    content = data.get("content") or data.get("text") or data.get("transcript")
    if isinstance(content, list):
        segs = []
        for c in content:
            if isinstance(c, dict):
                off = float(c.get("offset", c.get("start", c.get("startMs", 0))) or 0)
                dur = float(c.get("duration", c.get("durationMs", 0)) or 0)
                if off > 10000:            # milliseconds
                    off, dur = off / 1000.0, dur / 1000.0
                elif dur > 1000:
                    dur = dur / 1000.0
                segs.append((off, dur, c.get("text", "")))
            else:
                segs.append((0, 0, str(c)))
        if segs and any(x[0] for x in segs):
            return natural_blocks(segs)
        content = " ".join(x[2] for x in segs)
    if not content:
        return ""
    return re.sub(r"\s+", " ", str(content)).strip()


def get_transcript(video_id: str, s=None):
    """Teen raaste, isi kram me:
       1. Aapke PC wale helper ne jo bhej diya ho (muft, bina seema)
       2. Seedhe YouTube se — proxy diya ho to uske raaste (sasta, bina seema)
       3. Supadata (mahine ke 100 muft)"""
    s = s or settings()
    try:
        if video_id in gapi.inbox_ids():
            text = gapi.inbox_get(video_id)
            if len(text) > 200:
                return text, "your PC"
    except Exception:
        pass

    proxy = (s.get("proxy_url") or "").strip()
    text = transcript_direct(video_id, proxy)
    if text:
        return text, ("proxy" if proxy else "YouTube")

    troubles = []
    gkey = gemini_key(s)
    if gkey:
        try:
            return transcript_gemini(video_id, gkey, s), "Gemini"
        except NoCredits:
            troubles.append("Gemini's free limit is used up for now")
        except Exception as ex:
            troubles.append(f"Gemini: {ex}")

    key = supadata_key(s)
    if key:
        try:
            return transcript_supadata(video_id, key), "Supadata"
        except NoCredits:
            raise
        except Exception as ex:
            troubles.append(f"Supadata: {ex}")

    if troubles:
        raise RuntimeError(" | ".join(troubles)[:400])
    raise RuntimeError("No transcript yet. Add a Gemini key, run your PC helper, "
                       "or set a proxy.")


# ------------------------------------------------------------------ OpenRouter

def llm(system: str, user: str, key: str, max_tokens=4000, tries=3):
    payload = {
        "model": MODEL,
        "max_tokens": max_tokens,
        "temperature": 0.2,
        "messages": [{"role": "system", "content": system},
                     {"role": "user", "content": user}],
    }
    headers = {"Authorization": f"Bearer {key}", "Content-Type": "application/json"}
    last = ""
    for i in range(tries):
        try:
            r = requests.post(OPENROUTER_URL, headers=headers, json=payload, timeout=180)
            if r.status_code == 429:
                time.sleep(15 * (i + 1))
                last = "OpenRouter asked us to slow down (429)."
                continue
            if r.status_code >= 400:
                last = f"OpenRouter {r.status_code}: {r.text[:200]}"
                time.sleep(5)
                continue
            data = r.json()
            return data["choices"][0]["message"]["content"].strip()
        except Exception as e:
            last = str(e)
            time.sleep(5)
    raise RuntimeError(f"No answer from OpenRouter. {last}")


def to_english_stamped(text: str, key: str, on_step=None) -> str:
    """Samay ke nishan jaise ke waise, sirf baat angrezi me."""
    rows = split_stamped(text)
    system = ("You translate spoken-word transcripts into clear, natural English. "
              "You are given numbered lines. Return the SAME line numbers in the "
              "same order, one per line, in the form 'N| translated text'. "
              "Translate every line faithfully and completely. Do not merge lines, "
              "do not drop lines, do not add commentary.")
    out = list(rows)
    batch, start, done = [], 0, 0
    total = len(rows)

    def run(batch, start):
        if not batch:
            return
        body = "\n".join(f"{start + i + 1}| {t}" for i, t in enumerate(batch))
        reply = llm(system, body, key, max_tokens=8000)
        got = {}
        for line in reply.split("\n"):
            m = re.match(r"\s*(\d+)\s*\|\s*(.*)$", line)
            if m:
                got[int(m.group(1))] = m.group(2).strip()
        for i in range(len(batch)):
            n = start + i + 1
            if got.get(n):
                out[n - 1] = [rows[n - 1][0], got[n]]

    for i, (mark, body) in enumerate(rows):
        batch.append(body)
        if sum(len(b) for b in batch) > 6000 or i == total - 1:
            done += len(batch)
            if on_step:
                on_step(f"translating to English ({done}/{total} lines)")
            run(batch, i - len(batch) + 1)
            batch = []
            gc.collect()
    return "\n".join(f"{m} {t}".strip() for m, t in out)


def to_english(text: str, key: str, on_step=None) -> str:
    if not is_devanagari(text):
        return text
    if has_stamps(text):
        try:
            return to_english_stamped(text, key, on_step=on_step)
        except Exception as ex:
            log("wait", f"timed translation failed, falling back — {ex}")
    chunks = split_text(text, 9000)
    out = []
    system = ("You translate spoken-word transcripts into clear, natural English. "
              "Translate everything faithfully and completely — do not summarise, "
              "do not skip lines, do not add commentary. Keep proper nouns as they are. "
              "Write flowing paragraphs with normal punctuation. Output only the translation.")
    total = len(chunks)
    for i in range(total):
        if on_step:
            on_step(f"translating to English ({i + 1}/{total})")
        out.append(llm(system, chunks[i], key, max_tokens=8000))
        chunks[i] = ""
        gc.collect()
    return "\n\n".join(out)


DEFAULT_TASK = ("Write a clear English summary of this talk. Plain prose, no "
                "marketing language. Cover the main themes, the line of reasoning, "
                "and anything notable that was said, for someone who will not "
                "watch the video.")


TIMELINE_SYSTEM = (
    "You write a detailed timestamp summary of a recorded talk. You are given a "
    "transcript in which each paragraph begins with its start time, like [00:16].\n\n"
    "Produce numbered sections. Each section is exactly two parts:\n"
    "  a heading line:  N. START-END - Short headline\n"
    "  then one paragraph of 2 to 5 sentences saying what was actually said.\n\n"
    "Rules: take the times from the transcript itself; START is the start time of "
    "the first paragraph in the section and END is the start time of the next "
    "section. Write times as m:ss, or h:mm:ss past an hour. Join neighbouring "
    "paragraphs that belong to the same point, so each section is one topic — "
    "usually 20 seconds to 3 minutes. Write in plain English. Attribute claims to "
    "the speaker rather than stating them as fact. Invent nothing. Output the "
    "sections only, with a blank line between them.")


def make_timeline(text: str, title: str, key: str, on_step=None) -> str:
    """Samay ki seema, uska sheershak, aur us hisse ki baat."""
    chunks = split_text(text, 24000)
    parts, total = [], len(chunks)
    for i in range(total):
        if on_step:
            on_step(f"laying out the timeline ({i + 1}/{total})")
        tail = ("\n\nThis is part %d of %d — keep going from where the previous "
                "part ended, and do not repeat it." % (i + 1, total)) if total > 1 else ""
        parts.append(llm(TIMELINE_SYSTEM,
                         f"Title: {title}{tail}\n\nTranscript:\n{chunks[i]}",
                         key, max_tokens=6000))
        chunks[i] = ""
        gc.collect()

    # sab tukdon ke number ek hi kram me
    out, n = [], 0
    for block in parts:
        for line in block.split("\n"):
            m = re.match(r"\s*\d+[.)]\s*(.*)$", line)
            if m and re.match(r"\s*\d", m.group(1)):
                n += 1
                out.append(f"{n}. {m.group(1).strip()}")
            else:
                out.append(line.rstrip())
    return re.sub(r"\n{3,}", "\n\n", "\n".join(out)).strip()


def make_output(text: str, title: str, length: str, key: str,
                instruction: str = "", on_step=None, style: str = "") -> str:
    """Default me summary. Instruction di ho to wahi kaam hota hai —
    mukhya bindu, notes, sawaal-jawaab, lekh, jo bhi kaha jaye."""
    task = (instruction or "").strip()
    if not task and style == "timeline" and has_stamps(text):
        return make_timeline(text, title, key, on_step=on_step)
    task = task or DEFAULT_TASK
    if has_stamps(text):
        text = " ".join(t for _, t in split_stamped(text))
    target = {"short": "about 150 words",
              "medium": "about 350 words",
              "detailed": "about 700 words"}.get(length, "about 350 words")
    system = ("You work on transcripts of spoken talks and produce exactly what "
              "the user asks for, in English. Follow the user's instruction "
              "closely — its wording decides the form, the focus and the tone of "
              "what you write. Never add commentary about the instruction itself.")
    chunks = split_text(text, 30000)
    if len(chunks) == 1:
        if on_step:
            on_step("writing")
        return llm(system,
                   f"Title: {title}\n\nInstruction: {task}\n"
                   f"Length: aim for {target} unless the instruction says otherwise."
                   f"\n\nTranscript:\n{chunks[0]}", key, max_tokens=3500)
    notes = []
    total = len(chunks)
    for i in range(total):
        if on_step:
            on_step(f"reading ({i + 1}/{total})")
        notes.append(llm(system,
                         f"Part {i + 1} of {total} of a transcript. The final task "
                         f"will be: {task}\n\nList everything from this part that "
                         f"the final task will need.\n\n{chunks[i]}",
                         key, max_tokens=1500))
        chunks[i] = ""
        gc.collect()
    if on_step:
        on_step("putting it together")
    return llm(system,
               f"Title: {title}\n\nInstruction: {task}\n"
               f"Length: aim for {target} unless the instruction says otherwise."
               f"\n\nNotes from the full transcript:\n\n" + "\n\n".join(notes),
               key, max_tokens=3500)


# ------------------------------------------------------------------ PDF

def esc(t: str) -> str:
    """Har lipi ka apna font lag jaaye — hindi, gujarati, tamil, jo bhi."""
    return fonts.markup(t)


def where_it_broke(limit=4) -> str:
    """Galti thik kis pankti par hui — Logs me saaf dikhe."""
    try:
        frames = traceback.extract_tb(sys.exc_info()[2])[-limit:]
        return " <- ".join(f"{f.filename.split('/')[-1]}:{f.lineno} {f.name}()"
                           for f in reversed(frames))
    except Exception:
        return ""


def free_memory():
    """Jo chhoda ja sakta hai, use OS ko wapas kar do."""
    gc.collect()
    try:
        import ctypes
        ctypes.CDLL("libc.so.6").malloc_trim(0)
    except Exception:
        pass


def memory_mb():
    try:
        with open("/proc/self/status") as f:
            for line in f:
                if line.startswith("VmRSS:"):
                    return round(int(line.split()[1]) / 1024)
    except Exception:
        pass
    return 0


def build_pdf(title, channel, date_str, link, episode, summary, transcript,
              heading="Summary") -> bytes:
    from reportlab.lib.pagesizes import A4
    from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
    from reportlab.lib.units import mm
    from reportlab.lib import colors
    from reportlab.platypus import (SimpleDocTemplate, Paragraph, Spacer,
                                    PageBreak, HRFlowable)
    buf = io.BytesIO()
    doc = SimpleDocTemplate(
        buf, pagesize=A4,
        leftMargin=22 * mm, rightMargin=22 * mm,
        topMargin=20 * mm, bottomMargin=18 * mm,
        title=title, author="YouTube Brief Desk",
    )
    base = getSampleStyleSheet()
    ink = colors.HexColor("#1C2333")
    grey = colors.HexColor("#5C6472")
    green = colors.HexColor("#1F6F5C")

    st_title = ParagraphStyle("t", parent=base["Title"], fontName="Times-Bold",
                              fontSize=19, leading=24, textColor=ink, alignment=0,
                              spaceAfter=4)
    st_meta = ParagraphStyle("m", parent=base["Normal"], fontSize=9.5, leading=14,
                             textColor=grey, spaceAfter=2)
    st_head = ParagraphStyle("h", parent=base["Heading2"], fontName="Times-Bold",
                             fontSize=13.5, leading=18, textColor=green,
                             spaceBefore=14, spaceAfter=7)
    st_body = ParagraphStyle("b", parent=base["Normal"], fontName="Times-Roman",
                             fontSize=11, leading=16.5, textColor=ink,
                             spaceAfter=9, alignment=4)

    st_sec = ParagraphStyle("sec", parent=base["Normal"], fontName="Times-Bold",
                            fontSize=11, leading=15.5, textColor=ink,
                            spaceBefore=12, spaceAfter=4)
    SEC_RE = re.compile(r"^\s*(\d+)[.)]\s*"
                        r"([\d:]+\s*[\u2013\u2014-]\s*[\d:]+)?\s*"
                        r"[\u2013\u2014-]?\s*(.*)$")

    story = [Paragraph(esc(title), st_title)]
    meta = f"{channel} &nbsp;·&nbsp; {date_str}"
    if episode:
        meta = f"Episode {episode} &nbsp;·&nbsp; {meta}"
    story += [
        Paragraph(meta, st_meta),
        Paragraph(f'<link href="{esc(link)}" color="#1F6F5C">{esc(link)}</link>', st_meta),
        Spacer(1, 6),
        HRFlowable(width="100%", thickness=0.7, color=colors.HexColor("#DBD8D0")),
        Paragraph(heading, st_head),
    ]
    for para in [p for p in summary.split("\n") if p.strip()]:
        para = para.strip()
        m = SEC_RE.match(para)
        if m and m.group(2):
            num, span, head = m.group(1), m.group(2).strip(), m.group(3).strip()
            story.append(Paragraph(
                f'{num}.&nbsp;&nbsp;<font color="#1F6F5C">{esc(span)}</font>'
                f'&nbsp;&nbsp;{esc(head)}', st_sec))
        else:
            story.append(Paragraph(esc(para), st_body))

    story += [PageBreak(), Paragraph("Full transcript", st_head)]
    if has_stamps(transcript):
        st_line = ParagraphStyle("tl", parent=st_body, spaceAfter=6, leftIndent=0)
        for mark, body in split_stamped(transcript):
            if not body.strip():
                continue
            for i, piece in enumerate(split_text(body.strip(), 3500)):
                tag = (f'<font name="Courier-Bold" size="8" color="#1F6F5C">'
                       f'{esc(mark)}</font>&nbsp;&nbsp;' if i == 0 and mark else "")
                story.append(Paragraph(tag + esc(piece), st_line))
    else:
        for para in [p for p in re.split(r"\n{1,}", transcript) if p.strip()]:
            for piece in split_text(para.strip(), 3500):
                story.append(Paragraph(esc(piece), st_body))

    doc.build(story)
    return buf.getvalue()


# ------------------------------------------------------------------ state tab

def state_map():
    out = {}
    for row in gapi.read_all(force=True)["state"]:
        if row.get("Video ID"):
            out[row["Video ID"]] = row
    return out


def bump_state(video_id, error, existing):
    row = existing.get(video_id)
    attempts = int(row.get("Attempts") or 0) + 1 if row else 1
    values = [[video_id, attempts, str(error)[:500], now_str()]]
    if row:
        gapi.write_range("State", f"A{row['_row']}:D{row['_row']}", values)
    else:
        gapi.append_row("State", values[0])
    return attempts


def clear_state(video_id, existing):
    row = existing.get(video_id)
    if row:
        gapi.write_range("State", f"A{row['_row']}:D{row['_row']}",
                         [[video_id, "done", "", now_str()]])


def destination(row, data, default_tab="Episodes"):
    """(spreadsheet id, tab) — naam se registry me dhoondh kar."""
    name = (row.get("Spreadsheet") or "").strip()
    tab = (row.get("Sheet tab") or default_tab).strip() or default_tab
    sid = ""
    if name and name.lower() != gapi.MAIN.lower():
        for sh in data.get("sheets", []):
            if (sh.get("Name") or "").strip().lower() == name.lower():
                sid = (sh.get("Spreadsheet ID") or "").strip()
                break
        if not sid:
            raise RuntimeError(f"Spreadsheet '{name}' is not in the Sheets list.")
    gapi.ensure_tab_in(sid, tab)
    return sid, tab


# ------------------------------------------------------------------ one video

def process_video(video, channel_name, s, recipients, episode_no, serial_no,
                  tab="Episodes", instruction=None, sid="", replace_row=0):
    def step(msg):
        STATUS["step"] = f"{video['title'][:50]} — {msg} [{memory_mb()} MB]"

    def mark(msg):
        log("mem", f"{memory_mb()} MB — {msg} — {video['title'][:34]}")

    key = openrouter_key(s)
    if not key:
        raise RuntimeError("Add your OpenRouter key in Settings.")

    step("fetching transcript")
    raw, source = get_transcript(video["video_id"], s)
    mark(f"transcript {len(raw) // 1000}k chars")

    step("translating to English")
    english = to_english(raw, key, on_step=lambda m: step(m))
    del raw
    gc.collect()
    mark(f"english {len(english) // 1000}k chars")

    task = instruction if instruction is not None else s.get("output_instruction", "")
    heading = (s.get("output_title") or "Summary").strip() or "Summary"
    step("writing")
    style = s.get("output_style", "timeline")
    if not (s.get("output_title") or "").strip():
        heading = ("Detailed Timestamp Summary" if style == "timeline" else "Summary")
    summary = make_output(english, video["title"], s.get("summary_length", "medium"),
                          key, instruction=task, on_step=lambda m: step(m),
                          style=style)
    gc.collect()
    mark("after writing")

    date_local = video["published"].astimezone()
    date_str = date_local.strftime("%d %b %Y")

    step("writing to the Sheet")
    sheet_transcript = english
    if len(english) > CELL_LIMIT:
        parts = [english[i:i + CELL_LIMIT] for i in range(0, len(english), CELL_LIMIT)]
        sheet_transcript = parts[0] + "\n\n[… rest is in the Overflow tab; the full text is in the PDF]"
        try:
            gapi.ensure_tab_in(sid, "Overflow") if sid else None
            gapi.append_rows_in(sid, "Overflow",
                                [[video["video_id"], i + 1, p]
                                 for i, p in enumerate(parts[1:], 1)])
        except Exception:
            pass
        del parts
        gc.collect()

    step("building PDF")
    pdf = build_pdf(video["title"], channel_name, date_str, video["link"],
                    episode_no, summary, english, heading=heading)
    del english
    gc.collect()
    mark(f"pdf {len(pdf) // 1024} KB")

    step("saving to Drive")
    safe = re.sub(r"[^\w\s-]", "", video["title"], flags=re.UNICODE)[:70].strip() \
        or video["video_id"]
    fname = f"{date_local.strftime('%Y-%m-%d')} - {safe}.pdf"
    up = gapi.upload_pdf(fname, pdf, date_local.strftime("%Y-%m"),
                         public=s.get("pdf_public", "yes") == "yes")

    step("sending email")
    try:
        subject = s.get("email_subject", "{title}").format(
            title=video["title"], channel=channel_name, date=date_str,
            episode=episode_no)
    except Exception:
        subject = video["title"]
    body = (f"{video['title']}\n{channel_name} · {date_str}\n{video['link']}\n\n"
            f"{heading}\n\n{summary}\n\n"
            + ("Full transcript is in the attached PDF.\n"
               if s.get("attach_pdf", "yes") != "no"
               else f"Full transcript: {up['link']}\n"))
    attach = s.get("attach_pdf", "yes") != "no"
    if recipients and not replace_row:
        gapi.send_mail(recipients, subject, body,
                       attachment=pdf if attach else None,
                       attachment_name=fname)
    log("mem", f"{memory_mb()} MB — {video['title'][:40]}")

    del pdf
    gc.collect()
    mark("done")

    row_values = [
        serial_no, date_str, episode_no, video["link"], up["link"],
        sheet_transcript, summary, video["title"], channel_name,
        video["video_id"], ", ".join(recipients),
    ]
    if replace_row:
        gapi.update_row_in(sid, tab, replace_row, row_values)
    else:
        gapi.append_row_in(sid, tab, row_values)
    log("ok", f"Sent: {video['title']} (transcript via {source})")
    return up["link"]


def videos_needing_transcript(limit=6):
    """Helper ke liye: kin videos ka transcript abhi chahiye."""
    data = gapi.read_all(force=True)
    s = dict(DEFAULTS)
    s.update(data["settings"])
    have = {e.get("Video ID") for rs in data["rows"].values() for e in rs}
    try:
        have |= gapi.inbox_ids()
    except Exception:
        pass

    want = []
    for q in data.get("queue", []):
        if q.get("Video Link") and (q.get("Status") or "").lower() == "pending":
            vid = extract_video_id(q["Video Link"])
            if vid and vid not in have:
                want.append({"id": vid, "title": q["Video Link"]})

    cutoff = dt.datetime.now(dt.timezone.utc) - dt.timedelta(
        days=int(s.get("lookback_days", "5") or 5))
    for ch in data["channels"]:
        if not ch.get("Channel ID") or ch.get("Active", "yes") == "no":
            continue
        try:
            feed = fetch_feed(ch["Channel ID"].strip())
        except Exception:
            continue
        for v in feed["videos"]:
            if v["published"] >= cutoff and v["video_id"] not in have:
                want.append({"id": v["video_id"], "title": v["title"]})
        if len(want) >= limit:
            break
    seen, out = set(), []
    for w in want:
        if w["id"] not in seen:
            seen.add(w["id"])
            out.append(w)
    return out[:limit]


# ------------------------------------------------------------------ main run

def run_check(manual=False):
    if not _run_lock.acquire(blocking=False):
        return "A run is already in progress."
    STATUS.update({"running": True, "step": "starting",
                   "started": now_str()})
    done, failed, skipped = 0, 0, 0
    STATUS["did_work"] = False
    try:
        gapi.ensure_tabs()
        data = gapi.read_all(force=True)
        s = dict(DEFAULTS)
        s.update(data["settings"])
        if s.get("paused") == "yes" and not manual:
            STATUS["last_result"] = "Paused in Settings."
            return STATUS["last_result"]

        blocked = credits_blocked_until(s)
        if blocked:
            STATUS["last_result"] = (
                f"Waiting — Supadata credits are used up. Will try again after "
                f"{blocked.strftime('%d %b, %I:%M %p')}.")
            return STATUS["last_result"]

        channels = [c for c in data["channels"]
                    if c.get("Channel ID") and c.get("Active", "yes") != "no"]
        recipients = [r["Email"].strip() for r in data["recipients"]
                      if r.get("Email") and r.get("Active", "yes") != "no"]
        if not channels:
            STATUS["last_result"] = "No channels added."
            return STATUS["last_result"]

        all_rows = [r for rs in data["rows"].values() for r in rs]
        for sh in data.get("sheets", []):
            sid2 = (sh.get("Spreadsheet ID") or "").strip()
            if not sid2:
                continue
            for t in (gapi.tabs_in(sid2) if sid2 else []):
                if t in gapi.SYSTEM_TABS:
                    continue
                try:
                    all_rows += gapi.rows_in(sid2, t)
                except Exception:
                    pass
        seen = {e.get("Video ID") for e in all_rows if e.get("Video ID")}
        per_channel = {}
        for e in all_rows:
            ch = e.get("Channel", "")
            per_channel[ch] = max(per_channel.get(ch, 0),
                                  int(e.get("Episode") or 0) if str(e.get("Episode", "")).isdigit() else 0)

        states = {r["Video ID"]: r for r in data["state"] if r.get("Video ID")}
        lookback = int(s.get("lookback_days", "5") or 5)
        cutoff = dt.datetime.now(dt.timezone.utc) - dt.timedelta(days=lookback)

        for ch in channels:
            cid = ch["Channel ID"].strip()
            name = ch.get("Name") or cid
            try:
                ch_sid, target = destination(ch, data, default_tab="Episodes")
            except Exception as ex:
                log("error", f"{name}: {ex}")
                failed += 1
                continue
            STATUS["step"] = f"checking {name}"
            try:
                feed = fetch_feed(cid)
            except Exception as e:
                log("error", f"{name}: could not read the feed — {e}")
                failed += 1
                continue

            fresh = [v for v in feed["videos"]
                     if v["published"] >= cutoff and v["video_id"] not in seen]
            fresh.sort(key=lambda v: v["published"])

            for v in fresh:
                if memory_mb() > 320:
                    STATUS["step"] = "memory is high — pausing until the next round"
                    log("wait", f"{memory_mb()} MB — skipping the rest this round")
                    break
                if done >= 1:
                    STATUS["step"] = "one video done, the rest next time"
                    break
                st = states.get(v["video_id"])
                if st and str(st.get("Attempts")) == "done":
                    continue
                if st and str(st.get("Attempts", "0")).isdigit() \
                        and int(st["Attempts"]) >= MAX_ATTEMPTS:
                    skipped += 1
                    continue
                try:
                    serial = len(gapi.rows_in(ch_sid, target)) + 1
                    ep = episode_from_title(v["title"])
                    process_video(v, name, s, recipients, ep, serial, tab=target,
                                  sid=ch_sid)
                    seen.add(v["video_id"])
                    clear_state(v["video_id"], states)
                    done += 1
                    free_memory()
                except NotReady as e:
                    log("wait", f"{v['title'][:60]} — {e}")
                    continue
                except NoCredits as e:
                    until = block_credits()
                    log("error", f"Supadata credits are used up. Pausing until "
                                 f"{until.strftime('%d %b, %I:%M %p')}.")
                    STATUS["last_result"] = "Supadata credits are used up."
                    return STATUS["last_result"]
                except Exception as e:
                    attempts = bump_state(v["video_id"], e, states)
                    states = state_map()
                    failed += 1
                    spot = where_it_broke()
                    log("wait" if attempts < MAX_ATTEMPTS else "error",
                        f"{v['title'][:60]} — {e} (attempt {attempts})"
                        + (f" [{spot}]" if spot else ""))
                    if attempts == MAX_ATTEMPTS and recipients:
                        try:
                            gapi.send_mail(
                                recipients[:1],
                                f"No transcript: {v['title'][:60]}",
                                f"{v['link']}\n\nTried for two days, still no "
                                f"transcript.\nReason: {e}\n")
                        except Exception:
                            pass

        # --- pasted links pehle
        pending = [q for q in data.get("queue", [])
                   if q.get("Video Link")
                   and (q.get("Status") or "").lower() not in ("done", "skip", "error")]
        for q in pending[:2]:
            if memory_mb() > 320:
                log("wait", f"{memory_mb()} MB — the rest of the queue waits")
                break
            vid = extract_video_id(q["Video Link"])
            if not vid:
                gapi.write_range("Queue", f"D{q['_row']}", [["error: not a video link"]])
                failed += 1
                continue
            try:
                STATUS["step"] = "reading pasted link"
                sid, target = destination(q, data, default_tab="Links")
                v = video_meta(vid)
                to = [x.strip() for x in (q.get("Emails") or "").replace(";", ",").split(",")
                      if "@" in x] or recipients
                again = str(q.get("Replace row") or "").strip()
                again = int(again) if again.isdigit() else 0
                if again:
                    old_row = next((r for r in gapi.rows_in(sid, target)
                                    if r["_row"] == again), {})
                    serial = old_row.get("Sr.No.") or again - 1
                    v["title"] = old_row.get("Title") or v["title"]
                    STATUS["step"] = f"rebuilding — {v['title'][:44]}"
                    old_pdf = gapi.file_id_from_link(old_row.get("PDF", ""))
                else:
                    serial = len(gapi.rows_in(sid, target)) + 1
                    old_pdf = ""
                process_video(v, v["channel"], s, to, "", serial, tab=target,
                              instruction=q.get("Instruction") or None, sid=sid,
                              replace_row=again)
                if again and old_pdf:
                    gapi.delete_file(old_pdf)
                gapi.write_range("Queue", f"D{q['_row']}", [["done"]])
                done += 1
                free_memory()
            except NotReady as ex:
                log("wait", f"{q['Video Link']} — {ex}")
                continue
            except NoCredits:
                until = block_credits()
                log("error", f"Supadata credits are used up. Pausing until "
                             f"{until.strftime('%d %b, %I:%M %p')}.")
                STATUS["last_result"] = "Supadata credits are used up."
                return STATUS["last_result"]
            except Exception as ex:
                gapi.write_range("Queue", f"D{q['_row']}", [[f"error: {str(ex)[:120]}"]])
                log("error", f"link {q['Video Link']}: {ex} [{where_it_broke()}]")
                failed += 1

        parts = []
        if done:
            parts.append(f"{done} sent")
        if failed:
            parts.append(f"{failed} stalled")
        if skipped:
            parts.append(f"{skipped} skipped")
        STATUS["did_work"] = done > 0
        STATUS["last_result"] = (", ".join(parts) or "Nothing new") + "."
        return STATUS["last_result"]
    except Exception as e:
        STATUS["last_result"] = f"Problem: {e}"
        log("error", f"run: {e}\n{where_it_broke()}")
        return STATUS["last_result"]
    finally:
        free_memory()
        STATUS.update({"running": False, "step": "", "last_run": now_str()})
        _run_lock.release()


def run_in_background(manual=False):
    if STATUS["running"]:
        return False
    threading.Thread(target=run_check, kwargs={"manual": manual}, daemon=True).start()
    return True
