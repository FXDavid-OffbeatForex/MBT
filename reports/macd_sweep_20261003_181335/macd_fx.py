#!/usr/bin/env python3
"""MACD_Cross_EA v1.50 on forex: ATR exit-mode sweep per pair + out-of-sample check.

In-sample 2024-01-01..2026-10-01 (1-min OHLC), OOS 2022-01-01..2024-01-01. Signal logic = gold v1.40
(fast EMA 16, slow 26, signal 9, H4 EMA200 trend filter, no zero-line/gap/hours filter, 1% risk).
"""
import sys, json, time
sys.argv += []  # keep macd_sweep's argv checks happy
import macd_sweep as M

PAIRS = ["EURUSD", "GBPUSD", "USDJPY", "AUDUSD"]
BASE = {"InpFastEMA": "16", "InpSlowEMA": "26", "InpSignalSMA": "9", "InpUseTrendFilter": "true",
        "InpTrendTF": "16388", "InpTrendPeriod": "200", "InpZeroLineFilter": "false", "InpMinGapATR": "0.0",
        "InpUseTimeFilter": "false", "InpCloseOnOpposite": "false", "InpRiskMode": "1", "InpRiskValue": "1.0"}
GOLD_V140 = dict(BASE, InpStopLossPct="0.5", InpTakeProfitPct="3.75", InpBreakEvenPct="0.5",
                 InpBreakEvenLockPct="0.1", InpTrailStartPct="1.0", InpTrailATRMult="4.0", InpExitMode="0")
ATR = dict(BASE, InpExitMode="1", InpBELockATR="0.1")
GRID = {"InpSLATR": "1.0:0.5:3.0", "InpTPATR": "3:3:12", "InpBEATR": "0:1.5:1.5",
        "InpTrailStartATR": "0:2:4", "InpTrailATRMult": "2:1:4"}
PARAMS = list(GRID)
M.MIN_TRADES, M.MAX_DD = 100, 20.0



def main():
    out = {"outdir": M.OUTDIR, "pairs": {}}
    M.log("=== FX sweep", M.STAMP, "pairs", PAIRS)
    # regression: gold, percent mode, fast EMA 12 variant must reproduce the earlier +269,345.29
    d = M.run("single", "regress_gold_pct", sets=dict(GOLD_V140, InpFastEMA="12"), symbol="XAUUSD")
    out["regression_gold_pct_fast12"] = (d.get("metrics") or {}).get("net_profit")
    # gold in ATR mode with gold-equivalent multiples (0.5%~1.4xATR, 3.75%~10.7x, 1%~2.85x)
    g = dict(ATR, InpSLATR="1.5", InpTPATR="10.0", InpBEATR="1.5", InpTrailStartATR="3.0", InpTrailATRMult="4.0")
    out["gold_atr_is"] = M.run("single", "gold_atr_is", sets=g, symbol="XAUUSD").get("metrics")
    out["gold_atr_oos"] = M.run("single", "gold_atr_oos", sets=g, symbol="XAUUSD",
                                frm="2022-01-01", to="2024-01-01").get("metrics")
    for sym in PAIRS:
        d = M.run("opt", f"fx_{sym}_grid", sets=ATR, ranges=GRID, symbol=sym)
        r = M.rank(d.get("passes") or [])
        M.log(f"--- {sym}: {len(d.get('passes') or [])} passes, top by PF x RF:")
        M.show(r, PARAMS, n=6)
        res = {"top": r[:6], "oos": []}
        for k, p in enumerate(r[:3]):
            s = dict(ATR); s.update({q: M.fmt(p[q]) for q in PARAMS})
            o = M.run("single", f"fx_{sym}_oos{k+1}", sets=s, symbol=sym, frm="2022-01-01", to="2024-01-01")
            res["oos"].append({"inputs": {q: s[q] for q in PARAMS}, "is": {q: p.get(q) for q in M.KEYS},
                               "oos": o.get("metrics")})
        out["pairs"][sym] = res
    json.dump(out, open(M.os.path.join(M.OUTDIR, "fx_summary.json"), "w"), indent=1, default=str)
    M.log("=== DONE", time.strftime("%H:%M:%S"))


if __name__ == "__main__":
    main()
