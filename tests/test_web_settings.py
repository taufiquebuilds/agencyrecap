from urllib.parse import parse_qs, urlparse

from fastapi.testclient import TestClient
from sqlmodel import Session

from clientreport import google_oauth
from clientreport.db import get_agency, get_google

FULL_SCOPES = " ".join(google_oauth.SCOPES)
FORM = {"name": "Our Agency", "accent_hex": "#1f7a4d", "owner_email": "owner@agency.com",
        "reply_to": "hello@agency.com", "footer": "Our Agency · hello@agency.com"}


def test_save_settings(web, app):
    r = web.post("/settings", data=FORM, follow_redirects=False)
    assert r.headers["location"] == "/settings?saved=1"
    with Session(app.state.engine) as session:
        agency = get_agency(session)
        assert (agency.name, agency.accent_hex, agency.owner_email) == ("Our Agency", "#1f7a4d", "owner@agency.com")


def test_bad_colour_is_rejected(web):
    r = web.post("/settings", data={**FORM, "accent_hex": "green"})
    assert r.status_code == 422
    assert "Accent colour must look like" in r.text


def test_validation_error_keeps_typed_values(web):
    r = web.post("/settings", data={**FORM, "name": "New Agency", "accent_hex": "green", "footer": "Typed footer"})
    assert r.status_code == 422
    assert 'value="New Agency"' in r.text
    assert 'value="Typed footer"' in r.text
    assert 'value="green"' in r.text


def test_settings_reject_control_characters(web, app):
    r = web.post("/settings", data={**FORM, "reply_to": "hello@agency.com\r\nBcc: x@evil.com"})
    assert r.status_code == 422
    assert "control characters" in r.text
    r = web.post("/settings", data={**FORM, "owner_email": "own er@agency.com"})
    assert r.status_code == 422
    assert "valid email" in r.text
    r = web.post("/settings", data={**FORM, "name": "Our\nAgency"})
    assert r.status_code == 422
    with Session(app.state.engine) as session:
        assert get_agency(session).owner_email != "own er@agency.com"


def test_logo_upload(web, settings):
    png = b"\x89PNG\r\n\x1a\nfake"
    web.post("/settings", data=FORM, files={"logo": ("logo.png", png, "image/png")})
    assert (settings.data_dir / "logo.png").read_bytes() == png


def test_logo_must_be_png_or_svg(web):
    r = web.post("/settings", data=FORM, files={"logo": ("logo.gif", b"GIF89a", "image/gif")})
    assert r.status_code == 422
    assert "PNG or SVG" in r.text


def test_logo_over_one_megabyte_is_rejected(web, settings):
    r = web.post("/settings", data=FORM, files={"logo": ("big.png", b"x" * 1_000_001, "image/png")})
    assert r.status_code == 422
    assert "1 MB or smaller" in r.text
    assert not (settings.data_dir / "logo.png").exists()


def test_logo_stored_name_ignores_uploaded_filename(web, app, settings):
    web.post("/settings", data=FORM, files={"logo": ("../../evil.png", b"\x89PNG", "image/png")})
    assert (settings.data_dir / "logo.png").read_bytes() == b"\x89PNG"
    assert not (settings.data_dir.parent / "evil.png").exists()
    with Session(app.state.engine) as session:
        assert get_agency(session).logo_path == str(settings.data_dir / "logo.png")


def test_connect_google(web, app, monkeypatch):
    monkeypatch.setattr(google_oauth, "finish_flow",
                        lambda settings, state, verifier, url: ("me@agency.com", "refresh-1", FULL_SCOPES))
    r = web.get("/auth/google/start", follow_redirects=False)
    assert r.status_code == 303
    assert r.headers["location"].startswith("https://accounts.google.com/")
    state = parse_qs(urlparse(r.headers["location"]).query)["state"][0]
    r = web.get(f"/auth/google/callback?state={state}&code=abc", follow_redirects=False)
    assert r.headers["location"] == "/settings?connected=1"
    with Session(app.state.engine) as session:
        assert get_google(session).email == "me@agency.com"
    assert "me@agency.com" in web.get("/settings").text


def test_callback_requires_analytics_and_search_console_scopes(web, app, monkeypatch):
    monkeypatch.setattr(google_oauth, "finish_flow",
                        lambda settings, state, verifier, url: ("me@agency.com", "refresh-1", "openid"))
    r = web.get("/auth/google/start", follow_redirects=False)
    state = parse_qs(urlparse(r.headers["location"]).query)["state"][0]
    r = web.get(f"/auth/google/callback?state={state}&code=abc", follow_redirects=False)
    assert r.status_code == 400
    assert "missing access to Analytics or Search Console" in r.text
    with Session(app.state.engine) as session:
        assert get_google(session) is None


def test_callback_with_wrong_state(web):
    web.get("/auth/google/start", follow_redirects=False)
    r = web.get("/auth/google/callback?state=wrong&code=abc")
    assert r.status_code == 400
    assert "Sign-in expired" in r.text


def test_callback_shows_google_failure(web, monkeypatch):
    def boom(settings, state, verifier, url):
        raise google_oauth.GoogleAuthError("Google did not return a refresh token.")

    monkeypatch.setattr(google_oauth, "finish_flow", boom)
    r = web.get("/auth/google/start", follow_redirects=False)
    state = parse_qs(urlparse(r.headers["location"]).query)["state"][0]
    r = web.get(f"/auth/google/callback?state={state}&code=abc")
    assert r.status_code == 502
    assert "refresh token" in r.text


def test_settings_and_google_routes_need_login(app):
    with TestClient(app) as anon:
        for method, path in [("get", "/settings"), ("post", "/settings"), ("get", "/auth/google/start"),
                             ("get", "/auth/google/callback?state=x&code=y")]:
            r = getattr(anon, method)(path, follow_redirects=False)
            assert (r.status_code, r.headers["location"]) == (303, "/login"), path
