"""Google sign-in: OAuth flow, encrypted token storage and an HTTP client for the APIs."""

import os

import requests
from google.auth.exceptions import RefreshError, TransportError
from google.auth.transport.requests import AuthorizedSession
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import Flow
from sqlmodel import Session

from clientreport import crypto
from clientreport.config import Settings
from clientreport.connectors.base import GoogleAuthError, SourceError
from clientreport.db import GoogleConnection, get_google

SCOPES = [
    "openid",
    "https://www.googleapis.com/auth/userinfo.email",
    "https://www.googleapis.com/auth/analytics.readonly",
    "https://www.googleapis.com/auth/webmasters.readonly",
]
AUTH_URI = "https://accounts.google.com/o/oauth2/auth"
TOKEN_URI = "https://oauth2.googleapis.com/token"
USERINFO_URL = "https://openidconnect.googleapis.com/v1/userinfo"


class GoogleHttp:
    """JSON client for Google APIs. Turns failures into SourceError or GoogleAuthError."""

    def __init__(self, session):
        self._session = session

    def get(self, url: str) -> dict:
        return self._call("get", url)

    def post(self, url: str, body: dict) -> dict:
        return self._call("post", url, json=body)

    def _call(self, method: str, url: str, **kwargs) -> dict:
        try:
            resp = getattr(self._session, method)(url, timeout=60, **kwargs)
        except RefreshError as exc:
            raise GoogleAuthError(f"Google refused the saved sign-in: {exc}") from exc
        except (TransportError, requests.RequestException) as exc:
            raise SourceError(f"Could not reach Google: {exc}") from exc
        if resp.status_code >= 400:
            raise SourceError(f"Google API error {resp.status_code} for {url}: {resp.text[:300]}")
        return resp.json()


def redirect_uri(settings: Settings) -> str:
    return f"{settings.base_url}/auth/google/callback"


def _flow(settings: Settings, **kwargs) -> Flow:
    config = {"web": {
        "client_id": settings.google_client_id,
        "client_secret": settings.google_client_secret,
        "auth_uri": AUTH_URI,
        "token_uri": TOKEN_URI,
        "redirect_uris": [redirect_uri(settings)],
    }}
    return Flow.from_client_config(config, scopes=SCOPES, redirect_uri=redirect_uri(settings), **kwargs)


def start_flow(settings: Settings) -> tuple[str, str, str]:
    flow = _flow(settings, autogenerate_code_verifier=True)
    url, state = flow.authorization_url(access_type="offline", prompt="consent", include_granted_scopes="true")
    return url, state, flow.code_verifier


def finish_flow(settings: Settings, state: str, code_verifier: str,
                authorization_response: str) -> tuple[str, str, str]:
    if settings.base_url.startswith("http://"):
        os.environ["OAUTHLIB_INSECURE_TRANSPORT"] = "1"  # local installs without HTTPS
    os.environ["OAUTHLIB_RELAX_TOKEN_SCOPE"] = "1"  # Google may list granted scopes differently
    flow = _flow(settings, state=state, code_verifier=code_verifier)
    flow.fetch_token(authorization_response=authorization_response)
    creds = flow.credentials
    email = AuthorizedSession(creds).get(USERINFO_URL, timeout=30).json().get("email", "")
    return email, creds.refresh_token or "", " ".join(creds.scopes or SCOPES)


def save_connection(session: Session, settings: Settings, email: str, refresh_token: str,
                    scopes: str) -> GoogleConnection:
    conn = get_google(session) or GoogleConnection()
    conn.email = email
    conn.refresh_token_enc = crypto.encrypt(settings.app_secret_key, refresh_token)
    conn.scopes = scopes
    conn.status = "ok"
    conn.last_error = ""
    session.add(conn)
    session.commit()
    session.refresh(conn)
    return conn


def make_http(session: Session, settings: Settings) -> GoogleHttp:
    conn = get_google(session)
    if conn is None or not conn.refresh_token_enc:
        raise GoogleAuthError("Google is not connected. Open Settings and click Connect Google.")
    if conn.status == "broken":
        raise GoogleAuthError(
            f"Google connection stopped working ({conn.last_error}). Open Settings and click Connect Google.")
    creds = Credentials(
        token=None,
        refresh_token=crypto.decrypt(settings.app_secret_key, conn.refresh_token_enc),
        token_uri=TOKEN_URI,
        client_id=settings.google_client_id,
        client_secret=settings.google_client_secret,
        scopes=conn.scopes.split(),
    )
    return GoogleHttp(AuthorizedSession(creds))
