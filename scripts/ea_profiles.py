"""Per-EA tester input defaults and [TesterInputs] line building.

Pure module (no MT5 / Wine imports) so it is importable by system python and tests.
Every EA input is written explicitly: MT5 silently applies the last GUI .set for any input left out,
and a bare "k=v" keeps an optimize flag cached from an earlier run, so fixed inputs are pinned as
v||v||1||v||N. Unknown input names are refused instead of silently ignored.
"""
import os

BLESSING_STRINGS = {"AlertSound", "DDorLevel", "GridSetArray", "HedgeSymbol", "Holidays", "LabelAcc", "LabelAdv", "LabelBBS", "LabelCCI", "LabelDisplay", "LabelEDD", "LabelEE", "LabelEH", "LabelES", "LabelEST", "LabelEST0", "LabelGS", "LabelGrid", "LabelGridOpt", "LabelHS", "LabelHS0", "LabelIchi", "LabelLS", "LabelMA", "LabelMACD", "LabelOS", "LabelOS0", "LabelOpt", "LabelSG", "LabelSto", "LabelTS", "LabelTST", "LabelUE", "Notes", "SetCountArray", "TP_SetArray", "TradeComment", "Version_3_9_6_23", "displayFont", "eopb", "eopb1", "s1", "s2", "s3"}
STRING_INPUTS = {"InpComment"} | BLESSING_STRINGS

_CORE_DEFAULTS = {
    "InpTimeframe": "16385", "InpSlippagePoints": "30", "InpMaxSpreadPoints": "150",
    "InpRiskMode": "1", "InpRiskValue": "1.0",
    "InpSpreadWaitMin": "30", "InpDailyLossPct": "4.0", "InpMonthlyLossPct": "12.0", "InpTradeLog": "true",
    "InpLogEveryBar": "false",   # hourly status lines are for live/VPS, too noisy for tester runs
}

PROFILES = {
    # MACD_Cross_EA v1.43 compiled defaults (break-even steps 2-3 off = v1.42 behaviour)
    "MACD_Cross_EA": dict(_CORE_DEFAULTS, **{
        "InpMagic": "240817", "InpComment": "MACD_X",
        "InpFastEMA": "16", "InpSlowEMA": "26", "InpSignalSMA": "9", "InpAppliedPrice": "1",
        "InpZeroLineFilter": "false",
        "InpUseTrendFilter": "true", "InpTrendTF": "16388", "InpTrendPeriod": "200",
        "InpMinGapATR": "0.0", "InpATRPeriod": "14",
        "InpUseTimeFilter": "false", "InpStartHour": "7", "InpEndHour": "20",
        "InpStopLossPct": "0.5", "InpTakeProfitPct": "3.75", "InpCloseOnOpposite": "false",
        "InpBreakEvenPct": "0.5", "InpBreakEvenLockPct": "0.1",
        "InpBreakEven2Pct": "0.0", "InpBreakEven2LockPct": "0.0", "InpBreakEven3Pct": "0.0", "InpBreakEven3LockPct": "0.0",
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
        "InpMagic": "261007", "InpComment": "HOLO", "InpTimeframe": "15", "InpSpreadWaitMin": "0", "InpEntryMode": "0",
        "InpLevelTF": "16385", "InpRR": "1.0", "InpBETriggerPct": "0.067", "InpBELockPct": "0.013",
        "InpMaxLatePct": "0.01", "InpMinStopPct": "0.03", "InpBreakoutFilter": "true",
        "InpSessionStartNY": "480", "InpSessionEndNY": "1015", "InpServerNYOffset": "7",
        "InpExcursionLog": "false",
    }),
    # EuroRiddle_EA v1.00 compiled defaults (docs/superpowers/specs/2026-10-06-euroriddle-prereg.md). EURUSD:
    # 1 pip = 10 points, so the spread guard is 20 points = 2 pips. InpTimeframe (H1) is the filter timeframe.
    "EuroRiddle_EA": dict(_CORE_DEFAULTS, **{
        "InpMagic": "261008", "InpComment": "EURID", "InpSpreadWaitMin": "0", "InpMaxSpreadPoints": "20",
        "InpMode": "0", "InpFilter": "0", "InpStopPips": "15", "InpTargetPips": "25",
        "InpADRPeriod": "20", "InpADRFrac": "1.0", "InpLateStartNY": "720", "InpRSIPeriod": "14", "InpRSILevel": "70",
        "InpServerNYOffset": "7",
    }),
    # Icarus_EA v1.00 compiled defaults = the Icarus 2.2 MQ4 defaults (docs/superpowers/specs/2026-10-06-icarus-prereg.md).
    # Grid EA without stop loss: EACore's daily/monthly stops are compiled to 0 (they would close legs the grid reopens).
    # InpMaxSpreadPoints 100 = max_spread 100 MT4 points. Progressions: 0 flat, 1 D'Alembert, 2 Martingale, 3 Fibonacci.
    "Icarus_EA": dict(_CORE_DEFAULTS, **{
        "InpMagic": "261009", "InpComment": "ICARUS", "InpSpreadWaitMin": "0", "InpMaxSpreadPoints": "100",
        "InpDailyLossPct": "0", "InpMonthlyLossPct": "0",
        "InpGridPips": "20", "InpGridProgression": "3", "InpTakeProfitPips": "20", "InpProfitLock": "0.3",
        "InpMinLots": "0.01", "InpEquityWarning": "0.20", "InpAccountRisk": "1.00", "InpLotProgression": "3",
        "InpMaxPositions": "6", "InpUnbalanceControl": "false",
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
    # Blessing_3 v3.9.6.23 (third-party MQL5 port of the MT4 Blessing 3 grid EA, compile fixes 2026-10-06; standalone, no
    # EACore, no trade CSV: trades come from the report's Deals table). All 190 compiled defaults, generated from
    # mql5/Blessing_3.mq5 with walkforward.parse_inputs; PortionChange "increase" = 1 and the four display colours
    # (BGR integers) were resolved by hand. Enums: tFrame 0 current/3 M15/5 H1; entType 0 off/1 on/2 reverse;
    # mktCond 3 automatic. Strings (labels, arrays, comment) are written bare, see BLESSING_STRINGS.
    "Blessing_3": {
        "Version_3_9_6_23": "EA Settings:",
        "TradeComment": "b3_v396.23",
        "Notes": "",
        "EANumber": "1",
        "EmergencyCloseAll": "false",
        "s1": "",
        "ShutDown": "false",
        "s2": "",
        "LabelAcc": "",
        "StopTradePercent": "10.0",
        "NanoAccount": "false",
        "s3": "... PortionPC > 100 forces effective balance to that amount (e.g. 1000)",
        "PortionPC": "100.0",
        "PortionChange": "1",
        "MaxDDPercent": "50.0",
        "MaxSpread": "5.0",
        "UseHolidayShutdown": "true",
        "Holidays": "18/12-01/01",
        "PlaySounds": "false",
        "AlertSound": "Alert.wav",
        "eopb": "",
        "EnableOncePerBar": "true",
        "UseMinMarginPercent": "false",
        "MinMarginPercent": "1500.0",
        "eopb1": "",
        "LabelTST": "",
        "TesterMinMarginPercent": "500.0",
        "TesterSelection": "1",
        "B3Traditional": "true",
        "ForceMarketCond": "3",
        "UseAnyEntry": "false",
        "LabelLS": "",
        "UseMM": "true",
        "LAF": "0.5",
        "Lot": "0.01",
        "Multiplier": "1.4",
        "LabelGS": "",
        "AutoCal": "false",
        "ATRTF": "0",
        "ATRPeriods": "21",
        "GAF": "1.0",
        "EntryDelay": "2400",
        "EntryOffset": "5.0",
        "UseSmartGrid": "true",
        "LabelTS": "",
        "MaxTrades": "15",
        "BreakEvenTrade": "12",
        "BEPlusPips": "2.0",
        "UseCloseOldest": "false",
        "CloseTradesLevel": "5",
        "ForceCloseOldest": "true",
        "MaxCloseTrades": "4",
        "CloseTPPips": "10.0",
        "ForceTPPips": "0.0",
        "MinTPPips": "0.0",
        "LabelES": "",
        "MaximizeProfit": "false",
        "ProfitSet": "70.0",
        "MoveTP": "30.0",
        "TotalMoves": "2",
        "UseStopLoss": "false",
        "SLPips": "30.0",
        "TSLPips": "10.0",
        "TSLPipsMin": "3.0",
        "UsePowerOutSL": "false",
        "POSLPips": "600.0",
        "UseFIFO": "false",
        "LabelEE": "",
        "UseEarlyExit": "false",
        "EEStartHours": "3.0",
        "EEFirstTrade": "true",
        "EEHoursPC": "0.5",
        "EEStartLevel": "5",
        "EELevelPC": "10.0",
        "EEAllowLoss": "false",
        "LabelAdv": "",
        "LabelGrid": "",
        "SetCountArray": "4,4",
        "GridSetArray": "25,50,100",
        "TP_SetArray": "50,100,200",
        "LabelEST0": "",
        "LabelEST": "",
        "LabelMA": "",
        "MAEntry": "1",
        "MA_TF": "0",
        "MAPeriod": "100",
        "MADistance": "10.0",
        "LabelCCI": "",
        "CCIEntry": "0",
        "CCIPeriod": "14",
        "LabelBBS": "",
        "BollingerEntry": "0",
        "BollPeriod": "10",
        "BollDistance": "10.0",
        "BollDeviation": "2.0",
        "LabelIchi": "",
        "IchimokuEntry": "0",
        "ICHI_TF": "0",
        "Tenkan_Sen": "9",
        "Kijun_Sen": "26",
        "Senkou_Span": "52",
        "useCloudBreakOut": "true",
        "useTenken_Kijun_cross": "2",
        "usePriceCrossTenken": "false",
        "usePriceCrossKijun": "false",
        "useChikuspan": "false",
        "useCloudSL": "false",
        "LabelSto": "",
        "StochEntry": "0",
        "BuySellStochZone": "20",
        "KPeriod": "10",
        "DPeriod": "2",
        "Slowing": "2",
        "LabelMACD": "",
        "MACDEntry": "0",
        "MACD_TF": "0",
        "FastPeriod": "12",
        "SlowPeriod": "26",
        "SignalPeriod": "9",
        "MACDPrice": "0",
        "LabelSG": "",
        "RSI_TF": "3",
        "RSI_Period": "14",
        "RSI_Price": "0",
        "RSI_MA_Period": "10",
        "RSI_MA_Method": "0",
        "LabelHS0": "",
        "LabelHS": "",
        "HedgeSymbol": "",
        "CorrPeriod": "30",
        "UseHedge": "false",
        "DDorLevel": "DD",
        "HedgeStart": "20.0",
        "hLotMult": "0.8",
        "hMaxLossPips": "30.0",
        "hFixedSL": "false",
        "hTakeProfit": "30.0",
        "hReEntryPC": "5.0",
        "StopTrailAtBE": "true",
        "ReduceTrailStop": "true",
        "LabelOS0": "",
        "LabelOS": "",
        "RecoupClosedLoss": "true",
        "Level": "7",
        "SaveStats": "false",
        "StatsPeriod": "3600",
        "StatsInitialise": "true",
        "LabelUE": "",
        "UseEmail": "false",
        "LabelEDD": "At what DD% would you like Email warnings (Max: 49, Disable: 0)?",
        "EmailDD1": "20.0",
        "EmailDD2": "30.0",
        "EmailDD3": "40.0",
        "LabelEH": "Hours before DD timer resets",
        "EmailHours": "24.0",
        "LabelDisplay": "",
        "displayOverlay": "true",
        "displayLogo": "true",
        "displayCCI": "true",
        "displayLines": "true",
        "displayXcord": "100",
        "displayYcord": "30",
        "displayCCIxCord": "10",
        "displayFont": "Arial Bold",
        "displayFontSize": "9",
        "displaySpacing": "14",
        "displayRatio": "1.3",
        "displayColor": "16760576",
        "displayColorProfit": "32768",
        "displayColorLoss": "255",
        "displayColorFGnd": "0",
        "HideIndicators": "true",
        "Debug": "false",
        "LabelGridOpt": "",
        "LabelOpt": "These values can only be used while optimizing",
        "UseGridOpt": "false",
        "SetArray1": "4",
        "SetArray2": "4",
        "SetArray3": "0",
        "SetArray4": "0",
        "GridArray1": "25",
        "GridArray2": "50",
        "GridArray3": "100",
        "GridArray4": "0",
        "GridArray5": "0",
        "TPArray1": "50",
        "TPArray2": "100",
        "TPArray3": "200",
        "TPArray4": "0",
        "TPArray5": "0",
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
