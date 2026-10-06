import os
import time

import pytest

import ea_profiles as P


def test_expert_path_known_and_unknown():
    assert P.expert_path("MeanRev_EA") == "Advisors\\MeanRev_EA.ex5"
    assert P.expert_path("MACD_Cross_EA") == "Advisors\\MACD_Cross_EA.ex5"
    with pytest.raises(ValueError):
        P.expert_path("NoSuch_EA")


def test_profiles_have_core_inputs():
    core = ["InpMagic", "InpTimeframe", "InpComment", "InpSlippagePoints", "InpMaxSpreadPoints",
            "InpRiskMode", "InpRiskValue", "InpSpreadWaitMin", "InpDailyLossPct", "InpMonthlyLossPct",
            "InpTradeLog", "InpLogEveryBar"]
    for name in ("MACD_Cross_EA", "MeanRev_EA", "VWAP_RSI_EA", "HoLo_EA", "EuroRiddle_EA", "Icarus_EA"):
        for k in core:
            assert k in P.PROFILES[name], (name, k)
    assert P.PROFILES["MeanRev_EA"]["InpMagic"] == "240819"
    assert P.PROFILES["MACD_Cross_EA"]["InpMagic"] == "240817"
    assert P.PROFILES["VWAP_RSI_EA"]["InpMagic"] == "261006"


def test_icarus_profile_pins_the_grid_defaults():
    p = P.PROFILES["Icarus_EA"]
    assert p["InpMagic"] == "261009"
    assert p["InpDailyLossPct"] == "0" and p["InpMonthlyLossPct"] == "0"   # no entry gate for EACore's stops in a grid
    assert p["InpMaxSpreadPoints"] == "100" and p["InpSpreadWaitMin"] == "0"
    assert (p["InpGridProgression"], p["InpLotProgression"], p["InpMaxPositions"]) == ("3", "3", "6")
    assert P.PROFILES["VWAP_RSI_EA"]["InpSpreadWaitMin"] == "0"    # a retried bar-level stop would re-anchor
    assert P.PROFILES["VWAP_RSI_EA"]["InpMinStopATR"] == "0.1"      # micro-stop guard (review finding #1)


def test_merge_inputs_overrides_and_copies():
    merged = P.merge_inputs("MeanRev_EA", {"InpSLATR": "2.0"})
    assert merged["InpSLATR"] == "2.0"
    assert P.PROFILES["MeanRev_EA"]["InpSLATR"] == "1.5"   # profile untouched


def test_merge_inputs_rejects_unknown():
    with pytest.raises(ValueError, match="InpSLATRR"):
        P.merge_inputs("MeanRev_EA", {"InpSLATRR": "2.0"})


def test_parse_range_ok_and_refusals():
    assert P.parse_range("InpSLATR=1.0:0.5:2.0") == ("InpSLATR", ("1.0", "0.5", "2.0"))
    for bad in ("InpZeroLineFilter=false:0:true", "InpSLATR=1:0:2", "InpSLATR=2:1:1", "InpSLATR=1:2"):
        with pytest.raises(ValueError):
            P.parse_range(bad)


def test_tester_input_lines_pins_ranges_and_strings():
    inputs = {"InpComment": "MREV", "InpSLATR": "1.5", "InpMaxBars": "12"}
    lines = P.tester_input_lines(inputs, {"InpSLATR": ("1.0", "0.5", "2.0")})
    assert lines == ["InpComment=MREV",
                     "InpSLATR=1.5||1.0||0.5||2.0||Y",
                     "InpMaxBars=12||12||1||12||N"]


def test_tester_input_lines_rejects_range_for_unknown_input():
    with pytest.raises(ValueError):
        P.tester_input_lines({"InpSLATR": "1.5"}, {"InpNope": ("1", "1", "2")})


def test_tester_log_name():
    assert P.tester_log_name("MeanRev_EA", "XAUUSD", "240819") == "MeanRev_EA_XAUUSD_240819_tester_trades.csv"


def test_pick_fresh_log_accepts_new_file(tmp_path):
    f = tmp_path / "log.csv"
    started = time.time() - 1
    f.write_text("x")
    assert P.pick_fresh_log(str(f), started) == str(f)


def test_pick_fresh_log_rejects_stale_or_missing(tmp_path):
    f = tmp_path / "log.csv"
    f.write_text("x")
    old = time.time() - 3600
    os.utime(f, (old, old))
    assert P.pick_fresh_log(str(f), time.time()) is None
    assert P.pick_fresh_log(str(tmp_path / "missing.csv"), 0) is None


def test_atr_candle_breakout_profile():
    prof = P.PROFILES["ATR Candle Breakout EA"]
    assert prof["InpMagicNumber"] == "14" and "InpMagic" not in prof
    assert P.expert_path("ATR Candle Breakout EA") == "Advisors\\ATR Candle Breakout EA.ex5"


def test_trade_log_name_for_needs_inpmagic():
    assert P.trade_log_name_for("MeanRev_EA", "XAUUSD", P.PROFILES["MeanRev_EA"]) == \
        "MeanRev_EA_XAUUSD_240819_tester_trades.csv"
    assert P.trade_log_name_for("ATR Candle Breakout EA", "XAUUSD", P.PROFILES["ATR Candle Breakout EA"]) is None


def test_holo_profile_matches_design():
    p = P.PROFILES["HoLo_EA"]
    assert p["InpMagic"] == "261007" and p["InpSpreadWaitMin"] == "0"      # entry exactly at the level: no delayed retry
    assert p["InpTimeframe"] == "15" and p["InpLevelTF"] == "16385"         # M15 trigger, H1 levels
    assert (p["InpSessionStartNY"], p["InpSessionEndNY"], p["InpServerNYOffset"]) == ("480", "1015", "7")


def test_euroriddle_profile_matches_prereg():
    p = P.PROFILES["EuroRiddle_EA"]
    assert p["InpMagic"] == "261008" and p["InpSpreadWaitMin"] == "0"
    assert (p["InpStopPips"], p["InpTargetPips"], p["InpMaxSpreadPoints"]) == ("15", "25", "20")   # 2 pips on EURUSD
    assert (p["InpMode"], p["InpFilter"], p["InpADRPeriod"], p["InpADRFrac"], p["InpLateStartNY"]) == ("0", "0", "20", "1.0", "720")
