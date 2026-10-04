import datetime as dt

import pytest

import combine_eas as C

T0 = dt.datetime(2024, 1, 1, 10, 0, 0)


def tr(day, profit, balance_after, hours=2.0, volume=1.0, price=2000.0):
    o = T0 + dt.timedelta(days=day)
    return C.Trade(open_time=o, close_time=o + dt.timedelta(hours=hours), symbol="XAUUSD",
                   volume=volume, open_price=price, profit=profit, balance_after=balance_after)


def test_trade_return_uses_balance_before_close():
    assert tr(0, 1000.0, 101000.0).ret == pytest.approx(0.01)


def test_load_trades_parses_csv(tmp_path):
    f = tmp_path / "t.csv"
    f.write_text("close_time,open_time,symbol,side,volume,open_price,close_price,sl,tp,gross,commission,swap,profit,"
                 "balance,r_multiple,exit_reason,spread_pts,slippage_pts,deal,position,magic\n"
                 "2024.01.03 11:44:49,2024.01.03 06:00:00,XAUUSD,buy,0.96,2065.22,2054.89,2054.89,2142.67,"
                 "-991.68,-9.89,0.00,-1001.57,98998.43,-1.010,sl,12,0.0,3,2,240817\n")
    (t,) = C.load_trades(str(f))
    assert t.open_time == dt.datetime(2024, 1, 3, 6, 0, 0)
    assert t.profit == pytest.approx(-1001.57)
    assert t.balance_after == pytest.approx(98998.43)


def test_merged_curve_and_drawdown():
    a = [tr(0, 1000.0, 101000.0), tr(2, -2020.0, 98980.0)]   # +1%, then -2%
    b = [tr(1, 1000.0, 101000.0)]                            # +1%
    rets = C.merged_returns(a, b)
    assert [round(r, 4) for _, r in rets] == [0.01, 0.01, -0.02]
    curve = C.equity_curve(rets)
    assert curve[-1][1] == pytest.approx(1.01 * 1.01 * 0.98)
    assert C.max_drawdown_pct(curve) == pytest.approx(2.0)


def test_period_returns_compound():
    rets = [(T0, 0.10), (T0 + dt.timedelta(days=1), 0.10), (T0 + dt.timedelta(days=40), -0.05)]
    m = C.period_returns(rets, "%Y-%m")
    assert m["2024-01"] == pytest.approx(21.0)
    assert m["2024-02"] == pytest.approx(-5.0)


def test_monthly_correlation_identical_and_opposite():
    a = [tr(0, 1000.0, 101000.0), tr(35, -1000.0, 100000.0), tr(70, 2000.0, 102000.0)]
    neg = [tr(0, -1000.0, 99000.0), tr(35, 1000.0, 100000.0), tr(70, -2000.0, 98000.0)]
    assert C.monthly_correlation(a, a) == pytest.approx(1.0)
    assert C.monthly_correlation(a, neg) == pytest.approx(-1.0, abs=0.02)


def test_monthly_correlation_zero_variance():
    a = [tr(0, 1000.0, 101000.0), tr(35, 1000.0, 102000.0)]
    flat = []
    assert C.monthly_correlation(a, flat) == 0.0


def test_calibration_days_reaches_25_decisions():
    trades = [tr(d, 0.0, 100000.0) for d in range(40)]   # identical exposure: each trade weighs exactly 1
    days = C.calibration_days(trades)
    assert days == [24]                                   # 25th trade opens on day 24 of January
    assert C.calibration_days(trades[:10]) == []


def test_summarize_keys_and_values():
    a = [tr(0, 1000.0, 101000.0), tr(2, -2020.0, 98980.0)]
    s = C.summarize(a)
    assert set(s) == {"net_return_pct", "max_dd_pct", "ret_dd", "worst_day_pct", "worst_month_pct",
                      "calib_median_days", "trades"}
    assert s["trades"] == 2
    assert s["worst_day_pct"] == pytest.approx(-2.0)
    assert s["calib_median_days"] is None
