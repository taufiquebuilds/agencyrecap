"""Dashboard and settings pages."""

import re

from fastapi import APIRouter, Depends, Request
from fastapi.responses import RedirectResponse
from sqlmodel import Session, select

from clientreport.db import Client, ReportRun, get_agency, get_google
from clientreport.google_oauth import redirect_uri
from clientreport.months import label, previous_month
from clientreport.routes.common import db, render, require_login

router = APIRouter(dependencies=[Depends(require_login)])

HEX = re.compile(r"^#[0-9a-fA-F]{6}$")
MAX_LOGO_BYTES = 1_000_000
LOGO_TYPES = {"image/png": ".png", "image/svg+xml": ".svg"}


@router.get("/")
def dashboard(request: Request, session: Session = Depends(db)):
    period = previous_month(request.app.state.deps.today())
    clients = session.exec(select(Client).order_by(Client.name)).all()
    runs = {run.client_id: run for run in session.exec(select(ReportRun).where(ReportRun.period == period)).all()}
    return render(request, "dashboard.html", clients=clients, runs=runs, month=label(period),
                  google=get_google(session))


def _settings_page(request: Request, session: Session, status_code: int = 200, error: str | None = None):
    return render(
        request, "settings.html", status_code=status_code,
        agency=get_agency(session), google=get_google(session),
        redirect_uri=redirect_uri(request.app.state.settings),
        saved=request.query_params.get("saved"), connected=request.query_params.get("connected"),
        error=error,
    )


@router.get("/settings")
def settings_page(request: Request, session: Session = Depends(db)):
    return _settings_page(request, session)


@router.post("/settings")
async def save_settings(request: Request, session: Session = Depends(db)):
    form = await request.form()
    agency = get_agency(session)

    accent = str(form.get("accent_hex", "")).strip()
    if not HEX.match(accent):
        return _settings_page(request, session, 422, "Accent colour must look like #1f7a4d.")

    logo = form.get("logo")
    if logo is not None and getattr(logo, "filename", ""):
        ext = LOGO_TYPES.get(logo.content_type)
        if ext is None:
            return _settings_page(request, session, 422, "Logo must be a PNG or SVG file.")
        data = await logo.read(MAX_LOGO_BYTES + 1)  # never read more than one byte past the limit
        if len(data) > MAX_LOGO_BYTES:
            return _settings_page(request, session, 422, "Logo must be 1 MB or smaller.")
        # The stored name is fixed (logo.png or logo.svg); the uploaded filename is never used.
        path = request.app.state.settings.data_dir / f"logo{ext}"
        path.write_bytes(data)
        agency.logo_path = str(path)

    agency.name = str(form.get("name", "")).strip() or agency.name
    agency.accent_hex = accent
    agency.owner_email = str(form.get("owner_email", "")).strip()
    agency.reply_to = str(form.get("reply_to", "")).strip()
    agency.footer = str(form.get("footer", "")).strip()
    session.add(agency)
    session.commit()
    return RedirectResponse("/settings?saved=1", status_code=303)
