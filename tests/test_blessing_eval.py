import datetime as dt

import blessing_eval as B


def test_ladder_matches_blessing_formula():
    assert B.ladder() == [0.01, 0.02, 0.03, 0.04, 0.06, 0.08, 0.11, 0.15, 0.21, 0.29, 0.41, 0.57, 0.80, 1.12, 1.57]
    assert B.ladder(multiplier=0.9, n=4) == [0.01, 0.02, 0.03, 0.04]   # +step floor when the multiplier is below 1


def _leg(side, open_t, close_t, vol, px, profit=1.0, kind="market"):
    return {"side": side, "kind": kind, "open_time": dt.datetime.fromisoformat(open_t), "close_time": dt.datetime.fromisoformat(close_t),
            "volume": vol, "open_price": px, "close_price": px, "profit": profit, "balance": 100000.0, "open_balance": 100000.0}


def test_baskets_gaps_ladder_and_gate():
    tr = [_leg("buy", "2021-03-01T04:10", "2021-03-02T08:00", 0.20, 1.1000, kind="pending"),   # entry stop
          _leg("buy", "2021-03-01T05:30", "2021-03-02T08:00", 0.20, 1.0965, kind="pending"),   # the other entry pending, 35 pips below
          _leg("buy", "2021-03-01T08:00", "2021-03-02T08:00", 0.60, 1.0940),                    # SmartGrid add: Lots[2], 25 below the lowest
          _leg("buy", "2021-03-01T12:00", "2021-03-02T08:00", 0.80, 1.0900),                    # Lots[3], 40 below (overshoot)
          _leg("sell", "2021-03-01T04:00", "2021-03-02T08:00", 0.21, 1.1000, profit=5.0),
          _leg("buy", "2023-05-01T00:00", "2023-05-01T04:00", 0.20, 1.1000, profit=-8.0)]
    b = B.baskets(tr)
    assert len(b) == 3 and [t["volume"] for t in b[("buy", dt.datetime(2021, 3, 2, 8))]] == [0.20, 0.20, 0.60, 0.80]
    assert B.leg_gaps(b, 0.0001, kind="market") == [(25.0, 2, 25), (40.0, 3, 25)]
    dbl = B.baskets([_leg("buy", "2023-04-04T04:35", "2023-04-11T12:00", 0.22, 1.0895, kind="pending"),    # entry limit
                     _leg("buy", "2023-04-04T10:52", "2023-04-11T12:00", 0.22, 1.09301, kind="pending"),   # entry stop, later and higher
                     _leg("buy", "2023-04-06T08:00", "2023-04-11T12:00", 0.66, 1.08934)])                   # add measured from the stop fill
    assert B.leg_gaps(dbl, 0.0001, kind="market") == [(36.7, 2, 25)]
    assert B.leg_gaps(b, 0.0001, kind="pending") == [(35.0, 1, 25)]
    assert [B.grid_pips(n) for n in (1, 4, 5, 8, 9, 15)] == [25, 25, 50, 50, 100, 100]
    assert B.ladder_violations(b) == []
    assert B.lotmult_expected(100000.0) == 20 and B.lotmult_expected(62000.0) == 12 and B.lotmult_expected(1000.0) == 1
    tr[3]["volume"] = 0.70
    assert len(B.ladder_violations(B.baskets(tr))) == 1
    assert B.regime_pnl(tr) == {"R18/19-21": 9.0, "R22-24": -8.0}
    years = B.regime_years(2019, 2025)
    k, per_year, verdict = B.gate({"R18/19-21": 30000.0, "R22-24": 15000.0}, years, 16.0)   # k = 0.5
    assert k == 0.5 and per_year["R18/19-21"] == 5.0 and per_year["R22-24"] == 2.5 and verdict == "fail"
    assert B.gate({"R18/19-21": 90000.0, "R22-24": 90000.0}, years, 16.0)[2] == "PASS*"
    assert B.gate({"R18/19-21": 0.0, "R22-24": 1.0}, B.regime_years(2024, 2025), 4.0)[2] == "n/a"
    assert B.local("Z:\\Users\\x\\MBT\\reports\\a.htm") == "/Users/x/MBT/reports/a.htm"


def test_deals_trades_matches_out_to_same_volume_leg(tmp_path):
    def row(t, typ, direction, vol, px, comm, swap, profit, bal):
        oid = "2" if vol == "0.40" else "1"
        return f"<tr><td>{t}</td><td>1</td><td>EURUSD</td><td>{typ}</td><td>{direction}</td><td>{vol}</td><td>{px}</td><td>{oid}</td><td>{comm}</td><td>{swap}</td><td>{profit}</td><td>{bal}</td><td></td></tr>"
    def order(t, oid, typ):
        return f"<tr><td>{t}</td><td>{oid}</td><td>EURUSD</td><td>{typ}</td><td>0.20</td><td>1.1</td><td></td><td></td><td>{t}</td><td>filled</td><td></td></tr>"
    f = tmp_path / "r.htm"
    f.write_text("<b>Orders</b><table>" + order("2024.01.02 03:00:00", "1", "buy stop") + order("2024.01.02 08:00:00", "2", "buy") + "</table>"
                 "<b>Deals</b><table>" + row("2024.01.02 04:00:00", "buy", "in", "0.20", "1.1", "-0.6", "0", "0", "99999.4")
                 + row("2024.01.02 08:00:00", "buy", "in", "0.40", "1.0975", "-1.2", "0", "0", "99998.2")
                 + row("2024.01.03 00:00:00", "sell", "out", "0.40", "1.1", "-1.2", "-0.5", "100", "100096.5")
                 + row("2024.01.03 00:00:00", "sell", "out", "0.20", "1.1", "-0.6", "-0.2", "0", "100095.7") + "</table>", encoding="utf-8")
    tr, mx = B.deals_trades(str(f))
    assert mx == 2 and len(tr) == 2
    big = next(t for t in tr if t["volume"] == 0.40)
    assert big["open_price"] == 1.0975 and abs(big["profit"] - (100 - 0.5 - 1.2 - 1.2)) < 1e-9
    assert big["kind"] == "market" and big["open_balance"] == 99998.2
    assert next(t for t in tr if t["volume"] == 0.20)["kind"] == "pending"
