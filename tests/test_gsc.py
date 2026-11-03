from fakes import FakeHttp

from clientreport.connectors import gsc
from clientreport.connectors.base import SearchRow, Source


def test_list_sites_skips_unverified():
    assert gsc.list_sites(FakeHttp()) == [Source("sc-domain:acme.com", "sc-domain:acme.com")]


def test_fetch_month():
    m = gsc.fetch_month(FakeHttp(), "sc-domain:acme.com", "2026-10")
    assert (m.clicks, m.prev_clicks) == (500, 400)
    assert (m.impressions, m.prev_impressions) == (21000, 18000)
    assert len(m.trend) == 13
    assert m.trend[0] == ("2025-10", 100)
    assert m.trend[-2] == ("2026-09", 400)
    assert m.trend[-1] == ("2026-10", 500)
    assert m.top_queries[0] == SearchRow("acme widgets", 120, 2400, 0.05, 3.2)
    assert m.top_pages[1].key == "https://acme.com/widgets"


def test_site_url_is_encoded():
    http = FakeHttp()
    gsc.fetch_month(http, "sc-domain:acme.com", "2026-10")
    assert "/sites/sc-domain%3Aacme.com/searchAnalytics/query" in http.posts[0][0]


def test_fetch_month_with_no_data():
    m = gsc.fetch_month(FakeHttp(empty=True), "sc-domain:acme.com", "2026-10")
    assert (m.clicks, m.top_queries, m.top_pages) == (0, [], [])
    assert all(value == 0 for _, value in m.trend)
