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

import gapi

BUILD = "17"

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


def log(level, message):
    try:
        gapi.append_row("Log", [now_str(), level, str(message)[:2000]])
    except Exception:
        pass
    print(f"[{level}] {message}", flush=True)


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

def transcript_direct(video_id: str):
    """Muft koshish. Cloud se aksar YouTube rok deta hai — isliye chup-chaap fail hone denge."""
    try:
        from youtube_transcript_api import YouTubeTranscriptApi
        try:
            api = YouTubeTranscriptApi()
            fetched = api.fetch(video_id)
            snippets = getattr(fetched, "snippets", fetched)
            text = " ".join(getattr(s, "text", s.get("text", "")) for s in snippets)
        except Exception:
            data = YouTubeTranscriptApi.get_transcript(video_id)
            text = " ".join(d["text"] for d in data)
        text = re.sub(r"\s+", " ", text).strip()
        return text if len(text) > 200 else None
    except Exception:
        return None


def transcript_supadata(video_id: str, key: str):
    """Caption ho to caption, na ho to Supadata khud audio se bana deta hai."""
    if not key:
        raise RuntimeError("No Supadata key in Settings.")
    headers = {"x-api-key": key}
    watch = f"https://www.youtube.com/watch?v={video_id}"
    attempts = [
        ("https://api.supadata.ai/v1/transcript", {"url": watch, "text": "true", "mode": "auto"}),
        ("https://api.supadata.ai/v1/youtube/transcript", {"videoId": video_id, "text": "true"}),
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
            raise RuntimeError("Supadata credits for this month are used up.")
        if r.status_code >= 400:
            last_error = f"{r.status_code} {r.text[:200]}"
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
    if isinstance(data, str):
        return re.sub(r"\s+", " ", data).strip()
    content = data.get("content") or data.get("text") or data.get("transcript")
    if isinstance(content, list):
        content = " ".join(
            (c.get("text", "") if isinstance(c, dict) else str(c)) for c in content)
    if not content:
        return ""
    return re.sub(r"\s+", " ", str(content)).strip()


def get_transcript(video_id: str, s=None):
    s = s or settings()
    text = transcript_direct(video_id)
    if text:
        return text, "YouTube"
    return transcript_supadata(video_id, supadata_key(s)), "Supadata"


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


def to_english(text: str, key: str, on_step=None) -> str:
    if not is_devanagari(text):
        return text
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


def make_output(text: str, title: str, length: str, key: str,
                instruction: str = "", on_step=None) -> str:
    """Default me summary. Instruction di ho to wahi kaam hota hai —
    mukhya bindu, notes, sawaal-jawaab, lekh, jo bhi kaha jaye."""
    task = (instruction or "").strip() or DEFAULT_TASK
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
    return (t.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;"))


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
        story.append(Paragraph(esc(para.strip()), st_body))

    story += [PageBreak(), Paragraph("Full transcript", st_head)]
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
                  tab="Episodes", instruction=None, sid=""):
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
    summary = make_output(english, video["title"], s.get("summary_length", "medium"),
                          key, instruction=task, on_step=lambda m: step(m))
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
    safe = re.sub(r"[^\w\s-]", "", video["title"])[:70].strip() or video["video_id"]
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
            f"Full transcript is in the attached PDF.\n")
    if recipients:
        gapi.send_mail(recipients, subject, body, attachment=pdf, attachment_name=fname)
    log("mem", f"{memory_mb()} MB — {video['title'][:40]}")

    del pdf
    gc.collect()
    mark("done")

    gapi.append_row_in(sid, tab, [
        serial_no, date_str, episode_no, video["link"], up["link"],
        sheet_transcript, summary, video["title"], channel_name,
        video["video_id"], ", ".join(recipients),
    ])
    log("ok", f"Sent: {video['title']} (transcript via {source})")
    return up["link"]


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
                    ep = per_channel.get(name, 0) + 1
                    process_video(v, name, s, recipients, ep, serial, tab=target,
                                  sid=ch_sid)
                    per_channel[name] = ep
                    seen.add(v["video_id"])
                    clear_state(v["video_id"], states)
                    done += 1
                    gc.collect()
                except Exception as e:
                    attempts = bump_state(v["video_id"], e, states)
                    states = state_map()
                    failed += 1
                    log("wait" if attempts < MAX_ATTEMPTS else "error",
                        f"{v['title'][:60]} — {e} (attempt {attempts})")
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
                serial = len(gapi.rows_in(sid, target)) + 1
                process_video(v, v["channel"], s, to, "", serial, tab=target,
                              instruction=q.get("Instruction") or None, sid=sid)
                gapi.write_range("Queue", f"D{q['_row']}", [["done"]])
                done += 1
                gc.collect()
            except Exception as ex:
                gapi.write_range("Queue", f"D{q['_row']}", [[f"error: {str(ex)[:120]}"]])
                log("error", f"link {q['Video Link']}: {ex}")
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
        log("error", f"run: {e}")
        return STATUS["last_result"]
    finally:
        STATUS.update({"running": False, "step": "", "last_run": now_str()})
        _run_lock.release()


def run_in_background(manual=False):
    if STATUS["running"]:
        return False
    threading.Thread(target=run_check, kwargs={"manual": manual}, daemon=True).start()
    return True
