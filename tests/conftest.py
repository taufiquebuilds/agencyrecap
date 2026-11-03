from datetime import date

import pytest
from cryptography.fernet import Fernet
from fakes import FakeHttp, FakeMailer
from fastapi.testclient import TestClient

from clientreport.app import create_app
from clientreport.config import load_settings
from clientreport.pipeline import Deps


@pytest.fixture
def settings(tmp_path):
    return load_settings({
        "BASE_URL": "http://localhost:8000", "ADMIN_PASSWORD": "test-password-10",
        "APP_SECRET_KEY": Fernet.generate_key().decode(),
        "GOOGLE_CLIENT_ID": "cid.apps.googleusercontent.com", "GOOGLE_CLIENT_SECRET": "secret",
        "DATA_DIR": str(tmp_path),
    })


@pytest.fixture
def http():
    return FakeHttp()


@pytest.fixture
def mailer():
    return FakeMailer()


@pytest.fixture
def app(settings, http, mailer):
    deps = Deps(data_dir=settings.data_dir, from_email="reports@agency.com", http_factory=lambda s: http,
                send_mail=mailer, today=lambda: date(2026, 11, 4))
    return create_app(settings, deps=deps, start_scheduler=False)


@pytest.fixture
def web(app):
    with TestClient(app) as client:
        client.post("/login", data={"password": "test-password-10"})
        yield client
