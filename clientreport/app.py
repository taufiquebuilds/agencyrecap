"""FastAPI app: wires settings, database, scheduler and routes."""

from contextlib import asynccontextmanager
from datetime import datetime
from zoneinfo import ZoneInfo

from fastapi import FastAPI
from starlette.middleware.sessions import SessionMiddleware

from clientreport import mail, scheduler
from clientreport.config import Settings
from clientreport.db import make_engine
from clientreport.google_oauth import make_http
from clientreport.pipeline import Deps
from clientreport.routes import auth, clients, google, pages


def default_deps(settings: Settings) -> Deps:
    smtp = mail.SmtpConfig(settings.smtp_host, settings.smtp_port, settings.smtp_user,
                           settings.smtp_password, settings.from_email)
    return Deps(
        data_dir=settings.data_dir,
        from_email=settings.from_email,
        http_factory=lambda session: make_http(session, settings),
        send_mail=lambda message: mail.send(message, smtp),
        today=lambda: datetime.now(ZoneInfo(settings.tz)).date(),
    )


def create_app(settings: Settings, deps: Deps | None = None, start_scheduler: bool = True) -> FastAPI:
    engine = make_engine(settings.data_dir)
    deps = deps or default_deps(settings)

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        app.state.scheduler = scheduler.start_scheduler(engine, deps, settings.tz) if start_scheduler else None
        try:
            yield
        finally:
            if app.state.scheduler is not None:
                app.state.scheduler.shutdown(wait=False)

    app = FastAPI(title="clientreport", lifespan=lifespan, docs_url=None, redoc_url=None, openapi_url=None)
    app.add_middleware(SessionMiddleware, secret_key=settings.app_secret_key, same_site="lax",
                       https_only=settings.base_url.startswith("https://"))
    app.state.settings = settings
    app.state.engine = engine
    app.state.deps = deps
    app.state.scheduler = None
    app.state.login_failures = {}
    app.include_router(auth.router)
    app.include_router(pages.router)
    app.include_router(google.router)
    app.include_router(clients.router)
    return app
