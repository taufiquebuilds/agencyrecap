"""Render the example report and dashboard pages used in the README.

All names, domains and numbers are made up. Run from the repo root:

    export DYLD_FALLBACK_LIBRARY_PATH=/opt/homebrew/lib   # macOS
    uv run python scripts/make_screenshots.py OUT_DIR

It writes report.pdf plus dashboard.html, client_form.html and settings.html to OUT_DIR.
Turn them into PNGs with pdftoppm and a headless browser (see docs/images/README.md).
"""

import sys
from datetime import UTC, date, datetime
from pathlib import Path

from cryptography.fernet import Fernet
from fastapi.testclient import TestClient
from sqlmodel import Session

from clientreport.app import create_app
from clientreport.config import load_settings
from clientreport.connectors.base import GA4Month, GSCMonth, SearchRow
from clientreport.db import Client, ReportRun, get_agency, get_google
from clientreport.google_oauth import save_connection
from clientreport.pipeline import Deps
from clientreport.report.render import Branding, render_report

ACCENT = "#0f766e"
PERIOD = "2026-10"
MONTHS = ["2025-10", "2025-11", "2025-12", "2026-01", "2026-02", "2026-03", "2026-04",
          "2026-05", "2026-06", "2026-07", "2026-08", "2026-09", "2026-10"]

LOGO = f"""<svg xmlns="http://www.w3.org/2000/svg" width="240" height="48" viewBox="0 0 240 48">
<circle cx="24" cy="24" r="20" fill="{ACCENT}"/>
<path d="M24 10 L28 22 L40 24 L28 26 L24 38 L20 26 L8 24 L20 22 Z" fill="#fff"/>
<text x="54" y="31" font-family="Helvetica, Arial, sans-serif" font-size="20" font-weight="700"
fill="#111">Northstar Digital</text></svg>"""


def demo_months() -> tuple[GA4Month, GSCMonth]:
    sessions = [3120, 2980, 2710, 3050, 3240, 3480, 3610, 3700, 3820, 3950, 4105, 4390, 4812]
    clicks = [1020, 990, 940, 1080, 1150, 1260, 1330, 1420, 1510, 1580, 1690, 1810, 2140]
    ga4 = GA4Month(
        period=PERIOD, sessions=4812, prev_sessions=4390, key_events=126, prev_key_events=118,
        channels=[("Organic Search", 2280), ("Direct", 1105), ("Paid Search", 640),
                  ("Referral", 410), ("Organic Social", 290), ("Email", 87)],
        trend=list(zip(MONTHS, sessions)),
    )
    q = [("dentist near me", 412, 9800), ("harbor lane dental", 365, 1450),
         ("emergency dentist", 188, 6200), ("teeth whitening cost", 142, 7100),
         ("invisalign price", 121, 5400), ("dental implants", 98, 4900),
         ("family dentist", 87, 3300), ("root canal pain", 64, 4100),
         ("kids dentist", 58, 2200), ("dentist open saturday", 41, 1900)]
    p = [("/", 690, 15200), ("/emergency-dentist", 284, 8100), ("/teeth-whitening", 231, 9400),
         ("/invisalign", 176, 6800), ("/dental-implants", 149, 7200), ("/book", 133, 2100),
         ("/blog/root-canal-recovery", 96, 6400), ("/childrens-dentistry", 81, 3000),
         ("/pricing", 74, 2600), ("/blog/whitening-at-home", 52, 4300)]
    positions = [3.1, 1.2, 4.8, 6.3, 5.9, 7.4, 4.2, 8.1, 5.5, 6.7]

    def rows(items, prefix=""):
        return [SearchRow(key=prefix + k, clicks=c, impressions=i, ctr=c / i, position=pos)
                for (k, c, i), pos in zip(items, positions)]

    gsc = GSCMonth(
        period=PERIOD, clicks=2140, prev_clicks=1810, impressions=61200, prev_impressions=54800,
        trend=list(zip(MONTHS, clicks)),
        top_queries=rows(q), top_pages=rows(p, "https://harborlanedental.example"),
    )
    return ga4, gsc


class DemoHttp:
    """Answers the property and site lists for the client form. No network."""

    def get(self, url: str) -> dict:
        if "accountSummaries" in url:
            return {"accountSummaries": [{"account": "accounts/1", "displayName": "Northstar clients",
                    "propertySummaries": [
                        {"property": "properties/301", "displayName": "harborlanedental.example"},
                        {"property": "properties/302", "displayName": "ridgewaybakery.example"},
                        {"property": "properties/303", "displayName": "cobaltfitness.example"},
                        {"property": "properties/304", "displayName": "maplegrovelaw.example"}]}]}
        return {"siteEntry": [{"siteUrl": f"sc-domain:{d}.example", "permissionLevel": "siteOwner"}
                              for d in ("harborlanedental", "ridgewaybakery", "cobaltfitness", "maplegrovelaw")]}

    def post(self, url: str, body: dict) -> dict:
        return {}


def main(out: Path) -> None:
    out.mkdir(parents=True, exist_ok=True)
    data = out / "data"
    data.mkdir(exist_ok=True)
    logo = data / "logo.svg"
    logo.write_text(LOGO)

    ga4, gsc = demo_months()
    branding = Branding(agency_name="Northstar Digital", logo_path=str(logo), accent_hex=ACCENT,
                        footer="Northstar Digital · hello@northstar.example")
    (out / "report.pdf").write_bytes(render_report("Harbor Lane Dental", PERIOD, ga4, gsc, branding).pdf)

    settings = load_settings({
        "BASE_URL": "http://localhost:8000", "ADMIN_PASSWORD": "demo-password-10",
        "APP_SECRET_KEY": Fernet.generate_key().decode(), "GOOGLE_CLIENT_ID": "demo",
        "GOOGLE_CLIENT_SECRET": "demo", "DATA_DIR": str(data), "TZ": "Europe/London",
    })
    deps = Deps(data_dir=data, from_email="reports@northstar.example", http_factory=lambda s: DemoHttp(),
                send_mail=lambda m: None, today=lambda: date(2026, 11, 4))
    app = create_app(settings, deps=deps, start_scheduler=False)

    with TestClient(app) as web:
        with Session(app.state.engine) as s:
            agency = get_agency(s)
            agency.name, agency.logo_path, agency.accent_hex = "Northstar Digital", str(logo), ACCENT
            agency.owner_email, agency.reply_to = "owner@northstar.example", "hello@northstar.example"
            agency.footer = branding.footer
            s.add(agency)
            s.commit()
            save_connection(s, settings, "reports@northstar.example", "demo-refresh-token", "demo")
            clients = [
                Client(name="Harbor Lane Dental", recipients="maria@harborlanedental.example",
                       ga4_property_id="301", gsc_site_url="sc-domain:harborlanedental.example",
                       send_day=4, enabled=True, enabled_at=date(2026, 9, 1)),
                Client(name="Ridgeway Bakery", recipients="owner@ridgewaybakery.example",
                       ga4_property_id="302", gsc_site_url="sc-domain:ridgewaybakery.example",
                       send_day=4, enabled=True, enabled_at=date(2026, 9, 1)),
                Client(name="Cobalt Fitness", recipients="team@cobaltfitness.example, gm@cobaltfitness.example",
                       ga4_property_id="303", gsc_site_url="sc-domain:cobaltfitness.example",
                       send_day=6, enabled=True, enabled_at=date(2026, 9, 1)),
                Client(name="Maple Grove Law", recipients="office@maplegrovelaw.example",
                       ga4_property_id="304", gsc_site_url="sc-domain:maplegrovelaw.example",
                       send_day=10, enabled=False),
            ]
            s.add_all(clients)
            s.commit()
            sent = datetime(2026, 11, 4, 7, 0, 12, tzinfo=UTC)
            s.add_all([
                ReportRun(client_id=clients[0].id, period=PERIOD, status="sent", attempts=1, sent_at=sent),
                ReportRun(client_id=clients[1].id, period=PERIOD, status="sent", attempts=1, sent_at=sent),
            ])
            s.commit()
            google = get_google(s)
            assert google is not None and google.status == "ok"

        web.post("/login", data={"password": "demo-password-10"})
        for name, path in [("dashboard", "/"), ("client_form", "/clients/1/edit"), ("settings", "/settings")]:
            r = web.get(path)
            r.raise_for_status()
            (out / f"{name}.html").write_text(r.text)


if __name__ == "__main__":
    main(Path(sys.argv[1]))
