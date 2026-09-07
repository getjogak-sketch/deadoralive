"""
ledger.py — pre-registration ledger (task R1).

Every tradeable registry variant (registry.iter_variants() + registry.iter_popular_combo_variants()
— i.e. exactly the universe that ever receives a verdict badge; REFERENCE rows are excluded, same
as verdict.py already excludes them) gets exactly one immutable ledger entry recording WHEN its
exact rule text and fixed parameters were committed, and in which commit — the same idea as
pre-registering a clinical trial: the rule is nailed down before any result is computed, so it
cannot be quietly retuned after the fact. See REGISTRY.md for the human-readable rules this file
exists to enforce.

`registered_on` / `registered_commit` are derived from `git log --diff-filter=A -- registry.py`
(both entries below are read straight from that log, once, and then hard-coded — this file itself
must never be regenerated from git history again after first publication, otherwise a rebase/
squash of unrelated history could silently change a "registered_on" date):

    ccc7ff6bf9dfb86795ca5378a1ed28744f7fb8db  2026-09-07  Add "Popular combos" registry group
    c65f546c8f50da968e744821efe3ce1b99dace77  2026-09-06  Dead or Alive v0: weekly after-fees...

registry.py has exactly these two commits in its history, and each one added exactly one of the
two groups below in full (not incrementally) — so there is no ambiguity here about which entries
belong to which commit; every REGISTRY variant's `registered_on` is the first (2026-09-06, when
the whole file — including REGISTRY and REFERENCE — was created) and every POPULAR_COMBOS variant's
is the second (2026-09-07, when that group was added in one commit on top of the untouched first
group). Nothing here reruns `git log` at import time; this module is pure static data plus small
read helpers over `registry_ledger.json`.

Task B1 (2026-09-07) added a third group, BOT_TEMPLATES (grid_bot/dca_bot/dca_bot_sl) — same
one-time-hard-code convention: `_BOT_TEMPLATE_COMMIT` below is filled in with the actual commit
hash that added `BOT_TEMPLATES` to registry.py via a small follow-up commit (the hash cannot be
known before the commit that introduces it exists — see this task's own instruction), exactly the
same chicken-and-egg step the original two commits above were already through once.
"""
from __future__ import annotations
import json
import os

import config
import registry as reg

LEDGER_PATH = os.path.join(config.BASE_DIR, "registry_ledger.json")
SNAPSHOT_PATH = os.path.join(config.BASE_DIR, "registry_ledger.snapshot.json")

_TEXTBOOK_COMMIT = "c65f546c8f50da968e744821efe3ce1b99dace77"
_TEXTBOOK_DATE = "2026-09-06"
_POPULAR_COMBO_COMMIT = "ccc7ff6bf9dfb86795ca5378a1ed28744f7fb8db"
_POPULAR_COMBO_DATE = "2026-09-07"
# Filled in by a small follow-up commit right after the commit that adds BOT_TEMPLATES to
# registry.py (see this module's own docstring) — set to that commit's real hash, never rerun from
# git history automatically.
_BOT_TEMPLATE_COMMIT = "PENDING_COMMIT_HASH"
_BOT_TEMPLATE_DATE = "2026-09-07"


def _entry_id(strategy_id: str, params_str: str) -> str:
    return f"{strategy_id}:{params_str}"


def build_ledger_entries() -> list[dict]:
    """The full ledger, freshly assembled from registry.py's own current tables. Adding a brand
    new strategy/variant to registry.py and re-running the (one-time) generator that wrote
    registry_ledger.json would add a new entry here with today's date/commit — it can never alter
    an existing entry's date/commit/rule_text, which is exactly what
    tests.py's ledger-immutability check (against registry_ledger.snapshot.json) exists to catch."""
    entries = []
    for sid, sname, _stype, variant in reg.iter_variants():
        entry = next(e for e in reg.REGISTRY if e["id"] == sid)
        entries.append({
            "id": _entry_id(sid, variant["params_str"]),
            "strategy_id": sid,
            "strategy_name": sname,
            "params": variant["params"],
            "params_str": variant["params_str"],
            "source": "textbook",
            "registered_on": _TEXTBOOK_DATE,
            "registered_commit": _TEXTBOOK_COMMIT,
            "rule_text": entry["rule"],
        })
    for sid, sname, _stype, variant in reg.iter_popular_combo_variants():
        entry = next(e for e in reg.POPULAR_COMBOS if e["id"] == sid)
        entries.append({
            "id": _entry_id(sid, variant["params_str"]),
            "strategy_id": sid,
            "strategy_name": sname,
            "params": variant["params"],
            "params_str": variant["params_str"],
            "source": "popular_combo",
            "registered_on": _POPULAR_COMBO_DATE,
            "registered_commit": _POPULAR_COMBO_COMMIT,
            "rule_text": entry["rule"],
        })
    for sid, sname, _stype, variant in reg.iter_bot_template_variants():
        entry = next(e for e in reg.BOT_TEMPLATES if e["id"] == sid)
        entries.append({
            "id": _entry_id(sid, variant["params_str"]),
            "strategy_id": sid,
            "strategy_name": sname,
            "params": variant["params"],
            "params_str": variant["params_str"],
            "source": "bot_template",
            "registered_on": _BOT_TEMPLATE_DATE,
            "registered_commit": _BOT_TEMPLATE_COMMIT,
            "rule_text": entry["rule"],
        })
    entries.sort(key=lambda e: (e["registered_on"], e["strategy_id"], e["params_str"]))
    return entries


def write_ledger(path: str | None = None) -> str:
    path = path or LEDGER_PATH
    with open(path, "w", encoding="utf-8") as f:
        json.dump(build_ledger_entries(), f, indent=2, ensure_ascii=False)
        f.write("\n")
    return path


def load_ledger(path: str | None = None) -> list[dict]:
    path = path or LEDGER_PATH
    if not os.path.exists(path):
        return []
    with open(path, encoding="utf-8") as f:
        return json.load(f)


if __name__ == "__main__":
    # One-time (or "add a new strategy, then re-run once") generator — never wired into the
    # weekly pipeline, since a ledger entry must not shift just because run_weekly.py ran again.
    out = write_ledger()
    print(f"Wrote {out} ({len(load_ledger())} entries)")
