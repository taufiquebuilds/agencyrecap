"""SQLite tables and small helpers."""

from datetime import UTC, date, datetime
from pathlib import Path

from sqlalchemy import UniqueConstraint
from sqlmodel import Field, Session, SQLModel, create_engine


class AgencySettings(SQLModel, table=True):
    id: int = Field(default=1, primary_key=True)
    name: str = "My Agency"
    logo_path: str = ""
    accent_hex: str = "#d9480f"
    owner_email: str = ""
    reply_to: str = ""
    footer: str = ""


class GoogleConnection(SQLModel, table=True):
    id: int = Field(default=1, primary_key=True)
    email: str = ""
    refresh_token_enc: str = ""
    scopes: str = ""
    status: str = "ok"  # "ok" or "broken"
    last_error: str = ""


class Client(SQLModel, table=True):
    id: int | None = Field(default=None, primary_key=True)
    name: str
    recipients: str  # comma-separated emails
    ga4_property_id: str
    gsc_site_url: str
    send_day: int = 4
    enabled: bool = False
    enabled_at: date | None = None
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))

    def recipient_list(self) -> list[str]:
        return [r.strip() for r in self.recipients.split(",") if r.strip()]


class ReportRun(SQLModel, table=True):
    __table_args__ = (UniqueConstraint("client_id", "period"),)

    id: int | None = Field(default=None, primary_key=True)
    client_id: int = Field(foreign_key="client.id")
    period: str  # YYYY-MM
    status: str = "pending"  # "pending", "held", "sent" or "failed"
    attempts: int = 0
    last_error: str = ""
    pdf_path: str = ""
    sent_at: datetime | None = None


def make_engine(data_dir: Path):
    data_dir.mkdir(parents=True, exist_ok=True)
    engine = create_engine(
        f"sqlite:///{data_dir / 'clientreport.db'}", connect_args={"check_same_thread": False}
    )
    SQLModel.metadata.create_all(engine)
    return engine


def get_agency(session: Session) -> AgencySettings:
    agency = session.get(AgencySettings, 1)
    if agency is None:
        agency = AgencySettings()
        session.add(agency)
        session.commit()
        session.refresh(agency)
    return agency


def get_google(session: Session) -> GoogleConnection | None:
    return session.get(GoogleConnection, 1)
