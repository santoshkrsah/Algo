"""5 intraday strategies from the Groww video (Aseem Singhal): EVERY number in ONE dict + the assumptions.

Rules: strategies/GROWW5_INTRADAY_RULES.md (V = said in the video, A = our assumption).
Data: stocks 1m = fast store (../Algo_Historical_Data/data/store, 2021-10-01 ->), NIFTY futures 1m = data/bars (Feb 2018 ->),
stock daily history for zones = data/daily/equity_daily.parquet (2010 ->) joined to the store panel.
"""
from copy import deepcopy

TITLE_SUFFIX = "Master These 5 Intraday Strategies Before Your Next Trade"
NAME = "groww5_intraday"
VIDEO = "https://www.youtube.com/watch?v=XlryGjTWQjk"

STRATS = {   # key -> (number, short name, Hinglish name)
    "scalp": (1, "Scalping 1-min", "Scalping (1-min, day high/low break + PSAR)"),
    "momo": (2, "Momentum NIFTY 5-min", "Momentum / staircase pullback (NIFTY futures 5-min)"),
    "brk": (3, "Breakout 15-min", "Breakout (zone 5+ touch, 15-min close ke paar)"),
    "rev": (4, "Reversal 15-min", "Reversal (zone 2-3 touch, 15-min rejection)"),
    "news": (5, "News / gap 5-min", "News proxy: gap >= 2% + pehli 5-min candle ulti"),
}

CONFIG = {
    "data": {"start": "2021-10-01", "end": "2026-11-13", "fut_start": "2018-02-01",
             "dev_end": "2022-12-31", "val_end": "2023-12-31"},          # build 2021-10..2022 | validation 2023 | latest 2024 ->
    "universe": "Large",               # AMFI Jun-2026 category (equity_universe.csv)
    "money": {"capital": 1_000_000, "risk_pct": 1.0, "max_pos_value": 500_000, "max_open": 5,
              "nifty_margin_lot": 150_000, "nifty_max_lots": 6},
    "slip_pct": 0.03,                  # % per side, stocks
    "slip_pts": 2.0,                   # NIFTY futures points per side
    "stress": 2.0,                     # stress run = slippage x 2
    "sq_off": 915,                     # 15:15 (minute of day) - every open trade exits at this minute's open
    "scalp": {"n_red": 3, "psar": (0.02, 0.02, 0.2), "hold": 5, "from": 570, "to": 900, "per_stock_day": 2, "per_day": 10,
              "use_psar": True},
    "momo": {"k": 2, "from": 570, "to": 870, "per_day": 3, "sides": "both"},
    "zones": {"pivot_k": 5, "lookback": 250, "span_pct": 1.5, "min_half_pct": 0.25, "tf": 15, "from": 570, "to": 885,
              "rev_touch": (2, 3), "brk_touch": 5, "rr": 2.0, "brk_mode": "direct", "use_touch": True},
    "news": {"gap_pct": 2.0, "until": 660, "per_day": 5, "need_color": True},
    # NIFTY lot size from this date (same table as reports 25 / 26)
    "lot_sizes": [("2015-10-29", 75), ("2021-07-30", 50), ("2024-04-26", 25), ("2025-01-31", 75), ("2025-12-31", 65)],
}

ASSUMPTIONS = [
    "Video sirf idea + ek-ek chart example deta hai: SL, target, qty, kitne trade - kuch nahi bataya. Neeche ke saare number hamare assumption hain (Ayush ko Telegram pe bheje).",
    "News ka data nahi hai: 'news stock' = jiska open kal ke close se 2% ya zyada upar/neeche khula (gap). Asli news filter nahi hai.",
    "Stocks = AMFI Jun-2026 large cap 100 (survivorship bias: aaj ki list purane saalon pe lagayi). NIFTY strategy NIFTY near-month futures me (index trade nahi hota).",
    "Signal candle band hone ke baad agle 1-min candle ke open pe entry (aage ka data nahi dekha). SL/target 1-min candle se check: chhua to us level pe, gap se paar ho to open pe; ek hi minute me dono -> SL maana.",
    "Har strategy ka alag Rs 10 lakh, compounding nahi. Risk 1% = Rs 10,000 per trade; stock position max Rs 5 lakh; max 5 trade ek saath; 15:15 pe sab band.",
    "Zone = daily swing high/low (5+5 din fractal, pichhle 250 din), 1.5% ke andar wale swing ek zone; touch = zone me kitne swing. 2-3 touch reversal, 5+ breakout, 4 skip.",
    "Scalping: 3 lagatar red (green) candle + close aaj ke ab tak ke low (high) ke paar + PSAR (0.02/0.2) upar (neeche). SL = 3 candles ka high (low), exit 5 min baad.",
    "Momentum: 5-min fractal swings (2+2 candle). HH + HL ke baad naya higher low confirm -> long, SL/trail = latest swing low. Short ulta (video me sirf buy dikhaya, alag report kiya).",
    "Charges + slippage hamesha on: stocks 0.03% har side (stress 0.06%), NIFTY 2 point har side (stress 4).",
]

COST_RATES = {
    "cash": {"brokerage_pct": 0.03, "brokerage_cap": 20.0, "exch_pct": 0.00297, "sebi_pct": 0.0001, "stamp_buy_pct": 0.003, "gst_pct": 18.0,
             "stt_sell": [("2000-01-01", 0.025)]},
    "fut": {"brokerage": 20.0, "exch_pct": 0.00173, "sebi_pct": 0.0001, "stamp_buy_pct": 0.002, "gst_pct": 18.0,
            "stt_sell": [("2000-01-01", 0.01), ("2023-04-01", 0.0125), ("2024-10-01", 0.02), ("2026-04-01", 0.05)]},
}


def cfg(**over) -> dict:
    """Copy of CONFIG with nested overrides, e.g. cfg(scalp={"use_psar": False})."""
    c = deepcopy(CONFIG)
    for k, v in over.items():
        if isinstance(v, dict) and isinstance(c.get(k), dict):
            c[k].update(v)
        else:
            c[k] = v
    return c
