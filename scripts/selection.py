"""Shoot-out selection rules for MeanRev_EA (spec section 4). Pure functions, no MT5."""
import itertools

GATE_KEYS = ("1_oos", "2_in_sample", "3_no_disaster_year", "4_correlation", "5_better_together")


def split_uniform(values):
    """Cover a list of grid values with evenly spaced runs (MT5 ranges) plus single values."""
    vals = [float(v) for v in values]
    raw = list(values)
    segs, i = [], 0
    while i < len(vals):
        j = i + 1
        if j < len(vals):
            step = vals[j] - vals[i]
            while j + 1 < len(vals) and abs((vals[j + 1] - vals[j]) - step) < 1e-9:
                j += 1
            segs.append(f"{raw[i]}:{_step_str(raw[i], raw[i + 1], step)}:{raw[j]}")
            i = j + 1
        else:
            segs.append(raw[i])
            i += 1
    return segs


def _step_str(a, b, step):
    decimals = max(len(a.split(".")[1]) if "." in a else 0, len(b.split(".")[1]) if "." in b else 0)
    return f"{step:.{decimals}f}"


def plan_launches(grid):
    """(fixed_sets, ranges) per tester launch so that all launches together cover the grid exactly."""
    per_param = []
    for name, values in grid.items():
        options = []
        for seg in split_uniform(values):
            options.append((name, seg))
        per_param.append(options)
    launches = []
    for combo in itertools.product(*per_param):
        fixed, ranges = {}, {}
        for name, seg in combo:
            if ":" in seg:
                ranges[name] = seg
            else:
                fixed[name] = seg
        launches.append((fixed, ranges))
    return launches


def drop_failed_passes(passes):
    """Remove passes whose OnInit failed (MT5 still emits a row: Trades 0, empty Profit Factor)."""
    kept = [p for p in passes if not (float(p.get("Trades") or 0) == 0 and p.get("Profit Factor") in ("", None))]
    return kept, len(passes) - len(kept)


def score(p):
    return float(p.get("Profit Factor") or 0.0) * float(p.get("Recovery Factor") or 0.0)


def survivors(passes, min_trades=150, max_dd=20.0):
    return [p for p in passes
            if float(p.get("Trades") or 0) >= min_trades
            and float(p.get("Equity DD %") or 999.0) <= max_dd
            and float(p.get("Profit Factor") or 0.0) > 1.0]


def neighbours(p, passes, grid):
    axes = {k: sorted(float(v) for v in vals) for k, vals in grid.items()}
    out = []
    for k, vals in axes.items():
        idx = vals.index(float(p[k]))
        for j in (idx - 1, idx + 1):
            if 0 <= j < len(vals):
                want = dict((q, float(p[q])) for q in axes)
                want[k] = vals[j]
                for cand in passes:
                    if all(abs(float(cand[q]) - want[q]) < 1e-9 for q in axes):
                        out.append(cand)
                        break
    return out


def pick_plateau(passes, grid, min_trades=150, max_dd=20.0, min_neighbour_pf=1.10):
    ranked = sorted(survivors(passes, min_trades, max_dd), key=score, reverse=True)
    for cand in ranked:
        nb = neighbours(cand, passes, grid)
        if nb and sum(float(q.get("Profit Factor") or 0.0) for q in nb) / len(nb) >= min_neighbour_pf:
            return cand
    return None


def joint_pick(passes_a, passes_b, grid, min_trades=150, max_dd=20.0, min_pf=1.15, min_neighbour_pf=1.10):
    """pick_plateau across two regimes: the same parameter tuple must survive, reach min_pf and sit on a plateau
    in BOTH; ranked by the weaker regime's score. Returns (pass_a, pass_b) or None."""
    def key(p):
        return tuple(float(p[k]) for k in grid)

    def on_plateau(p, passes):
        nb = neighbours(p, passes, grid)
        return bool(nb) and sum(float(q.get("Profit Factor") or 0.0) for q in nb) / len(nb) >= min_neighbour_pf

    in_b = {key(p): p for p in survivors(passes_b, min_trades, max_dd)}
    best = None
    for pa in survivors(passes_a, min_trades, max_dd):
        pb = in_b.get(key(pa))
        if pb is None or min(float(pa["Profit Factor"]), float(pb["Profit Factor"])) < min_pf:
            continue
        if not (on_plateau(pa, passes_a) and on_plateau(pb, passes_b)):
            continue
        s = min(score(pa), score(pb))
        if best is None or s > best[0]:
            best = (s, pa, pb)
    return None if best is None else (best[1], best[2])


def evaluate_gates(years, in_sample, corr, combined, macd_alone, deposit=100000.0):
    oos = [years["2022"], years["2023"]]
    oos_net = sum(y["net_profit"] for y in oos)
    oos_gl = sum(-y["gross_loss"] for y in oos)
    oos_pf = (sum(y["gross_profit"] for y in oos) / oos_gl) if oos_gl > 0 else 0.0
    worst_year = min(y["net_profit"] / deposit for y in years.values())
    ins_pf = float(in_sample.get("profit_factor") or 0.0)
    calib_c, calib_m = combined.get("calib_median_days"), macd_alone.get("calib_median_days")
    better = (combined["ret_dd"] > macd_alone["ret_dd"] and calib_c is not None
              and (calib_m is None or calib_c < calib_m))
    return {
        "1_oos": (oos_net > 0 and oos_pf >= 1.15, f"2022+2023 net {oos_net:.0f}, PF {oos_pf:.2f}"),
        "2_in_sample": (ins_pf >= 1.20, f"2024-26 PF {ins_pf:.2f}"),
        "3_no_disaster_year": (worst_year >= -0.10, f"worst year {100 * worst_year:.1f}%"),
        "4_correlation": (corr <= 0.3, f"monthly correlation with MACD {corr:.2f}"),
        "5_better_together": (better, f"ret/DD {combined['ret_dd']:.2f} vs MACD {macd_alone['ret_dd']:.2f}; "
                                      f"calibration {calib_c} vs {calib_m} days"),
    }


def choose_winner(results):
    passed = [m for m, r in results.items() if all(r["gates"][k][0] for k in GATE_KEYS)]
    if not passed:
        return None
    return sorted(passed, key=lambda m: (results[m]["corr"], -results[m]["combined"]["ret_dd"]))[0]
