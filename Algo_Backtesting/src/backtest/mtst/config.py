"""NIFTY multi-timeframe Supertrend (daily trend filter + 1-hour entries / exits): EVERY parameter in ONE dict + assumptions.

Data (Fyers v3, ../Algo_Historical_Data/data/bars, python -m fyers_data.download_bars --only NIFTY50 NIFTY_F1 NIFTY_F2 INDIAVIX):
NIFTY 50 spot 1m (Dec 2017 ->), NIFTY near / next futures 1m (Feb 2018 ->, own continuous series), NIFTY daily (2005 ->),
India VIX daily (2008 ->). Shared data layer: backtest.podcast.data (continuous futures, expiries, session filter).
Change a number here, nowhere else; every report prints the settings it used.
"""
from copy import deepcopy

TITLE_SUFFIX = "This 1 Strategy Made Me Profitable"
NAME = "mtst_nifty_supertrend"

BASE = {                      # the speaker's setup = the main run (pre-specified, NOT optimised)
    "chart": "spot",          # signal chart: spot (NIFTY 50 index) | fut (continuous near-month futures)
    "atr": "rma",             # rma = Wilder ATR (TradingView ta.atr / ta.supertrend) | sma = simple average of true range
    "h_n": 7, "h_mult": 3.0,  # 1-hour Supertrend
    "d_n": 7, "d_mult": 3.0,  # daily Supertrend
    "entry": "E1",            # E1 = 1h direction == daily direction just became true (1h flip OR daily flip); E2 = only 1h flips
    "fill": "next",           # next = next 1-minute futures open after the candle closes | close = futures close of the signal candle
    "sl": "1h",               # initial stop: 1h Supertrend band at entry | daily = daily Supertrend band at entry
    "exit": "close",          # close = 1h candle closes beyond the band (1h flip) | touch = intrabar touch of the band | close_daily = close + daily flip against
    "last": "include",        # include = 15:15-15:30 part candle gives signals | defer = its signals fill at the next session's first bar | drop = candle not used
    "sides": "both",          # both | long | short | nofilter (control: 1h Supertrend alone, no daily filter, both sides)
}

CONFIG = {
    # ------------------------------------------------------------------------------------------------- data
    "data": {"start": "2018-02-01", "end": "2026-11-13",       # Fyers 1m futures start 1 Feb 2018; last complete day downloaded
             "spot_start": "2017-12-14",                      # spot 1m from here (1-hour indicator warm-up before trading starts)
             "daily_start": "2005-01-01"},                    # NIFTY daily from 2005 (daily Supertrend warm-up, long history)
    "base": BASE,
    # ------------------------------------------------------------------------------------------------- grid (neighbourhood)
    "grid": {"n": [5, 7, 10, 14], "mult": [2.0, 2.5, 3.0, 4.0]},      # for the 1-hour line AND the daily line (16 x 16 = 256)
    "min_is_trades": 60,
    # ------------------------------------------------------------------------------------------------- money
    "capital": 2_500_000,                         # Rs 25 lakh
    "risk_pct": 1.0, "risk_alt": [0.5, 2.0],      # % of sizing capital risked per trade (stop distance x lot x lots)
    "lots_round": "round",                        # round (min 1, as in the video) | floor (0 lots -> trade skipped)
    "min_lots": 1,
    "dd_sizing": {"planned_dd_pct": 10.0},        # on-variant: sizing capital = equity x (1 - 10%); DD > 10% -> half size till new high
    "margin_per_lot": 150_000,                    # Rs per lot (SPAN + exposure, rough); trade skipped if 1 lot > equity
    "margin_cap": True,                           # lots also capped at floor(equity / margin per lot) (cannot hold more)
    # ------------------------------------------------------------------------------------------------- costs
    "slippage": 2.0, "slippage_alt": [1.0, 2.0, 5.0],     # index points per side, every fill (entry, exit, both legs of a roll)
    "rates_mode": "current",                      # current = today's rates on every trade (conservative) | dated = rate in force that day
    "costs": {"brokerage": 20.0,                  # Rs per order (discount broker)
              "exch_pct": 0.00173, "sebi_pct": 0.0001, "stamp_buy_pct": 0.002, "gst_pct": 18.0,
              "stt_sell_pct": [("2000-01-01", 0.01), ("2023-04-01", 0.0125), ("2024-10-01", 0.02), ("2026-04-01", 0.05)]},
    "video_costs": {"notional": 1_500_000, "buy": 85.0, "sell": 788.0},   # earlier video: ~Rs 85 buy / ~Rs 788 sell per lot at ~Rs 15 lakh
    # NIFTY futures lot size from this date (same table as report 25: NSE circular 75 -> 50 Jul 2021; NSE bhavcopy afterwards)
    "lot_sizes": [("2015-10-29", 75), ("2021-07-30", 50), ("2024-04-26", 25), ("2025-01-31", 75), ("2025-12-31", 65)],
    # ------------------------------------------------------------------------------------------------- evaluation
    "is_frac": 0.60,
    "random_sims": 1000, "seed": 23,
    "min_trades_warn": 100,
    "periods": [("2019 - early 2020 (speaker: ~35%)", "2019-01-01", "2020-03-31"), ("COVID crash", "2020-02-17", "2020-04-30"),
                ("2022 sell-off", "2022-01-01", "2022-06-30"), ("Sep 2024 - Mar 2025 correction", "2024-09-27", "2025-03-31"),
                ("July 2026 (speaker: many losing buys)", "2026-07-01", "2026-07-31")],
    "regime": {"flip_lookback": 60, "trend_max_flips": 1, "chop_min_flips": 4, "vix_cuts": [14.0, 20.0]},
    # ------------------------------------------------------------------------------------------------- validation (video)
    "video_trade": {"side": -1, "daily_flip": "2026-09-02", "entry_time": "2026-09-02 15:15", "entry": 23913.0, "stop": 24153.0,
                    "exit_time": "2026-09-21 09:15", "exit": 23365.0, "points": 548.0, "lot": 65, "risk_rs": 15600.0},
}

ASSUMPTIONS = [
    "No look-ahead: a 1-hour signal is known only when its candle closes. The daily Supertrend state used at a 1-hour close is the one of the LAST COMPLETED daily candle (close 15:30): for every candle before 15:15 that is the previous day's; the 15:15-15:30 part candle closes together with the day, so it sees that day's daily candle (this is how the video's 2 Sep 2026 entry works).",
    "Daily Supertrend on the spot chart uses Fyers' downloaded daily NIFTY 50 candles (from 2005, official close); on the futures chart, daily candles built from the back-adjusted 1-minute futures.",
    "1-hour candles are built from 1-minute data, 09:15-anchored: 09:15, 10:15, 11:15, 12:15, 13:15, 14:15 and the part candle 15:15-15:29.",
    "Orders are always NIFTY near-month futures (own continuous series: next contract for the last 3 trading days of the old one, back-adjusted, so a roll gap is never P&L; a position held over a roll pays 2 extra orders + 2 slippages). Points = futures points per 1 unit.",
    "Fill 'next' (main) = next 1-minute futures open after the signal candle closes (for the 15:15 candle: next day 09:15 open, so overnight gaps hit the trade). Fill 'close' = futures close of the signal candle's last minute (needs the order in the last minute; slightly optimistic for the 15:15 candle because the official daily close is fixed at 15:30).",
    "Intrabar 'touch' exit: stop = the 1-hour Supertrend band known at the previous candle's close (the tighter of it and the initial stop). The first 1-minute candle of the signal chart that reaches it exits at the stop, or at that minute's open if it gapped through (worse price); spot levels are moved to futures with that minute's futures-spot basis. If the same candle also closes beyond the band (or another exit fires), the worse of the two prices is used.",
    "Stop distance for sizing = |signal candle close - initial stop| on the signal chart (video: 23,913 - 24,153 = 240 points). Lots = round(1% x sizing capital / (stop distance x lot size)), minimum 1; variant floor (0 lots -> skipped).",
    "Sizing capital = realised equity at entry (profits COMPOUND). Lots are also capped at floor(equity / Rs 1.5 lakh margin per lot); a trade is skipped when even 1 lot's margin is more than the equity.",
    "Charges from rates (verify with your broker): Rs 20 brokerage per order, STT 0.05% on the sell side (today's rate on every trade in the main run; dated rates as a variant), NSE 0.00173%, SEBI 0.0001%, stamp 0.002% on buys, GST 18% on brokerage + exchange + SEBI. Slippage 2 points per side (1 and 5 tested).",
    "NIFTY lot size by date: 75 until Jul 2021, 50 until 25 Apr 2024, 25 until 30 Jan 2025, 75 until 30 Dec 2025, 65 after (NSE circulars / bhavcopy, same table as report 25).",
    "No interest on idle cash, no margin interest, no dividends. Days without a full session (Muhurat, special sessions) are dropped.",
    "A new entry always needs a new alignment event (direction of 'aligned' changes); exit and an opposite entry can happen on the same candle (stop and reverse).",
]


def run_params(**over) -> dict:
    p = dict(BASE)
    p.update(over)
    return p


def cfg(**over) -> dict:
    c = deepcopy(CONFIG)
    c.update(over)
    return c
