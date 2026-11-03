from io import BytesIO

import matplotlib.pyplot as plt
import pytest

from fakes import FakeHttp
from pypdf import PdfReader

from clientreport.connectors import ga4, gsc
from clientreport.report import chart_style, charts, render
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


def test_render_report_draws_charts_while_holding_the_render_lock(monkeypatch):
    g, s = sample()
    seen = []
    real_channels, real_trend = charts.channels_chart, charts.trend_chart

    def channels(*args):
        seen.append(render._render_lock.locked())
        return real_channels(*args)

    def trend(*args):
        seen.append(render._render_lock.locked())
        return real_trend(*args)

    monkeypatch.setattr(charts, "channels_chart", channels)
    monkeypatch.setattr(charts, "trend_chart", trend)
    render_report("Acme Ltd", "2026-10", g, s, BRANDING)
    assert seen == [True, True, True]
    assert not render._render_lock.locked()


def test_render_lock_is_released_when_rendering_fails(monkeypatch):
    g, s = sample()

    def boom(*args):
        raise RuntimeError("chart exploded")

    monkeypatch.setattr(charts, "channels_chart", boom)
    with pytest.raises(RuntimeError):
        render_report("Acme Ltd", "2026-10", g, s, BRANDING)
    assert not render._render_lock.locked()


@pytest.mark.parametrize("draw", [
    lambda g: charts.channels_chart(g.channels, "#1f7a4d", "t", "k"),
    lambda g: charts.trend_chart(g.trend, "#1f7a4d", "t", "k"),
])
def test_chart_figure_is_closed_when_styling_raises(monkeypatch, draw):
    g, _ = sample()
    plt.close("all")

    def boom(*args, **kwargs):
        raise RuntimeError("style failed")

    monkeypatch.setattr(chart_style, "finish", boom)
    with pytest.raises(RuntimeError):
        draw(g)
    assert plt.get_fignums() == []
