"""
badges.py — embeddable verdict badges (task R3).

Generates flat, shields.io-style SVG badges — a grey "Dead or Alive" label segment plus a
verdict-coloured value segment ("ALIVE (PF 1.55)") — for every (strategy variant, asset,
timeframe) row an edition's payload already computed, plus one edition-wide summary badge
("16 alive / 88"). Pure string templating like every other *.py in this repo that writes HTML/SVG
(build_site.py, seo_pages.py, charts.py) — no external image library, no external font, nothing
network-fetched. Colours are a small local hex palette (VERDICT_BADGE_COLORS below), deliberately
NOT build_site.VERDICT_COLORS — see that constant's own comment for why a var(--...)-based colour
would silently fail once the SVG is embedded outside this site's own pages.

A badge states ONLY this week's automated, out-of-sample, net-of-cost verdict — never an
endorsement, never advice, never "buy"/"sell"/"recommend" language. See docs/registry.html's
"Embed a badge" section for the embedding how-to shown to readers.
"""
from __future__ import annotations
import html
import os
import re

import config

# Standalone-SVG colours, deliberately NOT build_site.VERDICT_COLORS: those are CSS var(--alive-fg)
# etc. references that only resolve inside this site's own pages (where :root defines them) — a
# badge is served and embedded on its own (a README, a third-party page) with no access to that
# stylesheet, so var(--alive-fg) would simply fail to resolve there. Concrete hex values instead,
# chosen for the same verdict semantics (green/amber/grey/light-grey) and legible on both a light
# and a dark host background, the same shields.io-style convention badges are always embedded into.
VERDICT_BADGE_COLORS = {
    "ALIVE": ("#2ea44f", "#ffffff"),
    "FADING": ("#dbab09", "#1a1a1a"),
    "DEAD": ("#6a737d", "#ffffff"),
    "TOO FEW TRADES": ("#959da5", "#ffffff"),
}

# Rough per-character advance width (px) at the 11px label font used below — good enough for a
# "shields.io-style" badge (not a pixel-perfect clone); narrow characters get a smaller advance so
# text doesn't look sparse inside its background rect.
_NARROW = set("iIl1.,:;()| ")
_WIDE = set("mMW")


def _text_width(s: str) -> float:
    w = 0.0
    for ch in s:
        if ch in _NARROW:
            w += 4.2
        elif ch in _WIDE:
            w += 9.5
        elif ch.isupper():
            w += 7.6
        else:
            w += 6.4
    return w


def _slug(text: str) -> str:
    s = re.sub(r"[^a-zA-Z0-9]+", "-", str(text)).strip("-")
    return s or "x"


def badge_filename(strategy_id: str, params_str: str, asset: str, tf: str) -> str:
    """docs/badges/<edition>/<strategy_id>-<params-slug>-<asset>-<tf>.svg (task R3's own naming).
    Only `params_str` is slugged (it can contain a "." — e.g. ema200_macd's "ema200-macd12.26.9" —
    which isn't safe unescaped in a filename); `strategy_id`/`asset`/`tf` are already
    filesystem-safe identifiers (snake_case ids, exchange tickers, "1d"/"4h") and are kept verbatim
    so a badge URL reads the same asset/timeframe spelling the rest of the site uses."""
    return f"{strategy_id}-{_slug(params_str)}-{asset}-{tf}.svg"


def flat_badge_svg(left_label: str, right_label: str, right_bg: str, right_fg: str,
                    left_bg: str = "#555555", left_fg: str = "#ffffff") -> str:
    """A two-segment flat badge, shields.io's own visual convention (rounded corners, a fixed grey
    left/"subject" segment and a coloured right/"status" segment) — not shields.io's code, just its
    look, since this repo loads no external image service (spec_v2 §5: no external resource
    loads)."""
    pad = 6
    left_w = round(_text_width(left_label) + 2 * pad)
    right_w = round(_text_width(right_label) + 2 * pad)
    total_w = left_w + right_w
    height = 20
    r = 3
    left_x = left_w / 2
    right_x = left_w + right_w / 2
    return f"""<svg xmlns="http://www.w3.org/2000/svg" width="{total_w}" height="{height}" role="img" aria-label="{html.escape(left_label)}: {html.escape(right_label)}">
<title>{html.escape(left_label)}: {html.escape(right_label)}</title>
<clipPath id="r"><rect width="{total_w}" height="{height}" rx="{r}" fill="#fff"/></clipPath>
<g clip-path="url(#r)">
<rect width="{left_w}" height="{height}" fill="{left_bg}"/>
<rect x="{left_w}" width="{right_w}" height="{height}" fill="{right_bg}"/>
</g>
<g fill="{left_fg}" text-anchor="middle" font-family="DejaVu Sans,Verdana,Geneva,sans-serif" font-size="11">
<text x="{left_x}" y="14">{html.escape(left_label)}</text>
</g>
<g fill="{right_fg}" text-anchor="middle" font-family="DejaVu Sans,Verdana,Geneva,sans-serif" font-size="11">
<text x="{right_x}" y="14">{html.escape(right_label)}</text>
</g>
</svg>"""


_SUBJECT = "Dead or Alive"


def verdict_badge_svg(verdict: str | None, oos_pf) -> str:
    """The per-(strategy variant, asset, timeframe) badge. `verdict` is None for a reference row
    (buy_and_hold/dca_weekly) — callers never generate one of those (see write_badges_for_payload's
    own reference-row filter), so this only ever renders one of the four real verdict strings."""
    bg, fg = VERDICT_BADGE_COLORS.get(verdict, ("#9e9e9e", "#ffffff"))
    pf_txt = "n/a" if (oos_pf is None or oos_pf == "inf") else f"{oos_pf:.2f}"
    right = f"{verdict} (PF {pf_txt})" if verdict else "no verdict"
    return flat_badge_svg(_SUBJECT, right, bg, fg)


def summary_badge_svg(alive: int, total: int) -> str:
    """The one edition-wide badge: "Dead or Alive | 16 alive / 88" (task R3's own example)."""
    bg, fg = VERDICT_BADGE_COLORS["ALIVE"]
    right = f"{alive} alive / {total}"
    return flat_badge_svg(_SUBJECT, right, bg, fg)


def _badge_rows(payload: dict) -> list:
    return [r for r in ((payload.get("rows") or []) + (payload.get("popular_combos") or []))
            if r.get("type") != "reference"]


def write_badges_for_payload(edition_key: str, payload: dict | None,
                              out_root: str | None = None) -> int:
    """Writes docs/badges/<edition>/<...>.svg for every non-reference row in `payload`, plus
    docs/badges/<edition>/summary.svg from `payload["tally"]`. Returns the count of per-row badges
    written. `payload` may be None (an edition with no data this week, e.g. ko/stocks in this dev
    environment) — nothing is written and 0 is returned, same "skip, don't fail" convention every
    other per-edition writer in this pipeline already follows."""
    if not payload:
        return 0
    out_root = out_root or os.path.join(config.DOCS_DIR, "badges")
    out_dir = os.path.join(out_root, edition_key)
    os.makedirs(out_dir, exist_ok=True)

    n = 0
    for r in _badge_rows(payload):
        filename = badge_filename(r["strategy_id"], r["params"], r["asset"], r["timeframe"])
        svg = verdict_badge_svg(r["verdict"], (r.get("oos") or {}).get("profit_factor"))
        with open(os.path.join(out_dir, filename), "w", encoding="utf-8") as f:
            f.write(svg)
        n += 1

    tally = payload.get("tally") or {}
    alive = tally.get("ALIVE", 0)
    total = sum(tally.values())
    with open(os.path.join(out_dir, "summary.svg"), "w", encoding="utf-8") as f:
        f.write(summary_badge_svg(alive, total))

    return n
