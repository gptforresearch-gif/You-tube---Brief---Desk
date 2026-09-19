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

VERSION = "3"

# ------------------------------------------------------------------------------

import requests


def log(msg):
    print(time.strftime("[%d %b %I:%M %p] ") + str(msg), flush=True)


def fetch_transcript(video_id):
    """Ghar ke internet se transcript — JIS BHI BHASHA me mile.
    Hindi, gujarati, angrezi, kuch bhi. Anuvaad app khud kar leti hai."""
    from youtube_transcript_api import YouTubeTranscriptApi

    BLOCK = 30      # har 30 second par ek samay ka nishan

    def stamp(sec):
        sec = int(sec or 0)
        h, rest = divmod(max(sec, 0), 3600)
        m, s = divmod(rest, 60)
        return f"[{h}:{m:02d}:{s:02d}]" if h else f"[{m:02d}:{s:02d}]"

    def join(snippets):
        """Samay ke nishan ke saath — har 30 second par."""
        segs = []
        for sn in snippets:
            if isinstance(sn, dict):
                segs.append((sn.get("start", 0) or 0, sn.get("text", "")))
            else:
                segs.append((getattr(sn, "start", 0) or 0, getattr(sn, "text", "")))
        lines, cur, start = [], [], None
        for sec, text in segs:
            text = " ".join((text or "").split())
            if not text:
                continue
            if start is None:
                start = sec
            if sec - start >= BLOCK and cur:
                lines.append(f"{stamp(start)} {' '.join(cur)}")
                cur, start = [], sec
            cur.append(text)
        if cur:
            lines.append(f"{stamp(start or 0)} {' '.join(cur)}")
        return "\n".join(lines)

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
