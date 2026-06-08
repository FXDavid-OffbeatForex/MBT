"""
Render a BacktestReport to a standalone HTML file with an equity curve.

Uses Chart.js from a CDN so the output is a single self-contained .html the
user can open in any browser or screenshot for content.
"""

import html as _html
import os
from datetime import datetime

from .backtest import BacktestReport
from .connection import reports_dir


def _metric_card(label: str, value: str, good: bool = None) -> str:
    color = "#9aa0a6"
    if good is True:
        color = "#34a853"
    elif good is False:
        color = "#ea4335"
    return f"""
      <div class="card">
        <div class="card-value" style="color:{color}">{value}</div>
        <div class="card-label">{label}</div>
      </div>"""


def render(rep: BacktestReport, filename: str = None) -> str:
    """Write the HTML report and return its path."""
    if filename is None:
        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        filename = f"backtest_{rep.symbol}_{rep.timeframe}_{ts}.html"

    path = os.path.join(reports_dir(), filename)

    sym = _html.escape(rep.symbol)
    tf  = _html.escape(rep.timeframe)
    pf_str = "∞" if rep.profit_factor == float("inf") else f"{rep.profit_factor:.2f}"

    cards = "".join([
        _metric_card("Trades Taken", str(rep.total)),
        _metric_card("Signals Skipped", str(rep.signals_skipped)),
        _metric_card("Win Rate", f"{rep.win_rate:.1f}%", rep.win_rate >= 50),
        _metric_card("Profit Factor", pf_str, rep.profit_factor >= 1.5),
        _metric_card("Expectancy", f"{rep.expectancy:+.2f}R", rep.expectancy > 0),
        _metric_card("Net Result", f"{rep.net_r:+.1f}R", rep.net_r > 0),
        _metric_card(f"Return / Year @ {rep.risk_per_trade*100:.0f}% risk",
                     f"{rep.annual_return_pct:+.1f}%", rep.annual_return_pct > 0),
        _metric_card("Return / Month", f"{rep.monthly_return_pct:+.2f}%", rep.monthly_return_pct > 0),
        _metric_card("Max Drawdown", f"-{rep.max_drawdown_r:.1f}R", False),
        _metric_card("Max Drawdown %", f"-{rep.max_drawdown_pct:.1f}%", False),
        _metric_card("Avg Win", f"+{rep.avg_win:.2f}R", True),
        _metric_card("Avg Loss", f"-{rep.avg_loss:.2f}R", False),
        _metric_card("Max Win Streak", str(rep.max_win_streak)),
        _metric_card("Max Loss Streak", str(rep.max_loss_streak)),
        _metric_card("Wins / Losses", f"{rep.wins} / {rep.losses}"),
        _metric_card("Open", str(rep.open_trades)),
    ])

    # regime rows
    regime_rows = ""
    for rg, m in rep.by_regime.items():
        pf = "∞" if m["profit_factor"] == float("inf") else f"{m['profit_factor']:.2f}"
        regime_rows += f"""
          <tr><td>{rg}</td><td>{m['trades']}</td>
          <td>{m['win_rate']:.1f}%</td><td>{pf}</td>
          <td>{m['net_r']:+.1f}R</td></tr>"""
    regime_table = ""
    if regime_rows:
        regime_table = f"""
        <h2>By Regime</h2>
        <table class="regime">
          <tr><th>Regime</th><th>Trades</th><th>Win Rate</th><th>Profit Factor</th><th>Net R</th></tr>
          {regime_rows}
        </table>"""

    equity = rep.equity_curve
    labels = list(range(1, len(equity) + 1))

    html = f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<title>MBT Backtest — {sym} {tf}</title>
<script src="https://cdn.jsdelivr.net/npm/chart.js"></script>
<style>
  body {{ background:#0d1117; color:#e6edf3; font-family:-apple-system,Segoe UI,Roboto,sans-serif;
         margin:0; padding:32px; }}
  h1 {{ font-size:22px; margin:0 0 4px; }}
  .sub {{ color:#9aa0a6; margin-bottom:24px; font-size:14px; }}
  .grid {{ display:grid; grid-template-columns:repeat(auto-fit,minmax(150px,1fr));
           gap:12px; margin-bottom:32px; }}
  .card {{ background:#161b22; border:1px solid #30363d; border-radius:10px;
           padding:16px; text-align:center; }}
  .card-value {{ font-size:24px; font-weight:700; }}
  .card-label {{ font-size:12px; color:#9aa0a6; margin-top:4px; }}
  h2 {{ font-size:16px; border-bottom:1px solid #30363d; padding-bottom:8px; }}
  table {{ width:100%; border-collapse:collapse; margin-bottom:32px; font-size:14px; }}
  th,td {{ text-align:left; padding:8px 12px; border-bottom:1px solid #21262d; }}
  th {{ color:#9aa0a6; font-weight:600; }}
  .chart-wrap {{ background:#161b22; border:1px solid #30363d; border-radius:10px;
                 padding:20px; margin-bottom:32px; }}
  .foot {{ color:#6e7681; font-size:12px; }}
</style>
</head>
<body>
  <h1>Backtest Report — {sym} {tf}</h1>
  <div class="sub">{rep.total} trades taken · {rep.signals_skipped} signals skipped (trade already open)
      · generated {datetime.now().strftime('%Y-%m-%d %H:%M')}
      · ambiguous bars counted as losses (conservative)</div>

  <div class="grid">{cards}</div>

  <div class="chart-wrap">
    <h2 style="border:none;margin-top:0">Equity Curve (cumulative R)</h2>
    <canvas id="equity" height="100"></canvas>
  </div>

  {regime_table}

  <div class="foot">MBT — MT5 Backtest Toolkit · results in R units (1R = initial risk per trade)·
      P&amp;L replayed on real broker bars; indicator signals are the indicator's own logged output.</div>

<script>
const ctx = document.getElementById('equity');
new Chart(ctx, {{
  type: 'line',
  data: {{
    labels: {labels},
    datasets: [{{
      label: 'Cumulative R',
      data: {equity},
      borderColor: '#58a6ff',
      backgroundColor: 'rgba(88,166,255,0.1)',
      fill: true, tension: 0.1, pointRadius: 0, borderWidth: 2
    }}]
  }},
  options: {{
    plugins: {{ legend: {{ display:false }} }},
    scales: {{
      x: {{ grid:{{color:'#21262d'}}, ticks:{{color:'#6e7681'}} }},
      y: {{ grid:{{color:'#21262d'}}, ticks:{{color:'#9aa0a6'}} }}
    }}
  }}
}});
</script>
</body>
</html>"""

    with open(path, "w", encoding="utf-8") as f:
        f.write(html)

    return path
