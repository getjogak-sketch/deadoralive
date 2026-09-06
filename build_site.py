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
  <p class="meta">as_of: <strong>{html.escape(payload['as_of'])}</strong> &middot; generated {html.escape(payload['generated_at'])}
  &middot; <a href="ko/index.html">한국어 (Korean edition)</a></p>
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
    # Filtered to config.ASSETS (not all of config.COST) so this English page's output is
    # unaffected by the Korean edition's COST["KRW-BTC"/"KRW-ETH"] entries added alongside it.
    cost_rows = "".join(
        f"<tr><td>{html.escape(a)}</td><td>{config.COST[a]*100:.2f}%</td></tr>"
        for a in config.ASSETS
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
  <p class="tagline"><a href="index.html">&larr; back to results</a> &middot; <a href="ko/methodology.html">한국어 (Korean edition)</a></p>
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
    "sma_cross": "이동평균 교차 (SMA crossover)",
    "ema_cross": "지수이동평균 교차 (EMA crossover)",
    "above_sma": "이동평균선 상회 (Price above SMA)",
    "donchian": "돈치안/터틀 브레이크아웃 (Donchian/Turtle breakout)",
    "vol_breakout": "변동성 돌파 (Larry Williams volatility breakout)",
    "vol_breakout_trend": "변동성 돌파 + 추세 필터 (Volatility breakout + trend filter)",
    "rsi_mr": "RSI 평균회귀 (RSI mean reversion)",
    "rsi2_connors": "코너스 RSI(2) (Connors RSI(2))",
    "bb_mr": "볼린저밴드 평균회귀 (Bollinger mean reversion)",
    "bb_breakout": "볼린저밴드 브레이크아웃 (Bollinger breakout)",
    "macd": "MACD 시그널 교차 (MACD signal cross)",
    "supertrend": "슈퍼트렌드 (Supertrend)",
    "tsmom": "시계열 모멘텀 (Time-series momentum)",
    "dip_3down": "하락 눌림목 매수, 3일 연속 하락 (Buy the dip, 3 down closes)",
    "dip_pct": "하락 눌림목 매수, 일정 비율 하락 (Buy the dip, -x% bar)",
    "dca_weekly": "주간 정액 매수 (참고용) (Weekly DCA, reference)",
    "buy_and_hold": "매수 후 보유 (참고용) (Buy & hold, reference)",
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
    susp = (' <span class="suspicious" title="OOS PF 또는 샤프비율이 비정상적으로 높음 — 방법론 '
            '참고">&#9888;</span>') if row.get("suspicious") else ""

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
  <p class="meta">기준일(as of) {html.escape(as_of)}{price_note} &mdash; OOS(표본외) = 최근
     {config.OOS_DAYS}일, IS(표본내) = 그 이전 전체 기간.</p>
  <div class="tablewrap">
    <table>
      <thead><tr>
        <th>전략</th><th>파라미터</th><th>판정</th>
        <th>OOS 수익률</th><th>OOS PF</th><th>OOS MDD</th><th>OOS 거래수</th><th>OOS 승률</th>
        <th>IS PF</th><th>IS MDD</th>
        <th>B&amp;H OOS 수익률</th><th>B&amp;H OOS MDD</th>
        <th>수수료 손실&sup1;</th>
      </tr></thead>
      <tbody>
{body_rows}
      </tbody>
    </table>
  </div>
  <p class="gross-note">&sup1; 수수료 손실 = 동일 OOS 구간을 수수료 0으로 가정하고 계산한 수익률
     (참고용, before-fees) &minus; 실제(net) OOS 수익률. 이 표의 다른 모든 수치는 수수료를 반영한
     (net) 값입니다.</p>
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

    skipped = sorted(set(config.UPBIT_ASSETS) - {a for a, _tf in order})
    skipped_note = ""
    if skipped:
        skipped_note = (f'<p class="meta">이번 주 건너뜀(로컬 데이터 파일 없음): '
                         f'{html.escape(", ".join(ASSET_LABEL_KO.get(a, a) for a in skipped))}.</p>')

    signup_html = ""
    if payload.get("signup_url"):
        signup_html = f' &middot; <a href="{html.escape(payload["signup_url"])}">주간 소식 받기</a>'
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
  <p class="meta">기준일(as_of): <strong>{html.escape(payload['as_of'])}</strong> &middot;
     생성 시각 {html.escape(payload['generated_at'])} &middot; <a href="../index.html">English</a></p>
  <div class="tally">{tally_html}</div>
  {skipped_note}
</header>
<main>
  <nav class="jump">{nav_html}</nav>
  {sections_html}
</main>
<footer class="bottom">
  <div><a href="methodology.html">방법론</a>{repo_html}{signup_html} &middot; <a href="../index.html">English</a></div>
</footer>
{_disclaimer_block_ko()}
</body>
</html>
"""
    os.makedirs(os.path.dirname(out_path), exist_ok=True)
    with open(out_path, "w") as f:
        f.write(doc)
    return out_path


def _registry_table_html_ko():
    rows = []
    for entry in reg.REGISTRY:
        params = ", ".join(v["params_str"] for v in entry["variants"])
        name_ko = STRATEGY_NAME_KO.get(entry["id"], entry["name"])
        rows.append(
            f"<tr><td>{html.escape(entry['id'])}</td><td>{html.escape(name_ko)}</td>"
            f"<td>{html.escape(entry['type'])}</td><td>{html.escape(entry['rule'])}</td>"
            f"<td>{html.escape(params)}</td></tr>"
        )
    for entry in reg.REFERENCE:
        name_ko = STRATEGY_NAME_KO.get(entry["id"], entry["name"])
        rows.append(
            f"<tr class=\"ref-row\"><td>{html.escape(entry['id'])}</td><td>{html.escape(name_ko)}</td>"
            f"<td>{html.escape(entry['type'])}</td><td>{html.escape(entry['rule'])}</td><td>-</td></tr>"
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
<title>{html.escape(config.PROJECT_NAME)} 방법론</title>
<style>{BASE_CSS}{KO_EXTRA_CSS}</style>
</head>
<body>
{_disclaimer_block_ko()}
<header class="top">
  <h1>방법론</h1>
  <p class="tagline"><a href="index.html">&larr; 결과로 돌아가기</a> &middot;
     <a href="../methodology.html">English</a></p>
</header>
<main>
  <section class="assetblock">
    <h2>비용 모델</h2>
    <p>수수료는 편도(one-way)로, 체결가에 반영됩니다: 매수 체결가 = 기준가 &times; (1 + 수수료),
       매도 체결가 = 기준가 &times; (1 &minus; 수수료). "수수료 손실" 열 하나를 제외한 모든 수치는
       수수료를 반영한(net) 값이며, 그 열은 수수료 미반영(gross, 참고용)임을 명시합니다.
       업비트(Upbit) 기준 수수료 0.05% + 슬리피지 0.05% = 편도 0.10%로, 기존 BTC/ETH 자산과 동일한
       가정을 그대로 사용합니다.</p>
    <div class="tablewrap"><table><thead><tr><th>자산</th><th>편도 수수료</th></tr></thead>
    <tbody>{cost_rows}</tbody></table></div>
  </section>

  <section class="assetblock">
    <h2>IS / OOS 롤링 구간</h2>
    <p>매 실행의 마지막으로 완결된 봉 날짜(<code>as_of</code>) 기준: OOS(표본외) = <code>as_of</code>
       를 기준으로 최근 {config.OOS_DAYS}일; IS(표본내) = 그 이전 데이터 전체. 매주 파이프라인이
       재실행될 때마다 이 구간은 한 주씩 앞으로 이동합니다. 지표 워밍업(예: SMA200)은 항상 전체
       가격 이력을 사용하므로, 룩백 기간이 허용하는 한 IS 구간의 첫 봉부터 신호가 유효합니다.</p>
  </section>

  <section class="assetblock">
    <h2>체결 규칙</h2>
    <p>모든 전략 공통: 롱 온리(매수만), 항상 전액 진입 또는 전액 현금(레버리지·공매도·부분 매매
       없음). "상태형" 전략은 bar t 종가 시점 데이터로 목표 상태(보유/미보유)를 결정하고, bar t+1
       시가에 체결합니다. "1-bar형" 전략(변동성 돌파 및 추세 필터 버전)은 매 bar 독립적으로 인트라
       바 돌파 조건을 평가하며 다음 bar 시가에 항상 청산합니다. "보유 N-bar형" 전략(눌림목 매수)은
       트리거 발생 다음 bar 시가에 진입해 정확히 N bar 뒤 시가에 청산합니다(중간 신호와 무관).</p>
  </section>

  <section class="assetblock">
    <h2>판정 배지 (OOS, 수수료 반영 후)</h2>
    <table><tbody>
      <tr><td><strong>생존 <small>ALIVE</small></strong></td><td>OOS PF &ge; {th['ALIVE_MIN_OOS_PF']} 그리고 OOS 거래수 &ge; {th['ALIVE_MIN_OOS_TRADES']} 그리고 OOS MDD &lt; 동일 기간 매수 후 보유(B&amp;H) MDD</td></tr>
      <tr><td><strong>약화 <small>FADING</small></strong></td><td>생존 조건 미달, 그러나 OOS PF &ge; {th['FADING_MIN_OOS_PF']} 그리고 OOS 거래수 &ge; {th['FADING_MIN_OOS_TRADES']}</td></tr>
      <tr><td><strong>사망 <small>DEAD</small></strong></td><td>OOS PF &lt; {th['DEAD_MAX_OOS_PF']} 그리고 OOS 거래수 &ge; {th['FADING_MIN_OOS_TRADES']}</td></tr>
      <tr><td><strong>표본 부족 <small>TOO FEW TRADES</small></strong></td><td>OOS 거래수 &lt; {th['TOO_FEW_MIN_OOS_TRADES']}</td></tr>
    </tbody></table>
    <p>참고 행(매수 후 보유, 주간 정액 매수)에는 배지가 부여되지 않습니다.</p>
  </section>

  <section class="assetblock">
    <h2>엔진 정직성 검증</h2>
    <p><strong>룩어헤드(미래 참조) 방지 테스트</strong>: 레지스트리의 모든 전략 변형에 대해, 전체
       가격 시리즈로 계산한 신호와 특정 시점 T에서 잘라낸 데이터로 계산한 신호를 비교하여, T 이전
       시점의 값이 이후 데이터 존재 여부와 무관하게 완전히 동일한지 확인합니다. <strong>이중 엔진
       대조</strong>(sma_cross만 해당): 직접 구현한 pandas 엔진과 <code>backtesting.py</code>
       라이브러리를 동일 데이터·비용으로 비교하여, 거래 횟수는 정확히 일치하고 총수익률은 2% 이내로
       일치해야 합니다. <strong>정합성 점검</strong>: 수수료 0으로 계산한 결과는 항상 수수료 반영
       결과 이상이어야 합니다(수수료 0 수치는 이 내부 점검과 "수수료 손실" 열에만 쓰이며, 전략
       자체의 성과로 제시되지 않습니다).</p>
  </section>

  <section class="assetblock">
    <h2>전략 레지스트리</h2>
    <div class="tablewrap"><table>
      <thead><tr><th>id</th><th>이름</th><th>유형</th><th>규칙</th><th>파라미터</th></tr></thead>
      <tbody>{_registry_table_html_ko()}</tbody>
    </table></div>
  </section>

  <section class="assetblock">
    <h2>하지 않는 것</h2>
    <p>파라미터 튜닝, 새 필터 추가, 성과를 본 뒤 전략 변형을 추가하는 일을 하지 않습니다. 이
       사이트에 나타날 모든 전략과 모든 파라미터 값은 결과 계산 전에 <code>registry.py</code>에
       미리 고정되어 있으며, 새 항목은 앞으로 사전 등록된 형태로만 추가될 뿐 과거 데이터를
       소급 재실행해 보기 좋은 구간을 골라내는 데 쓰이지 않습니다.</p>
  </section>
</main>
<footer class="bottom">
  <div><a href="index.html">&larr; 결과로 돌아가기</a> &middot; <a href="../methodology.html">English</a></div>
</footer>
{_disclaimer_block_ko()}
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
  <p class="meta">기준일(as_of): <strong>{html.escape(as_of)}</strong> &middot;
     <a href="../index.html">English</a></p>
</header>
<main>
  <div class="notice-box">
    <h2>이번 주 데이터 없음</h2>
    <p>이번 주에는 업비트(Upbit) 시세 데이터를 가져오지 못해 결과표를 만들지 못했습니다.<br>
       (개발 환경 네트워크 차단, 또는 업비트 서버의 일시적 오류일 수 있습니다.)<br>
       다음 주 자동 실행 때 다시 시도합니다. 영어판(BTC/ETH, Bitstamp 데이터)은 정상적으로
       갱신되었습니다 — 위의 English 링크를 확인해 주세요.</p>
  </div>
</main>
<footer class="bottom">
  <div><a href="methodology.html">방법론</a> &middot; <a href="../index.html">English</a></div>
</footer>
{_disclaimer_block_ko()}
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
