import smtplib
import threading
from datetime import date
from pathlib import Path

import pytest
from fakes import FakeHttp, FakeMailer
from sqlmodel import Session, select

from clientreport.connectors.base import GoogleAuthError, SourceError
from clientreport.db import Client, GoogleConnection, ReportRun, get_agency, get_google, make_engine
from clientreport.pipeline import Deps, send_report
from clientreport.scheduler import run_due_reports


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
    send_report(session, client, "2026-10", d, manual=True, force=True)
    assert len(mailer.sent) == 2


def test_manual_send_of_a_sent_month_without_force_sends_nothing(env):
    session, client, tmp = env
    mailer = FakeMailer()
    d = deps(tmp, FakeHttp(), mailer)
    first = send_report(session, client, "2026-10", d)
    run = send_report(session, client, "2026-10", d, manual=True)
    assert run.status == "sent"
    assert run.sent_at == first.sent_at
    assert len(mailer.sent) == 1


def test_manual_send_retries_a_failed_month_without_force(env):
    session, client, tmp = env
    session.add(ReportRun(client_id=client.id, period="2026-10", status="failed", attempts=3))
    session.commit()
    mailer = FakeMailer()
    run = send_report(session, client, "2026-10", deps(tmp, FakeHttp(), mailer), manual=True)
    assert run.status == "sent"
    assert len(mailer.sent) == 1


def test_two_threads_sending_the_same_pending_month_send_one_email(env):
    session, client, tmp = env
    engine = session.get_bind()
    entered, release = threading.Event(), threading.Event()
    sent = []

    def slow_mailer(message):
        sent.append(message)
        entered.set()
        release.wait(10)  # the first sender stays inside the send until the test lets it go

    d = deps(tmp, FakeHttp(), slow_mailer)
    results = {}

    def worker(name):
        with Session(engine, expire_on_commit=False) as s:
            c = s.get(Client, client.id)
            results[name] = send_report(s, c, "2026-10", d).status

    first = threading.Thread(target=worker, args=("first",))
    second = threading.Thread(target=worker, args=("second",))
    first.start()
    assert entered.wait(30)
    second.start()
    second.join(0.3)  # without the lock the second sender would already be past its status check
    release.set()
    first.join(30)
    second.join(30)
    assert not first.is_alive() and not second.is_alive()
    assert len(sent) == 1
    assert results == {"first": "sent", "second": "sent"}


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


def test_unexpected_error_holds_and_counts_attempt(env, caplog):
    session, client, tmp = env
    mailer = FakeMailer()
    run = send_report(session, client, "2026-10", deps(tmp, FakeHttp(fail=KeyError("rows")), mailer))
    assert run.status == "held"
    assert run.attempts == 1
    assert "rows" in run.last_error
    assert mailer.sent == []
    assert "Unexpected error building report" in caplog.text


def test_owner_alert_failure_does_not_escape(env, caplog):
    session, client, tmp = env
    client.name = "Acme\nLtd"  # a newline in the subject makes build_message raise ValueError
    session.add(client)
    session.commit()
    mailer = FakeMailer()
    d = deps(tmp, FakeHttp(fail=SourceError("GSC 500"), fail_on="searchAnalytics"), mailer)
    statuses = [send_report(session, client, "2026-10", d).status for _ in range(3)]
    assert statuses == ["held", "held", "failed"]
    assert mailer.sent == []
    assert "owner alert" in caplog.text


def test_failed_manual_resend_keeps_sent_month_sent(env):
    session, client, tmp = env
    mailer = FakeMailer()
    first = send_report(session, client, "2026-10", deps(tmp, FakeHttp(), mailer))
    sent_at, pdf_path = first.sent_at, first.pdf_path
    assert len(mailer.sent) == 1
    failing = deps(tmp, FakeHttp(fail=SourceError("GSC 500"), fail_on="searchAnalytics"), mailer)
    run = send_report(session, client, "2026-10", failing, manual=True, force=True)
    assert run.status == "sent"
    assert run.sent_at == sent_at
    assert run.pdf_path == pdf_path
    assert "GSC 500" in run.last_error
    assert len(mailer.sent) == 1  # no second client email, no owner alert
    # the scheduler skips a sent month, so the client is never sent a duplicate
    send_report(session, client, "2026-10", deps(tmp, FakeHttp(), mailer))
    assert len(mailer.sent) == 1


def test_session_stays_usable_after_db_error(env):
    session, client, tmp = env

    def broken_http(s):
        s.add(ReportRun(client_id=client.id, period="2026-10"))  # duplicates the run row
        s.flush()  # raises IntegrityError and leaves the session needing a rollback
        return FakeHttp()

    d = Deps(data_dir=tmp, from_email="reports@agency.com", http_factory=broken_http,
             send_mail=FakeMailer(), today=lambda: date(2026, 11, 4))
    run = send_report(session, client, "2026-10", d)
    assert run.status == "held"
    assert run.attempts == 1
    assert session.get(ReportRun, run.id).status == "held"


def test_broken_google_holds_runs_without_attempts_and_alerts_the_owner_once(env):
    session, client, tmp = env
    engine = session.get_bind()
    client.enabled_at = date(2026, 10, 1)
    session.add(client)
    session.add(GoogleConnection(email="me@agency.com", refresh_token_enc="x", status="ok"))
    session.commit()
    mailer = FakeMailer()
    state = {"http": FakeHttp(fail=GoogleAuthError("invalid_grant"))}
    d = Deps(data_dir=tmp, from_email="reports@agency.com", http_factory=lambda s: state["http"],
             send_mail=mailer, today=lambda: date(2026, 11, 4))
    for day in range(4, 8):  # four daily runs, well past the three-attempt limit
        d.today = lambda day=day: date(2026, 11, day)
        assert run_due_reports(engine, d) == [(client.id, "2026-10", "held")]
    session.expire_all()
    run = session.exec(select(ReportRun)).one()
    assert (run.status, run.attempts) == ("held", 0)
    assert "invalid_grant" in run.last_error
    assert get_google(session).status == "broken"
    assert [m["To"] for m in mailer.sent] == ["owner@agency.com"]
    assert mailer.sent[0]["Subject"] == "Google connection stopped working"
    body = mailer.sent[0].get_body(("plain",)).get_content()
    assert "on hold" in body and "Connect Google" in body

    conn = get_google(session)  # the admin reconnects: a fresh saved connection has status ok
    conn.status, conn.last_error = "ok", ""
    session.add(conn)
    session.commit()
    state["http"] = FakeHttp()
    d.today = lambda: date(2026, 11, 8)
    assert run_due_reports(engine, d) == [(client.id, "2026-10", "sent")]
    assert [m["To"] for m in mailer.sent] == ["owner@agency.com", "ceo@acme.com"]


def test_broken_google_does_not_break_a_sent_month_on_manual_resend(env):
    session, client, tmp = env
    session.add(GoogleConnection(email="me@agency.com", refresh_token_enc="x", status="ok"))
    session.commit()
    mailer = FakeMailer()
    send_report(session, client, "2026-10", deps(tmp, FakeHttp(), mailer))
    failing = deps(tmp, FakeHttp(fail=GoogleAuthError("invalid_grant")), mailer)
    run = send_report(session, client, "2026-10", failing, manual=True, force=True)
    assert run.status == "sent"
    assert [m["To"] for m in mailer.sent] == ["ceo@acme.com", "owner@agency.com"]


def test_google_alert_failure_does_not_escape(env, caplog):
    session, client, tmp = env
    session.add(GoogleConnection(email="me@agency.com", refresh_token_enc="x", status="ok"))
    session.commit()
    mailer = FakeMailer(fail=smtplib.SMTPException("server down"))
    run = send_report(session, client, "2026-10", deps(tmp, FakeHttp(fail=GoogleAuthError("invalid_grant")), mailer))
    assert (run.status, run.attempts) == ("held", 0)
    assert "Google alert" in caplog.text
