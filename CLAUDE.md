# Algo project rules (apply to every chat)

## Every backtest, always (without being asked)
1. Telegram: message on start, progress every 10 minutes, finish message with key numbers (tools use Algo_Backtesting/src/backtest/notify.py).
2. On finish send the report as the HTML file only (no PDF). Tag @ayushanand440 (trading) on results, @albert_max (technical) on errors.
   Any issue or open question while running a backtest goes to Telegram immediately (don't wait for the end): trading issues/questions/assumptions tag @ayushanand440, technical issues (code, data, download, login, errors) tag @albert_max.
   Also keep posting short status updates to Telegram on what Claude is doing (each main step: data download, building/testing code, run started, results) with tools/send_telegram.py.
3. Report = the interactive HTML dashboard in Algo_Backtesting/src/backtest/assets/ (see results/vmu_v3/report.html): KPI cards, charts, month table, every stock and every trade, and ALWAYS a "Rules used" section.
4. Almax (@albert_max) has technical skills only; Ayush (@ayushanand440) is the trading expert. Ask trading questions to Ayush.

## Layout
- Algo_Historical_Data: downloads data (own .venv). Algo_Backtesting: strategies, engine, results (own .venv). Backtesting only reads ../Algo_Historical_Data/data.
