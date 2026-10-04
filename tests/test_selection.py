import itertools

import selection as S


def test_split_uniform():
    assert S.split_uniform(["25", "30", "50"]) == ["25:5:30", "50"]
    assert S.split_uniform(["15", "20", "25", "100"]) == ["15:5:25", "100"]
    assert S.split_uniform(["1.0", "1.5", "2.0", "3.0"]) == ["1.0:0.5:2.0", "3.0"]
    assert S.split_uniform(["1.5", "2.0", "3.0", "4.0"]) == ["1.5:0.5:2.0", "3.0:1.0:4.0"]
    assert S.split_uniform(["6", "12", "24"]) == ["6:6:12", "24"]
    assert S.split_uniform(["2.0", "2.5"]) == ["2.0:0.5:2.5"]
    assert S.split_uniform(["200"]) == ["200"]


def test_plan_launches_covers_grid_exactly():
    grid = {"A": ["1", "2", "4"], "B": ["10", "20"]}
    launches = S.plan_launches(grid)
    covered = set()
    for fixed, ranges in launches:
        axes = []
        for k in grid:
            if k in fixed:
                axes.append([float(fixed[k])])
            else:
                a, s, b = (float(x) for x in ranges[k].split(":"))
                n = int(round((b - a) / s)) + 1
                axes.append([a + i * s for i in range(n)])
        covered |= set(itertools.product(*axes))
    want = set(itertools.product([1.0, 2.0, 4.0], [10.0, 20.0]))
    assert covered == want


def p(pf, rf, trades=200, dd=10.0, **params):
    d = {"Profit Factor": pf, "Recovery Factor": rf, "Trades": trades, "Equity DD %": dd, "Profit": 1.0}
    d.update(params)
    return d


GRID = {"X": ["1", "2", "3"], "Y": ["10", "20", "30"]}


def full_grid(pf_of):
    return [p(pf_of(x, y), 2.0, X=float(x), Y=float(y)) for x in (1, 2, 3) for y in (10, 20, 30)]


def test_survivors_filters():
    ps = [p(1.5, 2, trades=100), p(1.5, 2, dd=25), p(0.9, 2), p(1.2, 2)]
    assert S.survivors(ps) == [ps[3]]


def test_neighbours_one_step_only():
    passes = full_grid(lambda x, y: 1.2)
    centre = next(q for q in passes if q["X"] == 2.0 and q["Y"] == 20.0)
    nb = S.neighbours(centre, passes, GRID)
    assert sorted((q["X"], q["Y"]) for q in nb) == [(1.0, 20.0), (2.0, 10.0), (2.0, 30.0), (3.0, 20.0)]


def test_pick_plateau_prefers_broad_region_over_spike():
    def pf(x, y):
        if (x, y) == (3, 30):
            return 3.0          # isolated spike: neighbours (2,30)=1.15, (3,20)=0.9 average 1.025 < 1.10
        return 1.15 if x <= 2 else 0.9
    pick = S.pick_plateau(full_grid(pf), GRID)
    assert pick is not None
    assert (pick["X"], pick["Y"]) != (3.0, 30.0)
    assert pick["X"] <= 2.0


def test_pick_plateau_no_survivors():
    assert S.pick_plateau(full_grid(lambda x, y: 0.9), GRID) is None
    assert S.pick_plateau([], GRID) is None


def year(net, gp, gl):
    return {"net_profit": net, "gross_profit": gp, "gross_loss": gl, "profit_factor": gp / -gl, "trades": 100}


def good_inputs():
    years = {"2022": year(5000, 30000, -25000), "2023": year(6000, 30000, -24000),
             "2024": year(8000, 40000, -32000), "2025": year(9000, 40000, -31000), "2026": year(4000, 20000, -16000)}
    in_sample = {"profit_factor": 1.25}
    combined = {"ret_dd": 3.0, "calib_median_days": 60}
    macd = {"ret_dd": 2.5, "calib_median_days": 100}
    return years, in_sample, 0.1, combined, macd


def test_gates_all_pass():
    g = S.evaluate_gates(*good_inputs())
    assert all(ok for ok, _ in g.values()), g


def test_gates_each_failure():
    years, ins, corr, comb, macd = good_inputs()
    years["2022"] = year(-20000, 10000, -30000)
    g = S.evaluate_gates(years, ins, corr, comb, macd)
    assert not g["1_oos"][0] and not g["3_no_disaster_year"][0]
    g = S.evaluate_gates(*good_inputs()[:1], {"profit_factor": 1.1}, 0.1, comb, macd)
    assert not g["2_in_sample"][0]
    g = S.evaluate_gates(years, ins, 0.45, comb, macd)
    assert not g["4_correlation"][0]
    g = S.evaluate_gates(years, ins, corr, {"ret_dd": 2.0, "calib_median_days": 60}, macd)
    assert not g["5_better_together"][0]
    g = S.evaluate_gates(years, ins, corr, {"ret_dd": 3.0, "calib_median_days": None}, macd)
    assert not g["5_better_together"][0]


def test_choose_winner():
    passing = {k: (True, "") for k in ("1_oos", "2_in_sample", "3_no_disaster_year", "4_correlation",
                                       "5_better_together")}
    failing = dict(passing, **{"1_oos": (False, "x")})
    res = {"bb": {"gates": passing, "corr": 0.20, "combined": {"ret_dd": 3.0}},
           "rsi2": {"gates": passing, "corr": 0.05, "combined": {"ret_dd": 2.0}}}
    assert S.choose_winner(res) == "rsi2"
    res["rsi2"]["corr"] = 0.20
    assert S.choose_winner(res) == "bb"
    res["bb"]["gates"] = failing
    assert S.choose_winner(res) == "rsi2"
    res["rsi2"]["gates"] = failing
    assert S.choose_winner(res) is None
