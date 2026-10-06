import datetime as dt

import icarus_eval as I
import tradelog_checks as K

HDR = ("close_time,open_time,symbol,side,volume,open_price,close_price,sl,tp,gross,commission,swap,profit,"
       "balance,r_multiple,exit_reason,spread_pts,slippage_pts,deal,position,magic\n")


def row(open_t, close_t, side="buy", vol=0.01, op=1.1000, profit=1.0, reason="basket_tp"):
    return (f"{close_t},{open_t},EURUSD,{side},{vol:.2f},{op:.5f},{op:.5f},0.00000,0.00000,0,0,0,{profit},100000,,{reason},"
            f",,1,1,261009\n")


def rows(tmp_path):
    f = tmp_path / "t.csv"
    f.write_text(HDR + "".join([
        row("2021.03.01 10:00:00", "2021.03.02 09:00:00", vol=0.01, op=1.1000, profit=0.4),      # buy basket A, leg 1
        row("2021.03.01 12:00:00", "2021.03.02 09:00:00", vol=0.01, op=1.0980, profit=0.3),      # leg 2, 20 pips lower
        row("2021.03.01 15:00:00", "2021.03.02 09:00:00", vol=0.02, op=1.0960, profit=0.5),      # leg 3
        row("2021.03.01 10:00:00", "2021.03.02 09:00:00", side="sell", op=1.1000, profit=0.8),   # sell basket, same tick
        row("2023.06.01 10:00:00", "2023.06.01 11:00:00", profit=-2.0),                          # 2023 basket (loss, odd)
        row("2024.12.30 10:00:00", "2024.12.31 23:59:00", profit=-5.0, reason="end_of_test"),
    ]))
    return K.load_rows(str(f))


def test_baskets_group_by_side_and_close_tick_oldest_first(tmp_path):
    b = I.baskets(rows(tmp_path))
    assert len(b) == 3
    legs = b[("buy", dt.datetime(2021, 3, 2, 9))]
    assert [r["volume"] for r in legs] == [0.01, 0.01, 0.02]
    assert I.lots_prefix_ok(legs, I.FIB) and not I.lots_prefix_ok(legs, [1, 2, 4])
    assert I.leg_gaps(b) == {1: [20.0], 2: [20.0]}


def test_regime_pnl_and_gate(tmp_path):
    r = rows(tmp_path)
    net = I.regime_pnl(r)
    assert net == {"R18/19-21": 2.0, "R22-24": -7.0}
    years = I.regime_years(2019, 2025)
    assert years == {"R18/19-21": 3, "R22-24": 3}
    k, per_year, verdict = I.gate({"R18/19-21": 60000.0, "R22-24": 45000.0}, years, 16000.0)   # k = 0.5
    assert k == 0.5 and per_year["R18/19-21"] == 10000.0 and per_year["R22-24"] == 7500.0 and verdict == "fail"
    assert I.gate({"R18/19-21": 60000.0, "R22-24": 60000.0}, years, 16000.0)[2] == "PASS"
    assert I.gate(net, I.regime_years(2024, 2025), 100.0)[2] == "n/a"


def test_events_parse_sides_and_values():
    ev = I.events("2024.01.02 ICARUS ADD BUY n=2 lots=0.01 gap=20 trigger=-2.00 profit=-2.13\n"
                  "x ICARUS CLOSE SELL n=3 total=0.70 target=4.00 max=4.10 lock=1.23\nICARUS RED equity=99000.00 peak=100000.00\n")
    assert ev[0] == ("ADD", "buy", {"n": 2, "lots": 0.01, "gap": 20, "trigger": -2.0, "profit": -2.13})
    assert ev[1][0:2] == ("CLOSE", "sell") and ev[2] == ("RED", "", {"equity": 99000.0, "peak": 100000.0})
    assert [I.gap_factor(n, 3) for n in range(1, 6)] == [1, 1, 2, 3, 5] and I.gap_factor(4, 2) == 8 and I.gap_factor(4, 1) == 4
