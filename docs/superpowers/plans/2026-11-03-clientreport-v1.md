# clientreport v1 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** A self-hosted tool that emails each agency client a branded monthly PDF report built from GA4 and Search Console data.

**Architecture:** One FastAPI app with a SQLite database and an in-process APScheduler job. Connectors return plain dataclasses; `report/` turns them into numbers, nopurple-style matplotlib charts and a WeasyPrint PDF; `pipeline.py` wires fetch → render → email and records each run so a month is never sent twice. Everything ships as one Docker container.

**Tech Stack:** Python 3.12, FastAPI, Jinja2, SQLModel (SQLite), APScheduler 3, matplotlib, WeasyPrint, google-auth + google-auth-oauthlib, uv, pytest.

**Spec:** `docs/superpowers/specs/2026-11-03-client-reporting-v1-design.md`

## Changes from the spec (decided while planning)

1. **No HTMX.** Plain HTML forms do everything v1 needs. Less to load and test.
2. **Connectors take an `Http` object, not credentials.** `GoogleHttp` in production, `FakeHttp` in tests. This keeps connectors testable with no network.
3. **Each source has its own month dataclass** (`GA4Month`, `GSCMonth`) instead of one generic `MonthData`.
4. **New run status `pending`** for a run that was created but not tried yet.
5. **New field `Client.enabled_at`.** A client switched on after this month's send day gets its first report next month, not the next morning.
6. **No automated PNG snapshot of page 1.** WeasyPrint dropped PNG output. The PDF test checks page count and text; Task 14 checks the look by eye.

## Global Constraints

- Python `>=3.12`. Pin 3.12 locally with uv. The Docker image uses `python:3.12-slim`.
- Run every command through uv: `uv run pytest`, `uv run uvicorn ...`.
- macOS only: WeasyPrint needs Homebrew's pango. Before running tests in a new terminal: `export DYLD_FALLBACK_LIBRARY_PATH=/opt/homebrew/lib`
- Google OAuth scopes, exactly: `openid`, `https://www.googleapis.com/auth/userinfo.email`, `https://www.googleapis.com/auth/analytics.readonly`, `https://www.googleapis.com/auth/webmasters.readonly`.
- Send day: 3–28, default 4.
- Scheduler: daily at 07:00 in `TZ`. After 3 failed attempts a run becomes `failed` and the owner gets an email.
- Never send a report when a data source failed.
- A change smaller than ±2% is written as "flat".
- Charts use the vendored `chart_style.py` unchanged.
- Run uvicorn with exactly one worker. The scheduler lives in-process.
- End every commit message with the line `Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>`.

## File map

```
pyproject.toml, .python-version, .gitignore, .env.example
Dockerfile, docker-compose.yml, README.md
clientreport/
  __init__.py
  config.py            Settings from environment variables
  crypto.py            Fernet encrypt/decrypt for the Google refresh token
  months.py            "YYYY-MM" period helpers
  db.py                SQLModel tables + get_agency/get_google
  connectors/
    __init__.py
    base.py            Http protocol, errors, Source, GA4Month, GSCMonth, SearchRow
    ga4.py             list_properties, fetch_month
    gsc.py             list_sites, fetch_month
  report/
    __init__.py
    metrics.py         Headline, pct_change, headline(s), channel_title, trend_title
    chart_style.py     Vendored from nopurple (MIT)
    chart_style.LICENSE
    charts.py          channels_chart, trend_chart -> PNG bytes
    render.py          Branding, Report, render_report -> PDF
    templates/report.html
  mail.py              SmtpConfig, build_message, send
  pipeline.py          Deps, build_report, send_report, emails
  scheduler.py         due_clients, run_due_reports, start_scheduler
  google_oauth.py      GoogleHttp, start_flow, finish_flow, save_connection, make_http
  app.py               create_app, default_deps
  main.py              app = create_app(load_settings())
  routes/
    __init__.py
    common.py          templates, db, require_login, render, error_page
    auth.py            /login, /logout
    pages.py           /, /settings
    google.py          /auth/google/start, /auth/google/callback
    clients.py         client CRUD, toggle, preview, send
  templates/           base, login, dashboard, settings, client_form, confirm_send, error
tests/
  conftest.py, fakes.py, fixtures/*.json, test_*.py
```

---

### Task 1: Project setup, config and encryption

About 20 minutes.

**Files:**
- Create: `pyproject.toml`, `.python-version` (via uv), `.gitignore`, `clientreport/__init__.py`, `clientreport/config.py`, `clientreport/crypto.py`
- Test: `tests/test_config.py`

**Interfaces:**
- Produces: `Settings` (frozen dataclass: `base_url, admin_password, app_secret_key, google_client_id, google_client_secret, smtp_host, smtp_port: int, smtp_user, smtp_password, from_email, tz, data_dir: Path`), `load_settings(env: Mapping[str, str] = os.environ) -> Settings`, `crypto.encrypt(key: str, text: str) -> str`, `crypto.decrypt(key: str, token: str) -> str`.

- [ ] **Step 1: Create the project files**

`pyproject.toml`:

```toml
[project]
name = "clientreport"
version = "0.1.0"
description = "Open-source monthly client reports for agencies"
requires-python = ">=3.12"
license = "MIT"
dependencies = [
    "fastapi>=0.115",
    "uvicorn[standard]>=0.32",
    "jinja2>=3.1",
    "python-multipart>=0.0.12",
    "itsdangerous>=2.2",
    "sqlmodel>=0.0.22",
    "apscheduler>=3.10,<4",
    "matplotlib>=3.9",
    "weasyprint>=63",
    "cryptography>=43",
    "google-auth>=2.35",
    "google-auth-oauthlib>=1.2",
    "requests>=2.32",
]

[dependency-groups]
dev = ["pytest>=8.3", "httpx>=0.27", "pypdf>=5.1"]

[build-system]
requires = ["hatchling"]
build-backend = "hatchling.build"

[tool.pytest.ini_options]
testpaths = ["tests"]
pythonpath = ["tests"]
```

`.gitignore`:

```
.venv/
__pycache__/
*.pyc
.pytest_cache/
data/
.env
```

`clientreport/__init__.py`:

```python
"""clientreport: open-source monthly client reports for agencies."""
```

Then run:

```bash
uv python pin 3.12
uv sync
```

Expected: `.python-version` contains `3.12` and `uv sync` ends with `Installed N packages`.

- [ ] **Step 2: Write the failing test**

`tests/test_config.py`:

```python
import pytest
from cryptography.fernet import Fernet

from clientreport import crypto
from clientreport.config import load_settings

KEY = Fernet.generate_key().decode()
BASE = {"BASE_URL": "http://localhost:8000/", "ADMIN_PASSWORD": "pw", "APP_SECRET_KEY": KEY}


def test_load_settings_defaults():
    s = load_settings(BASE)
    assert s.base_url == "http://localhost:8000"
    assert s.smtp_port == 587
    assert s.tz == "UTC"
    assert str(s.data_dir) == "/data"
    assert s.google_client_id == ""


def test_missing_required_variable():
    with pytest.raises(RuntimeError, match="ADMIN_PASSWORD"):
        load_settings({"BASE_URL": "http://x", "APP_SECRET_KEY": KEY})


def test_secret_key_must_be_fernet_key():
    with pytest.raises(RuntimeError, match="Fernet"):
        load_settings({**BASE, "APP_SECRET_KEY": "short"})


def test_encrypt_roundtrip():
    token = crypto.encrypt(KEY, "refresh-123")
    assert token != "refresh-123"
    assert crypto.decrypt(KEY, token) == "refresh-123"
```

- [ ] **Step 3: Run the test to see it fail**

Run: `uv run pytest tests/test_config.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'clientreport.config'`

- [ ] **Step 4: Implement**

`clientreport/crypto.py`:

```python
"""Encrypt secrets at rest with the app's Fernet key."""

from cryptography.fernet import Fernet


def encrypt(key: str, text: str) -> str:
    return Fernet(key.encode()).encrypt(text.encode()).decode()


def decrypt(key: str, token: str) -> str:
    return Fernet(key.encode()).decrypt(token.encode()).decode()
```

`clientreport/config.py`:

```python
"""Settings from environment variables."""

import os
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path

from cryptography.fernet import Fernet

REQUIRED = ("BASE_URL", "ADMIN_PASSWORD", "APP_SECRET_KEY")


@dataclass(frozen=True)
class Settings:
    base_url: str
    admin_password: str
    app_secret_key: str
    google_client_id: str
    google_client_secret: str
    smtp_host: str
    smtp_port: int
    smtp_user: str
    smtp_password: str
    from_email: str
    tz: str
    data_dir: Path


def load_settings(env: Mapping[str, str] = os.environ) -> Settings:
    missing = [name for name in REQUIRED if not env.get(name)]
    if missing:
        raise RuntimeError("Missing environment variables: " + ", ".join(missing))
    key = env["APP_SECRET_KEY"]
    try:
        Fernet(key.encode())
    except (ValueError, TypeError) as exc:
        raise RuntimeError(
            "APP_SECRET_KEY must be a Fernet key. Make one with: "
            'python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"'
        ) from exc
    return Settings(
        base_url=env["BASE_URL"].rstrip("/"),
        admin_password=env["ADMIN_PASSWORD"],
        app_secret_key=key,
        google_client_id=env.get("GOOGLE_CLIENT_ID", ""),
        google_client_secret=env.get("GOOGLE_CLIENT_SECRET", ""),
        smtp_host=env.get("SMTP_HOST", ""),
        smtp_port=int(env.get("SMTP_PORT", "587")),
        smtp_user=env.get("SMTP_USER", ""),
        smtp_password=env.get("SMTP_PASSWORD", ""),
        from_email=env.get("FROM_EMAIL", ""),
        tz=env.get("TZ", "UTC"),
        data_dir=Path(env.get("DATA_DIR", "/data")),
    )
```

- [ ] **Step 5: Run the test to see it pass**

Run: `uv run pytest tests/test_config.py -v`
Expected: 4 passed

- [ ] **Step 6: Commit**

```bash
git add pyproject.toml uv.lock .python-version .gitignore clientreport tests
git commit -m "Set up project with config and token encryption" -m "Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 2: Month helpers and database tables

About 20 minutes.

**Files:**
- Create: `clientreport/months.py`, `clientreport/db.py`
- Test: `tests/test_months.py`, `tests/test_db.py`

**Interfaces:**
- Produces (`months.py`): `shift(period: str, n: int) -> str`, `previous_month(today: date) -> str`, `month_bounds(period: str) -> tuple[date, date]`, `last_n(period: str, n: int) -> list[str]` (oldest first, ends with `period`), `label(period) -> "October 2026"`, `short_label(period) -> "Oct 26"`.
- Produces (`db.py`): tables `AgencySettings`, `GoogleConnection`, `Client` (with `recipient_list() -> list[str]`, `enabled_at: date | None`), `ReportRun` (status `pending | held | sent | failed`, unique on `client_id, period`); `make_engine(data_dir: Path)`, `get_agency(session) -> AgencySettings`, `get_google(session) -> GoogleConnection | None`.

- [ ] **Step 1: Write the failing tests**

`tests/test_months.py`:

```python
from datetime import date

from clientreport.months import label, last_n, month_bounds, previous_month, shift, short_label


def test_shift_across_year():
    assert shift("2026-01", -1) == "2025-12"
    assert shift("2025-12", 1) == "2026-01"


def test_previous_month():
    assert previous_month(date(2026, 11, 3)) == "2026-10"
    assert previous_month(date(2026, 1, 15)) == "2025-12"


def test_month_bounds_leap_year():
    assert month_bounds("2028-02") == (date(2028, 2, 1), date(2028, 2, 29))


def test_last_n():
    months = last_n("2026-10", 13)
    assert len(months) == 13
    assert months[0] == "2025-10"
    assert months[-1] == "2026-10"


def test_labels():
    assert label("2026-10") == "October 2026"
    assert short_label("2026-10") == "Oct 26"
```

`tests/test_db.py`:

```python
import pytest
from sqlalchemy.exc import IntegrityError
from sqlmodel import Session

from clientreport.db import Client, ReportRun, get_agency, make_engine


def make_client(**overrides):
    values = dict(name="Acme Ltd", recipients="ceo@acme.com", ga4_property_id="111", gsc_site_url="sc-domain:acme.com")
    values.update(overrides)
    return Client(**values)


def test_agency_row_is_created_once(tmp_path):
    engine = make_engine(tmp_path)
    with Session(engine) as session:
        agency = get_agency(session)
        agency.name = "Our Agency"
        session.add(agency)
        session.commit()
        assert get_agency(session).name == "Our Agency"


def test_recipient_list_trims_and_skips_blanks():
    client = make_client(recipients=" a@x.com, b@x.com ,")
    assert client.recipient_list() == ["a@x.com", "b@x.com"]


def test_one_run_per_client_and_month(tmp_path):
    engine = make_engine(tmp_path)
    with Session(engine) as session:
        client = make_client()
        session.add(client)
        session.commit()
        session.refresh(client)
        session.add(ReportRun(client_id=client.id, period="2026-10"))
        session.commit()
        session.add(ReportRun(client_id=client.id, period="2026-10"))
        with pytest.raises(IntegrityError):
            session.commit()
```

- [ ] **Step 2: Run the tests to see them fail**

Run: `uv run pytest tests/test_months.py tests/test_db.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'clientreport.months'`

- [ ] **Step 3: Implement**

`clientreport/months.py`:

```python
"""Helpers for report periods written as "YYYY-MM"."""

import calendar
from datetime import date


def _parse(period: str) -> tuple[int, int]:
    year, month = period.split("-")
    return int(year), int(month)


def shift(period: str, n: int) -> str:
    year, month = _parse(period)
    index = year * 12 + (month - 1) + n
    return f"{index // 12:04d}-{index % 12 + 1:02d}"


def previous_month(today: date) -> str:
    return shift(f"{today.year:04d}-{today.month:02d}", -1)


def month_bounds(period: str) -> tuple[date, date]:
    year, month = _parse(period)
    return date(year, month, 1), date(year, month, calendar.monthrange(year, month)[1])


def last_n(period: str, n: int) -> list[str]:
    return [shift(period, i) for i in range(-(n - 1), 1)]


def label(period: str) -> str:
    year, month = _parse(period)
    return f"{calendar.month_name[month]} {year}"


def short_label(period: str) -> str:
    year, month = _parse(period)
    return f"{calendar.month_abbr[month]} {str(year)[2:]}"
```

`clientreport/db.py`:

```python
"""SQLite tables and small helpers."""

from datetime import UTC, date, datetime
from pathlib import Path

from sqlalchemy import UniqueConstraint
from sqlmodel import Field, Session, SQLModel, create_engine


class AgencySettings(SQLModel, table=True):
    id: int = Field(default=1, primary_key=True)
    name: str = "My Agency"
    logo_path: str = ""
    accent_hex: str = "#d9480f"
    owner_email: str = ""
    reply_to: str = ""
    footer: str = ""


class GoogleConnection(SQLModel, table=True):
    id: int = Field(default=1, primary_key=True)
    email: str = ""
    refresh_token_enc: str = ""
    scopes: str = ""
    status: str = "ok"  # "ok" or "broken"
    last_error: str = ""


class Client(SQLModel, table=True):
    id: int | None = Field(default=None, primary_key=True)
    name: str
    recipients: str  # comma-separated emails
    ga4_property_id: str
    gsc_site_url: str
    send_day: int = 4
    enabled: bool = False
    enabled_at: date | None = None
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))

    def recipient_list(self) -> list[str]:
        return [r.strip() for r in self.recipients.split(",") if r.strip()]


class ReportRun(SQLModel, table=True):
    __table_args__ = (UniqueConstraint("client_id", "period"),)

    id: int | None = Field(default=None, primary_key=True)
    client_id: int = Field(foreign_key="client.id")
    period: str  # YYYY-MM
    status: str = "pending"  # "pending", "held", "sent" or "failed"
    attempts: int = 0
    last_error: str = ""
    pdf_path: str = ""
    sent_at: datetime | None = None


def make_engine(data_dir: Path):
    data_dir.mkdir(parents=True, exist_ok=True)
    engine = create_engine(
        f"sqlite:///{data_dir / 'clientreport.db'}", connect_args={"check_same_thread": False}
    )
    SQLModel.metadata.create_all(engine)
    return engine


def get_agency(session: Session) -> AgencySettings:
    agency = session.get(AgencySettings, 1)
    if agency is None:
        agency = AgencySettings()
        session.add(agency)
        session.commit()
        session.refresh(agency)
    return agency


def get_google(session: Session) -> GoogleConnection | None:
    return session.get(GoogleConnection, 1)
```

- [ ] **Step 4: Run the tests to see them pass**

Run: `uv run pytest tests/test_months.py tests/test_db.py -v`
Expected: 8 passed

- [ ] **Step 5: Commit**

```bash
git add clientreport/months.py clientreport/db.py tests/test_months.py tests/test_db.py
git commit -m "Add month helpers and database tables" -m "Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 3: Connector types and report maths

About 20 minutes.

**Files:**
- Create: `clientreport/connectors/__init__.py` (empty), `clientreport/connectors/base.py`, `clientreport/report/__init__.py` (empty), `clientreport/report/metrics.py`
- Test: `tests/test_metrics.py`

**Interfaces:**
- Produces (`connectors/base.py`): `Http` protocol (`get(url) -> dict`, `post(url, body: dict) -> dict`), `SourceError(Exception)`, `GoogleAuthError(SourceError)`, `Source(id, name)`, `GA4Month(period, sessions, prev_sessions, key_events, prev_key_events, channels: list[tuple[str, int]], trend: list[tuple[str, int]])`, `SearchRow(key, clicks, impressions, ctr, position)`, `GSCMonth(period, clicks, prev_clicks, impressions, prev_impressions, trend, top_queries: list[SearchRow], top_pages: list[SearchRow])`.
- Produces (`report/metrics.py`): `Headline(label, value, prev, change_pct, sentence)`, `pct_change(cur, prev) -> float | None`, `headline(label, cur, prev) -> Headline`, `headlines(ga4: GA4Month, gsc: GSCMonth) -> list[Headline]` (Sessions, Key events, Organic clicks), `channel_title(channels) -> str`, `trend_title(label, trend) -> str`.

- [ ] **Step 1: Write the failing test**

`tests/test_metrics.py`:

```python
from clientreport.connectors.base import GA4Month, GSCMonth
from clientreport.report.metrics import channel_title, headline, headlines, pct_change, trend_title


def test_pct_change():
    assert pct_change(110, 100) == 10
    assert pct_change(5, 0) is None


def test_headline_up():
    assert headline("Sessions", 1200, 1100).sentence == "Sessions up 9%: 1,200"


def test_headline_down():
    assert headline("Key events", 30, 40).sentence == "Key events down 25%: 30"


def test_headline_flat_under_two_percent():
    assert headline("Sessions", 1010, 1000).sentence == "Sessions flat: 1,010"


def test_headline_with_nothing_last_month():
    assert headline("Organic clicks", 50, 0).sentence == "Organic clicks: 50 (none last month)"
    assert headline("Organic clicks", 0, 0).sentence == "Organic clicks: 0"


def test_headlines_order():
    ga4 = GA4Month("2026-10", 1200, 1100, 48, 40, [], [])
    gsc = GSCMonth("2026-10", 500, 400, 21000, 18000, [], [], [])
    assert [h.sentence for h in headlines(ga4, gsc)] == [
        "Sessions up 9%: 1,200",
        "Key events up 20%: 48",
        "Organic clicks up 25%: 500",
    ]


def test_channel_title():
    channels = [("Organic Search", 620), ("Direct", 310), ("Paid Search", 270)]
    assert channel_title(channels) == "Organic Search brought 52% of sessions: 620 of 1,200"


def test_channel_title_with_no_sessions():
    assert channel_title([]) == "No sessions recorded this month"


def test_trend_title_compares_with_a_year_ago():
    trend = [("2025-10", 100)] + [("x", 0)] * 11 + [("2026-10", 500)]
    assert trend_title("Organic clicks", trend) == "Organic clicks up 400% on a year ago: 500"


def test_trend_title_without_history():
    assert trend_title("Sessions", [("2025-10", 0), ("2026-10", 500)]) == "Sessions in the latest month: 500"
```

- [ ] **Step 2: Run the test to see it fail**

Run: `uv run pytest tests/test_metrics.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'clientreport.connectors'`

- [ ] **Step 3: Implement**

Create empty `clientreport/connectors/__init__.py` and `clientreport/report/__init__.py`.

`clientreport/connectors/base.py`:

```python
"""Shared types for data source connectors."""

from dataclasses import dataclass
from typing import Protocol


class Http(Protocol):
    """Minimal JSON HTTP client: GoogleHttp in production, FakeHttp in tests."""

    def get(self, url: str) -> dict: ...

    def post(self, url: str, body: dict) -> dict: ...


class SourceError(Exception):
    """A data source could not be read."""


class GoogleAuthError(SourceError):
    """Google sign-in is missing, revoked or expired."""


@dataclass(frozen=True)
class Source:
    id: str
    name: str


@dataclass(frozen=True)
class GA4Month:
    period: str
    sessions: int
    prev_sessions: int
    key_events: int
    prev_key_events: int
    channels: list[tuple[str, int]]  # this month, biggest first
    trend: list[tuple[str, int]]  # (YYYY-MM, sessions), 13 months, oldest first


@dataclass(frozen=True)
class SearchRow:
    key: str
    clicks: int
    impressions: int
    ctr: float
    position: float


@dataclass(frozen=True)
class GSCMonth:
    period: str
    clicks: int
    prev_clicks: int
    impressions: int
    prev_impressions: int
    trend: list[tuple[str, int]]  # (YYYY-MM, clicks), 13 months, oldest first
    top_queries: list[SearchRow]
    top_pages: list[SearchRow]
```

`clientreport/report/metrics.py`:

```python
"""Month-over-month maths and headline sentences. Pure functions."""

from dataclasses import dataclass

from clientreport.connectors.base import GA4Month, GSCMonth

FLAT_PCT = 2


@dataclass(frozen=True)
class Headline:
    label: str
    value: int
    prev: int
    change_pct: float | None
    sentence: str


def pct_change(cur: int, prev: int) -> float | None:
    if prev == 0:
        return None
    return (cur - prev) / prev * 100


def _direction(change: float) -> str:
    return f"{'up' if change > 0 else 'down'} {round(abs(change))}%"


def headline(label: str, cur: int, prev: int) -> Headline:
    change = pct_change(cur, prev)
    value = f"{cur:,}"
    if change is None:
        sentence = f"{label}: {value}" if cur == 0 else f"{label}: {value} (none last month)"
    elif abs(change) < FLAT_PCT:
        sentence = f"{label} flat: {value}"
    else:
        sentence = f"{label} {_direction(change)}: {value}"
    return Headline(label, cur, prev, change, sentence)


def headlines(ga4: GA4Month, gsc: GSCMonth) -> list[Headline]:
    return [
        headline("Sessions", ga4.sessions, ga4.prev_sessions),
        headline("Key events", ga4.key_events, ga4.prev_key_events),
        headline("Organic clicks", gsc.clicks, gsc.prev_clicks),
    ]


def channel_title(channels: list[tuple[str, int]]) -> str:
    total = sum(n for _, n in channels)
    if total == 0:
        return "No sessions recorded this month"
    name, n = channels[0]
    return f"{name} brought {round(n / total * 100)}% of sessions: {n:,} of {total:,}"


def trend_title(label: str, trend: list[tuple[str, int]]) -> str:
    latest = trend[-1][1]
    change = pct_change(latest, trend[0][1])
    value = f"{latest:,}"
    if change is None:
        return f"{label} in the latest month: {value}"
    if abs(change) < FLAT_PCT:
        return f"{label} flat on a year ago: {value}"
    return f"{label} {_direction(change)} on a year ago: {value}"
```

- [ ] **Step 4: Run the test to see it pass**

Run: `uv run pytest tests/test_metrics.py -v`
Expected: 10 passed

- [ ] **Step 5: Commit**

```bash
git add clientreport/connectors clientreport/report tests/test_metrics.py
git commit -m "Add connector types and headline maths" -m "Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 4: GA4 connector with sample-response fixtures

About 30 minutes.

**Files:**
- Create: `clientreport/connectors/ga4.py`, `tests/fakes.py`, `tests/fixtures/ga4_account_summaries.json`, `tests/fixtures/ga4_totals.json`, `tests/fixtures/ga4_channels.json`, `tests/fixtures/ga4_trend.json`
- Create now (used in Task 5): `tests/fixtures/gsc_sites.json`, `tests/fixtures/gsc_totals.json`, `tests/fixtures/gsc_trend.json`, `tests/fixtures/gsc_queries.json`, `tests/fixtures/gsc_pages.json`
- Test: `tests/test_ga4.py`

**Interfaces:**
- Consumes: `Http`, `Source`, `GA4Month` (Task 3); `shift`, `month_bounds`, `last_n` (Task 2).
- Produces: `ga4.list_properties(http) -> list[Source]` (id = numeric property id, name = `"<property> (<account>)"`, sorted case-insensitively), `ga4.fetch_month(http, property_id: str, period: str) -> GA4Month`.
- Produces (tests): `fakes.FakeHttp(fail: Exception | None = None, fail_on: str | None = None, empty: bool = False)` with `.gets: list[str]`, `.posts: list[tuple[str, dict]]` and a writable `.fail`; `fakes.FakeMailer(fail: Exception | None = None)` with `.sent: list[EmailMessage]`; `fakes.load(name) -> dict`.

- [ ] **Step 1: Add the fixtures**

These are shaped like real API replies. The numbers are made up and agree with each other: October 2026 has 1,200 sessions, September 1,100.

`tests/fixtures/ga4_account_summaries.json`:

```json
{
  "accountSummaries": [
    {
      "account": "accounts/1",
      "displayName": "Acme Ltd",
      "propertySummaries": [
        {"property": "properties/111", "displayName": "acme.com"},
        {"property": "properties/222", "displayName": "Acme Blog"}
      ]
    }
  ]
}
```

`tests/fixtures/ga4_totals.json`:

```json
{
  "dimensionHeaders": [{"name": "dateRange"}],
  "metricHeaders": [{"name": "sessions", "type": "TYPE_INTEGER"}, {"name": "keyEvents", "type": "TYPE_FLOAT"}],
  "rows": [
    {"dimensionValues": [{"value": "current"}], "metricValues": [{"value": "1200"}, {"value": "48"}]},
    {"dimensionValues": [{"value": "previous"}], "metricValues": [{"value": "1100"}, {"value": "40"}]}
  ]
}
```

`tests/fixtures/ga4_channels.json`:

```json
{
  "rows": [
    {"dimensionValues": [{"value": "Organic Search"}], "metricValues": [{"value": "620"}]},
    {"dimensionValues": [{"value": "Direct"}], "metricValues": [{"value": "310"}]},
    {"dimensionValues": [{"value": "Paid Search"}], "metricValues": [{"value": "150"}]},
    {"dimensionValues": [{"value": "Referral"}], "metricValues": [{"value": "80"}]},
    {"dimensionValues": [{"value": "Organic Social"}], "metricValues": [{"value": "40"}]}
  ]
}
```

`tests/fixtures/ga4_trend.json`:

```json
{
  "rows": [
    {"dimensionValues": [{"value": "202510"}], "metricValues": [{"value": "900"}]},
    {"dimensionValues": [{"value": "202609"}], "metricValues": [{"value": "1100"}]},
    {"dimensionValues": [{"value": "202610"}], "metricValues": [{"value": "1200"}]}
  ]
}
```

`tests/fixtures/gsc_sites.json`:

```json
{
  "siteEntry": [
    {"siteUrl": "sc-domain:acme.com", "permissionLevel": "siteOwner"},
    {"siteUrl": "https://old.acme.com/", "permissionLevel": "siteUnverifiedUser"}
  ]
}
```

`tests/fixtures/gsc_totals.json` (keyed by the request's `startDate`, see `FakeHttp`):

```json
{
  "2026-10-01": {"rows": [{"clicks": 500, "impressions": 21000, "ctr": 0.0238, "position": 12.4}], "responseAggregationType": "byProperty"},
  "2026-09-01": {"rows": [{"clicks": 400, "impressions": 18000, "ctr": 0.0222, "position": 13.1}], "responseAggregationType": "byProperty"}
}
```

`tests/fixtures/gsc_trend.json`:

```json
{
  "rows": [
    {"keys": ["2025-10-01"], "clicks": 100, "impressions": 5000, "ctr": 0.02, "position": 15.0},
    {"keys": ["2026-09-01"], "clicks": 300, "impressions": 9000, "ctr": 0.0333, "position": 13.0},
    {"keys": ["2026-09-15"], "clicks": 100, "impressions": 9000, "ctr": 0.0111, "position": 13.2},
    {"keys": ["2026-10-01"], "clicks": 250, "impressions": 10500, "ctr": 0.0238, "position": 12.4},
    {"keys": ["2026-10-02"], "clicks": 250, "impressions": 10500, "ctr": 0.0238, "position": 12.4}
  ]
}
```

`tests/fixtures/gsc_queries.json`:

```json
{
  "rows": [
    {"keys": ["acme widgets"], "clicks": 120, "impressions": 2400, "ctr": 0.05, "position": 3.2},
    {"keys": ["buy widgets"], "clicks": 80, "impressions": 4000, "ctr": 0.02, "position": 7.5},
    {"keys": ["acme"], "clicks": 60, "impressions": 300, "ctr": 0.2, "position": 1.1}
  ]
}
```

`tests/fixtures/gsc_pages.json`:

```json
{
  "rows": [
    {"keys": ["https://acme.com/"], "clicks": 300, "impressions": 9000, "ctr": 0.0333, "position": 5.0},
    {"keys": ["https://acme.com/widgets"], "clicks": 150, "impressions": 7000, "ctr": 0.0214, "position": 9.8}
  ]
}
```

- [ ] **Step 2: Add the test fakes**

`tests/fakes.py`:

```python
"""Fake Google HTTP client and mailer for tests. No network."""

import json
from email.message import EmailMessage
from pathlib import Path

FIXTURES = Path(__file__).parent / "fixtures"

RUN_REPORT = {
    (): "ga4_totals.json",
    ("sessionDefaultChannelGroup",): "ga4_channels.json",
    ("yearMonth",): "ga4_trend.json",
}
SEARCH = {
    ("date",): "gsc_trend.json",
    ("query",): "gsc_queries.json",
    ("page",): "gsc_pages.json",
}


def load(name: str) -> dict:
    return json.loads((FIXTURES / name).read_text())


class FakeHttp:
    """Answers GA4 and Search Console calls from fixtures.

    fail: raise this error. fail_on: only for URLs containing this text.
    empty: answer every POST with {} (an account with no data).
    """

    def __init__(self, fail: Exception | None = None, fail_on: str | None = None, empty: bool = False):
        self.fail = fail
        self.fail_on = fail_on
        self.empty = empty
        self.gets: list[str] = []
        self.posts: list[tuple[str, dict]] = []

    def _maybe_fail(self, url: str) -> None:
        if self.fail and (self.fail_on is None or self.fail_on in url):
            raise self.fail

    def get(self, url: str) -> dict:
        self.gets.append(url)
        self._maybe_fail(url)
        if "accountSummaries" in url:
            return load("ga4_account_summaries.json")
        if url.endswith("/sites"):
            return load("gsc_sites.json")
        raise AssertionError(f"unexpected GET {url}")

    def post(self, url: str, body: dict) -> dict:
        self.posts.append((url, body))
        self._maybe_fail(url)
        if self.empty:
            return {}
        dims = tuple(d["name"] if isinstance(d, dict) else d for d in body.get("dimensions", []))
        if ":runReport" in url:
            return load(RUN_REPORT[dims])
        if "searchAnalytics" in url:
            if not dims:
                return load("gsc_totals.json")[body["startDate"]]
            return load(SEARCH[dims])
        raise AssertionError(f"unexpected POST {url}")


class FakeMailer:
    def __init__(self, fail: Exception | None = None):
        self.fail = fail
        self.sent: list[EmailMessage] = []

    def __call__(self, message: EmailMessage) -> None:
        if self.fail:
            raise self.fail
        self.sent.append(message)
```

- [ ] **Step 3: Write the failing test**

`tests/test_ga4.py`:

```python
from fakes import FakeHttp

from clientreport.connectors import ga4
from clientreport.connectors.base import Source


def test_list_properties():
    assert ga4.list_properties(FakeHttp()) == [
        Source("222", "Acme Blog (Acme Ltd)"),
        Source("111", "acme.com (Acme Ltd)"),
    ]


def test_fetch_month():
    m = ga4.fetch_month(FakeHttp(), "111", "2026-10")
    assert (m.sessions, m.prev_sessions) == (1200, 1100)
    assert (m.key_events, m.prev_key_events) == (48, 40)
    assert m.channels[0] == ("Organic Search", 620)
    assert len(m.channels) == 5
    assert len(m.trend) == 13
    assert m.trend[0] == ("2025-10", 900)
    assert m.trend[1] == ("2025-11", 0)
    assert m.trend[-1] == ("2026-10", 1200)


def test_fetch_month_asks_for_this_and_last_month():
    http = FakeHttp()
    ga4.fetch_month(http, "111", "2026-10")
    url, body = http.posts[0]
    assert url.endswith("/properties/111:runReport")
    assert body["dateRanges"] == [
        {"startDate": "2026-10-01", "endDate": "2026-10-31", "name": "current"},
        {"startDate": "2026-09-01", "endDate": "2026-09-30", "name": "previous"},
    ]
    trend_body = http.posts[2][1]
    assert trend_body["dateRanges"] == [{"startDate": "2025-10-01", "endDate": "2026-10-31"}]


def test_fetch_month_with_no_data():
    m = ga4.fetch_month(FakeHttp(empty=True), "111", "2026-10")
    assert (m.sessions, m.key_events, m.channels) == (0, 0, [])
    assert all(value == 0 for _, value in m.trend)
```

- [ ] **Step 4: Run the test to see it fail**

Run: `uv run pytest tests/test_ga4.py -v`
Expected: FAIL with `ImportError: cannot import name 'ga4'`

- [ ] **Step 5: Implement**

`clientreport/connectors/ga4.py`:

```python
"""Google Analytics 4: property list and one month of data."""

from clientreport.connectors.base import GA4Month, Http, Source
from clientreport.months import last_n, month_bounds, shift

DATA_API = "https://analyticsdata.googleapis.com/v1beta"
ADMIN_API = "https://analyticsadmin.googleapis.com/v1beta"


def list_properties(http: Http) -> list[Source]:
    sources: list[Source] = []
    token = ""
    while True:
        url = f"{ADMIN_API}/accountSummaries?pageSize=200"
        if token:
            url += f"&pageToken={token}"
        data = http.get(url)
        for account in data.get("accountSummaries", []):
            for prop in account.get("propertySummaries", []):
                prop_id = prop["property"].split("/")[-1]
                sources.append(Source(prop_id, f"{prop['displayName']} ({account.get('displayName', '')})"))
        token = data.get("nextPageToken", "")
        if not token:
            break
    return sorted(sources, key=lambda s: s.name.lower())


def _range(period: str, name: str | None = None) -> dict:
    start, end = month_bounds(period)
    date_range = {"startDate": start.isoformat(), "endDate": end.isoformat()}
    if name:
        date_range["name"] = name
    return date_range


def _num(row: dict, index: int = 0) -> int:
    return int(float(row["metricValues"][index]["value"]))


def _key(row: dict) -> str:
    return row["dimensionValues"][0]["value"]


def fetch_month(http: Http, property_id: str, period: str) -> GA4Month:
    url = f"{DATA_API}/properties/{property_id}:runReport"

    # With two named date ranges, GA4 adds a dateRange dimension holding the range name.
    totals = http.post(url, {
        "dateRanges": [_range(period, "current"), _range(shift(period, -1), "previous")],
        "metrics": [{"name": "sessions"}, {"name": "keyEvents"}],
    })
    by_range = {_key(row): row for row in totals.get("rows", [])}

    def total(range_name: str, index: int) -> int:
        row = by_range.get(range_name)
        return _num(row, index) if row else 0

    channels = http.post(url, {
        "dateRanges": [_range(period)],
        "dimensions": [{"name": "sessionDefaultChannelGroup"}],
        "metrics": [{"name": "sessions"}],
        "orderBys": [{"metric": {"metricName": "sessions"}, "desc": True}],
        "limit": 10,
    })

    months = last_n(period, 13)
    trend = http.post(url, {
        "dateRanges": [{
            "startDate": month_bounds(months[0])[0].isoformat(),
            "endDate": month_bounds(period)[1].isoformat(),
        }],
        "dimensions": [{"name": "yearMonth"}],
        "metrics": [{"name": "sessions"}],
    })
    per_month = {_key(row): _num(row) for row in trend.get("rows", [])}

    return GA4Month(
        period=period,
        sessions=total("current", 0),
        prev_sessions=total("previous", 0),
        key_events=total("current", 1),
        prev_key_events=total("previous", 1),
        channels=[(_key(row), _num(row)) for row in channels.get("rows", [])],
        trend=[(m, per_month.get(m.replace("-", ""), 0)) for m in months],
    )
```

- [ ] **Step 6: Run the test to see it pass**

Run: `uv run pytest tests/test_ga4.py -v`
Expected: 4 passed

- [ ] **Step 7: Commit**

```bash
git add clientreport/connectors/ga4.py tests/fakes.py tests/fixtures tests/test_ga4.py
git commit -m "Add GA4 connector" -m "Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 5: Search Console connector

About 20 minutes.

**Files:**
- Create: `clientreport/connectors/gsc.py`
- Test: `tests/test_gsc.py`

**Interfaces:**
- Consumes: `Http`, `Source`, `GSCMonth`, `SearchRow` (Task 3); fixtures and `FakeHttp` (Task 4).
- Produces: `gsc.list_sites(http) -> list[Source]` (skips `siteUnverifiedUser`), `gsc.fetch_month(http, site_url: str, period: str) -> GSCMonth`.

- [ ] **Step 1: Write the failing test**

`tests/test_gsc.py`:

```python
from fakes import FakeHttp

from clientreport.connectors import gsc
from clientreport.connectors.base import SearchRow, Source


def test_list_sites_skips_unverified():
    assert gsc.list_sites(FakeHttp()) == [Source("sc-domain:acme.com", "sc-domain:acme.com")]


def test_fetch_month():
    m = gsc.fetch_month(FakeHttp(), "sc-domain:acme.com", "2026-10")
    assert (m.clicks, m.prev_clicks) == (500, 400)
    assert (m.impressions, m.prev_impressions) == (21000, 18000)
    assert len(m.trend) == 13
    assert m.trend[0] == ("2025-10", 100)
    assert m.trend[-2] == ("2026-09", 400)
    assert m.trend[-1] == ("2026-10", 500)
    assert m.top_queries[0] == SearchRow("acme widgets", 120, 2400, 0.05, 3.2)
    assert m.top_pages[1].key == "https://acme.com/widgets"


def test_site_url_is_encoded():
    http = FakeHttp()
    gsc.fetch_month(http, "sc-domain:acme.com", "2026-10")
    assert "/sites/sc-domain%3Aacme.com/searchAnalytics/query" in http.posts[0][0]


def test_fetch_month_with_no_data():
    m = gsc.fetch_month(FakeHttp(empty=True), "sc-domain:acme.com", "2026-10")
    assert (m.clicks, m.top_queries, m.top_pages) == (0, [], [])
    assert all(value == 0 for _, value in m.trend)
```

- [ ] **Step 2: Run the test to see it fail**

Run: `uv run pytest tests/test_gsc.py -v`
Expected: FAIL with `ImportError: cannot import name 'gsc'`

- [ ] **Step 3: Implement**

`clientreport/connectors/gsc.py`:

```python
"""Google Search Console: site list and one month of data."""

from collections import defaultdict
from collections.abc import Sequence
from datetime import date
from urllib.parse import quote

from clientreport.connectors.base import GSCMonth, Http, SearchRow, Source
from clientreport.months import last_n, month_bounds, shift

API = "https://www.googleapis.com/webmasters/v3"


def list_sites(http: Http) -> list[Source]:
    data = http.get(f"{API}/sites")
    sites = [
        entry["siteUrl"]
        for entry in data.get("siteEntry", [])
        if entry.get("permissionLevel") != "siteUnverifiedUser"
    ]
    return [Source(site, site) for site in sorted(sites)]


def _query(http: Http, site_url: str, start: date, end: date,
           dimensions: Sequence[str] = (), row_limit: int | None = None) -> list[dict]:
    body: dict = {"startDate": start.isoformat(), "endDate": end.isoformat()}
    if dimensions:
        body["dimensions"] = list(dimensions)
    if row_limit:
        body["rowLimit"] = row_limit
    url = f"{API}/sites/{quote(site_url, safe='')}/searchAnalytics/query"
    return http.post(url, body).get("rows", [])


def _month_totals(http: Http, site_url: str, period: str) -> tuple[int, int]:
    rows = _query(http, site_url, *month_bounds(period))
    if not rows:
        return 0, 0
    return int(rows[0]["clicks"]), int(rows[0]["impressions"])


def _row(raw: dict) -> SearchRow:
    return SearchRow(
        key=raw["keys"][0],
        clicks=int(raw["clicks"]),
        impressions=int(raw["impressions"]),
        ctr=float(raw["ctr"]),
        position=float(raw["position"]),
    )


def fetch_month(http: Http, site_url: str, period: str) -> GSCMonth:
    clicks, impressions = _month_totals(http, site_url, period)
    prev_clicks, prev_impressions = _month_totals(http, site_url, shift(period, -1))

    months = last_n(period, 13)
    daily = _query(http, site_url, month_bounds(months[0])[0], month_bounds(period)[1], ["date"], 25000)
    per_month: dict[str, int] = defaultdict(int)
    for raw in daily:
        per_month[raw["keys"][0][:7]] += int(raw["clicks"])

    start, end = month_bounds(period)
    return GSCMonth(
        period=period,
        clicks=clicks,
        prev_clicks=prev_clicks,
        impressions=impressions,
        prev_impressions=prev_impressions,
        trend=[(m, per_month[m]) for m in months],
        top_queries=[_row(r) for r in _query(http, site_url, start, end, ["query"], 10)],
        top_pages=[_row(r) for r in _query(http, site_url, start, end, ["page"], 10)],
    )
```

- [ ] **Step 4: Run the test to see it pass**

Run: `uv run pytest tests/test_gsc.py -v`
Expected: 4 passed

- [ ] **Step 5: Commit**

```bash
git add clientreport/connectors/gsc.py tests/test_gsc.py
git commit -m "Add Search Console connector" -m "Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 6: Charts and the PDF report

About 40 minutes.

**Files:**
- Create: `clientreport/report/chart_style.py` (copied), `clientreport/report/chart_style.LICENSE` (copied), `clientreport/report/charts.py`, `clientreport/report/render.py`, `clientreport/report/templates/report.html`
- Test: `tests/test_report.py`

**Interfaces:**
- Consumes: `GA4Month`, `GSCMonth` (Task 3); `headlines`, `channel_title`, `trend_title`, `Headline` (Task 3); `label`, `short_label` (Task 2).
- Produces: `charts.channels_chart(channels, accent_hex, title, kicker) -> bytes | None`, `charts.trend_chart(trend, accent_hex, title, kicker) -> bytes | None` (None when there is nothing to draw); `render.Branding(agency_name, logo_path, accent_hex, footer)`, `render.Report(pdf: bytes, headlines: list[Headline], html: str)`, `render.render_report(client_name: str, period: str, ga4_month: GA4Month, gsc_month: GSCMonth, branding: Branding) -> Report`.

- [ ] **Step 1: Copy the chart style and its licence**

```bash
cp "/Users/taufiquekhan/gihub repo public/nopurple/skills/charts/scripts/chart_style.py" clientreport/report/chart_style.py
cp "/Users/taufiquekhan/gihub repo public/nopurple/LICENSE" clientreport/report/chart_style.LICENSE
```

Do not edit `chart_style.py`. `charts.py` adds the agency colour as a new theme at runtime.

- [ ] **Step 2: Write the failing test**

`tests/test_report.py`:

```python
from io import BytesIO

from fakes import FakeHttp
from pypdf import PdfReader

from clientreport.connectors import ga4, gsc
from clientreport.report import charts
from clientreport.report.render import Branding, render_report

BRANDING = Branding("Our Agency", "", "#1f7a4d", "Our Agency · hello@agency.com")


def sample(empty=False):
    http = FakeHttp(empty=empty)
    return ga4.fetch_month(http, "111", "2026-10"), gsc.fetch_month(http, "sc-domain:acme.com", "2026-10")


def test_charts_are_png():
    g, _ = sample()
    png = charts.channels_chart(g.channels, "#1f7a4d", "Title", "Kicker")
    assert png[:8] == b"\x89PNG\r\n\x1a\n"
    assert charts.trend_chart(g.trend, "#1f7a4d", "Title", "Kicker")[:4] == b"\x89PNG"


def test_charts_skip_empty_data():
    g, _ = sample(empty=True)
    assert charts.channels_chart(g.channels, "#1f7a4d", "t", "k") is None
    assert charts.trend_chart(g.trend, "#1f7a4d", "t", "k") is None


def test_report_has_three_pages_and_headlines():
    g, s = sample()
    report = render_report("Acme Ltd", "2026-10", g, s, BRANDING)
    reader = PdfReader(BytesIO(report.pdf))
    assert len(reader.pages) == 3
    first_page = reader.pages[0].extract_text()
    assert "Acme Ltd" in first_page
    assert "October 2026" in first_page
    assert "Sessions up 9%: 1,200" in first_page
    assert [h.sentence for h in report.headlines][2] == "Organic clicks up 25%: 500"
    assert "acme widgets" in reader.pages[2].extract_text()


def test_report_with_no_data_still_renders():
    g, s = sample(empty=True)
    report = render_report("Acme Ltd", "2026-10", g, s, BRANDING)
    assert len(PdfReader(BytesIO(report.pdf)).pages) == 3
    assert "No sessions recorded this month." in report.html
```

- [ ] **Step 3: Run the test to see it fail**

Run: `export DYLD_FALLBACK_LIBRARY_PATH=/opt/homebrew/lib && uv run pytest tests/test_report.py -v`
Expected: FAIL with `ImportError: cannot import name 'charts'`

- [ ] **Step 4: Implement the charts**

`clientreport/report/charts.py`:

```python
"""Report charts drawn with the nopurple chart style, returned as PNG bytes."""

from io import BytesIO

import matplotlib.pyplot as plt

from clientreport.months import short_label
from clientreport.report import chart_style as cs


def _theme(accent_hex: str) -> str:
    cs.THEMES["agency"] = {**cs.THEMES["swiss"], "accent": accent_hex}
    return "agency"


def _png(fig) -> bytes:
    buf = BytesIO()
    fig.savefig(buf, format="png", dpi=150)
    plt.close(fig)
    return buf.getvalue()


def channels_chart(channels: list[tuple[str, int]], accent_hex: str, title: str, kicker: str) -> bytes | None:
    if not channels or not any(n for _, n in channels):
        return None
    labels = [name for name, _ in channels]
    values = [n for _, n in channels]
    fig, _ = cs.hbar(labels, values, highlight=labels[0], theme=_theme(accent_hex), size="wide")
    cs.finish(fig, title=title, kicker=kicker)
    return _png(fig)


def trend_chart(trend: list[tuple[str, int]], accent_hex: str, title: str, kicker: str) -> bytes | None:
    values = [v for _, v in trend]
    if not any(values):
        return None
    x = [short_label(m) for m, _ in trend]
    fig, _ = cs.line(x, {"total": values}, theme=_theme(accent_hex), size="wide")
    cs.finish(fig, title=title, kicker=kicker)
    return _png(fig)
```

- [ ] **Step 5: Implement the PDF template**

`clientreport/report/templates/report.html`:

```html
<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<title>{{ client_name }} report, {{ month }}</title>
<style>
  @page {
    size: A4;
    margin: 16mm 15mm 18mm;
    @bottom-left { content: element(footer); }
    @bottom-right { content: counter(page) " / " counter(pages); font-size: 8pt; color: #6b6b6b; }
  }
  body { font-family: "Helvetica Neue", Helvetica, Arial, "DejaVu Sans", sans-serif; color: #0a0a0a; font-size: 10pt; margin: 0; }
  .footer { position: running(footer); font-size: 8pt; color: #6b6b6b; }
  .page { break-after: page; }
  .page:last-child { break-after: auto; }
  .brand { display: flex; align-items: center; justify-content: space-between; border-bottom: 2pt solid {{ branding.accent_hex }}; padding-bottom: 6pt; margin-bottom: 18pt; }
  .brand img { max-height: 28pt; max-width: 160pt; }
  .brand .agency { font-weight: 700; font-size: 11pt; }
  h1 { font-size: 26pt; margin: 0 0 4pt; }
  .month { color: #6b6b6b; font-size: 13pt; margin: 0 0 22pt; }
  h2 { font-size: 16pt; margin: 0 0 10pt; }
  h3 { font-size: 11pt; margin: 6pt 0 4pt; }
  .stat { border-left: 4pt solid {{ branding.accent_hex }}; padding: 6pt 0 6pt 12pt; margin-bottom: 14pt; }
  .stat .label { text-transform: uppercase; letter-spacing: 0.08em; font-size: 8.5pt; color: #6b6b6b; font-weight: 700; }
  .stat .value { font-size: 30pt; font-weight: 800; line-height: 1.1; }
  .stat .sentence { font-size: 12pt; }
  img.chart { width: 100%; margin-bottom: 10pt; }
  .empty { color: #6b6b6b; font-style: italic; margin: 8pt 0 16pt; }
  table { width: 100%; border-collapse: collapse; margin-bottom: 12pt; font-size: 8.5pt; }
  th { text-align: left; color: #6b6b6b; font-weight: 700; border-bottom: 1pt solid #dedede; padding: 3pt 4pt; }
  td { border-bottom: 0.5pt solid #eeeeee; padding: 3pt 4pt; }
  td.num, th.num { text-align: right; white-space: nowrap; }
  td.key { word-break: break-all; }
</style>
</head>
<body>
<div class="footer">{{ branding.footer or branding.agency_name }}</div>

<section class="page">
  <div class="brand">
    {% if logo_uri %}<img src="{{ logo_uri }}" alt="">{% endif %}
    <span class="agency">{{ branding.agency_name }}</span>
  </div>
  <h1>{{ client_name }}</h1>
  <p class="month">Monthly report, {{ month }}</p>
  {% for h in headlines %}
  <div class="stat">
    <div class="label">{{ h.label }}</div>
    <div class="value">{{ "{:,}".format(h.value) }}</div>
    <div class="sentence">{{ h.sentence }}</div>
  </div>
  {% endfor %}
</section>

<section class="page">
  <h2>Website traffic</h2>
  {% if channels_png %}<img class="chart" src="{{ channels_png }}" alt="">{% else %}<p class="empty">No sessions recorded this month.</p>{% endif %}
  {% if sessions_png %}<img class="chart" src="{{ sessions_png }}" alt="">{% else %}<p class="empty">No sessions recorded in the last 13 months.</p>{% endif %}
</section>

<section class="page">
  <h2>Google search</h2>
  {% if clicks_png %}<img class="chart" src="{{ clicks_png }}" alt="">{% else %}<p class="empty">No search clicks recorded in the last 13 months.</p>{% endif %}
  {% for title, column, rows in [("Top search queries", "Query", gsc.top_queries), ("Top pages", "Page", gsc.top_pages)] %}
  <h3>{{ title }}</h3>
  {% if rows %}
  <table>
    <tr><th>{{ column }}</th><th class="num">Clicks</th><th class="num">Impressions</th><th class="num">CTR</th><th class="num">Avg. position</th></tr>
    {% for r in rows %}
    <tr>
      <td class="key">{{ r.key }}</td>
      <td class="num">{{ "{:,}".format(r.clicks) }}</td>
      <td class="num">{{ "{:,}".format(r.impressions) }}</td>
      <td class="num">{{ "%.1f"|format(r.ctr * 100) }}%</td>
      <td class="num">{{ "%.1f"|format(r.position) }}</td>
    </tr>
    {% endfor %}
  </table>
  {% else %}<p class="empty">No data this month.</p>{% endif %}
  {% endfor %}
</section>
</body>
</html>
```

- [ ] **Step 6: Implement the renderer**

`clientreport/report/render.py`:

```python
"""Turn one month of data into the branded PDF report."""

import base64
from dataclasses import dataclass
from pathlib import Path

from jinja2 import Environment, PackageLoader, select_autoescape
from weasyprint import HTML

from clientreport.connectors.base import GA4Month, GSCMonth
from clientreport.months import label
from clientreport.report import charts
from clientreport.report.metrics import Headline, channel_title, headlines, trend_title

_env = Environment(loader=PackageLoader("clientreport.report", "templates"), autoescape=select_autoescape())


@dataclass(frozen=True)
class Branding:
    agency_name: str
    logo_path: str
    accent_hex: str
    footer: str


@dataclass(frozen=True)
class Report:
    pdf: bytes
    headlines: list[Headline]
    html: str


def _data_uri(png: bytes | None) -> str | None:
    if png is None:
        return None
    return "data:image/png;base64," + base64.b64encode(png).decode()


def _logo_uri(path: str) -> str | None:
    if path and Path(path).exists():
        return Path(path).resolve().as_uri()
    return None


def render_report(client_name: str, period: str, ga4_month: GA4Month, gsc_month: GSCMonth,
                  branding: Branding) -> Report:
    month = label(period)
    accent = branding.accent_hex
    heads = headlines(ga4_month, gsc_month)
    html = _env.get_template("report.html").render(
        client_name=client_name,
        month=month,
        branding=branding,
        logo_uri=_logo_uri(branding.logo_path),
        headlines=heads,
        channels_png=_data_uri(charts.channels_chart(
            ga4_month.channels, accent, channel_title(ga4_month.channels), f"Sessions by channel, {month}")),
        sessions_png=_data_uri(charts.trend_chart(
            ga4_month.trend, accent, trend_title("Sessions", ga4_month.trend), "Sessions per month, last 13 months")),
        clicks_png=_data_uri(charts.trend_chart(
            gsc_month.trend, accent, trend_title("Organic clicks", gsc_month.trend),
            "Google search clicks per month, last 13 months")),
        gsc=gsc_month,
    )
    return Report(pdf=HTML(string=html).write_pdf(), headlines=heads, html=html)
```

- [ ] **Step 7: Run the test to see it pass**

Run: `uv run pytest tests/test_report.py -v`
Expected: 4 passed. If WeasyPrint fails with `cannot load library 'libgobject-2.0-0'`, the `DYLD_FALLBACK_LIBRARY_PATH` export from Step 3 is missing in this terminal.

- [ ] **Step 8: Look at the PDF**

```bash
mkdir -p data
uv run python -c "
import sys; sys.path.insert(0, 'tests')
from fakes import FakeHttp
from clientreport.connectors import ga4, gsc
from clientreport.report.render import Branding, render_report
h = FakeHttp()
r = render_report('Acme Ltd', '2026-10', ga4.fetch_month(h, '111', '2026-10'), gsc.fetch_month(h, 'sc-domain:acme.com', '2026-10'), Branding('Our Agency', '', '#1f7a4d', 'Our Agency'))
open('data/sample-report.pdf', 'wb').write(r.pdf)
"
open data/sample-report.pdf
```

Check: 3 pages, nothing cut off, charts use the green accent, the page 2 and page 3 chart titles are sentences with numbers. (`data/` is git-ignored.)

- [ ] **Step 9: Commit**

```bash
git add clientreport/report tests/test_report.py
git commit -m "Add nopurple charts and PDF report" -m "Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 7: Email sending

About 15 minutes.

**Files:**
- Create: `clientreport/mail.py`
- Test: `tests/test_mail.py`

**Interfaces:**
- Produces: `SmtpConfig(host, port, user, password, from_email)`, `build_message(*, from_email, to: list[str], subject, text, html=None, reply_to=None, attachment: bytes | None = None, filename="report.pdf") -> EmailMessage`, `send(message, config: SmtpConfig, smtp_factory=None) -> None` (raises `OSError` when `host` is empty; SMTP errors are `OSError` subclasses).

- [ ] **Step 1: Write the failing test**

`tests/test_mail.py`:

```python
import pytest

from clientreport.mail import SmtpConfig, build_message, send


def test_build_message_with_pdf():
    msg = build_message(
        from_email="reports@agency.com", to=["a@x.com", "b@x.com"], subject="S", text="hello",
        html="<p>hello</p>", reply_to="me@agency.com", attachment=b"%PDF-1.7 fake", filename="acme-2026-10.pdf",
    )
    assert msg["To"] == "a@x.com, b@x.com"
    assert msg["Reply-To"] == "me@agency.com"
    assert msg.get_body(("plain",)).get_content().strip() == "hello"
    attachment = next(msg.iter_attachments())
    assert attachment.get_filename() == "acme-2026-10.pdf"
    assert attachment.get_content() == b"%PDF-1.7 fake"


class FakeSMTP:
    instances: list["FakeSMTP"] = []

    def __init__(self, host, port, timeout):
        self.host, self.port, self.calls = host, port, []
        FakeSMTP.instances.append(self)

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def starttls(self):
        self.calls.append("starttls")

    def login(self, user, password):
        self.calls.append(("login", user))

    def send_message(self, msg):
        self.calls.append(("send", msg["Subject"]))


def test_send_uses_starttls_and_login():
    FakeSMTP.instances.clear()
    msg = build_message(from_email="r@a.com", to=["x@y.com"], subject="Hi", text="t")
    send(msg, SmtpConfig("smtp.example.com", 587, "user", "pw", "r@a.com"), smtp_factory=FakeSMTP)
    assert FakeSMTP.instances[0].calls == ["starttls", ("login", "user"), ("send", "Hi")]


def test_send_without_host_fails():
    msg = build_message(from_email="r@a.com", to=["x@y.com"], subject="Hi", text="t")
    with pytest.raises(OSError, match="SMTP_HOST"):
        send(msg, SmtpConfig("", 587, "", "", "r@a.com"))
```

- [ ] **Step 2: Run the test to see it fail**

Run: `uv run pytest tests/test_mail.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'clientreport.mail'`

- [ ] **Step 3: Implement**

`clientreport/mail.py`:

```python
"""Build and send emails over SMTP."""

import smtplib
from dataclasses import dataclass
from email.message import EmailMessage


@dataclass(frozen=True)
class SmtpConfig:
    host: str
    port: int
    user: str
    password: str
    from_email: str


def build_message(*, from_email: str, to: list[str], subject: str, text: str, html: str | None = None,
                  reply_to: str | None = None, attachment: bytes | None = None,
                  filename: str = "report.pdf") -> EmailMessage:
    msg = EmailMessage()
    msg["From"] = from_email
    msg["To"] = ", ".join(to)
    msg["Subject"] = subject
    if reply_to:
        msg["Reply-To"] = reply_to
    msg.set_content(text)
    if html:
        msg.add_alternative(html, subtype="html")
    if attachment is not None:
        msg.add_attachment(attachment, maintype="application", subtype="pdf", filename=filename)
    return msg


def send(message: EmailMessage, config: SmtpConfig, smtp_factory=None) -> None:
    if not config.host:
        raise OSError("SMTP_HOST is not set, so email cannot be sent")
    factory = smtp_factory or (smtplib.SMTP_SSL if config.port == 465 else smtplib.SMTP)
    with factory(config.host, config.port, timeout=30) as smtp:
        if config.port == 587:
            smtp.starttls()
        if config.user:
            smtp.login(config.user, config.password)
        smtp.send_message(message)
```

- [ ] **Step 4: Run the test to see it pass**

Run: `uv run pytest tests/test_mail.py -v`
Expected: 3 passed

- [ ] **Step 5: Commit**

```bash
git add clientreport/mail.py tests/test_mail.py
git commit -m "Add SMTP email sending" -m "Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 8: Report pipeline (fetch, render, send, record)

About 35 minutes.

**Files:**
- Create: `clientreport/pipeline.py`
- Test: `tests/test_pipeline.py`

**Interfaces:**
- Consumes: `ga4.fetch_month`, `gsc.fetch_month`, `SourceError`, `GoogleAuthError`, `Http` (Tasks 3–5); `Client`, `ReportRun`, `get_agency`, `get_google` (Task 2); `render_report`, `Branding`, `Report` (Task 6); `build_message` (Task 7).
- Produces: `Deps(data_dir: Path, from_email: str, http_factory: Callable[[Session], Http], send_mail: Callable[[EmailMessage], None], today: Callable[[], date])`, `MAX_ATTEMPTS = 3`, `build_report(session, client, period, deps) -> Report`, `send_report(session, client, period, deps, manual: bool = False) -> ReportRun`, `client_email(...) -> EmailMessage`, `alert_email(...) -> EmailMessage`, `mark_google_broken(session, error: str) -> None`.

Rules `send_report` must follow:
- A `sent` run is returned untouched unless `manual=True`.
- `manual=True` resets `attempts` to 0 before trying.
- Any `SourceError` or `OSError` (SMTP errors included) → attempts +1, `held`, or `failed` at 3 attempts. At `failed`, email the owner if `owner_email` is set.
- A `GoogleAuthError` also marks the Google connection `broken` (only if it is currently `ok`).
- On success: save the PDF to `data_dir/reports/client-<id>-<period>.pdf`, set `sent`, `sent_at`, `pdf_path`, clear `last_error`.

- [ ] **Step 1: Write the failing test**

`tests/test_pipeline.py`:

```python
import smtplib
from datetime import date
from pathlib import Path

import pytest
from fakes import FakeHttp, FakeMailer
from sqlmodel import Session

from clientreport.connectors.base import GoogleAuthError, SourceError
from clientreport.db import Client, GoogleConnection, get_agency, get_google, make_engine
from clientreport.pipeline import Deps, send_report


@pytest.fixture
def env(tmp_path):
    engine = make_engine(tmp_path)
    session = Session(engine, expire_on_commit=False)
    agency = get_agency(session)
    agency.name = "Our Agency"
    agency.owner_email = "owner@agency.com"
    agency.reply_to = "hello@agency.com"
    session.add(agency)
    client = Client(name="Acme Ltd", recipients="ceo@acme.com", ga4_property_id="111",
                    gsc_site_url="sc-domain:acme.com", enabled=True)
    session.add(client)
    session.commit()
    session.refresh(client)
    yield session, client, tmp_path
    session.close()


def deps(tmp_path, http, mailer):
    return Deps(data_dir=tmp_path, from_email="reports@agency.com", http_factory=lambda s: http,
                send_mail=mailer, today=lambda: date(2026, 11, 4))


def test_sends_email_with_pdf(env):
    session, client, tmp = env
    mailer = FakeMailer()
    run = send_report(session, client, "2026-10", deps(tmp, FakeHttp(), mailer))
    assert run.status == "sent"
    assert run.sent_at is not None
    assert Path(run.pdf_path).read_bytes()[:4] == b"%PDF"
    msg = mailer.sent[0]
    assert msg["Subject"] == "Acme Ltd report for October 2026"
    assert msg["To"] == "ceo@acme.com"
    assert msg["Reply-To"] == "hello@agency.com"
    assert "Organic clicks up 25%: 500" in msg.get_body(("plain",)).get_content()
    assert next(msg.iter_attachments()).get_filename() == "acme-ltd-2026-10.pdf"


def test_same_month_is_not_sent_twice(env):
    session, client, tmp = env
    mailer = FakeMailer()
    d = deps(tmp, FakeHttp(), mailer)
    send_report(session, client, "2026-10", d)
    send_report(session, client, "2026-10", d)
    assert len(mailer.sent) == 1


def test_manual_send_sends_again(env):
    session, client, tmp = env
    mailer = FakeMailer()
    d = deps(tmp, FakeHttp(), mailer)
    send_report(session, client, "2026-10", d)
    send_report(session, client, "2026-10", d, manual=True)
    assert len(mailer.sent) == 2


def test_failing_source_holds_then_fails_and_alerts_owner(env):
    session, client, tmp = env
    mailer = FakeMailer()
    d = deps(tmp, FakeHttp(fail=SourceError("GSC 500"), fail_on="searchAnalytics"), mailer)
    statuses = [send_report(session, client, "2026-10", d).status for _ in range(3)]
    assert statuses == ["held", "held", "failed"]
    assert [m["To"] for m in mailer.sent] == ["owner@agency.com"]
    assert "GSC 500" in mailer.sent[0].get_body(("plain",)).get_content()


def test_auth_error_marks_google_broken(env):
    session, client, tmp = env
    session.add(GoogleConnection(email="me@agency.com", refresh_token_enc="x", status="ok"))
    session.commit()
    run = send_report(session, client, "2026-10", deps(tmp, FakeHttp(fail=GoogleAuthError("invalid_grant")), FakeMailer()))
    assert run.status == "held"
    assert get_google(session).status == "broken"
    assert "invalid_grant" in get_google(session).last_error


def test_smtp_failure_holds(env):
    session, client, tmp = env
    run = send_report(session, client, "2026-10",
                      deps(tmp, FakeHttp(), FakeMailer(fail=smtplib.SMTPException("server down"))))
    assert run.status == "held"
    assert run.attempts == 1
    assert "server down" in run.last_error
```

- [ ] **Step 2: Run the test to see it fail**

Run: `uv run pytest tests/test_pipeline.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'clientreport.pipeline'`

- [ ] **Step 3: Implement**

`clientreport/pipeline.py`:

```python
"""Fetch a client's data, build the PDF, email it and record the run."""

import html as html_lib
import re
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, date, datetime
from email.message import EmailMessage
from pathlib import Path

from sqlmodel import Session, select

from clientreport.connectors import ga4, gsc
from clientreport.connectors.base import GoogleAuthError, Http, SourceError
from clientreport.db import AgencySettings, Client, ReportRun, get_agency, get_google
from clientreport.mail import build_message
from clientreport.months import label
from clientreport.report.metrics import Headline
from clientreport.report.render import Branding, Report, render_report

MAX_ATTEMPTS = 3


@dataclass
class Deps:
    data_dir: Path
    from_email: str
    http_factory: Callable[[Session], Http]
    send_mail: Callable[[EmailMessage], None]
    today: Callable[[], date]


def build_report(session: Session, client: Client, period: str, deps: Deps) -> Report:
    http = deps.http_factory(session)
    ga4_month = ga4.fetch_month(http, client.ga4_property_id, period)
    gsc_month = gsc.fetch_month(http, client.gsc_site_url, period)
    agency = get_agency(session)
    branding = Branding(agency.name, agency.logo_path, agency.accent_hex, agency.footer)
    return render_report(client.name, period, ga4_month, gsc_month, branding)


def _slug(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-") or "report"


def client_email(agency: AgencySettings, client: Client, period: str, heads: list[Headline],
                 pdf: bytes, from_email: str) -> EmailMessage:
    month = label(period)
    sign_off = agency.footer or agency.name
    lines = "\n".join(f"- {h.sentence}" for h in heads)
    text = (f"Hi,\n\nHere are your results for {month}:\n\n{lines}\n\n"
            f"The full report is attached as a PDF.\n\n{sign_off}\n")
    items = "".join(f"<li>{html_lib.escape(h.sentence)}</li>" for h in heads)
    html = (f"<p>Hi,</p><p>Here are your results for {month}:</p><ul>{items}</ul>"
            f"<p>The full report is attached as a PDF.</p><p>{html_lib.escape(sign_off)}</p>")
    return build_message(
        from_email=from_email, to=client.recipient_list(), subject=f"{client.name} report for {month}",
        text=text, html=html, reply_to=agency.reply_to or None, attachment=pdf,
        filename=f"{_slug(client.name)}-{period}.pdf",
    )


def alert_email(agency: AgencySettings, client: Client, run: ReportRun, from_email: str) -> EmailMessage:
    month = label(run.period)
    text = (f"The {month} report for {client.name} failed {run.attempts} times and was not sent.\n\n"
            f"Last error:\n{run.last_error}\n\n"
            "Fix the problem, then click Send now on the dashboard.\n")
    return build_message(from_email=from_email, to=[agency.owner_email],
                         subject=f"Report failed: {client.name}, {month}", text=text)


def mark_google_broken(session: Session, error: str) -> None:
    conn = get_google(session)
    if conn is not None and conn.refresh_token_enc and conn.status == "ok":
        conn.status = "broken"
        conn.last_error = error
        session.add(conn)
        session.commit()


def _get_run(session: Session, client_id: int, period: str) -> ReportRun:
    run = session.exec(
        select(ReportRun).where(ReportRun.client_id == client_id, ReportRun.period == period)
    ).first()
    if run is None:
        run = ReportRun(client_id=client_id, period=period)
        session.add(run)
        session.commit()
        session.refresh(run)
    return run


def send_report(session: Session, client: Client, period: str, deps: Deps, manual: bool = False) -> ReportRun:
    run = _get_run(session, client.id, period)
    if run.status == "sent" and not manual:
        return run
    if manual:
        run.attempts = 0

    try:
        report = build_report(session, client, period, deps)
        pdf_path = deps.data_dir / "reports" / f"client-{client.id}-{period}.pdf"
        pdf_path.parent.mkdir(parents=True, exist_ok=True)
        pdf_path.write_bytes(report.pdf)
        deps.send_mail(client_email(get_agency(session), client, period, report.headlines, report.pdf,
                                    deps.from_email))
    except (SourceError, OSError) as exc:  # SMTP errors are OSErrors
        if isinstance(exc, GoogleAuthError):
            mark_google_broken(session, str(exc))
        run.attempts += 1
        run.last_error = str(exc) or exc.__class__.__name__
        run.status = "failed" if run.attempts >= MAX_ATTEMPTS else "held"
        session.add(run)
        session.commit()
        agency = get_agency(session)
        if run.status == "failed" and agency.owner_email:
            try:
                deps.send_mail(alert_email(agency, client, run, deps.from_email))
            except OSError:
                pass  # the dashboard still shows the failure
        return run

    run.status = "sent"
    run.sent_at = datetime.now(UTC)
    run.pdf_path = str(pdf_path)
    run.last_error = ""
    session.add(run)
    session.commit()
    return run
```

- [ ] **Step 4: Run the test to see it pass**

Run: `uv run pytest tests/test_pipeline.py -v`
Expected: 6 passed

- [ ] **Step 5: Commit**

```bash
git add clientreport/pipeline.py tests/test_pipeline.py
git commit -m "Add report pipeline with hold, retry and owner alerts" -m "Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 9: Daily scheduler

About 20 minutes.

**Files:**
- Create: `clientreport/scheduler.py`
- Test: `tests/test_scheduler.py`

**Interfaces:**
- Consumes: `Client`, `ReportRun` (Task 2); `previous_month` (Task 2); `Deps`, `send_report` (Task 8).
- Produces: `due_clients(session, today: date) -> list[tuple[Client, str]]`, `run_due_reports(engine, deps) -> list[tuple[int, str, str]]` (client_id, period, status), `start_scheduler(engine, deps, tz: str) -> BackgroundScheduler`.

A client is due when all of these hold: `enabled`; `today.day >= send_day`; `enabled_at` is set and is on or before this month's send date; last month's run is missing, `pending` or `held`.

- [ ] **Step 1: Write the failing test**

`tests/test_scheduler.py`:

```python
from datetime import date

from fakes import FakeHttp, FakeMailer
from sqlmodel import Session

from clientreport.db import Client, ReportRun, make_engine
from clientreport.pipeline import Deps
from clientreport.scheduler import due_clients, run_due_reports


def add_client(session, **overrides):
    values = dict(name="Acme Ltd", recipients="ceo@acme.com", ga4_property_id="111",
                  gsc_site_url="sc-domain:acme.com", enabled=True, send_day=4, enabled_at=date(2026, 10, 1))
    values.update(overrides)
    client = Client(**values)
    session.add(client)
    session.commit()
    session.refresh(client)
    return client


def test_not_due_before_send_day(tmp_path):
    with Session(make_engine(tmp_path)) as session:
        add_client(session)
        assert due_clients(session, date(2026, 11, 3)) == []


def test_due_on_send_day(tmp_path):
    with Session(make_engine(tmp_path)) as session:
        client = add_client(session)
        assert due_clients(session, date(2026, 11, 4)) == [(client, "2026-10")]


def test_switched_on_after_send_day_waits_for_next_month(tmp_path):
    with Session(make_engine(tmp_path)) as session:
        add_client(session, enabled_at=date(2026, 11, 10))
        assert due_clients(session, date(2026, 11, 12)) == []


def test_disabled_client_is_not_due(tmp_path):
    with Session(make_engine(tmp_path)) as session:
        add_client(session, enabled=False)
        assert due_clients(session, date(2026, 11, 4)) == []


def test_held_is_retried_but_failed_and_sent_are_not(tmp_path):
    with Session(make_engine(tmp_path)) as session:
        held = add_client(session, name="Held")
        failed = add_client(session, name="Failed")
        sent = add_client(session, name="Sent")
        for client, status in [(held, "held"), (failed, "failed"), (sent, "sent")]:
            session.add(ReportRun(client_id=client.id, period="2026-10", status=status))
        session.commit()
        assert [c.name for c, _ in due_clients(session, date(2026, 11, 5))] == ["Held"]


def test_running_twice_sends_once(tmp_path):
    engine = make_engine(tmp_path)
    with Session(engine) as session:
        add_client(session)
    mailer = FakeMailer()
    deps = Deps(data_dir=tmp_path, from_email="reports@agency.com", http_factory=lambda s: FakeHttp(),
                send_mail=mailer, today=lambda: date(2026, 11, 4))
    assert run_due_reports(engine, deps) == [(1, "2026-10", "sent")]
    assert run_due_reports(engine, deps) == []
    assert len(mailer.sent) == 1
```

- [ ] **Step 2: Run the test to see it fail**

Run: `uv run pytest tests/test_scheduler.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'clientreport.scheduler'`

- [ ] **Step 3: Implement**

`clientreport/scheduler.py`:

```python
"""Daily job that sends every report that is due."""

from datetime import date

from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger
from sqlmodel import Session, select

from clientreport.db import Client, ReportRun
from clientreport.months import previous_month
from clientreport.pipeline import Deps, send_report

DONE = ("sent", "failed")


def due_clients(session: Session, today: date) -> list[tuple[Client, str]]:
    period = previous_month(today)
    due = []
    for client in session.exec(select(Client).where(Client.enabled == True).order_by(Client.id)).all():  # noqa: E712
        if today.day < client.send_day:
            continue
        send_date = date(today.year, today.month, client.send_day)
        if client.enabled_at is None or client.enabled_at > send_date:
            continue
        run = session.exec(
            select(ReportRun).where(ReportRun.client_id == client.id, ReportRun.period == period)
        ).first()
        if run is not None and run.status in DONE:
            continue
        due.append((client, period))
    return due


def run_due_reports(engine, deps: Deps) -> list[tuple[int, str, str]]:
    results = []
    with Session(engine, expire_on_commit=False) as session:
        for client, period in due_clients(session, deps.today()):
            run = send_report(session, client, period, deps)
            results.append((client.id, period, run.status))
    return results


def start_scheduler(engine, deps: Deps, tz: str) -> BackgroundScheduler:
    scheduler = BackgroundScheduler(timezone=tz)
    scheduler.add_job(
        run_due_reports, CronTrigger(hour=7, minute=0, timezone=tz), args=[engine, deps],
        id="due-reports", replace_existing=True, coalesce=True, misfire_grace_time=6 * 3600,
    )
    scheduler.start()
    return scheduler
```

- [ ] **Step 4: Run the test to see it pass**

Run: `uv run pytest tests/test_scheduler.py -v`
Expected: 6 passed

- [ ] **Step 5: Commit**

```bash
git add clientreport/scheduler.py tests/test_scheduler.py
git commit -m "Add daily scheduler for due reports" -m "Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 10: Google sign-in and API client

About 30 minutes.

**Files:**
- Create: `clientreport/google_oauth.py`
- Test: `tests/test_google_oauth.py`

**Interfaces:**
- Consumes: `Settings` (Task 1), `crypto` (Task 1), `GoogleConnection`, `get_google` (Task 2), `SourceError`, `GoogleAuthError` (Task 3).
- Produces: `SCOPES`, `GoogleHttp(session)` implementing `Http`, `redirect_uri(settings) -> str`, `start_flow(settings) -> tuple[str, str, str]` (url, state, code_verifier), `finish_flow(settings, state, code_verifier, authorization_response) -> tuple[str, str, str]` (email, refresh_token, scopes), `save_connection(session, settings, email, refresh_token, scopes) -> GoogleConnection`, `make_http(session, settings) -> GoogleHttp`.

`finish_flow` talks to Google, so it is not unit-tested. Task 12 replaces it with a stub, and Task 14 checks it for real.

- [ ] **Step 1: Write the failing test**

`tests/test_google_oauth.py`:

```python
from urllib.parse import parse_qs, urlparse

import pytest
from cryptography.fernet import Fernet
from google.auth.exceptions import RefreshError
from sqlmodel import Session

from clientreport import crypto
from clientreport.config import load_settings
from clientreport.connectors.base import GoogleAuthError, SourceError
from clientreport.db import GoogleConnection, get_google, make_engine
from clientreport.google_oauth import GoogleHttp, make_http, save_connection, start_flow


@pytest.fixture
def settings(tmp_path):
    return load_settings({
        "BASE_URL": "http://localhost:8000", "ADMIN_PASSWORD": "pw",
        "APP_SECRET_KEY": Fernet.generate_key().decode(),
        "GOOGLE_CLIENT_ID": "cid.apps.googleusercontent.com", "GOOGLE_CLIENT_SECRET": "secret",
        "DATA_DIR": str(tmp_path),
    })


class Resp:
    def __init__(self, status_code, payload=None, text=""):
        self.status_code, self._payload, self.text = status_code, payload or {}, text

    def json(self):
        return self._payload


class Sess:
    def __init__(self, resp=None, error=None):
        self.resp, self.error = resp, error

    def get(self, url, timeout):
        if self.error:
            raise self.error
        return self.resp

    def post(self, url, timeout, json):
        return self.get(url, timeout)


def test_google_http_returns_json():
    assert GoogleHttp(Sess(Resp(200, {"ok": 1}))).post("https://x", {}) == {"ok": 1}


def test_google_http_maps_api_errors():
    with pytest.raises(SourceError, match="403"):
        GoogleHttp(Sess(Resp(403, text="denied"))).get("https://x")


def test_google_http_maps_refresh_errors():
    with pytest.raises(GoogleAuthError, match="invalid_grant"):
        GoogleHttp(Sess(error=RefreshError("invalid_grant"))).get("https://x")


def test_start_flow_asks_for_offline_access(settings):
    url, state, verifier = start_flow(settings)
    query = parse_qs(urlparse(url).query)
    assert url.startswith("https://accounts.google.com/")
    assert query["access_type"] == ["offline"]
    assert query["prompt"] == ["consent"]
    assert query["redirect_uri"] == ["http://localhost:8000/auth/google/callback"]
    assert "code_challenge" in query
    assert state and verifier


def test_save_connection_encrypts_token(settings):
    with Session(make_engine(settings.data_dir)) as session:
        conn = save_connection(session, settings, "me@agency.com", "refresh-1", "openid")
        assert conn.refresh_token_enc != "refresh-1"
        assert crypto.decrypt(settings.app_secret_key, conn.refresh_token_enc) == "refresh-1"
        assert get_google(session).status == "ok"


def test_make_http_needs_a_connection(settings):
    with Session(make_engine(settings.data_dir)) as session:
        with pytest.raises(GoogleAuthError, match="not connected"):
            make_http(session, settings)


def test_make_http_refuses_broken_connection(settings):
    with Session(make_engine(settings.data_dir)) as session:
        session.add(GoogleConnection(email="me@agency.com", refresh_token_enc="x", status="broken",
                                     last_error="invalid_grant"))
        session.commit()
        with pytest.raises(GoogleAuthError, match="stopped working"):
            make_http(session, settings)


def test_make_http_builds_client(settings):
    with Session(make_engine(settings.data_dir)) as session:
        save_connection(session, settings, "me@agency.com", "refresh-1", "openid")
        assert isinstance(make_http(session, settings), GoogleHttp)
```

- [ ] **Step 2: Run the test to see it fail**

Run: `uv run pytest tests/test_google_oauth.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'clientreport.google_oauth'`

- [ ] **Step 3: Implement**

`clientreport/google_oauth.py`:

```python
"""Google sign-in: OAuth flow, encrypted token storage and an HTTP client for the APIs."""

import os

import requests
from google.auth.exceptions import RefreshError, TransportError
from google.auth.transport.requests import AuthorizedSession
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import Flow
from sqlmodel import Session

from clientreport import crypto
from clientreport.config import Settings
from clientreport.connectors.base import GoogleAuthError, SourceError
from clientreport.db import GoogleConnection, get_google

SCOPES = [
    "openid",
    "https://www.googleapis.com/auth/userinfo.email",
    "https://www.googleapis.com/auth/analytics.readonly",
    "https://www.googleapis.com/auth/webmasters.readonly",
]
AUTH_URI = "https://accounts.google.com/o/oauth2/auth"
TOKEN_URI = "https://oauth2.googleapis.com/token"
USERINFO_URL = "https://openidconnect.googleapis.com/v1/userinfo"


class GoogleHttp:
    """JSON client for Google APIs. Turns failures into SourceError or GoogleAuthError."""

    def __init__(self, session):
        self._session = session

    def get(self, url: str) -> dict:
        return self._call("get", url)

    def post(self, url: str, body: dict) -> dict:
        return self._call("post", url, json=body)

    def _call(self, method: str, url: str, **kwargs) -> dict:
        try:
            resp = getattr(self._session, method)(url, timeout=60, **kwargs)
        except RefreshError as exc:
            raise GoogleAuthError(f"Google refused the saved sign-in: {exc}") from exc
        except (TransportError, requests.RequestException) as exc:
            raise SourceError(f"Could not reach Google: {exc}") from exc
        if resp.status_code >= 400:
            raise SourceError(f"Google API error {resp.status_code} for {url}: {resp.text[:300]}")
        return resp.json()


def redirect_uri(settings: Settings) -> str:
    return f"{settings.base_url}/auth/google/callback"


def _flow(settings: Settings, **kwargs) -> Flow:
    config = {"web": {
        "client_id": settings.google_client_id,
        "client_secret": settings.google_client_secret,
        "auth_uri": AUTH_URI,
        "token_uri": TOKEN_URI,
        "redirect_uris": [redirect_uri(settings)],
    }}
    return Flow.from_client_config(config, scopes=SCOPES, redirect_uri=redirect_uri(settings), **kwargs)


def start_flow(settings: Settings) -> tuple[str, str, str]:
    flow = _flow(settings, autogenerate_code_verifier=True)
    url, state = flow.authorization_url(access_type="offline", prompt="consent", include_granted_scopes="true")
    return url, state, flow.code_verifier


def finish_flow(settings: Settings, state: str, code_verifier: str,
                authorization_response: str) -> tuple[str, str, str]:
    if settings.base_url.startswith("http://"):
        os.environ["OAUTHLIB_INSECURE_TRANSPORT"] = "1"  # local installs without HTTPS
    os.environ["OAUTHLIB_RELAX_TOKEN_SCOPE"] = "1"  # Google may list granted scopes differently
    flow = _flow(settings, state=state, code_verifier=code_verifier)
    flow.fetch_token(authorization_response=authorization_response)
    creds = flow.credentials
    email = AuthorizedSession(creds).get(USERINFO_URL, timeout=30).json().get("email", "")
    return email, creds.refresh_token or "", " ".join(creds.scopes or SCOPES)


def save_connection(session: Session, settings: Settings, email: str, refresh_token: str,
                    scopes: str) -> GoogleConnection:
    conn = get_google(session) or GoogleConnection()
    conn.email = email
    conn.refresh_token_enc = crypto.encrypt(settings.app_secret_key, refresh_token)
    conn.scopes = scopes
    conn.status = "ok"
    conn.last_error = ""
    session.add(conn)
    session.commit()
    session.refresh(conn)
    return conn


def make_http(session: Session, settings: Settings) -> GoogleHttp:
    conn = get_google(session)
    if conn is None or not conn.refresh_token_enc:
        raise GoogleAuthError("Google is not connected. Open Settings and click Connect Google.")
    if conn.status == "broken":
        raise GoogleAuthError(
            f"Google connection stopped working ({conn.last_error}). Open Settings and click Connect Google.")
    creds = Credentials(
        token=None,
        refresh_token=crypto.decrypt(settings.app_secret_key, conn.refresh_token_enc),
        token_uri=TOKEN_URI,
        client_id=settings.google_client_id,
        client_secret=settings.google_client_secret,
        scopes=conn.scopes.split(),
    )
    return GoogleHttp(AuthorizedSession(creds))
```

- [ ] **Step 4: Run the test to see it pass**

Run: `uv run pytest tests/test_google_oauth.py -v`
Expected: 8 passed. If `test_start_flow_asks_for_offline_access` fails because `code_challenge` is missing, the installed google-auth-oauthlib ignores `autogenerate_code_verifier`; upgrade it with `uv add "google-auth-oauthlib>=1.2"` and rerun.

- [ ] **Step 5: Commit**

```bash
git add clientreport/google_oauth.py tests/test_google_oauth.py
git commit -m "Add Google sign-in and API client" -m "Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 11: Web app shell, login and dashboard

About 35 minutes.

**Files:**
- Create: `clientreport/app.py`, `clientreport/routes/__init__.py` (empty), `clientreport/routes/common.py`, `clientreport/routes/auth.py`, `clientreport/routes/pages.py` (dashboard only in this task), `clientreport/templates/base.html`, `clientreport/templates/login.html`, `clientreport/templates/dashboard.html`, `clientreport/templates/error.html`
- Create: `tests/conftest.py`
- Test: `tests/test_web_dashboard.py`

**Interfaces:**
- Consumes: `Settings`, `load_settings` (Task 1); `make_engine`, tables, `get_google` (Task 2); `Deps` (Task 8); `start_scheduler` (Task 9); `make_http` (Task 10); `SmtpConfig`, `send` (Task 7).
- Produces: `create_app(settings: Settings, deps: Deps | None = None, start_scheduler: bool = True) -> FastAPI` with `app.state.settings`, `app.state.engine`, `app.state.deps`; `default_deps(settings) -> Deps`; `routes.common.templates`, `db(request) -> Iterator[Session]`, `require_login(request)`, `render(request, name, status_code=200, **context)`, `error_page(request, message, status_code=400)`; `routes.pages.router` (Task 12 adds settings routes to it).
- Produces (tests): fixtures `settings`, `http`, `mailer`, `app`, `web` (logged-in `TestClient`).

- [ ] **Step 1: Add the shared test fixtures**

`tests/conftest.py`:

```python
from datetime import date

import pytest
from cryptography.fernet import Fernet
from fakes import FakeHttp, FakeMailer
from fastapi.testclient import TestClient

from clientreport.app import create_app
from clientreport.config import load_settings
from clientreport.pipeline import Deps


@pytest.fixture
def settings(tmp_path):
    return load_settings({
        "BASE_URL": "http://localhost:8000", "ADMIN_PASSWORD": "pw",
        "APP_SECRET_KEY": Fernet.generate_key().decode(),
        "GOOGLE_CLIENT_ID": "cid.apps.googleusercontent.com", "GOOGLE_CLIENT_SECRET": "secret",
        "DATA_DIR": str(tmp_path),
    })


@pytest.fixture
def http():
    return FakeHttp()


@pytest.fixture
def mailer():
    return FakeMailer()


@pytest.fixture
def app(settings, http, mailer):
    deps = Deps(data_dir=settings.data_dir, from_email="reports@agency.com", http_factory=lambda s: http,
                send_mail=mailer, today=lambda: date(2026, 11, 4))
    return create_app(settings, deps=deps, start_scheduler=False)


@pytest.fixture
def web(app):
    with TestClient(app) as client:
        client.post("/login", data={"password": "pw"})
        yield client
```

(`tests/test_google_oauth.py` defines its own `settings` fixture; the local one wins there, which is fine.)

- [ ] **Step 2: Write the failing test**

`tests/test_web_dashboard.py`:

```python
from fastapi.testclient import TestClient
from sqlmodel import Session

from clientreport.db import Client, GoogleConnection


def test_pages_need_login(app):
    with TestClient(app) as client:
        r = client.get("/", follow_redirects=False)
        assert r.status_code == 303
        assert r.headers["location"] == "/login"


def test_wrong_password(app):
    with TestClient(app) as client:
        r = client.post("/login", data={"password": "nope"})
        assert r.status_code == 401
        assert "Wrong password" in r.text


def test_dashboard_says_google_is_not_connected(web):
    r = web.get("/")
    assert r.status_code == 200
    assert "Google is not connected" in r.text
    assert "No clients yet" in r.text


def test_dashboard_shows_broken_google_and_clients(web, app):
    with Session(app.state.engine) as session:
        session.add(GoogleConnection(email="me@agency.com", refresh_token_enc="x", status="broken",
                                     last_error="invalid_grant"))
        session.add(Client(name="Acme Ltd", recipients="ceo@acme.com", ga4_property_id="111",
                           gsc_site_url="sc-domain:acme.com"))
        session.commit()
    r = web.get("/")
    assert "Google connection stopped working: invalid_grant" in r.text
    assert "Acme Ltd" in r.text
    assert "October 2026 report" in r.text


def test_logout(web):
    web.post("/logout")
    assert web.get("/", follow_redirects=False).status_code == 303
```

- [ ] **Step 3: Run the test to see it fail**

Run: `uv run pytest tests/test_web_dashboard.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'clientreport.app'`

- [ ] **Step 4: Implement the shared route helpers**

Create empty `clientreport/routes/__init__.py`.

`clientreport/routes/common.py`:

```python
"""Helpers shared by all routes."""

from collections.abc import Iterator
from pathlib import Path

from fastapi import HTTPException, Request
from fastapi.templating import Jinja2Templates
from sqlmodel import Session

templates = Jinja2Templates(directory=str(Path(__file__).resolve().parent.parent / "templates"))


def db(request: Request) -> Iterator[Session]:
    with Session(request.app.state.engine, expire_on_commit=False) as session:
        yield session


def require_login(request: Request) -> None:
    if not request.session.get("admin"):
        raise HTTPException(status_code=303, headers={"Location": "/login"})


def render(request: Request, name: str, status_code: int = 200, **context):
    return templates.TemplateResponse(request, name, context, status_code=status_code)


def error_page(request: Request, message: str, status_code: int = 400):
    return render(request, "error.html", status_code=status_code, message=message)
```

- [ ] **Step 5: Implement login and the dashboard routes**

`clientreport/routes/auth.py`:

```python
"""Admin password login."""

import hmac

from fastapi import APIRouter, Form, Request
from fastapi.responses import RedirectResponse

from clientreport.routes.common import render

router = APIRouter()


@router.get("/login")
def login_page(request: Request):
    return render(request, "login.html", error=None)


@router.post("/login")
def login(request: Request, password: str = Form(...)):
    expected = request.app.state.settings.admin_password
    if hmac.compare_digest(password.encode(), expected.encode()):
        request.session["admin"] = True
        return RedirectResponse("/", status_code=303)
    return render(request, "login.html", status_code=401, error="Wrong password.")


@router.post("/logout")
def logout(request: Request):
    request.session.clear()
    return RedirectResponse("/login", status_code=303)
```

`clientreport/routes/pages.py`:

```python
"""Dashboard and settings pages."""

from fastapi import APIRouter, Depends, Request
from sqlmodel import Session, select

from clientreport.db import Client, ReportRun, get_google
from clientreport.months import label, previous_month
from clientreport.routes.common import db, render, require_login

router = APIRouter(dependencies=[Depends(require_login)])


@router.get("/")
def dashboard(request: Request, session: Session = Depends(db)):
    period = previous_month(request.app.state.deps.today())
    clients = session.exec(select(Client).order_by(Client.name)).all()
    runs = {run.client_id: run for run in session.exec(select(ReportRun).where(ReportRun.period == period)).all()}
    return render(request, "dashboard.html", clients=clients, runs=runs, month=label(period),
                  google=get_google(session))
```

- [ ] **Step 6: Implement the app factory**

`clientreport/app.py`:

```python
"""FastAPI app: wires settings, database, scheduler and routes."""

from contextlib import asynccontextmanager
from datetime import datetime
from zoneinfo import ZoneInfo

from fastapi import FastAPI
from starlette.middleware.sessions import SessionMiddleware

from clientreport import mail, scheduler
from clientreport.config import Settings
from clientreport.db import make_engine
from clientreport.google_oauth import make_http
from clientreport.pipeline import Deps
from clientreport.routes import auth, pages


def default_deps(settings: Settings) -> Deps:
    smtp = mail.SmtpConfig(settings.smtp_host, settings.smtp_port, settings.smtp_user,
                           settings.smtp_password, settings.from_email)
    return Deps(
        data_dir=settings.data_dir,
        from_email=settings.from_email,
        http_factory=lambda session: make_http(session, settings),
        send_mail=lambda message: mail.send(message, smtp),
        today=lambda: datetime.now(ZoneInfo(settings.tz)).date(),
    )


def create_app(settings: Settings, deps: Deps | None = None, start_scheduler: bool = True) -> FastAPI:
    engine = make_engine(settings.data_dir)
    deps = deps or default_deps(settings)

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        job = scheduler.start_scheduler(engine, deps, settings.tz) if start_scheduler else None
        yield
        if job is not None:
            job.shutdown(wait=False)

    app = FastAPI(title="clientreport", lifespan=lifespan)
    app.add_middleware(SessionMiddleware, secret_key=settings.app_secret_key, same_site="lax",
                       https_only=settings.base_url.startswith("https://"))
    app.state.settings = settings
    app.state.engine = engine
    app.state.deps = deps
    app.include_router(auth.router)
    app.include_router(pages.router)
    return app
```

- [ ] **Step 7: Add the templates**

`clientreport/templates/base.html`:

```html
<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{% block title %}clientreport{% endblock %}</title>
<style>
  :root { --fg: #16181d; --muted: #6b7280; --line: #e5e7eb; --ok: #1f7a4d; --bad: #b42318; --bg: #fafafa; }
  * { box-sizing: border-box; }
  body { margin: 0; font-family: -apple-system, "Segoe UI", Helvetica, Arial, sans-serif; color: var(--fg); background: var(--bg); }
  nav { display: flex; gap: 16px; align-items: center; padding: 12px 24px; background: #fff; border-bottom: 1px solid var(--line); }
  nav .grow { flex: 1; }
  nav a { color: var(--fg); text-decoration: none; font-weight: 600; }
  main { max-width: 980px; margin: 24px auto; padding: 0 16px; }
  .banner { padding: 12px 16px; border-radius: 8px; margin-bottom: 16px; background: #fff4e5; border: 1px solid #f5c26b; }
  .banner.bad { background: #fdecea; border-color: #f1a7a0; color: var(--bad); }
  .ok { color: var(--ok); }
  .err { color: var(--bad); }
  .hint { color: var(--muted); font-size: 0.9em; }
  table { width: 100%; border-collapse: collapse; background: #fff; border: 1px solid var(--line); }
  th, td { text-align: left; padding: 10px 12px; border-bottom: 1px solid var(--line); vertical-align: top; }
  form.inline { display: inline; }
  button, .button { font: inherit; padding: 6px 12px; border-radius: 6px; border: 1px solid var(--line); background: #fff; cursor: pointer; color: var(--fg); text-decoration: none; display: inline-block; }
  button.primary, .button.primary { background: var(--fg); color: #fff; border-color: var(--fg); }
  label { display: block; margin: 14px 0 4px; font-weight: 600; }
  input, select { font: inherit; padding: 8px; width: 100%; max-width: 480px; border: 1px solid var(--line); border-radius: 6px; }
</style>
</head>
<body>
{% if request.session.get("admin") %}
<nav>
  <a href="/">Clients</a>
  <a href="/settings">Settings</a>
  <span class="grow"></span>
  <form class="inline" method="post" action="/logout"><button>Log out</button></form>
</nav>
{% endif %}
<main>{% block content %}{% endblock %}</main>
</body>
</html>
```

`clientreport/templates/login.html`:

```html
{% extends "base.html" %}
{% block content %}
<h1>Sign in</h1>
{% if error %}<p class="err">{{ error }}</p>{% endif %}
<form method="post" action="/login">
  <label for="password">Admin password</label>
  <input id="password" name="password" type="password" autofocus required>
  <p><button class="primary">Sign in</button></p>
</form>
{% endblock %}
```

`clientreport/templates/dashboard.html`:

```html
{% extends "base.html" %}
{% block content %}
{% if not google or not google.refresh_token_enc %}
<div class="banner">Google is not connected. <a href="/settings">Connect Google</a> before adding clients.</div>
{% elif google.status == "broken" %}
<div class="banner bad">Google connection stopped working: {{ google.last_error }}. Reports are on hold until you <a href="/settings">reconnect</a>.</div>
{% endif %}
<h1>Clients</h1>
<p><a class="button primary" href="/clients/new">Add client</a></p>
{% if clients %}
<table>
  <tr><th>Client</th><th>Monthly send</th><th>{{ month }} report</th><th></th></tr>
  {% for c in clients %}
  {% set run = runs.get(c.id) %}
  <tr>
    <td><strong>{{ c.name }}</strong><br><span class="hint">{{ c.recipients }}</span></td>
    <td>
      <form class="inline" method="post" action="/clients/{{ c.id }}/toggle">
        <button>{{ "On, day " ~ c.send_day if c.enabled else "Off" }}</button>
      </form>
    </td>
    <td>
      {% if not run or run.status == "pending" %}Not sent yet
      {% elif run.status == "sent" %}<span class="ok">Sent {{ run.sent_at.strftime("%-d %b, %H:%M") }} UTC</span>
      {% else %}<span class="err">{{ "Failed" if run.status == "failed" else "On hold" }}: {{ run.last_error }}</span>
      {% endif %}
    </td>
    <td>
      <a class="button" href="/clients/{{ c.id }}/preview" target="_blank">Preview</a>
      <form class="inline" method="post" action="/clients/{{ c.id }}/send"><button>Send now</button></form>
      <a class="button" href="/clients/{{ c.id }}/edit">Edit</a>
    </td>
  </tr>
  {% endfor %}
</table>
{% else %}
<p>No clients yet.</p>
{% endif %}
{% endblock %}
```

`clientreport/templates/error.html`:

```html
{% extends "base.html" %}
{% block content %}
<h1>Something went wrong</h1>
<p class="err">{{ message }}</p>
<p><a class="button" href="/">Back to clients</a></p>
{% endblock %}
```

- [ ] **Step 8: Run the test to see it pass**

Run: `uv run pytest tests/test_web_dashboard.py -v`
Expected: 5 passed

- [ ] **Step 9: Run every test so far**

Run: `uv run pytest -q`
Expected: 62 passed

- [ ] **Step 10: Commit**

```bash
git add clientreport/app.py clientreport/routes clientreport/templates tests/conftest.py tests/test_web_dashboard.py
git commit -m "Add web app shell, login and dashboard" -m "Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 12: Settings, Google connect and client pages

About 45 minutes.

**Files:**
- Modify: `clientreport/routes/pages.py` (add settings routes), `clientreport/app.py` (register two routers)
- Create: `clientreport/routes/google.py`, `clientreport/routes/clients.py`, `clientreport/templates/settings.html`, `clientreport/templates/client_form.html`, `clientreport/templates/confirm_send.html`
- Test: `tests/test_web_settings.py`, `tests/test_web_clients.py`

**Interfaces:**
- Consumes: everything from Tasks 2–11. `google_oauth.finish_flow` must be called as a module attribute (`google_oauth.finish_flow(...)`) so tests can replace it.
- Produces: routes `GET/POST /settings`, `GET /auth/google/start`, `GET /auth/google/callback`, `GET /clients/new`, `POST /clients`, `GET /clients/{id}/edit`, `POST /clients/{id}`, `POST /clients/{id}/toggle`, `GET /clients/{id}/preview`, `POST /clients/{id}/send`.

- [ ] **Step 1: Write the failing tests**

`tests/test_web_settings.py`:

```python
from urllib.parse import parse_qs, urlparse

from sqlmodel import Session

from clientreport import google_oauth
from clientreport.db import get_agency, get_google

FORM = {"name": "Our Agency", "accent_hex": "#1f7a4d", "owner_email": "owner@agency.com",
        "reply_to": "hello@agency.com", "footer": "Our Agency · hello@agency.com"}


def test_save_settings(web, app):
    r = web.post("/settings", data=FORM, follow_redirects=False)
    assert r.headers["location"] == "/settings?saved=1"
    with Session(app.state.engine) as session:
        agency = get_agency(session)
        assert (agency.name, agency.accent_hex, agency.owner_email) == ("Our Agency", "#1f7a4d", "owner@agency.com")


def test_bad_colour_is_rejected(web):
    r = web.post("/settings", data={**FORM, "accent_hex": "green"})
    assert r.status_code == 422
    assert "Accent colour must look like" in r.text


def test_logo_upload(web, settings):
    png = b"\x89PNG\r\n\x1a\nfake"
    web.post("/settings", data=FORM, files={"logo": ("logo.png", png, "image/png")})
    assert (settings.data_dir / "logo.png").read_bytes() == png


def test_logo_must_be_png_or_svg(web):
    r = web.post("/settings", data=FORM, files={"logo": ("logo.gif", b"GIF89a", "image/gif")})
    assert r.status_code == 422
    assert "PNG or SVG" in r.text


def test_connect_google(web, app, monkeypatch):
    monkeypatch.setattr(google_oauth, "finish_flow",
                        lambda settings, state, verifier, url: ("me@agency.com", "refresh-1", "openid"))
    r = web.get("/auth/google/start", follow_redirects=False)
    assert r.status_code == 303
    assert r.headers["location"].startswith("https://accounts.google.com/")
    state = parse_qs(urlparse(r.headers["location"]).query)["state"][0]
    r = web.get(f"/auth/google/callback?state={state}&code=abc", follow_redirects=False)
    assert r.headers["location"] == "/settings?connected=1"
    with Session(app.state.engine) as session:
        assert get_google(session).email == "me@agency.com"
    assert "me@agency.com" in web.get("/settings").text


def test_callback_with_wrong_state(web):
    web.get("/auth/google/start", follow_redirects=False)
    r = web.get("/auth/google/callback?state=wrong&code=abc")
    assert r.status_code == 400
    assert "Sign-in expired" in r.text
```

`tests/test_web_clients.py`:

```python
from datetime import date

from sqlmodel import Session, select

from clientreport.connectors.base import GoogleAuthError
from clientreport.db import Client

GOOD = {"name": "Acme Ltd", "recipients": "ceo@acme.com", "ga4_property_id": "111",
        "gsc_site_url": "sc-domain:acme.com", "send_day": "4"}


def get_client(app) -> Client:
    with Session(app.state.engine) as session:
        return session.exec(select(Client)).one()


def test_new_client_form_lists_google_sources(web):
    r = web.get("/clients/new")
    assert "acme.com (Acme Ltd)" in r.text
    assert "sc-domain:acme.com" in r.text
    assert "https://old.acme.com/" not in r.text


def test_new_client_form_without_google(web, http):
    http.fail = GoogleAuthError("Google is not connected. Open Settings and click Connect Google.")
    r = web.get("/clients/new")
    assert "Google is not connected" in r.text
    assert 'placeholder="Property ID' in r.text


def test_create_client_validates_send_day(web):
    r = web.post("/clients", data={**GOOD, "send_day": "1"})
    assert r.status_code == 422
    assert "between 3 and 28" in r.text


def test_create_client_validates_emails(web):
    r = web.post("/clients", data={**GOOD, "recipients": "not-an-email"})
    assert r.status_code == 422
    assert "valid email" in r.text


def test_create_and_edit_client(web, app):
    r = web.post("/clients", data=GOOD, follow_redirects=False)
    assert r.headers["location"] == "/"
    client = get_client(app)
    assert (client.name, client.enabled) == ("Acme Ltd", False)
    web.post(f"/clients/{client.id}", data={**GOOD, "name": "Acme Group", "send_day": "10"})
    client = get_client(app)
    assert (client.name, client.send_day) == ("Acme Group", 10)


def test_toggle_records_switch_on_date(web, app):
    web.post("/clients", data=GOOD)
    web.post("/clients/1/toggle")
    client = get_client(app)
    assert client.enabled is True
    assert client.enabled_at == date(2026, 11, 4)
    web.post("/clients/1/toggle")
    assert get_client(app).enabled_at is None


def test_preview_returns_pdf(web):
    web.post("/clients", data=GOOD)
    r = web.get("/clients/1/preview")
    assert r.headers["content-type"] == "application/pdf"
    assert r.content[:4] == b"%PDF"


def test_preview_shows_source_errors(web, http):
    web.post("/clients", data=GOOD)
    http.fail = GoogleAuthError("Google is not connected. Open Settings and click Connect Google.")
    r = web.get("/clients/1/preview")
    assert r.status_code == 502
    assert "Could not build the report" in r.text


def test_send_now_asks_before_sending_twice(web, mailer):
    web.post("/clients", data=GOOD)
    web.post("/clients/1/send")
    assert len(mailer.sent) == 1
    r = web.post("/clients/1/send")
    assert "Send again?" in r.text
    assert len(mailer.sent) == 1
    web.post("/clients/1/send", data={"confirm": "yes"})
    assert len(mailer.sent) == 2


def test_unknown_client(web):
    assert web.get("/clients/99/edit").status_code == 404
```

- [ ] **Step 2: Run the tests to see them fail**

Run: `uv run pytest tests/test_web_settings.py tests/test_web_clients.py -v`
Expected: FAIL, mostly with `404 Not Found` responses because the routes do not exist yet.

- [ ] **Step 3: Add the settings routes**

Replace `clientreport/routes/pages.py` with:

```python
"""Dashboard and settings pages."""

import re

from fastapi import APIRouter, Depends, Request
from fastapi.responses import RedirectResponse
from sqlmodel import Session, select

from clientreport.db import Client, ReportRun, get_agency, get_google
from clientreport.google_oauth import redirect_uri
from clientreport.months import label, previous_month
from clientreport.routes.common import db, render, require_login

router = APIRouter(dependencies=[Depends(require_login)])

HEX = re.compile(r"^#[0-9a-fA-F]{6}$")
MAX_LOGO_BYTES = 1_000_000
LOGO_TYPES = {"image/png": ".png", "image/svg+xml": ".svg"}


@router.get("/")
def dashboard(request: Request, session: Session = Depends(db)):
    period = previous_month(request.app.state.deps.today())
    clients = session.exec(select(Client).order_by(Client.name)).all()
    runs = {run.client_id: run for run in session.exec(select(ReportRun).where(ReportRun.period == period)).all()}
    return render(request, "dashboard.html", clients=clients, runs=runs, month=label(period),
                  google=get_google(session))


def _settings_page(request: Request, session: Session, status_code: int = 200, error: str | None = None):
    return render(
        request, "settings.html", status_code=status_code,
        agency=get_agency(session), google=get_google(session),
        redirect_uri=redirect_uri(request.app.state.settings),
        saved=request.query_params.get("saved"), connected=request.query_params.get("connected"),
        error=error,
    )


@router.get("/settings")
def settings_page(request: Request, session: Session = Depends(db)):
    return _settings_page(request, session)


@router.post("/settings")
async def save_settings(request: Request, session: Session = Depends(db)):
    form = await request.form()
    agency = get_agency(session)

    accent = str(form.get("accent_hex", "")).strip()
    if not HEX.match(accent):
        return _settings_page(request, session, 422, "Accent colour must look like #1f7a4d.")

    logo = form.get("logo")
    if logo is not None and getattr(logo, "filename", ""):
        ext = LOGO_TYPES.get(logo.content_type)
        if ext is None:
            return _settings_page(request, session, 422, "Logo must be a PNG or SVG file.")
        data = await logo.read()
        if len(data) > MAX_LOGO_BYTES:
            return _settings_page(request, session, 422, "Logo must be 1 MB or smaller.")
        path = request.app.state.settings.data_dir / f"logo{ext}"
        path.write_bytes(data)
        agency.logo_path = str(path)

    agency.name = str(form.get("name", "")).strip() or agency.name
    agency.accent_hex = accent
    agency.owner_email = str(form.get("owner_email", "")).strip()
    agency.reply_to = str(form.get("reply_to", "")).strip()
    agency.footer = str(form.get("footer", "")).strip()
    session.add(agency)
    session.commit()
    return RedirectResponse("/settings?saved=1", status_code=303)
```

- [ ] **Step 4: Add the Google routes**

`clientreport/routes/google.py`:

```python
"""Connect the agency's Google account."""

from fastapi import APIRouter, Depends, Request
from fastapi.responses import RedirectResponse
from sqlmodel import Session

from clientreport import google_oauth
from clientreport.routes.common import db, error_page, require_login

router = APIRouter(dependencies=[Depends(require_login)])


@router.get("/auth/google/start")
def google_start(request: Request):
    settings = request.app.state.settings
    if not settings.google_client_id or not settings.google_client_secret:
        return error_page(request, "GOOGLE_CLIENT_ID and GOOGLE_CLIENT_SECRET are not set. See the README.")
    url, state, verifier = google_oauth.start_flow(settings)
    request.session["oauth_state"] = state
    request.session["oauth_verifier"] = verifier
    return RedirectResponse(url, status_code=303)


@router.get("/auth/google/callback")
def google_callback(request: Request, session: Session = Depends(db)):
    settings = request.app.state.settings
    state = request.session.pop("oauth_state", None)
    verifier = request.session.pop("oauth_verifier", None)
    if request.query_params.get("error"):
        return error_page(request, f"Google sign-in was cancelled: {request.query_params['error']}")
    if not state or request.query_params.get("state") != state:
        return error_page(request, "Sign-in expired. Click Connect Google again.")
    callback_url = f"{google_oauth.redirect_uri(settings)}?{request.url.query}"
    try:
        email, refresh_token, scopes = google_oauth.finish_flow(settings, state, verifier, callback_url)
    except Exception as exc:  # oauthlib raises many error types; show the message
        return error_page(request, f"Google sign-in failed: {exc}", 502)
    if not refresh_token:
        return error_page(
            request,
            "Google did not return a refresh token. Remove this app at "
            "myaccount.google.com/permissions, then click Connect Google again.",
        )
    google_oauth.save_connection(session, settings, email, refresh_token, scopes)
    return RedirectResponse("/settings?connected=1", status_code=303)
```

- [ ] **Step 5: Add the client routes**

`clientreport/routes/clients.py`:

```python
"""Add and edit clients, preview reports and send them."""

from fastapi import APIRouter, Depends, Request
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import RedirectResponse, Response
from sqlmodel import Session, select

from clientreport.connectors import ga4, gsc
from clientreport.connectors.base import SourceError
from clientreport.db import Client, ReportRun
from clientreport.months import label, previous_month
from clientreport.pipeline import build_report, send_report
from clientreport.routes.common import db, error_page, render, require_login

router = APIRouter(dependencies=[Depends(require_login)])

FIELDS = ("name", "recipients", "ga4_property_id", "gsc_site_url", "send_day")
EMPTY = {"name": "", "recipients": "", "ga4_property_id": "", "gsc_site_url": "", "send_day": "4"}


def _sources(request: Request, session: Session):
    try:
        http = request.app.state.deps.http_factory(session)
        return ga4.list_properties(http), gsc.list_sites(http), None
    except SourceError as exc:
        return [], [], str(exc)


def _form(request: Request, session: Session, values: dict, client_id: int | None = None,
          error: str | None = None, status_code: int = 200):
    properties, sites, source_error = _sources(request, session)
    return render(request, "client_form.html", status_code=status_code, values=values, client_id=client_id,
                  properties=properties, sites=sites, source_error=source_error, error=error)


def _read(form) -> dict:
    return {field: str(form.get(field, "")).strip() for field in FIELDS}


def _validate(values: dict) -> str | None:
    if not values["name"]:
        return "Client name is required."
    emails = [e.strip() for e in values["recipients"].split(",") if e.strip()]
    if not emails or any("@" not in e for e in emails):
        return "Enter at least one valid email address."
    if not values["ga4_property_id"] or not values["gsc_site_url"]:
        return "Choose a GA4 property and a Search Console site."
    if not values["send_day"].isdigit() or not 3 <= int(values["send_day"]) <= 28:
        return "Send day must be between 3 and 28."
    return None


def _apply(client: Client, values: dict) -> None:
    client.name = values["name"]
    client.recipients = values["recipients"]
    client.ga4_property_id = values["ga4_property_id"]
    client.gsc_site_url = values["gsc_site_url"]
    client.send_day = int(values["send_day"])


@router.get("/clients/new")
def new_client(request: Request, session: Session = Depends(db)):
    return _form(request, session, dict(EMPTY))


@router.post("/clients")
async def create_client(request: Request, session: Session = Depends(db)):
    values = _read(await request.form())
    error = _validate(values)
    if error:
        return _form(request, session, values, error=error, status_code=422)
    client = Client(name="", recipients="", ga4_property_id="", gsc_site_url="")
    _apply(client, values)
    session.add(client)
    session.commit()
    return RedirectResponse("/", status_code=303)


@router.get("/clients/{client_id}/edit")
def edit_client(request: Request, client_id: int, session: Session = Depends(db)):
    client = session.get(Client, client_id)
    if client is None:
        return error_page(request, "Client not found.", 404)
    values = {field: str(getattr(client, field)) for field in FIELDS}
    return _form(request, session, values, client_id=client_id)


@router.post("/clients/{client_id}")
async def update_client(request: Request, client_id: int, session: Session = Depends(db)):
    client = session.get(Client, client_id)
    if client is None:
        return error_page(request, "Client not found.", 404)
    values = _read(await request.form())
    error = _validate(values)
    if error:
        return _form(request, session, values, client_id=client_id, error=error, status_code=422)
    _apply(client, values)
    session.add(client)
    session.commit()
    return RedirectResponse("/", status_code=303)


@router.post("/clients/{client_id}/toggle")
def toggle_client(request: Request, client_id: int, session: Session = Depends(db)):
    client = session.get(Client, client_id)
    if client is None:
        return error_page(request, "Client not found.", 404)
    client.enabled = not client.enabled
    client.enabled_at = request.app.state.deps.today() if client.enabled else None
    session.add(client)
    session.commit()
    return RedirectResponse("/", status_code=303)


@router.get("/clients/{client_id}/preview")
async def preview(request: Request, client_id: int, session: Session = Depends(db)):
    client = session.get(Client, client_id)
    if client is None:
        return error_page(request, "Client not found.", 404)
    deps = request.app.state.deps
    period = previous_month(deps.today())
    try:
        report = await run_in_threadpool(build_report, session, client, period, deps)
    except SourceError as exc:
        return error_page(request, f"Could not build the report: {exc}", 502)
    return Response(report.pdf, media_type="application/pdf",
                    headers={"Content-Disposition": f'inline; filename="preview-{period}.pdf"'})


@router.post("/clients/{client_id}/send")
async def send_now(request: Request, client_id: int, session: Session = Depends(db)):
    client = session.get(Client, client_id)
    if client is None:
        return error_page(request, "Client not found.", 404)
    form = await request.form()
    deps = request.app.state.deps
    period = previous_month(deps.today())
    run = session.exec(
        select(ReportRun).where(ReportRun.client_id == client.id, ReportRun.period == period)
    ).first()
    if run is not None and run.status == "sent" and form.get("confirm") != "yes":
        return render(request, "confirm_send.html", client=client, month=label(period), run=run)
    await run_in_threadpool(send_report, session, client, period, deps, True)
    return RedirectResponse("/", status_code=303)
```

- [ ] **Step 6: Register the routers**

In `clientreport/app.py`, change the routes import and the router lines:

```python
from clientreport.routes import auth, clients, google, pages
```

```python
    app.include_router(auth.router)
    app.include_router(pages.router)
    app.include_router(google.router)
    app.include_router(clients.router)
```

- [ ] **Step 7: Add the templates**

`clientreport/templates/settings.html`:

```html
{% extends "base.html" %}
{% block content %}
<h1>Settings</h1>
{% if saved %}<p class="ok">Saved.</p>{% endif %}
{% if connected %}<p class="ok">Google connected.</p>{% endif %}
{% if error %}<p class="err">{{ error }}</p>{% endif %}

<h2>Google</h2>
{% if google and google.refresh_token_enc %}
<p>Connected as <strong>{{ google.email }}</strong>{% if google.status == "broken" %} <span class="err">(stopped working: {{ google.last_error }})</span>{% endif %}.</p>
{% else %}
<p>Not connected.</p>
{% endif %}
<p><a class="button primary" href="/auth/google/start">Connect Google</a></p>
<p class="hint">Sign in with the Google account that can see your clients' GA4 properties and Search Console sites.
Redirect URI for your Google OAuth client: <code>{{ redirect_uri }}</code></p>

<h2>Agency</h2>
<form method="post" action="/settings" enctype="multipart/form-data">
  <label for="name">Agency name</label>
  <input id="name" name="name" value="{{ agency.name }}" required>
  <label for="logo">Logo (PNG or SVG, max 1 MB)</label>
  <input id="logo" name="logo" type="file" accept="image/png,image/svg+xml">
  {% if agency.logo_path %}<p class="hint">Current logo: {{ agency.logo_path.split("/")[-1] }}</p>{% endif %}
  <label for="accent_hex">Accent colour</label>
  <input id="accent_hex" name="accent_hex" value="{{ agency.accent_hex }}">
  <label for="owner_email">Your email (for failure alerts)</label>
  <input id="owner_email" name="owner_email" type="email" value="{{ agency.owner_email }}">
  <label for="reply_to">Reply-to email for clients</label>
  <input id="reply_to" name="reply_to" type="email" value="{{ agency.reply_to }}">
  <label for="footer">Footer line on reports and emails</label>
  <input id="footer" name="footer" value="{{ agency.footer }}">
  <p><button class="primary">Save</button></p>
</form>
{% endblock %}
```

`clientreport/templates/client_form.html`:

```html
{% extends "base.html" %}
{% block content %}
<h1>{{ "Edit " ~ values.name if client_id else "Add client" }}</h1>
{% if error %}<p class="err">{{ error }}</p>{% endif %}
{% if source_error %}<p class="err">{{ source_error }}</p>{% endif %}
<form method="post" action="{{ '/clients/%d' % client_id if client_id else '/clients' }}">
  <label for="name">Client name</label>
  <input id="name" name="name" value="{{ values.name }}" required>

  <label for="recipients">Send to (comma-separated emails)</label>
  <input id="recipients" name="recipients" value="{{ values.recipients }}" required>

  <label for="ga4_property_id">GA4 property</label>
  {% if properties %}
  <select id="ga4_property_id" name="ga4_property_id">
    {% for p in properties %}<option value="{{ p.id }}" {{ "selected" if p.id == values.ga4_property_id }}>{{ p.name }}</option>{% endfor %}
  </select>
  {% else %}
  <input id="ga4_property_id" name="ga4_property_id" value="{{ values.ga4_property_id }}" placeholder="Property ID, e.g. 123456789" required>
  {% endif %}

  <label for="gsc_site_url">Search Console site</label>
  {% if sites %}
  <select id="gsc_site_url" name="gsc_site_url">
    {% for s in sites %}<option value="{{ s.id }}" {{ "selected" if s.id == values.gsc_site_url }}>{{ s.name }}</option>{% endfor %}
  </select>
  {% else %}
  <input id="gsc_site_url" name="gsc_site_url" value="{{ values.gsc_site_url }}" placeholder="sc-domain:example.com or https://example.com/" required>
  {% endif %}

  <label for="send_day">Send on day of the month (3–28)</label>
  <input id="send_day" name="send_day" type="number" min="3" max="28" value="{{ values.send_day }}">
  <p class="hint">Search Console data arrives 2–3 days late, so the earliest send day is the 3rd.</p>

  <p><button class="primary">Save</button> <a class="button" href="/">Cancel</a></p>
</form>
{% endblock %}
```

`clientreport/templates/confirm_send.html`:

```html
{% extends "base.html" %}
{% block content %}
<h1>Send again?</h1>
<p>The {{ month }} report for <strong>{{ client.name }}</strong> was already sent on {{ run.sent_at.strftime("%-d %b at %H:%M") }} UTC.</p>
<form method="post" action="/clients/{{ client.id }}/send">
  <input type="hidden" name="confirm" value="yes">
  <button class="primary">Send it again</button>
  <a class="button" href="/">Cancel</a>
</form>
{% endblock %}
```

- [ ] **Step 8: Run the tests to see them pass**

Run: `uv run pytest tests/test_web_settings.py tests/test_web_clients.py -v`
Expected: 16 passed

- [ ] **Step 9: Run every test**

Run: `uv run pytest -q`
Expected: 78 passed

- [ ] **Step 10: Commit**

```bash
git add clientreport tests/test_web_settings.py tests/test_web_clients.py
git commit -m "Add settings, Google connect and client pages" -m "Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 13: Docker, entry point and README

About 30 minutes.

**Files:**
- Create: `clientreport/main.py`, `Dockerfile`, `.dockerignore`, `docker-compose.yml`, `.env.example`, `README.md`

**Interfaces:**
- Consumes: `create_app` (Task 11), `load_settings` (Task 1).
- Produces: `clientreport.main:app` for uvicorn; a container that serves on port 8000 with data in `/data`.

- [ ] **Step 1: Add the entry point**

`clientreport/main.py`:

```python
"""Production entry point: uvicorn clientreport.main:app"""

from clientreport.app import create_app
from clientreport.config import load_settings

app = create_app(load_settings())
```

- [ ] **Step 2: Add the Docker files**

`Dockerfile`:

```dockerfile
FROM python:3.12-slim

RUN apt-get update \
    && apt-get install -y --no-install-recommends libpango-1.0-0 libpangoft2-1.0-0 libharfbuzz-subset0 fonts-dejavu-core \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY pyproject.toml ./
COPY clientreport ./clientreport
RUN pip install --no-cache-dir .

ENV DATA_DIR=/data
VOLUME /data
EXPOSE 8000
CMD ["uvicorn", "clientreport.main:app", "--host", "0.0.0.0", "--port", "8000", "--workers", "1"]
```

`.dockerignore`:

```
.venv
.git
data
tests
test-image
docs
.env
```

`docker-compose.yml`:

```yaml
services:
  clientreport:
    build: .
    env_file: .env
    ports:
      - "8000:8000"
    volumes:
      - ./data:/data
    restart: unless-stopped
```

`.env.example`:

```
# Where you open the app. Must match the redirect URI in your Google OAuth client.
BASE_URL=http://localhost:8000
ADMIN_PASSWORD=change-me
# Make one with: python3 -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
APP_SECRET_KEY=

GOOGLE_CLIENT_ID=
GOOGLE_CLIENT_SECRET=

SMTP_HOST=smtp.gmail.com
SMTP_PORT=587
SMTP_USER=
SMTP_PASSWORD=
FROM_EMAIL=

# Timezone for the 07:00 send job and for report months
TZ=Asia/Kolkata
```

- [ ] **Step 3: Write the README**

`README.md`:

````markdown
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
3. Set `ADMIN_PASSWORD`, your SMTP details and `TZ`.
4. Create the Google OAuth client (next section) and fill in `GOOGLE_CLIENT_ID` and `GOOGLE_CLIENT_SECRET`.
5. Start it: `docker compose up -d`
6. Open http://localhost:8000 and sign in with `ADMIN_PASSWORD`.

Data (database, logo, sent PDFs) is stored in `./data`. Back that folder up.

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
````

- [ ] **Step 4: Build and smoke-test the container**

```bash
cp .env.example .env
python3 -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
```

Paste the printed key into `APP_SECRET_KEY` in `.env`, then:

```bash
docker compose up -d --build
curl -s -o /dev/null -w "%{http_code}\n" http://localhost:8000/login
docker compose logs --tail 20
docker compose down
```

Expected: `200`, and the logs show `Application startup complete` with no errors.

- [ ] **Step 5: Commit**

```bash
git add clientreport/main.py Dockerfile .dockerignore docker-compose.yml .env.example README.md
git commit -m "Add Docker setup and README" -m "Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 14: End-to-end check with a real client

About 45 minutes, most of it Google Cloud setup. **The user does steps 1–3** because they use the user's own Google account and email password.

**Files:** none (manual check). Fix any bug found here in its own commit with a test.

- [ ] **Step 1: Google Cloud setup.** Follow "Google setup" in the README. Confirm the app shows **In production**.
- [ ] **Step 2: SMTP.** For Gmail: turn on 2-step verification, create an app password at myaccount.google.com/apppasswords and use it as `SMTP_PASSWORD`; `SMTP_USER` and `FROM_EMAIL` are the Gmail address.
- [ ] **Step 3: Fill in `.env`** with the Google client ID and secret, SMTP details and `TZ=Asia/Kolkata`.
- [ ] **Step 4: Run locally**

```bash
export DYLD_FALLBACK_LIBRARY_PATH=/opt/homebrew/lib
uv run --env-file .env uvicorn clientreport.main:app
```

- [ ] **Step 5: Connect and add a client.** Sign in, save settings with the agency logo and colour, click **Connect Google**, then add one real client with **your own email** as the recipient.
- [ ] **Step 6: Preview.** Open **Preview**. Check against GA4 and Search Console for last month: sessions, key events and organic clicks match; 3 pages; nothing cut off; the logo shows.
- [ ] **Step 7: Send now.** Click **Send now**. Check the email arrives with the right subject, the three sentences and the PDF attached. Click **Send now** again and confirm the "Send again?" page appears.
- [ ] **Step 8: Docker run.** Stop uvicorn, run `docker compose up -d --build`, sign in at http://localhost:8000 and click **Preview** again to confirm the container renders the same PDF.
- [ ] **Step 9: Switch Monthly send on** for the real client, with their real email, when you are happy with the report.
````
