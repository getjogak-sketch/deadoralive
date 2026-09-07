"""
build_site.py — spec_v2 §5 static site generator. Turns results/latest.json (in-memory `payload`
here) into docs/index.html and docs/methodology.html. Pure Python string templating: no external
JS framework, no external CSS/font/JS resources (spec_v2 §5: "외부 리소스 로드 없음").
"""
from __future__ import annotations
import html
import math
import os

import config
import registry as reg

VERDICT_COLORS = {
    "ALIVE": ("var(--alive-bg)", "var(--alive-fg)"),
    "FADING": ("var(--fading-bg)", "var(--fading-fg)"),
    "DEAD": ("var(--dead-bg)", "var(--dead-fg)"),
    "TOO FEW TRADES": ("var(--toofew-bg)", "var(--toofew-fg)"),
}

BASE_CSS = """
:root {
  --bg: #ffffff; --fg: #14181f; --muted: #5b6270; --border: #e2e5ea; --card-bg: #f7f8fa;
  --link: #1a56db;
  --alive-bg: #d9f2e3; --alive-fg: #106a37;
  --fading-bg: #fdf1c8; --fading-fg: #7a5b00;
  --dead-bg: #e6e8eb; --dead-fg: #4b5160;
  --toofew-bg: #f2f3f5; --toofew-fg: #8a909c;
  --suspicious: #c0392b;
}
@media (prefers-color-scheme: dark) {
  :root {
    --bg: #10131a; --fg: #e7e9ee; --muted: #9aa1b0; --border: #2a2f3a; --card-bg: #171b24;
    --link: #7aa2ff;
    --alive-bg: #113a26; --alive-fg: #6bdc9c;
    --fading-bg: #3a3210; --fading-fg: #f0d572;
    --dead-bg: #262a33; --dead-fg: #a8adb8;
    --toofew-bg: #1c2029; --toofew-fg: #6b7180;
    --suspicious: #ff8a7a;
  }
}
* { box-sizing: border-box; }
body { background: var(--bg); color: var(--fg); font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Helvetica, Arial, sans-serif; margin: 0; padding: 0 0 3rem 0; line-height: 1.45; }
a { color: var(--link); }
header.top { padding: 2rem 1.25rem 1.25rem; border-bottom: 1px solid var(--border); }
header.top h1 { margin: 0 0 0.25rem 0; font-size: 1.7rem; }
header.top p.tagline { margin: 0 0 1rem 0; color: var(--muted); }
.meta { color: var(--muted); font-size: 0.9rem; }
.tally { display: flex; gap: 0.6rem; flex-wrap: wrap; margin-top: 1rem; }
.tally span { padding: 0.3rem 0.7rem; border-radius: 999px; font-size: 0.85rem; font-weight: 600; }
main { max-width: 1200px; margin: 0 auto; padding: 0 1.25rem; }
nav.jump { display: flex; gap: 0.75rem; flex-wrap: wrap; margin: 1.25rem 0; }
nav.jump a { padding: 0.3rem 0.7rem; border: 1px solid var(--border); border-radius: 6px; text-decoration: none; font-size: 0.9rem; }
section.assetblock { margin: 2.25rem 0; }
section.assetblock h2 { border-bottom: 2px solid var(--border); padding-bottom: 0.4rem; }
.tablewrap { overflow-x: auto; border: 1px solid var(--border); border-radius: 8px; }
table { border-collapse: collapse; width: 100%; min-width: 980px; font-size: 0.85rem; }
th, td { padding: 0.5rem 0.6rem; text-align: right; white-space: nowrap; border-bottom: 1px solid var(--border); }
th:nth-child(1), td:nth-child(1), th:nth-child(2), td:nth-child(2) { text-align: left; }
thead th { background: var(--card-bg); position: sticky; top: 0; }
tbody tr:hover { background: var(--card-bg); }
.badge { padding: 0.15rem 0.55rem; border-radius: 999px; font-weight: 700; font-size: 0.78rem; display: inline-block; }
.ref-row { font-style: italic; color: var(--muted); }
.suspicious { color: var(--suspicious); font-weight: 700; }
.gross-note { font-size: 0.75rem; color: var(--muted); }
footer.bottom { max-width: 1200px; margin: 2.5rem auto 0; padding: 1.25rem; border-top: 1px solid var(--border); color: var(--muted); font-size: 0.85rem; }
footer.bottom .disclaimer { margin-top: 0.75rem; }
"""


def _check_strategy_url() -> str:
    """spec_v3 §E: the new-issue URL for the "Check my strategy" issue form, built from
    config.REPO_URL — the one and only place this path is assembled, so every page's link stays
    in sync if REPO_URL is ever filled in with the repo's real public address."""
    return config.REPO_URL.rstrip("/") + "/issues/new?template=check-strategy.yml"


def _fmt_pct(x, dp=1):
    if x is None or (isinstance(x, float) and math.isnan(x)):
        return "-"
    return f"{x*100:.{dp}f}%"


def _fmt_pct_already(x, dp=1):
    """x is already a percentage number (e.g. mdd, win_rate are stored as %, not fraction)."""
    if x is None or (isinstance(x, float) and math.isnan(x)):
        return "-"
    return f"{x:.{dp}f}%"


def _fmt_num(x, dp=2):
    if x is None:
        return "-"
    if x == "inf":
        return "&infin;"
    if isinstance(x, float) and math.isnan(x):
        return "-"
    return f"{x:.{dp}f}"


def _fmt_int(x):
    if x is None or (isinstance(x, float) and math.isnan(x)):
        return "-"
    return str(int(x))


def _badge_html(verdict):
    if verdict is None:
        return '<span class="ref-row">reference</span>'
    bg, fg = VERDICT_COLORS.get(verdict, ("var(--card-bg)", "var(--fg)"))
    return f'<span class="badge" style="background:{bg};color:{fg}">{html.escape(verdict)}</span>'


# spec_v3 §C: 3-level colour coding for the robustness column, reusing the exact same badge color
# tokens already used for verdicts (green-ish/amber/grey) rather than inventing a fourth palette.
_ROBUST_HIGH = VERDICT_COLORS["ALIVE"]
_ROBUST_MID = VERDICT_COLORS["FADING"]
_ROBUST_LOW = VERDICT_COLORS["TOO FEW TRADES"]


def _fmt_grid_pf(pf):
    if pf is None:
        return "n/a"
    if pf == "inf":
        return "inf"
    return f"{pf:.2f}"


def _robustness_badge_html(robustness: dict | None) -> str:
    """spec_v3 §C: a small `n/total` badge with a tooltip listing every grid point's own
    (params -> PF, n trades), colour-coded at the 2/3 and 1/3 `share_pf_ge_1` breakpoints. `None`
    or an empty grid (a strategy with no numeric parameter to vary, e.g. `macd`) renders as a
    plain dash — never a badge, so it isn't mistaken for a share of 0."""
    if not robustness or robustness.get("share_pf_ge_1") is None:
        return '<span class="meta">-</span>'
    share = robustness["share_pf_ge_1"]
    n_pass, n_total = robustness["n_pass"], robustness["n_total"]
    if share >= 2 / 3:
        bg, fg = _ROBUST_HIGH
    elif share >= 1 / 3:
        bg, fg = _ROBUST_MID
    else:
        bg, fg = _ROBUST_LOW
    tooltip = "; ".join(
        f"{g['params']} → PF={_fmt_grid_pf(g['oos_pf'])} (n={g['oos_n_trades']})"
        for g in robustness["grid"]
    )
    return (f'<span class="badge robustness-badge" style="background:{bg};color:{fg}" '
            f'title="{html.escape(tooltip)}">{n_pass}/{n_total}</span>')


def _row_html(row):
    is_ = row["is"]
    oos = row["oos"]
    is_ref = row["type"] == "reference"
    cls = ' class="ref-row"' if is_ref else ""
    susp = ' <span class="suspicious" title="OOS PF or Sharpe unusually high — see methodology">&#9888;</span>' if row.get("suspicious") else ""

    oos_return = _fmt_pct(oos.get("total_return"))
    oos_pf = "-" if is_ref else _fmt_num(oos.get("profit_factor"))
    oos_mdd = _fmt_pct_already(oos.get("mdd"))
    oos_trades = "-" if is_ref else _fmt_int(oos.get("n_trades"))
    oos_win = "-" if is_ref else _fmt_pct_already(oos.get("win_rate"))
    is_pf = "-" if is_ref else _fmt_num(is_.get("profit_factor"))
    is_mdd = _fmt_pct_already(is_.get("mdd"))
    bh_oos_return = _fmt_pct(oos.get("bh_return")) if not is_ref else "-"
    bh_oos_mdd = _fmt_pct_already(oos.get("bh_mdd")) if not is_ref else "-"
    fee_drag = _fmt_pct(oos.get("fee_drag"))
    robustness_html = "-" if is_ref else _robustness_badge_html(row.get("robustness"))

    return (
        f"<tr{cls}>"
        f"<td>{html.escape(row['strategy_name'])}{susp}</td>"
        f"<td>{html.escape(row['params'])}</td>"
        f"<td>{_badge_html(row['verdict'])}</td>"
        f"<td>{oos_return}</td>"
        f"<td>{oos_pf}</td>"
        f"<td>{oos_mdd}</td>"
        f"<td>{oos_trades}</td>"
        f"<td>{oos_win}</td>"
        f"<td>{is_pf}</td>"
        f"<td>{is_mdd}</td>"
        f"<td>{bh_oos_return}</td>"
        f"<td>{bh_oos_mdd}</td>"
        f"<td>{fee_drag}</td>"
        f"<td>{robustness_html}</td>"
        f"</tr>"
    )


def _asset_tf_section(asset, tf, rows, as_of):
    anchor = f"{asset}-{tf}"
    body_rows = "\n".join(_row_html(r) for r in rows)
    return f"""
<section class="assetblock" id="{anchor}">
  <h2>{html.escape(asset)} &middot; {html.escape(tf)}</h2>
  <p class="meta">as of {html.escape(as_of)} &mdash; OOS = trailing {config.OOS_DAYS} days, IS = everything before that.</p>
  <div class="tablewrap">
    <table>
      <thead><tr>
        <th>Strategy</th><th>Params</th><th>Verdict</th>
        <th>OOS Return</th><th>OOS PF</th><th>OOS MDD</th><th>OOS Trades</th><th>OOS Win%</th>
        <th>IS PF</th><th>IS MDD</th>
        <th>B&amp;H OOS Return</th><th>B&amp;H OOS MDD</th>
        <th>Fee drag&sup1;</th><th>Robustness&sup2;</th>
      </tr></thead>
      <tbody>
{body_rows}
      </tbody>
    </table>
  </div>
  <p class="gross-note">&sup1; Fee drag = gross (before-fees, for illustration only) OOS return
     &minus; net OOS return. Every other number on this page is net of trading cost.
     &sup2; Robustness = how many of a small grid of nearby parameter values (hover for the list)
     also clear OOS PF &ge; 1.0 with &ge; 10 trades &mdash; diagnostic only, see methodology; it
     never changes the verdict.</p>
</section>"""


# =================================================================================================
# spec_v3 §D "Popular combos" — additive extension, nothing above this line is modified. Reuses
# _row_html/_row_html_ko unchanged (the row shape is identical to the main registry's), just
# grouped into its own separate table per spec_v3 §D ("rendered on all pages as its own table").
# =================================================================================================

def _popular_combos_html(payload: dict, assets: list | None = None, timeframes: list | None = None) -> str:
    assets = assets if assets is not None else config.ASSETS
    timeframes = timeframes if timeframes is not None else config.TIMEFRAMES
    combo_rows = payload.get("popular_combos") or []
    if not combo_rows:
        return ""

    groups = {}
    order = []
    for r in combo_rows:
        key = (r["asset"], r["timeframe"])
        if key not in groups:
            groups[key] = []
            order.append(key)
    order = [k for k in [(a, tf) for a in assets for tf in timeframes] if k in groups] or order
    for r in combo_rows:
        groups[(r["asset"], r["timeframe"])].append(r)

    subsections = "".join(f"""
  <h3>{html.escape(a)} &middot; {html.escape(tf)}</h3>
  <div class="tablewrap">
    <table>
      <thead><tr>
        <th>Strategy</th><th>Params</th><th>Verdict</th>
        <th>OOS Return</th><th>OOS PF</th><th>OOS MDD</th><th>OOS Trades</th><th>OOS Win%</th>
        <th>IS PF</th><th>IS MDD</th>
        <th>B&amp;H OOS Return</th><th>B&amp;H OOS MDD</th>
        <th>Fee drag&sup1;</th><th>Robustness&sup2;</th>
      </tr></thead>
      <tbody>
{chr(10).join(_row_html(r) for r in groups[(a, tf)])}
      </tbody>
    </table>
  </div>""" for a, tf in order)

    return f"""
<section class="assetblock" id="popular-combos">
  <h2>Popular combos &mdash; as commonly taught on YouTube / TradingView</h2>
  <p class="meta">Same execution rules as every strategy above (long-only, always fully invested
     or fully in cash, state decided at the close of bar t and executed at bar t+1's open, net of
     cost) and pre-registered the same way, before any result was computed.</p>
  {subsections}
</section>"""


def build_index(payload: dict, out_path: str | None = None, assets: list | None = None,
                 timeframes: list | None = None, lang_links: str | None = None):
    """spec_v3 §A extension (additive): `assets`/`timeframes`/`lang_links` let a second edition
    (the stocks edition — SPY/QQQ, 1d only) reuse this exact template instead of duplicating it,
    per spec_v3 §A's own instruction ("reuse the English builders with an edition parameter").
    All three default to exactly what this function already hard-coded before spec_v3, so the
    English crypto edition's call site (no new args passed) renders byte-for-byte the same rows/
    table structure as before — only its header gained one more nav link (Stocks), which changes
    no number on the page."""
    out_path = out_path or os.path.join(config.DOCS_DIR, "index.html")
    assets = assets if assets is not None else config.ASSETS
    timeframes = timeframes if timeframes is not None else config.TIMEFRAMES
    lang_links = lang_links if lang_links is not None else (
        '<a href="stocks/index.html">Stocks edition</a> &middot; '
        '<a href="ko/index.html">한국어 (Korean edition)</a>'
    )

    groups = {}
    order = []
    for r in payload["rows"]:
        key = (r["asset"], r["timeframe"])
        if key not in groups:
            groups[key] = []
            order.append(key)
    order = [k for k in [(a, tf) for a in assets for tf in timeframes] if k in groups] or order
    for r in payload["rows"]:
        groups[(r["asset"], r["timeframe"])].append(r)

    tally = payload["tally"]
    tally_html = "".join(
        f'<span style="background:{VERDICT_COLORS[k][0]};color:{VERDICT_COLORS[k][1]}">{html.escape(k)} {v}</span>'
        for k, v in tally.items()
    )

    nav_html = "".join(
        f'<a href="#{a}-{tf}">{html.escape(a)} {html.escape(tf)}</a>' for a, tf in order
    )

    sections_html = "".join(_asset_tf_section(a, tf, groups[(a, tf)], payload["as_of"]) for a, tf in order)
    popular_combos_html = _popular_combos_html(payload, assets, timeframes)

    skipped = sorted(set(assets) - {a for a, _tf in order})
    skipped_note = ""
    if skipped:
        skipped_note = (f'<p class="meta">Skipped this run (no local data file): '
                         f'{html.escape(", ".join(skipped))}.</p>')

    signup_html = ""
    if payload.get("signup_url"):
        signup_html = f' &middot; <a href="{html.escape(payload["signup_url"])}">Get weekly updates</a>'
    repo_html = ""
    if payload.get("repo_url"):
        repo_html = f' &middot; <a href="{html.escape(payload["repo_url"])}">Source code</a>'

    doc = f"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{html.escape(payload['project_name'])}</title>
<style>{BASE_CSS}</style>
</head>
<body>
<header class="top">
  <h1>{html.escape(payload['project_name'])}</h1>
  <p class="tagline">{html.escape(payload['tagline'])}</p>
  <p class="meta">as_of: <strong>{html.escape(payload['as_of'])}</strong> &middot; generated {html.escape(payload['generated_at'])}
  &middot; {lang_links}</p>
  <div class="tally">{tally_html}</div>
  {skipped_note}
</header>
<main>
  <nav class="jump">{nav_html}</nav>
  {sections_html}
  {popular_combos_html}
</main>
<footer class="bottom">
  <div><a href="methodology.html">Methodology</a>{repo_html}{signup_html}
  &middot; <a href="{html.escape(_check_strategy_url())}">Check your own strategy</a>
  &middot; <a href="s/index.html">All strategy pages</a></div>
  <div class="disclaimer">{html.escape(payload['legal_disclaimer'])}</div>
</footer>
{config.ANALYTICS_SNIPPET}
</body>
</html>
"""
    os.makedirs(os.path.dirname(out_path), exist_ok=True)
    with open(out_path, "w") as f:
        f.write(doc)
    return out_path


def _registry_table_html():
    rows = []
    for entry in reg.REGISTRY:
        params = ", ".join(v["params_str"] for v in entry["variants"])
        rows.append(
            f"<tr><td>{html.escape(entry['id'])}</td><td>{html.escape(entry['name'])}</td>"
            f"<td>{html.escape(entry['type'])}</td><td>{html.escape(entry['rule'])}</td>"
            f"<td>{html.escape(params)}</td></tr>"
        )
    for entry in reg.REFERENCE:
        rows.append(
            f"<tr class=\"ref-row\"><td>{html.escape(entry['id'])}</td><td>{html.escape(entry['name'])}</td>"
            f"<td>{html.escape(entry['type'])}</td><td>{html.escape(entry['rule'])}</td><td>-</td></tr>"
        )
    return "\n".join(rows)


def _popular_combos_table_html() -> str:
    """spec_v3 §D methodology table — same shape as _registry_table_html above, over
    registry.POPULAR_COMBOS instead of registry.REGISTRY."""
    rows = []
    for entry in reg.POPULAR_COMBOS:
        params = ", ".join(v["params_str"] for v in entry["variants"])
        rows.append(
            f"<tr><td>{html.escape(entry['id'])}</td><td>{html.escape(entry['name'])}</td>"
            f"<td>{html.escape(entry['type'])}</td><td>{html.escape(entry['rule'])}</td>"
            f"<td>{html.escape(params)}</td></tr>"
        )
    return "\n".join(rows)


def build_methodology(out_path: str | None = None, assets: list | None = None,
                       lang_links: str | None = None):
    """spec_v3 §A extension (additive): `assets`/`lang_links` let the stocks edition reuse this
    template (see build_index's docstring above for the same rationale); both default to exactly
    what this function already hard-coded before spec_v3."""
    out_path = out_path or os.path.join(config.DOCS_DIR, "methodology.html")
    assets = assets if assets is not None else config.ASSETS
    lang_links = lang_links if lang_links is not None else (
        '<a href="stocks/methodology.html">Stocks edition</a> &middot; '
        '<a href="ko/methodology.html">한국어 (Korean edition)</a>'
    )
    # Filtered to `assets` (not all of config.COST) so this page's output is unaffected by the
    # other editions' COST entries added alongside it.
    cost_rows = "".join(
        f"<tr><td>{html.escape(a)}</td><td>{config.COST[a]*100:.2f}%</td></tr>"
        for a in assets
    )
    th = config.VERDICT_THRESHOLDS

    doc = f"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{html.escape(config.PROJECT_NAME)} methodology</title>
<style>{BASE_CSS}</style>
</head>
<body>
<header class="top">
  <h1>Methodology</h1>
  <p class="tagline"><a href="index.html">&larr; back to results</a> &middot; {lang_links}</p>
</header>
<main>
  <section class="assetblock">
    <h2>Cost model</h2>
    <p>Costs are one-way and applied to the fill price: buy fill = reference price &times; (1 + cost),
       sell fill = reference price &times; (1 &minus; cost). All reported figures are net of cost
       except the single "Fee drag" column, which is explicitly labeled gross/illustrative.</p>
    <div class="tablewrap"><table><thead><tr><th>Asset</th><th>One-way cost</th></tr></thead>
    <tbody>{cost_rows}</tbody></table></div>
  </section>

  <section class="assetblock">
    <h2>IS / OOS rolling window</h2>
    <p>As of each run's last fully-closed bar (<code>as_of</code>): OOS = the trailing
       {config.OOS_DAYS} days ending at <code>as_of</code>; IS = everything in the data before
       that. The window rolls forward one week at a time as the pipeline re-runs weekly.
       Indicator warm-up (e.g. SMA200) always uses the full price history, not just the IS
       window, so signals are valid from the IS window's very first bar where the lookback
       allows.</p>
  </section>

  <section class="assetblock">
    <h2>Execution rules</h2>
    <p>Common to every strategy: long-only, always fully invested or fully in cash (no leverage,
       no shorting, no partial sizing). A "state" strategy decides its target state (long/flat)
       at the close of bar t using data through bar t, and executes any change at bar t+1's open.
       A "one-bar" strategy (Larry Williams volatility breakout and its trend-filtered variant)
       evaluates an intrabar breakout condition each bar independently and always exits by the
       next bar's open. A "hold-N-bar" strategy (buy-the-dip) enters at the next bar's open after
       its trigger and exits exactly N bars later at that bar's open, regardless of any
       intervening signal.</p>
  </section>

  <section class="assetblock">
    <h2>Verdict badges (OOS, net of cost)</h2>
    <table><tbody>
      <tr><td><strong>ALIVE</strong></td><td>OOS PF &ge; {th['ALIVE_MIN_OOS_PF']} AND OOS trades &ge; {th['ALIVE_MIN_OOS_TRADES']} AND OOS MDD &lt; same-period buy &amp; hold MDD</td></tr>
      <tr><td><strong>FADING</strong></td><td>not ALIVE, but OOS PF &ge; {th['FADING_MIN_OOS_PF']} AND OOS trades &ge; {th['FADING_MIN_OOS_TRADES']}</td></tr>
      <tr><td><strong>DEAD</strong></td><td>OOS PF &lt; {th['DEAD_MAX_OOS_PF']} AND OOS trades &ge; {th['FADING_MIN_OOS_TRADES']}</td></tr>
      <tr><td><strong>TOO FEW TRADES</strong></td><td>OOS trades &lt; {th['TOO_FEW_MIN_OOS_TRADES']}</td></tr>
    </tbody></table>
    <p>Reference rows (buy &amp; hold, weekly DCA) never receive a badge.</p>
  </section>

  <section class="assetblock">
    <h2>Robustness map (diagnostic)</h2>
    <p>Every registered variant with at least one numeric parameter (a window length, a
       multiplier, a threshold) is also re-run, out-of-sample only, at a small grid of nearby
       values &mdash; roughly &plusmn;25% around each registered number (or &plusmn;0.1 for the
       volatility-breakout multiplier <code>k</code>), one parameter combination at a time. The
       "Robustness" column reports what fraction of that grid still clears OOS PF &ge; 1.0 with
       at least 10 trades, e.g. "7/9"; hover it for the full grid. A strategy that only works at
       exactly its registered numbers and falls apart one step away is more likely to be a
       historical coincidence than a real, durable edge &mdash; this column exists to make that
       visible.</p>
    <p><strong>This never changes the verdict.</strong> The grid is read-only: it reuses the same
       simulation engine on the same data, but its output feeds nothing except this one column.
       Verdicts are always computed from &mdash; and only from &mdash; the exact parameter values
       fixed in <code>registry.py</code> before any result was ever seen; the grid can never
       select a "better" parameter or retroactively change what was registered.</p>
  </section>

  <section class="assetblock">
    <h2>Engine honesty checks</h2>
    <p><strong>No-lookahead test</strong>: for every strategy variant in the registry below, the
       signal computed on the full price series is compared against the signal computed on data
       truncated at several cut points T; all values before T must be bit-identical whether or
       not the data after T exists. <strong>Two-engine cross-check</strong> (sma_cross only): the
       hand-written pandas engine is compared against the <code>backtesting.py</code> library on
       identical data and cost; trade counts must match and total return must agree within 2%.
       <strong>Sanity check</strong>: a zero-cost run must never underperform the cost-applied
       run (cost-free figures are used only for this internal check and the fee-drag column,
       never presented as a strategy's own performance).</p>
  </section>

  <section class="assetblock">
    <h2>Strategy registry</h2>
    <div class="tablewrap"><table>
      <thead><tr><th>id</th><th>Name</th><th>Type</th><th>Rule</th><th>Params</th></tr></thead>
      <tbody>{_registry_table_html()}</tbody>
    </table></div>
  </section>

  <section class="assetblock">
    <h2>Popular combos &mdash; as commonly taught on YouTube / TradingView</h2>
    <p>A second, separately pre-registered group (spec_v3 §D) of multi-indicator combinations as
       they are commonly taught in retail trading content, rather than single-indicator textbook
       strategies. Same execution rules, same cost model, same verdict thresholds, same
       robustness map as every strategy above &mdash; rendered in its own table on the results
       page rather than mixed into the main sections, purely for readability.</p>
    <div class="tablewrap"><table>
      <thead><tr><th>id</th><th>Name</th><th>Type</th><th>Rule</th><th>Params</th></tr></thead>
      <tbody>{_popular_combos_table_html()}</tbody>
    </table></div>
  </section>

  <section class="assetblock">
    <h2>Check your own strategy</h2>
    <p>Open a <a href="{html.escape(_check_strategy_url())}">GitHub issue with the "Check my
       strategy" template</a> to run one strategy id/parameter combination through this same
       engine, cost model, and verdict thresholds, against the data already committed to this
       repo. A bot replies on the issue with the scorecard and closes it automatically &mdash;
       there is no server, nothing is stored beyond that comment, and the result is exactly as
       automated and non-advisory as the rest of this page.</p>
  </section>

  <section class="assetblock">
    <h2>What we don't do</h2>
    <p>No parameter tuning, no new filters, no adding a strategy variant after seeing how it
       performs. Every strategy and every parameter value that will ever appear on this site is
       fixed in <code>registry.py</code> before results are computed; a new one can only be added
       going forward, pre-registered, never retroactively re-run against history to cherry-pick
       a good-looking window.</p>
  </section>
</main>
<footer class="bottom">
  <div><a href="index.html">&larr; back to results</a>
  &middot; <a href="{html.escape(_check_strategy_url())}">Check your own strategy</a>
  &middot; <a href="s/index.html">All strategy pages</a></div>
  <div class="disclaimer">{html.escape(config.LEGAL_DISCLAIMER)}</div>
</footer>
{config.ANALYTICS_SNIPPET}
</body>
</html>
"""
    os.makedirs(os.path.dirname(out_path), exist_ok=True)
    with open(out_path, "w") as f:
        f.write(doc)
    return out_path


# =================================================================================================
# Korean edition (docs/ko/) — additive extension. Nothing above this line (build_index,
# build_methodology, BASE_CSS, VERDICT_COLORS, the _fmt_* helpers, which are reused as-is) is
# rewritten; this section only adds Korean-language templates that consume the same payload shape
# run_weekly.py's English-edition block already produces (plus payload["last_price"] and
# payload["legal_disclaimer_ko"], both additive keys — see run_weekly._run_ko_edition).
#
# Mandatory verbatim Korean disclaimer (this task's requirement 4) appears at TOP and BOTTOM of
# every Korean page. Banned words (never to appear anywhere on the Korean page): 추천, 수익 보장,
# 확실, 필승 — every string below was written to avoid all four; tests.py's
# test_korean_page_disclaimer_and_banned_words asserts this holds for the rendered HTML.
# =================================================================================================

VERDICT_LABELS_KO = {
    "ALIVE": "생존",
    "FADING": "약화",
    "DEAD": "사망",
    "TOO FEW TRADES": "표본 부족",
}

# Strategy id -> "Korean name (English name)", per this task's requirement 4.
STRATEGY_NAME_KO = {
    "sma_cross": "단순이동평균 교차 (SMA crossover)",
    "ema_cross": "지수이동평균 교차 (EMA crossover)",
    "above_sma": "이동평균선 위에서만 보유 (Price above SMA)",
    "donchian": "돈치안 채널 돌파, 터틀 방식 (Donchian/Turtle breakout)",
    "vol_breakout": "변동성 돌파 (Larry Williams volatility breakout)",
    "vol_breakout_trend": "변동성 돌파 + 추세 필터 (Volatility breakout + trend filter)",
    "rsi_mr": "RSI 평균회귀 (RSI mean reversion)",
    "rsi2_connors": "코너스 RSI(2) 전략 (Connors RSI(2))",
    "bb_mr": "볼린저밴드 평균회귀 (Bollinger mean reversion)",
    "bb_breakout": "볼린저밴드 돌파 (Bollinger breakout)",
    "macd": "MACD 시그널 교차 (MACD signal cross)",
    "supertrend": "슈퍼트렌드 (Supertrend)",
    "tsmom": "시계열 모멘텀 (Time-series momentum)",
    "dip_3down": "3봉 연속 하락 뒤 매수 (Buy the dip, 3 down closes)",
    "dip_pct": "한 봉에 5% 이상 급락하면 매수 (Buy the dip, -x% bar)",
    "dca_weekly": "매주 일정 금액 적립 매수 — 참고용 (Weekly DCA)",
    "buy_and_hold": "단순 보유 — 참고용 (Buy & hold)",
    # spec_v3 §D "Popular combos" — additive.
    "ema_9_21": "지수이동평균 9/21 교차 (EMA 9/21 crossover)",
    "ema200_macd": "200일 이동평균 추세 + MACD 교차 (EMA200 trend + MACD cross)",
    "rsi_uptrend": "상승 추세 중 RSI 조정 매수 (RSI dip in uptrend)",
    "bb_squeeze": "볼린저밴드 수축 후 돌파 (Bollinger squeeze breakout)",
    "ichimoku_cloud": "일목균형표 구름대 돌파 (Ichimoku cloud breakout)",
    "heikin_ashi_trend": "헤이킨아시 캔들 색 전환 (Heikin-Ashi colour)",
    "supertrend_ema200": "슈퍼트렌드 + 200일 이동평균 필터 (Supertrend + EMA200 filter)",
}

ASSET_LABEL_KO = {
    "KRW-BTC": "KRW-BTC (비트코인)",
    "KRW-ETH": "KRW-ETH (이더리움)",
}

TF_LABEL_KO = {
    "1d": "일봉 (1d)",
    "4h": "4시간봉 (4h)",
}


def _fmt_krw(x):
    """KRW has no minor unit in everyday use — whole-won, comma-grouped, with a ₩ prefix."""
    if x is None or (isinstance(x, float) and math.isnan(x)):
        return "-"
    return f"₩{x:,.0f}"


def _disclaimer_block_ko(css_class="disclaimer-block"):
    return (f'<div class="{css_class}">{html.escape(config.LEGAL_DISCLAIMER_KO)}</div>')


KO_EXTRA_CSS = """
.regtable th, .regtable td { text-align: left; white-space: normal; }
.regtable td:nth-child(4) { white-space: nowrap; }

.disclaimer-block { max-width: 1200px; margin: 1rem auto; padding: 0.9rem 1.1rem; border: 1px solid var(--border); border-radius: 8px; background: var(--card-bg); color: var(--muted); font-size: 0.82rem; line-height: 1.5; }
.notice-box { max-width: 1200px; margin: 2rem auto; padding: 2rem 1.5rem; border: 1px dashed var(--border); border-radius: 10px; text-align: center; }
.notice-box h2 { margin-top: 0; }
.badge small { font-weight: 500; opacity: 0.75; margin-left: 0.3rem; font-size: 0.72em; }
"""


def _badge_html_ko(verdict):
    if verdict is None:
        return '<span class="ref-row">참고</span>'
    bg, fg = VERDICT_COLORS.get(verdict, ("var(--card-bg)", "var(--fg)"))
    label_ko = VERDICT_LABELS_KO.get(verdict, verdict)
    return (f'<span class="badge" style="background:{bg};color:{fg}">'
            f'{html.escape(label_ko)}<small>{html.escape(verdict)}</small></span>')


def _row_html_ko(row):
    is_ = row["is"]
    oos = row["oos"]
    is_ref = row["type"] == "reference"
    cls = ' class="ref-row"' if is_ref else ""
    susp = (' <span class="suspicious" title="수치가 비정상적으로 높습니다. 표본이 적을 때 흔한 착시이니 '
            '방법론을 참고하세요">&#9888;</span>') if row.get("suspicious") else ""

    name_ko = STRATEGY_NAME_KO.get(row["strategy_id"], row["strategy_name"])

    oos_return = _fmt_pct(oos.get("total_return"))
    oos_pf = "-" if is_ref else _fmt_num(oos.get("profit_factor"))
    oos_mdd = _fmt_pct_already(oos.get("mdd"))
    oos_trades = "-" if is_ref else _fmt_int(oos.get("n_trades"))
    oos_win = "-" if is_ref else _fmt_pct_already(oos.get("win_rate"))
    is_pf = "-" if is_ref else _fmt_num(is_.get("profit_factor"))
    is_mdd = _fmt_pct_already(is_.get("mdd"))
    bh_oos_return = _fmt_pct(oos.get("bh_return")) if not is_ref else "-"
    bh_oos_mdd = _fmt_pct_already(oos.get("bh_mdd")) if not is_ref else "-"
    fee_drag = _fmt_pct(oos.get("fee_drag"))
    robustness_html = "-" if is_ref else _robustness_badge_html(row.get("robustness"))

    return (
        f"<tr{cls}>"
        f"<td>{html.escape(name_ko)}{susp}</td>"
        f"<td>{html.escape(row['params'])}</td>"
        f"<td>{_badge_html_ko(row['verdict'])}</td>"
        f"<td>{oos_return}</td>"
        f"<td>{oos_pf}</td>"
        f"<td>{oos_mdd}</td>"
        f"<td>{oos_trades}</td>"
        f"<td>{oos_win}</td>"
        f"<td>{is_pf}</td>"
        f"<td>{is_mdd}</td>"
        f"<td>{bh_oos_return}</td>"
        f"<td>{bh_oos_mdd}</td>"
        f"<td>{fee_drag}</td>"
        f"<td>{robustness_html}</td>"
        f"</tr>"
    )


def _asset_tf_section_ko(asset, tf, rows, as_of, last_price):
    anchor = f"{asset}-{tf}"
    body_rows = "\n".join(_row_html_ko(r) for r in rows)
    asset_label = ASSET_LABEL_KO.get(asset, asset)
    tf_label = TF_LABEL_KO.get(tf, tf)
    price = last_price.get(f"{asset}_{tf}") if last_price else None
    price_note = f" &middot; 최근 종가 {_fmt_krw(price)}" if price is not None else ""
    return f"""
<section class="assetblock" id="{anchor}">
  <h2>{html.escape(asset_label)} &middot; {html.escape(tf_label)}</h2>
  <p class="meta">기준일 {html.escape(as_of)}{price_note} &mdash; 최근 {config.OOS_DAYS}일을
     시험 구간(OOS), 그 이전 전체를 학습 구간(IS)으로 나누어 봅니다. 판정은 시험 구간만 봅니다.</p>
  <div class="tablewrap">
    <table>
      <thead><tr>
        <th>전략</th><th>설정값</th><th>판정</th>
        <th>최근 2년 수익률</th><th>PF</th><th>최대 낙폭</th><th>거래 횟수</th><th>승률</th>
        <th>이전 기간 PF</th><th>이전 기간 최대 낙폭</th>
        <th>단순 보유 수익률</th><th>단순 보유 최대 낙폭</th>
        <th>수수료로 사라진 수익&sup1;</th><th>주변 설정값 안정성&sup2;</th>
      </tr></thead>
      <tbody>
{body_rows}
      </tbody>
    </table>
  </div>
  <p class="gross-note">&sup1; 수수료가 없다고 가정했을 때의 수익률에서 실제 수익률을 뺀 값입니다(참고용).
     이 열을 제외한 모든 숫자는 수수료(편도 0.10%)를 뗀 뒤의 값입니다. PF(Profit Factor)는 이긴 거래의
     이익 합계를 진 거래의 손실 합계로 나눈 값으로, 1.0이면 본전입니다. 최대 낙폭(MDD)은 고점 대비
     가장 많이 빠졌던 비율입니다. &sup2; 등록된 설정값 근처의 값들로도 같은 조건(PF 1.0 이상, 거래
     10회 이상)을 통과하는 비율입니다(마우스를 올리면 목록이 나옵니다) — 참고용이며 판정에는 전혀
     반영되지 않습니다.</p>
</section>"""


def _popular_combos_html_ko(payload: dict) -> str:
    """Korean edition of _popular_combos_html — same structure, Korean labels/title (spec_v3 §D:
    "유튜브·트레이딩뷰에서 많이 가르치는 조합 전략")."""
    combo_rows = payload.get("popular_combos") or []
    if not combo_rows:
        return ""

    groups = {}
    order = []
    for r in combo_rows:
        key = (r["asset"], r["timeframe"])
        if key not in groups:
            groups[key] = []
            order.append(key)
    canonical_order = [(a, tf) for a in config.UPBIT_ASSETS for tf in config.TIMEFRAMES]
    order = [k for k in canonical_order if k in groups] or order
    for r in combo_rows:
        groups[(r["asset"], r["timeframe"])].append(r)

    subsections = "".join(f"""
  <h3>{html.escape(ASSET_LABEL_KO.get(a, a))} &middot; {html.escape(TF_LABEL_KO.get(tf, tf))}</h3>
  <div class="tablewrap">
    <table>
      <thead><tr>
        <th>전략</th><th>설정값</th><th>판정</th>
        <th>최근 2년 수익률</th><th>PF</th><th>최대 낙폭</th><th>거래 횟수</th><th>승률</th>
        <th>이전 기간 PF</th><th>이전 기간 최대 낙폭</th>
        <th>단순 보유 수익률</th><th>단순 보유 최대 낙폭</th>
        <th>수수료로 사라진 수익</th><th>주변 설정값 안정성</th>
      </tr></thead>
      <tbody>
{chr(10).join(_row_html_ko(r) for r in groups[(a, tf)])}
      </tbody>
    </table>
  </div>""" for a, tf in order)

    return f"""
<section class="assetblock" id="popular-combos">
  <h2>유튜브·트레이딩뷰에서 많이 가르치는 조합 전략</h2>
  <p class="meta">위의 전략들과 사고파는 규칙은 완전히 같습니다(매수만, 전액 보유 또는 전액 현금,
     봉이 닫힌 뒤 판단해서 다음 봉 시가에 체결, 수수료 뗀 뒤 값). 이 조합들도 결과를 보기 전에
     미리 등록해 둔 것입니다.</p>
  {subsections}
</section>"""


def build_index_ko(payload: dict, out_path: str | None = None):
    """Korean edition of build_index — same layout/columns as the English page, all UI text in
    Korean, verbatim disclaimer top and bottom (requirement 4)."""
    out_path = out_path or os.path.join(config.DOCS_DIR_KO, "index.html")

    groups = {}
    order = []
    for r in payload["rows"]:
        key = (r["asset"], r["timeframe"])
        if key not in groups:
            groups[key] = []
            order.append(key)
    canonical_order = [(a, tf) for a in config.UPBIT_ASSETS for tf in config.TIMEFRAMES]
    order = [k for k in canonical_order if k in groups] or order
    for r in payload["rows"]:
        groups[(r["asset"], r["timeframe"])].append(r)

    tally = payload["tally"]
    tally_html = "".join(
        f'<span style="background:{VERDICT_COLORS[k][0]};color:{VERDICT_COLORS[k][1]}">'
        f'{html.escape(VERDICT_LABELS_KO.get(k, k))} {v}</span>'
        for k, v in tally.items()
    )

    nav_html = "".join(
        f'<a href="#{a}-{tf}">{html.escape(ASSET_LABEL_KO.get(a, a))} &middot; '
        f'{html.escape(TF_LABEL_KO.get(tf, tf))}</a>'
        for a, tf in order
    )

    last_price = payload.get("last_price") or {}
    sections_html = "".join(
        _asset_tf_section_ko(a, tf, groups[(a, tf)], payload["as_of"], last_price) for a, tf in order
    )
    popular_combos_html = _popular_combos_html_ko(payload)

    skipped = sorted(set(config.UPBIT_ASSETS) - {a for a, _tf in order})
    skipped_note = ""
    if skipped:
        skipped_note = (f'<p class="meta">이번 주에는 시세 데이터를 받지 못해 건너뛴 자산: '
                         f'{html.escape(", ".join(ASSET_LABEL_KO.get(a, a) for a in skipped))}.</p>')

    signup_html = ""
    if payload.get("signup_url"):
        signup_html = f' &middot; <a href="{html.escape(payload["signup_url"])}">매주 결과 받아보기</a>'
    repo_html = ""
    if payload.get("repo_url"):
        repo_html = f' &middot; <a href="{html.escape(payload["repo_url"])}">소스 코드</a>'

    doc = f"""<!doctype html>
<html lang="ko">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{html.escape(config.PROJECT_TITLE_KO)}</title>
<style>{BASE_CSS}{KO_EXTRA_CSS}</style>
</head>
<body>
{_disclaimer_block_ko()}
<header class="top">
  <h1>{html.escape(payload['project_name'])}</h1>
  <p class="tagline">{html.escape(payload.get('tagline') or config.TAGLINE_KO)}</p>
  <p class="meta">기준일 <strong>{html.escape(payload['as_of'])}</strong> &middot;
     매주 월요일 오전 9시 30분(한국 시간)에 자동으로 다시 계산합니다 &middot; <a href="../index.html">English</a> &middot; <a href="../stocks/index.html">Stocks</a></p>
  <div class="tally">{tally_html}</div>
  {skipped_note}
</header>
<main>
  <nav class="jump">{nav_html}</nav>
  {sections_html}
  {popular_combos_html}
</main>
<footer class="bottom">
  <div><a href="methodology.html">어떻게 계산했나</a>{repo_html}{signup_html} &middot; <a href="../index.html">English</a> &middot; <a href="../stocks/index.html">Stocks</a>
  &middot; <a href="{html.escape(_check_strategy_url())}">내 전략도 검사해 보기</a>
  &middot; <a href="s/index.html">전략별 페이지 전체</a></div>
</footer>
{_disclaimer_block_ko()}
{config.ANALYTICS_SNIPPET}
</body>
</html>
"""
    os.makedirs(os.path.dirname(out_path), exist_ok=True)
    with open(out_path, "w") as f:
        f.write(doc)
    return out_path


RULE_KO = {
    "sma_cross": "짧은 이동평균이 긴 이동평균 위에 있으면 보유, 아래로 내려오면 현금",
    "ema_cross": "짧은 지수이동평균이 긴 지수이동평균 위에 있으면 보유, 아래면 현금",
    "above_sma": "종가가 이동평균선 위에 있으면 보유, 아래면 현금",
    "donchian": "종가가 최근 N봉 최고가를 넘으면 매수, 최근 M봉 최저가 아래로 내려오면 매도",
    "vol_breakout": "오늘 시가 + (어제 고가−저가)×k 를 봉 안에서 넘으면 매수, 다음 봉 시가에 매도",
    "vol_breakout_trend": "위와 같되, 시가가 20봉 이동평균 위에 있을 때만 매수",
    "rsi_mr": "RSI(14)가 30 아래로 내려가면 매수, 지정한 값 위로 올라오면 매도",
    "rsi2_connors": "RSI(2)가 10 아래이고 종가가 200봉 이동평균 위이면 매수, 종가가 5봉 이동평균 위로 올라오면 매도",
    "bb_mr": "종가가 볼린저밴드 아래선 밑으로 내려가면 매수, 가운데선 위로 올라오면 매도",
    "bb_breakout": "종가가 볼린저밴드 위선을 넘으면 매수, 가운데선 아래로 내려오면 매도",
    "macd": "MACD선이 시그널선 위에 있으면 보유, 아래면 현금",
    "supertrend": "슈퍼트렌드(10, 3) 방향이 위이면 보유, 아래면 현금",
    "tsmom": "지금 종가가 n봉 전 종가보다 높으면 보유, 낮으면 현금",
    "dip_3down": "종가가 3봉 연속 내리면 매수, 처음 오르는 봉에서 매도",
    "dip_pct": "한 봉에 5% 이상 떨어지면 다음 봉 시가에 매수, 5봉 뒤 시가에 매도",
    "dca_weekly": "매주 첫 봉 시가에 같은 금액을 사고 팔지 않음 (참고용)",
    "buy_and_hold": "구간 첫날 사서 마지막 날까지 들고 있음 (참고용)",
    # spec_v3 §D "Popular combos" — additive.
    "ema_9_21": "지수이동평균 9가 21 위에 있으면 보유, 아래면 현금",
    "ema200_macd": "종가가 200일 이동평균 위이고 MACD선이 시그널선 위이면 보유, MACD선이 시그널선 "
                   "아래로 내려오거나 종가가 200일 이동평균 아래로 내려오면 현금",
    "rsi_uptrend": "RSI(14)가 30 아래이고 종가가 200일 이동평균 위이면 매수, RSI가 70을 넘거나 "
                   "종가가 200일 이동평균 아래로 내려오면 매도",
    "bb_squeeze": "볼린저밴드 폭이 최근 120봉 중 가장 좁았던 뒤 5봉 안에서 종가가 위선을 넘으면 매수, "
                  "가운데선 아래로 내려오면 매도",
    "ichimoku_cloud": "종가가 구름대 위쪽 경계를 넘으면 매수, 아래쪽 경계 밑으로 내려오면 매도 "
                      "(구름은 26봉 앞으로 미뤄 표시하므로 계산 시점의 미래 정보를 쓰지 않음)",
    "heikin_ashi_trend": "헤이킨아시 캔들이 2봉 연속 양봉이면 매수, 처음 음봉이 나오면 매도",
    "supertrend_ema200": "슈퍼트렌드(10, 3) 방향이 위이고 종가가 200일 이동평균 위이면 매수, "
                         "슈퍼트렌드 방향이 아래로 바뀌는 순간 매도",
}


def _registry_table_html_ko():
    rows = []
    for entry in reg.REGISTRY:
        params = ", ".join(v["params_str"] for v in entry["variants"])
        name_ko = STRATEGY_NAME_KO.get(entry["id"], entry["name"])
        rule_ko = RULE_KO.get(entry["id"], entry["rule"])
        rows.append(
            f"<tr><td>{html.escape(entry['id'])}</td><td>{html.escape(name_ko)}</td>"
            f"<td>{html.escape(rule_ko)}</td>"
            f"<td>{html.escape(params)}</td></tr>"
        )
    for entry in reg.REFERENCE:
        name_ko = STRATEGY_NAME_KO.get(entry["id"], entry["name"])
        rule_ko = RULE_KO.get(entry["id"], entry["rule"])
        rows.append(
            f"<tr class=\"ref-row\"><td>{html.escape(entry['id'])}</td><td>{html.escape(name_ko)}</td>"
            f"<td>{html.escape(rule_ko)}</td><td>-</td></tr>"
        )
    return "\n".join(rows)


def _popular_combos_table_html_ko() -> str:
    rows = []
    for entry in reg.POPULAR_COMBOS:
        params = ", ".join(v["params_str"] for v in entry["variants"])
        name_ko = STRATEGY_NAME_KO.get(entry["id"], entry["name"])
        rule_ko = RULE_KO.get(entry["id"], entry["rule"])
        rows.append(
            f"<tr><td>{html.escape(entry['id'])}</td><td>{html.escape(name_ko)}</td>"
            f"<td>{html.escape(rule_ko)}</td>"
            f"<td>{html.escape(params)}</td></tr>"
        )
    return "\n".join(rows)


def build_methodology_ko(out_path: str | None = None):
    """Korean edition of build_methodology — same sections as the English page, translated."""
    out_path = out_path or os.path.join(config.DOCS_DIR_KO, "methodology.html")
    cost_rows = "".join(
        f"<tr><td>{html.escape(ASSET_LABEL_KO.get(a, a))}</td><td>{c*100:.2f}%</td></tr>"
        for a, c in config.COST.items() if a in config.UPBIT_ASSETS
    )
    th = config.VERDICT_THRESHOLDS

    doc = f"""<!doctype html>
<html lang="ko">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{html.escape(config.PROJECT_NAME)} — 어떻게 계산했나</title>
<style>{BASE_CSS}{KO_EXTRA_CSS}</style>
</head>
<body>
{_disclaimer_block_ko()}
<header class="top">
  <h1>어떻게 계산했나</h1>
  <p class="tagline"><a href="index.html">&larr; 결과표로 돌아가기</a> &middot;
     <a href="../methodology.html">English</a> &middot; <a href="../stocks/methodology.html">Stocks</a></p>
</header>
<main>
  <section class="assetblock">
    <h2>수수료</h2>
    <p>살 때와 팔 때 각각 0.10%를 뗍니다(업비트 수수료 0.05% + 체결 미끄러짐 0.05%). 매수 체결가는
       기준가보다 0.10% 비싸게, 매도 체결가는 0.10% 싸게 잡는 방식입니다. 결과표의 모든 숫자는 이
       수수료를 뗀 뒤의 값이고, "수수료로 사라진 수익" 열 하나만 참고용으로 수수료 전후 차이를 보여줍니다.</p>
    <div class="tablewrap"><table><thead><tr><th>자산</th><th>편도 수수료</th></tr></thead>
    <tbody>{cost_rows}</tbody></table></div>
  </section>

  <section class="assetblock">
    <h2>구간 나누기 — 학습 구간과 시험 구간</h2>
    <p>마지막으로 완성된 봉의 날짜를 기준일로 잡고, 그날부터 거꾸로 {config.OOS_DAYS}일을
       <strong>시험 구간(OOS, out-of-sample)</strong>, 그 이전 전체를 <strong>학습 구간(IS,
       in-sample)</strong>으로 나눕니다. 판정은 시험 구간만 봅니다. 매주 다시 계산할 때마다 이 구간이
       한 주씩 앞으로 밀립니다. 이동평균 같은 지표는 전체 가격 이력으로 계산하므로, 학습 구간 첫날부터
       신호가 나옵니다.</p>
  </section>

  <section class="assetblock">
    <h2>사고파는 규칙</h2>
    <p>모든 전략이 같은 조건입니다. 매수만 하고(공매도 없음), 살 때는 전액, 팔면 전액 현금입니다.
       레버리지도, 나눠 사는 것도 없습니다. 판단은 봉이 닫힌 뒤에 하고, 실제 체결은 <em>다음 봉의
       시가</em>에 합니다 — 봉이 닫히기 전에 미리 아는 척하지 않기 위해서입니다. 변동성 돌파 계열은
       봉 안에서 돌파선을 넘는 순간 사고, 다음 봉 시가에 무조건 팝니다. 급락 매수 계열은 신호 다음
       봉 시가에 사서 정해진 봉 수가 지나면 시가에 팝니다.</p>
  </section>

  <section class="assetblock">
    <h2>판정 기준 (시험 구간, 수수료 뗀 뒤)</h2>
    <table><tbody>
      <tr><td><strong>생존 <small>ALIVE</small></strong></td><td>PF가 {th['ALIVE_MIN_OOS_PF']} 이상이고, 거래가 {th['ALIVE_MIN_OOS_TRADES']}번 이상이며, 최대 낙폭이 같은 기간 단순 보유보다 작음</td></tr>
      <tr><td><strong>약화 <small>FADING</small></strong></td><td>생존 조건에는 못 미치지만, PF가 {th['FADING_MIN_OOS_PF']} 이상이고 거래가 {th['FADING_MIN_OOS_TRADES']}번 이상</td></tr>
      <tr><td><strong>사망 <small>DEAD</small></strong></td><td>PF가 {th['DEAD_MAX_OOS_PF']} 미만(손실)이고 거래가 {th['FADING_MIN_OOS_TRADES']}번 이상</td></tr>
      <tr><td><strong>표본 부족 <small>TOO FEW TRADES</small></strong></td><td>거래가 {th['TOO_FEW_MIN_OOS_TRADES']}번 미만이라 판단을 보류</td></tr>
    </tbody></table>
    <p>참고용 두 줄(단순 보유, 적립 매수)에는 판정을 붙이지 않습니다. 이 기준은 결과를 보기 전에
       정해 두었고, 결과에 맞춰 바꾸지 않습니다.</p>
  </section>

  <section class="assetblock">
    <h2>주변 설정값 안정성 (참고용)</h2>
    <p>숫자로 된 설정값이 하나라도 있는 전략은, 시험 구간(OOS)에서 등록된 값 근처의 몇 가지 값으로도
       다시 계산해 봅니다. 등록된 값을 기준으로 대략 위아래 25%(변동성 돌파의 k 값은 위아래 0.1) 떨어진
       값들을 조합해서, PF가 1.0 이상이고 거래가 10번 이상인 조건을 몇 개나 통과하는지 셉니다. 예를 들어
       "7/9"라면 아홉 가지 조합 중 일곱 가지가 이 조건을 통과했다는 뜻이고, 표에서 마우스를 올리면 전체
       목록을 볼 수 있습니다. 등록된 값 딱 하나에서만 결과가 좋고 조금만 벗어나면 무너지는 전략은, 실제로
       통하는 방식이라기보다 과거 데이터에 우연히 들어맞았을 가능성이 큽니다.</p>
    <p>이 값은 판정에 전혀 반영되지 않습니다. 계산은 같은 엔진과 같은 데이터로 하지만, 결과는 이 열
       하나에만 쓰입니다. 판정은 언제나 결과를 보기 전에 <code>registry.py</code>에 고정해 둔 값 하나로만
       계산하며, 이 참고 자료를 보고 더 나아 보이는 값으로 바꾸는 일은 없습니다.</p>
  </section>

  <section class="assetblock">
    <h2>계산기가 거짓말하지 않는지 확인하는 방법</h2>
    <p><strong>미래 정보 차단 테스트.</strong> 백테스트에서 가장 흔한 실수는 코드가 실수로 "내일
       가격"을 보고 오늘 결정하는 것입니다. 이를 막기 위해 모든 전략에 대해, 데이터를 어느 날짜에서
       잘라도 그 전날까지의 매매 결정이 똑같이 나오는지를 매주 실행 때마다 자동으로 검사합니다. 하나라도
       다르면 결과를 내보내지 않고 멈춥니다. <strong>다른 엔진과 대조.</strong> 단순이동평균 교차
       전략은 널리 쓰이는 공개 라이브러리 <code>backtesting.py</code>로도 돌려서 거래 횟수가 정확히
       같고 수익률 차이가 2% 이내인지 확인했습니다. <strong>부호 점검.</strong> 수수료를 0으로 놓고
       계산한 결과가 수수료를 뗀 결과보다 항상 좋아야 합니다. 당연한 말이지만, 코드의 부호 실수를
       잡아내는 검사입니다.</p>
  </section>

  <section class="assetblock">
    <h2>검사하는 전략 목록</h2>
    <div class="tablewrap"><table class="regtable">
      <thead><tr><th>id</th><th>전략</th><th>규칙</th><th>설정값</th></tr></thead>
      <tbody>{_registry_table_html_ko()}</tbody>
    </table></div>
  </section>

  <section class="assetblock">
    <h2>유튜브·트레이딩뷰에서 많이 가르치는 조합 전략</h2>
    <p>단일 지표가 아니라, 여러 지표를 함께 쓰는 방식으로 유튜브나 트레이딩뷰에서 흔히 가르치는
       조합들을 따로 모아 검사합니다. 사고파는 규칙, 수수료, 판정 기준, 주변 설정값 안정성 검사는
       위의 전략들과 완전히 같고, 결과표에서만 보기 쉽게 별도의 표로 나눠 보여줍니다.</p>
    <div class="tablewrap"><table class="regtable">
      <thead><tr><th>id</th><th>전략</th><th>규칙</th><th>설정값</th></tr></thead>
      <tbody>{_popular_combos_table_html_ko()}</tbody>
    </table></div>
  </section>

  <section class="assetblock">
    <h2>내 전략도 검사해 보기</h2>
    <p><a href="{html.escape(_check_strategy_url())}">"Check my strategy" 이슈 양식</a>으로
       깃허브 이슈를 하나 열면, 이미 저장소에 있는 데이터를 기준으로 같은 엔진·수수료·판정 기준을
       그대로 적용해 전략 하나를 검사해 드립니다. 봇이 결과를 이슈 댓글로 남기고 이슈를 자동으로
       닫으며, 이 결과 역시 사이트의 다른 결과와 마찬가지로 자동화된 참고 자료일 뿐 투자 자문이
       아닙니다.</p>
  </section>

  <section class="assetblock">
    <h2>일부러 하지 않는 것</h2>
    <p>설정값을 결과가 좋아질 때까지 바꾸는 일, 결과를 본 뒤 조건을 덧붙이는 일은 하지 않습니다.
       그렇게 하면 과거에만 맞는 전략이 만들어지기 때문입니다. 이 사이트에 있는 모든 전략과 설정값은
       결과를 계산하기 전에 <code>registry.py</code>에 고정해 두었고, 새 전략을 넣을 때도 같은
       방식으로 먼저 등록한 뒤에 계산합니다.</p>
  </section>
</main>
<footer class="bottom">
  <div><a href="index.html">&larr; 결과표로 돌아가기</a> &middot; <a href="../methodology.html">English</a> &middot; <a href="../stocks/methodology.html">Stocks</a>
  &middot; <a href="{html.escape(_check_strategy_url())}">내 전략도 검사해 보기</a>
  &middot; <a href="s/index.html">전략별 페이지 전체</a></div>
</footer>
{_disclaimer_block_ko()}
{config.ANALYTICS_SNIPPET}
</body>
</html>
"""
    os.makedirs(os.path.dirname(out_path), exist_ok=True)
    with open(out_path, "w") as f:
        f.write(doc)
    return out_path


def build_empty_edition_page_ko(out_path: str | None = None, as_of: str | None = None):
    """Rendered when the Korean edition has no Upbit data at all this week (network-blocked dev
    env, or Upbit returning 4xx in CI) — requirement 6: a clear notice, never a blank page, and
    the mandatory disclaimer still appears top and bottom."""
    out_path = out_path or os.path.join(config.DOCS_DIR_KO, "index.html")
    as_of = as_of or "-"

    doc = f"""<!doctype html>
<html lang="ko">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{html.escape(config.PROJECT_TITLE_KO)}</title>
<style>{BASE_CSS}{KO_EXTRA_CSS}</style>
</head>
<body>
{_disclaimer_block_ko()}
<header class="top">
  <h1>{html.escape(config.PROJECT_NAME)}</h1>
  <p class="tagline">{html.escape(config.TAGLINE_KO)}</p>
  <p class="meta">기준일 <strong>{html.escape(as_of)}</strong> &middot;
     <a href="../index.html">English</a> &middot; <a href="../stocks/index.html">Stocks</a></p>
</header>
<main>
  <div class="notice-box">
    <h2>이번 주는 결과가 없습니다</h2>
    <p>업비트에서 시세 데이터를 받아오지 못해 이번 주 결과표를 만들지 못했습니다. 업비트 서버의
       일시적인 문제일 수 있으며, 다음 주 자동 실행 때 다시 시도합니다.<br>
       영어판(BTC/ETH, Bitstamp 데이터)은 정상적으로 갱신되었으니 위의 English 링크에서 확인할 수
       있습니다.</p>
  </div>
</main>
<footer class="bottom">
  <div><a href="methodology.html">어떻게 계산했나</a> &middot; <a href="../index.html">English</a> &middot; <a href="../stocks/index.html">Stocks</a>
  &middot; <a href="{html.escape(_check_strategy_url())}">내 전략도 검사해 보기</a>
  &middot; <a href="s/index.html">전략별 페이지 전체</a></div>
</footer>
{_disclaimer_block_ko()}
{config.ANALYTICS_SNIPPET}
</body>
</html>
"""
    os.makedirs(os.path.dirname(out_path), exist_ok=True)
    with open(out_path, "w") as f:
        f.write(doc)
    return out_path


# =================================================================================================
# Machine-readable API docs (spec_v3 §B) — additive extension, nothing above this line is
# modified. `docs/api/v1/README.md` and `docs/api/index.html` document the same static JSON tree
# run_weekly.py's _write_api_v1() writes under docs/api/v1/<edition>/ — this module only renders
# the human-readable description, never the data files themselves.
# =================================================================================================

API_EDITIONS_INFO = [
    ("en", "English (crypto: BTCUSD, ETHUSD, 1d/4h)", "methodology.html"),
    ("ko", "한국어 (Korean, crypto: KRW-BTC, KRW-ETH, 1d/4h)", "ko/methodology.html"),
    ("stocks", "Stocks (SPY, QQQ, 1d)", "stocks/methodology.html"),
]

API_SCHEMA_FIELDS = [
    ("strategy_id", "registry id, e.g. \"sma_cross\" — see the methodology page's strategy table "
                     "for the full list, including the POPULAR_COMBOS group"),
    ("strategy_name", "human-readable name of the strategy"),
    ("params", "the fixed parameter string for this variant, e.g. \"10-50\"; \"-\" for reference rows"),
    ("type", "\"state\" | \"onebar\" | \"holdN\" | \"reference\" — which engine primitive ran this row"),
    ("asset", "e.g. \"BTCUSD\", \"KRW-BTC\", \"SPY\""),
    ("timeframe", "\"1d\" or \"4h\""),
    ("edition", "\"en\" | \"ko\" | \"stocks\""),
    ("as_of", "date (UTC) of the last fully-closed bar this run used, ISO YYYY-MM-DD"),
    ("verdict", "\"ALIVE\" | \"FADING\" | \"DEAD\" | \"TOO FEW TRADES\" | null (reference rows)"),
    ("is", "object of in-sample metrics — see the methodology page's metric definitions"),
    ("oos", "object of out-of-sample metrics (the ones the verdict is based on), plus fee_drag"),
    ("robustness", "object — {grid: [...], share_pf_ge_1, note}; diagnostic only, added by the "
                    "parameter-neighbourhood robustness map (see methodology: \"Robustness map "
                    "(diagnostic)\"); present on tradeable (non-reference) rows once that "
                    "extension has run, absent on older history snapshots and on reference rows"),
    ("suspicious", "bool — OOS PF or Sharpe crossed the \"investigate, don't trust\" threshold"),
]


def _api_readme_markdown() -> str:
    editions_md = "\n".join(f"- `{k}` — {label} (`{link}`)" for k, label, link in API_EDITIONS_INFO)
    schema_md = "\n".join(f"- `{name}`: {desc}" for name, desc in API_SCHEMA_FIELDS)
    example = (
        "```python\n"
        "import requests\n\n"
        f'data = requests.get("{config.PAGES_URL}/api/v1/en/latest.json").json()\n'
        'for row in data["rows"]:\n'
        '    print(row["strategy_id"], row["params"], row["asset"], row["timeframe"], row["verdict"])\n'
        "```\n"
    )
    return f"""# {config.PROJECT_NAME} — API v1

Static, read-only JSON. No key, no rate limit beyond normal HTTP caching — these are plain files
served by GitHub Pages, refreshed once a week by the same pipeline that renders the HTML pages.

## Editions

{editions_md}

## Endpoints (per edition)

- `GET /api/v1/<edition>/latest.json` — the most recent run's full payload (identical in shape to
  this repo's own `results/latest.json` / `results/latest_ko.json` / `results/latest_stocks.json`).
- `GET /api/v1/<edition>/history/index.json` — `{{"edition": "...", "history": [{{"as_of": "...",
  "file": "history/<as_of>.json"}}, ...]}}`, newest first.
- `GET /api/v1/<edition>/history/<as_of>.json` — a copy of that edition's payload as of that date.

## Row schema

`payload["popular_combos"]` (added by spec_v3 §D) is a second array of rows in the exact same
shape as `payload["rows"]` below, for the separately-pre-registered "popular combos" strategy
group (multi-indicator combinations as commonly taught on YouTube/TradingView) — kept as its own
array rather than merged into `rows`, so a consumer that assumed `rows` meant "the original
registry" is not silently handed extra strategies.

Every element of `payload["rows"]` (and `payload["popular_combos"]`) carries:

{schema_md}

Cost model and verdict-badge thresholds are defined once, by reference, on each edition's
methodology page (see the table above) — they are not repeated as numbers in this document so
this document never goes stale relative to `config.py`, the single source of truth for both.

## Update cadence

Weekly, Monday 00:30 UTC (see `.github/workflows/weekly.yml`) — one run per week, rolling the
in-sample/out-of-sample window forward by a week each time. `workflow_dispatch` can trigger an
out-of-cycle run; `payload["generated_at"]` (UTC, ISO 8601) is the actual wall-clock time of the
run that produced a given snapshot, which may differ from `as_of` (the date of the last
fully-closed price bar that run used).

## Stability

Fields are only ever added, never renamed or removed, and an existing field's meaning is never
changed — a consumer that only reads fields it recognizes keeps working across every future
weekly run. `robustness` above is one such field: it did not exist in the first release of this
API and appears only once the robustness-map extension started running; its absence on an older
`history/<as_of>.json` snapshot is not an error.

## License

- **Data**: sourced from each exchange's/data provider's own public API (Bitstamp, Upbit, Stooq,
  Yahoo Finance) — subject to those providers' own terms, not this project's.
- **Results** (the computed metrics, verdicts, and this JSON structure itself): CC BY 4.0 — reuse
  freely with attribution.

## Legal

{config.LEGAL_DISCLAIMER}

## Example

{example}"""


def _api_readme_html_body() -> str:
    editions_li = "\n".join(
        f'<li><code>{html.escape(k)}</code> — {html.escape(label)} '
        f'(<a href="../{html.escape(link)}">methodology</a>)</li>'
        for k, label, link in API_EDITIONS_INFO
    )
    schema_li = "\n".join(
        f'<li><code>{html.escape(name)}</code>: {html.escape(desc)}</li>'
        for name, desc in API_SCHEMA_FIELDS
    )
    example = (
        "import requests\n\n"
        f'data = requests.get("{config.PAGES_URL}/api/v1/en/latest.json").json()\n'
        'for row in data["rows"]:\n'
        '    print(row["strategy_id"], row["params"], row["asset"], row["timeframe"], row["verdict"])'
    )
    return f"""
  <section class="assetblock">
    <h2>Editions</h2>
    <ul>{editions_li}</ul>
  </section>

  <section class="assetblock">
    <h2>Endpoints (per edition)</h2>
    <ul>
      <li><code>GET /api/v1/&lt;edition&gt;/latest.json</code> &mdash; the most recent run's full
        payload (identical in shape to this repo's own <code>results/latest.json</code> /
        <code>results/latest_ko.json</code> / <code>results/latest_stocks.json</code>).</li>
      <li><code>GET /api/v1/&lt;edition&gt;/history/index.json</code> &mdash; a list of every
        available snapshot, newest first: <code>{{"edition": "...", "history": [{{"as_of": "...",
        "file": "history/&lt;as_of&gt;.json"}}, ...]}}</code>.</li>
      <li><code>GET /api/v1/&lt;edition&gt;/history/&lt;as_of&gt;.json</code> &mdash; a copy of
        that edition's payload as of that date.</li>
    </ul>
  </section>

  <section class="assetblock">
    <h2>Row schema</h2>
    <p><code>payload["popular_combos"]</code> (added by the popular-combos extension) is a second
       array in the same row shape as <code>payload["rows"]</code>, for a separately
       pre-registered group of multi-indicator combinations &mdash; kept separate so
       <code>rows</code> keeps meaning exactly what it always meant.</p>
    <p>Every element of <code>payload["rows"]</code> (and <code>payload["popular_combos"]</code>)
       carries:</p>
    <ul>{schema_li}</ul>
    <p>Cost model and verdict-badge thresholds are defined once, by reference, on each edition's
       methodology page linked above &mdash; not repeated as numbers here, so this page never goes
       stale relative to <code>config.py</code>.</p>
  </section>

  <section class="assetblock">
    <h2>Update cadence</h2>
    <p>Weekly, Monday 00:30 UTC. <code>payload["generated_at"]</code> is the run's own wall-clock
       time; <code>as_of</code> is the date of the last fully-closed price bar that run used.</p>
  </section>

  <section class="assetblock">
    <h2>Stability</h2>
    <p>Fields are only ever added, never renamed or removed. <code>robustness</code> is one such
       field, added by a later extension &mdash; its absence on an older history snapshot is not
       an error.</p>
  </section>

  <section class="assetblock">
    <h2>License</h2>
    <p><strong>Data</strong>: sourced from each provider's own public API (Bitstamp, Upbit, Stooq,
       Yahoo Finance) &mdash; subject to those providers' own terms.
       <strong>Results</strong> (computed metrics, verdicts, this JSON structure): CC BY 4.0.</p>
  </section>

  <section class="assetblock">
    <h2>Example</h2>
    <pre style="white-space:pre-wrap;background:var(--card-bg);padding:1rem;border-radius:8px;overflow-x:auto;">{html.escape(example)}</pre>
  </section>
"""


def build_api_readme(out_path: str | None = None) -> str:
    out_path = out_path or os.path.join(config.DOCS_DIR, "api", "v1", "README.md")
    os.makedirs(os.path.dirname(out_path), exist_ok=True)
    with open(out_path, "w") as f:
        f.write(_api_readme_markdown())
    return out_path


def build_api_index_html(out_path: str | None = None) -> str:
    out_path = out_path or os.path.join(config.DOCS_DIR, "api", "index.html")
    doc = f"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{html.escape(config.PROJECT_NAME)} API</title>
<style>{BASE_CSS}</style>
</head>
<body>
<header class="top">
  <h1>{html.escape(config.PROJECT_NAME)} &mdash; API v1</h1>
  <p class="tagline">Static, read-only JSON — no key, refreshed weekly.
     <a href="../index.html">&larr; back to results</a></p>
</header>
<main>
{_api_readme_html_body()}
</main>
<footer class="bottom">
  <div><a href="../index.html">&larr; back to results</a>
  &middot; <a href="{html.escape(_check_strategy_url())}">Check your own strategy</a>
  &middot; <a href="../s/index.html">All strategy pages</a></div>
  <div class="disclaimer">{html.escape(config.LEGAL_DISCLAIMER)}</div>
</footer>
{config.ANALYTICS_SNIPPET}
</body>
</html>
"""
    os.makedirs(os.path.dirname(out_path), exist_ok=True)
    with open(out_path, "w") as f:
        f.write(doc)
    return out_path


if __name__ == "__main__":
    import json
    with open(os.path.join(config.RESULTS_DIR, "latest.json")) as f:
        payload = json.load(f)
    build_index(payload)
    build_methodology()
    print("Wrote docs/index.html and docs/methodology.html")
