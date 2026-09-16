"""
Google ki saari baat-cheet yahin hoti hai:
  - ek hi login se Sheet likhna, Drive me PDF rakhna, Gmail se bhejna
Koi service account nahi, koi app password nahi.
"""

import io
import os
import base64
import threading
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from email.mime.application import MIMEApplication

from google.oauth2.credentials import Credentials
from google.auth.transport.requests import Request
from googleapiclient.discovery import build
from googleapiclient.http import MediaIoBaseUpload

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
    "Channels": ["Channel ID", "Name", "Added On", "Active"],
    "Recipients": ["Email", "Name", "Active"],
    "Settings": ["Key", "Value"],
    "State": ["Video ID", "Attempts", "Last Error", "Updated"],
    "Overflow": ["Video ID", "Part", "Text"],
    "Log": ["Time", "Level", "Message"],
}

_lock = threading.Lock()
_cache = {"creds": None, "sheet_id": None, "root_folder": None, "month_folders": {}}


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
        return creds


def _svc(name, version):
    return build(name, version, credentials=credentials(), cache_discovery=False)


def account_email() -> str:
    try:
        info = _svc("oauth2", "v2").userinfo().get().execute()
        return info.get("email", "")
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


def ensure_tabs():
    """Purani sheet me koi tab kam ho to jod deta hai."""
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


def write_range(tab: str, a1: str, values: list):
    _svc("sheets", "v4").spreadsheets().values().update(
        spreadsheetId=spreadsheet_id(),
        range=f"{tab}!{a1}",
        valueInputOption="USER_ENTERED",
        body={"values": values},
    ).execute()


def replace_tab(tab: str, header: list, rows: list):
    sid = spreadsheet_id()
    svc = _svc("sheets", "v4").spreadsheets().values()
    svc.clear(spreadsheetId=sid, range=f"{tab}!A1:ZZ100000", body={}).execute()
    svc.update(
        spreadsheetId=sid, range=f"{tab}!A1",
        valueInputOption="USER_ENTERED",
        body={"values": [header] + rows},
    ).execute()


# ---------------------------------------------------------------- settings tab

def get_settings() -> dict:
    out = {}
    for row in read_tab("Settings")[1:]:
        if row and row[0]:
            out[row[0]] = row[1] if len(row) > 1 else ""
    return out


def set_settings(updates: dict):
    current = get_settings()
    current.update({k: str(v) for k, v in updates.items()})
    rows = [[k, v] for k, v in sorted(current.items())]
    replace_tab("Settings", TABS["Settings"], rows)


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
