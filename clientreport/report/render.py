"""Turn one month of data into the branded PDF report."""

import base64
import threading
from dataclasses import dataclass
from pathlib import Path

from jinja2 import Environment, PackageLoader, select_autoescape
from weasyprint import HTML

from clientreport.connectors.base import GA4Month, GSCMonth
from clientreport.months import label
from clientreport.report import charts
from clientreport.report.metrics import Headline, channel_title, headlines, trend_title

# pyplot and chart_style keep global state (open figures, the "agency" theme), so only one report is
# drawn at a time. The app runs one worker process, so a process-wide lock is enough.
_render_lock = threading.Lock()

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
    with _render_lock:
        return _render_report(client_name, period, ga4_month, gsc_month, branding)


def _render_report(client_name: str, period: str, ga4_month: GA4Month, gsc_month: GSCMonth,
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
            ga4_month.channels, accent, channel_title(ga4_month.channels, ga4_month.sessions), f"Sessions by channel, {month}")),
        sessions_png=_data_uri(charts.trend_chart(
            ga4_month.trend, accent, trend_title("Sessions", ga4_month.trend), "Sessions per month, last 13 months")),
        clicks_png=_data_uri(charts.trend_chart(
            gsc_month.trend, accent, trend_title("Organic clicks", gsc_month.trend),
            "Google search clicks per month, last 13 months")),
        gsc=gsc_month,
    )
    return Report(pdf=HTML(string=html).write_pdf(), headlines=heads, html=html)
