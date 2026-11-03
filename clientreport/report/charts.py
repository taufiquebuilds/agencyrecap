"""Report charts drawn with the nopurple chart style, returned as PNG bytes."""

from io import BytesIO

import matplotlib.pyplot as plt

from clientreport.months import short_label
from clientreport.report import chart_style as cs


def _theme(accent_hex: str) -> str:
    cs.THEMES["agency"] = {**cs.THEMES["swiss"], "accent": accent_hex}
    return "agency"


def _png(fig) -> bytes:
    buf = BytesIO()
    fig.savefig(buf, format="png", dpi=150)
    plt.close(fig)
    return buf.getvalue()


def channels_chart(channels: list[tuple[str, int]], accent_hex: str, title: str, kicker: str) -> bytes | None:
    if not channels or not any(n for _, n in channels):
        return None
    labels = [name for name, _ in channels]
    values = [n for _, n in channels]
    fig, _ = cs.hbar(labels, values, highlight=labels[0], theme=_theme(accent_hex), size="wide")
    cs.finish(fig, title=title, kicker=kicker)
    return _png(fig)


def trend_chart(trend: list[tuple[str, int]], accent_hex: str, title: str, kicker: str) -> bytes | None:
    values = [v for _, v in trend]
    if not any(values):
        return None
    x = [short_label(m) for m, _ in trend]
    fig, _ = cs.line(x, {"total": values}, theme=_theme(accent_hex), size="wide")
    cs.finish(fig, title=title, kicker=kicker)
    return _png(fig)
