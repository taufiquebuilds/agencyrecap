"""Connect the agency's Google account."""

from fastapi import APIRouter, Depends, Request
from fastapi.responses import RedirectResponse
from sqlmodel import Session

from clientreport import google_oauth
from clientreport.routes.common import db, error_page, require_login

router = APIRouter(dependencies=[Depends(require_login)])


@router.get("/auth/google/start")
def google_start(request: Request):
    settings = request.app.state.settings
    if not settings.google_client_id or not settings.google_client_secret:
        return error_page(request, "GOOGLE_CLIENT_ID and GOOGLE_CLIENT_SECRET are not set. See the README.")
    url, state, verifier = google_oauth.start_flow(settings)
    request.session["oauth_state"] = state
    request.session["oauth_verifier"] = verifier
    return RedirectResponse(url, status_code=303)


@router.get("/auth/google/callback")
def google_callback(request: Request, session: Session = Depends(db)):
    settings = request.app.state.settings
    state = request.session.pop("oauth_state", None)
    verifier = request.session.pop("oauth_verifier", None)
    if request.query_params.get("error"):
        return error_page(request, f"Google sign-in was cancelled: {request.query_params['error']}")
    if not state or request.query_params.get("state") != state:
        return error_page(request, "Sign-in expired. Click Connect Google again.")
    callback_url = f"{google_oauth.redirect_uri(settings)}?{request.url.query}"
    try:
        email, refresh_token, scopes = google_oauth.finish_flow(settings, state, verifier, callback_url)
    except Exception as exc:  # oauthlib raises many error types; show the message
        return error_page(request, f"Google sign-in failed: {exc}", 502)
    if not refresh_token:
        return error_page(
            request,
            "Google did not return a refresh token. Remove this app at "
            "myaccount.google.com/permissions, then click Connect Google again.",
        )
    google_oauth.save_connection(session, settings, email, refresh_token, scopes)
    return RedirectResponse("/settings?connected=1", status_code=303)
