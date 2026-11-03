from datetime import UTC, datetime

from fastapi.testclient import TestClient
from sqlmodel import Session

from clientreport.db import Client, GoogleConnection, ReportRun


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


def test_login_is_throttled_after_five_failures(app):
    with TestClient(app) as client:
        for _ in range(5):
            assert client.post("/login", data={"password": "nope"}).status_code == 401
        r = client.post("/login", data={"password": "pw"})  # right password, but locked out
        assert r.status_code == 429
        assert "Too many attempts. Try again in 15 minutes." in r.text
        assert client.get("/", follow_redirects=False).status_code == 303  # not logged in


def test_throttle_is_per_app_and_cleared_by_success(app, settings, http, mailer):
    from clientreport.app import create_app
    with TestClient(app) as client:
        for _ in range(4):
            client.post("/login", data={"password": "nope"})
        assert client.post("/login", data={"password": "pw"}, follow_redirects=False).status_code == 303
        for _ in range(4):  # counter was cleared, so four more failures are still allowed
            assert client.post("/login", data={"password": "nope"}).status_code == 401
    fresh = create_app(settings, deps=app.state.deps, start_scheduler=False)
    with TestClient(fresh) as other:
        assert other.post("/login", data={"password": "nope"}).status_code == 401


def test_throttle_expires_after_fifteen_minutes(app, monkeypatch):
    from clientreport.routes import auth
    now = [1000.0]
    monkeypatch.setattr(auth.time, "monotonic", lambda: now[0])
    with TestClient(app) as client:
        for _ in range(5):
            client.post("/login", data={"password": "nope"})
        assert client.post("/login", data={"password": "pw"}).status_code == 429
        now[0] += 15 * 60 + 1
        assert client.post("/login", data={"password": "pw"}, follow_redirects=False).status_code == 303


def test_api_docs_are_not_exposed(app):
    with TestClient(app) as client:
        for path in ("/docs", "/redoc", "/openapi.json"):
            assert client.get(path).status_code == 404


def test_scheduler_runs_during_lifespan_and_stops_after(settings):
    from clientreport.app import create_app
    app = create_app(settings, start_scheduler=True)
    with TestClient(app):
        scheduler = app.state.scheduler
        assert scheduler.running
        assert scheduler.get_job("due-reports") is not None
    assert not scheduler.running


def _seed_run(app, name, status, **fields):
    with Session(app.state.engine) as session:
        client = Client(name=name, recipients="x@example.com", ga4_property_id="1", gsc_site_url="sc-domain:x.com")
        session.add(client)
        session.commit()
        if status:
            session.add(ReportRun(client_id=client.id, period="2026-10", status=status, **fields))
            session.commit()


def test_dashboard_shows_every_run_status(web, app):
    _seed_run(app, "Sent Co", "sent", sent_at=datetime(2026, 11, 5, 7, 0, tzinfo=UTC))
    _seed_run(app, "Failed Co", "failed", last_error="smtp down")
    _seed_run(app, "Held Co", "held", last_error="google broken")
    _seed_run(app, "Pending Co", None)
    text = web.get("/").text
    assert "Sent 5 Nov, 07:00 UTC" in text
    assert "Failed: smtp down" in text
    assert "On hold: google broken" in text
    assert "Not sent yet" in text
    assert "re-send failed" not in text


def test_dashboard_shows_failed_resend_on_a_sent_month(web, app):
    _seed_run(app, "Sent Co", "sent", sent_at=datetime(2026, 11, 5, 7, 0, tzinfo=UTC), last_error="smtp timeout")
    text = web.get("/").text
    assert "Sent 5 Nov, 07:00 UTC" in text
    assert "Last re-send failed: smtp timeout" in text
