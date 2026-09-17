"""
PDF me har bhaasha theek dikhe — hindi, gujarati, bangla, tamil, jo bhi.

ReportLab ke apne font me sirf angrezi hoti hai, isliye baaki lipiyon ke
akshar khaali dibbe ban jaate the. Yahan zaroorat padne par us lipi ka font
utaar kar rakh liya jaata hai, ek hi baar.
"""

import os
import re
import threading

BASE = ("https://raw.githubusercontent.com/googlefonts/noto-fonts/main/"
        "hinted/ttf")
CACHE = "/tmp/ybd-fonts"

# (naam, akshar ki seema, font ka naam)
SCRIPTS = [
    ("Deva", (0x0900, 0x097F), "NotoSansDevanagari"),   # hindi, marathi, nepali
    ("Beng", (0x0980, 0x09FF), "NotoSansBengali"),
    ("Guru", (0x0A00, 0x0A7F), "NotoSansGurmukhi"),
    ("Gujr", (0x0A80, 0x0AFF), "NotoSansGujarati"),
    ("Orya", (0x0B00, 0x0B7F), "NotoSansOriya"),
    ("Taml", (0x0B80, 0x0BFF), "NotoSansTamil"),
    ("Telu", (0x0C00, 0x0C7F), "NotoSansTelugu"),
    ("Knda", (0x0C80, 0x0CFF), "NotoSansKannada"),
    ("Mlym", (0x0D00, 0x0D7F), "NotoSansMalayalam"),
    ("Arab", (0x0600, 0x06FF), "NotoSansArabic"),
    ("Hebr", (0x0590, 0x05FF), "NotoSansHebrew"),
    ("Thai", (0x0E00, 0x0E7F), "NotoSansThai"),
]

_lock = threading.Lock()
_ready = {}          # "Deva" -> font ka naam, ya None (na mil paaya)


def script_of(ch: str) -> str:
    code = ord(ch)
    if code < 0x0590:
        return ""                      # angrezi, ank, viraam — default font
    for name, (lo, hi), _ in SCRIPTS:
        if lo <= code <= hi:
            return name
    return ""


def ensure(script: str):
    """Us lipi ka font taiyaar karo. Naam lautao, ya None."""
    if not script:
        return None
    if script in _ready:
        return _ready[script]
    with _lock:
        if script in _ready:
            return _ready[script]
        _ready[script] = None
        try:
            import requests
            from reportlab.pdfbase import pdfmetrics
            from reportlab.pdfbase.ttfonts import TTFont

            family = next(f for n, _, f in SCRIPTS if n == script)
            os.makedirs(CACHE, exist_ok=True)
            path = os.path.join(CACHE, f"{family}-Regular.ttf")
            if not os.path.exists(path) or os.path.getsize(path) < 5000:
                url = f"{BASE}/{family}/{family}-Regular.ttf"
                r = requests.get(url, timeout=60)
                r.raise_for_status()
                with open(path, "wb") as fh:
                    fh.write(r.content)
            pdfmetrics.registerFont(TTFont(family, path))
            _ready[script] = family
            print(f"[fonts] {family} ready", flush=True)
        except Exception as ex:
            print(f"[fonts] {script} not available: {ex}", flush=True)
        return _ready[script]


def esc(t: str) -> str:
    return (t or "").replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def markup(text: str) -> str:
    """Text ko ReportLab ke liye taiyaar karo — har lipi apne font me."""
    if not text:
        return ""
    if all(ord(c) < 0x0590 for c in text):
        return esc(text)

    out, buf, cur = [], [], script_of(text[0])
    for ch in text:
        sc = script_of(ch)
        if ch.isspace() or sc == cur:
            buf.append(ch)
            continue
        out.append((cur, "".join(buf)))
        buf, cur = [ch], sc
    out.append((cur, "".join(buf)))

    parts = []
    for sc, chunk in out:
        if not chunk:
            continue
        font = ensure(sc)
        if font:
            parts.append(f'<font name="{font}">{esc(chunk)}</font>')
        else:
            parts.append(esc(chunk))
    return "".join(parts)


def plain(text: str) -> str:
    """Jahan font nahi chal sakta (jaise PDF ka naam), wahan saaf text."""
    return re.sub(r"\s+", " ", text or "").strip()
