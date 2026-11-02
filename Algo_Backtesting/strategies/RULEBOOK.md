# Rulebook strategies — exact spec (orb, noise, vwap, fh)

Engine: `src/backtest/rulebook.py` (reference). Runner: `tools/run_rulebook.py`. If this file and the code ever disagree, stop and ask which one is intended; a rule change updates both in the same edit.
Research background (Hinglish rulebook with sources): the "Intraday Rulebook" Claude doc.

## 0. Seedhi baat (Hinglish)
- 4 strategies research papers se: **ORB-5 Stocks in Play** (Zarattini–Barbon–Aziz 2024), **Noise-band momentum** ("Beat the Market", 2024), **VWAP trend** (India version), **First half-hour → last half-hour** (Gao et al.).
- Har din point-in-time universe: sirf liquid stocks (turnover, price, ATR) — aaj ki list purane din par nahi lagti.
- Saare charges + slippage hamesha on. Pehle **DEV** (2021-10 → 2023-12) par test/tune, phir **FINAL** (2024 → aaj) sirf ek baar.
- Stock list AMFI Jun-2026 wali hai (1,386): jo stocks beech me delist/merge hue (jaise HDFC Ltd, Jul 2023) ya chhote ho gaye woh data me nahi → survivorship bias. Report me hamesha likhna hai.

## 1. Conventions
- 1-minute candles, IST, timestamp = candle start. Session minute index m = 0..374 (09:15..15:29).
- A decision at minute m uses data up to the close of candle m; it fills at the OPEN of candle m+1.
- Exit time 15:15 = open of candle m = 360. ORB/VWAP entry cutoff 14:30 (m = 315 for ORB triggers; last VWAP signal bar ends at m = 314).
- Day open = open of m = 0. Day close = close of m = 374. prev_close = previous trading day's close.
- Drop a day if candle m = 0 is missing or fewer than 95% of 375 candles exist. Inside kept days, a missing minute gets O = H = L = C = last close, V = 0.
- If |day open / prev_close − 1| > 25%: treat as an unadjusted split/bonus and skip that stock from that day for max(20, lookback) + 1 days.
- Prices come through `fast.symbol_1m()` with `data/store/adjustments.csv` applied.

## 2. Features (only days strictly before today)
- TR_d = max(H_d − L_d, |H_d − C_{d−1}|, |L_d − C_{d−1}|); ATR14 = simple mean of the previous 14 TR.
- sigma_daily = sample stdev (ddof = 1) of the previous 14 close-to-close returns.
- AvgTurnover20 = mean of (close × volume) over the previous 20 days, rupees (filter in crore = 1e7).
- ADV20 = mean daily volume over the previous 20 days.
- RVOL = volume of m = 0..4 ÷ mean of the same over the previous 14 days.
- NR4 / NR7 (today) = yesterday's range is the smallest of the last 4 / 7 days.
- CPR width % (today), from yesterday's H, L, C: P = (H+L+C)/3, BC = (H+L)/2, width = 2·|P − BC| / P × 100.
- Session VWAP from 09:15 with typical price (H+L+C)/3.

## 3. Universe and selection (every day, point in time)
- Eligible: day open ≥ ₹100, AvgTurnover20 ≥ ₹50 crore, ATR14 / day open ≥ min_atr_pct.
- orb: min_atr_pct 1.0; RVOL ≥ 1.0; top 5 by RVOL.  vwap: min_atr_pct 1.0; RVOL ≥ 1.5; top 5 by RVOL.
- noise: min_atr_pct 1.5; top 10 by AvgTurnover20.  fh: min_atr_pct 1.0; top 10 by AvgTurnover20.
- Ties are broken by symbol name (stable sort). A selected stock without a signal leaves its slot empty.
- Speed-up (same result): `prefilter_symbols()` skips stocks that never reach 70% of the turnover rule / 90% of the price rule in the period.

## 4. orb (ORB-5 Stocks in Play)
- Opening range = m = 0..4. direction = sign(close of m = 4 − open of m = 0); 0 → no trade.
- Long: first m in [5, 315) with High[m] ≥ OR_high → entry = max(OR_high, Open[m]). Short: Low[m] ≤ OR_low → entry = min(OR_low, Open[m]). Only the side matching direction. One trade per stock per day.
- Stop = entry ∓ k·ATR14; k = stop_atr, default 0.25 (paper 0.10).
- Stop check from the entry candle (inclusive) to m = 359. Hit in the entry candle → exit at the stop. Later → min(stop, Open[hit]) for longs, max(stop, Open[hit]) for shorts.
- No stop hit → exit at Open[360] (15:15). No target.

## 5. noise (noise-band momentum)
- Checkpoints m = 29, 59, …, 329 (09:45 … 14:45).
- sigma(m) = mean over the previous `lookback` days (14) of |Close[m] / DayOpen − 1|.
- UB = max(DayOpen, prev_close) · (1 + VM·sigma(m)); LB = min(DayOpen, prev_close) · (1 − VM·sigma(m)); VM = 1.0.
- At each checkpoint: exit long if Close < max(UB, VWAP); exit short if Close > min(LB, VWAP); then if flat: long if Close > UB, short if Close < LB. Fills at Open[m + 1]. Open positions exit at Open[360].

## 6. vwap (VWAP trend, India version)
- Bars of B = 5 minutes; first decision at the bar ending m = 14 (09:30).
- Exit long when bar close < VWAP; exit short when bar close > VWAP.
- If flat, entries so far < 3 and bar end m ≤ 314: long if close > VWAP·(1 + 0.2%), short if close < VWAP·(1 − 0.2%). Fills at Open[m + 1]. Open positions exit at Open[360].

## 7. fh (first half-hour momentum)
- r_FH = Close[29] / prev_close − 1. RVOL30 = volume of m = 0..29 ÷ its mean over the previous 14 days.
- Trade if |r_FH| ≥ 0.5 · sigma_daily and RVOL30 ≥ 1; side = sign(r_FH). Entry Open[330] (14:45), exit Open[360].

## 8. Sizing and risk
- E = equity at the start of the day (compounded daily). L = max leverage (5; fh 3). N = top_n.
- orb: shares = floor(min(1% · E / |entry − stop|, L·E / (N · entry))).
- noise, vwap, fh: shares = floor(E · min(L, 0.02 / sigma_daily) / N / entry).
- Cap: shares ≤ 1% of ADV20. Daily loss limit: no new entries once trades already closed that day lost ≥ 3% of E.

## 9. Costs (both legs of every trade)
- Brokerage per order = min(₹20, 0.03% of order value). STT = 0.025% of sell value. NSE transaction = 0.00307% of (buy + sell). SEBI = ₹10 per crore of (buy + sell). Stamp = 0.003% of buy value. GST = 18% of (brokerage + transaction + SEBI).
- Slippage = 0.03% per side on fill prices; stress run = 0.06% per side.
- Check: a ₹5,00,000 round trip costs ₹224.61 before slippage; ₹1,00,000 costs ₹82.68.
- Note: these are F&O-liquid-stock assumptions (AvgTurnover20 ≥ ₹50 Cr). For small caps use a higher slippage (`--slippage-pct`), never a lower one.

## 10. Optional filters (one at a time, DEV only)
nr4, nr7, cpr (yesterday's CPR width < 0.2%), gap (|gap| ≥ 0.5%), noexpiry (Nifty weekly expiry weekday: Thu until 2025-08-31, Tue from 2025-09-01), vixhigh (needs `--vix-file`, India VIX daily: `python -m fyers_data.download_reference index NSE:INDIAVIX-INDEX INDIAVIX`), trendshort (needs `--nifty-file ../Algo_Historical_Data/data/index/NIFTY50_1m.csv`), `--skip-dates-file` (Budget, RBI, results days).

## 11. Protocol and pass criteria
- DEV = 2021-10-01 → 2023-12-31, OOS from 2023-01-01 (2021-22 build, 2023 validate). Default of `tools/run_rulebook.py`; the runner refuses a DEV run that touches 2024.
- FINAL = 2024-01-01 → latest (`--final`), once per frozen parameter set; the runner refuses a repeat (`--force-final` is logged).
- Pass (OOS part): Sharpe ≥ 1.0; max drawdown ≤ 20%; profit factor ≥ 1.3; expectancy ≥ +0.10R (orb); ≥ 300 trades and t-stat ≥ 2; average gross trade ÷ average cost ≥ 2; OOS Sharpe ≥ 0.5 × in-sample Sharpe; still profitable with slippage 2x.
- Any single FAIL → not ready for paper trading. Every run (good or bad) is a row in `results/RULEBOOK_RUNS.csv`; always say how many variants were tried.
