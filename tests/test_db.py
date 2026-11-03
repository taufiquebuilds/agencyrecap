import pytest
from sqlalchemy.exc import IntegrityError
from sqlmodel import Session

from clientreport.db import Client, ReportRun, get_agency, make_engine


def make_client(**overrides):
    values = dict(name="Acme Ltd", recipients="ceo@acme.com", ga4_property_id="111", gsc_site_url="sc-domain:acme.com")
    values.update(overrides)
    return Client(**values)


def test_agency_row_is_created_once(tmp_path):
    engine = make_engine(tmp_path)
    with Session(engine) as session:
        agency = get_agency(session)
        agency.name = "Our Agency"
        session.add(agency)
        session.commit()
        assert get_agency(session).name == "Our Agency"


def test_recipient_list_trims_and_skips_blanks():
    client = make_client(recipients=" a@x.com, b@x.com ,")
    assert client.recipient_list() == ["a@x.com", "b@x.com"]


def test_one_run_per_client_and_month(tmp_path):
    engine = make_engine(tmp_path)
    with Session(engine) as session:
        client = make_client()
        session.add(client)
        session.commit()
        session.refresh(client)
        session.add(ReportRun(client_id=client.id, period="2026-10"))
        session.commit()
        session.add(ReportRun(client_id=client.id, period="2026-10"))
        with pytest.raises(IntegrityError):
            session.commit()
