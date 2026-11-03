"""Google sign-in: OAuth flow, encrypted token storage and an HTTP client for the APIs."""

import os
from urllib.parse import urlparse

import requests
from cryptography.fernet import InvalidToken
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
            reason = str(exc.args[0]) if exc.args else "no reason given"
            if getattr(exc, "retryable", False):  # Google had a hiccup; the token itself is fine
                raise SourceError(f"Google sign-in is temporarily unavailable: {reason}") from exc
            raise GoogleAuthError(f"Google refused the saved sign-in: {reason}") from exc
        except (TransportError, requests.RequestException) as exc:
            raise SourceError(f"Could not reach Google: {exc}") from exc
        if resp.status_code >= 400:
            raise SourceError(f"Google API error {resp.status_code} for {url}: {resp.text[:300]}")
        try:
            return resp.json()
        except ValueError as exc:
            raise SourceError(f"Google returned a reply that is not JSON for {url}") from exc


def _is_local_http(base_url: str) -> bool:
    parsed = urlparse(base_url)
    return parsed.scheme == "http" and parsed.hostname in {"localhost", "127.0.0.1", "::1"}


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
    if not state or not code_verifier:
        raise GoogleAuthError("The Google sign-in session is missing or expired. Click Connect Google again.")
    if _is_local_http(settings.base_url):
        os.environ["OAUTHLIB_INSECURE_TRANSPORT"] = "1"  # plain http is fine on this machine only
    os.environ["OAUTHLIB_RELAX_TOKEN_SCOPE"] = "1"  # Google may list granted scopes differently
    flow = _flow(settings, state=state, code_verifier=code_verifier)
    flow.fetch_token(authorization_response=authorization_response)
    creds = flow.credentials
    if not creds.refresh_token:
        raise GoogleAuthError(
            "Google did not return a refresh token. Remove the app's access in your Google account "
            "and connect again.")
    email = AuthorizedSession(creds).get(USERINFO_URL, timeout=30).json().get("email", "")
    # creds.scopes is what we asked for; granted_scopes is what Google actually granted (None if it sent no scope field)
    granted = creds.granted_scopes if creds.granted_scopes is not None else (creds.scopes or SCOPES)
    return email, creds.refresh_token, " ".join(granted)


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
    try:
        refresh_token = crypto.decrypt(settings.app_secret_key, conn.refresh_token_enc)
    except InvalidToken as exc:
        raise GoogleAuthError(
            "The saved Google connection can't be read. APP_SECRET_KEY may have changed. "
            "Connect Google again.") from exc
    creds = Credentials(
        token=None,
        refresh_token=refresh_token,
        token_uri=TOKEN_URI,
        client_id=settings.google_client_id,
        client_secret=settings.google_client_secret,
        scopes=conn.scopes.split(),
    )
    return GoogleHttp(AuthorizedSession(creds))
