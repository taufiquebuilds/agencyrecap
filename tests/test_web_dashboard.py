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
