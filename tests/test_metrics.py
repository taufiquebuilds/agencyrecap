from clientreport.connectors.base import GA4Month, GSCMonth
from clientreport.report.metrics import channel_title, headline, headlines, pct_change, trend_title


def test_pct_change():
    assert pct_change(110, 100) == 10
    assert pct_change(5, 0) is None


def test_headline_up():
    assert headline("Sessions", 1200, 1100).sentence == "Sessions up 9%: 1,200"


def test_headline_down():
    assert headline("Key events", 30, 40).sentence == "Key events down 25%: 30"


def test_headline_flat_under_two_percent():
    assert headline("Sessions", 1010, 1000).sentence == "Sessions flat: 1,010"


def test_headline_with_nothing_last_month():
    assert headline("Organic clicks", 50, 0).sentence == "Organic clicks: 50 (none last month)"
    assert headline("Organic clicks", 0, 0).sentence == "Organic clicks: 0"


def test_headlines_order():
    ga4 = GA4Month("2026-10", 1200, 1100, 48, 40, [], [])
    gsc = GSCMonth("2026-10", 500, 400, 21000, 18000, [], [], [])
    assert [h.sentence for h in headlines(ga4, gsc)] == [
        "Sessions up 9%: 1,200",
        "Key events up 20%: 48",
        "Organic clicks up 25%: 500",
    ]


def test_channel_title():
    channels = [("Organic Search", 620), ("Direct", 310), ("Paid Search", 270)]
    assert channel_title(channels) == "Organic Search brought 52% of sessions: 620 of 1,200"


def test_channel_title_with_no_sessions():
    assert channel_title([]) == "No sessions recorded this month"


def test_trend_title_compares_with_a_year_ago():
    trend = [("2025-10", 100)] + [("x", 0)] * 11 + [("2026-10", 500)]
    assert trend_title("Organic clicks", trend) == "Organic clicks up 400% on a year ago: 500"


def test_trend_title_without_history():
    assert trend_title("Sessions", [("2025-10", 0), ("2026-10", 500)]) == "Sessions in the latest month: 500"
