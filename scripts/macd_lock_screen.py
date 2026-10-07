#!/usr/bin/env python3
"""MACD_Cross_EA profit-lock ladder screen (docs/superpowers/specs/2026-10-07-macd-lock-ladder-prereg.md).
System python; optimizer runs on 1-minute OHLC through vwap_rsi_eval.run. Quit the MT5 GUI first.

  python3 scripts/macd_lock_screen.py run 2019-21     # then: run 2022-24
  python3 scripts/macd_lock_screen.py score
"""
import json
import os
import sys

import macd_sweep as M
import selection as S
import vwap_rsi_eval as E

GRID = {"InpBreakEvenPct": ["0.5", "0.75", "1.0", "1.5"], "InpBreakEvenLockPct": ["0.1", "0.25"],
        "InpBreakEven2Pct": ["0.0", "2.0", "2.5", "3.0"], "InpBreakEven2LockPct": ["1.0", "1.5"]}
PERIODS = {"2019-21": ("2019-01-01", "2022-01-01"), "2022-24": ("2022-01-01", "2025-01-01")}
OUT = os.path.join(E.REPORTS, "macd_lock")


def run(period):
    frm, to = PERIODS[period]
    passes = []
    for i, (fixed, ranges) in enumerate(S.plan_launches(GRID)):
        d, _ = E.run(f"ml_{period}_{i}", fixed, mode="opt", ranges=ranges, frm=frm, to=to, timeout=3600,
                     expert="MACD_Cross_EA", model="1min_ohlc")
        for p in d.get("passes") or []:
            p.update({k: float(v) for k, v in fixed.items()})
            passes.append(p)
    passes, failed = S.drop_failed_passes(passes)
    json.dump({"passes": passes, "failed": failed}, open(os.path.join(OUT, f"passes_{period}.json"), "w"), indent=1)
    print(f"{period}: {len(passes)} passes, {failed} failed OnInit")


def key(p):
    return tuple(float(p[k]) for k in GRID)


def score():
    per = {k: {key(p): p for p in json.load(open(os.path.join(OUT, f"passes_{k}.json")))["passes"]} for k in PERIODS}
    base = {k: per[k][(0.5, 0.1, 0.0, 1.0)] for k in PERIODS}
    pf = lambda p: float(p["Profit Factor"] or 0.0)
    rows = []
    for kk in per["2019-21"]:
        if kk not in per["2022-24"] or (kk[2] == 0.0 and kk[3] != 1.0):     # step 2 off: one copy only
            continue
        cells = {k: per[k][kk] for k in PERIODS}
        qualifies = all(pf(cells[k]) >= pf(base[k]) and float(cells[k]["Profit"]) >= float(base[k]["Profit"]) for k in PERIODS)
        plateau = True
        for k in PERIODS:
            nb = S.neighbours(cells[k], list(per[k].values()), GRID)
            plateau &= bool(nb) and sum(pf(n) for n in nb) / len(nb) >= pf(base[k]) - 0.03
        ratio = min(pf(cells[k]) / pf(base[k]) for k in PERIODS)
        rows.append((ratio, kk, cells, qualifies, plateau))
    rows.sort(key=lambda r: -r[0])
    print(f"baseline (0.5/0.1, step 2 off): PF {pf(base['2019-21']):.3f} / {pf(base['2022-24']):.3f}, "
          f"profit {float(base['2019-21']['Profit']):,.0f} / {float(base['2022-24']['Profit']):,.0f}")
    print(f"{'step1':>10} {'step2':>10} | {'PF 2019-21':>10} {'PF 2022-24':>10} | {'profit 2019-21':>14} {'profit 2022-24':>14} | ratio  q  plateau")
    for ratio, kk, c, q, pl in rows:
        s2 = "off" if kk[2] == 0.0 else f"{kk[2]}/{kk[3]}"
        print(f"{kk[0]:>5}/{kk[1]:<4} {s2:>10} | {pf(c['2019-21']):10.3f} {pf(c['2022-24']):10.3f} | "
              f"{float(c['2019-21']['Profit']):14,.0f} {float(c['2022-24']['Profit']):14,.0f} | {ratio:.3f}  {'Y' if q else '-'}  {'Y' if pl else '-'}")
    fin = [r for r in rows if r[3] and r[4]]
    print("\nfinalist:", None if not fin else {k: v for k, v in zip(GRID, fin[0][1])})
    json.dump({"finalist": None if not fin else dict(zip(GRID, fin[0][1]))}, open(os.path.join(OUT, "finalist.json"), "w"))


if __name__ == "__main__":
    M.OUTDIR = OUT
    os.makedirs(OUT, exist_ok=True)
    if sys.argv[1] == "run":
        if E.mt5_running():
            sys.exit("Quit the MetaTrader 5 GUI first (MT5 is single-instance).")
        run(sys.argv[2])
    else:
        score()
