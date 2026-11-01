# Algo project rules (apply to every chat)

## Every backtest, always (without being asked)
1. Telegram: message on start, progress every 10 minutes, finish message with key numbers (tools use Algo_Backtesting/src/backtest/notify.py).
2. On finish send the report as the HTML file only (no PDF) AND the Reports website link (see "Reports website" below). Tag @ayushanand440 (trading) on results, @albert_max (technical) on errors.
   Any issue or open question while running a backtest goes to Telegram immediately (don't wait for the end): trading issues/questions/assumptions tag @ayushanand440, technical issues (code, data, download, login, errors) tag @albert_max.
   Also keep posting short status updates to Telegram on what Claude is doing (each main step: data download, building/testing code, run started, results) with tools/send_telegram.py.
   And EVERY 10 MINUTES while Claude is working (download, coding, testing, running - not only during the backtest run): start
   Algo_Backtesting/tools/status_heartbeat.py in the background at the start of the work (--status-file in the scratchpad, plus --count-dir / --log for progress),
   keep the status file's first line updated with the current step, and write "DONE ..." to it at the end to stop the updates.
3. Report = the interactive HTML dashboard in Algo_Backtesting/src/backtest/assets/ (latest: vol5l_template.html, results/07_vol5l_2021_2026/report.html): KPI cards, charts, month table, every stock and every trade, and ALWAYS a "Rules" tab (not "Rules used").
   The whole report is written in simple Hinglish (easy words), especially the Rules tab. At the bottom of the Rules tab, ALWAYS a "Formulas" card listing every formula used in that backtest (entry, qty, target, SL/trailing, exit fill, slippage, P&L, costs, win rate, profit factor, drawdown, ...).
   The Rules card and the Formulas card each have a one-click "Copy karo" button in the top-right corner that copies that card as plain text.
   NEW LOOK for every new report (user asked 1 Nov 2026; keep the old tabs/cards, add this): start from assets/opt_r3_template.html (example: results/12_opt_r3_2024_2026/report.html),
   which pulls in assets/viewer.css + viewer.js + lightweight-charts.js through backtest/viewer.py:
   - sticky header (web view) with logo, title, a "Data <first date> -> <last date>" chip (period of the data that was tested) and the Dark/Light button; on mobile the header scrolls away and the tabs stay sticky.
   - navy theme (dark and light), saved theme choice, fully responsive on mobile (no horizontal page scroll, tables scroll inside their card).
   - a "Trade viewer" tab: filters (stock, profit/loss, exit reason, dates, Reset), trade list with arrow-key navigation, meta box, TradingView lightweight-charts (candles / price with Entry-Exit markers and R3/SL/target lines; use 1-minute candles when the data has them, daily candles otherwise) and a P&L/cost breakdown.
   - Every report also shows: whether profits are compounded or not (say it in the subtitle / Rules), a Return % column in the year and month tables (net / starting capital), and an "investment" card (capital, risk per trade, qty rule, max per stock, max open positions, average / biggest position, peak money deployed).
   - Home page (top of the report, visible on every tab) has a "Test ki settings" strip of chips with EVERY setting the test used: universe / filters (e.g. small cap, market cap rule), slippage, costs, capital + compounding, risk per trade, position limits, entry rule, plus the selected side / gap / exit. Whenever a new setting (slippage, filter, cost ...) is added to a test, add it to this strip too (key ones highlighted). Code: setrow CSS in viewer.css, `settings` in the report data.
   - Rules and Formulas tabs: only as much detail as needed to follow the test, no extra clutter.
   No personal details of the site that inspired the design are used.
4. Almax (@albert_max) has technical skills only; Ayush (@ayushanand440) is the trading expert. Ask trading questions to Ayush.

## Layout
- Algo_Historical_Data: downloads data (own .venv). Algo_Backtesting: strategies, engine, results (own .venv). Backtesting only reads ../Algo_Historical_Data/data.
- Every report folder in Algo_Backtesting/results is numbered in the order it was made: NN_<name> (01_oct_2026 ... 09_stbt_options, next one 10_).
  Runners get their folder from backtest.config.result_dir(name): a new name gets the next number, a rerun of an existing name reuses its folder. Never create results/<name> directly.

## Reports website (after every new report or rerun, without being asked)
- One private Claude Artifact lists every report (search, type/result filter, sort; each opens in a new tab):
  https://claude.ai/artifact/Qn4HCC5NT1iakRxbAfuYCa (also in Algo_Backtesting/site/site.json). Always update THIS url, never make a second site.
- Steps: add/update the report's entry in Algo_Backtesting/site/reports.json (name, type Intraday/Options/Calendar/Swing, period,
  one-line Hinglish result, net, pf, trades, verdict profit/loss/study) -> `python tools/build_site.py` -> Artifact publish with
  url from site.json, file_path site/index.html, files = only the new/changed report(s) from site/files.json (the others stay on the site).
- The Telegram finish message carries the HTML file, the website link and the direct report link <site url>#report-NN (NN = folder number).
- Limits: 16 MB per report file, 64 MB per publish, 256 MB / 511 files per site version.
