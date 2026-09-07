"""
digest.py — weekly "what changed this week" digest (this task's requirement S2).

Purely additive / read-only over what run_weekly.py already wrote this run: compares the
just-written payload for an edition (results/latest.json / latest_ko.json / latest_stocks.json)
against the previous results/history/<...>.json snapshot for that same edition, and produces a
short, strictly factual digest — tally, verdict flips, the single biggest fee-drag row, and the
best/worst OOS profit factor among rows with >=30 OOS trades. No opinions, no advice language;
every sentence restates a number already computed elsewhere (engine.py/metrics.py/verdict.py are
never touched or re-run here).

Output per edition:
  results/digest/<edition>_<as_of>.json   — the digest's own data (also feeds publish.py/feeds)
  docs/digest/<as_of>.html, docs/digest/index.html          (English / crypto edition)
  docs/ko/digest/<as_of>.html, docs/ko/digest/index.html    (Korean edition)
  docs/stocks/digest/<as_of>.html, docs/stocks/digest/index.html  (stocks edition, if it has data)

First run for an edition (no earlier history snapshot) -> a "first week" digest with an empty
flips list, per this task's own instruction.
"""
from __future__ import annotations
import html
import json
import os
import re

import config

DIGEST_DIR = os.path.join(config.RESULTS_DIR, "digest")

_HIST_FILE_RE = {
    "en": re.compile(r"^(\d{4}-\d{2}-\d{2})\.json$"),
    "ko": re.compile(r"^ko_(\d{4}-\d{2}-\d{2})\.json$"),
    "stocks": re.compile(r"^stocks_(\d{4}-\d{2}-\d{2})\.json$"),
}

EDITION_DOCS_SUBDIR = {"en": config.DOCS_DIR, "ko": config.DOCS_DIR_KO, "stocks": config.DOCS_DIR_STOCKS}
EDITION_LABEL_EN = {"en": "Crypto (BTC/ETH)", "ko": "Korean (Upbit KRW)", "stocks": "Stocks (SPY/QQQ)"}


def _variant_rows(payload: dict) -> list:
    return [r for r in ((payload.get("rows") or []) + (payload.get("popular_combos") or []))
            if r.get("type") != "reference"]


def _row_key(r: dict):
    return (r["strategy_id"], r["params"], r["asset"], r["timeframe"])


def previous_snapshot(edition_key: str, current_as_of: str) -> dict | None:
    """The most recent results/history/<...>.json snapshot for this edition strictly before
    `current_as_of`, or None if this is the edition's first recorded week."""
    pat = _HIST_FILE_RE[edition_key]
    if not os.path.isdir(config.HISTORY_DIR):
        return None
    best_as_of, best_path = None, None
    for fn in os.listdir(config.HISTORY_DIR):
        m = pat.match(fn)
        if not m:
            continue
        as_of = m.group(1)
        if as_of >= current_as_of:
            continue
        if best_as_of is None or as_of > best_as_of:
            best_as_of, best_path = as_of, os.path.join(config.HISTORY_DIR, fn)
    if best_path is None:
        return None
    try:
        with open(best_path, encoding="utf-8") as f:
            return json.load(f)
    except (OSError, json.JSONDecodeError):
        return None


def _is_number(x) -> bool:
    return isinstance(x, (int, float)) and not isinstance(x, bool)


def compute_digest(edition_key: str, payload: dict) -> dict:
    as_of = payload["as_of"]
    tally = payload.get("tally") or {}
    curr_rows = _variant_rows(payload)
    prev_payload = previous_snapshot(edition_key, as_of)

    flips = []
    if prev_payload is not None:
        prev_idx = {_row_key(r): r.get("verdict") for r in _variant_rows(prev_payload)}
        for r in curr_rows:
            k = _row_key(r)
            if k in prev_idx:
                prev_v, curr_v = prev_idx[k], r.get("verdict")
                if prev_v is not None and curr_v is not None and prev_v != curr_v:
                    flips.append({
                        "strategy_id": r["strategy_id"], "strategy_name": r["strategy_name"],
                        "params": r["params"], "asset": r["asset"], "timeframe": r["timeframe"],
                        "from": prev_v, "to": curr_v,
                    })
    flips.sort(key=lambda f: (f["strategy_id"], f["asset"], f["timeframe"]))

    fee_candidates = [r for r in curr_rows if _is_number(r.get("oos", {}).get("fee_drag"))]
    biggest_fee = max(fee_candidates, key=lambda r: r["oos"]["fee_drag"]) if fee_candidates else None

    big_sample = [r for r in curr_rows
                  if _is_number(r.get("oos", {}).get("n_trades")) and r["oos"]["n_trades"] >= 30
                  and _is_number(r.get("oos", {}).get("profit_factor"))]
    best_pf = max(big_sample, key=lambda r: r["oos"]["profit_factor"]) if big_sample else None
    worst_pf = min(big_sample, key=lambda r: r["oos"]["profit_factor"]) if big_sample else None

    return {
        "edition": edition_key, "as_of": as_of, "tally": tally,
        "first_week": prev_payload is None,
        "prev_as_of": (prev_payload or {}).get("as_of"),
        "flips": flips, "biggest_fee_drag": biggest_fee,
        "best_pf": best_pf, "worst_pf": worst_pf,
        "n_rows": len(curr_rows),
    }


# ---------------------------------------------------------------------------
# Rendering — English
# ---------------------------------------------------------------------------

def _fmt_pct(x):
    return "-" if x is None else f"{x * 100:.1f}%"


def _fmt_num(x):
    if x is None:
        return "-"
    if x == "inf":
        return "inf"
    return f"{x:.2f}"


def _row_label_en(r: dict) -> str:
    return f"{r['strategy_name']} ({r['params']}) on {r['asset']} {r['timeframe']}"


def digest_title_en(d: dict) -> str:
    t = d["tally"]
    return (f"Week of {d['as_of']}: {t.get('ALIVE', 0)} alive / {t.get('FADING', 0)} fading / "
            f"{t.get('DEAD', 0)} dead — what changed")


def one_liners_en(d: dict) -> list:
    t = d["tally"]
    lines = [
        f"This run covers {d['n_rows']} strategy/asset/timeframe rows: "
        f"{t.get('ALIVE', 0)} ALIVE, {t.get('FADING', 0)} FADING, {t.get('DEAD', 0)} DEAD, "
        f"{t.get('TOO FEW TRADES', 0)} with too few trades."
    ]
    if d["first_week"]:
        lines.append("This is the first recorded week for this edition, so there is no prior "
                      "run to compare verdicts against.")
    elif not d["flips"]:
        lines.append(f"No verdict changed since the {d['prev_as_of']} run.")
    else:
        lines.append(f"{len(d['flips'])} verdict(s) changed since the {d['prev_as_of']} run.")
    if d["best_pf"] or d["worst_pf"]:
        parts = []
        if d["best_pf"]:
            parts.append(f"the highest was {_fmt_num(d['best_pf']['oos']['profit_factor'])} "
                         f"({_row_label_en(d['best_pf'])})")
        if d["worst_pf"] and d["worst_pf"] is not d["best_pf"]:
            parts.append(f"the lowest was {_fmt_num(d['worst_pf']['oos']['profit_factor'])} "
                         f"({_row_label_en(d['worst_pf'])})")
        lines.append("Among rows with at least 30 out-of-sample trades, " + "; ".join(parts) + ".")
    elif d["biggest_fee_drag"]:
        r = d["biggest_fee_drag"]
        lines.append(f"The largest fee drag this week was {_fmt_pct(r['oos']['fee_drag'])} on "
                     f"{_row_label_en(r)}.")
    return lines[:3]


def render_markdown_en(d: dict) -> str:
    lines = [f"# {digest_title_en(d)}", ""]
    for s in one_liners_en(d):
        lines.append(f"- {s}")
    lines.append("")
    if d["flips"]:
        lines.append("## Verdict changes")
        for f in d["flips"]:
            lines.append(f"- {f['strategy_name']} ({f['params']}) on {f['asset']} "
                         f"{f['timeframe']}: {f['from']} -> {f['to']}")
        lines.append("")
    if d["biggest_fee_drag"]:
        r = d["biggest_fee_drag"]
        lines.append(f"Biggest fee drag: {_fmt_pct(r['oos']['fee_drag'])} on {_row_label_en(r)}.")
        lines.append("")
    lines.append("This digest restates numbers already published on the site; it is not "
                  "investment advice.")
    return "\n".join(lines)


def render_html_body_en(d: dict) -> str:
    """The inner HTML used both by docs/digest/<as_of>.html and as the feed item's full content."""
    items = "".join(f"<li>{html.escape(s)}</li>" for s in one_liners_en(d))
    flips_html = ""
    if d["flips"]:
        flip_items = "".join(
            f"<li>{html.escape(f['strategy_name'])} ({html.escape(f['params'])}) on "
            f"{html.escape(f['asset'])} {html.escape(f['timeframe'])}: "
            f"{html.escape(f['from'])} &rarr; {html.escape(f['to'])}</li>"
            for f in d["flips"]
        )
        flips_html = f"<h2>Verdict changes</h2><ul>{flip_items}</ul>"
    fee_html = ""
    if d["biggest_fee_drag"]:
        r = d["biggest_fee_drag"]
        fee_html = (f"<p><strong>Biggest fee drag:</strong> {_fmt_pct(r['oos']['fee_drag'])} on "
                   f"{html.escape(_row_label_en(r))}.</p>")
    return f"<ul>{items}</ul>{flips_html}{fee_html}"


# ---------------------------------------------------------------------------
# Rendering — Korean (formal, no opinions, no banned words)
# ---------------------------------------------------------------------------

def _row_label_ko(r: dict) -> str:
    import build_site as bs
    name = bs.STRATEGY_NAME_KO.get(r["strategy_id"], r["strategy_name"])
    asset = bs.ASSET_LABEL_KO.get(r["asset"], r["asset"])
    tf = bs.TF_LABEL_KO.get(r["timeframe"], r["timeframe"])
    return f"{name}({r['params']}) {asset} {tf}"


def digest_title_ko(d: dict) -> str:
    t = d["tally"]
    return (f"{d['as_of']} 주간 요약: 생존 {t.get('ALIVE', 0)} / 약화 {t.get('FADING', 0)} / "
            f"사망 {t.get('DEAD', 0)} — 이번 주 변경 사항")


def one_liners_ko(d: dict) -> list:
    import build_site as bs
    t = d["tally"]
    lines = [
        f"이번 실행은 전략·자산·시간대 조합 {d['n_rows']}건을 다뤘습니다: "
        f"생존 {t.get('ALIVE', 0)}건, 약화 {t.get('FADING', 0)}건, 사망 {t.get('DEAD', 0)}건, "
        f"표본 부족 {t.get('TOO FEW TRADES', 0)}건입니다."
    ]
    if d["first_week"]:
        lines.append("이번 에디션의 첫 기록 주간이라 이전 실행과 비교할 판정이 없습니다.")
    elif not d["flips"]:
        lines.append(f"{d['prev_as_of']} 실행 이후 바뀐 판정이 없습니다.")
    else:
        lines.append(f"{d['prev_as_of']} 실행 이후 판정이 {len(d['flips'])}건 바뀌었습니다.")
    if d["best_pf"] or d["worst_pf"]:
        parts = []
        if d["best_pf"]:
            parts.append(f"가장 높은 값은 {_fmt_num(d['best_pf']['oos']['profit_factor'])}"
                         f"({_row_label_ko(d['best_pf'])})")
        if d["worst_pf"] and d["worst_pf"] is not d["best_pf"]:
            parts.append(f"가장 낮은 값은 {_fmt_num(d['worst_pf']['oos']['profit_factor'])}"
                         f"({_row_label_ko(d['worst_pf'])})")
        lines.append("거래가 30회 이상인 조합의 표본외 순손익비 중, " + ", ".join(parts) + "이었습니다.")
    elif d["biggest_fee_drag"]:
        r = d["biggest_fee_drag"]
        lines.append(f"이번 주 수수료 영향이 가장 컸던 조합은 {_row_label_ko(r)}로, "
                     f"{_fmt_pct(r['oos']['fee_drag'])}였습니다.")
    return lines[:3]


def render_markdown_ko(d: dict) -> str:
    lines = [f"# {digest_title_ko(d)}", ""]
    for s in one_liners_ko(d):
        lines.append(f"- {s}")
    lines.append("")
    if d["flips"]:
        lines.append("## 판정 변경")
        for f in d["flips"]:
            import build_site as bs
            name = bs.STRATEGY_NAME_KO.get(f["strategy_id"], f["strategy_name"])
            lines.append(f"- {name}({f['params']}) {f['asset']} {f['timeframe']}: "
                         f"{f['from']} -> {f['to']}")
        lines.append("")
    lines.append("본 요약은 이미 공개된 수치를 다시 정리한 것이며 투자 자문이 아닙니다.")
    return "\n".join(lines)


def render_html_body_ko(d: dict) -> str:
    items = "".join(f"<li>{html.escape(s)}</li>" for s in one_liners_ko(d))
    flips_html = ""
    if d["flips"]:
        import build_site as bs
        flip_items = "".join(
            f"<li>{html.escape(bs.STRATEGY_NAME_KO.get(f['strategy_id'], f['strategy_name']))}"
            f"({html.escape(f['params'])}) {html.escape(f['asset'])} {html.escape(f['timeframe'])}: "
            f"{html.escape(f['from'])} &rarr; {html.escape(f['to'])}</li>"
            for f in d["flips"]
        )
        flips_html = f"<h2>판정 변경</h2><ul>{flip_items}</ul>"
    return f"<ul>{items}</ul>{flips_html}"


# ---------------------------------------------------------------------------
# Page assembly (reuses build_site's CSS/disclaimer helpers — additive import, no changes there).
# ---------------------------------------------------------------------------

def _page_html(title: str, body: str, lang: str, back_href: str, disclaimer_html: str,
               index_href: str) -> str:
    import build_site as bs
    back_text = "전체 표로 돌아가기" if lang == "ko" else "Back to the full table"
    index_text = "모든 주간 요약" if lang == "ko" else "All weekly digests"
    return f"""<!doctype html>
<html lang="{lang}">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{html.escape(title)}</title>
<style>{bs.BASE_CSS}</style>
</head>
<body>
<header class="top"><h1>{html.escape(title)}</h1>
<p class="meta"><a href="{back_href}">{back_text}</a> &middot; <a href="{index_href}">{index_text}</a></p></header>
<main>{disclaimer_html}{body}{disclaimer_html}</main>
{config.ANALYTICS_SNIPPET}
</body>
</html>
"""


def build_for_edition(edition_key: str, payload: dict | None) -> dict | None:
    """Computes and writes the digest for one edition. Returns the digest dict (also persisted
    to results/digest/<edition>_<as_of>.json) or None if this edition has no data this week."""
    if not payload or not _variant_rows(payload):
        return None
    d = compute_digest(edition_key, payload)
    lang = "ko" if edition_key == "ko" else "en"

    if lang == "ko":
        title = digest_title_ko(d)
        body = render_html_body_ko(d)
        markdown = render_markdown_ko(d)
        import build_site as bs
        disclaimer_html = bs._disclaimer_block_ko()
    else:
        title = digest_title_en(d)
        body = render_html_body_en(d)
        markdown = render_markdown_en(d)
        disclaimer_html = f'<div class="disclaimer-block">{html.escape(config.LEGAL_DISCLAIMER)}</div>'

    d["title"] = title
    d["markdown"] = markdown
    d["html_body"] = body

    os.makedirs(DIGEST_DIR, exist_ok=True)
    with open(os.path.join(DIGEST_DIR, f"{edition_key}_{d['as_of']}.json"), "w", encoding="utf-8") as f:
        json.dump(d, f, indent=2, default=str)

    out_dir = os.path.join(EDITION_DOCS_SUBDIR[edition_key], "digest")
    os.makedirs(out_dir, exist_ok=True)
    page = _page_html(title, body, lang, "../index.html", disclaimer_html, "index.html")
    with open(os.path.join(out_dir, f"{d['as_of']}.html"), "w", encoding="utf-8") as f:
        f.write(page)

    _build_digest_index(edition_key, lang)
    return d


def _list_persisted_digests(edition_key: str) -> list:
    """All persisted results/digest/<edition>_<as_of>.json for one edition, sorted newest first,
    capped at 52 (spec S1: "keep last 52")."""
    if not os.path.isdir(DIGEST_DIR):
        return []
    prefix = f"{edition_key}_"
    out = []
    for fn in os.listdir(DIGEST_DIR):
        if fn.startswith(prefix) and fn.endswith(".json"):
            as_of = fn[len(prefix):-len(".json")]
            out.append(as_of)
    out.sort(reverse=True)
    return out[:52]


def _build_digest_index(edition_key: str, lang: str):
    as_ofs = _list_persisted_digests(edition_key)
    items = []
    for as_of in as_ofs:
        try:
            with open(os.path.join(DIGEST_DIR, f"{edition_key}_{as_of}.json"), encoding="utf-8") as f:
                d = json.load(f)
        except (OSError, json.JSONDecodeError):
            continue
        items.append((as_of, d.get("title", as_of)))
    heading = "주간 요약" if lang == "ko" else "Weekly digests"
    back_text = "전체 표로 돌아가기" if lang == "ko" else "Back to the full table"
    li = "".join(f'<li><a href="{html.escape(a)}.html">{html.escape(t)}</a></li>' for a, t in items)
    import build_site as bs
    doc = f"""<!doctype html>
<html lang="{lang}">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{html.escape(heading)} — {html.escape(config.PROJECT_NAME)}</title>
<style>{bs.BASE_CSS}</style>
</head>
<body>
<header class="top"><h1>{html.escape(heading)}</h1>
<p class="meta"><a href="../index.html">{back_text}</a></p></header>
<main><ul>{li}</ul></main>
{config.ANALYTICS_SNIPPET}
</body>
</html>
"""
    out_dir = os.path.join(EDITION_DOCS_SUBDIR[edition_key], "digest")
    os.makedirs(out_dir, exist_ok=True)
    with open(os.path.join(out_dir, "index.html"), "w", encoding="utf-8") as f:
        f.write(doc)


# ---------------------------------------------------------------------------
# Feeds — RSS 2.0 (docs/feed.xml, docs/ko/feed.xml) and JSON Feed 1.1 (docs/feed.json, en only).
# One item per weekly run, newest first, capped at 52. Items carry full HTML content (feed
# `<content:encoded>` / JSON Feed `content_html`) so importers like dev.to/Medium can ingest them.
# ---------------------------------------------------------------------------

def _feed_items(edition_key: str) -> list:
    items = []
    for as_of in _list_persisted_digests(edition_key):
        try:
            with open(os.path.join(DIGEST_DIR, f"{edition_key}_{as_of}.json"), encoding="utf-8") as f:
                items.append(json.load(f))
        except (OSError, json.JSONDecodeError):
            continue
    return items


def _item_url(edition_key: str, as_of: str) -> str:
    path = {"en": "", "ko": "/ko", "stocks": "/stocks"}[edition_key]
    return f"{config.PAGES_URL.rstrip('/')}{path}/digest/{as_of}.html"


def _rfc822(date_str: str) -> str:
    # date_str is a bare YYYY-MM-DD (as_of); render as a UTC midnight RFC-822 date for RSS.
    import datetime as _dt
    dt = _dt.datetime.strptime(date_str, "%Y-%m-%d").replace(tzinfo=_dt.timezone.utc)
    return dt.strftime("%a, %d %b %Y %H:%M:%S %z")


def write_rss(edition_key: str, out_path: str, feed_title: str, feed_desc: str):
    items = _feed_items(edition_key)
    entries = []
    for d in items:
        url = _item_url(edition_key, d["as_of"])
        entries.append(f"""  <item>
    <title>{html.escape(d.get('title', d['as_of']))}</title>
    <link>{html.escape(url)}</link>
    <guid isPermaLink="true">{html.escape(url)}</guid>
    <pubDate>{_rfc822(d['as_of'])}</pubDate>
    <description>{html.escape(d.get('title', ''))}</description>
    <content:encoded><![CDATA[{d.get('html_body', '')}]]></content:encoded>
  </item>""")
    doc = f"""<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0" xmlns:content="http://purl.org/rss/1.0/modules/content/">
<channel>
  <title>{html.escape(feed_title)}</title>
  <link>{html.escape(config.PAGES_URL)}</link>
  <description>{html.escape(feed_desc)}</description>
{chr(10).join(entries)}
</channel>
</rss>
"""
    os.makedirs(os.path.dirname(out_path), exist_ok=True)
    with open(out_path, "w", encoding="utf-8") as f:
        f.write(doc)


def write_json_feed(edition_key: str, out_path: str, feed_title: str):
    items = _feed_items(edition_key)
    feed_items = []
    for d in items:
        url = _item_url(edition_key, d["as_of"])
        feed_items.append({
            "id": url, "url": url, "title": d.get("title", d["as_of"]),
            "content_html": d.get("html_body", ""),
            "date_published": f"{d['as_of']}T00:00:00Z",
        })
    doc = {
        "version": "https://jsonfeed.org/version/1.1",
        "title": feed_title,
        "home_page_url": config.PAGES_URL,
        "feed_url": config.PAGES_URL.rstrip("/") + "/feed.json",
        "items": feed_items,
    }
    os.makedirs(os.path.dirname(out_path), exist_ok=True)
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(doc, f, indent=2)


def build_all(payloads: dict) -> dict:
    """payloads: {"en": payload_or_None, "ko": payload_or_None, "stocks": payload_or_None}.
    Writes each edition's digest page(s) + index, then the feeds (docs/feed.xml, docs/feed.json,
    docs/ko/feed.xml). Returns {edition: digest_dict_or_None}."""
    out = {}
    for edition_key, payload in payloads.items():
        out[edition_key] = build_for_edition(edition_key, payload)
        if out[edition_key] is None and edition_key in ("en", "ko"):
            # No data this run (e.g. ko/stocks with no local Upbit/Stooq file yet) — still write
            # a (possibly empty, listing only past weeks') digest index so the main page's
            # "Weekly digests" footer link never points at a missing file.
            _build_digest_index(edition_key, "ko" if edition_key == "ko" else "en")

    write_rss("en", os.path.join(EDITION_DOCS_SUBDIR["en"], "feed.xml"),
              f"{config.PROJECT_NAME} — weekly digest",
              "Weekly, plain-numbers digest of what changed in this week's strategy backtest run.")
    write_json_feed("en", os.path.join(EDITION_DOCS_SUBDIR["en"], "feed.json"),
                     f"{config.PROJECT_NAME} — weekly digest")
    write_rss("ko", os.path.join(EDITION_DOCS_SUBDIR["ko"], "feed.xml"),
              f"{config.PROJECT_NAME} — 주간 요약",
              "매주 전략 백테스트 결과 중 바뀐 부분만 숫자로 정리한 요약입니다.")
    return out


def _load_json_or_none(path: str) -> dict | None:
    if not os.path.exists(path):
        return None
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except (OSError, json.JSONDecodeError):
        return None


def main() -> int:
    """Standalone CLI entry point (this task's §S3 workflow step: `python digest.py`, run after
    the weekly pipeline and before the commit step). Reads each edition's just-written
    results/latest*.json straight off disk rather than taking an in-memory payload, so it can run
    as its own process step in CI. run_weekly.py --offline also calls digest.build_all() directly
    at the end of its own run (so a solo local run already produces every digest artifact, per
    this task's "run_weekly.py --offline must produce everything" requirement) — calling this
    script again afterwards in CI is a harmless, idempotent no-op recomputation of the same data,
    kept because spec_v3-style task instructions ask for a standalone `python digest.py` step."""
    payloads = {
        "en": _load_json_or_none(os.path.join(config.RESULTS_DIR, "latest.json")),
        "ko": _load_json_or_none(os.path.join(config.RESULTS_DIR, "latest_ko.json")),
        "stocks": _load_json_or_none(os.path.join(config.RESULTS_DIR, "latest_stocks.json")),
    }
    if not any(payloads.values()):
        print("digest.py: no results/latest*.json found yet (run run_weekly.py first) — nothing "
              "to do.")
        return 0
    build_all(payloads)
    print("digest.py: wrote digest pages + feeds.")
    return 0


if __name__ == "__main__":
    import sys
    sys.exit(main())
