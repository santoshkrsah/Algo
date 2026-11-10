"""NIFTY 1-hour Supertrend + 50 MA trend following (Big Bull Series Ep 94): every parameter in ONE dict + assumptions.

Data (Fyers, ../Algo_Historical_Data/data/bars via python -m fyers_data.download_bars): NIFTY 50 spot 1m, NIFTY near / next
futures 1m (own continuous series, roll 3 trading days before expiry, back-adjusted so roll gaps are never P&L), India
VIX daily, Bank Nifty spot 1m, MCX gold futures. Shared data layer: backtest.podcast.data (continuous futures, expiries).
"""
from copy import deepcopy

TITLE_SUFFIX = "This Profitable F&O Trader Takes Only 35 Trades a Year | Big Bull Series Ep 94"

CONFIG = {
    "data": {"start": "2018-02-01", "end": "2026-11-09"},           # Fyers 1m futures from Feb 2018; Fyers had no 10 Nov candles yet
    # ---------------------------------------------------------------------------------------------- Module A grid
    "grid": {"atr_n": [7, 10, 14], "mult": [2.0, 3.0, 4.0], "ma_type": ["SMA", "EMA"], "ma_len": [40, 50, 60]},
    "main": {"atr_n": 10, "mult": 3.0, "ma_type": "SMA", "ma_len": 50},   # only used to label "speaker-like" rows
    # signal variants: L1 = spot incl. the 15:15-15:30 part candle, L2 = spot without it, L3 = futures chart (incl.)
    "last_candle": ["L1", "L2", "L3"],
    "exit_mode": ["close", "touch"],                                 # close beyond Supertrend / intrabar touch
    "sides": ["long", "longshort"],                                  # long-only / long + short (shorts at 50% size)
    "short_size": 0.5,
    "instrument": ["F", "S"],                                        # F = futures, S = synthetic (buy ATM CE + sell ATM PE)
    "slippage_pts": [1.0, 2.0, 5.0],                                 # per side, every fill (entry, exit, both legs of a roll)
    "slippage_main": 2.0,
    # ---------------------------------------------------------------------------------------------- costs (per order)
    # rates_mode 'current' = today's rates on every trade (the speaker's example); 'dated' = the rate in force that day
    "rates_mode": "current",
    "costs": {
        "brokerage": 20.0,                      # Rs per order
        "fut": {"exch_pct": 0.00173, "sebi_pct": 0.0001, "stamp_buy_pct": 0.002, "gst_pct": 18.0,
                "stt_sell_pct": [("2000-01-01", 0.01), ("2023-04-01", 0.0125), ("2024-10-01", 0.02), ("2026-04-01", 0.05)]},
        "opt": {"exch_pct": 0.03503, "sebi_pct": 0.0001, "stamp_buy_pct": 0.003, "gst_pct": 18.0,
                "stt_sell_pct": [("2000-01-01", 0.05), ("2023-04-01", 0.0625), ("2024-10-01", 0.1), ("2026-04-01", 0.15)]},
        "synthetic_days": 30,                   # synthetic legs: ATM options ~30 days to expiry (premium for charges)
    },
    # NIFTY near-month futures lot size from this date. 2024-04-26 on: NSE bhavcopy (lot column from Jul 2024, turnover
    # estimate before: 50 Oct 2021 - Apr 2024, 25 from May 2024); 75 -> 50 in Jul 2021 from the NSE circular (no local data)
    "lot_sizes": [("2015-10-29", 75), ("2021-07-30", 50), ("2024-04-26", 25), ("2025-01-31", 75), ("2025-12-31", 65)],
    # ---------------------------------------------------------------------------------------------- money
    "capital": 750_000,                         # Rs 7.5 lakh (speaker: ~Rs 7.5 lakh per lot, ~2x leverage)
    "leverage": [1.0, 1.5, 2.0, 3.0],
    "leverage_main": 2.0,
    "dd_rule": {"steps": [(5.0, 0.5), (10.0, 0.25)]},               # DD > 5% -> half size, > 10% -> quarter; new high -> full
    # ---------------------------------------------------------------------------------------------- evaluation
    "is_frac": 0.60,
    "random_sims": 1000,
    "seed": 11,
    "min_trades_warn": 100,
    "min_is_trades": 40,
    "stress": [("COVID crash", "2020-02-20", "2020-04-30"), ("2022 sell-off (war, rate hikes)", "2022-01-01", "2022-06-30"),
               ("Election result week", "2024-06-03", "2024-06-07"), ("Sep 2024 - Mar 2025 correction", "2024-09-27", "2025-03-31"),
               ("Apr 2025 tariff shock", "2025-04-01", "2025-04-15")],
    "claims": {"trades_per_year": (30, 35), "breakeven_pts_year": 370, "best_trades": (800, 1400)},
    "workers": 8,
}

ASSUMPTIONS = [
    "Signal only after the 1-hour candle closes; every order fills at the NEXT 1-minute bar's open of NIFTY futures, plus slippage (2 points per side main; 1 and 5 tested).",
    "1-hour candles are built from 1-minute data, 09:15-anchored: 09:15, 10:15, 11:15, 12:15, 13:15, 14:15 and the part candle 15:15-15:30.",
    "Futures: own continuous near-month series (Fyers near + next contracts), next contract used for the last 3 trading days before expiry; back-adjusted, so a roll gap is never P&L. A position held over a roll pays 2 extra orders + 2 slippages.",
    "Touch exit: the stop = Supertrend line known at the previous candle's close; the first 1-minute candle (of the signal chart) that touches it triggers a market order at the next 1-minute futures open.",
    "Synthetic futures (S): same points as the futures trade; charges of 4 option orders per round trip (buy ATM call + sell ATM put, and back), premium from Black-Scholes with India VIX (approximation, labelled).",
    "Charges built from rates: Rs 20 brokerage per order, STT on the sell side (futures 0.05%, options 0.15% of premium: today's rates on every trade in the main run), NSE 0.00173% (futures) / 0.03503% (options), SEBI Rs 10 per crore, stamp 0.002% / 0.003% on buys, GST 18% on brokerage + exchange + SEBI. A dated-rates run is also shown.",
    "NIFTY lot size by date: 75 until Jul 2021 (NSE circular, not checkable in local data), 50 until 25 Apr 2024, 25 until 30 Jan 2025, 75 until 30 Dec 2025, 65 after - from the NSE bhavcopy (lot column; turnover estimate before Jul 2024).",
    "Equity in Rs: lots = floor(equity x leverage / (futures price x lot size)) at each entry (compounding); 0 lots = trade skipped and counted. Drawdown rule: DD > 5% half size, > 10% quarter size, back to full on a new equity high.",
    "No dividends, interest on idle cash or margin interest. Holidays / special sessions come from the data itself (days without a full session are dropped).",
]


def cfg(**over) -> dict:
    c = deepcopy(CONFIG)
    c.update(over)
    return c
