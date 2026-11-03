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


def test_unexpected_error_holds_and_counts_attempt(env, caplog):
    session, client, tmp = env
    mailer = FakeMailer()
    run = send_report(session, client, "2026-10", deps(tmp, FakeHttp(fail=KeyError("rows")), mailer))
    assert run.status == "held"
    assert run.attempts == 1
    assert "rows" in run.last_error
    assert mailer.sent == []
    assert "Unexpected error building report" in caplog.text
