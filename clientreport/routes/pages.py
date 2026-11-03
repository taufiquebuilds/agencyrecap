"""Dashboard and settings pages."""

from fastapi import APIRouter, Depends, Request
from sqlmodel import Session, select

from clientreport.db import Client, ReportRun, get_google
from clientreport.months import label, previous_month
from clientreport.routes.common import db, render, require_login

router = APIRouter(dependencies=[Depends(require_login)])


@router.get("/")
def dashboard(request: Request, session: Session = Depends(db)):
    period = previous_month(request.app.state.deps.today())
    clients = session.exec(select(Client).order_by(Client.name)).all()
    runs = {run.client_id: run for run in session.exec(select(ReportRun).where(ReportRun.period == period)).all()}
    return render(request, "dashboard.html", clients=clients, runs=runs, month=label(period),
                  google=get_google(session))
