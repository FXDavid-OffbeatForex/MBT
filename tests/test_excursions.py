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


def path_trade(path, d=10.0, mfe=2.0, close_r=-1.0, cost_r=-0.02):
    """path: [(favourable price move reached, worst adverse price move so far)], prices relative to entry."""
    return {"path": path, "risk_price": d, "mfe_r": mfe, "close_r": close_r, "cost_r": cost_r}


def test_mae_before_counts_only_adverse_moves_before_the_target():
    p = [(0.0, 3.0), (4.0, 3.0), (4.0, 6.0), (12.0, 6.0)]     # down 3, up to 4, down to 6, then up to 12
    assert X.mae_before(p, 4.0) == 3.0                          # +4 first reached after the -3 only
    assert X.mae_before(p, 5.0) == 6.0                          # +5 came after the -6
    assert X.max_mae(p) == 6.0


def test_tighter_stop_and_target_replay():
    p = [(0.0, 3.0), (4.0, 3.0), (4.0, 6.0), (12.0, 6.0)]
    t = path_trade(p, d=10.0, mfe=1.2, close_r=0.8)
    # stop 5 (half the original 10): the -6 dip stops it before +2R (10) -> -1R, costs double (d/s = 2)
    assert X.stop_target_r(t, 5.0, 2.0) == pytest.approx(-1.0 - 0.04)
    # stop 5, target 0.8R = +4: reached after only a -3 dip -> win
    assert X.stop_target_r(t, 5.0, 0.8) == pytest.approx(0.8 - 0.04)
    # stop 8: the -6 dip survives; +1R = 8 reached at the end -> win
    assert X.stop_target_r(t, 8.0, 1.0) == pytest.approx(1.0 - 0.025)
    # stop 8, target 3R = 24 never reached, stop never hit -> session flat at +8 price = +1R of the new stop
    assert X.stop_target_r(t, 8.0, 3.0) == pytest.approx(8.0 / 8.0 - 0.025)


def test_original_stop_matches_plain_take_profit():
    p = [(0.0, 2.0), (15.0, 2.0)]
    t = path_trade(p, d=10.0, mfe=1.5, close_r=-1.0)
    t.update({"reached": {1.0: True}, "returned": {1.0: True}, "run": {1.0: 1.5}})
    assert X.stop_target_r(t, 10.0, 1.0) == pytest.approx(X.variant_r(t, "tp", 1.0))


def test_terciles_split_into_thirds():
    lo, hi = X.terciles([1, 2, 3, 4, 5, 6, 7, 8, 9])
    assert sum(v < lo for v in range(1, 10)) == 3 and sum(v >= hi for v in range(1, 10)) == 3


def test_exit_price_counts_as_the_last_adverse_move():
    # stopped at the original stop (close_r -1) but the stop-out tick itself was never logged: worst logged -7.0
    t = path_trade([(0.0, 3.0), (2.0, 7.0)], d=10.0, mfe=0.2, close_r=-1.0, cost_r=0.0)
    assert X.stop_target_r(t, 9.0, 1.0) == pytest.approx(-1.0)     # a 9-point stop is hit by the -10 exit


def test_stop_outs_carry_slippage():
    # original stop, actually filled 0.2R beyond it: the replay keeps the real fill
    t = path_trade([(0.0, 10.0)], d=10.0, mfe=0.0, close_r=-1.2, cost_r=0.0)
    assert X.stop_target_r(t, 10.0, 1.0) == pytest.approx(-1.2)
    # half stop: charge the setup's average price slippage (0.1) in R of the 5-point stop
    assert X.stop_target_r(t, 5.0, 1.0, slip_price=0.1) == pytest.approx(-1.02)
