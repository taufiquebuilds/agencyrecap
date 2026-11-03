from fakes import FakeHttp

from clientreport.connectors import ga4
from clientreport.connectors.base import Source


def test_list_properties():
    assert ga4.list_properties(FakeHttp()) == [
        Source("222", "Acme Blog (Acme Ltd)"),
        Source("111", "acme.com (Acme Ltd)"),
    ]


def test_fetch_month():
    m = ga4.fetch_month(FakeHttp(), "111", "2026-10")
    assert (m.sessions, m.prev_sessions) == (1200, 1100)
    assert (m.key_events, m.prev_key_events) == (48, 40)
    assert m.channels[0] == ("Organic Search", 620)
    assert len(m.channels) == 5
    assert len(m.trend) == 13
    assert m.trend[0] == ("2025-10", 900)
    assert m.trend[1] == ("2025-11", 0)
    assert m.trend[-1] == ("2026-10", 1200)


def test_fetch_month_asks_for_this_and_last_month():
    http = FakeHttp()
    ga4.fetch_month(http, "111", "2026-10")
    url, body = http.posts[0]
    assert url.endswith("/properties/111:runReport")
    assert body["dateRanges"] == [
        {"startDate": "2026-10-01", "endDate": "2026-10-31", "name": "current"},
        {"startDate": "2026-09-01", "endDate": "2026-09-30", "name": "previous"},
    ]
    trend_body = http.posts[2][1]
    assert trend_body["dateRanges"] == [{"startDate": "2025-10-01", "endDate": "2026-10-31"}]


def test_fetch_month_with_no_data():
    m = ga4.fetch_month(FakeHttp(empty=True), "111", "2026-10")
    assert (m.sessions, m.key_events, m.channels) == (0, 0, [])
    assert all(value == 0 for _, value in m.trend)
