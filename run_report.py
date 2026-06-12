"""One-off runner: backtest the configured signals and render the HTML report."""
import os, sys, time
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from core.signals import load_signals
from core.backtest import run_backtest, report_to_dict
from core.report_html import render

t0 = time.time()
sigs = load_signals()
print(f"loaded {len(sigs)} signals", flush=True)

rep = run_backtest(signals=sigs)
d = report_to_dict(rep)
print("=== SUMMARY ===", flush=True)
for k in ("symbol","timeframe","trades_taken","signals_skipped","wins","losses",
          "open","win_rate_pct","profit_factor","expectancy_r","net_r",
          "max_drawdown_r","years","annual_return_pct","max_drawdown_pct"):
    print(f"  {k}: {d.get(k)}", flush=True)

path = render(rep, filename="eurusd_h1_backtest.html")
print(f"REPORT: {path}", flush=True)
print(f"elapsed: {time.time()-t0:.1f}s", flush=True)
