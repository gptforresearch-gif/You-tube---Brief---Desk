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

# ------------------------------------------------------------------------------

import requests


def log(msg):
    print(time.strftime("[%d %b %I:%M %p] ") + str(msg), flush=True)


def fetch_transcript(video_id):
    """Ghar ke internet se transcript. Pehle jo mile, wahi."""
    from youtube_transcript_api import YouTubeTranscriptApi
    try:
        api = YouTubeTranscriptApi()
        fetched = api.fetch(video_id)
        snippets = getattr(fetched, "snippets", fetched)
        text = " ".join(getattr(s, "text", "") or s.get("text", "") for s in snippets)
    except AttributeError:
        data = YouTubeTranscriptApi.get_transcript(video_id)
        text = " ".join(d["text"] for d in data)
    return " ".join(text.split())


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
    log("YouTube Brief Desk helper chalu. Band karne ke liye ye khidki band kar dijiye.")
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
