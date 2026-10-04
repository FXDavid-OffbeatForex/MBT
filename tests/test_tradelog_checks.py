import datetime as dt

import pytest

import tradelog_checks as K

HDR = ("close_time,open_time,symbol,side,volume,open_price,close_price,sl,tp,gross,commission,swap,profit,"
       "balance,r_multiple,exit_reason,spread_pts,slippage_pts,deal,position,magic\n")


def row(open_t, close_t, side="buy", op=2000.0, tp=2010.0, profit=-100.0, balance=99900.0, r="-1.0",
        reason="sl", spread="30"):
    return (f"{close_t},{open_t},XAUUSD,{side},1.00,{op},{op},0,{tp},0,0,0,{profit},{balance},{r},{reason},"
            f"{spread},0.0,1,1,240819\n")


def write(tmp_path, *rows):
    f = tmp_path / "t.csv"
    f.write_text(HDR + "".join(rows))
    return str(f)


def test_load_rows_types(tmp_path):
    (r,) = K.load_rows(write(tmp_path, row("2024.01.03 10:00:00", "2024.01.03 12:00:00", r="")))
    assert r["open_time"] == dt.datetime(2024, 1, 3, 10)
    assert r["r_multiple"] is None
    assert r["spread_pts"] == 30


def test_trading_hours_subtracts_weekend():
    fri = dt.datetime(2024, 1, 5, 22)          # Friday
    mon = dt.datetime(2024, 1, 8, 2)           # Monday
    assert K.trading_hours(fri, mon) == pytest.approx(4.0)
    assert K.trading_hours(dt.datetime(2024, 1, 3, 10), dt.datetime(2024, 1, 3, 13)) == pytest.approx(3.0)


def test_tp_side_violations(tmp_path):
    rows = K.load_rows(write(tmp_path,
                             row("2024.01.03 10:00:00", "2024.01.03 12:00:00", side="buy", op=2000, tp=2010),
                             row("2024.01.03 13:00:00", "2024.01.03 14:00:00", side="buy", op=2000, tp=1990),
                             row("2024.01.03 15:00:00", "2024.01.03 16:00:00", side="sell", op=2000, tp=1990),
                             row("2024.01.03 17:00:00", "2024.01.03 18:00:00", side="sell", op=2000, tp=0)))
    bad = K.tp_side_violations(rows)
    assert len(bad) == 1 and bad[0]["tp"] == 1990 and bad[0]["side"] == "buy"


def test_time_limit_violations_weekend_aware(tmp_path):
    rows = K.load_rows(write(tmp_path,
                             # Fri 20:00 -> Mon 09:00 = 61h calendar, 13h trading: fine for max_bars=12
                             row("2024.01.05 20:00:00", "2024.01.08 09:00:00", reason="time_limit"),
                             # closed after 3 trading hours: too early for max_bars=12
                             row("2024.01.03 10:00:00", "2024.01.03 13:00:00", reason="time_limit"),
                             # an sl exit is never a time-limit violation
                             row("2024.01.03 14:00:00", "2024.01.03 15:00:00", reason="sl")))
    bad = K.time_limit_violations(rows, max_bars=12)
    assert len(bad) == 1 and bad[0]["open_time"] == dt.datetime(2024, 1, 3, 10)
    assert K.time_limit_violations(rows[1:2], max_bars=1) == []          # 3h is within 1 + 2.5h
    assert K.time_limit_violations(
        K.load_rows(write(tmp_path, row("2024.01.03 10:00:00", "2024.01.03 15:00:00", reason="time_limit"))),
        max_bars=1) != []                                                # 5h > 3.5h -> violation


def test_monthly_stop_check(tmp_path):
    rows = K.load_rows(write(tmp_path,
                             row("2024.01.02 10:00:00", "2024.01.02 12:00:00", profit=-1500, balance=98500),
                             row("2024.01.03 10:00:00", "2024.01.03 12:00:00", profit=-1000, balance=97500),
                             # opened after the -2% breach in the same month -> violation
                             row("2024.01.04 10:00:00", "2024.01.04 12:00:00", profit=100, balance=97600),
                             # next month is allowed
                             row("2024.02.01 10:00:00", "2024.02.01 12:00:00", profit=100, balance=97700)))
    breaches, bad = K.monthly_stop_check(rows, 2.0)
    assert breaches == 1
    assert len(bad) == 1 and bad[0]["open_time"] == dt.datetime(2024, 1, 4, 10)


def test_counters(tmp_path):
    rows = K.load_rows(write(tmp_path,
                             row("2024.01.03 10:00:00", "2024.01.03 12:00:00", reason="sl", r="-1.02", spread="40"),
                             row("2024.01.03 13:00:00", "2024.01.03 14:00:00", reason="time_limit", r="0.3",
                                 spread="15"),
                             row("2024.01.03 15:00:00", "2024.01.03 16:00:00", reason="sl", r="-0.98", spread="")))
    assert K.count_reason(rows, "sl") == 2
    assert K.max_spread(rows) == 40
    assert K.median_sl_r(rows) == pytest.approx(-1.0)
