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
  <p class="meta">기준일 {html.escape(as_of)}{price_note} &mdash; 최근 {config.OOS_DAYS}일을
     시험 구간(OOS), 그 이전 전체를 학습 구간(IS)으로 나누어 봅니다. 판정은 시험 구간만 봅니다.</p>
  <div class="tablewrap">
    <table>
      <thead><tr>
        <th>전략</th><th>설정값</th><th>판정</th>
        <th>최근 2년 수익률</th><th>PF</th><th>최대 낙폭</th><th>거래 횟수</th><th>승률</th>
        <th>이전 기간 PF</th><th>이전 기간 최대 낙폭</th>
        <th>단순 보유 수익률</th><th>단순 보유 최대 낙폭</th>
        <th>수수료로 사라진 수익&sup1;</th>
      </tr></thead>
      <tbody>
{body_rows}
      </tbody>
    </table>
  </div>
  <p class="gross-note">&sup1; 수수료가 없다고 가정했을 때의 수익률에서 실제 수익률을 뺀 값입니다(참고용).
     이 열을 제외한 모든 숫자는 수수료(편도 0.10%)를 뗀 뒤의 값입니다. PF(Profit Factor)는 이긴 거래의
     이익 합계를 진 거래의 손실 합계로 나눈 값으로, 1.0이면 본전입니다. 최대 낙폭(MDD)은 고점 대비
     가장 많이 빠졌던 비율입니다.</p>
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
</main>
<footer class="bottom">
  <div><a href="methodology.html">어떻게 계산했나</a>{repo_html}{signup_html} &middot; <a href="../index.html">English</a> &middot; <a href="../stocks/index.html">Stocks</a></div>
</footer>
{_disclaimer_block_ko()}
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
    <h2>일부러 하지 않는 것</h2>
    <p>설정값을 결과가 좋아질 때까지 바꾸는 일, 결과를 본 뒤 조건을 덧붙이는 일은 하지 않습니다.
       그렇게 하면 과거에만 맞는 전략이 만들어지기 때문입니다. 이 사이트에 있는 모든 전략과 설정값은
       결과를 계산하기 전에 <code>registry.py</code>에 고정해 두었고, 새 전략을 넣을 때도 같은
       방식으로 먼저 등록한 뒤에 계산합니다.</p>
  </section>
</main>
<footer class="bottom">
  <div><a href="index.html">&larr; 결과표로 돌아가기</a> &middot; <a href="../methodology.html">English</a> &middot; <a href="../stocks/methodology.html">Stocks</a></div>
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
  <div><a href="methodology.html">어떻게 계산했나</a> &middot; <a href="../index.html">English</a> &middot; <a href="../stocks/index.html">Stocks</a></div>
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
