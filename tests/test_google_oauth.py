from dataclasses import replace
from urllib.parse import parse_qs, urlparse

import pytest
from cryptography.fernet import Fernet
from google.auth.exceptions import RefreshError
from sqlmodel import Session

from clientreport import crypto
from clientreport.config import load_settings
from clientreport.connectors.base import GoogleAuthError, SourceError
from clientreport.db import GoogleConnection, get_google, make_engine
from clientreport import google_oauth
from clientreport.google_oauth import (
    GoogleHttp, _is_local_http, finish_flow, make_http, save_connection, start_flow,
)


@pytest.fixture
def settings(tmp_path):
    return load_settings({
        "BASE_URL": "http://localhost:8000", "ADMIN_PASSWORD": "pw",
        "APP_SECRET_KEY": Fernet.generate_key().decode(),
        "GOOGLE_CLIENT_ID": "cid.apps.googleusercontent.com", "GOOGLE_CLIENT_SECRET": "secret",
        "DATA_DIR": str(tmp_path),
    })


class Resp:
    def __init__(self, status_code, payload=None, text="", json_error=None):
        self.status_code, self._payload, self.text = status_code, payload or {}, text
        self._json_error = json_error

    def json(self):
        if self._json_error:
            raise self._json_error
        return self._payload


class Sess:
    def __init__(self, resp=None, error=None):
        self.resp, self.error = resp, error

    def get(self, url, timeout):
        if self.error:
            raise self.error
        return self.resp

    def post(self, url, timeout, json):
        return self.get(url, timeout)


def test_google_http_returns_json():
    assert GoogleHttp(Sess(Resp(200, {"ok": 1}))).post("https://x", {}) == {"ok": 1}


def test_google_http_maps_api_errors():
    with pytest.raises(SourceError, match="403"):
        GoogleHttp(Sess(Resp(403, text="denied"))).get("https://x")


def test_google_http_maps_refresh_errors():
    with pytest.raises(GoogleAuthError, match="invalid_grant"):
        GoogleHttp(Sess(error=RefreshError("invalid_grant"))).get("https://x")


def test_google_http_revoked_token_is_an_auth_error_with_readable_message():
    err = RefreshError("invalid_grant: Token has been expired or revoked.", {"error": "invalid_grant"})
    with pytest.raises(GoogleAuthError) as info:
        GoogleHttp(Sess(error=err)).get("https://x")
    assert "invalid_grant: Token has been expired or revoked." in str(info.value)
    assert "{" not in str(info.value)


def test_google_http_retryable_refresh_error_is_only_a_source_error():
    err = RefreshError("temporarily_unavailable", retryable=True)
    with pytest.raises(SourceError, match="temporarily_unavailable") as info:
        GoogleHttp(Sess(error=err)).get("https://x")
    assert not isinstance(info.value, GoogleAuthError)


def test_google_http_non_json_success_body_is_a_source_error():
    resp = Resp(200, text="<html>oops</html>", json_error=ValueError("bad json"))
    with pytest.raises(SourceError, match="https://x") as info:
        GoogleHttp(Sess(resp)).get("https://x")
    assert "oops" not in str(info.value)


@pytest.mark.parametrize("url,expected", [
    ("http://localhost:8000", True),
    ("http://127.0.0.1", True),
    ("http://127.0.0.1:8000", True),
    ("http://[::1]:8000", True),
    ("http://reports.example.com", False),
    ("http://localhost.evil.com", False),
    ("https://localhost", False),
    ("https://reports.example.com", False),
])
def test_is_local_http(url, expected):
    assert _is_local_http(url) is expected


def test_finish_flow_rejects_empty_state_or_verifier(settings):
    with pytest.raises(GoogleAuthError, match="sign-in"):
        finish_flow(settings, "", "verifier", "http://localhost:8000/auth/google/callback?code=c")
    with pytest.raises(GoogleAuthError, match="sign-in"):
        finish_flow(settings, "state", "", "http://localhost:8000/auth/google/callback?code=c")


class FakeFlow:
    def __init__(self, refresh_token):
        self.credentials = type("C", (), {"refresh_token": refresh_token, "scopes": ["openid"]})()

    def fetch_token(self, authorization_response):
        pass


def test_finish_flow_requires_a_refresh_token(settings, monkeypatch):
    monkeypatch.setattr(google_oauth, "_flow", lambda *a, **k: FakeFlow(None))
    with pytest.raises(GoogleAuthError, match="did not return a refresh token"):
        finish_flow(settings, "state", "verifier", "http://localhost:8000/cb?code=c")


def test_finish_flow_only_relaxes_transport_for_local_http(settings, monkeypatch):
    monkeypatch.delenv("OAUTHLIB_INSECURE_TRANSPORT", raising=False)
    monkeypatch.setattr(google_oauth, "_flow", lambda *a, **k: FakeFlow(None))
    remote = replace(settings, base_url="http://reports.example.com")
    with pytest.raises(GoogleAuthError):
        finish_flow(remote, "state", "verifier", "http://x/cb?code=c")
    assert "OAUTHLIB_INSECURE_TRANSPORT" not in google_oauth.os.environ


def test_start_flow_asks_for_offline_access(settings):
    url, state, verifier = start_flow(settings)
    query = parse_qs(urlparse(url).query)
    assert url.startswith("https://accounts.google.com/")
    assert query["access_type"] == ["offline"]
    assert query["prompt"] == ["consent"]
    assert query["redirect_uri"] == ["http://localhost:8000/auth/google/callback"]
    assert "code_challenge" in query
    assert state and verifier


def test_save_connection_encrypts_token(settings):
    with Session(make_engine(settings.data_dir)) as session:
        conn = save_connection(session, settings, "me@agency.com", "refresh-1", "openid")
        assert conn.refresh_token_enc != "refresh-1"
        assert crypto.decrypt(settings.app_secret_key, conn.refresh_token_enc) == "refresh-1"
        assert get_google(session).status == "ok"


def test_make_http_needs_a_connection(settings):
    with Session(make_engine(settings.data_dir)) as session:
        with pytest.raises(GoogleAuthError, match="not connected"):
            make_http(session, settings)


def test_make_http_refuses_broken_connection(settings):
    with Session(make_engine(settings.data_dir)) as session:
        session.add(GoogleConnection(email="me@agency.com", refresh_token_enc="x", status="broken",
                                     last_error="invalid_grant"))
        session.commit()
        with pytest.raises(GoogleAuthError, match="stopped working"):
            make_http(session, settings)


def test_make_http_builds_client(settings):
    with Session(make_engine(settings.data_dir)) as session:
        save_connection(session, settings, "me@agency.com", "refresh-1", "openid")
        assert isinstance(make_http(session, settings), GoogleHttp)


def test_make_http_wrong_secret_key_is_a_clear_auth_error(settings):
    with Session(make_engine(settings.data_dir)) as session:
        save_connection(session, settings, "me@agency.com", "refresh-1", "openid")
        other = replace(settings, app_secret_key=Fernet.generate_key().decode())
        with pytest.raises(GoogleAuthError, match="APP_SECRET_KEY may have changed"):
            make_http(session, other)
