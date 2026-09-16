"""YouTube Brief Desk — browser wala hissa."""

import os
import html
import datetime as dt

from flask import (Flask, request, redirect, url_for, session,
                   render_template_string, jsonify, Response)

os.environ.setdefault("OAUTHLIB_RELAX_TOKEN_SCOPE", "1")
os.environ.setdefault("OAUTHLIB_INSECURE_TRANSPORT", "1")

import gapi
import pipeline

from werkzeug.middleware.proxy_fix import ProxyFix

app = Flask(__name__)
app.wsgi_app = ProxyFix(app.wsgi_app, x_proto=1, x_host=1)
app.secret_key = os.environ.get("SECRET_KEY", "badal-dijiye-ise")

UI_PASSWORD = os.environ.get("UI_PASSWORD", "")
CRON_KEY = os.environ.get("CRON_KEY", "")
BUILD = "6"


# ---------------------------------------------------------------- background
# Do kaam app ke andar hi chalte hain:
#   1. har 10 minute apne aap ko ping — Render free plan sulaata nahi
#   2. har kuch ghante (Settings: check_every_hours) naye video ki jaanch
# Isliye na cron-job.org chahiye, na khulne me 50 second.

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


def _scheduler_loop():
    time.sleep(600)                      # pehle UI khulne dijiye, phir kaam
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
        hours = max(0.5, min(hours, 24))
        nxt = dt.datetime.now() + dt.timedelta(hours=hours)
        SCHED["next_check"] = nxt.strftime("%d %b, %I:%M %p")
        time.sleep(hours * 3600)


def start_background():
    if os.environ.get("DISABLE_BACKGROUND") == "1":
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
nav h1 small{display:block;font:400 11px/1.4 inherit;color:var(--soft);
  font-family:-apple-system,sans-serif;margin-top:3px}
nav a{display:block;padding:9px 20px;color:var(--ink);text-decoration:none}
nav a:hover{background:#F4F2EC}
nav a.on{background:#F0F4F2;color:var(--green);box-shadow:inset 3px 0 0 var(--green);
  font-weight:600}
main{flex:1;padding:30px 34px 70px;max-width:920px}
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

NAV = [("/", "Dashboard"), ("/library", "Library"), ("/channels", "Channels"),
       ("/recipients", "Recipients"), ("/settings", "Settings"), ("/logs", "Logs")]

SHELL = """<!doctype html><html lang="hi"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<meta name="theme-color" content="#1F6F5C">
<title>{{title}} · YouTube Brief Desk</title><style>{{css|safe}}</style></head>
<body><div class="wrap">
<nav><h1>YouTube Brief Desk<small>build {{build}}</small></h1>
{% for href,label in nav %}<a href="{{href}}" class="{{'on' if href==active}}">{{label}}</a>{% endfor %}
</nav><main>
{% if msg %}<div class="msg {{'bad' if bad else ''}}">{{msg}}</div>{% endif %}
{{body|safe}}
</main></div></body></html>"""


def page(title, body, active="/"):
    return render_template_string(
        SHELL, title=title, body=body, css=CSS, nav=NAV, active=active,
        build=BUILD, msg=request.args.get("msg"),
        bad=request.args.get("bad") == "1")


def back(where, msg="", bad=False):
    q = f"?msg={msg}" + ("&bad=1" if bad else "") if msg else ""
    return redirect(where + q)


def e(t):
    return html.escape(str(t or ""))


@app.before_request
def guard():
    open_paths = ("/login", "/cron", "/static", "/oauth")
    if request.path.startswith(open_paths):
        return
    if UI_PASSWORD and not session.get("ok"):
        return redirect(url_for("login"))
    if not gapi.has_token() and request.path not in ("/", "/settings", "/logout",
                                                     "/healthz", "/api/status"):
        return redirect("/")


@app.route("/login", methods=["GET", "POST"])
def login():
    if request.method == "POST":
        if request.form.get("password") == UI_PASSWORD:
            session["ok"] = True
            session.permanent = True
            return redirect("/")
        return page("Login", "<h2>Password</h2><p class='sub'>Galat password.</p>"
                    + LOGIN_FORM)
    return page("Login", "<h2>YouTube Brief Desk</h2>"
                "<p class='sub'>Andar aane ke liye password daaliye.</p>" + LOGIN_FORM)


LOGIN_FORM = """<form method="post" class="card" style="max-width:340px">
<label>Password</label><input type="password" name="password" autofocus>
<div style="margin-top:14px"><button class="btn">Kholiye</button></div></form>"""


@app.route("/logout")
def logout():
    session.clear()
    return redirect("/login")


# ---------------------------------------------------------------- dashboard

@app.route("/")
def dashboard():
    if not gapi.has_token():
        return page("Setup", SETUP_BODY, "/")
    try:
        gapi.ensure_tabs()
        data = gapi.read_all()
        eps = data["episodes"]
        chans = data["channels"]
        recips = [r for r in data["recipients"] if r.get("Active", "yes") != "no"]
    except Exception as ex:
        return page("Dashboard", f"<h2>Dashboard</h2><div class='msg bad'>"
                    f"Google se baat nahi ho paayi: {e(ex)}</div>", "/")

    s = data["settings"]
    st = pipeline.STATUS
    latest = eps[-1] if eps else None

    if latest:
        lead = f"""<div class="card lead">
          <div class="note">Sabse naya · {e(latest.get('Date'))}</div>
          <h3 style="margin:6px 0 10px;font:600 19px/1.3 Georgia,serif">{e(latest.get('Title'))}</h3>
          <div class="summary" style="color:#3A4152">{e((latest.get('Summary') or '')[:420])}…</div>
          <div class="row" style="margin-top:14px">
            <a class="btn small" href="{e(latest.get('PDF'))}" target="_blank">PDF kholiye</a>
            <a class="btn small ghost" href="{e(latest.get('Video Link'))}" target="_blank">Video</a>
            <a class="btn small ghost" href="/library">Sab dekhiye</a>
          </div></div>"""
    else:
        lead = ("<div class='card lead'><h3 style='margin:0 0 6px'>Abhi kuch nahi aaya</h3>"
                "<p class='note' style='margin:0'>Ek channel jodiye aur ek email pata "
                "daaliye — agla video aate hi kaam shuru ho jayega.</p>"
                "<div class='row' style='margin-top:14px'>"
                "<a class='btn small' href='/channels'>Channel jodiye</a></div></div>")

    running = st["running"]
    state = (f"<span class='dot'></span>{e(st['step']) or 'chal raha hai'}"
             if running else
             f"<span class='dot idle'></span>{e(st['last_result'])}")

    body = f"""<h2>Dashboard</h2><p class="sub">Aaj tak {len(eps)} episode bheje gaye.</p>
    {lead}
    <div class="card"><div class="row" style="justify-content:space-between">
      <div id="state">{state}</div>
      <form method="post" action="/run" style="margin:0">
        <button class="btn small" {'disabled' if running else ''}>Abhi chalao</button>
      </form></div>
      <div class="note" style="margin-top:10px">Pichhli baar: {e(st['last_run'] or '—')}
        &nbsp;·&nbsp; Agli jaanch: {e(SCHED['next_check'] or 'thodi der me')}
        &nbsp;·&nbsp; {'Jaagti rahegi' if SCHED['keep_awake'] else 'Beech me so sakti hai'}</div>
    </div>
    <div class="grid">
      <div class="card"><div class="note">Channels</div>
        <div style="font:600 22px/1.4 Georgia,serif">{len(chans)}</div>
        <a class="note" href="/channels">badliye</a></div>
      <div class="card"><div class="note">Email jaata hai</div>
        <div style="font:600 22px/1.4 Georgia,serif">{len(recips)} log</div>
        <a class="note" href="/recipients">badliye</a></div>
    </div>
    <div class="card"><div class="note" style="margin-bottom:8px">Aapka record</div>
      <div class="row">
        <a class="btn small ghost" href="{e(safe_sheet_url())}" target="_blank">Google Sheet</a>
        <a class="btn small ghost" href="{e(safe_drive_url())}" target="_blank">Drive folder (PDF)</a>
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


@app.route("/run", methods=["POST"])
def run_now():
    started = pipeline.run_in_background(manual=True)
    return back("/", "Kaam shuru kar diya." if started else "Pehle se chal raha hai.")


@app.route("/cron")
def cron():
    if CRON_KEY and request.args.get("key") != CRON_KEY:
        return Response("nahi", status=403)
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
                    return back("/channels", "Yeh channel pehle se juda hai.", True)
                gapi.append_row("Channels", [cid, name,
                                             dt.date.today().strftime("%d %b %Y"), "yes"])
                return back("/channels", f"{name} jud gaya.")
            if action == "delete":
                keep = [[r.get("Channel ID"), r.get("Name"), r.get("Added On"), r.get("Active")]
                        for r in rows if r.get("Channel ID") != request.form.get("cid")]
                gapi.replace_tab("Channels", gapi.TABS["Channels"], keep)
                return back("/channels", "Hata diya.")
        except Exception as ex:
            return back("/channels", f"Nahi ho paya: {ex}", True)

    rows = gapi.read_all()["channels"]
    trs = "".join(f"""<tr><td><strong>{e(r.get('Name'))}</strong>
        <div class="note">{e(r.get('Channel ID'))}</div></td>
        <td class="note">{e(r.get('Added On'))}</td>
        <td style="text-align:right"><form method="post" style="margin:0">
        <input type="hidden" name="action" value="delete">
        <input type="hidden" name="cid" value="{e(r.get('Channel ID'))}">
        <button class="btn small ghost">Hatao</button></form></td></tr>""" for r in rows)
    body = f"""<h2>Channels</h2>
    <p class="sub">Jin channels par nazar rakhni hai.</p>
    <div class="card"><form method="post">
      <input type="hidden" name="action" value="add">
      <label>Channel ka link, @handle ya UC… se shuru hone wali ID</label>
      <input name="channel" placeholder="https://www.youtube.com/@channelname" required>
      <div style="margin-top:13px"><button class="btn">Jodiye</button></div>
    </form></div>
    {'<div class="card"><table><tr><th>Channel</th><th>Kab se</th><th></th></tr>'
     + trs + '</table></div>' if rows else
     '<p class="note">Abhi koi channel nahi juda.</p>'}"""
    return page("Channels", body, "/channels")


# ---------------------------------------------------------------- recipients

@app.route("/recipients", methods=["GET", "POST"])
def recipients():
    if request.method == "POST":
        rows = gapi.read_all(force=True)["recipients"]
        if request.form.get("action") == "add":
            email = (request.form.get("email") or "").strip()
            if "@" not in email:
                return back("/recipients", "Yeh email pata theek nahi lag raha.", True)
            gapi.append_row("Recipients", [email, request.form.get("name", ""), "yes"])
            return back("/recipients", "Jod diya.")
        keep = [[r.get("Email"), r.get("Name"), r.get("Active")]
                for r in rows if r.get("Email") != request.form.get("email")]
        gapi.replace_tab("Recipients", gapi.TABS["Recipients"], keep)
        return back("/recipients", "Hata diya.")

    rows = gapi.read_all()["recipients"]
    trs = "".join(f"""<tr><td>{e(r.get('Email'))}<div class="note">{e(r.get('Name'))}</div></td>
        <td style="text-align:right"><form method="post" style="margin:0">
        <input type="hidden" name="action" value="delete">
        <input type="hidden" name="email" value="{e(r.get('Email'))}">
        <button class="btn small ghost">Hatao</button></form></td></tr>""" for r in rows)
    body = f"""<h2>Recipients</h2>
    <p class="sub">Har naye video ki PDF in sab ko jaayegi.</p>
    <div class="card"><form method="post">
      <input type="hidden" name="action" value="add">
      <div class="grid"><div><label>Email</label>
        <input name="email" type="email" placeholder="naam@example.com" required></div>
        <div><label>Naam (marzi ho to)</label><input name="name"></div></div>
      <div style="margin-top:13px"><button class="btn">Jodiye</button></div>
    </form></div>
    {'<div class="card"><table>' + trs + '</table></div>' if rows else
     '<p class="note">Abhi kisi ka pata nahi daala gaya.</p>'}"""
    return page("Recipients", body, "/recipients")


# ---------------------------------------------------------------- library

@app.route("/library")
def library():
    q = (request.args.get("q") or "").lower().strip()
    rows = list(reversed(gapi.read_all()["episodes"]))
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
            <form method="post" action="/resend" style="margin:0">
              <input type="hidden" name="row" value="{r['_row']}">
              <button class="btn small ghost">Dobara email</button></form>
          </div></td></tr>""" for r in rows[:200])
    body = f"""<h2>Library</h2><p class="sub">{len(rows)} episode.</p>
    <form class="card" method="get"><div class="row">
      <input name="q" value="{e(q)}" placeholder="title ya summary me dhoondhiye"
        style="flex:1;min-width:200px"><button class="btn">Dhoondhiye</button></div></form>
    {'<div class="card"><table>' + trs + '</table></div>' if rows else
     '<p class="note">Kuch nahi mila.</p>'}"""
    return page("Library", body, "/library")


@app.route("/resend", methods=["POST"])
def resend():
    try:
        data = gapi.read_all()
        rows = data["episodes"]
        row = next(r for r in rows if str(r["_row"]) == request.form.get("row"))
        to = [r["Email"] for r in data["recipients"]
              if r.get("Email") and r.get("Active", "yes") != "no"]
        if not to:
            return back("/library", "Kisi ka email pata nahi mila.", True)
        fid = gapi.file_id_from_link(row.get("PDF", ""))
        pdf = gapi.download_file(fid) if fid else None
        body = (f"{row.get('Title')}\n{row.get('Channel')} · {row.get('Date')}\n"
                f"{row.get('Video Link')}\n\nSummary\n\n{row.get('Summary')}\n")
        gapi.send_mail(to, row.get("Title", "Brief"), body, attachment=pdf,
                       attachment_name="brief.pdf")
        return back("/library", "Dobara bhej diya.")
    except Exception as ex:
        return back("/library", f"Nahi bheja ja saka: {ex}", True)


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
            "paused": "yes" if request.form.get("paused") else "no",
            "check_every_hours": request.form.get("check_every_hours", "3"),
            "keep_awake": "yes" if request.form.get("keep_awake") else "no",
            "email_subject": request.form.get("email_subject", "{title}"),
            "openrouter_key": request.form.get("openrouter_key", "").strip(),
            "supadata_key": request.form.get("supadata_key", "").strip(),
        })
        SCHED["keep_awake"] = bool(request.form.get("keep_awake"))
        return back("/settings", "Sambhaal liya.")

    s = pipeline.settings()
    sel = lambda v: "selected" if s.get("summary_length") == v else ""
    body = f"""<h2>Settings</h2><p class="sub">Google se juda hua account:
      {e(gapi.account_email())}</p>
    <form method="post">
    <div class="card">
      <div class="grid">
        <div><label>Summary kitni lambi</label>
          <select name="summary_length">
            <option value="short" {sel('short')}>Chhoti (~150 shabd)</option>
            <option value="medium" {sel('medium')}>Theek-thaak (~350 shabd)</option>
            <option value="detailed" {sel('detailed')}>Vistaar se (~700 shabd)</option>
          </select></div>
        <div><label>Kitne din peechhe tak dekhe</label>
          <input name="lookback_days" value="{e(s.get('lookback_days'))}"></div>
        <div><label>Kitne ghante me naye video dekhe</label>
          <input name="check_every_hours" value="{e(s.get('check_every_hours', '3'))}"></div>
      </div>
      <label>Email ka subject</label>
      <input name="email_subject" value="{e(s.get('email_subject'))}">
      <div class="note" style="margin-top:5px">
        {{title}}, {{channel}}, {{date}}, {{episode}} — inki jagah asli baat aa jaayegi.</div>
      <label style="margin-top:16px">
        <input type="checkbox" name="pdf_public" style="width:auto"
          {'checked' if s.get('pdf_public') == 'yes' else ''}>
        PDF ka link jise mile, wo khol sake</label>
      <label><input type="checkbox" name="keep_awake" style="width:auto"
          {'checked' if s.get('keep_awake', 'yes') == 'yes' else ''}>
        App ko jaagta rakhiye (Render sulaayega nahi; mahine ke ~744 ghante lagte hain)</label>
      <label><input type="checkbox" name="paused" style="width:auto"
          {'checked' if s.get('paused') == 'yes' else ''}>
        Kuch din ke liye rok dijiye</label>
    </div>
    <div class="card">
      <label>OpenRouter key</label>
      <input name="openrouter_key" value="{e(s.get('openrouter_key'))}"
        placeholder="sk-or-...">
      <label>Supadata key</label>
      <input name="supadata_key" value="{e(s.get('supadata_key'))}">
      <div class="note" style="margin-top:6px">Ye dono keys Render ke environment
        me bhi rakh sakte hain — tab yahan khali chhod dijiye.</div>
    </div>
    <button class="btn">Sambhaaliye</button>
    </form>
    <div class="card" style="margin-top:18px">
      <div class="note">Jude hue hisse</div>
      <div class="row" style="margin-top:8px">
        <a class="btn small ghost" href="{e(safe_sheet_url())}" target="_blank">Sheet</a>
        <a class="btn small ghost" href="{e(safe_drive_url())}" target="_blank">Drive</a>
        <a class="btn small ghost" href="/oauth/start">Google dobara jodiye</a>
        <a class="btn small ghost" href="/logout">Logout</a>
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
    body = ("<h2>Logs</h2><p class='sub'>Kya hua, kahan atka.</p>"
            + (f"<div class='card'><table>{trs}</table></div>" if rows
               else "<p class='note'>Abhi kuch nahi.</p>"))
    return page("Logs", body, "/logs")


# ---------------------------------------------------------------- google oauth

SETUP_BODY = """<h2>Google se jodiye</h2>
<p class="sub">Ek hi login se teen kaam honge — Sheet likhna, Drive me PDF rakhna,
aapke Gmail se email bhejna.</p>
<div class="card">
<p>Render me <strong>GOOGLE_CLIENT_ID</strong> aur <strong>GOOGLE_CLIENT_SECRET</strong>
daal dene ke baad neeche wala button dabaiye.</p>
<a class="btn" href="/oauth/start">Google se jodiye</a>
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
        return page("Google", "<h2>Google</h2><div class='msg bad'>Pehle Render me "
                    "GOOGLE_CLIENT_ID aur GOOGLE_CLIENT_SECRET daaliye.</div>", "/settings")
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
                        "nahi mila. Google account ki permissions se app hata kar "
                        "dobara koshish kijiye.</div>", "/settings")
        os.environ["GOOGLE_REFRESH_TOKEN"] = creds.refresh_token
        gapi._cache["creds"] = None
        body = f"""<h2>Jud gaya</h2>
        <p class="sub">Abhi kaam karne laga hai. Ek aakhri kadam baaki hai.</p>
        <div class="card"><p>Neeche wali line Render ke <strong>Environment</strong>
        me <strong>GOOGLE_REFRESH_TOKEN</strong> naam se chipka dijiye — warna app
        dobara shuru hote hi ye jodna bhool jaayega.</p>
        <textarea rows="3" onclick="this.select()">{e(creds.refresh_token)}</textarea>
        </div><a class="btn" href="/">Dashboard</a>"""
        return page("Google", body, "/settings")
    except Exception as ex:
        return page("Google", f"<h2>Google</h2><div class='msg bad'>{e(ex)}</div>",
                    "/settings")


@app.route("/healthz")
def healthz():
    return "ok"


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 5000)), debug=False)
