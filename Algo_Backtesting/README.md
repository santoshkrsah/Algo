# Algo_Backtesting

Backtesting on the NSE equity data downloaded by the sibling project `Algo_Historical_Data`
(read-only: nothing here writes to that folder).

    src/backtest/config.py   folder locations (data path, results/)
    src/backtest/data.py     load_equity(symbol, timeframe, start, end)
    src/backtest/notify.py   Telegram messages
    tools/send_telegram.py   send a custom Telegram message
    results/                 backtest output (git-ignored)

Setup: copy `.env.example` to `.env`, then `pip install -r requirements.txt`.

## Fast data access (backtest.fast)
Reads the Parquet store built by `python -m fyers_data.store build` (Algo_Historical_Data); falls back to the CSVs if it is missing.

    from backtest import fast
    fast.read_day(day, ("1m", "15m"))      # same columns/order as the daily CSV (all engines use this now)
    fast.day_1m(day)                        # symbol, minute, OHLCV
    fast.symbol_1m("RELIANCE", start, end)  # one stock, all days
    fast.add_features(fast.panel(start, end))   # one row per stock-day + ATR, avg value, RVOL (previous days only)

## Rulebook strategies (orb, noise, vwap, fh)
    python tools/run_rulebook.py --strategy orb                         # DEV 2021-10 -> 2023-12 (2023 = validation)
    python tools/run_rulebook.py --strategy orb --grid "stop_atr=0.1,0.25,0.5;rvol_min=1,1.5,2"
    python tools/run_rulebook.py --strategy orb --final --stop-atr 0.25 # FINAL 2024 -> latest, once per setting
Rules: strategies/RULEBOOK.md. Run log: results/RULEBOOK_RUNS.csv.

## Running a backtest and the report
    python tools/run_backtest.py --start 2021-10-01 --end 2026-10-31 --name full_2021_2026
Writes results/<name>/trades.csv and report.html (interactive dashboard: charts, every company, every trade) and
sends the HTML to Telegram. Report design: src/backtest/assets/report_template.html (+ chart.umd.min.js, bundled so it
works offline). Rebuild a report from saved trades with --from-trades.
