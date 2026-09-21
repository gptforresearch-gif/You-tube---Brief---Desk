"""
YouTube Brief Desk — PC Helper
==============================

Ye chhota sa program aapke Windows PC par chalta hai aur sirf ek kaam karta hai:
cloud wali app se poochta hai "kis video ka transcript chahiye?", use ghar ke
internet se utaar kar wapas bhej deta hai. Baaki sab kuch — English, summary,
PDF, email, Sheet — cloud par hi hota hai.

YouTube cloud servers ko rokta hai, ghar ke internet ko nahi. Isliye ye muft
hai aur iski koi mahine wali seema nahi.

Chalane se pehle:
    pip install requests youtube-transcript-api

Phir neeche do line bhariye aur file ko double-click kar dijiye.
"""

import time
import traceback

# ---------------------------------------------------------------- yahan bhariye

APP_URL = "https://youtube-brief-desk.onrender.com"   # aapki app ka pata
HELPER_KEY = "PASTE-YOUR-HELPER-KEY-HERE"             # Render me daali gayi HELPER_KEY

EVERY_MINUTES = 10        # kitni-kitni der me poochhe

VERSION = "4"

# ------------------------------------------------------------------------------

import requests


def log(msg):
    print(time.strftime("[%d %b %I:%M %p] ") + str(msg), flush=True)


def fetch_transcript(video_id):
    """Ghar ke internet se transcript — JIS BHI BHASHA me mile.
    Hindi, gujarati, angrezi, kuch bhi. Anuvaad app khud kar leti hai."""
    from youtube_transcript_api import YouTubeTranscriptApi

    # Samay ka nishan wahin lagta hai jahan baat badalti hai —
    # gine hue second par nahi.
    SHORT_MIN, SOFT, MAX_B, PAUSE = 14, 95, 240, 1.2
    ENDS = ("।", "॥", ".", "?", "!", "|")

    def stamp(sec):
        sec = int(sec or 0)
        h, rest = divmod(max(sec, 0), 3600)
        m, s = divmod(rest, 60)
        return f"[{h}:{m:02d}:{s:02d}]" if h else f"[{m:02d}:{s:02d}]"

    def ends(t):
        t = (t or "").rstrip().rstrip('"\'\u201d\u2019)')
        return bool(t) and t[-1] in ENDS

    def join(snippets):
        rows = []
        for sn in snippets:
            if isinstance(sn, dict):
                st, du, tx = sn.get("start", 0), sn.get("duration", 0), sn.get("text", "")
            else:
                st = getattr(sn, "start", 0)
                du = getattr(sn, "duration", 0)
                tx = getattr(sn, "text", "")
            tx = " ".join((tx or "").split())
            if tx:
                rows.append((float(st or 0), float(du or 0), tx))
        if not rows:
            return ""
        out, cur, start = [], [], rows[0][0]
        for i, (st, du, tx) in enumerate(rows):
            cur.append(tx)
            spent = st + du - start
            nxt = rows[i + 1][0] if i + 1 < len(rows) else None
            gap = (nxt - (st + du)) if (nxt is not None and du) else 0
            done = False
            if ends(tx):
                if gap >= PAUSE and spent >= SHORT_MIN:
                    done = True
                elif spent >= SOFT:
                    done = True
            if spent >= MAX_B and (ends(tx) or gap >= 0.5):
                done = True
            if done and nxt is not None:
                out.append(f"{stamp(start)} {' '.join(cur)}")
                cur, start = [], nxt
        if cur:
            out.append(f"{stamp(start)} {' '.join(cur)}")
        return "\n".join(out)

    # naya tareeka (version 1.x)
    try:
        api = YouTubeTranscriptApi()
        try:
            listing = api.list(video_id)
        except AttributeError:
            listing = None

        if listing is not None:
            tracks = list(listing)
            if not tracks:
                raise RuntimeError("koi transcript nahi hai")
            # pehle aadmi ka likha hua, na ho to apne aap bana hua
            tracks.sort(key=lambda t: getattr(t, "is_generated", True))
            langs = [getattr(t, "language_code", "") for t in tracks]
            print(f"      milne wali bhashayein: {', '.join(langs)}", flush=True)
            return join(tracks[0].fetch())

        return join(api.fetch(video_id).snippets)
    except Exception as first:
        # purana tareeka (version 0.x)
        try:
            listing = YouTubeTranscriptApi.list_transcripts(video_id)
            tracks = list(listing)
            tracks.sort(key=lambda t: getattr(t, "is_generated", True))
            return join(tracks[0].fetch())
        except Exception:
            raise first


def one_round():
    r = requests.get(f"{APP_URL}/api/jobs", params={"key": HELPER_KEY}, timeout=120)
    if r.status_code == 403:
        log("Key galat hai. Render ki HELPER_KEY aur upar wali line milaaiye.")
        return
    r.raise_for_status()
    videos = r.json().get("videos", [])
    if not videos:
        log("Kuch naya nahi.")
        return
    log(f"{len(videos)} video chahiye.")
    for v in videos:
        vid = v.get("id")
        try:
            text = fetch_transcript(vid)
            if len(text) < 200:
                log(f"  {vid}: transcript bahut chhota, chhod diya")
                continue
            resp = requests.post(f"{APP_URL}/api/transcript",
                                 json={"key": HELPER_KEY, "video_id": vid,
                                       "text": text}, timeout=300)
            if resp.status_code < 400:
                log(f"  {vid}: bhej diya ({len(text) // 1000}k akshar)")
            else:
                log(f"  {vid}: app ne mana kiya — {resp.text[:120]}")
        except Exception as ex:
            log(f"  {vid}: nahi mila — {ex}")
        time.sleep(3)


def main():
    log(f"YouTube Brief Desk helper (v{VERSION}) chalu. Band karne ke liye ye khidki band kar dijiye.")
    log(f"App: {APP_URL}")
    while True:
        try:
            one_round()
        except Exception:
            log("Gadbad:")
            traceback.print_exc()
        time.sleep(EVERY_MINUTES * 60)


if __name__ == "__main__":
    main()
