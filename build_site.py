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
        <th>Fee drag&sup1;</th>
      </tr></thead>
      <tbody>
{body_rows}
      </tbody>
    </table>
  </div>
  <p class="gross-note">&sup1; Fee drag = gross (before-fees, for illustration only) OOS return
     &minus; net OOS return. Every other number on this page is net of trading cost.</p>
</section>"""


def build_index(payload: dict, out_path: str | None = None):
    out_path = out_path or os.path.join(config.DOCS_DIR, "index.html")

    groups = {}
    order = []
    for r in payload["rows"]:
        key = (r["asset"], r["timeframe"])
        if key not in groups:
            groups[key] = []
            order.append(key)
    order = [k for k in [(a, tf) for a in config.ASSETS for tf in config.TIMEFRAMES] if k in groups] or order
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

    skipped = sorted(set(config.ASSETS) - {a for a, _tf in order})
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
  <p class="meta">as_of: <strong>{html.escape(payload['as_of'])}</strong> &middot; generated {html.escape(payload['generated_at'])}</p>
  <div class="tally">{tally_html}</div>
  {skipped_note}
</header>
<main>
  <nav class="jump">{nav_html}</nav>
  {sections_html}
</main>
<footer class="bottom">
  <div><a href="methodology.html">Methodology</a>{repo_html}{signup_html}</div>
  <div class="disclaimer">{html.escape(payload['legal_disclaimer'])}</div>
</footer>
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


def build_methodology(out_path: str | None = None):
    out_path = out_path or os.path.join(config.DOCS_DIR, "methodology.html")
    cost_rows = "".join(
        f"<tr><td>{html.escape(a)}</td><td>{c*100:.2f}%</td></tr>" for a, c in config.COST.items()
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
  <p class="tagline"><a href="index.html">&larr; back to results</a></p>
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
    <h2>What we don't do</h2>
    <p>No parameter tuning, no new filters, no adding a strategy variant after seeing how it
       performs. Every strategy and every parameter value that will ever appear on this site is
       fixed in <code>registry.py</code> before results are computed; a new one can only be added
       going forward, pre-registered, never retroactively re-run against history to cherry-pick
       a good-looking window.</p>
  </section>
</main>
<footer class="bottom">
  <div><a href="index.html">&larr; back to results</a></div>
  <div class="disclaimer">{html.escape(config.LEGAL_DISCLAIMER)}</div>
</footer>
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
