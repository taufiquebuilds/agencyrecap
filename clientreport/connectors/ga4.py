"""Google Analytics 4: property list and one month of data."""

from clientreport.connectors.base import GA4Month, Http, Source
from clientreport.months import last_n, month_bounds, shift

DATA_API = "https://analyticsdata.googleapis.com/v1beta"
ADMIN_API = "https://analyticsadmin.googleapis.com/v1beta"


def list_properties(http: Http) -> list[Source]:
    sources: list[Source] = []
    token = ""
    while True:
        url = f"{ADMIN_API}/accountSummaries?pageSize=200"
        if token:
            url += f"&pageToken={token}"
        data = http.get(url)
        for account in data.get("accountSummaries", []):
            for prop in account.get("propertySummaries", []):
                prop_id = prop["property"].split("/")[-1]
                sources.append(Source(prop_id, f"{prop['displayName']} ({account.get('displayName', '')})"))
        token = data.get("nextPageToken", "")
        if not token:
            break
    return sorted(sources, key=lambda s: s.name.lower())


def _range(period: str, name: str | None = None) -> dict:
    start, end = month_bounds(period)
    date_range = {"startDate": start.isoformat(), "endDate": end.isoformat()}
    if name:
        date_range["name"] = name
    return date_range


def _num(row: dict, index: int = 0) -> int:
    return int(float(row["metricValues"][index]["value"]))


def _key(row: dict) -> str:
    return row["dimensionValues"][0]["value"]


def fetch_month(http: Http, property_id: str, period: str) -> GA4Month:
    url = f"{DATA_API}/properties/{property_id}:runReport"

    # With two named date ranges, GA4 adds a dateRange dimension holding the range name.
    totals = http.post(url, {
        "dateRanges": [_range(period, "current"), _range(shift(period, -1), "previous")],
        "metrics": [{"name": "sessions"}, {"name": "keyEvents"}],
    })
    by_range = {_key(row): row for row in totals.get("rows", [])}

    def total(range_name: str, index: int) -> int:
        row = by_range.get(range_name)
        return _num(row, index) if row else 0

    channels = http.post(url, {
        "dateRanges": [_range(period)],
        "dimensions": [{"name": "sessionDefaultChannelGroup"}],
        "metrics": [{"name": "sessions"}],
        "orderBys": [{"metric": {"metricName": "sessions"}, "desc": True}],
        "limit": 10,
    })

    months = last_n(period, 13)
    trend = http.post(url, {
        "dateRanges": [{
            "startDate": month_bounds(months[0])[0].isoformat(),
            "endDate": month_bounds(period)[1].isoformat(),
        }],
        "dimensions": [{"name": "yearMonth"}],
        "metrics": [{"name": "sessions"}],
    })
    per_month = {_key(row): _num(row) for row in trend.get("rows", [])}

    return GA4Month(
        period=period,
        sessions=total("current", 0),
        prev_sessions=total("previous", 0),
        key_events=total("current", 1),
        prev_key_events=total("previous", 1),
        channels=[(_key(row), _num(row)) for row in channels.get("rows", [])],
        trend=[(m, per_month.get(m.replace("-", ""), 0)) for m in months],
    )
