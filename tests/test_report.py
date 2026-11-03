from io import BytesIO

from fakes import FakeHttp
from pypdf import PdfReader

from clientreport.connectors import ga4, gsc
from clientreport.report import charts
from clientreport.report.render import Branding, render_report

BRANDING = Branding("Our Agency", "", "#1f7a4d", "Our Agency · hello@agency.com")


def sample(empty=False):
    http = FakeHttp(empty=empty)
    return ga4.fetch_month(http, "111", "2026-10"), gsc.fetch_month(http, "sc-domain:acme.com", "2026-10")


def test_charts_are_png():
    g, _ = sample()
    png = charts.channels_chart(g.channels, "#1f7a4d", "Title", "Kicker")
    assert png[:8] == b"\x89PNG\r\n\x1a\n"
    assert charts.trend_chart(g.trend, "#1f7a4d", "Title", "Kicker")[:4] == b"\x89PNG"


def test_charts_skip_empty_data():
    g, _ = sample(empty=True)
    assert charts.channels_chart(g.channels, "#1f7a4d", "t", "k") is None
    assert charts.trend_chart(g.trend, "#1f7a4d", "t", "k") is None


def test_report_has_three_pages_and_headlines():
    g, s = sample()
    report = render_report("Acme Ltd", "2026-10", g, s, BRANDING)
    reader = PdfReader(BytesIO(report.pdf))
    assert len(reader.pages) == 3
    first_page = reader.pages[0].extract_text()
    assert "Acme Ltd" in first_page
    assert "October 2026" in first_page
    assert "Sessions up 9%: 1,200" in first_page
    assert [h.sentence for h in report.headlines][2] == "Organic clicks up 25%: 500"
    assert "acme widgets" in reader.pages[2].extract_text()


def test_report_with_no_data_still_renders():
    g, s = sample(empty=True)
    report = render_report("Acme Ltd", "2026-10", g, s, BRANDING)
    assert len(PdfReader(BytesIO(report.pdf)).pages) == 3
    assert "No sessions recorded this month." in report.html
