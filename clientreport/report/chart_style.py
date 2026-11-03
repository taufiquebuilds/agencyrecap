"""nopurple chart style for matplotlib: one accent, direct labels, honest scales.

Usage:
    import sys; sys.path.insert(0, "<skill-dir>/scripts")
    from chart_style import hbar, bar, line, slope, finish

    fig, ax = hbar(["Google Ads", "Organic", "Referrals"], [86, 54, 31], highlight="Google Ads")
    finish(fig, kicker="Leads by channel, October 2026",
           title="Google Ads brought 42% of all leads: 86 of 204",
           source="<where the user said the data is from, or Sample data.>",
       path="leads.png")

Every chart function returns (fig, ax) so you can adjust anything afterwards.
Requires matplotlib.
"""

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402

# Same palettes as nopurple-slides (templates/deck.html).
THEMES = {
    "paper": {"bg": "#f6f4ef", "fg": "#1b1b1b", "muted": "#6f6a61", "faint": "#d8d3c8", "accent": "#d9480f"},
    "ink": {"bg": "#111316", "fg": "#f2f2ee", "muted": "#9a9c9f", "faint": "#3a3f45", "accent": "#7bdcb5"},
    "swiss": {"bg": "#ffffff", "fg": "#0a0a0a", "muted": "#6b6b6b", "faint": "#dedede", "accent": "#e10600"},
    "forest": {"bg": "#f3f5f0", "fg": "#16241c", "muted": "#5d6b62", "faint": "#cfd8cc", "accent": "#1f7a4d"},
    "sand": {"bg": "#fbf7f0", "fg": "#2a2118", "muted": "#7d6e5c", "faint": "#e5d9c5", "accent": "#2f5bd3"},
    "mono": {"bg": "#fafafa", "fg": "#111111", "muted": "#6a6a6a", "faint": "#d9d9d9", "accent": "#111111"},
}
SIZES = {"slide": (12.8, 7.2), "wide": (12.8, 5.4), "square": (8, 8), "post": (10.8, 13.5)}
FONTS = ["Helvetica Neue", "Helvetica", "Arial", "DejaVu Sans"]

_current = {"theme": THEMES["paper"]}


def use_theme(name="paper"):
    """Apply a theme to matplotlib's defaults. Returns the colour dict."""
    t = THEMES[name]
    _current["theme"] = t
    plt.rcParams.update({
        "font.family": "sans-serif", "font.sans-serif": FONTS, "font.size": 15,
        "figure.facecolor": t["bg"], "axes.facecolor": t["bg"], "savefig.facecolor": t["bg"],
        "text.color": t["fg"], "axes.labelcolor": t["muted"],
        "xtick.color": t["muted"], "ytick.color": t["fg"],
        "axes.spines.top": False, "axes.spines.right": False,
        "axes.spines.left": False, "axes.spines.bottom": False,
        "axes.grid": False, "legend.frameon": False,
        "xtick.major.size": 0, "ytick.major.size": 0,
    })
    return t


def _plain(text):
    """Escape $ so matplotlib shows "$41 to $17" as text, not as maths."""
    return str(text).replace("$", r"\$")


def _fmt(v, fmt):
    return _plain(fmt(v) if callable(fmt) else fmt.format(v))


def _spread(values, min_gap):
    """Return label positions near `values`, pushed apart so none are closer than `min_gap`."""
    order = sorted(range(len(values)), key=lambda i: values[i])
    pos = [0.0] * len(values)
    last = None
    for i in order:
        y = values[i] if last is None else max(values[i], last + min_gap)
        pos[i] = y
        last = y
    # Shift the whole stack down by half its overflow so labels stay centred on the data.
    overflow = sum(pos[i] - values[i] for i in order) / max(1, len(order))
    return [p - overflow / 2 for p in pos]


def _new(size, theme):
    t = use_theme(theme)
    fig, ax = plt.subplots(figsize=SIZES.get(size, size))
    return fig, ax, t


def hbar(labels, values, highlight=None, theme="paper", size="slide", fmt="{:,}", sort=True):
    """Horizontal bars for ranking categories, largest first, one highlighted."""
    fig, ax, t = _new(size, theme)
    pairs = list(zip(labels, values))
    if sort:
        pairs.sort(key=lambda p: p[1], reverse=True)
    names = [p[0] for p in pairs][::-1]
    vals = [p[1] for p in pairs][::-1]
    colors = [t["accent"] if n == highlight else t["faint"] for n in names]
    bars = ax.barh(names, vals, color=colors, height=0.62)
    for b, n, v in zip(bars, names, vals):
        ax.text(b.get_width() + max(vals) * 0.012, b.get_y() + b.get_height() / 2, _fmt(v, fmt),
                va="center", fontsize=16, color=t["fg"], fontweight="bold" if n == highlight else "normal")
    ax.set_xlim(0, max(vals) * 1.12)
    ax.set_xticks([])
    ax.tick_params(axis="y", labelsize=16)
    return fig, ax


def bar(labels, values, highlight=None, theme="paper", size="slide", fmt="{:,}"):
    """Vertical bars for a few periods or categories. Always starts at zero."""
    fig, ax, t = _new(size, theme)
    colors = [t["accent"] if lab == highlight else t["faint"] for lab in labels]
    bars = ax.bar(labels, values, color=colors, width=0.62)
    for b, lab, v in zip(bars, labels, values):
        ax.text(b.get_x() + b.get_width() / 2, b.get_height() + max(values) * 0.015, _fmt(v, fmt),
                ha="center", va="bottom", fontsize=16, color=t["fg"],
                fontweight="bold" if lab == highlight else "normal")
    ax.set_ylim(0, max(values) * 1.15)
    ax.set_yticks([])
    ax.tick_params(axis="x", labelsize=15, colors=t["fg"])
    return fig, ax


def line(x, series, highlight=None, theme="paper", size="slide", fmt="{:,}", zero=True):
    """Lines over time with labels at the line ends instead of a legend.

    series: dict of name -> list of values (same length as x).
    """
    fig, ax, t = _new(size, theme)
    lo = min(min(v) for v in series.values())
    hi = max(max(v) for v in series.values())
    ends = _spread([ys[-1] for ys in series.values()], (hi - min(lo, 0)) * 0.06)
    for (name, ys), y_label in zip(series.items(), ends):
        is_hi = name == highlight or (highlight is None and len(series) == 1)
        color = t["accent"] if is_hi else t["faint"]
        ax.plot(x, ys, color=color, linewidth=3.2 if is_hi else 2, zorder=3 if is_hi else 2)
        ax.scatter([x[-1]], [ys[-1]], color=color, s=40, zorder=4)
        label = f"{_plain(name)}  {_fmt(ys[-1], fmt)}" if len(series) > 1 else _fmt(ys[-1], fmt)
        ax.annotate(label, (x[-1], ys[-1]), xytext=(len(x) - 1 + 0.12, y_label), textcoords="data",
                    va="center", fontsize=15, color=t["fg"] if is_hi else t["muted"],
                    fontweight="bold" if is_hi else "normal", annotation_clip=False)
    ax.set_ylim(0 if zero else lo - (hi - lo) * 0.1, hi * 1.12)
    ax.spines["bottom"].set_visible(True)
    ax.spines["bottom"].set_color(t["faint"])
    ax.tick_params(axis="y", colors=t["muted"], labelsize=13)
    ax.tick_params(axis="x", labelsize=13)
    ax.margins(x=0.02)
    plt.subplots_adjust(right=0.82)
    return fig, ax


def slope(labels, before, after, highlight=(), theme="paper", size="slide",
          left="Before", right="After", fmt="{:,}"):
    """Before/after for several items: one line per item between two columns.

    All items must share one unit and scale (for example cost per lead for each
    campaign). For metrics with different units, chart the % change with hbar().
    """
    fig, ax, t = _new(size, theme)
    highlight = {highlight} if isinstance(highlight, str) else set(highlight)
    span = max(max(before), max(after)) - min(min(before), min(after)) or 1
    left_y = _spread(list(before), span * 0.07)
    right_y = _spread(list(after), span * 0.07)
    for lab, b, a, ly, ry in zip(labels, before, after, left_y, right_y):
        is_hi = lab in highlight
        color = t["accent"] if is_hi else t["faint"]
        ax.plot([0, 1], [b, a], color=color, linewidth=3 if is_hi else 2, marker="o", zorder=3 if is_hi else 2)
        weight = "bold" if is_hi else "normal"
        ax.text(-0.04, ly, f"{_plain(lab)}  {_fmt(b, fmt)}", ha="right", va="center", fontsize=14,
                color=t["fg"] if is_hi else t["muted"], fontweight=weight)
        ax.text(1.04, ry, _fmt(a, fmt), ha="left", va="center", fontsize=14,
                color=t["fg"] if is_hi else t["muted"], fontweight=weight)
    ax.set_xlim(-0.6, 1.3)
    ax.set_xticks([0, 1], [left, right])
    ax.tick_params(axis="x", labelsize=15, colors=t["fg"])
    ax.set_yticks([])
    return fig, ax


def finish(fig, title, kicker=None, source=None, path=None, dpi=200):
    """Add the kicker, headline and source line, then save if `path` is given."""
    t = _current["theme"]
    top = 0.78 if kicker else 0.82
    fig.subplots_adjust(top=top, bottom=0.12 if source else 0.08, left=0.17)
    if kicker:
        fig.text(0.04, 0.93, _plain(kicker.upper()), fontsize=13, fontweight="bold", color=t["muted"])
    fig.text(0.04, 0.85 if kicker else 0.9, _plain(title), fontsize=24, fontweight="bold", color=t["fg"],
             wrap=True)
    if source:
        fig.text(0.04, 0.03, _plain(source), fontsize=12, color=t["muted"])
    if path:
        fig.savefig(path, dpi=dpi)
    return fig
