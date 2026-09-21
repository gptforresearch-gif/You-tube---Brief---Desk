"""YouTube Brief Desk — browser wala hissa."""

import os
import re
import html
import datetime as dt

from flask import (Flask, request, redirect, url_for, session,
                   render_template_string, jsonify, Response)

os.environ.setdefault("OAUTHLIB_RELAX_TOKEN_SCOPE", "1")
os.environ.setdefault("OAUTHLIB_INSECURE_TRANSPORT", "1")

import gapi
import pipeline
from brand import LOGO, ICON, I192_B64, I512_B64, MASK_B64

from werkzeug.middleware.proxy_fix import ProxyFix

app = Flask(__name__)
app.wsgi_app = ProxyFix(app.wsgi_app, x_proto=1, x_host=1)
app.secret_key = os.environ.get("SECRET_KEY", "badal-dijiye-ise")

UI_PASSWORD = os.environ.get("UI_PASSWORD", "")
CRON_KEY = os.environ.get("CRON_KEY", "")
HELPER_KEY = os.environ.get("HELPER_KEY", "")
BUILD = "27"


# ---------------------------------------------------------------- background
# Two things run inside the app itself:
#   1. a self-ping every 12 minutes, so Render's free plan never sleeps it
#   2. a check for new videos every few hours (Settings: check_every_hours)
# So no external cron service is needed.

import time
import threading
import requests as _rq

SCHED = {"next_check": "", "keep_awake": True, "own_url": ""}


def _own_url():
    return (os.environ.get("RENDER_EXTERNAL_URL") or os.environ.get("APP_URL") or "").rstrip("/")


def _keepalive_loop():
    SCHED["own_url"] = _own_url()
    while True:
        time.sleep(720)
        if not SCHED["keep_awake"] or not SCHED["own_url"]:
            continue
        try:
            _rq.get(SCHED["own_url"] + "/healthz", timeout=20)
        except Exception:
            pass
        refresh_if_heavy()


MEM_LIMIT = 380          # MB — isse upar jaane par app khud ko taaza kar leti hai


def refresh_if_heavy(where=""):
    """Memory zyada ho to worker ko naye sire se shuru karo.
    Gunicorn ka master zinda rehta hai, isliye service band nahi hoti —
    bas ek pal ke liye anurodh ruk kar chalu ho jaate hain."""
    mb = pipeline.memory_mb()
    if mb < MEM_LIMIT or pipeline.STATUS.get("running"):
        return False
    pipeline.free_memory()
    mb = pipeline.memory_mb()
    if mb < MEM_LIMIT:
        return False
    try:
        pipeline.log("mem", f"{mb} MB — refreshing the app {where}".strip())
    except Exception:
        pass
    print(f"[memory] {mb} MB — restarting worker", flush=True)
    os._exit(3)


def _scheduler_loop():
    time.sleep(180)                      # let the UI come up first
    while True:
        hours = 3.0
        try:
            if gapi.has_token():
                s = pipeline.settings()
                hours = float(s.get("check_every_hours", "3") or 3)
                SCHED["keep_awake"] = s.get("keep_awake", "yes") != "no"
                pipeline.run_in_background()
        except Exception as ex:
            print("[scheduler]", ex, flush=True)
        pipeline.free_memory()
        refresh_if_heavy("after a round")
        hours = max(0.25, min(hours, 24))
        # agar abhi kaam mila tha to jaldi laut kar aao — ek baari me ek video
        wait = 0.2 if pipeline.STATUS.get("did_work") else hours
        nxt = dt.datetime.now() + dt.timedelta(hours=wait)
        SCHED["next_check"] = nxt.strftime("%d %b, %I:%M %p")
        time.sleep(wait * 3600)


AUTO = os.environ.get("DISABLE_BACKGROUND") != "1"


def start_background():
    if not AUTO:
        return
    threading.Thread(target=_keepalive_loop, daemon=True).start()
    threading.Thread(target=_scheduler_loop, daemon=True).start()


start_background()


# ---------------------------------------------------------------- chrome

CSS = """
:root{
  --paper:#FBFAF7; --card:#FFFFFF; --ink:#1C2333; --soft:#5C6472;
  --line:#E4E1D9; --green:#1F6F5C; --amber:#B4711A; --red:#A63A2B;
}
*{box-sizing:border-box}
body{margin:0;background:var(--paper);color:var(--ink);
  font:15px/1.6 -apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,sans-serif}
a{color:var(--green)}
.wrap{display:flex;min-height:100vh}
nav{width:212px;flex:0 0 212px;border-right:1px solid var(--line);padding:22px 0;
  background:#fff}
nav h1{font:600 15px/1.3 Georgia,"Times New Roman",serif;margin:0 20px 20px;
  letter-spacing:.2px}
nav h1 img{display:block;width:44px;height:44px;margin:0 0 9px}
nav h1 small{display:block;font:400 11px/1.4 inherit;color:var(--soft);
  font-family:-apple-system,sans-serif;margin-top:3px}
nav a{display:block;padding:9px 20px;color:var(--ink);text-decoration:none}
nav a:hover{background:#F4F2EC}
nav a.on{background:#F0F4F2;color:var(--green);box-shadow:inset 3px 0 0 var(--green);
  font-weight:600}
main{flex:1;padding:30px 34px 70px;max-width:920px;position:relative}
.clock{position:sticky;top:0;z-index:5;display:flex;justify-content:flex-end;
  gap:6px;align-items:baseline;margin:-20px 0 14px;padding:5px 0;
  background:linear-gradient(var(--paper) 70%,transparent);
  font-size:11.5px;color:#8A9099;white-space:nowrap;letter-spacing:.2px}
.clock b{font-weight:500;color:#6B727C;font-variant-numeric:tabular-nums}
@media(max-width:760px){.clock{margin:-6px 0 10px;font-size:11px}}
h2{font:600 24px/1.25 Georgia,serif;margin:0 0 4px}
.sub{color:var(--soft);margin:0 0 24px}
.card{background:var(--card);border:1px solid var(--line);border-radius:10px;
  padding:18px 20px;margin-bottom:16px}
.lead{border-left:3px solid var(--green)}
.row{display:flex;gap:10px;flex-wrap:wrap;align-items:center}
.btn{display:inline-block;background:var(--green);color:#fff;border:0;
  padding:9px 16px;border-radius:7px;font-size:14px;cursor:pointer;
  text-decoration:none}
.btn:hover{filter:brightness(1.08)}
.btn.ghost{background:#fff;color:var(--ink);border:1px solid var(--line)}
.btn.small{padding:5px 11px;font-size:13px}
.btn.wa{background:#25D366;color:#0B3B22;border:0;font-weight:600}
.btn.wa:hover{filter:brightness(1.05)}
input,select,textarea{font:inherit;padding:9px 11px;border:1px solid var(--line);
  border-radius:7px;background:#fff;color:var(--ink);width:100%}
label{display:block;font-size:13px;color:var(--soft);margin:12px 0 4px}
table{width:100%;border-collapse:collapse;font-size:14px}
th{text-align:left;font-weight:600;color:var(--soft);font-size:12.5px;
  padding:0 8px 8px;border-bottom:1px solid var(--line)}
td{padding:11px 8px;border-bottom:1px solid var(--line);vertical-align:top}
.note{color:var(--soft);font-size:13.5px}
.tag{display:inline-block;font-size:12px;padding:2px 8px;border-radius:20px;
  background:#F0F4F2;color:var(--green)}
.tag.warn{background:#FBF2E6;color:var(--amber)}
.tag.bad{background:#FAEDEA;color:var(--red)}
.dot{display:inline-block;width:8px;height:8px;border-radius:50%;
  background:var(--green);margin-right:7px}
.dot.idle{background:#C4C0B6}
.grid{display:grid;grid-template-columns:1fr 1fr;gap:16px}
.msg{padding:11px 15px;border-radius:8px;margin-bottom:18px;font-size:14px;
  background:#F0F4F2;border:1px solid #CFE0D9}
.msg.bad{background:#FAEDEA;border-color:#EBD2CC}
.summary{white-space:pre-wrap}
@media(max-width:760px){
  .wrap{display:block}
  nav{width:auto;border-right:0;border-bottom:1px solid var(--line);
    display:flex;overflow-x:auto;padding:0;gap:0}
  nav h1{display:none}
  nav a{padding:13px 15px;white-space:nowrap;font-size:14px}
  nav a.on{box-shadow:inset 0 -3px 0 var(--green)}
  main{padding:20px 16px 60px}
  .grid{grid-template-columns:1fr}
}
"""

NAV = [("/", "Dashboard"), ("/add", "Add video"), ("/library", "Library"),
       ("/channels", "Channels"), ("/sheets", "Spreadsheets"),
       ("/recipients", "Recipients"), ("/settings", "Settings"), ("/logs", "Logs")]

SHELL = """<!doctype html><html lang="hi"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<meta name="theme-color" content="#1F6F5C">
<link rel="icon" href="{{icon}}">
<link rel="manifest" href="/manifest.webmanifest">
<link rel="apple-touch-icon" href="/icon-192.png">
<meta name="apple-mobile-web-app-capable" content="yes">
<meta name="apple-mobile-web-app-status-bar-style" content="default">
<meta name="apple-mobile-web-app-title" content="Brief Desk">
<meta name="mobile-web-app-capable" content="yes">
<title>{{title}} · YouTube Brief Desk</title><style>{{css|safe}}</style></head>
<body><div class="wrap">
<nav><h1><img src="{{logo}}" alt=""><span>YouTube Brief Desk</span>
<small>build {{build}}</small></h1>
{% for href,label in nav %}<a href="{{href}}" class="{{'on' if href==active}}">{{label}}</a>{% endfor %}
</nav><main>
<div class="clock" id="clock"><span id="ckday"></span><span id="ckdate"></span>
  <b id="cktime"></b><span>IST</span></div>
{% if msg %}<div class="msg {{'bad' if bad else ''}}">{{msg}}</div>{% endif %}
{{body|safe}}
</main></div><script>
if ('serviceWorker' in navigator) {
  navigator.serviceWorker.register('/sw.js').catch(() => {});
}
(function tick() {
  var z = 'Asia/Kolkata', n = new Date();
  var f = function (o) { return new Intl.DateTimeFormat('en-IN',
      Object.assign({timeZone: z}, o)).format(n); };
  document.getElementById('ckday').textContent = f({weekday: 'short'}) + ' ·';
  document.getElementById('ckdate').textContent =
    f({day: '2-digit', month: 'short', year: 'numeric'}) + ' ·';
  document.getElementById('cktime').textContent =
    f({hour: '2-digit', minute: '2-digit', hour12: true});
  setTimeout(tick, 1000);
})();
</script></body></html>"""


def page(title, body, active="/"):
    u = current_user()
    r = role_of(u) if u else "viewer"
    nav = [("/", "Dashboard"), ("/library", "Library")] if r == "viewer" else list(NAV)
    if r == "owner" and ("/users", "People") not in nav:
        nav = nav[:-1] + [("/users", "People")] + nav[-1:]
    elif r == "admin":
        nav = nav[:-1] + [("/users", "People")] + nav[-1:]
    nav = nav + [("/me", "My account")]
    return render_template_string(
        SHELL, title=title, body=body, css=CSS, nav=nav, active=active,
        logo=LOGO, icon=ICON,
        build=BUILD, msg=request.args.get("msg"),
        bad=request.args.get("bad") == "1")


def back(where, msg="", bad=False):
    q = f"?msg={msg}" + ("&bad=1" if bad else "") if msg else ""
    return redirect(where + q)


def e(t):
    return html.escape(str(t or ""))


AUTH_OPEN = ("/login", "/register", "/verify", "/cron", "/static", "/oauth",
             "/healthz", "/forgot", "/manifest.webmanifest", "/icon-",
             "/sw.js", "/api/jobs", "/api/transcript")
VIEWER_OK = ("/", "/library", "/logout", "/api/status", "/me")
VIEWER_PREFIX = ("/photo/",)

PENDING = {}          # email -> {"code", "until", "row"} — OTP ka intezaar
OTP_MINUTES = 15


def users():
    try:
        return gapi.read_all()["users"]
    except Exception:
        return []


def find_user(ident):
    ident = (ident or "").strip().lower()
    digits = re.sub(r"\D", "", ident)
    for u in users():
        if (u.get("Email") or "").strip().lower() == ident:
            return u
        phone = re.sub(r"\D", "", u.get("Phone") or "")
        if digits and len(digits) >= 8 and phone.endswith(digits[-10:]):
            return u
    return None


def current_user():
    if session.get("master"):
        return {"Email": "owner", "Name": "Owner", "Role": "owner", "Status": "active"}
    em = session.get("user")
    if not em:
        return None
    for u in users():
        if (u.get("Email") or "").strip().lower() == em:
            return u
    return None


def role_of(u):
    return (u or {}).get("Role", "").strip().lower() or "viewer"


@app.before_request
def guard():
    if request.path.startswith(AUTH_OPEN):
        return
    u = current_user()
    if not u:
        return redirect(url_for("login"))
    if (u.get("Status") or "active").lower() == "pending":
        if request.path not in ("/logout", "/me"):
            return page("Waiting", "<h2>Almost there</h2><p class='sub'>Your account "
                        "is waiting for the owner to approve it.</p>"
                        "<a class='btn ghost' href='/logout'>Sign out</a>", "/")
        return
    if (u.get("Status") or "").lower() == "blocked":
        session.clear()
        return redirect(url_for("login"))
    if (role_of(u) == "viewer" and request.path not in VIEWER_OK
            and not request.path.startswith(VIEWER_PREFIX)):
        return back("/library", "You do not have access to that page.", True)
    if role_of(u) != "owner" and request.path == "/users" and request.method == "POST":
        return back("/users", "Only the owner can change people.", True)
    if not gapi.has_token() and request.path not in ("/", "/settings", "/logout",
                                                     "/healthz", "/api/status"):
        return redirect("/")


# ---------------------------------------------------------------- sign in

def hash_pw(p):
    from werkzeug.security import generate_password_hash
    return generate_password_hash(p)


def check_pw(stored, given):
    from werkzeug.security import check_password_hash
    try:
        return check_password_hash(stored or "", given or "")
    except Exception:
        return False


def auth_page(title, inner):
    return render_template_string(
        """<!doctype html><html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<link rel="icon" href="{{icon}}">
<link rel="manifest" href="/manifest.webmanifest">
<link rel="apple-touch-icon" href="/icon-192.png">
<meta name="apple-mobile-web-app-capable" content="yes">
<meta name="apple-mobile-web-app-status-bar-style" content="default">
<meta name="apple-mobile-web-app-title" content="Brief Desk">
<meta name="mobile-web-app-capable" content="yes">
<title>{{t}} · YouTube Brief Desk</title><style>{{css|safe}}
.auth{max-width:380px;margin:0 auto;padding:46px 18px 60px}
.brand{text-align:center;margin-bottom:26px}
.brand img{width:112px;height:112px;display:block;margin:0 auto 12px}
.brand h1{font:600 19px/1.3 Georgia,serif;margin:0}
.brand p{color:var(--soft);font-size:13px;margin:4px 0 0}
.auth .card{padding:20px}
.alt{text-align:center;margin-top:16px;font-size:14px}
</style></head><body><div class="auth">
<div class="brand"><img src="{{logo}}" alt="YouTube Brief Desk">
<p>transcripts, summaries and PDFs by email</p></div>
{% if msg %}<div class="msg {{'bad' if bad else ''}}">{{msg}}</div>{% endif %}
{{inner|safe}}</div><script>
if ('serviceWorker' in navigator) {
  navigator.serviceWorker.register('/sw.js').catch(() => {});
}
</script></body></html>""",
        t=title, css=CSS, inner=inner, logo=LOGO, icon=ICON,
        msg=request.args.get("msg"),
        bad=request.args.get("bad") == "1")


@app.route("/login", methods=["GET", "POST"])
def login():
    if request.method == "POST":
        ident = (request.form.get("ident") or "").strip()
        pw = request.form.get("password") or ""
        if UI_PASSWORD and ident.lower() in ("owner", "admin") and pw == UI_PASSWORD:
            session.clear()
            session["master"] = True
            session.permanent = True
            return redirect("/")
        u = find_user(ident)
        if not u or not check_pw(u.get("Password"), pw):
            return redirect("/login?msg=Wrong+phone%2Femail+or+password.&bad=1")
        if (u.get("Status") or "").lower() == "blocked":
            return redirect("/login?msg=This+account+is+blocked.&bad=1")
        session.clear()
        session["user"] = (u.get("Email") or "").strip().lower()
        session.permanent = True
        try:
            gapi.write_range("Users", f"J{u['_row']}", [[pipeline.now_str()]])
        except Exception:
            pass
        return redirect("/")

    inner = """<div class="card"><form method="post">
      <label>PHONE / EMAIL</label>
      <input name="ident" placeholder="Enter email or phone" autofocus required>
      <div class="note" style="margin-top:5px">Either one works.</div>
      <label>PASSWORD</label>
      <input type="password" name="password" required>
      <div style="margin-top:16px"><button class="btn" style="width:100%">Sign In</button></div>
    </form></div>
    <div class="card" style="margin-top:12px;text-align:center">
      <a class="btn ghost" style="width:100%" href="/register">Register</a></div>
    <div class="alt"><a href="/forgot">Forgotten your password?</a></div>"""
    return auth_page("Sign in", inner)


@app.route("/register", methods=["GET", "POST"])
def register():
    if request.method == "POST":
        f = request.form
        email = (f.get("email") or "").strip().lower()
        pw = f.get("password") or ""
        if "@" not in email or "." not in email:
            return redirect("/register?msg=Enter+a+valid+email.&bad=1")
        if len(pw) < 6:
            return redirect("/register?msg=Password+needs+6+characters+or+more.&bad=1")
        if pw != (f.get("password2") or ""):
            return redirect("/register?msg=The+two+passwords+do+not+match.&bad=1")
        if find_user(email):
            return redirect("/register?msg=That+email+is+already+registered.&bad=1")

        first = not users()
        phone = (f.get("phone") or "").strip()
        where = (f.get("otp_to") or "email").lower()
        st = pipeline.settings()
        if where == "phone" and not pipeline.sms_ready(st):
            return redirect("/register?msg=SMS+is+not+set+up+yet%2C+please+use+"
                            "email.&bad=1")
        code = f"{secrets_below(1000000):06d}"
        PENDING[email] = {
            "code": code,
            "until": dt.datetime.now() + dt.timedelta(minutes=OTP_MINUTES),
            "row": [email, (f.get("name") or "").strip(),
                    phone, (f.get("gender") or "").strip(),
                    (f.get("address") or "").strip(),
                    "owner" if first else "viewer",
                    "active" if first else "pending",
                    hash_pw(pw), dt.date.today().strftime("%d %b %Y"), ""],
        }
        try:
            if where == "phone":
                pipeline.send_sms(phone,
                                  f"{code} is your YouTube Brief Desk verification "
                                  f"code. Valid for {OTP_MINUTES} minutes.", st)
                PENDING[email]["sent_to"] = "your phone " + phone
            else:
                gapi.send_mail([email], "Your YouTube Brief Desk code",
                               f"Your verification code is {code}\n\n"
                               f"It is valid for {OTP_MINUTES} minutes.\n")
                PENDING[email]["sent_to"] = email
        except Exception as ex:
            PENDING.pop(email, None)
            return redirect(f"/register?msg=Could+not+send+the+code:+{e(ex)}&bad=1")
        return redirect(f"/verify?email={email}")

    inner = """<div class="card"><form method="post">
      <label>FULL NAME</label><input name="name" required>
      <label>MOBILE NUMBER</label>
      <input name="phone" placeholder="+91 98xxxxxxxx" required>
      <div class="note" style="margin-top:5px">Please add country code if you are a
        user outside of India.</div>
      <label>EMAIL</label><input type="email" name="email" required>
      <label>GENDER</label>
      <select name="gender">
        <option value="">Prefer not to say</option>
        <option>Female</option><option>Male</option><option>Other</option></select>
      <label>ADDRESS</label><textarea name="address" rows="2"></textarea>
      <label>PASSWORD</label><input type="password" name="password" required>
      <label>REPEAT PASSWORD</label><input type="password" name="password2" required>
      <label>WHERE SHOULD THE CODE GO?</label>
      <div class="row" style="gap:16px">
        <label style="margin:0"><input type="radio" name="otp_to" value="email"
          checked style="width:auto"> Email</label>
        <label style="margin:0"><input type="radio" name="otp_to" value="phone"
          style="width:auto" {dis}> SMS on my phone{note}</label>
      </div>
      <div style="margin-top:16px">
        <button class="btn" style="width:100%">Send code</button></div>
    </form></div>
    <div class="alt">Already have an account? <a href="/login">Sign in</a></div>"""
    try:
        ready = pipeline.sms_ready()
    except Exception:
        ready = False
    inner = inner.format(dis="" if ready else "disabled",
                         note="" if ready else " (not set up)")
    return auth_page("Register", inner)


def secrets_below(n):
    import secrets as _s
    return _s.randbelow(n)


@app.route("/verify", methods=["GET", "POST"])
def verify():
    email = (request.args.get("email") or request.form.get("email") or "").strip().lower()
    item = PENDING.get(email)
    if request.method == "POST":
        if not item:
            return redirect("/register?msg=That+request+expired.+Please+start+again.&bad=1")
        if dt.datetime.now() > item["until"]:
            PENDING.pop(email, None)
            return redirect("/register?msg=The+code+expired.+Please+start+again.&bad=1")
        if (request.form.get("code") or "").strip() != item["code"]:
            return redirect(f"/verify?email={email}&msg=Wrong+code.&bad=1")
        gapi.append_row("Users", item["row"])
        PENDING.pop(email, None)
        session.clear()
        session["user"] = email
        session.permanent = True
        return redirect("/")
    inner = f"""<div class="card"><form method="post">
      <input type="hidden" name="email" value="{e(email)}">
      <p class="note" style="margin-top:0">We sent a 6-digit code to
        <strong>{e((PENDING.get(email) or {}).get('sent_to') or email)}</strong>.
        It is valid for {OTP_MINUTES} minutes.</p>
      <label>CODE</label>
      <input name="code" inputmode="numeric" autofocus required>
      <div style="margin-top:16px">
        <button class="btn" style="width:100%">Verify</button></div>
    </form></div>
    <div class="alt"><a href="/register">Start again</a></div>"""
    return auth_page("Verify", inner)


@app.route("/forgot", methods=["GET", "POST"])
def forgot():
    if request.method == "POST":
        u = find_user(request.form.get("ident"))
        if u and u.get("Email"):
            new = f"{secrets_below(1000000):06d}"
            try:
                gapi.write_range("Users", f"H{u['_row']}", [[hash_pw(new)]])
                gapi.send_mail([u["Email"]], "Your new password",
                               f"Your new password is {new}\n\n"
                               "Please sign in and change it from your profile.\n")
            except Exception:
                pass
        return redirect("/login?msg=If+that+account+exists%2C+a+new+password+has+"
                        "been+emailed.")
    inner = """<div class="card"><form method="post">
      <label>PHONE / EMAIL</label><input name="ident" autofocus required>
      <div style="margin-top:16px">
        <button class="btn" style="width:100%">Email me a new password</button></div>
    </form></div><div class="alt"><a href="/login">Back to sign in</a></div>"""
    return auth_page("Password", inner)


MAX_PHOTO = 4 * 1024 * 1024          # 4 MB tak ki tasveer
PHOTO_TYPES = {"image/jpeg", "image/png", "image/webp", "image/gif"}


def avatar(u, size=40):
    """Tasveer ho to tasveer, warna naam ka pehla akshar."""
    fid = (u or {}).get("Photo") or ""
    if fid:
        return (f'<img src="/photo/{e(fid)}" alt="" style="width:{size}px;'
                f'height:{size}px;border-radius:50%;object-fit:cover;'
                f'border:1px solid var(--line)">')
    letter = ((u or {}).get("Name") or (u or {}).get("Email") or "?")[:1].upper()
    return (f'<span style="display:inline-block;width:{size}px;height:{size}px;'
            f'border-radius:50%;background:#EDEAE2;color:var(--soft);'
            f'font:600 {int(size * 0.42)}px/{size}px Georgia,serif;'
            f'text-align:center">{e(letter)}</span>')


@app.route("/photo/<file_id>")
def photo(file_id):
    try:
        data = gapi.download_file(file_id)
    except Exception:
        return Response(status=404)
    return Response(data, mimetype="image/jpeg",
                    headers={"Cache-Control": "private, max-age=86400"})


@app.route("/me", methods=["GET", "POST"])
def me():
    u = current_user() or {}
    row = u.get("_row")

    if request.method == "POST" and row:
        f = request.form
        try:
            if f.get("action") == "photo":
                up = request.files.get("photo")
                if not up or not up.filename:
                    return back("/me", "Please choose a picture first.", True)
                raw = up.read(MAX_PHOTO + 1)
                if len(raw) > MAX_PHOTO:
                    return back("/me", "That picture is over 4 MB.", True)
                kind = (up.mimetype or "").lower()
                if kind not in PHOTO_TYPES:
                    return back("/me", "Only JPG, PNG, WEBP or GIF pictures.", True)
                old_id = u.get("Photo") or ""
                fid = gapi.upload_image(f"{u.get('Email', 'user')}-photo", raw, kind)
                gapi.write_range("Users", f"K{row}", [[fid]])
                if old_id:
                    gapi.delete_file(old_id)
                return back("/me", "Picture updated.")

            if f.get("action") == "remove_photo":
                if u.get("Photo"):
                    gapi.delete_file(u["Photo"])
                gapi.write_range("Users", f"K{row}", [[""]])
                return back("/me", "Picture removed.")

            if f.get("action") == "password":
                if not check_pw(u.get("Password"), f.get("old") or ""):
                    return back("/me", "Your current password did not match.", True)
                new = f.get("new") or ""
                if len(new) < 6:
                    return back("/me", "The new password needs 6 characters "
                                "or more.", True)
                if new != (f.get("new2") or ""):
                    return back("/me", "The two new passwords do not match.", True)
                gapi.write_range("Users", f"H{row}", [[hash_pw(new)]])
                return back("/me", "Password changed.")

            gapi.write_range("Users", f"B{row}:E{row}",
                             [[f.get("name", "").strip(), f.get("phone", "").strip(),
                               f.get("gender", "").strip(),
                               f.get("address", "").strip()]])
            return back("/me", "Saved.")
        except Exception as ex:
            return back("/me", f"Could not save: {ex}", True)

    def sel(v):
        return "selected" if (u.get("Gender") or "") == v else ""

    remove_form = ""
    if u.get("Photo"):
        remove_form = ('<form method="post" style="margin-top:8px">'
                       '<input type="hidden" name="action" value="remove_photo">'
                       '<button class="btn small ghost">Remove picture</button></form>')

    body = f"""<h2>My account</h2>
    <div class="card">
      <div class="row" style="align-items:center;gap:14px">
        {avatar(u, 72)}
        <div>
          <div style="font:600 17px/1.3 Georgia,serif">{e(u.get('Name') or '—')}</div>
          <div class="note">{e(u.get('Email'))} &nbsp;·&nbsp;
            <span class="tag">{e(role_of(u))}</span></div>
          <div class="note">last seen {e(u.get('Last seen') or '—')}</div>
        </div>
      </div>
      <form method="post" enctype="multipart/form-data" style="margin-top:14px">
        <input type="hidden" name="action" value="photo">
        <label>Change picture (JPG, PNG or WEBP — up to 4 MB)</label>
        <input type="file" name="photo" accept="image/*">
        <div class="row" style="margin-top:11px">
          <button class="btn small">Upload</button></div>
      </form>
      {remove_form}
    </div>

    <div class="card"><form method="post">
      <div class="grid">
        <div><label>Full name</label>
          <input name="name" value="{e(u.get('Name'))}"></div>
        <div><label>Mobile number</label>
          <input name="phone" value="{e(u.get('Phone'))}"></div>
      </div>
      <div class="grid" style="margin-top:10px">
        <div><label>Gender</label>
          <select name="gender">
            <option value="" {sel('')}>Prefer not to say</option>
            <option {sel('Female')}>Female</option>
            <option {sel('Male')}>Male</option>
            <option {sel('Other')}>Other</option>
          </select></div>
        <div></div>
      </div>
      <label>Address</label>
      <textarea name="address" rows="2">{e(u.get('Address'))}</textarea>
      <div style="margin-top:13px"><button class="btn">Save</button></div>
    </form></div>

    <div class="card"><form method="post">
      <input type="hidden" name="action" value="password">
      <div class="note" style="margin-bottom:8px">Change password</div>
      <div class="grid">
        <div><label>Current password</label>
          <input type="password" name="old"></div>
        <div><label>New password</label>
          <input type="password" name="new"></div>
      </div>
      <label>Repeat new password</label>
      <input type="password" name="new2">
      <div style="margin-top:13px"><button class="btn">Change password</button></div>
    </form></div>

    <div class="card"><a class="btn small ghost" href="/logout">Sign out</a></div>"""
    return page("My account", body, "/me")


@app.route("/logout")
def logout():
    session.clear()
    return redirect("/login")


# ---------------------------------------------------------------- people

@app.route("/users", methods=["GET", "POST"])
def users_page():
    data = gapi.read_all(force=request.method == "POST")
    if request.method == "POST":
        email = (request.form.get("email") or "").strip().lower()
        action = request.form.get("action")
        row = next((u for u in data["users"]
                    if (u.get("Email") or "").strip().lower() == email), None)
        if not row:
            return back("/users", "Not found.", True)
        if role_of(row) == "owner" and action in ("role", "block", "delete"):
            return back("/users", "The owner cannot be changed here.", True)
        try:
            if action == "role":
                gapi.write_range("Users", f"F{row['_row']}",
                                 [[request.form.get("role", "viewer")]])
                gapi.write_range("Users", f"G{row['_row']}", [["active"]])
                return back("/users", "Updated.")
            if action == "block":
                gapi.write_range("Users", f"G{row['_row']}", [["blocked"]])
                return back("/users", "Blocked.")
            if action == "delete":
                keep = [[u.get(k) for k in gapi.TABS["Users"]]
                        for u in data["users"]
                        if (u.get("Email") or "").strip().lower() != email]
                gapi.replace_tab("Users", gapi.TABS["Users"], keep)
                return back("/users", "Removed.")
        except Exception as ex:
            return back("/users", f"Could not do that: {ex}", True)

    def card(u):
        st = (u.get("Status") or "active").lower()
        tag = {"active": "tag", "pending": "tag warn", "blocked": "tag bad"}.get(st, "tag")
        buttons = ""
        if role_of(u) != "owner":
            buttons = f"""<form method="post" class="row" style="margin:0;gap:6px">
              <input type="hidden" name="email" value="{e(u.get('Email'))}">
              <select name="role" style="width:auto">
                <option value="viewer" {'selected' if role_of(u) == 'viewer' else ''}>Viewer</option>
                <option value="admin" {'selected' if role_of(u) == 'admin' else ''}>Admin</option>
              </select>
              <button class="btn small" name="action" value="role">
                {'Approve' if st == 'pending' else 'Save'}</button>
              <button class="btn small ghost" name="action" value="block">Block</button>
              <button class="btn small ghost" name="action" value="delete">Remove</button>
            </form>"""
        return f"""<tr><td style="width:52px;vertical-align:top">{avatar(u, 40)}</td>
            <td><strong>{e(u.get('Name') or u.get('Email'))}</strong>
            <span class="{tag}" style="margin-left:7px">{e(st)}</span>
            <span class="tag" style="margin-left:4px">{e(role_of(u))}</span>
            <div class="note" style="margin-top:5px">{e(u.get('Email'))}
              &nbsp;·&nbsp; {e(u.get('Phone'))}
              &nbsp;·&nbsp; {e(u.get('Gender') or '—')}</div>
            <div class="note">{e(u.get('Address') or '')}</div>
            <div class="note">joined {e(u.get('Added'))} &nbsp;·&nbsp;
              last seen {e(u.get('Last seen') or '—')}</div>
            <div style="margin-top:9px">{buttons}</div></td></tr>"""

    us = data["users"]
    waiting = [u for u in us if (u.get("Status") or "").lower() == "pending"]
    body = f"""<h2>People</h2>
    <p class="sub">{len(us)} accounts{f' · {len(waiting)} waiting for approval' if waiting else ''}.
      Anyone can register from the sign-in screen; they can only see anything once
      you approve them.</p>
    {'<div class="card"><table>' + ''.join(card(u) for u in us) + '</table></div>'
     if us else '<p class="note">No accounts yet.</p>'}"""
    return page("People", body, "/users")


# ---------------------------------------------------------------- dashboard

@app.route("/")
def dashboard():
    if not gapi.has_token():
        return page("Setup", SETUP_BODY, "/")
    try:
        gapi.ensure_tabs()
        data = gapi.read_all()
        eps = [r for rs in data["rows"].values() for r in rs]
        chans = data["channels"]
        recips = [r for r in data["recipients"] if r.get("Active", "yes") != "no"]
    except Exception as ex:
        return page("Dashboard", f"<h2>Dashboard</h2><div class='msg bad'>"
                    f"Could not reach Google: {e(ex)}</div>", "/")

    s = data["settings"]
    st = pipeline.STATUS
    latest = eps[-1] if eps else None

    if latest:
        lead = f"""<div class="card lead">
          <div class="note">Latest · {e(latest.get('Date'))}</div>
          <h3 style="margin:6px 0 10px;font:600 19px/1.3 Georgia,serif">{e(latest.get('Title'))}</h3>
          <div class="summary" style="color:#3A4152">{e((latest.get('Summary') or '')[:420])}…</div>
          <div class="row" style="margin-top:14px">
            <a class="btn small" href="{e(latest.get('PDF'))}" target="_blank">Open PDF</a>
            <a class="btn small ghost" href="{e(latest.get('Video Link'))}" target="_blank">Video</a>
            <a class="btn small wa" href="{e(wa_link(latest, limit=int(s.get('whatsapp_length', '1200') or 1200)))}"
               target="_blank" rel="noopener">WhatsApp</a>
            <a class="btn small ghost" href="/library">See all</a>
          </div></div>"""
    else:
        lead = ("<div class='card lead'><h3 style='margin:0 0 6px'>Nothing yet</h3>"
                "<p class='note' style='margin:0'>Add a channel and an email address — "
                "the next video will be picked up automatically.</p>"
                "<div class='row' style='margin-top:14px'>"
                "<a class='btn small' href='/channels'>Add a channel</a></div></div>")

    blocked = pipeline.credits_blocked_until(s)
    warn = ""
    if blocked:
        warn = f"""<div class="card" style="border-color:#EBD2CC;background:#FCF6F4">
          <strong>Supadata credits are used up for this month.</strong>
          <p class="note" style="margin:6px 0 0">New videos are waiting in line —
            nothing is lost. They will be picked up as soon as credits are back.
            Paused until {e(blocked.strftime('%d %b, %I:%M %p'))}.</p>
          <div class="row" style="margin-top:11px">
            <a class="btn small ghost" href="https://dash.supadata.ai" target="_blank">
              Open Supadata</a>
            <form method="post" action="/unblock" style="margin:0">
              <button class="btn small ghost">I have added credits — try now</button>
            </form>
          </div></div>"""

    running = st["running"]
    state = (f"<span class='dot'></span>{e(st['step']) or 'working'}"
             if running else
             f"<span class='dot idle'></span>{e(st['last_result'])}")

    body = f"""<h2>Dashboard</h2><p class="sub">{len(eps)} episodes sent so far.</p>
    {warn}
    {lead}
    <div class="card"><div class="row" style="justify-content:space-between">
      <div id="state">{state}</div>
      <div class="row" style="gap:7px">
        <form method="post" action="/run" style="margin:0">
          <button class="btn small" {'disabled' if running else ''}>Run now</button>
        </form>
        <form method="post" action="/refresh" style="margin:0">
          <button class="btn small ghost" {'disabled' if running else ''}
            title="Free the memory and start fresh">Refresh app</button>
        </form>
      </div></div>
      <div class="note" style="margin-top:10px">Memory: {pipeline.memory_mb()} MB of 512
        &nbsp;·&nbsp; Auto: {'on' if AUTO else 'OFF — remove DISABLE_BACKGROUND in Render'}
        &nbsp;·&nbsp; Last run: {e(st['last_run'] or '—')}
        &nbsp;·&nbsp; Next check: {e(SCHED['next_check'] or 'shortly')}
        &nbsp;·&nbsp; {'Staying awake' if SCHED['keep_awake'] else 'May sleep'}</div>
    </div>
    <div class="grid">
      <div class="card"><div class="note">Channels</div>
        <div style="font:600 22px/1.4 Georgia,serif">{len(chans)}</div>
        <a class="note" href="/channels">change</a></div>
      <div class="card"><div class="note">Emails go to</div>
        <div style="font:600 22px/1.4 Georgia,serif">{len(recips)} people</div>
        <a class="note" href="/recipients">change</a></div>
    </div>
    <div class="card"><div class="note" style="margin-bottom:8px">Your records</div>
      <div class="row">
        <a class="btn small ghost" href="{e(safe_sheet_url())}" target="_blank">Google Sheet</a>
        <a class="btn small ghost" href="{e(safe_drive_url())}" target="_blank">Drive folder (PDFs)</a>
      </div></div>
    <script>
    setInterval(async () => {{
      const r = await fetch('/api/status'); const d = await r.json();
      document.getElementById('state').innerHTML =
        d.running ? "<span class='dot'></span>" + d.step
                  : "<span class='dot idle'></span>" + d.last_result;
    }}, 4000);
    </script>"""
    return page("Dashboard", body, "/")


def wa_link(row, phone="", limit=1200):
    """WhatsApp kholne wala link, sandesh pehle se bhara hua."""
    from urllib.parse import quote
    title = (row.get("Title") or "").strip()
    summary = re.sub(r"\s+\n", "\n", (row.get("Summary") or "").strip())
    if len(summary) > limit:
        summary = summary[:limit].rsplit(" ", 1)[0] + "…"
    parts = [title]
    if row.get("Channel") or row.get("Date"):
        parts.append(f"{row.get('Channel', '')} · {row.get('Date', '')}".strip(" ·"))
    parts += ["", summary, ""]
    if row.get("Video Link"):
        parts.append(f"Video: {row['Video Link']}")
    if row.get("PDF"):
        parts.append(f"PDF: {row['PDF']}")
    text = quote("\n".join(p for p in parts if p is not None))
    digits = re.sub(r"\D", "", phone or "")
    if digits and len(digits) == 10:
        digits = "91" + digits
    return (f"https://wa.me/{digits}?text={text}" if digits
            else f"https://wa.me/?text={text}")


def safe_sheet_url():
    try:
        return gapi.sheet_url()
    except Exception:
        return "#"


def safe_drive_url():
    try:
        return gapi.drive_root_url()
    except Exception:
        return "#"


@app.route("/api/status")
def api_status():
    return jsonify(pipeline.STATUS)


@app.route("/unblock", methods=["POST"])
def unblock():
    if role_of(current_user()) not in ("owner", "admin"):
        return back("/", "Only the owner can do that.", True)
    gapi.set_settings({"supadata_blocked_until": ""})
    pipeline.run_in_background(manual=True)
    return back("/", "Trying again now.")


@app.route("/run", methods=["POST"])
def run_now():
    started = pipeline.run_in_background(manual=True)
    return back("/", "Started." if started else "Already running.")


@app.route("/cron")
def cron():
    if CRON_KEY and request.args.get("key") != CRON_KEY:
        return Response("no", status=403)
    started = pipeline.run_in_background()
    return jsonify({"started": started, "status": pipeline.STATUS})


# ---------------------------------------------------------------- channels

@app.route("/channels", methods=["GET", "POST"])
def channels():
    if request.method == "POST":
        action = request.form.get("action")
        try:
            rows = gapi.read_all(force=True)["channels"]
            if action == "add":
                cid, name = pipeline.resolve_channel(request.form.get("channel", ""))
                if any(r["Channel ID"] == cid for r in rows):
                    return back("/channels", "That channel is already added.", True)
                tab = (request.form.get("new_tab", "").strip()
                       or request.form.get("tab", "").strip() or "Episodes")
                book = (request.form.get("book", "") or gapi.MAIN).strip()
                pipeline.destination({"Spreadsheet": book, "Sheet tab": tab},
                                     gapi.read_all(force=True))
                gapi.append_row("Channels", [cid, name,
                                             dt.date.today().strftime("%d %b %Y"),
                                             "yes", tab, book])
                return back("/channels", f"{name} added.")
            if action == "delete":
                keep = [[r.get("Channel ID"), r.get("Name"), r.get("Added On"),
                         r.get("Active"), r.get("Sheet tab"), r.get("Spreadsheet")]
                        for r in rows if r.get("Channel ID") != request.form.get("cid")]
                gapi.replace_tab("Channels", gapi.TABS["Channels"], keep)
                return back("/channels", "Removed.")
        except Exception as ex:
            return back("/channels", f"Nahi ho paya: {ex}", True)

    d = gapi.read_all()
    rows = d["channels"]
    copts = "".join(f'<option value="{e(t)}" {"selected" if t == "Episodes" else ""}>{e(t)}</option>'
                    for t in d["tabs"])
    trs = "".join(f"""<tr><td><strong>{e(r.get('Name'))}</strong>
        <div class="note">{e(r.get('Channel ID'))}</div></td>
        <td class="note">{e(r.get('Added On'))}<br>
          <span class="tag">{e(r.get('Spreadsheet') or gapi.MAIN)} ›
            {e(r.get('Sheet tab') or 'Episodes')}</span></td>
        <td style="text-align:right"><form method="post" style="margin:0">
        <input type="hidden" name="action" value="delete">
        <input type="hidden" name="cid" value="{e(r.get('Channel ID'))}">
        <button class="btn small ghost">Remove</button></form></td></tr>""" for r in rows)
    body = f"""<h2>Channels</h2>
    <p class="sub">Channels being watched.</p>
    <div class="card"><form method="post">
      <input type="hidden" name="action" value="add">
      <label>Channel link, @handle, or the UC… channel ID</label>
      <input name="channel" placeholder="https://www.youtube.com/@channelname" required>
      <div class="grid" style="margin-top:10px">
        <div><label>Spreadsheet</label>
          <select name="book">{sheet_options(d)}</select></div>
        <div><label>Tab</label><select name="tab">{copts}</select></div>
      </div>
      <label>…or a new tab</label>
      <input name="new_tab" placeholder="e.g. Morning talks">
      <div style="margin-top:13px"><button class="btn">Add</button></div>
    </form></div>
    {'<div class="card"><table><tr><th>Channel</th><th>Added</th><th></th></tr>'
     + trs + '</table></div>' if rows else
     '<p class="note">No channels yet.</p>'}"""
    return page("Channels", body, "/channels")


# ---------------------------------------------------------------- recipients

@app.route("/recipients", methods=["GET", "POST"])
def recipients():
    if request.method == "POST":
        rows = gapi.read_all(force=True)["recipients"]
        if request.form.get("action") == "add":
            email = (request.form.get("email") or "").strip()
            if "@" not in email:
                return back("/recipients", "That email address does not look right.", True)
            gapi.append_row("Recipients", [email, request.form.get("name", ""), "yes",
                                           request.form.get("phone", "").strip()])
            return back("/recipients", "Added.")
        keep = [[r.get("Email"), r.get("Name"), r.get("Active"), r.get("Phone")]
                for r in rows if r.get("Email") != request.form.get("email")]
        gapi.replace_tab("Recipients", gapi.TABS["Recipients"], keep)
        return back("/recipients", "Removed.")

    rows = gapi.read_all()["recipients"]
    latest = (gapi.read_all()["episodes"] or [{}])[-1]
    trs = "".join(f"""<tr><td>{e(r.get('Email'))}
        <div class="note">{e(r.get('Name'))} {e(r.get('Phone') or '')}</div></td>
        <td style="text-align:right">
        {f'<a class="btn small wa" target="_blank" rel="noopener" href="{e(wa_link(latest, r.get("Phone")))}">WhatsApp</a>&nbsp;' if r.get('Phone') and latest else ''}
        <form method="post" style="margin:0;display:inline-block">
        <input type="hidden" name="action" value="delete">
        <input type="hidden" name="email" value="{e(r.get('Email'))}">
        <button class="btn small ghost">Remove</button></form></td></tr>""" for r in rows)
    body = f"""<h2>Recipients</h2>
    <p class="sub">Every new video's PDF goes to these people.</p>
    <div class="card"><form method="post">
      <input type="hidden" name="action" value="add">
      <div class="grid"><div><label>Email</label>
        <input name="email" type="email" placeholder="name@example.com" required></div>
        <div><label>Name (optional)</label><input name="name"></div></div>
      <label>WhatsApp number (optional — gives a one-tap button for this person)</label>
      <input name="phone" placeholder="+91 98765 43210">
      <div style="margin-top:13px"><button class="btn">Add</button></div>
    </form></div>
    {'<div class="card"><table>' + trs + '</table></div>' if rows else
     '<p class="note">No recipients yet.</p>'}"""
    return page("Recipients", body, "/recipients")


# ---------------------------------------------------------------- add video

@app.route("/add", methods=["GET", "POST"])
def add_video():
    if request.method == "POST":
        raw = request.form.get("links", "")
        emails = request.form.get("emails", "").strip()
        instruction = request.form.get("instruction", "").strip()
        target = (request.form.get("new_tab", "").strip()
                  or request.form.get("tab", "").strip() or "Links")
        book = (request.form.get("book", "") or gapi.MAIN).strip()
        try:
            sid, target = pipeline.destination(
                {"Spreadsheet": book, "Sheet tab": target},
                gapi.read_all(force=True), default_tab="Links")
        except Exception as ex:
            return back("/add", f"Could not prepare that sheet: {ex}", True)
        found, bad = [], []
        for line in re.split(r"[\s,]+", raw):
            line = line.strip()
            if not line:
                continue
            vid = pipeline.extract_video_id(line)
            (found if vid else bad).append(vid or line)
        if not found:
            return back("/add", "No YouTube links found in that text.", True)
        today = dt.date.today().strftime("%d %b %Y")
        gapi.append_rows("Queue", [
            [f"https://www.youtube.com/watch?v={v}", today, emails, "pending",
             instruction, target, book] for v in found])
        pipeline.run_in_background(manual=True)
        note = (f"{len(found)} video(s) queued for {book} → {target}. "
                "They will be emailed shortly.")
        if bad:
            note += f" {len(bad)} line(s) skipped."
        return back("/add", note)

    d = gapi.read_all()
    q = d["queue"]
    opts = "".join(f'<option value="{e(t)}" {"selected" if t == "Links" else ""}>{e(t)}</option>'
                   for t in d["tabs"])
    waiting = [r for r in q if (r.get("Status") or "").lower() == "pending"]
    recent = list(reversed(q))[:15]
    trs = "".join(f"""<tr><td>{e(r.get('Video Link'))}</td>
        <td class="note" style="white-space:nowrap">{e(r.get('Added'))}</td>
        <td class="note">{e(r.get('Status'))}</td></tr>""" for r in recent)
    body = f"""<h2>Add video</h2>
    <p class="sub">Paste any YouTube links — they do not have to be from your
      channels. Each one gets a transcript, an English summary, a PDF by email,
      and a row in whichever tab of your Sheet you pick — an existing one, or a
      new one you name here. You can also say what should be written instead of
      the usual summary.</p>
    <div class="card"><form method="post">
      <label>YouTube links (one per line)</label>
      <textarea name="links" rows="5"
        placeholder="https://www.youtube.com/watch?v=..."></textarea>
      <label>Send to (optional — leave blank to use your usual recipients)</label>
      <input name="emails" placeholder="someone@example.com, another@example.com">
      <div class="grid">
        <div><label>Spreadsheet</label>
          <select name="book">{sheet_options(d)}</select></div>
        <div><label>Tab</label><select name="tab">{opts}</select></div>
      </div>
      <label>…or type a new tab name</label>
      <input name="new_tab" placeholder="e.g. Court hearings">
      <label>What should be written? (optional — leave blank for your usual
        setting in Settings)</label>
      <textarea name="instruction" rows="3"
        placeholder="e.g. List the main arguments as bullet points, with the legal
points first. Or: write it as questions and answers. Or: a 200-word note for a
newsletter."></textarea>
      <div style="margin-top:13px"><button class="btn">Fetch and send</button></div>
    </form></div>
    {f'<p class="note">{len(waiting)} waiting in the queue.</p>' if waiting else ''}
    {'<div class="card"><table><tr><th>Link</th><th>Added</th><th>Status</th></tr>'
     + trs + '</table></div>' if recent else ''}"""
    return page("Add video", body, "/add")


# ---------------------------------------------------------------- spreadsheets

def sheet_options(data, selected=""):
    names = [gapi.MAIN] + [(sh.get("Name") or "").strip()
                           for sh in data.get("sheets", []) if sh.get("Name")]
    return "".join(f'<option value="{e(n)}" {"selected" if n == selected else ""}>'
                   f'{e(n)}</option>' for n in names)


@app.route("/sheets", methods=["GET", "POST"])
def sheets_page():
    if request.method == "POST":
        action = request.form.get("action")
        try:
            rows = gapi.read_all(force=True)["sheets"]
            if action == "delete":
                keep = [[r.get("Name"), r.get("Spreadsheet ID"), r.get("Link"),
                         r.get("Added")] for r in rows
                        if r.get("Name") != request.form.get("name")]
                gapi.replace_tab("Sheets", gapi.TABS["Sheets"], keep)
                return back("/sheets", "Removed from the list. The file itself is "
                            "untouched.")

            name = (request.form.get("name") or "").strip()
            if not name or name.lower() == gapi.MAIN.lower():
                return back("/sheets", "Give it a different short name.", True)
            if any((r.get("Name") or "").lower() == name.lower() for r in rows):
                return back("/sheets", "That name is already used.", True)

            link = (request.form.get("link") or "").strip()
            if link:
                sid = gapi.sid_from(link)
                if not sid:
                    return back("/sheets", "That does not look like a Google "
                                "Sheets link.", True)
                title = gapi.spreadsheet_title(sid)       # pahunch ki jaanch
                made = {"id": sid, "link": gapi.sheet_link(sid), "title": title}
            else:
                made = gapi.make_spreadsheet(name,
                                             request.form.get("tab", "Episodes").strip()
                                             or "Episodes")
            gapi.append_row("Sheets", [name, made["id"], made["link"],
                                       dt.date.today().strftime("%d %b %Y")])
            return back("/sheets", f"{name} is ready.")
        except Exception as ex:
            return back("/sheets", f"Could not add it: {ex}", True)

    data = gapi.read_all()
    trs = "".join(f"""<tr><td><strong>{e(r.get('Name'))}</strong>
        <div class="note">added {e(r.get('Added'))}</div></td>
        <td><a href="{e(r.get('Link'))}" target="_blank">open</a></td>
        <td style="text-align:right"><form method="post" style="margin:0">
        <input type="hidden" name="action" value="delete">
        <input type="hidden" name="name" value="{e(r.get('Name'))}">
        <button class="btn small ghost">Remove</button></form></td></tr>"""
        for r in data["sheets"])
    body = f"""<h2>Spreadsheets</h2>
    <p class="sub">Rows normally go to your main sheet. Add other spreadsheet
      files here and you can send a channel — or any pasted link — to one of them
      instead.</p>
    <div class="card"><form method="post">
      <input type="hidden" name="action" value="add">
      <div class="grid">
        <div><label>Short name (you pick this)</label>
          <input name="name" placeholder="e.g. Court channel" required></div>
        <div><label>First tab name (for a brand-new file)</label>
          <input name="tab" placeholder="Episodes"></div>
      </div>
      <label>Link to an existing Google Sheet — leave blank to create a new file</label>
      <input name="link" placeholder="https://docs.google.com/spreadsheets/d/...">
      <div style="margin-top:13px"><button class="btn">Add</button></div>
    </form></div>
    <div class="card"><table>
      <tr><td><strong>{e(gapi.MAIN)}</strong>
        <div class="note">the sheet this app made</div></td>
      <td><a href="{e(safe_sheet_url())}" target="_blank">open</a></td>
      <td></td></tr>{trs}</table></div>"""
    return page("Spreadsheets", body, "/sheets")


# ---------------------------------------------------------------- library

@app.route("/library")
def library():
    q = (request.args.get("q") or "").lower().strip()
    data = gapi.read_all()
    book = request.args.get("book") or gapi.MAIN
    sid = ""
    if book != gapi.MAIN:
        for sh in data["sheets"]:
            if (sh.get("Name") or "") == book:
                sid = (sh.get("Spreadsheet ID") or "").strip()
        if not sid:
            book, sid = gapi.MAIN, ""
    try:
        tabs = (data["tabs"] if not sid
                else [t for t in gapi.tabs_in(sid) if t not in gapi.SYSTEM_TABS])
    except Exception:
        tabs = []
    tabs = tabs or ["Episodes"]
    which = request.args.get("tab") or tabs[0]
    if which not in tabs:
        which = tabs[0]
    try:
        rows = list(reversed(gapi.rows_in(sid, which)))
    except Exception:
        rows = []
    if q:
        rows = [r for r in rows if q in (r.get("Title", "") + r.get("Summary", "")).lower()]
    trs = "".join(f"""<tr>
        <td class="note" style="white-space:nowrap">{e(r.get('Date'))}<br>
          <span class="tag">Ep {e(r.get('Episode'))}</span></td>
        <td><strong>{e(r.get('Title'))}</strong>
          <div class="note">{e(r.get('Channel'))}</div>
          <div class="note" style="margin-top:6px">{e((r.get('Summary') or '')[:180])}…</div>
          <div class="row" style="margin-top:9px">
            <a class="btn small ghost" href="{e(r.get('PDF'))}" target="_blank">PDF</a>
            <a class="btn small ghost" href="{e(r.get('Video Link'))}" target="_blank">Video</a>
            <a class="btn small wa" href="{e(wa_link(r))}" target="_blank"
               rel="noopener">WhatsApp</a>
            <form method="post" action="/resend" style="margin:0">
              <input type="hidden" name="row" value="{r['_row']}">
              <input type="hidden" name="title" value="{e(r.get('Title'))}">
              <button class="btn small ghost">Email again</button></form>
          </div></td></tr>""" for r in rows[:200])
    books = [gapi.MAIN] + [(sh.get("Name") or "") for sh in data["sheets"]
                           if sh.get("Name")]
    bpick = " &nbsp;·&nbsp; ".join(
        f"<strong>{e(bk)}</strong>" if bk == book
        else f'<a href="/library?book={e(bk)}">{e(bk)}</a>' for bk in books)
    picker = " &nbsp;·&nbsp; ".join(
        f"<strong>{e(t)}</strong>" if t == which
        else f'<a href="/library?book={e(book)}&tab={e(t)}">{e(t)}</a>'
        for t in tabs)
    body = f"""<h2>Library</h2>
    <p class="sub">{bpick}</p><p class="sub" style="margin-top:-14px">{picker}
      &nbsp;·&nbsp; {len(rows)} rows</p>
    <form class="card" method="get"><div class="row">
      <input type="hidden" name="book" value="{e(book)}">
      <input type="hidden" name="tab" value="{e(which)}">
      <input name="q" value="{e(q)}" placeholder="search title or summary"
        style="flex:1;min-width:200px"><button class="btn">Search</button></div></form>
    {'<div class="card"><table>' + trs + '</table></div>' if rows else
     '<p class="note">Nothing found.</p>'}"""
    return page("Library", body, "/library")


@app.route("/resend", methods=["POST"])
def resend():
    try:
        data = gapi.read_all()
        rows = data["episodes"] + data["links"]
        want = request.form.get("row")
        row = next(r for r in rows if str(r["_row"]) == want
                   and r.get("Title") == request.form.get("title", r.get("Title")))
        to = [r["Email"] for r in data["recipients"]
              if r.get("Email") and r.get("Active", "yes") != "no"]
        if not to:
            return back("/library", "No recipients found.", True)
        fid = gapi.file_id_from_link(row.get("PDF", ""))
        pdf = gapi.download_file(fid) if fid else None
        body = (f"{row.get('Title')}\n{row.get('Channel')} · {row.get('Date')}\n"
                f"{row.get('Video Link')}\n\nSummary\n\n{row.get('Summary')}\n")
        gapi.send_mail(to, row.get("Title", "Brief"), body, attachment=pdf,
                       attachment_name="brief.pdf")
        return back("/library", "Sent again.")
    except Exception as ex:
        return back("/library", f"Could not send: {ex}", True)


# ---------------------------------------------------------------- settings

@app.route("/settings", methods=["GET", "POST"])
def settings_page():
    if not gapi.has_token():
        return page("Settings", SETUP_BODY, "/settings")
    if request.method == "POST":
        gapi.set_settings({
            "summary_length": request.form.get("summary_length", "medium"),
            "lookback_days": request.form.get("lookback_days", "5"),
            "pdf_public": "yes" if request.form.get("pdf_public") else "no",
            "attach_pdf": "yes" if request.form.get("attach_pdf") else "no",
            "whatsapp_length": request.form.get("whatsapp_length", "1200").strip(),
            "paused": "yes" if request.form.get("paused") else "no",
            "check_every_hours": request.form.get("check_every_hours", "3"),
            "keep_awake": "yes" if request.form.get("keep_awake") else "no",
            "email_subject": request.form.get("email_subject", "{title}"),
            "output_instruction": request.form.get("output_instruction", "").strip(),
            "output_title": request.form.get("output_title", "").strip(),
            "output_style": request.form.get("output_style", "timeline"),
            "openrouter_key": request.form.get("openrouter_key", "").strip(),
            "supadata_key": request.form.get("supadata_key", "").strip(),
            "proxy_url": request.form.get("proxy_url", "").strip(),
            "sms_provider": request.form.get("sms_provider", "").strip(),
            "fast2sms_key": request.form.get("fast2sms_key", "").strip(),
            "twilio_sid": request.form.get("twilio_sid", "").strip(),
            "twilio_token": request.form.get("twilio_token", "").strip(),
            "twilio_from": request.form.get("twilio_from", "").strip(),
        })
        SCHED["keep_awake"] = bool(request.form.get("keep_awake"))
        return back("/settings", "Saved.")

    s = pipeline.settings()
    sel = lambda v: "selected" if s.get("summary_length") == v else ""
    body = f"""<h2>Settings</h2><p class="sub">Connected Google account:
      {e(gapi.account_email())}</p>
    <form method="post">
    <div class="card">
      <div class="grid">
        <div><label>Summary length</label>
          <select name="summary_length">
            <option value="short" {sel('short')}>Short (~150 words)</option>
            <option value="medium" {sel('medium')}>Medium (~350 words)</option>
            <option value="detailed" {sel('detailed')}>Detailed (~700 words)</option>
          </select></div>
        <div><label>Look back how many days</label>
          <input name="lookback_days" value="{e(s.get('lookback_days'))}"></div>
        <div><label>Check for new videos every (hours)</label>
          <input name="check_every_hours" value="{e(s.get('check_every_hours', '3'))}"></div>
      </div>
      <label>How should each video be written up?</label>
      <select name="output_style">
        <option value="timeline" {'selected' if s.get('output_style', 'timeline') == 'timeline' else ''}>Detailed timestamp summary — sections with time ranges</option>
        <option value="summary" {'selected' if s.get('output_style') == 'summary' else ''}>One plain summary</option>
      </select>
      <label style="margin-top:12px">Or write your own instruction (this overrides
        the choice above)</label>
      <textarea name="output_instruction" rows="4"
        placeholder="Leave blank to use the choice above. Or write your own, e.g.
&quot;Pull out every legal point and list it with the reasoning&quot;, or
&quot;Write detailed study notes with headings&quot;.">{e(s.get('output_instruction'))}</textarea>
      <div class="grid" style="margin-top:12px">
        <div><label>Heading for that section (in the PDF and email)</label>
          <input name="output_title" value="{e(s.get('output_title'))}"
            placeholder="leave blank to pick automatically"></div>
        <div></div>
      </div>
      <label>Email subject</label>
      <input name="email_subject" value="{e(s.get('email_subject'))}">
      <div class="note" style="margin-top:5px">
        {{title}}, {{channel}}, {{date}}, {{episode}} — these are replaced with the real values.</div>
      <div class="grid" style="margin-top:12px">
        <div><label>WhatsApp message length (characters)</label>
          <input name="whatsapp_length" value="{e(s.get('whatsapp_length', '1200'))}"></div>
        <div></div>
      </div>
      <label style="margin-top:10px">
        <input type="checkbox" name="attach_pdf" style="width:auto"
          {'checked' if s.get('attach_pdf', 'yes') == 'yes' else ''}>
        Attach the PDF to the email (uncheck to send only the write-up, with a
        link to the PDF)</label>
      <label style="margin-top:6px">
        <input type="checkbox" name="pdf_public" style="width:auto"
          {'checked' if s.get('pdf_public') == 'yes' else ''}>
        Anyone with the PDF link can open it</label>
      <label><input type="checkbox" name="keep_awake" style="width:auto"
          {'checked' if s.get('keep_awake', 'yes') == 'yes' else ''}>
        Keep the app awake (Render will not sleep it; uses ~744 hours a month)</label>
      <label><input type="checkbox" name="paused" style="width:auto"
          {'checked' if s.get('paused') == 'yes' else ''}>
        Pause for now</label>
    </div>
    <div class="card">
      <label>OpenRouter key</label>
      <input name="openrouter_key" value="{e(s.get('openrouter_key'))}"
        placeholder="sk-or-...">
      <label>Proxy for YouTube (optional — a residential proxy lets the app
        fetch transcripts itself, with no monthly limit)</label>
      <input name="proxy_url" value="{e(s.get('proxy_url'))}"
        placeholder="http://user:password@host:port">
      <label>Supadata key</label>
      <input name="supadata_key" value="{e(s.get('supadata_key'))}">
      <div class="note" style="margin-top:6px">Both keys can also live in Render's environment
        — leave these blank in that case.</div>
    </div>
    <div class="card">
      <div class="note" style="margin-bottom:10px">SMS for phone verification —
        leave off to verify by email only.</div>
      <label>SMS service</label>
      <select name="sms_provider">
        <option value="" {'selected' if not s.get('sms_provider') else ''}>Off — email codes only</option>
        <option value="fast2sms" {'selected' if s.get('sms_provider') == 'fast2sms' else ''}>Fast2SMS (India)</option>
        <option value="twilio" {'selected' if s.get('sms_provider') == 'twilio' else ''}>Twilio</option>
      </select>
      <label>Fast2SMS key</label>
      <input name="fast2sms_key" value="{e(s.get('fast2sms_key'))}">
      <div class="grid" style="margin-top:12px">
        <div><label>Twilio account SID</label>
          <input name="twilio_sid" value="{e(s.get('twilio_sid'))}"></div>
        <div><label>Twilio auth token</label>
          <input name="twilio_token" value="{e(s.get('twilio_token'))}"></div>
      </div>
      <label>Twilio sender number</label>
      <input name="twilio_from" value="{e(s.get('twilio_from'))}" placeholder="+1...">
    </div>
    <button class="btn">Save</button>
    </form>
    <div class="card" style="margin-top:18px">
      <div class="note">Connected</div>
      <div class="row" style="margin-top:8px">
        <a class="btn small ghost" href="{e(safe_sheet_url())}" target="_blank">Sheet</a>
        <a class="btn small ghost" href="{e(safe_drive_url())}" target="_blank">Drive</a>
        <a class="btn small ghost" href="/oauth/start">Reconnect Google</a>
        <a class="btn small ghost" href="/me">My account</a>
      </div>
    </div>"""
    return page("Settings", body, "/settings")


@app.route("/logs")
def logs():
    rows = list(reversed(gapi.read_rows("Log")))[:200]
    tag = {"ok": "tag", "wait": "tag warn", "error": "tag bad"}
    trs = "".join(f"""<tr><td class="note" style="white-space:nowrap">{e(r.get('Time'))}</td>
        <td><span class="{tag.get(r.get('Level'), 'tag')}">{e(r.get('Level'))}</span></td>
        <td>{e(r.get('Message'))}</td></tr>""" for r in rows)
    body = ("<h2>Logs</h2><p class='sub'>What happened, and where it stopped.</p>"
            + (f"<div class='card'><table>{trs}</table></div>" if rows
               else "<p class='note'>Nothing yet.</p>"))
    return page("Logs", body, "/logs")


# ---------------------------------------------------------------- google oauth

SETUP_BODY = """<h2>Connect Google</h2>
<p class="sub">One sign-in covers three things — writing the Sheet, storing PDFs in Drive,
and sending email from your Gmail.</p>
<div class="card">
<p>Set <strong>GOOGLE_CLIENT_ID</strong> and <strong>GOOGLE_CLIENT_SECRET</strong>
in Render, then press the button below.</p>
<a class="btn" href="/oauth/start">Connect Google</a>
</div>"""


def base_url():
    url = request.url_root.rstrip("/")
    if url.startswith("http://") and "localhost" not in url and "127.0.0.1" not in url:
        url = "https://" + url[len("http://"):]
    return url


def make_flow():
    from google_auth_oauthlib.flow import Flow
    cfg = {
        "web": {
            "client_id": os.environ.get("GOOGLE_CLIENT_ID", ""),
            "client_secret": os.environ.get("GOOGLE_CLIENT_SECRET", ""),
            "auth_uri": "https://accounts.google.com/o/oauth2/auth",
            "token_uri": "https://oauth2.googleapis.com/token",
            "redirect_uris": [base_url() + "/oauth/callback"],
        }
    }
    flow = Flow.from_client_config(cfg, scopes=gapi.SCOPES)
    flow.redirect_uri = base_url() + "/oauth/callback"
    return flow


@app.route("/oauth/start")
def oauth_start():
    if not os.environ.get("GOOGLE_CLIENT_ID"):
        return page("Google", "<h2>Google</h2><div class='msg bad'>Add "
                    "GOOGLE_CLIENT_ID and GOOGLE_CLIENT_SECRET in Render first.</div>", "/settings")
    import secrets
    verifier = secrets.token_urlsafe(64)[:96]
    session["code_verifier"] = verifier
    flow = make_flow()
    flow.code_verifier = verifier
    auth_url, state = flow.authorization_url(
        access_type="offline", prompt="consent", include_granted_scopes="true")
    session["oauth_state"] = state
    return redirect(auth_url)


@app.route("/oauth/callback")
def oauth_callback():
    try:
        flow = make_flow()
        flow.code_verifier = session.get("code_verifier")
        flow.fetch_token(authorization_response=request.url.replace("http://", "https://")
                         if "localhost" not in request.url else request.url)
        creds = flow.credentials
        if not creds.refresh_token:
            return page("Google", "<h2>Google</h2><div class='msg bad'>Refresh token "
                        "was not returned. Remove this app from your Google account permissions and "
                        "try again.</div>", "/settings")
        os.environ["GOOGLE_REFRESH_TOKEN"] = creds.refresh_token
        gapi._cache["creds"] = None
        body = f"""<h2>Connected</h2>
        <p class="sub">Working already. One last step.</p>
        <div class="card"><p>Paste the line below into Render's <strong>Environment</strong>
        as <strong>GOOGLE_REFRESH_TOKEN</strong> — otherwise the app will forget
        this connection the next time it restarts.</p>
        <textarea rows="3" onclick="this.select()">{e(creds.refresh_token)}</textarea>
        </div><a class="btn" href="/">Dashboard</a>"""
        return page("Google", body, "/settings")
    except Exception as ex:
        return page("Google", f"<h2>Google</h2><div class='msg bad'>{e(ex)}</div>",
                    "/settings")


# ---------------------------------------------------------------- app install

APP_NAME = "YouTube Brief Desk"


@app.route("/manifest.webmanifest")
def manifest():
    return jsonify({
        "name": APP_NAME,
        "short_name": "Brief Desk",
        "description": "Transcripts, summaries and PDFs from YouTube, by email.",
        "start_url": "/?src=app",
        "scope": "/",
        "display": "standalone",
        "orientation": "portrait",
        "background_color": "#FBFAF7",
        "theme_color": "#1F6F5C",
        "icons": [
            {"src": "/icon-192.png", "sizes": "192x192", "type": "image/png",
             "purpose": "any"},
            {"src": "/icon-512.png", "sizes": "512x512", "type": "image/png",
             "purpose": "any"},
            {"src": "/icon-mask.png", "sizes": "512x512", "type": "image/png",
             "purpose": "maskable"},
        ],
        "shortcuts": [
            {"name": "Add video", "url": "/add"},
            {"name": "Library", "url": "/library"},
        ],
    })


def _png(b64s):
    import base64
    return Response(base64.b64decode(b64s), mimetype="image/png",
                    headers={"Cache-Control": "public, max-age=604800"})


@app.route("/icon-192.png")
def icon192():
    return _png(I192_B64)


@app.route("/icon-512.png")
def icon512():
    return _png(I512_B64)


@app.route("/icon-mask.png")
def iconmask():
    return _png(MASK_B64)


@app.route("/sw.js")
def service_worker():
    js = """
self.addEventListener('install', e => self.skipWaiting());
self.addEventListener('activate', e => e.waitUntil(self.clients.claim()));
self.addEventListener('fetch', e => {
  // sab kuch seedha network se — app ka data hamesha taaza rahe
  e.respondWith(fetch(e.request).catch(() =>
    new Response('<h2 style="font:16px sans-serif;padding:30px">' +
      'No internet just now. Please try again.</h2>',
      {headers: {'Content-Type': 'text/html'}})));
});
"""
    return Response(js, mimetype="application/javascript",
                    headers={"Cache-Control": "no-cache"})


# ------------------------------------------- aapke PC wale helper ke liye

def helper_ok():
    given = (request.args.get("key") or request.form.get("key")
             or (request.get_json(silent=True) or {}).get("key") or "")
    return bool(HELPER_KEY) and given == HELPER_KEY


@app.route("/api/jobs")
def api_jobs():
    if not helper_ok():
        return jsonify({"error": "bad key"}), 403
    try:
        return jsonify({"videos": pipeline.videos_needing_transcript()})
    except Exception as ex:
        return jsonify({"error": str(ex)}), 500


@app.route("/api/transcript", methods=["POST"])
def api_transcript():
    if not helper_ok():
        return jsonify({"error": "bad key"}), 403
    body = request.get_json(silent=True) or request.form
    vid = (body.get("video_id") or "").strip()
    text = body.get("text") or ""
    if not vid or len(text) < 200:
        return jsonify({"error": "need video_id and text"}), 400
    try:
        parts = gapi.inbox_put(vid, text)
        pipeline.log("ok", f"transcript received from your PC — {vid} "
                           f"({len(text) // 1000}k chars)")
        pipeline.run_in_background()
        return jsonify({"saved": True, "parts": parts})
    except Exception as ex:
        return jsonify({"error": str(ex)}), 500


@app.route("/refresh", methods=["POST"])
def refresh_now():
    u = current_user()
    if role_of(u) not in ("owner", "admin"):
        return back("/", "Only the owner can do that.", True)
    if pipeline.STATUS.get("running"):
        return back("/", "A run is in progress — try again when it finishes.", True)
    pipeline.free_memory()
    import threading as _t
    _t.Timer(0.5, lambda: os._exit(3)).start()
    return back("/", "Refreshing — the app will be back in a few seconds.")


@app.route("/healthz")
def healthz():
    return "ok"


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 5000)), debug=False)
