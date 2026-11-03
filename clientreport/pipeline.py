"""Fetch a client's data, build the PDF, email it and record the run."""

import html as html_lib
import logging
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

logger = logging.getLogger(__name__)

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
    was_sent = run.status == "sent"  # a manual re-send of a delivered month
    if manual:
        run.attempts = 0

    try:
        report = build_report(session, client, period, deps)
        pdf_path = deps.data_dir / "reports" / f"client-{client.id}-{period}.pdf"
        pdf_path.parent.mkdir(parents=True, exist_ok=True)
        pdf_path.write_bytes(report.pdf)
        deps.send_mail(client_email(get_agency(session), client, period, report.headlines, report.pdf,
                                    deps.from_email))
    except Exception as exc:  # SourceError and OSError (SMTP) are expected; anything else is a bug or bad data
        session.rollback()  # a database error mid-try leaves the session unusable until rolled back
        session.refresh(run)  # rollback expired run and discarded the manual attempts reset
        if not isinstance(exc, (SourceError, OSError)):
            logger.exception("Unexpected error building report for client %s, %s", client.id, period)
        if isinstance(exc, GoogleAuthError):
            mark_google_broken(session, str(exc))
        run.last_error = str(exc) or exc.__class__.__name__
        if was_sent:  # the month was already delivered: record the error, keep it sent, no alert
            session.add(run)
            session.commit()
            return run
        if manual:
            run.attempts = 0
        run.attempts += 1
        run.status = "failed" if run.attempts >= MAX_ATTEMPTS else "held"
        session.add(run)
        session.commit()
        agency = get_agency(session)
        if run.status == "failed" and agency.owner_email:
            try:
                deps.send_mail(alert_email(agency, client, run, deps.from_email))
            except Exception:  # the dashboard still shows the failure; never abort the scheduler loop
                logger.exception("Could not send the owner alert for client %s, %s", client.id, period)
        return run

    run.status = "sent"
    run.sent_at = datetime.now(UTC)
    run.pdf_path = str(pdf_path)
    run.last_error = ""
    session.add(run)
    session.commit()
    return run
