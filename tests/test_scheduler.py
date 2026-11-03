import logging
from datetime import date
from zoneinfo import ZoneInfo

from fakes import FakeHttp, FakeMailer
from sqlmodel import Session

from clientreport import scheduler
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


def test_send_day_31_in_november_is_skipped_and_healthy_client_is_still_due(tmp_path):
    with Session(make_engine(tmp_path)) as session:
        add_client(session, name="Short month", send_day=31)
        healthy = add_client(session, name="Healthy")
        assert due_clients(session, date(2026, 11, 30)) == [(healthy, "2026-10")]


def test_send_day_zero_is_skipped_with_a_warning_and_healthy_client_is_still_due(tmp_path, caplog):
    with Session(make_engine(tmp_path)) as session:
        add_client(session, name="Bad", send_day=0)
        healthy = add_client(session, name="Healthy")
        with caplog.at_level(logging.WARNING, logger="clientreport.scheduler"):
            assert due_clients(session, date(2026, 11, 4)) == [(healthy, "2026-10")]
    assert any(r.levelno == logging.WARNING and "send_day" in r.getMessage() for r in caplog.records)


def test_enabled_on_send_date_is_due(tmp_path):
    with Session(make_engine(tmp_path)) as session:
        client = add_client(session, enabled_at=date(2026, 11, 4))
        assert due_clients(session, date(2026, 11, 4)) == [(client, "2026-10")]


def test_enabled_the_day_after_send_date_is_not_due(tmp_path):
    with Session(make_engine(tmp_path)) as session:
        add_client(session, enabled_at=date(2026, 11, 5))
        assert due_clients(session, date(2026, 11, 4)) == []


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


def test_one_client_raising_does_not_stop_the_next_client(tmp_path, monkeypatch, caplog):
    engine = make_engine(tmp_path)
    with Session(engine) as session:
        broken_id = add_client(session, name="Broken").id
        add_client(session, name="Healthy")
    mailer = FakeMailer()
    deps = Deps(data_dir=tmp_path, from_email="reports@agency.com", http_factory=lambda s: FakeHttp(),
                send_mail=mailer, today=lambda: date(2026, 11, 4))
    real_send_report = scheduler.send_report

    def flaky_send_report(session, client, period, deps, manual=False):
        if client.id == broken_id:
            raise RuntimeError("boom")
        return real_send_report(session, client, period, deps, manual)

    monkeypatch.setattr(scheduler, "send_report", flaky_send_report)
    with caplog.at_level(logging.ERROR, logger="clientreport.scheduler"):
        assert run_due_reports(engine, deps) == [(2, "2026-10", "sent")]
    assert len(mailer.sent) == 1
    assert f"client {broken_id}, 2026-10" in caplog.text


def test_scheduler_runs_daily_at_seven_and_shuts_down(tmp_path):
    deps = Deps(data_dir=tmp_path, from_email="reports@agency.com", http_factory=lambda s: FakeHttp(),
                send_mail=FakeMailer(), today=lambda: date(2026, 11, 4))
    scheduler = start_scheduler(make_engine(tmp_path), deps, "Europe/London")
    try:
        job = scheduler.get_job("due-reports")
        assert job is not None
        assert str(job.next_run_time.tzinfo) == "Europe/London"
        local = job.next_run_time.astimezone(ZoneInfo("Europe/London"))
        assert (local.hour, local.minute) == (7, 0)
        assert job.next_run_time.utcoffset() == local.utcoffset()
    finally:
        scheduler.shutdown(wait=False)
