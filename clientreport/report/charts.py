"""Report charts drawn with the nopurple chart style, returned as PNG bytes."""

from io import BytesIO

import matplotlib.pyplot as plt

from clientreport.months import short_label
from clientreport.report import chart_style as cs


def _theme(accent_hex: str) -> str:
    cs.THEMES["agency"] = {**cs.THEMES["swiss"], "accent": accent_hex}
    return "agency"


def _png(make_figure, title: str, kicker: str) -> bytes:
    """Draw, style and save one figure, and close it even when drawing raises.

    pyplot keeps global state, so callers hold render's lock; closing "all" covers a failure inside
    make_figure before it hands the figure back.
    """
    fig = None
    try:
        fig, _ = make_figure()
        cs.finish(fig, title=title, kicker=kicker)
        buf = BytesIO()
        fig.savefig(buf, format="png", dpi=150)
        return buf.getvalue()
    finally:
        plt.close(fig if fig is not None else "all")


def channels_chart(channels: list[tuple[str, int]], accent_hex: str, title: str, kicker: str) -> bytes | None:
    if not channels or not any(n for _, n in channels):
        return None
    labels = [name for name, _ in channels]
    values = [n for _, n in channels]
    return _png(lambda: cs.hbar(labels, values, highlight=labels[0], theme=_theme(accent_hex), size="wide"),
                title, kicker)


def trend_chart(trend: list[tuple[str, int]], accent_hex: str, title: str, kicker: str) -> bytes | None:
    values = [v for _, v in trend]
    if not any(values):
        return None
    x = [short_label(m) for m, _ in trend]
    return _png(lambda: cs.line(x, {"total": values}, theme=_theme(accent_hex), size="wide"), title, kicker)
