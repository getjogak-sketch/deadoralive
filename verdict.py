"""
verdict.py — spec_v2 §4 badge assignment. Thresholds live ONLY in config.VERDICT_THRESHOLDS;
this module only reads them, never redefines a number.

    ALIVE           OOS PF >= 1.2 AND OOS trades >= 30 AND OOS MDD < same-period B&H MDD
    FADING          (not ALIVE) AND OOS PF >= 1.0 AND OOS trades >= 10
    DEAD            OOS PF < 1.0 AND OOS trades >= 10
    TOO FEW TRADES  OOS trades < 10

Reference rows (dca_weekly, buy_and_hold) never get a badge (spec_v2 §4: "참고 행에는 배지 없음").
"""
from __future__ import annotations
import math
import config

ALIVE = "ALIVE"
FADING = "FADING"
DEAD = "DEAD"
TOO_FEW = "TOO FEW TRADES"


def assign_verdict(oos_pf: float, oos_trades: int, oos_mdd: float, oos_bh_mdd: float) -> str:
    th = config.VERDICT_THRESHOLDS

    if oos_trades < th["TOO_FEW_MIN_OOS_TRADES"]:
        return TOO_FEW

    pf_finite = oos_pf if (oos_pf is not None and not (isinstance(oos_pf, float) and math.isnan(oos_pf))) else 0.0

    if (pf_finite >= th["ALIVE_MIN_OOS_PF"]
            and oos_trades >= th["ALIVE_MIN_OOS_TRADES"]
            and oos_mdd < oos_bh_mdd):
        return ALIVE

    if pf_finite >= th["FADING_MIN_OOS_PF"] and oos_trades >= th["FADING_MIN_OOS_TRADES"]:
        return FADING

    if pf_finite < th["DEAD_MAX_OOS_PF"] and oos_trades >= th["FADING_MIN_OOS_TRADES"]:
        return DEAD

    # Falls through here only if trades >= 10 but pf sits in some undefined gap — cannot happen
    # given the thresholds above are DEAD_MAX==FADING_MIN==1.0, but return DEAD defensively
    # rather than silently omitting a badge.
    return DEAD


def tally(verdicts: list) -> dict:
    out = {ALIVE: 0, FADING: 0, DEAD: 0, TOO_FEW: 0}
    for v in verdicts:
        if v in out:
            out[v] += 1
    return out
