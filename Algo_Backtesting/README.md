# Algo_Backtesting

Backtesting on the NSE equity data downloaded by the sibling project `Algo_Historical_Data`
(read-only: nothing here writes to that folder).

    src/backtest/config.py   folder locations (data path, results/)
    src/backtest/data.py     load_equity(symbol, timeframe, start, end)
    src/backtest/notify.py   Telegram messages
    tools/send_telegram.py   send a custom Telegram message
    results/                 backtest output (git-ignored)

Setup: copy `.env.example` to `.env`, then `pip install -r requirements.txt`.

## Running a backtest and the report
    python tools/run_backtest.py --start 2021-10-01 --end 2026-10-31 --name full_2021_2026
Writes results/<name>/trades.csv and report.html (interactive dashboard: charts, every company, every trade) and
sends the HTML to Telegram. Report design: src/backtest/assets/report_template.html (+ chart.umd.min.js, bundled so it
works offline). Rebuild a report from saved trades with --from-trades.
