"""Admin password login."""

import hmac

from fastapi import APIRouter, Form, Request
from fastapi.responses import RedirectResponse

from clientreport.routes.common import render

router = APIRouter()


@router.get("/login")
def login_page(request: Request):
    return render(request, "login.html", error=None)


@router.post("/login")
def login(request: Request, password: str = Form(...)):
    expected = request.app.state.settings.admin_password
    if hmac.compare_digest(password.encode(), expected.encode()):
        request.session["admin"] = True
        return RedirectResponse("/", status_code=303)
    return render(request, "login.html", status_code=401, error="Wrong password.")


@router.post("/logout")
def logout(request: Request):
    request.session.clear()
    return RedirectResponse("/login", status_code=303)
