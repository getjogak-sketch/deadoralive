"""
run_demand.py — search-demand study for candidate "seas" (see CRITERIA.md, pre-registered before
this script's results were ever seen).

Runs in GitHub Actions (`.github/workflows/research-demand.yml`, workflow_dispatch) — this dev box
has no network, so `pytrends` (Google Trends' unofficial API wrapper) is exercised here only via
`--synthetic`, on fabricated series with a known answer. The real network path (`fetch_group`) is
written against pytrends' own documented `TrendReq.build_payload`/`interest_over_time`/
`related_queries` calls and is never invoked by the self-test.

Usage
    python run_demand.py                 # full run (network, needs `pip install pytrends`)
    python run_demand.py --synthetic     # self-test on fabricated series (no network, no pytrends)
"""
from __future__ import annotations
import argparse
import csv
import json
import os
import random
import sys
import time
from datetime import date

import numpy as np
import pandas as pd

HERE = os.path.dirname(os.path.abspath(__file__))
OUT_DIR = os.path.join(HERE, "results")
KEYWORDS_PATH = os.path.join(HERE, "keywords.yml")

TIMEFRAME = "today 5-y"
ANCHOR_KEYWORD = "supertrend strategy"
ANCHOR_GROUP = "anchor_crypto"
QUALIFY_RATIO_MIN = 0.5
QUALIFY_YOY_MAX_DECLINE = -0.30  # a candidate whose own YoY change is <= this fails, regardless of ratio
MAX_KEYWORDS_PER_BATCH = 5
SLEEP_BETWEEN_BATCHES = 1.0
RELATED_QUERIES_SHOWN = 5


# ---------------------------------------------------------------------------
# Tiny hand-rolled parser for keywords.yml's own controlled shape (see that file's header
# comment). Not a general YAML parser — deliberately narrow, so it can be trusted without an
# external dependency (spec's own instruction: "no external lib").
# ---------------------------------------------------------------------------

def load_keywords_yaml(path: str) -> dict:
    with open(path, encoding="utf-8") as f:
        raw_lines = f.readlines()

    lines = []
    for ln in raw_lines:
        stripped = ln.rstrip("\n")
        if not stripped.strip() or stripped.strip().startswith("#"):
            continue
        lines.append(stripped)

    groups: dict[str, dict] = {}
    i, n = 0, len(lines)
    while i < n:
        line = lines[i]
        indent = len(line) - len(line.lstrip(" "))
        if indent != 0:
            raise ValueError(f"keywords.yml: unexpected indentation at top level: {line!r}")
        if not line.rstrip().endswith(":"):
            raise ValueError(f"keywords.yml: expected a 'group_name:' line, got: {line!r}")
        group_name = line.strip()[:-1].strip()
        i += 1

        body = []
        while i < n and (len(lines[i]) - len(lines[i].lstrip(" "))) > 0:
            body.append(lines[i])
            i += 1

        geo = None
        keywords: list[str] = []
        j, m = 0, len(body)
        while j < m:
            content = body[j].strip()
            if content.startswith("- "):
                keywords.append(content[2:].strip().strip('"').strip("'"))
                j += 1
            elif content.startswith("geo:"):
                geo = content.split(":", 1)[1].strip()
                j += 1
            elif content.rstrip().endswith("keywords:"):
                j += 1
                while j < m and body[j].strip().startswith("- "):
                    keywords.append(body[j].strip()[2:].strip().strip('"').strip("'"))
                    j += 1
            else:
                raise ValueError(f"keywords.yml: unrecognized line in group {group_name!r}: "
                                  f"{body[j]!r}")

        if not keywords:
            raise ValueError(f"keywords.yml: group {group_name!r} has no keywords")
        groups[group_name] = {"keywords": keywords, "geo": geo}

    return groups


# ---------------------------------------------------------------------------
# Pure scoring logic (CRITERIA.md) — no network, fully unit-testable via --synthetic.
# ---------------------------------------------------------------------------

def summarize_keyword(series: "pd.Series | None") -> dict:
    """One keyword's median-12-month interest and YoY change from a weekly 0-100 interest series
    (as returned, per-column, by pytrends' interest_over_time for a `timeframe="today 5-y"`
    request). `status` is "N/A" whenever Trends returned nothing usable for this keyword — never
    treated as a disqualifying 0, per CRITERIA.md."""
    if series is None or len(series) == 0 or series.dropna().empty:
        return {"median_12mo": None, "yoy_pct": None, "status": "N/A"}

    s = series.dropna().sort_index()
    last_date = s.index.max()
    last_12 = s[s.index > last_date - pd.Timedelta(days=365)]
    prior_12 = s[(s.index <= last_date - pd.Timedelta(days=365)) &
                 (s.index > last_date - pd.Timedelta(days=730))]

    if last_12.empty:
        return {"median_12mo": None, "yoy_pct": None, "status": "N/A"}

    median_12mo = float(last_12.median())
    if len(prior_12) and prior_12.median() not in (0, None) and not np.isnan(prior_12.median()):
        prior_median = float(prior_12.median())
        yoy_pct = (median_12mo - prior_median) / prior_median if prior_median != 0 else None
    else:
        yoy_pct = None  # not enough history for a YoY comparison — not counted as a decline

    return {"median_12mo": median_12mo, "yoy_pct": yoy_pct, "status": "OK"}


def qualifies(candidate: dict, anchor_median_12mo: "float | None"):
    """Returns (qualifies: bool | "N/A", ratio: float | None), per CRITERIA.md's rule. "N/A" if
    Trends had nothing for this keyword, or if the anchor itself had nothing in this batch (a
    ratio against an unknown anchor is meaningless, not a disqualify)."""
    if candidate["status"] != "OK" or candidate["median_12mo"] is None:
        return "N/A", None
    if anchor_median_12mo is None or anchor_median_12mo == 0:
        return "N/A", None

    ratio = candidate["median_12mo"] / anchor_median_12mo
    declining = candidate["yoy_pct"] is not None and candidate["yoy_pct"] <= QUALIFY_YOY_MAX_DECLINE
    return (ratio >= QUALIFY_RATIO_MIN and not declining), ratio


def pick_best_keyword(summaries: dict) -> "str | None":
    """The candidate group's own best keyword: whichever has the higher median 12-month interest
    among keywords Trends actually returned data for. None if every keyword in the group is N/A."""
    ok = {kw: s["median_12mo"] for kw, s in summaries.items()
          if s["status"] == "OK" and s["median_12mo"] is not None}
    if not ok:
        return None
    return max(ok, key=ok.get)


# ---------------------------------------------------------------------------
# Network path (pytrends) — never exercised by --synthetic; written against pytrends' documented
# API (https://github.com/GeneralMills/pytrends#interest-over-time and #related-queries).
# ---------------------------------------------------------------------------

def fetch_batch(keywords: list, geo: str = "", tries: int = 4):
    """Returns (interest_df: pd.DataFrame | None, related: dict). Handles 429/empty/transport
    errors by retrying with a polite backoff and, on final failure, returning (None, {}) rather
    than raising — a group with an unreachable Trends endpoint is a warning, never a crash."""
    from pytrends.request import TrendReq

    for attempt in range(tries):
        try:
            pt = TrendReq(hl="en-US", tz=0)
            pt.build_payload(keywords, timeframe=TIMEFRAME, geo=geo or "")
            df = pt.interest_over_time()
            if df is None or df.empty:
                print(f"[warn] empty interest_over_time for batch {keywords} (geo={geo!r})")
                return None, {}
            if "isPartial" in df.columns:
                df = df.drop(columns=["isPartial"])
            try:
                related = pt.related_queries()
            except Exception as e:  # related queries failing must not sink the interest data
                print(f"[warn] related_queries failed for batch {keywords}: {e}")
                related = {}
            return df, related
        except Exception as e:
            msg = str(e)
            is_rate_limited = "429" in msg or "TooManyRequests" in msg
            if attempt < tries - 1:
                delay = (3.0 if is_rate_limited else 1.5) * (attempt + 1) + random.uniform(0, 1)
                print(f"[warn] batch {keywords} (geo={geo!r}) failed ({e}); retrying in {delay:.1f}s")
                time.sleep(delay)
                continue
            print(f"[warn] giving up on batch {keywords} (geo={geo!r}): {e}")
            return None, {}
    return None, {}


def _related_top5(related: dict, keyword: str) -> list:
    """Up to 5 related queries for one keyword, preferring 'top' and falling back to 'rising'
    when 'top' is empty/absent — spec: "top 5 related queries" (unspecified which of the two, so
    the more directly comparable 'top' list is preferred)."""
    entry = (related or {}).get(keyword) or {}
    for kind in ("top", "rising"):
        df = entry.get(kind)
        if df is not None and not df.empty:
            out = []
            for _, row in df.head(RELATED_QUERIES_SHOWN).iterrows():
                out.append({"query": str(row.get("query", "")), "value": row.get("value"),
                            "kind": kind})
            return out
    return []


def run_group(name: str, keywords: list, geo: str, anchor_in_group: bool):
    """One CRITERIA.md group: builds the batch (adding the anchor keyword unless the group
    already contains it), fetches, scores every keyword, and returns the per-group result dict
    plus the raw interest_over_time DataFrame (for the CSV) and related-queries dict."""
    batch = list(keywords)
    if not anchor_in_group:
        batch = batch[: MAX_KEYWORDS_PER_BATCH - 1] + [ANCHOR_KEYWORD]
    else:
        batch = batch[:MAX_KEYWORDS_PER_BATCH]

    df, related = fetch_batch(batch, geo=geo)
    time.sleep(SLEEP_BETWEEN_BATCHES)

    summaries = {}
    for kw in batch:
        series = df[kw] if (df is not None and kw in df.columns) else None
        summaries[kw] = summarize_keyword(series)

    anchor_median = summaries.get(ANCHOR_KEYWORD, {}).get("median_12mo")
    rows = []
    for kw in keywords:  # report only this group's OWN keywords, not the borrowed anchor row
        s = summaries.get(kw, {"median_12mo": None, "yoy_pct": None, "status": "N/A"})
        q, ratio = qualifies(s, anchor_median)
        rows.append({
            "keyword": kw, "median_12mo": s["median_12mo"], "ratio_vs_anchor": ratio,
            "yoy_pct": s["yoy_pct"], "status": s["status"], "qualifies": q,
            "related": _related_top5(related, kw),
        })

    best_kw = pick_best_keyword({kw: summaries[kw] for kw in keywords if kw in summaries})
    best_row = next((r for r in rows if r["keyword"] == best_kw), None)
    group_result = {
        "group": name, "geo": geo or "worldwide", "anchor_median_12mo_this_batch": anchor_median,
        "best_keyword": best_kw,
        "group_qualifies": best_row["qualifies"] if best_row else "N/A",
        "keywords": rows,
    }
    return group_result, df, related


# ---------------------------------------------------------------------------
# Output writers
# ---------------------------------------------------------------------------

def _fmt_pct(x):
    return "-" if x is None else f"{x * 100:.0f}%"


def _fmt_ratio(x):
    return "-" if x is None else f"{x:.2f}x"


def _fmt_q(x):
    return {"N/A": "N/A", True: "YES", False: "no"}.get(x, str(x))


def write_results(all_groups: list, meta: dict):
    os.makedirs(OUT_DIR, exist_ok=True)

    for g in all_groups:
        if g.get("_interest_df") is not None:
            path = os.path.join(OUT_DIR, f"{g['group']}_interest.csv")
            g["_interest_df"].to_csv(path, index_label="date")
        if g.get("_related_rows"):
            path = os.path.join(OUT_DIR, f"{g['group']}_related.csv")
            with open(path, "w", newline="", encoding="utf-8") as f:
                w = csv.DictWriter(f, fieldnames=["keyword", "kind", "query", "value"])
                w.writeheader()
                w.writerows(g["_related_rows"])

    lines = [f"# Search-demand study — results ({meta.get('run_date')})", "",
              f"Anchor keyword: **{ANCHOR_KEYWORD}** (group `{ANCHOR_GROUP}`). Timeframe: "
              f"`{TIMEFRAME}`. Qualify rule: ratio vs. anchor >= {QUALIFY_RATIO_MIN:.0%} of "
              f"anchor's median 12-month interest (same batch) AND not a >{-QUALIFY_YOY_MAX_DECLINE:.0%} "
              "YoY decline — see CRITERIA.md.", ""]

    for g in all_groups:
        lines.append(f"## {g['group']}" + (f" (geo={g['geo']})" if g["geo"] != "worldwide" else ""))
        lines.append("")
        lines.append(f"Best keyword: **{g['best_keyword'] or 'N/A'}** &mdash; "
                      f"group qualifies: **{_fmt_q(g['group_qualifies'])}**")
        lines.append("")
        lines.append("| keyword | median 12-mo interest | ratio vs anchor | YoY change | qualifies? | top related queries |")
        lines.append("|---|---|---|---|---|---|")
        for r in g["keywords"]:
            related_txt = "; ".join(f"{x['query']} ({x['value']})" for x in r["related"]) or "-"
            median_txt = "-" if r["median_12mo"] is None else f"{r['median_12mo']:.0f}"
            lines.append(f"| {r['keyword']} | {median_txt} | {_fmt_ratio(r['ratio_vs_anchor'])} | "
                          f"{_fmt_pct(r['yoy_pct'])} | {_fmt_q(r['qualifies'])} | {related_txt} |")
        lines.append("")

    lines.append("Note: the `korean` group's ratio is measured in a `geo=KR`-only batch, a "
                  "different normalization basis from the worldwide batches above it — a weaker, "
                  "not directly comparable signal (see CRITERIA.md's own caveat).")
    with open(os.path.join(OUT_DIR, "RESULTS.md"), "w", encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n")

    summary = {
        "meta": meta,
        "groups": [{k: v for k, v in g.items() if not k.startswith("_")} for g in all_groups],
    }
    with open(os.path.join(OUT_DIR, "summary.json"), "w", encoding="utf-8") as f:
        json.dump(summary, f, indent=2, default=str)


# ---------------------------------------------------------------------------
# Self-test — fabricated series with a known answer, no network, no pytrends import.
# ---------------------------------------------------------------------------

def run_synthetic():
    idx = pd.date_range(end=pd.Timestamp.today().normalize(), periods=260, freq="W")  # ~5 years

    # Anchor: flat strong interest throughout.
    anchor = pd.Series(80.0, index=idx)
    a = summarize_keyword(anchor)
    assert a["status"] == "OK" and abs(a["median_12mo"] - 80.0) < 1e-9, a

    # Qualifying candidate: flat at 60% of anchor's level, no YoY decline (ratio 0.6 >= 0.5).
    strong = pd.Series(48.0, index=idx)
    s = summarize_keyword(strong)
    q, ratio = qualifies(s, a["median_12mo"])
    assert q is True and abs(ratio - 0.6) < 1e-9, (q, ratio)

    # Too weak: flat at 20% of anchor's level (ratio 0.2 < 0.5) -> does not qualify.
    weak = pd.Series(16.0, index=idx)
    w = summarize_keyword(weak)
    q, ratio = qualifies(w, a["median_12mo"])
    assert q is False and ratio is not None and ratio < 0.5, (q, ratio)

    # High level but in a >30% YoY decline -> disqualified on trend even though ratio is fine.
    vals = np.full(len(idx), 40.0)
    vals[-104:-52] = 100.0   # prior-12-month window: high
    vals[-52:] = 50.0        # last-12-month window: down 50% from prior, but still 0.625x anchor
    declining = pd.Series(vals, index=idx)
    d = summarize_keyword(declining)
    q, ratio = qualifies(d, a["median_12mo"])
    assert d["yoy_pct"] is not None and d["yoy_pct"] <= QUALIFY_YOY_MAX_DECLINE, d
    assert q is False, (q, ratio, d)

    # Empty / all-NaN series -> N/A, never scored as a disqualify.
    empty = pd.Series(dtype=float)
    e = summarize_keyword(empty)
    assert e["status"] == "N/A"
    q, ratio = qualifies(e, a["median_12mo"])
    assert q == "N/A" and ratio is None, (q, ratio)

    # N/A anchor (this batch's anchor came back empty) -> every candidate in that batch is N/A,
    # not silently scored against a missing denominator.
    q, ratio = qualifies(s, None)
    assert q == "N/A" and ratio is None, (q, ratio)

    # pick_best_keyword: highest median wins; all-N/A group -> None.
    summaries = {"a": {"status": "OK", "median_12mo": 10.0}, "b": {"status": "OK", "median_12mo": 55.0},
                 "c": {"status": "N/A", "median_12mo": None}}
    assert pick_best_keyword(summaries) == "b"
    assert pick_best_keyword({"x": {"status": "N/A", "median_12mo": None}}) is None

    # keywords.yml parses: anchor present in anchor_crypto, korean group has geo=KR and its own
    # keyword list, and no group is empty.
    groups = load_keywords_yaml(KEYWORDS_PATH)
    assert ANCHOR_KEYWORD in groups[ANCHOR_GROUP]["keywords"], groups[ANCHOR_GROUP]
    assert groups["korean"]["geo"] == "KR", groups["korean"]
    assert len(groups["korean"]["keywords"]) == 4, groups["korean"]
    for name, g in groups.items():
        assert len(g["keywords"]) >= 1, name

    print("[synthetic] OK — median/YoY/qualify/N-A scoring and the keywords.yml parser all pass")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--synthetic", action="store_true")
    args = ap.parse_args()
    if args.synthetic:
        run_synthetic()
        return 0

    groups = load_keywords_yaml(KEYWORDS_PATH)
    all_groups = []
    for name, g in groups.items():
        print(f"[demand] group={name} geo={g['geo'] or 'worldwide'} keywords={g['keywords']}")
        result, df, related = run_group(name, g["keywords"], g["geo"] or "",
                                          anchor_in_group=(name == ANCHOR_GROUP))
        result["_interest_df"] = df
        related_rows = []
        for kw in g["keywords"]:
            for item in _related_top5(related, kw):
                related_rows.append({"keyword": kw, **item})
        result["_related_rows"] = related_rows
        all_groups.append(result)

    meta = {"run_date": str(date.today()), "anchor_keyword": ANCHOR_KEYWORD, "timeframe": TIMEFRAME,
            "qualify_ratio_min": QUALIFY_RATIO_MIN, "qualify_yoy_max_decline": QUALIFY_YOY_MAX_DECLINE}
    write_results(all_groups, meta)
    n_qualify = sum(1 for g in all_groups if g["group_qualifies"] is True)
    print(f"[demand] done — {n_qualify}/{len(all_groups)} groups qualify")
    return 0


if __name__ == "__main__":
    sys.exit(main())
