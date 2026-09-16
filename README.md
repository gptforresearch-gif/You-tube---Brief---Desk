# YouTube Brief Desk

Kisi bhi YouTube channel ke naye video ka transcript leta hai, use English me
badalta hai, summary banata hai, PDF banakar email karta hai, aur Google Sheet +
Drive me record rakh deta hai. Sab kuch cloud par — computer band ho tab bhi.

## Files
- `app.py` — browser wala hissa (dashboard, settings, library)
- `pipeline.py` — roz ka kaam (feed, transcript, English, summary, PDF, email)
- `gapi.py` — Google Sheet, Drive aur Gmail se baat-cheet
- `requirements.txt` — zaroori libraries

## Render par settings
- Build command: `pip install -r requirements.txt`
- Start command: `gunicorn app:app --timeout 180 --workers 1`

## Environment variables
| Naam | Kaam |
|---|---|
| `GOOGLE_CLIENT_ID` | Google Cloud se |
| `GOOGLE_CLIENT_SECRET` | Google Cloud se |
| `GOOGLE_REFRESH_TOKEN` | app ke `/oauth/start` se milega |
| `UI_PASSWORD` | app kholne ka password |
| `SECRET_KEY` | koi bhi lambi line |
| `CRON_KEY` | (marzi ho to) bahar se /cron trigger karne ke liye |
| `OPENROUTER_API_KEY` | (marzi ho to; Settings page se bhi chalega) |
| `SUPADATA_API_KEY` | (marzi ho to; Settings page se bhi chalega) |

App khud har 10 minute apne aap ko ping karti hai (Render sulaata nahi) aur har
`check_every_hours` ghante (Settings, default 3) naye video dekhti hai. Bahar se
koi cron zaroori nahi. Chahein to `/cron?key=<CRON_KEY>` se bhi chala sakte hain.
