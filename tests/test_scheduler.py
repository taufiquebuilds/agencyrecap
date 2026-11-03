from datetime import date

from fakes import FakeHttp, FakeMailer
from sqlmodel import Session

from clientreport.db import Client, ReportRun, make_engine
from clientreport.pipeline import Deps
from clientreport.scheduler import due_clients, run_due_reports, start_scheduler


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


def test_scheduler_runs_daily_at_seven_and_shuts_down(tmp_path):
    deps = Deps(data_dir=tmp_path, from_email="reports@agency.com", http_factory=lambda s: FakeHttp(),
                send_mail=FakeMailer(), today=lambda: date(2026, 11, 4))
    scheduler = start_scheduler(make_engine(tmp_path), deps, "Europe/London")
    try:
        job = scheduler.get_job("due-reports")
        assert job is not None
        assert (job.next_run_time.hour, job.next_run_time.minute) == (7, 0)
    finally:
        scheduler.shutdown(wait=False)
