"""
Attention "hit-and-run" study — does attention persist after a spike?  (see CRITERIA.md)

Runs in GitHub Actions (the dev box has no network). Pure pandas/numpy/requests.

Steps
  1. Universe: union of Wikimedia "top 1000 articles per month" (en.wikipedia, all-access) for
     2016-07 .. last full month, minus Main_Page/Special:/search pages; random sample of
     UNIVERSE_N articles with a fixed seed (mechanical, no hand-picking).
  2. Fetch daily user pageviews per article for the whole range (one request per article).
  3. Detect spikes per CRITERIA.md, measure persistence, split IS/OOS, write results.

Usage
  python run_study.py                 # full run (network)
  python run_study.py --synthetic     # self-test on synthetic series (no network)
"""
from __future__ import annotations
import argparse, json, os, random, sys, time
from datetime import date, datetime, timedelta

import numpy as np
import pandas as pd

HERE = os.path.dirname(os.path.abspath(__file__))
OUT_DIR = os.path.join(HERE, "results")
CACHE_DIR = os.path.join(HERE, "cache")

API = "https://wikimedia.org/api/rest_v1/metrics/pageviews"
UA = {"User-Agent": "deadoralive-research/0.1 (https://github.com/getjogak-sketch/deadoralive; cayleepak@gmail.com)"}

# ---- fixed parameters (CRITERIA.md) -------------------------------------------------------
START = "2016-07-01"
UNIVERSE_N = 3000
SEED = 20260907
BASELINE_DAYS = 30
MIN_VIEWS = 5000
K_LIST = [3, 5, 10]
H_LIST = [3, 7, 14]
COOLDOWN_DAYS = 14
RAMP_MULT = 1.5
IS_END = "2022-12-31"          # discovery
OOS_START = "2023-01-01"       # confirmation
EXCLUDE_PREFIXES = ("Main_Page", "Special:", "Wikipedia:", "File:", "Help:", "Portal:", "Talk:",
                    "User:", "Template:", "Category:", "Draft:", "-", "Index_(")


def _get(url, tries=4):
    import requests
    for i in range(tries):
        try:
            r = requests.get(url, headers=UA, timeout=30)
            if r.status_code == 404:
                return None
            if r.status_code == 429:
                time.sleep(2 + 2 * i); continue
            r.raise_for_status()
            return r.json()
        except Exception as e:  # noqa
            if i == tries - 1:
                print(f"[warn] giving up {url[:90]}: {e}")
                return None
            time.sleep(1 + i)


def month_iter(start: date, end: date):
    d = date(start.year, start.month, 1)
    while d <= end:
        yield d
        d = date(d.year + (d.month // 12), (d.month % 12) + 1, 1)


def build_universe(end_month: date) -> list[str]:
    names = set()
    for m in month_iter(date(2016, 7, 1), end_month):
        url = f"{API}/top/en.wikipedia/all-access/{m.year}/{m.month:02d}/all-days"
        js = _get(url)
        if not js:
            continue
        for a in js["items"][0]["articles"]:
            n = a["article"]
            if n.startswith(EXCLUDE_PREFIXES) or n.lower().startswith("search"):
                continue
            names.add(n)
        time.sleep(0.05)
    names = sorted(names)
    random.Random(SEED).shuffle(names)
    return names[:UNIVERSE_N], len(names)


def fetch_article(name: str, end: date) -> pd.Series | None:
    os.makedirs(CACHE_DIR, exist_ok=True)
    cp = os.path.join(CACHE_DIR, name.replace("/", "%2F") + ".json")
    if os.path.exists(cp):
        with open(cp) as f:
            js = json.load(f)
    else:
        from urllib.parse import quote
        url = (f"{API}/per-article/en.wikipedia/all-access/user/{quote(name, safe='')}/daily/"
               f"{START.replace('-', '')}00/{end.strftime('%Y%m%d')}00")
        js = _get(url)
        if js is None:
            return None
        with open(cp, "w") as f:
            json.dump(js, f)
    items = js.get("items", [])
    if not items:
        return None
    s = pd.Series({pd.Timestamp(it["timestamp"][:8]): it["views"] for it in items}).sort_index()
    s = s.asfreq("D").fillna(0).astype(float)
    return s


# ---- core analysis (pure, testable) --------------------------------------------------------

def detect_events(s: pd.Series, k: float):
    """Return list of spike events for one series. Signal uses only data <= t (baseline is the
    median of the PRIOR 30 days, shifted by one so day t is not in its own baseline)."""
    v = s.to_numpy(dtype=float)
    idx = s.index
    base = s.rolling(BASELINE_DAYS, min_periods=BASELINE_DAYS).median().shift(1).to_numpy()
    events = []
    last_t = -10**9
    n = len(v)
    for t in range(BASELINE_DAYS + 1, n - max(H_LIST) - 1):
        b = base[t]
        if not np.isfinite(b) or b <= 0:
            continue
        if v[t] >= k * b and v[t] >= MIN_VIEWS and (t - last_t) > COOLDOWN_DAYS:
            last_t = t
            kind = "ramp" if v[t - 1] >= RAMP_MULT * b else "sudden"
            ev = {"date": idx[t].strftime("%Y-%m-%d"), "k": k, "kind": kind,
                  "views_t": float(v[t]), "baseline": float(b), "ratio_t": float(v[t] / b)}
            for H in H_LIST:
                ev[f"R_{H}"] = float(v[t + 1:t + 1 + H].mean() / b)
            fut = v[t + 1:t + 1 + 14]
            ev["peak_after_t"] = bool(fut.max() > v[t])
            below = np.flatnonzero(v[t + 1:t + 1 + 30] < 0.5 * v[t])
            ev["half_life_days"] = int(below[0] + 1) if len(below) else 31   # 31 = ">30"
            events.append(ev)
    return events


def summarize(df: pd.DataFrame) -> dict:
    out = {}
    for k in K_LIST:
        for period, sub in [("IS", df[(df.k == k) & (df.date <= IS_END)]),
                            ("OOS", df[(df.k == k) & (df.date >= OOS_START)])]:
            for kind in ["all", "sudden", "ramp"]:
                g = sub if kind == "all" else sub[sub.kind == kind]
                key = f"k{k}/{period}/{kind}"
                if len(g) == 0:
                    out[key] = {"N": 0}; continue
                out[key] = {
                    "N": int(len(g)),
                    "median_R_3": float(g.R_3.median()), "median_R_7": float(g.R_7.median()),
                    "median_R_14": float(g.R_14.median()),
                    "share_R7_ge_3": float((g.R_7 >= 3).mean()),
                    "peak_after_t_share": float(g.peak_after_t.mean()),
                    "median_half_life_days": float(g.half_life_days.median()),
                    "share_half_life_le_1": float((g.half_life_days <= 1).mean()),
                }
    return out


def verdict(summary: dict) -> dict:
    """CRITERIA.md decision, mechanically applied on k=5."""
    def ok_go(s):
        return s.get("N", 0) >= 100 and s["median_R_7"] >= 3.0 and s["peak_after_t_share"] >= 0.40 and s["median_half_life_days"] >= 3
    def ok_nogo(s):
        return s.get("N", 0) >= 100 and s["median_half_life_days"] <= 1 and s["peak_after_t_share"] < 0.20
    a_is, a_oos = summary.get("k5/IS/all", {}), summary.get("k5/OOS/all", {})
    r_is, r_oos = summary.get("k5/IS/ramp", {}), summary.get("k5/OOS/ramp", {})
    if a_is.get("N", 0) < 100 or a_oos.get("N", 0) < 100:
        return {"verdict": "HOLD", "reason": "N < 100 in IS or OOS"}
    if ok_go(a_is) and ok_go(a_oos):
        return {"verdict": "GO", "reason": "all-events criteria met in IS and OOS"}
    if ok_nogo(a_is) or ok_nogo(a_oos):
        return {"verdict": "NO-GO", "reason": "half-life <= 1 day and peak rarely after t"}
    if r_is.get("N", 0) >= 100 and r_oos.get("N", 0) >= 100 and ok_go(r_is) and ok_go(r_oos):
        return {"verdict": "CONDITIONAL GO (ramp only)", "reason": "ramp-type events meet GO in IS and OOS"}
    return {"verdict": "HOLD", "reason": "neither GO nor NO-GO thresholds met"}


def write_results(events: pd.DataFrame, summary: dict, vd: dict, meta: dict):
    os.makedirs(OUT_DIR, exist_ok=True)
    events.to_csv(os.path.join(OUT_DIR, "events.csv"), index=False)
    with open(os.path.join(OUT_DIR, "summary.json"), "w") as f:
        json.dump({"meta": meta, "summary": summary, "verdict": vd}, f, indent=2)
    lines = [f"# Attention spike persistence — results ({meta.get('run_date')})", "",
             f"Universe: {meta.get('universe_n')} articles sampled from {meta.get('universe_pool')} "
             f"(top-1000 monthly lists, {START}..{meta.get('end')}); fetched OK: {meta.get('fetched_ok')}.", "",
             f"**Verdict (k=5, per CRITERIA.md): {vd['verdict']}** — {vd['reason']}", "",
             "| key | N | med R_3 | med R_7 | med R_14 | R_7≥3 share | peak after t | med half-life (d) | half-life≤1 |",
             "|---|---|---|---|---|---|---|---|---|"]
    for key, s in summary.items():
        if s.get("N", 0) == 0:
            lines.append(f"| {key} | 0 | | | | | | | |"); continue
        lines.append(f"| {key} | {s['N']} | {s['median_R_3']:.2f} | {s['median_R_7']:.2f} | {s['median_R_14']:.2f} | "
                     f"{s['share_R7_ge_3']:.0%} | {s['peak_after_t_share']:.0%} | {s['median_half_life_days']:.0f} | {s['share_half_life_le_1']:.0%} |")
    lines += ["", "R_H = mean views over days t+1..t+H divided by the pre-spike 30-day median baseline.",
              "peak after t = share of events whose 14-day maximum comes after the spike day (i.e. acting on t+1 was not too late).",
              "half-life = first day after t on which views fall below 50% of the spike-day views (31 = never within 30 days)."]
    with open(os.path.join(OUT_DIR, "RESULTS.md"), "w") as f:
        f.write("\n".join(lines) + "\n")


def run_synthetic():
    """Self-test: build series with known behaviour and check the detector/metrics."""
    rng = np.random.default_rng(0)
    idx = pd.date_range("2016-07-01", "2026-08-31", freq="D")
    n = len(idx)
    all_events = []
    # (a) sudden spike that dies next day; (b) ramp that persists a week
    for kind in ["dies", "persists"]:
        v = rng.poisson(8000, n).astype(float)
        for t0 in range(100, n - 60, 400):
            if kind == "dies":
                v[t0] = 80000
            else:
                v[t0 - 1] = 20000; v[t0] = 80000; v[t0 + 1:t0 + 8] = 60000
        s = pd.Series(v, index=idx)
        for k in K_LIST:
            for ev in detect_events(s, k):
                ev["article"] = kind; all_events.append(ev)
    df = pd.DataFrame(all_events)
    d = df[(df.k == 5) & (df.article == "dies")]
    p = df[(df.k == 5) & (df.article == "persists")]
    assert len(d) > 5 and len(p) > 5, "detector found too few events"
    assert d.half_life_days.median() == 1 and (d.kind == "sudden").all(), "dies-series should be sudden with half-life 1"
    assert p.R_7.median() > 5 and (p.kind == "ramp").all(), "persist-series should be ramp with high R_7"
    assert (~d.peak_after_t).all(), "dies-series peak must be at t"
    # lookahead check: detection on a truncated series must match on the overlap
    s = pd.Series(v, index=idx)
    full = [e["date"] for e in detect_events(s, 5)]
    cut = [e["date"] for e in detect_events(s.iloc[: n - 500], 5)]
    assert cut == [x for x in full if x <= cut[-1]], "truncation changed earlier detections"
    print("[synthetic] OK — detector, metrics, and no-lookahead checks pass")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--synthetic", action="store_true")
    ap.add_argument("--limit", type=int, default=UNIVERSE_N)
    args = ap.parse_args()
    if args.synthetic:
        run_synthetic(); return 0

    today = date.today()
    last_full_month = date(today.year, today.month, 1) - timedelta(days=1)
    end_month = date(last_full_month.year, last_full_month.month, 1)
    end = last_full_month
    print(f"[study] universe from monthly top lists up to {end_month:%Y-%m} ...")
    universe, pool = build_universe(end_month)
    universe = universe[: args.limit]
    print(f"[study] pool={pool}, sampled={len(universe)}")

    all_events, ok = [], 0
    t0 = time.time()
    for i, name in enumerate(universe):
        s = fetch_article(name, end)
        if s is None or len(s) < 400:
            continue
        ok += 1
        for k in K_LIST:
            for ev in detect_events(s, k):
                ev["article"] = name; all_events.append(ev)
        if i % 200 == 0:
            print(f"[study] {i}/{len(universe)} fetched_ok={ok} events={len(all_events)} {time.time()-t0:.0f}s")
        time.sleep(0.02)
    df = pd.DataFrame(all_events)
    if df.empty:
        print("[study] no events"); return 1
    summary = summarize(df)
    vd = verdict(summary)
    meta = {"run_date": str(today), "end": str(end), "universe_pool": pool, "universe_n": len(universe),
            "fetched_ok": ok, "params": {"BASELINE_DAYS": BASELINE_DAYS, "MIN_VIEWS": MIN_VIEWS,
            "K_LIST": K_LIST, "H_LIST": H_LIST, "COOLDOWN_DAYS": COOLDOWN_DAYS, "RAMP_MULT": RAMP_MULT,
            "IS_END": IS_END, "OOS_START": OOS_START, "SEED": SEED}}
    write_results(df, summary, vd, meta)
    print(json.dumps(vd)); return 0


if __name__ == "__main__":
    sys.exit(main())
