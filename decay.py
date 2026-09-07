"""
decay.py — Strategy Decay Index (task R2).

Reads results/history/*.json (the same weekly snapshots seo_pages.py already reads for its
verdict-history line — see its `_HIST_FILE_RE`, reused verbatim here rather than duplicated) and,
per edition per as_of, turns the textbook registry's verdict tally into:

    alive, fading, dead, too_few   — each verdict's share of that as_of's textbook rows (0..1)
    decay_index                    — share DEAD among rows with >=10 OOS trades, i.e.
                                      dead / (alive + fading + dead)  (TOO FEW rows, by
                                      verdict.py's own definition, always have <10 OOS trades, so
                                      excluding them from the denominator is exactly "rows with
                                      >=10 OOS trades")
    n                               — total textbook rows that as_of (alive+fading+dead+too_few)

Nothing here reruns a backtest or reads a raw row — every one of run_weekly.py's history payloads
already carries a `tally` field that is precisely this same count, computed the same way
(vd.tally() over "popular_combos"-EXCLUDED, reference-row-EXCLUDED verdicts — see run_weekly.py's
own `tally = vd.tally(...)` call for both the main and Korean/stocks editions) — task R2 asks for
"share of textbook rows", and payload["tally"] already IS that count. This module is read-only
bookkeeping on top of it, the same spirit as run_weekly.py's own `_write_api_v1`.

An as_of with zero textbook rows (an edition with no local data that week — e.g. the Korean/stocks
editions in this dev environment) is skipped entirely rather than recorded as a 0%/None point:
"no data this week" is not the same claim as "0% of strategies are dead this week".
"""
from __future__ import annotations
import json
import os

import config
import verdict as vd

# Same filename convention seo_pages.py's _HIST_FILE_RE already established: "<as_of>.json" (en),
# "ko_<as_of>.json", "stocks_<as_of>.json". Duplicated as plain strings (not imported from
# seo_pages) to avoid a decay.py -> seo_pages.py import for one regex; both are read-only and
# independently unit-tested against the same real file-naming convention run_weekly.py writes.
import re

_HIST_FILE_RE = {
    "en": re.compile(r"^(\d{4}-\d{2}-\d{2})\.json$"),
    "ko": re.compile(r"^ko_(\d{4}-\d{2}-\d{2})\.json$"),
    "stocks": re.compile(r"^stocks_(\d{4}-\d{2}-\d{2})\.json$"),
    "macro": re.compile(r"^macro_(\d{4}-\d{2}-\d{2})\.json$"),
}


def _index_point(as_of: str, tally: dict) -> dict | None:
    alive = tally.get(vd.ALIVE, 0)
    fading = tally.get(vd.FADING, 0)
    dead = tally.get(vd.DEAD, 0)
    too_few = tally.get(vd.TOO_FEW, 0)
    n = alive + fading + dead + too_few
    if n == 0:
        return None
    ge10 = alive + fading + dead
    return {
        "as_of": as_of,
        "alive": alive / n,
        "fading": fading / n,
        "dead": dead / n,
        "too_few": too_few / n,
        "decay_index": (dead / ge10) if ge10 > 0 else None,
        "n": n,
    }


def compute_index_history(edition_key: str, history_dir: str | None = None) -> list[dict]:
    """The full {as_of, alive, fading, dead, too_few, decay_index, n} series for one edition,
    ascending by as_of, read from every results/history/<...>.json snapshot that matches this
    edition's filename pattern and has at least one textbook row."""
    history_dir = history_dir or config.HISTORY_DIR
    pat = _HIST_FILE_RE[edition_key]
    points = []
    if not os.path.isdir(history_dir):
        return points
    for fn in sorted(os.listdir(history_dir)):
        m = pat.match(fn)
        if not m:
            continue
        as_of = m.group(1)
        try:
            with open(os.path.join(history_dir, fn), encoding="utf-8") as f:
                snap = json.load(f)
        except (OSError, json.JSONDecodeError):
            continue
        point = _index_point(as_of, snap.get("tally") or {})
        if point is not None:
            points.append(point)
    points.sort(key=lambda p: p["as_of"])
    return points


def write_index_history(edition_key: str, out_root: str | None = None,
                         history_dir: str | None = None) -> str:
    """Writes docs/api/v1/<edition>/index_history.json — additive alongside the latest.json/
    history/ tree run_weekly.py's `_write_api_v1` already writes there; this function touches no
    file that one writes."""
    out_root = out_root or os.path.join(config.DOCS_DIR, "api", "v1")
    out_dir = os.path.join(out_root, edition_key)
    os.makedirs(out_dir, exist_ok=True)
    points = compute_index_history(edition_key, history_dir)
    out_path = os.path.join(out_dir, "index_history.json")
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(points, f, indent=2)
    return out_path


def load_index_history(edition_key: str, out_root: str | None = None) -> list[dict]:
    out_root = out_root or os.path.join(config.DOCS_DIR, "api", "v1")
    path = os.path.join(out_root, edition_key, "index_history.json")
    if not os.path.exists(path):
        return []
    with open(path, encoding="utf-8") as f:
        return json.load(f)


if __name__ == "__main__":
    for ed in ("en", "ko", "stocks", "macro"):
        p = write_index_history(ed)
        print(f"Wrote {p} ({len(load_index_history(ed))} points)")
