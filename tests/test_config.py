import pytest
from cryptography.fernet import Fernet

from clientreport import crypto
from clientreport.config import load_settings

KEY = Fernet.generate_key().decode()
BASE = {"BASE_URL": "http://localhost:8000/", "ADMIN_PASSWORD": "test-password-10", "APP_SECRET_KEY": KEY}


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


@pytest.mark.parametrize("password", ["change-me", "short", "123456789"])
def test_placeholder_or_short_admin_password_is_refused(password):
    with pytest.raises(RuntimeError, match="at least 10 characters"):
        load_settings({**BASE, "ADMIN_PASSWORD": password})


def test_ten_character_admin_password_is_accepted():
    assert load_settings({**BASE, "ADMIN_PASSWORD": "1234567890"}).admin_password == "1234567890"


def test_empty_tz_and_data_dir_fall_back_to_defaults():
    s = load_settings({**BASE, "TZ": "", "DATA_DIR": ""})
    assert s.tz == "UTC"
    assert str(s.data_dir) == "/data"


def test_valid_tz_is_kept():
    assert load_settings({**BASE, "TZ": "Asia/Kolkata"}).tz == "Asia/Kolkata"


@pytest.mark.parametrize("tz", ["Mars/Olympus", "not a zone", "../etc/passwd"])
def test_invalid_tz_is_refused_at_load_time(tz):
    with pytest.raises(RuntimeError, match="TZ"):
        load_settings({**BASE, "TZ": tz})
