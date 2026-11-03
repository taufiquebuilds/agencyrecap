# clientreport

Open-source monthly client reports for agencies. Connect Google Analytics 4 and Search Console once, add your clients, and each client gets a branded PDF report by email every month. Self-hosted and free for any number of clients.

## What a client gets

An email on the day you choose, with three headline numbers (sessions, key events, organic clicks, each compared with last month) and a 3-page PDF:

1. Summary: the three numbers, each with a one-line finding.
2. Website traffic: sessions by channel and a 13-month trend.
3. Google search: clicks trend, top 10 search queries and top 10 pages.

## Run it

You need Docker.

1. Copy the settings file: `cp .env.example .env`
2. Make a secret key and paste it into `APP_SECRET_KEY`:
   `python3 -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"`
   Do not change this key later. After you connect Google it is used to read the saved connection, and if it changes you have to connect Google again.
3. Set `ADMIN_PASSWORD`, your SMTP details and `TZ`. For Gmail, `SMTP_PASSWORD` must be an app password (Google Account → Security → 2-Step Verification → App passwords), not your normal password.
4. Create the Google OAuth client (next section) and fill in `GOOGLE_CLIENT_ID` and `GOOGLE_CLIENT_SECRET`.
5. Start it: `docker compose up -d`
6. Open http://localhost:8000 and sign in with `ADMIN_PASSWORD`.

Data (database, logo, sent PDFs) is stored in `./data`. Back that folder up.

## Behind a reverse proxy

To put the app on a real domain with HTTPS, run a reverse proxy (Caddy, nginx, Traefik) in front of port 8000 and set `BASE_URL` to the public address, for example `https://reports.example.com`.

The login page locks an IP address out after 5 wrong passwords in 15 minutes. Behind a proxy, every visitor looks like the proxy's IP unless the app is told to trust the proxy's forwarded headers, so one person's typos could lock everyone out. Set this in `.env`:

```
FORWARDED_ALLOW_IPS=<your proxy's IP address>
```

Use `*` only when the container can be reached through the proxy alone (for example, you do not publish port 8000 to the internet). The app already reads these headers; this setting says whose headers to believe. Then run `docker compose up -d` again.

## Google setup (about 10 minutes, once)

1. Go to https://console.cloud.google.com and create a project.
2. Under **APIs & Services → Library**, enable:
   - Google Analytics Data API
   - Google Analytics Admin API
   - Google Search Console API
3. Under **Google Auth Platform → Branding / Audience**, set up the consent screen as **External** and add your own email as a test user.
4. Under **Data access**, add these scopes: `.../auth/analytics.readonly`, `.../auth/webmasters.readonly`, `openid`, `.../auth/userinfo.email`.
5. **Important:** under **Audience**, click **Publish app** so the status is **In production**. In Testing status Google cancels the sign-in after 7 days and monthly reports stop. You do not need Google's verification: you will see an "unverified app" warning when you sign in. Click **Advanced → Go to (app name)**.
6. Under **Clients**, create an OAuth client of type **Web application** with this authorised redirect URI: `<BASE_URL>/auth/google/callback` (for example `http://localhost:8000/auth/google/callback`).
7. Copy the client ID and secret into `.env` and restart: `docker compose up -d`.

When you click **Connect Google**, Google shows a list of permissions with a tick box for each. Leave every box ticked. If Analytics or Search Console access is unticked, the app refuses the connection and you need to connect again.

## First client

1. **Settings:** set your agency name, logo, accent colour and your email for failure alerts. Click **Connect Google** and sign in with the Google account that can see your clients' GA4 and Search Console.
2. **Add client:** pick the GA4 property and Search Console site from the lists, enter the client's email and a send day (3–28).
3. **Preview** the PDF. Use **Send now** to send last month's report to yourself first.
4. Switch **Monthly send** on.

## How sending works

- A job runs every day at 07:00 in `TZ` and sends every report that is due.
- A client never gets the same month twice. **Send now** on a month already sent asks before sending again.
- If GA4 or Search Console fails, the report is held, never sent half-empty. It retries daily; after 3 failures you get an email with the reason.
- If Google stops accepting the sign-in, the dashboard shows a red banner and reports wait until you click **Connect Google** again.
- Run one container only. The send job lives inside the app.

## Develop

```bash
uv sync
export DYLD_FALLBACK_LIBRARY_PATH=/opt/homebrew/lib   # macOS: lets WeasyPrint find pango
uv run pytest
uv run --env-file .env uvicorn clientreport.main:app --reload
```

## Licence

MIT. `clientreport/report/chart_style.py` comes from [nopurple](https://github.com/taufiquebuilds/nopurple) (MIT, licence in `chart_style.LICENSE`).
