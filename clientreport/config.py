"""Settings from environment variables."""

import os
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path

from cryptography.fernet import Fernet

REQUIRED = ("BASE_URL", "ADMIN_PASSWORD", "APP_SECRET_KEY")


@dataclass(frozen=True)
class Settings:
    base_url: str
    admin_password: str
    app_secret_key: str
    google_client_id: str
    google_client_secret: str
    smtp_host: str
    smtp_port: int
    smtp_user: str
    smtp_password: str
    from_email: str
    tz: str
    data_dir: Path


def load_settings(env: Mapping[str, str] = os.environ) -> Settings:
    missing = [name for name in REQUIRED if not env.get(name)]
    if missing:
        raise RuntimeError("Missing environment variables: " + ", ".join(missing))
    key = env["APP_SECRET_KEY"]
    try:
        Fernet(key.encode())
    except (ValueError, TypeError) as exc:
        raise RuntimeError(
            "APP_SECRET_KEY must be a Fernet key. Make one with: "
            'python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"'
        ) from exc
    return Settings(
        base_url=env["BASE_URL"].rstrip("/"),
        admin_password=env["ADMIN_PASSWORD"],
        app_secret_key=key,
        google_client_id=env.get("GOOGLE_CLIENT_ID", ""),
        google_client_secret=env.get("GOOGLE_CLIENT_SECRET", ""),
        smtp_host=env.get("SMTP_HOST", ""),
        smtp_port=int(env.get("SMTP_PORT", "587")),
        smtp_user=env.get("SMTP_USER", ""),
        smtp_password=env.get("SMTP_PASSWORD", ""),
        from_email=env.get("FROM_EMAIL", ""),
        tz=env.get("TZ", "UTC"),
        data_dir=Path(env.get("DATA_DIR", "/data")),
    )
