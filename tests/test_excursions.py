import pytest

import excursions as X


def trade(close_r, cost_r=-0.05, **levels):
    """levels: x -> (reached, returned, run)."""
    t = {"close_r": close_r, "cost_r": cost_r, "reached": {}, "returned": {}, "run": {}}
    for x, (hit, back, run) in levels.items():
        x = float(x.replace("x", "").replace("_", "."))
        t["reached"][x], t["returned"][x], t["run"][x] = hit, back, run
    return t


def test_take_profit_only():
    win = trade(-1.0, x0_5=(True, True, 0.8))          # reached 0.5R before the stop
    loss = trade(-1.0, x0_5=(False, False, 0.0))       # stopped before 0.5R
    assert X.variant_r(win, "tp", 0.5) == pytest.approx(0.45)
    assert X.variant_r(loss, "tp", 0.5) == pytest.approx(-1.05)


def test_partial_then_break_even_to_session_flat():
    back = trade(-1.0, x0_5=(True, True, 0.8))          # half at 0.5R, rest stopped at entry
    ran = trade(1.7, x0_5=(True, False, 1.9))           # half at 0.5R, rest never came back: session flat at +1.7R
    never = trade(0.3, x0_5=(False, False, 0.0))        # never reached 0.5R: whole position exits at the flat
    assert X.variant_r(back, "partial", 0.5) == pytest.approx(0.25 - 0.05)
    assert X.variant_r(ran, "partial", 0.5) == pytest.approx(0.25 + 0.85 - 0.05)
    assert X.variant_r(never, "partial", 0.5) == pytest.approx(0.25)


def test_partial_with_second_target():
    hit2 = trade(-1.0, x0_5=(True, True, 1.2))          # ran to 1.2R before coming back: second half at 1R
    miss2 = trade(-1.0, x0_5=(True, True, 0.8))         # came back before 1R: second half at break-even
    assert X.variant_r(hit2, "partial", 0.5, 1.0) == pytest.approx(0.25 + 0.5 - 0.05)
    assert X.variant_r(miss2, "partial", 0.5, 1.0) == pytest.approx(0.25 - 0.05)


def test_summary():
    s = X.summary([1.0, -0.5, 0.5, -1.0])
    assert s["n"] == 4 and s["net_r"] == pytest.approx(0.0) and s["pf"] == pytest.approx(1.0)
    assert s["win_pct"] == pytest.approx(50.0)


def test_load_joins_trade_log_costs(tmp_path):
    exc = tmp_path / "exc.csv"
    exc.write_text("position,side,entry,risk_price,risk_money,mfe_r,hit_0.50,back_0.50,run_0.50\n"
                   "7,sell,2000.00,5.00,1000.00,0.80,1,1,0.80\n")
    log = tmp_path / "log.csv"
    log.write_text("close_time,open_time,symbol,side,volume,open_price,close_price,sl,tp,gross,commission,swap,profit,"
                   "balance,r_multiple,exit_reason,spread_pts,slippage_pts,deal,position,magic\n"
                   "2024.01.03 12:00:00,2024.01.03 10:00:00,XAUUSD,sell,2.00,2000.00,2005.00,2005.00,0,-1000.00,"
                   "-30.00,0.00,-1030.00,98970.00,-1.030,sl,30,0.0,9,7,261007\n")
    (t,) = X.load(str(exc), str(log))
    assert t["close_r"] == pytest.approx(-1.0) and t["cost_r"] == pytest.approx(-0.03)
    assert t["reached"][0.5] and t["returned"][0.5] and t["run"][0.5] == pytest.approx(0.8)
    assert t["open_time"].year == 2024
