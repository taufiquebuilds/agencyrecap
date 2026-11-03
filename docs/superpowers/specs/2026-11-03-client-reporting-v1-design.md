# clientreport v1: design

Date: 2026-11-03
Status: approved design, waiting for spec review

## Goal

An open-source, self-hosted alternative to AgencyAnalytics. One agency runs one install. Each month it emails every client a branded PDF report built from their GA4 and Search Console data. Self-hosting is free for any number of clients.

The first user is our own agency. v1 is done when a real client of ours gets a correct report by itself on the scheduled day.

## Decisions

| Topic | Decision |
| --- | --- |
| Shape | Self-host tool, one agency per install, one admin login |
| Data sources | GA4 and Google Search Console only |
| Delivery | Email with 3 headline numbers in the body and a PDF attached |
| Stack | Python 3.12, FastAPI, Jinja2 + HTMX, SQLite (SQLModel), APScheduler, matplotlib, WeasyPrint |
| Charts | Vendored copy of `nopurple/skills/charts/scripts/chart_style.py` (MIT, keep the licence notice) |
| Packaging | One Docker image, run with `docker compose up` |
| Licence | MIT |

## Not in v1

Meta Ads, Google Ads, more than one user, web report links, a custom report builder, AI-written commentary, billing or the hosted plan.

## User flow

1. Admin opens `BASE_URL`, signs in with `ADMIN_PASSWORD`.
2. Settings: agency name, logo (PNG/SVG, max 1 MB), one accent colour, owner email (for failure alerts), reply-to email, footer line.
3. Clicks **Connect Google** and signs in with the agency Google account that already has access to the clients' GA4 properties and Search Console sites.
4. Adds a client: name, one or more recipient emails, GA4 property (dropdown), Search Console site (dropdown), send day (3–28, default 4).
5. Clicks **Preview** to open the PDF for last month.
6. Turns on **Monthly send**. Also available: **Send now** for last month.

## Report content

Period: the previous calendar month in the install's timezone (`TZ`). Every number is compared with the month before it.

**Page 1: summary.** Agency logo, client name, month. Three big numbers with change from last month:
- Sessions (GA4 `sessions`)
- Key events (GA4 `keyEvents`, formerly conversions)
- Organic clicks (Search Console `clicks`)

Each has a sentence title, for example "Organic clicks up 18%: 4,210". A change smaller than ±2% is written as "flat".

**Page 2: website traffic (GA4).**
- Sessions by channel (`sessionDefaultChannelGroup`), sorted horizontal bars, the biggest channel highlighted.
- Sessions per month for the last 13 months, line, latest value labelled.

**Page 3: Google search (Search Console).**
- Clicks per month for the last 13 months, line.
- Top 10 search queries by clicks: query, clicks, impressions, CTR, average position.
- Top 10 pages by clicks: same columns.

Charts follow the nopurple rules: the finding as the title, one accent colour (the agency colour), values labelled directly, bars from zero, text 14 px or larger.

**Email.** Subject: "<Client> report for <Month YYYY>". Body: the three headline sentences, one line telling the client the full report is attached, and the agency footer. Plain text plus a simple HTML version. Reply-to is the agency reply-to email.

## Architecture

```
clientreport/
  app.py              FastAPI app, routes, startup (DB + scheduler)
  config.py           Settings from environment variables
  db.py               SQLModel models and session
  auth.py             Admin password login (signed session cookie)
  google_oauth.py     OAuth flow, token storage (encrypted), credential refresh
  connectors/
    base.py           Connector interface and the MonthData dataclass
    ga4.py            GA4 Data API + Admin API (property list)
    gsc.py            Search Console API (site list, search analytics)
  report/
    metrics.py        Month-over-month maths and headline sentences (pure functions)
    charts.py         Builds chart PNGs with chart_style.py
    chart_style.py    Vendored from nopurple (MIT)
    render.py         Jinja HTML template -> PDF with WeasyPrint
    templates/report.html
  mail.py             SMTP send with attachment
  scheduler.py        Daily job: find due reports, generate, send, record
  templates/          Dashboard pages (Jinja + HTMX)
tests/
Dockerfile
docker-compose.yml
```

Each unit is usable alone: connectors return plain dataclasses, `report.metrics` takes dataclasses and returns numbers and sentences, `report.render` takes those and returns PDF bytes, `mail` takes bytes and addresses. Only `scheduler.py` and the web routes wire them together.

### Connector interface

```python
class Connector(Protocol):
    def list_sources(self, creds) -> list[Source]          # id + display name for dropdowns
    def fetch_month(self, creds, source_id, month) -> MonthData
```

`MonthData` holds totals for the month and the month before, the breakdown tables and the 13-month series. Meta Ads and Google Ads become new connectors with the same interface.

### Data model (SQLite)

- **AgencySettings** (one row): name, logo_path, accent_hex, owner_email, reply_to, footer.
- **GoogleConnection** (one row): email, encrypted refresh token, scopes, status (`ok` / `broken`), last_error.
- **Client**: id, name, recipients (comma-separated), ga4_property_id, gsc_site_url, send_day, enabled, created_at.
- **ReportRun**: id, client_id, period (`YYYY-MM`), status (`sent` / `held` / `failed`), attempts, last_error, pdf_path, sent_at. Unique on (client_id, period).

Refresh tokens are encrypted with Fernet using `APP_SECRET_KEY`.

### Google access

- Scopes: `analytics.readonly`, `webmasters.readonly`, plus `openid email`.
- Each install uses its own Google Cloud OAuth client (`GOOGLE_CLIENT_ID`, `GOOGLE_CLIENT_SECRET`). The README walks through creating it.
- The OAuth app must be set to **In production** publishing status. In **Testing** status Google expires refresh tokens after 7 days, which would break monthly sends. An unverified app in production shows a warning screen at sign-in and is limited to 100 users; that is fine for a self-hosted install.
- Search Console data lags 2–3 days, so the send day defaults to the 4th and the dropdown allows only 3–28.

### Scheduler

APScheduler runs `run_due_reports()` every day at 07:00 in `TZ`. For each enabled client where today's day of month ≥ send_day and there is no `sent` ReportRun for last month:

1. Fetch GA4 and Search Console data.
2. If either fetch fails, set the run to `held`, add 1 to attempts, save the error. After 3 attempts set it to `failed` and email the owner. Never send a report with a source missing.
3. Build the PDF, send the email, set the run to `sent` and record sent_at.

**Send now** and the scheduler call the same function. The unique (client_id, period) row and the `sent` check mean a client never gets the same month twice. **Send now** on a month already sent asks for confirmation and then sends again on purpose.

If Google rejects the refresh token, GoogleConnection becomes `broken`, the dashboard shows a red banner and all runs are held until the admin connects again.

## Configuration

| Variable | Purpose |
| --- | --- |
| `BASE_URL` | Public or local URL, used for the OAuth redirect (`/auth/google/callback`) |
| `ADMIN_PASSWORD` | Dashboard login |
| `APP_SECRET_KEY` | Signs the session cookie and encrypts tokens (Fernet key) |
| `GOOGLE_CLIENT_ID`, `GOOGLE_CLIENT_SECRET` | OAuth client |
| `SMTP_HOST`, `SMTP_PORT`, `SMTP_USER`, `SMTP_PASSWORD`, `FROM_EMAIL` | Email sending |
| `TZ` | Timezone for the schedule and report months, default `UTC` |
| `DATA_DIR` | SQLite file, logos and PDFs, default `/data` |

## Testing

- **Connectors:** unit tests on saved sample API responses (JSON fixtures). No live calls in CI.
- **Metrics:** pure-function tests for percentage change, the "flat" rule, sentences and edge cases (zero last month, missing month).
- **Report:** render a PDF from fixture data; check it has 3 pages and the expected headline text. Keep a PNG snapshot of page 1 to review by eye when it changes.
- **Scheduler:** with fake connectors and a fake mailer, prove that a second run on the same day does not send again, that a failed source holds the run, and that the third failure alerts the owner.
- **End to end (manual):** connect our agency Google account, add one real client, preview, then send to our own address.

## Risks

- **Google sign-in warning:** an unverified OAuth app shows a warning screen. Acceptable for self-host; document it.
- **Looker Studio is free:** our edge is the automatic monthly email and the white-label PDF, not the dashboard. Keep v1 focused on that.
- **Google Ads later** needs a developer token with Basic access, which Google has to approve. Apply early.
