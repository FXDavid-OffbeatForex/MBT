#!/usr/bin/env python3
"""Validate MACD_Cross_EA v1.42: guards off == v1.40 to the cent; guards on diffs explained; forced triggers fire."""
import sys, json, time
import macd_sweep as M
OFF = {"InpMaxSpreadPoints": "0", "InpDailyLossPct": "0", "InpMonthlyLossPct": "0"}
V140 = {"2022": 17113.59, "2023": 31165.91, "2024-2026": 201329.32}
P = {"2022": ("2022-01-01", "2023-01-01"), "2023": ("2023-01-01", "2024-01-01"), "2024-2026": ("2024-01-01", "2026-10-01")}
out = {}
for k, (a, b) in P.items():
    d = M.run("single", f"v142_off_{k}", sets=OFF, model="real_ticks", frm=a, to=b, timeout=900)
    out[f"off_{k}"] = (d.get("metrics") or {}).get("net_profit")
for k, (a, b) in P.items():
    d = M.run("single", f"v142_on_{k}", sets={}, model="real_ticks", frm=a, to=b, timeout=900)
    out[f"on_{k}"] = d.get("metrics")
# forced triggers on a short window
d = M.run("single", "v142_force_daily", sets={"InpDailyLossPct": "0.3", "InpMonthlyLossPct": "0"}, model="real_ticks",
          frm="2024-01-01", to="2024-04-01", timeout=900)
out["force_daily"] = d.get("metrics")
d = M.run("single", "v142_force_monthly", sets={"InpDailyLossPct": "0", "InpMonthlyLossPct": "2.0"}, model="real_ticks",
          frm="2024-01-01", to="2024-04-01", timeout=900)
out["force_monthly"] = d.get("metrics")
d = M.run("single", "v142_force_spread", sets={"InpMaxSpreadPoints": "20", "InpDailyLossPct": "0", "InpMonthlyLossPct": "0"},
          model="real_ticks", frm="2024-01-01", to="2024-04-01", timeout=900)
out["force_spread"] = d.get("metrics")
M.log("V140 reference:", V140)
M.log("RESULT", json.dumps({k: (v if not isinstance(v, dict) else {q: v.get(q) for q in ("net_profit", "trades", "profit_factor", "equity_dd_rel_pct")}) for k, v in out.items()}))
json.dump(out, open(M.os.path.join(M.OUTDIR, "v142_validation.json"), "w"), indent=1, default=str)
M.log("=== DONE")
