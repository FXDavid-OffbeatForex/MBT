#!/usr/bin/env python
"""Rolling walk-forward analysis for any MT5 EA (roadmap Phase 3, gate 4).

Per fold: optimize the --range inputs on an in-sample (IS) window with MT5's complete grid, pick one
parameter set, run it once on the following out-of-sample (OOS) window. The OOS segments are stitched
into one trade list. Output: per-fold table, walk-forward efficiency (WFE = annualized OOS return /
annualized IS return of the chosen set), stitched OOS stats, and oos_trades.csv for prop_mc.py.

Runs under the Wine Python like macd_tester.py. Quit the MT5 GUI first (MT5 is single-instance).

  mbt-wine-py scripts/walkforward.py --tag tpl_ema --ea StrategyTemplate --symbol XAUUSD \
      --from 2022-01-01 --to 2026-10-01 --is-months 24 --oos-months 6 \
      --range InpFastEMA=10:10:30 --range InpSlowEMA=40:20:80 --range InpSLATR=1.5:0.5:2.5
  python3 scripts/prop_mc.py reports/wf_tpl_ema/oos_trades.csv --bt-risk 0.5

Every EA input is written to [TesterInputs]: defaults are parsed from the EA source (--source,
default mql5/<ea>.mq5, plus #includes found in the same folder) and overridden by --set, because MT5
silently applies the last GUI .set for any input left out.

Selection (--select):
  plateau (default): best mean score over the pass and its grid neighbours (+-1 step in every
           optimized input; cells outside the grid count as 0), among passes that score themselves --
           favours broad regions over spikes and interior over edges (an edge pick: widen the range)
  best   : best single-pass score
Score = Profit Factor x Recovery Factor if Trades >= --min-trades, Equity DD % <= --max-dd and PF > 1,
else 0 (the macd_sweep.py rule).

Fold results are saved in reports/wf_<tag>/; re-running the same command resumes after the last
finished fold. A different config under the same tag is refused.
"""
import os, sys, re, csv, json, time, glob, shutil, argparse, itertools, subprocess
from datetime import date

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, "scripts"))
import numpy as np
from core.tester import _launch_cmd, _data_dir, _read_text_any, _safe_run_name, _PERIOD_MAP, _MODEL_MAP
from core.connection import reports_dir
from macd_tester import parse_htm, parse_opt_xml
from prop_mc import read_mt5_report, profit_factor, compounded_dd

BUILTIN_ENUMS = {
    **{"PERIOD_" + k: v for k, v in {
        "CURRENT": 0, "M1": 1, "M2": 2, "M3": 3, "M4": 4, "M5": 5, "M6": 6, "M10": 10, "M12": 12, "M15": 15,
        "M20": 20, "M30": 30, "H1": 16385, "H2": 16386, "H3": 16387, "H4": 16388, "H6": 16390, "H8": 16392,
        "H12": 16396, "D1": 16408, "W1": 32769, "MN1": 49153}.items()},
    "PRICE_CLOSE": 1, "PRICE_OPEN": 2, "PRICE_HIGH": 3, "PRICE_LOW": 4, "PRICE_MEDIAN": 5,
    "PRICE_TYPICAL": 6, "PRICE_WEIGHTED": 7,
    "MODE_SMA": 0, "MODE_EMA": 1, "MODE_SMMA": 2, "MODE_LWMA": 3,
}


# ------------------------------------------------------------------------------------ EA inputs

def _strip_comments(src):
    return re.sub(r'("(?:\\.|[^"\\])*")|/\*.*?\*/|//[^\n]*', lambda m: m.group(1) or "", src, flags=re.S)


def read_sources(path, seen=None):
    """EA source + #included files that sit in the same folder (std-lib includes are skipped)."""
    seen = seen if seen is not None else set()
    if path in seen or not os.path.isfile(path):
        return ""
    seen.add(path)
    src = _strip_comments(_read_text_any(path))
    for inc in re.findall(r'#include\s*[<"]([^>"]+)[>"]', src):
        src += "\n" + read_sources(os.path.join(os.path.dirname(path), os.path.basename(inc.replace("\\", "/"))), seen)
    return src


def _value(expr, enums):
    e = expr.strip()
    if e in enums:
        return enums[e]
    if e in ("true", "false"):
        return e
    if re.fullmatch(r'"(?:\\.|[^"\\])*"', e):
        return e[1:-1]
    if re.fullmatch(r"[\d\s.+\-*/()eE]+", e):          # literal arithmetic, e.g. 60*24
        return eval(e, {"__builtins__": {}})
    raise ValueError(e)


def parse_inputs(src):
    """{name: tester value} for every input, {name: type}, [unresolvable 'name = expr']."""
    enums = dict(BUILTIN_ENUMS)
    for body in re.findall(r"\benum\s+\w+\s*\{(.*?)\}", src, flags=re.S):
        nxt = 0
        for item in filter(None, (x.strip() for x in body.split(","))):
            m = re.match(r"(\w+)\s*(?:=\s*(.+))?$", item, flags=re.S)
            if m:
                nxt = int(_value(m.group(2), enums)) if m.group(2) else nxt
                enums[m.group(1)] = nxt
                nxt += 1
    inputs, types, bad = {}, {}, []
    for typ, name, expr in re.findall(r"^\s*s?input\s+(\w+)\s+(\w+)\s*=\s*([^;]+);", src, flags=re.M):
        try:
            v = _value(expr, enums)
        except (ValueError, SyntaxError):
            bad.append(f"{name} = {expr.strip()}")
            continue
        inputs[name] = v if isinstance(v, str) else (repr(float(v)) if typ in ("double", "float") else str(int(v)))
        types[name] = typ
    return inputs, types, bad


# ------------------------------------------------------------------------------------ tester

def write_ini(path, name, a, inputs, types, ranges, optimize, frm, to, model):
    lines = ["[Tester]", f"Expert={a.expert}", f"Symbol={a.symbol}",
             f"Period={_PERIOD_MAP.get(a.period.lower(), a.period.upper())}", f"Model={_MODEL_MAP[model]}",
             f"Optimization={1 if optimize else 0}", f"OptimizationCriterion={a.criterion}",
             f"Deposit={a.deposit}", f"Leverage={a.leverage}", "Currency=USD", "ExecutionMode=0", "Visual=0",
             "ShutdownTerminal=1", "ReplaceReport=1", f"FromDate={frm:%Y.%m.%d}", f"ToDate={to:%Y.%m.%d}",
             f"Report={name}", "ForwardMode=0", "[TesterInputs]"]
    for k, v in inputs.items():
        if optimize and k in ranges:
            s, st, b = ranges[k]
            lines.append(f"{k}={v}||{s}||{st}||{b}||Y")
        elif types.get(k) == "string":
            lines.append(f"{k}={v}")
        else:   # pinned: a bare k=v keeps any optimize flag MT5 cached from an earlier run (see macd_tester.py)
            lines.append(f"{k}={v}||{v}||1||{v}||N")
    with open(path, "w", encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n")


def run_tester(ini, name, ext, outdir, timeout):
    """Launch one tester job; return the copied report path ('' if none)."""
    for old in glob.glob(os.path.join(_data_dir(), name + ".*")):
        os.remove(old)
    t0 = time.time()
    try:
        subprocess.run(_launch_cmd(ini), timeout=timeout, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    except subprocess.TimeoutExpired:
        print(f"  [{name}] timed out after {timeout}s")
    src = os.path.join(_data_dir(), name + ext)
    if not os.path.isfile(src):
        if time.time() - t0 < 8:
            sys.exit(f"[{name}] terminal returned in {time.time() - t0:.1f}s with no report -- MT5 is already "
                     f"running (single instance). Quit it and re-run; finished folds are kept.")
        print(f"  [{name}] no report written -- check Tester/logs")
        return ""
    dst = os.path.join(outdir, name + ext)
    shutil.copyfile(src, dst)
    return dst


# ------------------------------------------------------------------------------------ selection

def _fmt(v):
    return str(int(v)) if isinstance(v, float) and v.is_integer() else str(v)


def select(passes, ranges, a):
    for p in passes:
        ok = ((p.get("Trades") or 0) >= a.min_trades and (p.get("Equity DD %") or 99) <= a.max_dd
              and (p.get("Profit Factor") or 0) > 1.0)
        p["_score"] = (p.get("Profit Factor") or 0) * (p.get("Recovery Factor") or 0) if ok else 0.0
    scored = [p for p in passes if p["_score"] > 0]
    if not scored:
        best = max(passes, key=lambda p: (p.get("Profit Factor") or 0) * (p.get("Recovery Factor") or 0))
        return best, "no pass met the constraints (took best raw PF x RF)"
    if a.select == "best":
        return max(scored, key=lambda p: p["_score"]), ""
    names = list(ranges)

    def idx(p):
        return tuple(round((float(p[q]) - float(ranges[q][0])) / float(ranges[q][1])) for q in names)

    grid = {idx(p): p["_score"] for p in passes}
    offsets = list(itertools.product((-1, 0, 1), repeat=len(names)))
    for p in scored:
        i = idx(p)
        # cells outside the grid count as 0: an edge pick must beat interior ones despite unknown ground
        p["_plateau"] = sum(grid.get(tuple(x + o for x, o in zip(i, off)), 0.0) for off in offsets) / len(offsets)
    return max(scored, key=lambda p: (p["_plateau"], p["_score"])), ""


# ------------------------------------------------------------------------------------ folds

def add_months(d, n):
    y, m = divmod(d.month - 1 + n, 12)
    return date(d.year + y, m + 1, min(d.day, 28))


def make_folds(frm, to, is_m, oos_m, anchored):
    folds, k = [], 0
    while True:
        oos0 = add_months(frm, is_m + k * oos_m)
        oos1 = min(add_months(oos0, oos_m), to)
        if (oos1 - oos0).days < 28:
            return folds
        folds.append((frm if anchored else add_months(frm, k * oos_m), oos0, oos1))
        k += 1


def run_fold(k, is0, is1, oos1, a, inputs, types, ranges, outdir):
    tag = _safe_run_name(a.tag)
    name = f"wf_{tag}_f{k:02d}_is"
    ini = os.path.join(outdir, name + ".ini")
    write_ini(ini, name, a, inputs, types, ranges, True, is0, is1, a.model)
    xml = run_tester(ini, name, ".xml", outdir, a.timeout)
    passes = parse_opt_xml(xml) if xml else []
    r = {"fold": k, "is_from": str(is0), "is_to": str(is1), "oos_to": str(oos1), "passes": len(passes)}
    if not passes:
        return dict(r, error="no optimization passes")
    chosen, note = select(passes, ranges, a)
    is_years = (is1 - is0).days / 365.25
    r["params"] = {q: _fmt(chosen[q]) for q in ranges}
    r["note"] = note
    r["is"] = {"profit": chosen.get("Profit"), "pf": chosen.get("Profit Factor"), "rf": chosen.get("Recovery Factor"),
               "dd_pct": chosen.get("Equity DD %"), "trades": chosen.get("Trades"),
               "ann_pct": (chosen.get("Profit") or 0) / a.deposit * 100 / is_years,
               "plateau": chosen.get("_plateau"), "score": chosen.get("_score")}

    name = f"wf_{tag}_f{k:02d}_oos"
    ini = os.path.join(outdir, name + ".ini")
    write_ini(ini, name, a, dict(inputs, **r["params"]), types, {}, False, is1, oos1, a.oos_model)
    htm = run_tester(ini, name, ".htm", outdir, a.timeout)
    if not htm:
        return dict(r, error="no OOS report")
    m = parse_htm(htm)
    oos_years = (oos1 - is1).days / 365.25
    r["oos"] = {"profit": m.get("net_profit") or 0.0, "pf": m.get("profit_factor"), "trades": m.get("trades") or 0,
                "dd_pct": m.get("equity_dd_max_pct"), "ann_pct": (m.get("net_profit") or 0.0) / a.deposit * 100 / oos_years}
    r["wfe"] = r["oos"]["ann_pct"] / r["is"]["ann_pct"] if r["is"]["ann_pct"] > 0 else None
    r["oos_report"] = htm
    return r


def _n(v, f="{:.2f}"):
    return f.format(v) if isinstance(v, (int, float)) else "-"


def print_fold(r):
    if r.get("error"):
        print(f"{r['fold']:>4}  {r['is_from']}..{r['is_to']} -> {r['oos_to']}  ERROR: {r['error']}")
        return
    i, o = r["is"], r["oos"]
    wfe = r["wfe"] if i["ann_pct"] >= 1.0 else None     # ratio over a near-zero IS return is noise
    print(f"{r['fold']:>4}  {r['is_from']}..{r['is_to']} -> {r['oos_to']}  "
          f"IS {_n(i['trades'], '{:.0f}'):>4}tr PF {_n(i['pf'])} {_n(i['ann_pct'], '{:+.1f}'):>6}%/y | "
          f"OOS {_n(o['trades'], '{:.0f}'):>4}tr PF {_n(o['pf'])} {_n(o['ann_pct'], '{:+.1f}'):>6}%/y | "
          f"WFE {_n(wfe, '{:+.0%}') if wfe is not None else 'n/a':>6} | " + " ".join(f"{k}={v}" for k, v in r["params"].items())
          + (f"  ({r['note']})" if r.get("note") else ""))


# ------------------------------------------------------------------------------------ main

def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--tag", required=True, help="run name; results go to reports/wf_<tag>/")
    ap.add_argument("--ea", required=True, help="EA name, e.g. StrategyTemplate")
    ap.add_argument("--expert", help=r"tester Expert= path (default Advisors\<ea>.ex5)")
    ap.add_argument("--source", help="EA source for input defaults (default mql5/<ea>.mq5)")
    ap.add_argument("--symbol", default="XAUUSD")
    ap.add_argument("--period", default="H1")
    ap.add_argument("--from", dest="date_from", required=True, help="YYYY-MM-DD, start of the first IS window")
    ap.add_argument("--to", dest="date_to", required=True, help="YYYY-MM-DD, end of the last OOS window")
    ap.add_argument("--is-months", type=int, default=24)
    ap.add_argument("--oos-months", type=int, default=6)
    ap.add_argument("--anchored", action="store_true", help="IS always starts at --from (growing window)")
    ap.add_argument("--model", default="1min_ohlc", choices=list(_MODEL_MAP), help="IS optimization model")
    ap.add_argument("--oos-model", default="1min_ohlc", choices=list(_MODEL_MAP), help="OOS run model")
    ap.add_argument("--deposit", type=float, default=100000)
    ap.add_argument("--leverage", default="20")
    ap.add_argument("--criterion", default="0", help="MT5 OptimizationCriterion (only steers genetic search)")
    ap.add_argument("--set", action="append", default=[], help="Name=value fixed input (overrides source default)")
    ap.add_argument("--range", action="append", default=[], help="Name=start:step:stop optimized input")
    ap.add_argument("--select", choices=["plateau", "best"], default="plateau")
    ap.add_argument("--min-trades", type=int, default=30)
    ap.add_argument("--max-dd", type=float, default=20.0)
    ap.add_argument("--timeout", type=int, default=3600, help="seconds per tester job")
    a = ap.parse_args()
    a.expert = a.expert or "Advisors\\" + a.ea + ".ex5"
    a.source = a.source or os.path.join(ROOT, "mql5", a.ea + ".mq5")

    inputs, types, bad = parse_inputs(read_sources(a.source))
    if not inputs:
        sys.exit(f"no inputs found in {a.source}")
    sets = dict(s.split("=", 1) for s in a.set)
    unknown = [k for k in sets if k not in inputs and not any(b.startswith(k + " ") for b in bad)]
    if unknown:
        sys.exit(f"--set names not inputs of {a.ea}: {unknown}")
    inputs.update(sets)
    missing = [b for b in bad if b.split(" =")[0] not in sets]
    if missing:
        sys.exit(f"cannot evaluate these defaults from source, pass --set Name=value: {missing}")
    ranges = {}
    for s in a.range:
        k, v = s.split("=", 1)
        st, step, sp = v.split(":")
        if k not in inputs or float(step) <= 0 or float(sp) < float(st):
            sys.exit(f"bad --range {s}: needs an input name and numeric start:step:stop with step > 0")
        ranges[k] = (st, step, sp)
    if not ranges:
        sys.exit("give at least one --range")

    frm, to = date.fromisoformat(a.date_from), date.fromisoformat(a.date_to)
    folds = make_folds(frm, to, a.is_months, a.oos_months, a.anchored)
    if not folds:
        sys.exit("no complete fold fits between --from and --to")
    outdir = os.path.join(reports_dir(), "wf_" + _safe_run_name(a.tag))
    os.makedirs(outdir, exist_ok=True)
    config = {k: getattr(a, k) for k in ("ea", "expert", "symbol", "period", "is_months", "oos_months", "anchored",
                                         "model", "oos_model", "deposit", "leverage", "select", "min_trades", "max_dd")}
    config.update(date_from=a.date_from, inputs=inputs, ranges=ranges)
    config = json.loads(json.dumps(config))   # tuples -> lists, so it compares equal to a saved fold

    grid = 1
    for st, step, sp in ranges.values():
        grid *= int(round((float(sp) - float(st)) / float(step))) + 1
    print(f"Walk-forward {a.ea} {a.symbol} {a.period}: {len(folds)} folds, IS {a.is_months}m "
          f"{'anchored' if a.anchored else 'rolling'}, OOS {a.oos_months}m, grid {grid} passes/fold, "
          f"select={a.select} -> {os.path.relpath(outdir, ROOT)}")
    results = []
    for k, (is0, is1, oos1) in enumerate(folds, 1):
        fpath = os.path.join(outdir, f"fold{k:02d}.json")
        if os.path.isfile(fpath):
            saved = json.load(open(fpath))
            if saved.get("config") != config:
                sys.exit(f"{outdir} holds results from a different config -- use a new --tag")
            r = saved["result"]
        else:
            t0 = time.time()
            r = run_fold(k, is0, is1, oos1, a, inputs, types, ranges, outdir)
            r["seconds"] = round(time.time() - t0)
            if not r.get("error"):
                json.dump({"config": config, "result": r}, open(fpath, "w"), indent=1, default=str)
        results.append(r)
        print_fold(r)

    # ---- stitch OOS segments (each fold starts from a fresh deposit, so chain returns, not money)
    rows = []
    for r in results:
        for t, net, bal in (read_mt5_report(r["oos_report"]) if r.get("oos_report") else []):
            rows.append((t, net / bal * 100, r["fold"]))
    rows.sort()
    with open(os.path.join(outdir, "oos_trades.csv"), "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["close_time", "return_pct", "fold"])
        w.writerows([(t.strftime("%Y.%m.%d %H:%M:%S"), f"{p:.5f}", k) for t, p, k in rows])

    ok = [r for r in results if not r.get("error")]
    oos_years = sum((date.fromisoformat(r["oos_to"]) - date.fromisoformat(r["is_to"])).days for r in ok) / 365.25
    is_ann = [r["is"]["ann_pct"] for r in ok]
    oos_simple = sum(r["oos"]["profit"] for r in ok) / a.deposit * 100
    wfe = (oos_simple / oos_years) / (sum(is_ann) / len(is_ann)) if ok and oos_years and sum(is_ann) > 0 else None
    rets = np.array([p for _, p, _ in rows]) / 100
    summary = {"folds": len(results), "folds_ok": len(ok), "folds_profitable": sum(r["oos"]["profit"] > 0 for r in ok),
               "oos_trades": len(rows), "oos_years": oos_years, "oos_pf": profit_factor(rets) if len(rets) else None,
               "oos_compounded_pct": float((np.prod(1 + rets) - 1) * 100) if len(rets) else 0.0,
               "oos_max_dd_pct": float(compounded_dd(rets[None, :], 1.0)[0]) if len(rets) else 0.0,
               "oos_simple_ann_pct": oos_simple / oos_years if oos_years else None,
               "is_mean_ann_pct": sum(is_ann) / len(is_ann) if is_ann else None, "wfe": wfe}

    print(f"\nStitched OOS: {summary['oos_trades']} trades over {oos_years:.1f} years | "
          f"PF {_n(summary['oos_pf'])} | compounded {summary['oos_compounded_pct']:+.1f}% | "
          f"max DD {summary['oos_max_dd_pct']:.1f}% | profitable folds {summary['folds_profitable']}/{len(ok)}")
    print(f"Annualized: OOS {_n(summary['oos_simple_ann_pct'], '{:+.1f}')}%/y vs IS {_n(summary['is_mean_ann_pct'], '{:+.1f}')}%/y "
          f"-> WFE {_n(wfe, '{:.0%}')}")
    checks = [("stitched OOS profitable", summary["oos_compounded_pct"] > 0, f"{summary['oos_compounded_pct']:+.1f}%"),
              ("WFE >= 50%", wfe is not None and wfe >= 0.5, _n(wfe, "{:.0%}")),
              ("stitched OOS PF >= 1.2", (summary["oos_pf"] or 0) >= 1.2, _n(summary["oos_pf"])),
              ("OOS trades >= 100", summary["oos_trades"] >= 100, str(summary["oos_trades"]))]
    for name, passed, val in checks:
        print(f"  {'PASS' if passed else 'FAIL'}  {name}: {val}")
    summary["verdict"] = "PASS" if all(c[1] for c in checks) else "FAIL"
    print(f"Walk-forward verdict (roadmap gates 4-5): {summary['verdict']}")
    is_pf = [r["is"]["pf"] for r in ok if r["is"].get("pf")]
    if is_pf and sum(is_pf) / len(is_pf) < 1.2:
        print(f"NOTE: weak in-sample edge (mean PF of chosen sets {sum(is_pf) / len(is_pf):.2f}) -- WFE divides by a "
              f"near-zero IS return, so treat it as noise; the PF check is the meaningful one here.")
    json.dump({"config": config, "summary": summary, "folds": results}, open(os.path.join(outdir, "summary.json"), "w"),
              indent=1, default=str)
    rel = os.path.relpath(os.path.join(outdir, "oos_trades.csv"), ROOT).replace("\\", "/")
    print(f"Prop-firm view: python3 scripts/prop_mc.py {rel} --bt-risk <risk % per trade>")


if __name__ == "__main__":
    main()
