"""Every parameter of the podcast strategy framework in ONE dict (CONFIG), plus the assumptions behind them.

Modules (each runs on its own, tools/run_podcast.py --module A|B|C|D):
    A  opening price vs previous close (intraday, 1-minute exits)
    B  moving-average crossover (positional, daily and 1-hour)
    C  Dow-theory swing breakout (intraday, 3- and 5-minute)
    D  previous-session S/R level breakout (signal on index futures; options not testable -> underlying only, index points)

Change a number here, nowhere else. Every report prints the settings it used (settings strip + Rules tab).
"""
from copy import deepcopy

from backtest.config import DATA

STOCKS = ["RELIANCE", "HDFCBANK", "ICICIBANK", "INFY", "TCS", "SBIN", "ITC", "LT", "AXISBANK", "BHARTIARTL"]
INDEX_FUT = ["NIFTY", "BANKNIFTY"]
SPOT_OF = {"NIFTY": "NIFTY50", "BANKNIFTY": "BANKNIFTY"}        # index futures -> their index (expiry detection, checks)

CONFIG = {
    # ----------------------------------------------------------------------------------------------------- data
    "data": {
        "bars_dir": DATA / "bars",              # written by Algo_Historical_Data: python -m fyers_data.download_bars
        "start": "2018-01-01",                  # main test window: Fyers intraday + futures history starts here
        "end": "2026-11-09",                    # last complete day downloaded
        "long_start": "2005-01-01",             # long-history daily checks (index + stock cash daily from 2005)
        "roll_days": 3,                         # futures: next contract used for the last 3 trading days of the old one
        "session": (555, 929),                  # 09:15 .. 15:29 candle starts (minute of day); others dropped
        "min_day_bars": 300,                    # 1m bars needed for a normal day (375 max); shorter days = special session
        "corp_fixes": {"ITC": "2025-01-06",     # demerger days Fyers did not adjust (ITC Hotels; Jio Financial):
                       "RELIANCE": "2023-07-20"},   # older prices x (open that day / previous close), cash and futures
    },
    # ------------------------------------------------------------------------------------------ instruments
    # fut  = continuous futures built from <ROOT>_F1 + <ROOT>_F2 (back-adjusted, roll costs charged)
    # cash = stock cash series (intraday MIS for modules A / C)
    "slippage": {                               # per side, every fill (entry, stop, target, time exit, roll)
        "NIFTY": 1.5,                           # index points (5-min futures: 1-2 points)
        "BANKNIFTY": 3.0,                       # index points (price ~2.3x NIFTY, wider spread)
        "stock_pct": 0.03,                      # % of price, stock cash and stock futures
    },
    "tick": 0.05,
    # ------------------------------------------------------------------------------------------- money + risk
    "capital": 1_000_000,                       # Rs 10 lakh
    "compounding": False,                       # risk always on the starting capital (fixed rupee risk)
    "risk_pct": 0.25,                           # % of capital lost if the stop is hit (main test)
    "risk_pct_alt": 0.50,                       # second test
    "max_notional_x": 5.0,                      # one position <= 5 x capital (futures margin / MIS leverage)
    "daily_loss_cap_pct": 0.50,                 # realised loss of the day <= -0.5% of capital -> no new trades that day
    "dd_kill_pct": 5.0,                         # equity 5% below its peak -> stop new trades ...
    "dd_restart_days": 20,                      # ... for 20 trading days, then restart (peak reset to equity then)
    # ------------------------------------------------------------------------------------------------ costs
    "costs": {
        "fut": {"brokerage": 20.0, "exch_pct": 0.00173, "sebi_pct": 0.0001, "stamp_buy_pct": 0.002, "gst_pct": 18.0,
                "stt_sell_pct": [("2000-01-01", 0.01), ("2023-04-01", 0.0125), ("2024-10-01", 0.02), ("2026-04-01", 0.05)]},
        "cash_intraday": {"brokerage_pct": 0.03, "brokerage_cap": 20.0, "exch_pct": 0.00297, "sebi_pct": 0.0001,
                          "stamp_buy_pct": 0.003, "gst_pct": 18.0, "stt_sell_pct": [("2000-01-01", 0.025)]},
        "opt": {"brokerage": 20.0, "exch_pct": 0.03503, "sebi_pct": 0.0001, "stamp_buy_pct": 0.003, "gst_pct": 18.0,
                "stt_sell_pct": [("2000-01-01", 0.05), ("2023-04-01", 0.0625), ("2024-10-01", 0.1), ("2026-04-01", 0.15)]},
    },
    # ------------------------------------------------------------------------------------------ evaluation
    "is_frac": 0.60,                            # first 60% of the test window = in-sample (parameters chosen here)
    "random_sims": 1000,
    "seed": 7,
    "min_trades_warn": 200,
    "min_is_trades": 60,                        # a grid cell needs this many in-sample trades to be chosen
    "eod_exit": 915,                            # 15:15 (minute of day): intraday positions closed at this bar's open
    "big_loss_year_x": 2.0,                     # loss year flagged when |loss| > 2 x median |yearly net|
    "workers": 10,
    # -------------------------------------------------------------------------------------------- modules
    "modules": {
        "A": {
            "instruments": INDEX_FUT + STOCKS,
            "tf": "1m",                         # exits checked on 1-minute bars (entry = 09:15 open)
            "pairs": [(1.0, 2.0), (2.0, 4.0), (0.5, 1.0)],                  # (stop %, target %) from the podcast
            "grid_stop": [0.4, 0.5, 0.6, 0.7, 0.8, 0.9, 1.0, 1.1, 1.2, 1.4, 1.6, 1.8, 2.0, 2.2],
            "grid_target": [0.8, 1.0, 1.2, 1.4, 1.6, 1.8, 2.0, 2.2, 2.4, 2.8, 3.2, 3.6, 4.0, 4.4],
            "atr_n": 14, "std_n": 20,           # ATR / stdev of daily returns, previous days only
            "grid_k_stop": [0.3, 0.4, 0.5, 0.6, 0.75, 0.9, 1.0, 1.25, 1.5],
            "grid_k_target": [0.5, 0.75, 1.0, 1.25, 1.5, 2.0, 2.5, 3.0],
            "min_gap_pct": 0.0,                 # open must differ from previous close by more than this
        },
        "B": {
            "instruments": INDEX_FUT + STOCKS,  # stocks traded as stock futures (shorts are allowed only in futures)
            "tfs": ["1D", "60m"],
            "ma_types": ["EMA", "SMA"],
            "grid_fast": [10, 15, 20, 25, 30],
            "grid_slow": [40, 45, 50, 55, 60],
            "pairs": [(20, 50), (20, 60), (10, 50)],                        # from the podcast
            "versions": [1, 2],                 # 1 = pure cross, 2 = state based (re-entry after stop / target)
            "exits": ["sltp", "cross", "sltp_eod"],    # sl/tp 1%/2%; opposite cross (stop and reverse); sl/tp + 15:15 exit
            "stop_pct": 1.0, "target_pct": 2.0,
            "grid_stop": [0.6, 0.8, 0.9, 1.0, 1.1, 1.2, 1.5, 2.0],
            "grid_target": [1.2, 1.6, 1.8, 2.0, 2.2, 2.4, 3.0, 4.0],
            "cross_risk_pct": 1.0,              # opposite-cross exits have no stop: size as if the stop were 1%
        },
        "C": {
            "instruments": INDEX_FUT + STOCKS,
            "tfs": ["3m", "5m"],
            "grid_n": [2, 3, 4, 5],
            "entries": ["touch", "close"],
            "exits": ["thirds", "trail", "2r"],
            "grid_attempts": [1, 2, 3],
            "grid_max_stop": [0.3, 0.4, 0.5, 0.6, 0.7],   # % of price; wider swing stop -> skip the trade
            "event_filter": [False, True],
            "gap_pct": 1.5, "range_x": 2.0, "range_n": 20,
            "last_entry": 900,                  # no new entries from 15:00
        },
        "D": {
            "instruments": INDEX_FUT,
            "tfs": ["5m", "3m"],
            "levels": ["pivot", "pdhl"],        # classic pivot R1/S1, previous-day high / low
            "stop_modes": ["level", "close_back"],      # hard stop beyond the level / exit when a candle closes back inside
            "grid_buffer": [0.05, 0.1, 0.15, 0.2, 0.3],   # % of price beyond the level for the hard stop
            "grid_rr": [1.0, 1.5, 2.0, 2.5, 3.0],
            "max_trades_day": 2,
            "last_entry": 900,
        },
    },
}

ASSUMPTIONS = [
    "No look-ahead: signals use only data up to the close of the current bar; orders fill at the next bar's open, or at the stop-entry level (+1 tick) for Module C 'touch' entries (at the open if the bar gaps through the level).",
    "Stop and target in the same bar -> the stop counts first. A bar that opens beyond the stop fills at its open (worse price). Targets fill at the target price (never better).",
    "Slippage is added on every fill (entry, stop, target, time exit, roll): NIFTY 1.5 points, BANKNIFTY 3 points, stocks 0.03% of price per side.",
    "Index and stock futures: own continuous series from Fyers' near (F1) and next (F2) contracts; the next contract is used for the last 3 trading days of the old one. Prices are back-adjusted (difference), so a roll gap is never P&L; a position held over a roll pays two extra orders (exit old + enter new) with slippage and charges.",
    "Expiry days are found from the data (the day after which Fyers' near series jumps to the next contract), checked against the NSE calendar rule (last Thursday; last Tuesday from Sep 2025).",
    "Sizing: quantity = risk Rs / (entry - stop) in units (index units or shares), not whole lots, so every trade risks exactly 0.25% (or 0.5%) of Rs 10 lakh. One position is capped at 5x capital. Real NIFTY lots (65-75 units) need more capital; results in R do not depend on this.",
    "No compounding: risk is always on the starting capital. Return % = net / starting capital.",
    "Daily loss cap uses realised P&L of the day (closed trades and partial exits). The 5% kill switch uses realised equity; after it fires, no new trades for 20 trading days, then trading restarts with the peak reset.",
    "Stocks: intraday modules (A, C) trade cash (MIS, intraday charges). Positional Module B trades stock futures (shorts are not possible in cash delivery).",
    "Costs per order (verify with your broker): futures Rs 20 brokerage, STT sell 0.01% / 0.0125% (Apr 2023) / 0.02% (Oct 2024) / 0.05% (Apr 2026), NSE 0.00173%, SEBI 0.0001%, stamp buy 0.002%, GST 18% on brokerage + exchange + SEBI. Stock intraday: brokerage min(Rs 20, 0.03%), STT sell 0.025%, NSE 0.00297%, stamp buy 0.003%, SEBI, GST.",
    "Module D: Fyers does not serve expired option contracts and intraday option history; premium-based stops (25/35/50%) and the Rs 80 strike cannot be tested. Module D runs on the futures only and reports index points.",
    "Fyers cash prices are adjusted for splits and bonuses, demergers are not: ITC (ITC Hotels, 6 Jan 2025) and RELIANCE (Jio Financial, 20 Jul 2023) prices before those days are scaled by (open / previous close) of that day, in cash and futures.",
    "Fyers stock futures were not adjusted for HDFCBANK's 2019 split and TCS's 2018 bonus (futures = 2x cash before them): those futures prices are divided by the whole-number ratio to cash.",
    "Stock list = 10 large caps chosen today (survivorship bias: they are big because they did well).",
]


def cfg(**over) -> dict:
    """A deep copy of CONFIG with top-level keys replaced (tests, stress runs)."""
    c = deepcopy(CONFIG)
    c.update(over)
    return c
