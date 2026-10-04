#!/usr/bin/env python
"""Headless MT5 Strategy Tester driver for MACD_Cross_EA (single runs + grid optimizations).

Runs under the Wine Python (mbt-wine-py) like the MBT MCP server. Writes a [Tester]+[TesterInputs]
ini into reports/, launches terminal64.exe /portable /config:<ini>, waits for ShutdownTerminal=1,
then parses the .htm (single) or .xml (optimization) report MT5 wrote into the data dir.

  single: mbt-wine-py scripts/macd_tester.py single --name base --from 2026-01-01 --to 2026-10-01 --model real_ticks
  opt   : mbt-wine-py scripts/macd_tester.py opt --name sweep1 --range InpStopLossPct=0.4:0.2:1.0 --range InpTakeProfitPct=1.5:0.5:4.0
"""
import os, sys, re, json, glob, time, shutil, argparse, subprocess, html
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from core.tester import _launch_cmd, _data_dir, _read_text_any, _safe_run_name, _PERIOD_MAP, _MODEL_MAP
from core.connection import reports_dir
from core.connection import load_config
import ea_profiles



def _num(s):
    s = s.replace(" ", "").replace(" ", "")
    try:
        return float(s)
    except ValueError:
        return None


def parse_htm(path):
    txt = _read_text_any(path)
    txt = html.unescape(re.sub(r"<[^>]+>", " ", txt))
    txt = re.sub(r"\s+", " ", txt)
    out = {}
    def grab(key, pat, conv=_num, idx=1):
        m = re.search(pat, txt)
        if m:
            out[key] = conv(m.group(idx)) if conv else m.group(idx)
    grab("net_profit", r"Total Net Profit: (-?[\d\s ]+\.\d+)")
    grab("gross_profit", r"Gross Profit: (-?[\d\s ]+\.\d+)")
    grab("gross_loss", r"Gross Loss: (-?[\d\s ]+\.\d+)")
    grab("profit_factor", r"Profit Factor: (-?[\d.]+)")
    grab("expected_payoff", r"Expected Payoff: (-?[\d\s ]+\.\d+)")
    grab("recovery_factor", r"Recovery Factor: (-?[\d.]+)")
    grab("sharpe", r"Sharpe Ratio: (-?[\d.]+)")
    grab("equity_dd_max_pct", r"Equity Drawdown Maximal: [\d\s ]+\.\d+ \(([\d.]+)%\)")
    grab("equity_dd_rel_pct", r"Equity Drawdown Relative: ([\d.]+)%")
    grab("balance_dd_max_pct", r"Balance Drawdown Maximal: [\d\s ]+\.\d+ \(([\d.]+)%\)")
    grab("trades", r"Total Trades: (\d+)")
    grab("win_trades", r"Profit Trades \(% of total\): (\d+) \(([\d.]+)%\)")
    grab("win_rate_pct", r"Profit Trades \(% of total\): (\d+) \(([\d.]+)%\)", idx=2)
    grab("short_trades", r"Short Trades \(won %\): (\d+) \(([\d.]+)%\)")
    grab("short_win_pct", r"Short Trades \(won %\): (\d+) \(([\d.]+)%\)", idx=2)
    grab("long_trades", r"Long Trades \(won %\): (\d+) \(([\d.]+)%\)")
    grab("long_win_pct", r"Long Trades \(won %\): (\d+) \(([\d.]+)%\)", idx=2)
    grab("avg_win", r"Average profit trade: (-?[\d\s ]+\.\d+)")
    grab("avg_loss", r"Average loss trade: (-?[\d\s ]+\.\d+)")
    grab("largest_win", r"Largest profit trade: (-?[\d\s ]+\.\d+)")
    grab("largest_loss", r"Largest loss trade: (-?[\d\s ]+\.\d+)")
    grab("max_consec_losses", r"Maximum consecutive losses \(\$\): (\d+)")
    grab("ontester", r"OnTester result: (-?[\d.eE+-]+)")
    grab("history_quality", r"History Quality: (\d+)%")
    grab("bars", r"Bars: (\d+)")
    grab("ticks", r"Ticks: (\d+)")
    if out.get("avg_win") is not None and out.get("avg_loss"):
        out["win_loss_ratio"] = round(out["avg_win"] / abs(out["avg_loss"]), 3)
    return out


def parse_opt_xml(path):
    txt = _read_text_any(path)
    rows = []
    for row in re.findall(r"<Row[^>]*>(.*?)</Row>", txt, flags=re.S):
        cells = re.findall(r"<Cell[^>]*>\s*<Data[^>]*>(.*?)</Data>\s*</Cell>|<Cell[^>]*/>", row, flags=re.S)
        rows.append([html.unescape(c) for c in cells])
    if not rows:
        return []
    header = rows[0]
    out = []
    for r in rows[1:]:
        d = {}
        for k, v in zip(header, r):
            n = _num(v)
            d[k] = n if n is not None else v
        out.append(d)
    return out


def write_ini(name, args, inputs, ranges):
    period = _PERIOD_MAP.get(args.period.lower(), args.period.upper())
    model = _MODEL_MAP.get(args.model.lower(), 1)
    opt = 0 if args.mode == "single" else args.algo
    lines = ["[Tester]", f"Expert={ea_profiles.expert_path(args.expert)}", f"Symbol={args.symbol}", f"Period={period}",
             f"Model={model}", f"Optimization={opt}", f"OptimizationCriterion={args.criterion}",
             f"Deposit={args.deposit}", f"Leverage={args.leverage}", "Currency=USD",
             "ExecutionMode=0", "Visual=0", "ShutdownTerminal=1", "ReplaceReport=1",
             f"FromDate={args.date_from.replace('-', '.')}", f"ToDate={args.date_to.replace('-', '.')}",
             f"Report={name}"]
    if args.forward:
        lines += ["ForwardMode=4", f"ForwardDate={args.forward.replace('-', '.')}"]
    else:
        lines.append("ForwardMode=0")
    lines.append("[TesterInputs]")
    lines += ea_profiles.tester_input_lines(inputs, ranges)
    ini_path = os.path.join(reports_dir(), name + ".ini")
    with open(ini_path, "w", encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n")
    return ini_path


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("mode", choices=["single", "opt"])
    ap.add_argument("--name", required=True)
    ap.add_argument("--symbol", default="XAUUSD")
    ap.add_argument("--period", default="H1")
    ap.add_argument("--model", default="1min_ohlc")
    ap.add_argument("--deposit", default="100000")
    ap.add_argument("--leverage", default="20")
    ap.add_argument("--from", dest="date_from", default="2026-01-01")
    ap.add_argument("--to", dest="date_to", default="2026-10-01")
    ap.add_argument("--forward", default="")
    ap.add_argument("--criterion", default="0")
    ap.add_argument("--algo", type=int, default=1, help="1=complete grid, 2=genetic")
    ap.add_argument("--set", action="append", default=[], help="Name=value (fixed input)")
    ap.add_argument("--range", action="append", default=[], help="Name=start:step:stop")
    ap.add_argument("--timeout", type=int, default=1800)
    ap.add_argument("--out", default="", help="also write the result JSON here (Windows path under Wine)")
    ap.add_argument("--expert", default="MACD_Cross_EA", help="EA name under MQL5\\Experts\\Advisors")
    args = ap.parse_args()

    sets = {}
    for s in args.set:
        k, v = s.split("=", 1)
        sets[k.strip()] = v.strip()
    ranges = {}
    try:
        inputs = ea_profiles.merge_inputs(args.expert, sets)
        for s in args.range:
            k, r = ea_profiles.parse_range(s)
            ranges[k] = r
    except ValueError as e:
        sys.exit(str(e))

    name = _safe_run_name(args.name) + "_" + time.strftime("%Y%m%d_%H%M%S")
    ini_path = write_ini(name, args, inputs, ranges)
    cmd = _launch_cmd(ini_path)
    t0 = time.time()
    timed_out = False
    try:
        subprocess.run(cmd, timeout=args.timeout, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    except subprocess.TimeoutExpired:
        timed_out = True
    elapsed = round(time.time() - t0, 1)

    data = _data_dir()
    result = {"name": name, "expert": args.expert, "ini": ini_path, "mode": args.mode, "model": args.model,
              "period": args.period, "from": args.date_from, "to": args.date_to,
              "inputs": inputs, "ranges": ranges, "ran_seconds": elapsed, "timed_out": timed_out}
    found = []
    for ext in ("htm", "html", "xml", "forward.xml"):
        found += glob.glob(os.path.join(data, name + "*." + ext))
    result["reports"] = []
    for src in sorted(set(found)):
        dst = os.path.join(reports_dir(), os.path.basename(src))
        try:
            shutil.copyfile(src, dst)
        except OSError:
            dst = src
        result["reports"].append(dst)
        if dst.lower().endswith((".htm", ".html")):
            result["metrics"] = parse_htm(dst)
        elif dst.lower().endswith(".forward.xml"):
            result["forward_passes"] = parse_opt_xml(dst)
        elif dst.lower().endswith(".xml"):
            result["passes"] = parse_opt_xml(dst)
    if not found:
        result["error"] = ("no report written (elapsed %.1fs): another terminal running? EA not compiled? "
                           "check Tester/logs" % elapsed)
    common = (load_config().get("tester") or {}).get("common_files", "")
    log_name = ea_profiles.trade_log_name_for(args.expert, args.symbol, inputs)
    src = os.path.join(common, log_name) if (common and log_name) else ""
    fresh = ea_profiles.pick_fresh_log(src, t0) if src else None
    result["trade_log"] = None
    if fresh:
        dst_name = name + "_trades.csv"
        shutil.copyfile(fresh, os.path.join(reports_dir(), dst_name))
        result["trade_log"] = dst_name
    with open(os.path.join(reports_dir(), name + ".json"), "w", encoding="utf-8") as f:
        json.dump(result, f, indent=1, default=str)
    if args.out:
        with open(args.out, "w", encoding="utf-8") as f:
            json.dump(result, f, indent=1, default=str)
    print(json.dumps(result, indent=1, default=str))


if __name__ == "__main__":
    main()
