"""Month-over-month maths and headline sentences. Pure functions."""

from dataclasses import dataclass

from clientreport.connectors.base import GA4Month, GSCMonth

FLAT_PCT = 2


@dataclass(frozen=True)
class Headline:
    label: str
    value: int
    prev: int
    change_pct: float | None
    sentence: str


def pct_change(cur: int, prev: int) -> float | None:
    if prev == 0:
        return None
    return (cur - prev) / prev * 100


def _direction(change: float) -> str:
    return f"{'up' if change > 0 else 'down'} {round(abs(change))}%"


def headline(label: str, cur: int, prev: int) -> Headline:
    change = pct_change(cur, prev)
    value = f"{cur:,}"
    if change is None:
        sentence = f"{label}: {value}" if cur == 0 else f"{label}: {value} (none last month)"
    elif abs(change) < FLAT_PCT:
        sentence = f"{label} flat: {value}"
    else:
        sentence = f"{label} {_direction(change)}: {value}"
    return Headline(label, cur, prev, change, sentence)


def headlines(ga4: GA4Month, gsc: GSCMonth) -> list[Headline]:
    return [
        headline("Sessions", ga4.sessions, ga4.prev_sessions),
        headline("Key events", ga4.key_events, ga4.prev_key_events),
        headline("Organic clicks", gsc.clicks, gsc.prev_clicks),
    ]


def channel_title(channels: list[tuple[str, int]]) -> str:
    total = sum(n for _, n in channels)
    if total == 0:
        return "No sessions recorded this month"
    name, n = channels[0]
    return f"{name} brought {round(n / total * 100)}% of sessions: {n:,} of {total:,}"


def trend_title(label: str, trend: list[tuple[str, int]]) -> str:
    latest = trend[-1][1]
    change = pct_change(latest, trend[0][1])
    value = f"{latest:,}"
    if change is None:
        return f"{label} in the latest month: {value}"
    if abs(change) < FLAT_PCT:
        return f"{label} flat on a year ago: {value}"
    return f"{label} {_direction(change)} on a year ago: {value}"
