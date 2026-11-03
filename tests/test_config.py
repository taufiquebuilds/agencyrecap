import pytest
from cryptography.fernet import Fernet

from clientreport import crypto
from clientreport.config import load_settings

KEY = Fernet.generate_key().decode()
BASE = {"BASE_URL": "http://localhost:8000/", "ADMIN_PASSWORD": "pw", "APP_SECRET_KEY": KEY}


def test_load_settings_defaults():
    s = load_settings(BASE)
    assert s.base_url == "http://localhost:8000"
    assert s.smtp_port == 587
    assert s.tz == "UTC"
    assert str(s.data_dir) == "/data"
    assert s.google_client_id == ""


def test_missing_required_variable():
    with pytest.raises(RuntimeError, match="ADMIN_PASSWORD"):
        load_settings({"BASE_URL": "http://x", "APP_SECRET_KEY": KEY})


def test_secret_key_must_be_fernet_key():
    with pytest.raises(RuntimeError, match="Fernet"):
        load_settings({**BASE, "APP_SECRET_KEY": "short"})


def test_encrypt_roundtrip():
    token = crypto.encrypt(KEY, "refresh-123")
    assert token != "refresh-123"
    assert crypto.decrypt(KEY, token) == "refresh-123"
