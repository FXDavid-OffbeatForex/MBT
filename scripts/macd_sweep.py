#!/usr/bin/env python3
"""Staged parameter sweep for MACD_Cross_EA on XAUUSD H1 (system python; shells out to macd_tester.py under Wine).

Stages (in-sample window 2024-01-01..2026-10-01, 1-min OHLC):
  S2 exits   : SL x TP x trail-start x trail-ATR grid
  S3 filters : zero-line, MACD gap, hours, close-on-opposite, break-even
  S4 signal  : MACD periods; trend-filter timeframe/period
Validation (real ticks): finalist and v1.30 defaults by year 2022..2026 (2022-2023 never used for selection).
Selection: Trades >= MIN_TRADES, Equity DD <= MAX_DD, PF > 1; score = Profit Factor x Recovery Factor.
"""
import os, sys, json, time, subprocess
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
RUNNER = os.path.expanduser("~/.local/bin/mbt-wine-py")
STAMP = time.strftime("%Y%m%d_%H%M%S")
OUTDIR = os.path.join(ROOT, "reports", "macd_sweep_" + STAMP)
os.makedirs(OUTDIR, exist_ok=True)
LOGF = open(os.path.join(OUTDIR, "sweep.log"), "a")
IS_FROM, IS_TO = "2024-01-01", "2026-10-01"
MIN_TRADES, MAX_DD = 150, 20.0
DRY = "--dry" in sys.argv

V130 = {"InpStopLossPct": "0.6", "InpTakeProfitPct": "3.0", "InpTrailStartPct": "2.0", "InpTrailATRMult": "3.0",
        "InpZeroLineFilter": "false", "InpMinGapATR": "0.0", "InpUseTimeFilter": "false",
        "InpCloseOnOpposite": "false", "InpBreakEvenPct": "0.0", "InpFastEMA": "12", "InpSlowEMA": "26",
        "InpSignalSMA": "9", "InpUseTrendFilter": "true", "InpTrendTF": "16388", "InpTrendPeriod": "200"}
KEYS = ("Profit", "Profit Factor", "Recovery Factor", "Sharpe Ratio", "Equity DD %", "Trades", "_score")


def log(*a):
    s = " ".join(str(x) for x in a)
    print(s, flush=True); LOGF.write(s + "\n"); LOGF.flush()


def winpath(p):
    return "Z:" + p.replace("/", "\\")


def run(mode, name, sets=None, ranges=None, model="1min_ohlc", frm=IS_FROM, to=IS_TO, timeout=3000, symbol="XAUUSD"):
    out = os.path.join(OUTDIR, name + ".json")
    cmd = [RUNNER, "scripts/macd_tester.py", mode, "--name", name, "--out", winpath(out), "--model", model,
           "--from", frm, "--to", to, "--timeout", str(timeout), "--symbol", symbol]
    if mode == "opt":
        cmd += ["--criterion", "6"]
    for k, v in (sets or {}).items():
        cmd += ["--set", f"{k}={v}"]
    for k, v in (ranges or {}).items():
        cmd += ["--range", f"{k}={v}"]
    if DRY:
        log("DRY:", " ".join(cmd[1:])); return {"passes": [], "metrics": {}}
    t0 = time.time()
    try:
        subprocess.run(cmd, cwd=ROOT, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=timeout + 120)
    except subprocess.TimeoutExpired:
        log(f"[{name}] TIMEOUT")
    if not os.path.isfile(out):
        log(f"[{name}] no output file"); return {"passes": [], "metrics": {}, "error": "no output"}
    d = json.load(open(out))
    log(f"[{name}] {time.time()-t0:.0f}s error={d.get('error')} passes={len(d.get('passes') or [])} "
        f"metrics={ {k: d.get('metrics', {}).get(k) for k in ('net_profit','profit_factor','trades','win_rate_pct','equity_dd_rel_pct','sharpe','recovery_factor')} if d.get('metrics') else None}")
    return d


def rank(passes):
    ok = [p for p in passes if (p.get("Trades") or 0) >= MIN_TRADES and (p.get("Equity DD %") or 99) <= MAX_DD
          and (p.get("Profit Factor") or 0) > 1.0]
    if not ok:
        log("   (no pass met constraints; ranking all)"); ok = list(passes)
    for p in ok:
        p["_score"] = round((p.get("Profit Factor") or 0) * (p.get("Recovery Factor") or 0), 3)
    ok.sort(key=lambda p: -p["_score"])
    return ok


def show(passes, params, n=8):
    for p in passes[:n]:
        row = {k: (round(p[k], 3) if isinstance(p.get(k), float) else p.get(k)) for k in KEYS}
        row.update({k: p.get(k) for k in params})
        log("   ", row)


def fmt(v):
    if isinstance(v, float):
        return str(int(v)) if v.is_integer() else str(v)
    return str(v)


def main():
    best = dict(V130)
    summary = {"outdir": OUTDIR, "in_sample": [IS_FROM, IS_TO], "stages": {}}
    log(f"=== MACD_Cross_EA staged sweep {STAMP} | in-sample {IS_FROM}..{IS_TO} | out {OUTDIR}")

    # ---- S2: exits  (--resume-s2 <s2_exits.json> reuses a finished stage)
    params = ["InpStopLossPct", "InpTakeProfitPct", "InpTrailStartPct", "InpTrailATRMult"]
    if "--resume-s2" in sys.argv:
        d = json.load(open(sys.argv[sys.argv.index("--resume-s2") + 1])); log("[s2_exits] resumed from file")
    else:
      d = run("opt", "s2_exits", sets=best, ranges={
        "InpStopLossPct": "0.3:0.1:0.6", "InpTakeProfitPct": "3.0:0.75:6.0",
          "InpTrailStartPct": "0:1:3", "InpTrailATRMult": "2:1:5"})
    r = rank(d.get("passes") or [])
    if r:
        show(r, params)
        for k in params:
            best[k] = fmt(r[0][k])
        summary["stages"]["s2_exits"] = {"best": {k: best[k] for k in params}, "top": r[:8]}
    log("best after S2:", {k: best[k] for k in params})

    # ---- S3: filters. Bools are fixed per launch (8 combos); only numeric inputs are ranged.
    params = ["InpZeroLineFilter", "InpMinGapATR", "InpUseTimeFilter", "InpCloseOnOpposite", "InpBreakEvenPct"]
    cands = []
    for zl in ("false", "true"):
        for tf in ("false", "true"):
            for co in ("false", "true"):
                s3 = dict(best); s3.update({"InpZeroLineFilter": zl, "InpUseTimeFilter": tf, "InpCloseOnOpposite": co})
                d = run("opt", f"s3_zl{zl[0]}_tf{tf[0]}_co{co[0]}", sets=s3,
                        ranges={"InpMinGapATR": "0:0.05:0.2", "InpBreakEvenPct": "0:0.5:1.0"}, timeout=900)
                for p in d.get("passes") or []:
                    p.update({"InpZeroLineFilter": zl, "InpUseTimeFilter": tf, "InpCloseOnOpposite": co})
                    cands.append(p)
    r = rank(cands)
    if r:
        show(r, params)
        for k in params:
            best[k] = fmt(r[0][k])
        summary["stages"]["s3_filters"] = {"best": {k: best[k] for k in params}, "top": r[:8]}
    log("best after S3:", {k: best[k] for k in params})

    # ---- S4a: MACD periods
    params = ["InpFastEMA", "InpSlowEMA", "InpSignalSMA"]
    d = run("opt", "s4a_macd", sets=best, ranges={"InpFastEMA": "8:4:16", "InpSlowEMA": "20:6:32", "InpSignalSMA": "5:4:13"})
    r = rank(d.get("passes") or [])
    if r:
        show(r, params)
        for k in params:
            best[k] = fmt(r[0][k])
        summary["stages"]["s4a_macd"] = {"best": {k: best[k] for k in params}, "top": r[:8]}
    log("best after S4a:", {k: best[k] for k in params})

    # ---- S4b: trend filter (fixed TF per launch; enum ranges are not safe in MT5 grids)
    cands = []
    for tf, label in (("16385", "H1"), ("16388", "H4"), ("16408", "D1")):
        s = dict(best); s.update({"InpUseTrendFilter": "true", "InpTrendTF": tf})
        d = run("opt", f"s4b_trend_{label}", sets=s, ranges={"InpTrendPeriod": "50:50:200"})
        for p in d.get("passes") or []:
            p["InpTrendTF"] = tf; p["InpUseTrendFilter"] = "true"; cands.append(p)
    s = dict(best); s["InpUseTrendFilter"] = "false"
    d = run("single", "s4b_trend_off", sets=s)
    m = d.get("metrics") or {}
    if m:
        cands.append({"Profit": m.get("net_profit"), "Profit Factor": m.get("profit_factor"),
                      "Recovery Factor": m.get("recovery_factor"), "Sharpe Ratio": m.get("sharpe"),
                      "Equity DD %": m.get("equity_dd_rel_pct"), "Trades": m.get("trades"),
                      "InpUseTrendFilter": "false", "InpTrendTF": best["InpTrendTF"], "InpTrendPeriod": best["InpTrendPeriod"]})
    params = ["InpUseTrendFilter", "InpTrendTF", "InpTrendPeriod"]
    r = rank(cands)
    if r:
        show(r, params)
        for k in params:
            best[k] = fmt(r[0][k])
        summary["stages"]["s4b_trend"] = {"best": {k: best[k] for k in params}, "top": r[:8]}
    log("best after S4b:", {k: best[k] for k in params})
    summary["final_inputs"] = dict(best)
    log("=== FINAL candidate:", json.dumps(best))

    # ---- Validation with real ticks: finalist vs v1.30 by year (2022-2023 are untouched out-of-sample)
    periods = [("2022", "2022-01-01", "2023-01-01"), ("2023", "2023-01-01", "2024-01-01"),
               ("2024", "2024-01-01", "2025-01-01"), ("2025", "2025-01-01", "2026-01-01"),
               ("2026ytd", "2026-01-01", "2026-10-01"), ("2024-2026", "2024-01-01", "2026-10-01")]
    summary["validation"] = {"final": {}, "v130": {}}
    for label, a, b in periods:
        d = run("single", f"val_final_{label}", sets=best, model="real_ticks", frm=a, to=b, timeout=900)
        summary["validation"]["final"][label] = d.get("metrics")
    for label, a, b in periods:
        if label == "2026ytd":
            continue  # v1.30 real ticks 2026 YTD already measured: +47,841 / PF 1.42 / 128 trades
        d = run("single", f"val_v130_{label}", sets=V130, model="real_ticks", frm=a, to=b, timeout=900)
        summary["validation"]["v130"][label] = d.get("metrics")

    with open(os.path.join(OUTDIR, "summary.json"), "w") as f:
        json.dump(summary, f, indent=1, default=str)
    log("=== DONE", time.strftime("%H:%M:%S"), "summary:", os.path.join(OUTDIR, "summary.json"))


if __name__ == "__main__":
    main()
