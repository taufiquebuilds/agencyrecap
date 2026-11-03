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
