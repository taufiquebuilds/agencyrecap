"""Add and edit clients, preview reports and send them."""

import logging

from fastapi import APIRouter, Depends, Request
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import RedirectResponse, Response
from sqlmodel import Session, select

from clientreport.connectors import ga4, gsc
from clientreport.connectors.base import SourceError
from clientreport.db import Client, ReportRun
from clientreport.months import label, previous_month
from clientreport.pipeline import build_report, send_report
from clientreport.routes.common import db, error_page, has_control_chars, render, require_login

logger = logging.getLogger(__name__)
router = APIRouter(dependencies=[Depends(require_login)])

FIELDS = ("name", "recipients", "ga4_property_id", "gsc_site_url", "send_day")
EMPTY = {"name": "", "recipients": "", "ga4_property_id": "", "gsc_site_url": "", "send_day": "4"}


def _sources(request: Request, session: Session):
    # GoogleAuthError (not connected, broken, unreadable token) is a SourceError, so it lands here too.
    try:
        http = request.app.state.deps.http_factory(session)
        return ga4.list_properties(http), gsc.list_sites(http), None
    except SourceError as exc:
        return [], [], str(exc)


def _form(request: Request, session: Session, values: dict, client_id: int | None = None,
          error: str | None = None, status_code: int = 200):
    properties, sites, source_error = _sources(request, session)
    return render(request, "client_form.html", status_code=status_code, values=values, client_id=client_id,
                  properties=properties, sites=sites, source_error=source_error, error=error)


def _read(form) -> dict:
    return {field: str(form.get(field, "")).strip() for field in FIELDS}


def _validate(values: dict) -> str | None:
    if not values["name"]:
        return "Client name is required."
    if has_control_chars(values["name"]) or has_control_chars(values["recipients"]):
        return "Client name and email addresses must not contain line breaks or control characters."
    emails = [e.strip() for e in values["recipients"].split(",") if e.strip()]
    if not emails or any("@" not in e or any(ch.isspace() for ch in e) for e in emails):
        return "Enter at least one valid email address."
    if not values["ga4_property_id"] or not values["gsc_site_url"]:
        return "Choose a GA4 property and a Search Console site."
    day = values["send_day"]
    # isascii: str.isdigit() is also true for characters like "²" that int() cannot parse
    if not (day.isascii() and day.isdigit()) or not 3 <= int(day) <= 28:
        return "Send day must be between 3 and 28."
    return None


def _apply(client: Client, values: dict) -> None:
    client.name = values["name"]
    client.recipients = values["recipients"]
    client.ga4_property_id = values["ga4_property_id"]
    client.gsc_site_url = values["gsc_site_url"]
    client.send_day = int(values["send_day"])


@router.get("/clients/new")
def new_client(request: Request, session: Session = Depends(db)):
    return _form(request, session, dict(EMPTY))


@router.post("/clients")
async def create_client(request: Request, session: Session = Depends(db)):
    values = _read(await request.form())
    error = _validate(values)
    if error:  # _form calls Google, which blocks, so keep it off the event loop
        return await run_in_threadpool(_form, request, session, values, error=error, status_code=422)
    client = Client(name="", recipients="", ga4_property_id="", gsc_site_url="")
    _apply(client, values)
    session.add(client)
    session.commit()
    return RedirectResponse("/", status_code=303)


@router.get("/clients/{client_id}/edit")
def edit_client(request: Request, client_id: int, session: Session = Depends(db)):
    client = session.get(Client, client_id)
    if client is None:
        return error_page(request, "Client not found.", 404)
    values = {field: str(getattr(client, field)) for field in FIELDS}
    return _form(request, session, values, client_id=client_id)


@router.post("/clients/{client_id}")
async def update_client(request: Request, client_id: int, session: Session = Depends(db)):
    client = session.get(Client, client_id)
    if client is None:
        return error_page(request, "Client not found.", 404)
    values = _read(await request.form())
    error = _validate(values)
    if error:
        return await run_in_threadpool(_form, request, session, values, client_id=client_id, error=error,
                                       status_code=422)
    _apply(client, values)
    session.add(client)
    session.commit()
    return RedirectResponse("/", status_code=303)


@router.post("/clients/{client_id}/toggle")
def toggle_client(request: Request, client_id: int, session: Session = Depends(db)):
    client = session.get(Client, client_id)
    if client is None:
        return error_page(request, "Client not found.", 404)
    client.enabled = not client.enabled
    client.enabled_at = request.app.state.deps.today() if client.enabled else None
    session.add(client)
    session.commit()
    return RedirectResponse("/", status_code=303)


@router.get("/clients/{client_id}/preview")
async def preview(request: Request, client_id: int, session: Session = Depends(db)):
    client = session.get(Client, client_id)
    if client is None:
        return error_page(request, "Client not found.", 404)
    deps = request.app.state.deps
    period = previous_month(deps.today())
    try:
        report = await run_in_threadpool(build_report, session, client, period, deps)
    except SourceError as exc:
        return error_page(request, f"Could not build the report: {exc}", 502)
    except Exception:
        logger.exception("Preview failed for client %s", client_id)
        return error_page(request, "The preview could not be built. Check the server log.", 500)
    return Response(report.pdf, media_type="application/pdf",
                    headers={"Content-Disposition": f'inline; filename="preview-{period}.pdf"'})


@router.post("/clients/{client_id}/send")
async def send_now(request: Request, client_id: int, session: Session = Depends(db)):
    client = session.get(Client, client_id)
    if client is None:
        return error_page(request, "Client not found.", 404)
    form = await request.form()
    deps = request.app.state.deps
    period = previous_month(deps.today())
    run = session.exec(
        select(ReportRun).where(ReportRun.client_id == client.id, ReportRun.period == period)
    ).first()
    confirmed = form.get("confirm") == "yes"
    if run is not None and run.status == "sent" and not confirmed:
        return render(request, "confirm_send.html", client=client, month=label(period), run=run)
    # force only when the admin confirmed; a month that became sent in the meantime is left alone
    await run_in_threadpool(send_report, session, client, period, deps, True, confirmed)
    return RedirectResponse("/", status_code=303)
