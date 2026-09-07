"""
charts.py — tiny inline-SVG chart helpers, no external JS/CSS/fonts (spec_v2 §5 rule: no external
resource loads). Used by build_site.py (task R2's Strategy Decay Index page) and seo_pages.py
(task R2's per-strategy OOS-PF sparkline). Pure string templating, like the rest of this codebase's
HTML generation — nothing here computes a number that feeds a verdict; these are display-only.
"""
from __future__ import annotations
import html


def _lerp(v, vmin, vmax, out_min, out_max):
    if vmax <= vmin:
        return (out_min + out_max) / 2
    return out_min + (v - vmin) / (vmax - vmin) * (out_max - out_min)


def sparkline_svg(values: list, width: int = 96, height: int = 26,
                   color: str = "var(--link)") -> str:
    """A minimal, axis-free line+dots sparkline over `values` (a list of floats/None, in order).
    Returns "" if fewer than 2 non-None points (nothing meaningful to draw as a line) — callers
    that only want to show this from >=3 points (task R2) enforce that threshold themselves before
    calling in, so this stays a generic, reusable-at-2+ primitive."""
    pad = 3
    pts = [(i, v) for i, v in enumerate(values) if v is not None]
    if len(pts) < 2:
        return ""
    vals = [v for _, v in pts]
    vmin, vmax = min(vals), max(vals)
    n = len(values)

    def x_at(i):
        return pad if n == 1 else pad + i * (width - 2 * pad) / (n - 1)

    def y_at(v):
        return height - pad - _lerp(v, vmin, vmax, 0, height - 2 * pad)

    coords = [(x_at(i), y_at(v)) for i, v in pts]
    poly = " ".join(f"{x:.1f},{y:.1f}" for x, y in coords)
    circles = "".join(
        f'<circle cx="{x:.1f}" cy="{y:.1f}" r="1.7" fill="{color}"/>' for x, y in coords
    )
    return (f'<svg width="{width}" height="{height}" viewBox="0 0 {width} {height}" '
            f'role="img" aria-label="trend sparkline">'
            f'<polyline points="{poly}" fill="none" stroke="{color}" stroke-width="1.4"/>'
            f'{circles}</svg>')


def line_chart_svg(points: list, width: int = 560, height: int = 200,
                    color: str = "var(--dead-fg)", y_max: float | None = None,
                    y_fmt=lambda v: f"{v * 100:.0f}%") -> str:
    """points: list of (label: str, value: float|None), in x-axis order. A small line-and-dot
    chart with a 0-baseline, a top y-axis label, and every point's own value in a native SVG
    <title> tooltip (no JS/hover library — the same convention this codebase already uses for the
    robustness-grid tooltip in build_site._robustness_badge_html). y_max defaults to the largest
    value present, floored at a sane minimum so a single 0% point doesn't divide by zero.
    Returns a "not enough data yet" <p> instead of an <svg> when there is nothing plottable."""
    valid = [(i, v) for i, (_lbl, v) in enumerate(points) if v is not None]
    if not valid:
        return '<p class="meta">Not enough data yet.</p>'

    pad_l, pad_r, pad_t, pad_b = 42, 12, 14, 26
    plot_w = max(1, width - pad_l - pad_r)
    plot_h = max(1, height - pad_t - pad_b)
    vmax = y_max if y_max is not None else max(0.05, max(v for _, v in valid))
    n = len(points)

    def x_at(i):
        return pad_l if n == 1 else pad_l + i * plot_w / (n - 1)

    def y_at(v):
        return pad_t + plot_h - _lerp(v, 0, vmax, 0, plot_h)

    coords = [(x_at(i), y_at(v)) for i, v in valid]
    line_svg = ""
    if len(coords) > 1:
        poly = " ".join(f"{x:.1f},{y:.1f}" for x, y in coords)
        line_svg = f'<polyline points="{poly}" fill="none" stroke="{color}" stroke-width="2"/>'
    circles = "".join(
        f'<circle cx="{x:.1f}" cy="{y:.1f}" r="3.2" fill="{color}">'
        f'<title>{html.escape(points[i][0])}: {html.escape(y_fmt(v))}</title></circle>'
        for (i, v), (x, y) in zip(valid, coords)
    )
    axis = (f'<line x1="{pad_l}" y1="{pad_t + plot_h:.1f}" x2="{pad_l + plot_w}" '
            f'y2="{pad_t + plot_h:.1f}" stroke="var(--border)" stroke-width="1"/>')
    y0_label = (f'<text x="{pad_l - 6}" y="{pad_t + plot_h:.1f}" text-anchor="end" '
                f'font-size="10" fill="var(--muted)">0%</text>')
    ymax_label = (f'<text x="{pad_l - 6}" y="{pad_t + 8}" text-anchor="end" font-size="10" '
                  f'fill="var(--muted)">{html.escape(y_fmt(vmax))}</text>')
    x_labels = ""
    first_i = valid[0][0]
    x_labels += (f'<text x="{x_at(first_i):.1f}" y="{height - 6}" text-anchor="middle" '
                 f'font-size="9" fill="var(--muted)">{html.escape(points[first_i][0])}</text>')
    if len(valid) > 1:
        last_i = valid[-1][0]
        x_labels += (f'<text x="{x_at(last_i):.1f}" y="{height - 6}" text-anchor="middle" '
                     f'font-size="9" fill="var(--muted)">{html.escape(points[last_i][0])}</text>')
    return (f'<svg width="{width}" height="{height}" viewBox="0 0 {width} {height}" '
            f'role="img" aria-label="chart">{axis}{line_svg}{circles}{y0_label}{ymax_label}'
            f'{x_labels}</svg>')
