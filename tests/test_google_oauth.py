from urllib.parse import parse_qs, urlparse

import pytest
from cryptography.fernet import Fernet
from google.auth.exceptions import RefreshError
from sqlmodel import Session

from clientreport import crypto
from clientreport.config import load_settings
from clientreport.connectors.base import GoogleAuthError, SourceError
from clientreport.db import GoogleConnection, get_google, make_engine
from clientreport.google_oauth import GoogleHttp, make_http, save_connection, start_flow


@pytest.fixture
def settings(tmp_path):
    return load_settings({
        "BASE_URL": "http://localhost:8000", "ADMIN_PASSWORD": "pw",
        "APP_SECRET_KEY": Fernet.generate_key().decode(),
        "GOOGLE_CLIENT_ID": "cid.apps.googleusercontent.com", "GOOGLE_CLIENT_SECRET": "secret",
        "DATA_DIR": str(tmp_path),
    })


class Resp:
    def __init__(self, status_code, payload=None, text=""):
        self.status_code, self._payload, self.text = status_code, payload or {}, text

    def json(self):
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
