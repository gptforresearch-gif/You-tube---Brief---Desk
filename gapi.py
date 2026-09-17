"""
Google ki saari baat-cheet yahin hoti hai:
  - ek hi login se Sheet likhna, Drive me PDF rakhna, Gmail se bhejna
Koi service account nahi, koi app password nahi.
"""

import io
import os
import re
import base64
import socket
import threading
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from email.mime.application import MIMEApplication

from google.oauth2.credentials import Credentials
from google.auth.transport.requests import Request
from googleapiclient.discovery import build
from googleapiclient.http import MediaIoBaseUpload

socket.setdefaulttimeout(90)    # koi bhi request 90 second se zyada na latke

SCOPES = [
    "https://www.googleapis.com/auth/drive.file",
    "https://www.googleapis.com/auth/spreadsheets",
    "https://www.googleapis.com/auth/gmail.send",
    "https://www.googleapis.com/auth/userinfo.email",
    "openid",
]

SHEET_NAME = "YouTube Brief Desk Data"
DRIVE_ROOT_NAME = "YouTube Brief Desk"

EPISODE_HEADER = [
    "Sr.No.", "Date", "Episode", "Video Link", "PDF", "Transcript", "Summary",
    "Title", "Channel", "Video ID", "Sent To",
]
TABS = {
    "Episodes": EPISODE_HEADER,
    "Links": EPISODE_HEADER,
    "Queue": ["Video Link", "Added", "Emails", "Status", "Instruction", "Sheet tab",
              "Spreadsheet"],
    "Sheets": ["Name", "Spreadsheet ID", "Link", "Added"],
    "Channels": ["Channel ID", "Name", "Added On", "Active", "Sheet tab",
                 "Spreadsheet"],
    "Recipients": ["Email", "Name", "Active"],
    "Settings": ["Key", "Value"],
    "State": ["Video ID", "Attempts", "Last Error", "Updated"],
    "Overflow": ["Video ID", "Part", "Text"],
    "Log": ["Time", "Level", "Message"],
}

import time as _time

SYSTEM_TABS = {"Channels", "Recipients", "Settings", "State", "Overflow",
               "Log", "Queue", "Sheets"}
MAIN = "Main"
MAX_DATA_TABS = 12

_lock = threading.RLock()      # RLock: ek hi dhaage ko dobara taala
                               # lagane deta hai, warna wo khud atak jaata hai
_cache = {"creds": None, "sheet_id": None, "root_folder": None, "month_folders": {},
          "svc": {}, "email": "", "tabs_ok": False}
_bundle = {"at": 0.0, "data": None}
BUNDLE_TTL = 45          # second — itni der purana data chalega


# ---------------------------------------------------------------- credentials

def has_token() -> bool:
    return bool(os.environ.get("GOOGLE_REFRESH_TOKEN"))


def credentials():
    with _lock:
        creds = _cache.get("creds")
        if creds and creds.valid:
            return creds
        rt = os.environ.get("GOOGLE_REFRESH_TOKEN", "").strip()
        cid = os.environ.get("GOOGLE_CLIENT_ID", "").strip()
        secret = os.environ.get("GOOGLE_CLIENT_SECRET", "").strip()
        if not (rt and cid and secret):
            raise RuntimeError(
                "Google login abhi baaki hai. Settings > Connect Google se ek baar jod dijiye."
            )
        creds = Credentials(
            None,
            refresh_token=rt,
            token_uri="https://oauth2.googleapis.com/token",
            client_id=cid,
            client_secret=secret,
            scopes=SCOPES,
        )
        creds.refresh(Request())
        _cache["creds"] = creds
        _cache["svc"] = {}
        return creds


def _svc(name, version):
    """Connection ek hi baar banta hai aur sambhaal kar rakha jaata hai.
    Har baar naya banane me Google ka bhaari discovery document utarta tha,
    jisse Render ki 512 MB waali memory bhar jaati thi."""
    key = f"{name}:{version}"
    svc = _cache["svc"].get(key)
    if svc is not None:
        return svc
    creds = credentials()          # taale ke bahar, taaki fanda na bane
    with _lock:
        svc = _cache["svc"].get(key)
        if svc is None:
            svc = build(name, version, credentials=creds,
                        cache_discovery=False, static_discovery=True)
            _cache["svc"][key] = svc
    return svc


def account_email() -> str:
    if _cache.get("email"):
        return _cache["email"]
    try:
        info = _svc("oauth2", "v2").userinfo().get().execute()
        _cache["email"] = info.get("email", "")
        return _cache["email"]
    except Exception:
        return ""


# ---------------------------------------------------------------- spreadsheet

def spreadsheet_id() -> str:
    """App apni sheet khud banata hai, isliye dobara dhoondh bhi leta hai."""
    if _cache.get("sheet_id"):
        return _cache["sheet_id"]
    drive = _svc("drive", "v3")
    q = ("mimeType='application/vnd.google-apps.spreadsheet' and trashed=false "
         f"and name='{SHEET_NAME}'")
    res = drive.files().list(q=q, spaces="drive", fields="files(id,name)",
                             pageSize=5).execute()
    files = res.get("files", [])
    if files:
        _cache["sheet_id"] = files[0]["id"]
        return _cache["sheet_id"]
    return create_spreadsheet()


def create_spreadsheet() -> str:
    sheets = _svc("sheets", "v4")
    body = {
        "properties": {"title": SHEET_NAME},
        "sheets": [{"properties": {"title": t}} for t in TABS],
    }
    ss = sheets.spreadsheets().create(body=body, fields="spreadsheetId").execute()
    sid = ss["spreadsheetId"]
    _cache["sheet_id"] = sid
    data = [{"range": f"{tab}!A1", "values": [header]} for tab, header in TABS.items()]
    sheets.spreadsheets().values().batchUpdate(
        spreadsheetId=sid,
        body={"valueInputOption": "RAW", "data": data},
    ).execute()
    _freeze_header(sid)
    return sid


def _freeze_header(sid):
    try:
        sheets = _svc("sheets", "v4")
        meta = sheets.spreadsheets().get(spreadsheetId=sid).execute()
        reqs = []
        for sh in meta.get("sheets", []):
            reqs.append({
                "updateSheetProperties": {
                    "properties": {
                        "sheetId": sh["properties"]["sheetId"],
                        "gridProperties": {"frozenRowCount": 1},
                    },
                    "fields": "gridProperties.frozenRowCount",
                }
            })
        if reqs:
            sheets.spreadsheets().batchUpdate(
                spreadsheetId=sid, body={"requests": reqs}).execute()
    except Exception:
        pass


def ensure_tabs(force=False):
    """Purani sheet me koi tab kam ho to jod deta hai. Ek hi baar chalta hai."""
    if _cache.get("tabs_ok") and not force:
        return
    _cache["tabs_ok"] = True
    _cache["tab_names"] = None
    sid = spreadsheet_id()
    sheets = _svc("sheets", "v4")
    meta = sheets.spreadsheets().get(spreadsheetId=sid).execute()
    have = {sh["properties"]["title"] for sh in meta.get("sheets", [])}
    missing = [t for t in TABS if t not in have]
    if not missing:
        return
    sheets.spreadsheets().batchUpdate(
        spreadsheetId=sid,
        body={"requests": [{"addSheet": {"properties": {"title": t}}} for t in missing]},
    ).execute()
    data = [{"range": f"{t}!A1", "values": [TABS[t]]} for t in missing]
    sheets.spreadsheets().values().batchUpdate(
        spreadsheetId=sid, body={"valueInputOption": "RAW", "data": data}).execute()


def sheet_url() -> str:
    return f"https://docs.google.com/spreadsheets/d/{spreadsheet_id()}/edit"


def invalidate():
    _bundle["at"] = 0.0


def all_tab_names(force=False):
    if _cache.get("tab_names") and not force:
        return _cache["tab_names"]
    meta = _svc("sheets", "v4").spreadsheets().get(
        spreadsheetId=spreadsheet_id(), fields="sheets.properties.title").execute()
    names = [sh["properties"]["title"] for sh in meta.get("sheets", [])]
    _cache["tab_names"] = names
    return names


def data_tabs(force=False):
    """Episodes, Links aur aapke banaye hue tab — system waale chhod kar."""
    names = [n for n in all_tab_names(force) if n not in SYSTEM_TABS]
    for fixed in ("Links", "Episodes"):
        if fixed in names:
            names.remove(fixed)
            names.insert(0, fixed)
    return names[:MAX_DATA_TABS]


def create_data_tab(name: str) -> str:
    name = (name or "").strip()[:60]
    if not name or name in SYSTEM_TABS:
        raise RuntimeError("That tab name cannot be used.")
    if name in all_tab_names(force=True):
        return name
    sheets = _svc("sheets", "v4")
    sheets.spreadsheets().batchUpdate(
        spreadsheetId=spreadsheet_id(),
        body={"requests": [{"addSheet": {"properties": {"title": name}}}]}).execute()
    sheets.spreadsheets().values().update(
        spreadsheetId=spreadsheet_id(), range=f"{name}!A1",
        valueInputOption="RAW", body={"values": [EPISODE_HEADER]}).execute()
    _cache["tab_names"] = None
    invalidate()
    return name


def read_all(force=False):
    """Channels, Recipients, Settings, Episodes aur State — sab ek hi
    request me. Pehle har cheez alag maangi jaati thi, isi se der lagti thi."""
    if not force and _bundle["data"] and (_time.time() - _bundle["at"]) < BUNDLE_TTL:
        return _bundle["data"]
    tabs = data_tabs()
    ranges = [
        "Channels!A2:F1000", "Recipients!A2:C1000", "Settings!A2:B300",
        "State!A2:D5000", "Queue!A2:G2000", "Sheets!A2:D200",
    ]
    base = len(ranges)
    for t in tabs:
        ranges += [f"{t}!A2:E100000", f"{t}!G2:K100000"]
    res = _svc("sheets", "v4").spreadsheets().values().batchGet(
        spreadsheetId=spreadsheet_id(), ranges=ranges).execute()
    vr = [r.get("values", []) for r in res.get("valueRanges", [])]
    while len(vr) < len(ranges):
        vr.append([])

    def rows(raw, keys, start=2):
        out = []
        for i, row in enumerate(raw):
            row = (list(row) + [""] * len(keys))[:len(keys)]
            d = dict(zip(keys, row))
            d["_row"] = i + start
            if any(str(v).strip() for k, v in d.items() if k != "_row"):
                out.append(d)
        return out

    def episode_rows(a, b):
        out = []
        for i in range(max(len(a), len(b))):
            ra = ((list(a[i]) if i < len(a) else []) + [""] * 5)[:5]
            rb = ((list(b[i]) if i < len(b) else []) + [""] * 5)[:5]
            if not (ra[3] or rb[1] or rb[3]):
                continue
            out.append({
                "_row": i + 2, "Sr.No.": ra[0], "Date": ra[1], "Episode": ra[2],
                "Video Link": ra[3], "PDF": ra[4], "Summary": rb[0], "Title": rb[1],
                "Channel": rb[2], "Video ID": rb[3], "Sent To": rb[4],
            })
        return out

    per_tab = {}
    for i, t in enumerate(tabs):
        per_tab[t] = episode_rows(vr[base + i * 2], vr[base + 1 + i * 2])

    settings = {}
    for row in vr[2]:
        if row and row[0]:
            settings[row[0]] = row[1] if len(row) > 1 else ""

    data = {
        "channels": rows(vr[0], TABS["Channels"]),
        "recipients": rows(vr[1], TABS["Recipients"]),
        "settings": settings,
        "tabs": tabs,
        "rows": per_tab,
        "episodes": per_tab.get("Episodes", []),
        "links": per_tab.get("Links", []),
        "state": rows(vr[3], TABS["State"]),
        "queue": rows(vr[4], TABS["Queue"]),
        "sheets": rows(vr[5], TABS["Sheets"]),
    }
    _bundle.update({"at": _time.time(), "data": data})
    return data


def read_tab(tab: str):
    sid = spreadsheet_id()
    res = _svc("sheets", "v4").spreadsheets().values().get(
        spreadsheetId=sid, range=f"{tab}!A1:ZZ100000").execute()
    return res.get("values", [])


def read_rows(tab: str):
    """Header ke baad ki rows, dict ki tarah."""
    values = read_tab(tab)
    if not values:
        return []
    header = values[0]
    out = []
    for i, row in enumerate(values[1:], start=2):
        row = row + [""] * (len(header) - len(row))
        d = {header[j]: row[j] for j in range(len(header))}
        d["_row"] = i
        out.append(d)
    return out


def episodes_light():
    """Episodes padhte waqt bhaari Transcript column chhod dete hain,
    warna har baar poora transcript download hoga."""
    sid = spreadsheet_id()
    res = _svc("sheets", "v4").spreadsheets().values().batchGet(
        spreadsheetId=sid,
        ranges=["Episodes!A2:E100000", "Episodes!G2:K100000"],
    ).execute()
    ranges = res.get("valueRanges", [])
    a = ranges[0].get("values", []) if len(ranges) > 0 else []
    b = ranges[1].get("values", []) if len(ranges) > 1 else []
    rows = []
    for i in range(max(len(a), len(b))):
        ra = ((a[i] if i < len(a) else []) + [""] * 5)[:5]
        rb = ((b[i] if i < len(b) else []) + [""] * 5)[:5]
        rows.append({
            "_row": i + 2, "Sr.No.": ra[0], "Date": ra[1], "Episode": ra[2],
            "Video Link": ra[3], "PDF": ra[4], "Summary": rb[0], "Title": rb[1],
            "Channel": rb[2], "Video ID": rb[3], "Sent To": rb[4],
        })
    while rows and not (rows[-1]["Video ID"] or rows[-1]["Title"]
                        or rows[-1]["Video Link"]):
        rows.pop()
    return rows


def append_row(tab: str, row: list):
    _svc("sheets", "v4").spreadsheets().values().append(
        spreadsheetId=spreadsheet_id(),
        range=f"{tab}!A1",
        valueInputOption="USER_ENTERED",
        insertDataOption="INSERT_ROWS",
        body={"values": [row]},
    ).execute()
    invalidate()


def append_rows(tab: str, rows: list):
    if not rows:
        return
    _svc("sheets", "v4").spreadsheets().values().append(
        spreadsheetId=spreadsheet_id(),
        range=f"{tab}!A1",
        valueInputOption="USER_ENTERED",
        insertDataOption="INSERT_ROWS",
        body={"values": rows},
    ).execute()
    invalidate()


def write_range(tab: str, a1: str, values: list):
    _svc("sheets", "v4").spreadsheets().values().update(
        spreadsheetId=spreadsheet_id(),
        range=f"{tab}!{a1}",
        valueInputOption="USER_ENTERED",
        body={"values": values},
    ).execute()
    invalidate()


def replace_tab(tab: str, header: list, rows: list):
    sid = spreadsheet_id()
    svc = _svc("sheets", "v4").spreadsheets().values()
    svc.clear(spreadsheetId=sid, range=f"{tab}!A1:ZZ100000", body={}).execute()
    svc.update(
        spreadsheetId=sid, range=f"{tab}!A1",
        valueInputOption="USER_ENTERED",
        body={"values": [header] + rows},
    ).execute()
    invalidate()


# ---------------------------------------------------------------- settings tab

def get_settings(force=False) -> dict:
    return dict(read_all(force=force)["settings"])


def set_settings(updates: dict):
    current = get_settings(force=True)
    current.update({k: str(v) for k, v in updates.items()})
    rows = [[k, v] for k, v in sorted(current.items())]
    replace_tab("Settings", TABS["Settings"], rows)


# ------------------------------------------------- doosri spreadsheet files

def sid_from(text: str) -> str:
    """Link ya ID — dono se spreadsheet ID."""
    text = (text or "").strip()
    m = re.search(r"/spreadsheets/d/([A-Za-z0-9_-]{20,})", text)
    if m:
        return m.group(1)
    return text if re.fullmatch(r"[A-Za-z0-9_-]{20,}", text) else ""


def sheet_link(sid: str) -> str:
    return f"https://docs.google.com/spreadsheets/d/{sid}/edit"


def spreadsheet_title(sid: str) -> str:
    meta = _svc("sheets", "v4").spreadsheets().get(
        spreadsheetId=sid, fields="properties.title").execute()
    return meta.get("properties", {}).get("title", "")


def make_spreadsheet(name: str, first_tab: str = "Episodes") -> dict:
    ss = _svc("sheets", "v4").spreadsheets().create(
        body={"properties": {"title": name},
              "sheets": [{"properties": {"title": first_tab}}]},
        fields="spreadsheetId").execute()
    sid = ss["spreadsheetId"]
    _svc("sheets", "v4").spreadsheets().values().update(
        spreadsheetId=sid, range=f"{first_tab}!A1", valueInputOption="RAW",
        body={"values": [EPISODE_HEADER]}).execute()
    return {"id": sid, "link": sheet_link(sid), "title": name}


def tabs_in(sid: str):
    meta = _svc("sheets", "v4").spreadsheets().get(
        spreadsheetId=sid, fields="sheets.properties.title").execute()
    return [sh["properties"]["title"] for sh in meta.get("sheets", [])]


def ensure_tab_in(sid: str, tab: str):
    if sid in (None, "", spreadsheet_id()):
        if tab not in data_tabs():
            create_data_tab(tab)
        return
    if tab in tabs_in(sid):
        return
    svc = _svc("sheets", "v4").spreadsheets()
    svc.batchUpdate(spreadsheetId=sid,
                    body={"requests": [{"addSheet": {"properties": {"title": tab}}}]}
                    ).execute()
    svc.values().update(spreadsheetId=sid, range=f"{tab}!A1",
                        valueInputOption="RAW",
                        body={"values": [EPISODE_HEADER]}).execute()


def append_row_in(sid: str, tab: str, row: list):
    target = sid or spreadsheet_id()
    _svc("sheets", "v4").spreadsheets().values().append(
        spreadsheetId=target, range=f"{tab}!A1",
        valueInputOption="USER_ENTERED", insertDataOption="INSERT_ROWS",
        body={"values": [row]}).execute()
    if target == spreadsheet_id():
        invalidate()


def append_rows_in(sid: str, tab: str, rows: list):
    if not rows:
        return
    target = sid or spreadsheet_id()
    _svc("sheets", "v4").spreadsheets().values().append(
        spreadsheetId=target, range=f"{tab}!A1",
        valueInputOption="USER_ENTERED", insertDataOption="INSERT_ROWS",
        body={"values": rows}).execute()
    if target == spreadsheet_id():
        invalidate()


def rows_in(sid: str, tab: str):
    """Kisi bhi spreadsheet ke ek tab ki rows — Transcript column chhod kar."""
    if not sid or sid == spreadsheet_id():
        return read_all()["rows"].get(tab, [])
    res = _svc("sheets", "v4").spreadsheets().values().batchGet(
        spreadsheetId=sid,
        ranges=[f"{tab}!A2:E100000", f"{tab}!G2:K100000"]).execute()
    vr = [r.get("values", []) for r in res.get("valueRanges", [])]
    while len(vr) < 2:
        vr.append([])
    a, b = vr[0], vr[1]
    out = []
    for i in range(max(len(a), len(b))):
        ra = ((list(a[i]) if i < len(a) else []) + [""] * 5)[:5]
        rb = ((list(b[i]) if i < len(b) else []) + [""] * 5)[:5]
        if not (ra[3] or rb[1] or rb[3]):
            continue
        out.append({"_row": i + 2, "Sr.No.": ra[0], "Date": ra[1], "Episode": ra[2],
                    "Video Link": ra[3], "PDF": ra[4], "Summary": rb[0],
                    "Title": rb[1], "Channel": rb[2], "Video ID": rb[3],
                    "Sent To": rb[4]})
    return out


# ---------------------------------------------------------------- drive (PDF)

def _find_or_create_folder(name: str, parent: str = None) -> str:
    drive = _svc("drive", "v3")
    q = (f"name='{name}' and mimeType='application/vnd.google-apps.folder' "
         "and trashed=false")
    if parent:
        q += f" and '{parent}' in parents"
    res = drive.files().list(q=q, spaces="drive", fields="files(id)", pageSize=5).execute()
    files = res.get("files", [])
    if files:
        return files[0]["id"]
    meta = {"name": name, "mimeType": "application/vnd.google-apps.folder"}
    if parent:
        meta["parents"] = [parent]
    return drive.files().create(body=meta, fields="id").execute()["id"]


def month_folder(yyyy_mm: str) -> str:
    if yyyy_mm in _cache["month_folders"]:
        return _cache["month_folders"][yyyy_mm]
    if not _cache.get("root_folder"):
        _cache["root_folder"] = _find_or_create_folder(DRIVE_ROOT_NAME)
    fid = _find_or_create_folder(yyyy_mm, _cache["root_folder"])
    _cache["month_folders"][yyyy_mm] = fid
    return fid


def drive_root_url() -> str:
    if not _cache.get("root_folder"):
        _cache["root_folder"] = _find_or_create_folder(DRIVE_ROOT_NAME)
    return f"https://drive.google.com/drive/folders/{_cache['root_folder']}"


def upload_pdf(filename: str, data: bytes, yyyy_mm: str, public: bool = True) -> dict:
    drive = _svc("drive", "v3")
    media = MediaIoBaseUpload(io.BytesIO(data), mimetype="application/pdf",
                              resumable=False)
    f = drive.files().create(
        body={"name": filename, "parents": [month_folder(yyyy_mm)]},
        media_body=media,
        fields="id,webViewLink",
    ).execute()
    if public:
        try:
            drive.permissions().create(
                fileId=f["id"],
                body={"type": "anyone", "role": "reader"},
            ).execute()
        except Exception:
            pass
    return {"id": f["id"], "link": f.get("webViewLink", "")}


def download_file(file_id: str) -> bytes:
    return _svc("drive", "v3").files().get_media(fileId=file_id).execute()


def file_id_from_link(link: str) -> str:
    if not link:
        return ""
    if "/d/" in link:
        return link.split("/d/")[1].split("/")[0]
    if "id=" in link:
        return link.split("id=")[1].split("&")[0]
    return ""


# ---------------------------------------------------------------- gmail

def send_mail(to_list, subject, body_text, attachment=None, attachment_name=None):
    if not to_list:
        return
    msg = MIMEMultipart()
    msg["To"] = ", ".join(to_list)
    msg["Subject"] = subject
    msg.attach(MIMEText(body_text, "plain", "utf-8"))
    if attachment:
        part = MIMEApplication(attachment, _subtype="pdf")
        part.add_header("Content-Disposition", "attachment",
                        filename=attachment_name or "brief.pdf")
        msg.attach(part)
    raw = base64.urlsafe_b64encode(msg.as_bytes()).decode()
    _svc("gmail", "v1").users().messages().send(
        userId="me", body={"raw": raw}).execute()
