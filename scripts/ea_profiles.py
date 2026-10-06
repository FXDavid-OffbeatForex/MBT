"""Per-EA tester input defaults and [TesterInputs] line building.

Pure module (no MT5 / Wine imports) so it is importable by system python and tests.
Every EA input is written explicitly: MT5 silently applies the last GUI .set for any input left out,
and a bare "k=v" keeps an optimize flag cached from an earlier run, so fixed inputs are pinned as
v||v||1||v||N. Unknown input names are refused instead of silently ignored.
"""
import os

STRING_INPUTS = {"InpComment"}

_CORE_DEFAULTS = {
    "InpTimeframe": "16385", "InpSlippagePoints": "30", "InpMaxSpreadPoints": "150",
    "InpRiskMode": "1", "InpRiskValue": "1.0",
    "InpSpreadWaitMin": "30", "InpDailyLossPct": "4.0", "InpMonthlyLossPct": "12.0", "InpTradeLog": "true",
    "InpLogEveryBar": "false",   # hourly status lines are for live/VPS, too noisy for tester runs
}

PROFILES = {
    # MACD_Cross_EA v1.42 compiled defaults
    "MACD_Cross_EA": dict(_CORE_DEFAULTS, **{
        "InpMagic": "240817", "InpComment": "MACD_X",
        "InpFastEMA": "16", "InpSlowEMA": "26", "InpSignalSMA": "9", "InpAppliedPrice": "1",
        "InpZeroLineFilter": "false",
        "InpUseTrendFilter": "true", "InpTrendTF": "16388", "InpTrendPeriod": "200",
        "InpMinGapATR": "0.0", "InpATRPeriod": "14",
        "InpUseTimeFilter": "false", "InpStartHour": "7", "InpEndHour": "20",
        "InpStopLossPct": "0.5", "InpTakeProfitPct": "3.75", "InpCloseOnOpposite": "false",
        "InpBreakEvenPct": "0.5", "InpBreakEvenLockPct": "0.1",
        "InpTrailStartPct": "1.0", "InpTrailDistPct": "1.0", "InpTrailATRMult": "4.0",
    }),
    # MeanRev_EA v1.00 compiled defaults (pre-tuning; Task 8 replaces the strategy values with the winner's)
    "MeanRev_EA": dict(_CORE_DEFAULTS, **{
        "InpMagic": "240819", "InpComment": "MREV",
        "InpEntryMode": "0", "InpATRPeriod": "14", "InpSLATR": "1.5", "InpMaxBars": "12",
        "InpBBPeriod": "20", "InpBBDev": "2.0", "InpRSIPeriod": "14", "InpRSILow": "30",
        "InpADXPeriod": "14", "InpADXMax": "20",
        "InpRSI2Period": "2", "InpRSI2Entry": "10", "InpRSI2Exit": "70", "InpTrendPeriod": "200",
    }),
    # VWAP_RSI_EA v1.00 compiled defaults. Always pass InpTimeframe with --period (5 / 15 / 16385 / 16388).
    # InpSpreadWaitMin=0: the stop is a bar level; Core_RetryPending would re-apply the stored distance to a later price.
    "VWAP_RSI_EA": dict(_CORE_DEFAULTS, **{
        "InpMagic": "261006", "InpComment": "VWAP_RSI", "InpSpreadWaitMin": "0",
        "InpAnchor": "0", "InpRSIPeriod": "21", "InpRR": "1.0", "InpSLBufferATR": "0.0", "InpMinStopATR": "0.1",
        "InpReentryMode": "0",
    }),
    # HoLo_EA v1.00 compiled defaults (MichaelG's HoLo, NY session only). InpTimeframe = trigger timeframe.
    "HoLo_EA": dict(_CORE_DEFAULTS, **{
        "InpMagic": "261007", "InpComment": "HOLO", "InpTimeframe": "15", "InpSpreadWaitMin": "0",
        "InpLevelTF": "16385", "InpRR": "1.0", "InpBETriggerPct": "0.067", "InpBELockPct": "0.013",
        "InpMaxLatePct": "0.01", "InpMinStopPct": "0.03", "InpBreakoutFilter": "true",
        "InpSessionStartNY": "480", "InpSessionEndNY": "1015", "InpServerNYOffset": "7",
    }),
    # RSI_Reversal_EA v1.10 compiled defaults (standalone, no EACore: no trade-log CSV)
    "RSI_Reversal_EA": {
        "InpMagic": "261004", "InpComment": "RSI_REV", "InpSlippage": "30",
        "InpRsiTimeframe": "16390", "InpRsiPeriod": "4", "InpRsiBuyLevel": "40.0", "InpRsiSellLevel": "55.0",
        "InpUseMaFilter": "true", "InpMaTimeframe": "16396", "InpMaPeriod": "250", "InpMaMethod": "0",
        "InpStopLossPct": "1.0", "InpTakeProfitPct": "5.0",
        "InpTrailTriggerPct": "1.0", "InpTrailDistancePct": "1.0", "InpTrailStepPct": "0.05",
        "InpRiskMode": "0", "InpRiskValue": "1.0", "InpLotsWithoutSL": "0.01",
    },
    # ATR Candle Breakout EA (third-party .ex5, no source): defaults as saved by MT5 on 2026-10-01.
    # Risk is a fixed money amount per trade (InpRiskAmount); no CSV trade log, magic input is InpMagicNumber.
    "ATR Candle Breakout EA": {
        "InpTimeframe": "16385", "InpATRPeriod": "200", "InpATRMultiplier": "2.5", "InpCloseProximity": "25.0",
        "InpMinBodyRatio": "0.0",
        "InpUseTrendFilter": "false", "InpTrendTF": "16408", "InpTrendMAPeriod": "200", "InpTrendMAMethod": "1",
        "InpUseMTFATR": "false", "InpHTFTimeframe": "16388", "InpHTFATRPeriod": "14", "InpHTFATRMultiplier": "1.0",
        "InpUseTimeFilter": "false", "InpStartHour": "8", "InpEndHour": "20", "InpSkipFriday": "true",
        "InpSkipMonday": "false",
        "InpUseSRFilter": "false", "InpSRTimeframe": "16408", "InpSRLookback": "50", "InpSRZoneATRMult": "0.5",
        "InpSRMinTouches": "2",
        "InpSLPercent": "0.5", "InpTPPercent": "2.0", "InpRiskAmount": "100.0",
        "InpUseTrailing": "false", "InpTrailStartPct": "0.5", "InpTrailStepPct": "0.3",
        "InpMagicNumber": "14", "InpSlippage": "100",
    },
}


def expert_path(expert):
    if expert not in PROFILES:
        raise ValueError(f"unknown expert {expert!r}; known: {sorted(PROFILES)}")
    return f"Advisors\\{expert}.ex5"


def merge_inputs(expert, sets):
    expert_path(expert)
    base = dict(PROFILES[expert])
    unknown = sorted(k for k in sets if k not in base)
    if unknown:
        raise ValueError(f"unknown input(s) for {expert}: {', '.join(unknown)}")
    base.update(sets)
    return base


def parse_range(spec):
    try:
        name, value = spec.split("=", 1)
        start, step, stop = value.split(":")
        ok = float(step) > 0 and float(stop) >= float(start)
    except ValueError:
        ok = False
    if not ok:
        raise ValueError(f"refusing range {spec!r}: numeric start:step:stop with step > 0 only; "
                         f"sweep bools/enums as separate launches")
    return name.strip(), (start, step, stop)


def tester_input_lines(inputs, ranges):
    unknown = sorted(k for k in ranges if k not in inputs)
    if unknown:
        raise ValueError(f"range for unknown input(s): {', '.join(unknown)}")
    lines = []
    for k, v in inputs.items():
        if k in ranges:
            a, s, b = ranges[k]
            lines.append(f"{k}={v}||{a}||{s}||{b}||Y")
        elif k in STRING_INPUTS:
            lines.append(f"{k}={v}")
        else:
            lines.append(f"{k}={v}||{v}||1||{v}||N")
    return lines


def tester_log_name(expert, symbol, magic):
    return f"{expert}_{symbol}_{magic}_tester_trades.csv"


def trade_log_name_for(expert, symbol, inputs):
    """Tester trade-log file name for EAs built on EACore / v1.42 (they have InpMagic); None otherwise."""
    if "InpMagic" not in inputs:
        return None
    return tester_log_name(expert, symbol, inputs["InpMagic"])


def pick_fresh_log(path, started_at):
    if os.path.isfile(path) and os.path.getmtime(path) >= started_at:
        return path
    return None
