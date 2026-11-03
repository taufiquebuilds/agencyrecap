"""Helpers shared by all routes."""

from collections.abc import Iterator
from datetime import UTC, datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from fastapi import HTTPException, Request
from fastapi.templating import Jinja2Templates
from sqlmodel import Session

templates = Jinja2Templates(directory=str(Path(__file__).resolve().parent.parent / "templates"))


def local_time(value: datetime, tz: str, fmt: str = "%-d %b, %H:%M %Z") -> str:
    """Show a stored UTC time in the agency's timezone. SQLite returns datetimes without a zone: that is UTC."""
    if value.tzinfo is None:
        value = value.replace(tzinfo=UTC)
    return value.astimezone(ZoneInfo(tz)).strftime(fmt)


templates.env.filters["local_time"] = local_time


def db(request: Request) -> Iterator[Session]:
    with Session(request.app.state.engine, expire_on_commit=False) as session:
        yield session


def require_login(request: Request) -> None:
    if not request.session.get("admin"):
        raise HTTPException(status_code=303, headers={"Location": "/login"})


def render(request: Request, name: str, status_code: int = 200, **context):
    context.setdefault("tz", request.app.state.settings.tz)
    return templates.TemplateResponse(request, name, context, status_code=status_code)


def error_page(request: Request, message: str, status_code: int = 400):
    return render(request, "error.html", status_code=status_code, message=message)


def has_control_chars(value: str) -> bool:
    """True for CR, LF, tabs and any other ASCII control character (which break mail headers)."""
    return any(ord(ch) < 32 or ord(ch) == 127 for ch in value)
