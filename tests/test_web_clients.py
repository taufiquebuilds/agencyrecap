import asyncio
import re
from datetime import date

from fastapi.testclient import TestClient
from sqlmodel import Session, select

from clientreport.connectors.base import GoogleAuthError
from clientreport.db import Client

GOOD = {"name": "Acme Ltd", "recipients": "ceo@acme.com", "ga4_property_id": "111",
        "gsc_site_url": "sc-domain:acme.com", "send_day": "4"}


def selected_option(html: str, select_id: str) -> str | None:
    """The value of the selected <option> in the named <select>, or None."""
    block = re.search(rf'<select id="{select_id}".*?</select>', html, re.S).group(0)
    match = re.search(r'<option value="([^"]*)"\s+selected', block)
    return match.group(1) if match else None


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


def _loop_running() -> bool:
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return False
    return True


def test_blocking_google_lookups_on_validation_errors_stay_off_the_event_loop(web, monkeypatch):
    from clientreport.routes import clients as clients_routes

    web.post("/clients", data=GOOD)
    on_loop = []
    real = clients_routes._sources

    def spy(request, session):
        on_loop.append(_loop_running())  # _sources makes blocking Google calls
        return real(request, session)

    monkeypatch.setattr(clients_routes, "_sources", spy)
    assert web.post("/clients", data={**GOOD, "send_day": "1"}).status_code == 422
    assert web.post("/clients/1", data={**GOOD, "send_day": "1"}).status_code == 422
    assert on_loop == [False, False]


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


def test_send_now_does_not_resend_a_month_the_scheduler_just_sent(web, mailer, monkeypatch):
    from clientreport.routes import clients as clients_routes

    web.post("/clients", data=GOOD)
    real = clients_routes.send_report

    def scheduler_wins_the_race(session, client, period, deps, manual=False, force=False):
        real(session, client, period, deps)  # the 07:00 run sends between the page check and our send
        return real(session, client, period, deps, manual, force)

    monkeypatch.setattr(clients_routes, "send_report", scheduler_wins_the_race)
    assert web.post("/clients/1/send", follow_redirects=False).status_code == 303
    assert len(mailer.sent) == 1


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


def make_client(app, **overrides) -> int:
    values = dict(name="Acme Ltd", recipients="ceo@acme.com", ga4_property_id="111",
                  gsc_site_url="sc-domain:acme.com", send_day=4) | overrides
    with Session(app.state.engine) as session:
        client = Client(**values)
        session.add(client)
        session.commit()
        return client.id


def test_edit_keeps_saved_sources_that_are_not_listed(web, app):
    client_id = make_client(app, ga4_property_id="999", gsc_site_url="https://gone.example/")
    r = web.get(f"/clients/{client_id}/edit")
    assert selected_option(r.text, "ga4_property_id") == "999"
    assert selected_option(r.text, "gsc_site_url") == "https://gone.example/"
    assert "999 (not visible to the connected Google account)" in r.text
    assert "https://gone.example/ (not visible to the connected Google account)" in r.text


def test_edit_selects_saved_sources_that_are_listed(web, app):
    client_id = make_client(app, ga4_property_id="222")
    r = web.get(f"/clients/{client_id}/edit")
    assert selected_option(r.text, "ga4_property_id") == "222"
    assert selected_option(r.text, "gsc_site_url") == "sc-domain:acme.com"
    assert "not visible to the connected Google account" not in r.text


def test_new_client_form_selects_the_empty_choice(web):
    r = web.get("/clients/new")
    assert selected_option(r.text, "ga4_property_id") == ""
    assert selected_option(r.text, "gsc_site_url") == ""
    r = web.post("/clients", data={**GOOD, "ga4_property_id": "", "gsc_site_url": ""})
    assert r.status_code == 422
    assert "Choose a GA4 property" in r.text


def test_client_form_rejects_control_characters(web, app):
    r = web.post("/clients", data={**GOOD, "recipients": "ceo@acme.com,\r\nBcc: x@evil.com"})
    assert r.status_code == 422
    assert "control characters" in r.text
    r = web.post("/clients", data={**GOOD, "name": "Acme\nLtd"})
    assert r.status_code == 422
    assert "control characters" in r.text
    r = web.post("/clients", data={**GOOD, "recipients": "ceo @acme.com"})
    assert r.status_code == 422
    assert "valid email" in r.text
    with Session(app.state.engine) as session:
        assert session.exec(select(Client)).all() == []


def test_preview_survives_unexpected_errors(web, http):
    web.post("/clients", data=GOOD)
    http.fail = KeyError("boom")
    r = web.get("/clients/1/preview")
    assert r.status_code == 500
    assert "The preview could not be built. Check the server log." in r.text
