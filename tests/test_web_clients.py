from datetime import date

from fastapi.testclient import TestClient
from sqlmodel import Session, select

from clientreport.connectors.base import GoogleAuthError
from clientreport.db import Client

GOOD = {"name": "Acme Ltd", "recipients": "ceo@acme.com", "ga4_property_id": "111",
        "gsc_site_url": "sc-domain:acme.com", "send_day": "4"}


def get_client(app) -> Client:
    with Session(app.state.engine) as session:
        return session.exec(select(Client)).one()


def test_new_client_form_lists_google_sources(web):
    r = web.get("/clients/new")
    assert "acme.com (Acme Ltd)" in r.text
    assert "sc-domain:acme.com" in r.text
    assert "https://old.acme.com/" not in r.text


def test_new_client_form_without_google(web, http):
    http.fail = GoogleAuthError("Google is not connected. Open Settings and click Connect Google.")
    r = web.get("/clients/new")
    assert "Google is not connected" in r.text
    assert 'placeholder="Property ID' in r.text


def test_form_shows_notice_when_google_client_cannot_be_built(web, app):
    def cannot_decrypt(session):
        raise GoogleAuthError("The saved Google connection can't be read. Connect Google again.")

    app.state.deps.http_factory = cannot_decrypt
    r = web.get("/clients/new")
    assert r.status_code == 200
    assert "be read. Connect Google again" in r.text  # the apostrophe is HTML-escaped, so skip it
    assert 'placeholder="Property ID' in r.text


def test_edit_form_survives_unreadable_google_connection(web, app):
    web.post("/clients", data=GOOD)

    def cannot_decrypt(session):
        raise GoogleAuthError("The saved Google connection can't be read. Connect Google again.")

    app.state.deps.http_factory = cannot_decrypt
    r = web.get("/clients/1/edit")
    assert r.status_code == 200
    assert "be read. Connect Google again" in r.text  # the apostrophe is HTML-escaped, so skip it
    assert 'value="111"' in r.text


def test_invalid_submission_with_google_down_still_renders(web, http):
    http.fail = GoogleAuthError("Google is not connected. Open Settings and click Connect Google.")
    r = web.post("/clients", data={**GOOD, "send_day": "1"})
    assert r.status_code == 422
    assert "between 3 and 28" in r.text
    assert "Google is not connected" in r.text


def test_create_client_validates_send_day(web):
    r = web.post("/clients", data={**GOOD, "send_day": "1"})
    assert r.status_code == 422
    assert "between 3 and 28" in r.text


def test_send_day_limits_and_junk(web):
    for bad in ("2", "29", "", "abc", "4.5", "-4", "²", "٣"):
        r = web.post("/clients", data={**GOOD, "send_day": bad})
        assert r.status_code == 422, bad
        assert "between 3 and 28" in r.text, bad
    for good in ("3", "28"):
        assert web.post("/clients", data={**GOOD, "send_day": good}).status_code == 200  # follows redirect


def test_create_client_validates_emails(web):
    r = web.post("/clients", data={**GOOD, "recipients": "not-an-email"})
    assert r.status_code == 422
    assert "valid email" in r.text


def test_create_and_edit_client(web, app):
    r = web.post("/clients", data=GOOD, follow_redirects=False)
    assert r.headers["location"] == "/"
    client = get_client(app)
    assert (client.name, client.enabled) == ("Acme Ltd", False)
    web.post(f"/clients/{client.id}", data={**GOOD, "name": "Acme Group", "send_day": "10"})
    client = get_client(app)
    assert (client.name, client.send_day) == ("Acme Group", 10)


def test_edit_rejects_bad_input_and_keeps_client(web, app):
    web.post("/clients", data=GOOD)
    r = web.post("/clients/1", data={**GOOD, "name": "Changed", "send_day": "99"})
    assert r.status_code == 422
    assert get_client(app).name == "Acme Ltd"


def test_toggle_records_switch_on_date(web, app):
    web.post("/clients", data=GOOD)
    web.post("/clients/1/toggle")
    client = get_client(app)
    assert client.enabled is True
    assert client.enabled_at == date(2026, 11, 4)
    web.post("/clients/1/toggle")
    assert get_client(app).enabled_at is None


def test_preview_returns_pdf(web):
    web.post("/clients", data=GOOD)
    r = web.get("/clients/1/preview")
    assert r.headers["content-type"] == "application/pdf"
    assert r.content[:4] == b"%PDF"


def test_preview_shows_source_errors(web, http):
    web.post("/clients", data=GOOD)
    http.fail = GoogleAuthError("Google is not connected. Open Settings and click Connect Google.")
    r = web.get("/clients/1/preview")
    assert r.status_code == 502
    assert "Could not build the report" in r.text


def test_send_now_asks_before_sending_twice(web, mailer):
    web.post("/clients", data=GOOD)
    web.post("/clients/1/send")
    assert len(mailer.sent) == 1
    r = web.post("/clients/1/send")
    assert "Send again?" in r.text
    assert len(mailer.sent) == 1
    web.post("/clients/1/send", data={"confirm": "yes"})
    assert len(mailer.sent) == 2


def test_unknown_client(web):
    assert web.get("/clients/99/edit").status_code == 404
    assert web.post("/clients/99").status_code == 404
    assert web.post("/clients/99/toggle").status_code == 404
    assert web.get("/clients/99/preview").status_code == 404
    assert web.post("/clients/99/send").status_code == 404


def test_client_routes_need_login(app):
    with TestClient(app) as anon:
        for method, path in [("get", "/clients/new"), ("post", "/clients"), ("get", "/clients/1/edit"),
                             ("post", "/clients/1"), ("post", "/clients/1/toggle"),
                             ("get", "/clients/1/preview"), ("post", "/clients/1/send")]:
            r = getattr(anon, method)(path, follow_redirects=False)
            assert (r.status_code, r.headers["location"]) == (303, "/login"), path
