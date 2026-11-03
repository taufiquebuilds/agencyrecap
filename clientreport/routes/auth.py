"""Admin password login."""

import hmac
import time

from fastapi import APIRouter, Form, Request
from fastapi.responses import RedirectResponse

from clientreport.routes.common import render

router = APIRouter()

MAX_FAILURES = 5
WINDOW_SECONDS = 15 * 60


def _recent_failures(request: Request, ip: str) -> list[float]:
    """Failed-login times for this IP inside the window; older ones are dropped."""
    failures = request.app.state.login_failures
    cutoff = time.monotonic() - WINDOW_SECONDS
    recent = [t for t in failures.get(ip, []) if t > cutoff]
    if recent:
        failures[ip] = recent
    else:
        failures.pop(ip, None)
    return recent


@router.get("/login")
def login_page(request: Request):
    return render(request, "login.html", error=None)


@router.post("/login")
def login(request: Request, password: str = Form(...)):
    ip = request.client.host if request.client else "unknown"
    failures = _recent_failures(request, ip)
    if len(failures) >= MAX_FAILURES:
        return render(request, "login.html", status_code=429, error="Too many attempts. Try again in 15 minutes.")
    expected = request.app.state.settings.admin_password
    if hmac.compare_digest(password.encode(), expected.encode()):
        request.app.state.login_failures.pop(ip, None)
        request.session["admin"] = True
        return RedirectResponse("/", status_code=303)
    request.app.state.login_failures.setdefault(ip, []).append(time.monotonic())
    return render(request, "login.html", status_code=401, error="Wrong password.")


@router.post("/logout")
def logout(request: Request):
    request.session.clear()
    return RedirectResponse("/login", status_code=303)
