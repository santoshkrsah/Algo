# Algo

Research and trading workspace for **Indian markets (NSE equities, F&O, indices)**.
It downloads historical data from the Fyers API, backtests intraday / options / swing strategies on it,
publishes every result as an interactive HTML report, and has a starting layer for live trading with the Firstock broker.

```
Algo/
├── Algo_Historical_Data/   download Fyers data + build the fast Parquet store      (own .venv)
├── Algo_Backtesting/       strategies, backtest engines, HTML reports, results     (own .venv)
├── Algo_Live_Trading/      Firstock broker utilities for live order placement
└── CLAUDE.md               working rules for every backtest (notifications, report format, honest testing)
```

The three folders are separate Python projects. Backtesting only **reads** `../Algo_Historical_Data/data`; it never writes there.

---

## 1. Algo_Historical_Data — data

Pulls 1-minute candles from Fyers and resamples them to 5m / 15m / 30m / 60m (anchored at 09:15).

| Data | Where (`data/`, git-ignored) |
|---|---|
| NSE equities above a market-cap cut-off, Oct 2021 → today, one CSV per day | `equity/<YYYY-MM-DD>.csv` |
| Futures + ATM ±5 strike options (stocks and indices) | `fno/<timeframe>/<futures\|options>/<SYMBOL>.csv` |
| Daily candles 2010 → today + Nifty 50 / 200 / 500 | `daily/`, `daily/equity_daily.parquet` |
| Fast Parquet store (rebuildable cache of the daily CSVs) | `store/` (+ `store/adjustments.csv` split/bonus factors) |
| Universe, contracts, AMFI list, corporate actions | `reference/` |

Daily routine:

```powershell
cd Algo_Historical_Data
.venv\Scripts\activate
python -m fyers_data.update_equity        # only the missing days (~10 min), also rebuilds the store
python -m fyers_data.store verify --days 5  # must say OK
```

Never re-run the full `download_equity` for new days, and never run two Fyers downloads at once.
Full command list and data formats: [Algo_Historical_Data/README.md](Algo_Historical_Data/README.md).

## 2. Algo_Backtesting — strategies and reports

```
src/backtest/
    fast.py               fast data access (read_day, day_1m, symbol_1m, panel + add_features)
    config.py             paths; result_dir(name) gives the numbered results folder
    engine.py, strategy.py, report.py    generic engine and report
    <strategy>.py         one engine per idea (orb, vol5l, opt_r3, straddle, calspread, gapbo, vcp, ...)
    <strategy>_report.py  its HTML report
    rulebook.py           honest-testing engine for orb / noise / vwap / fh / gapfade
    viewer.py + assets/   report templates, viewer.css / viewer.js, lightweight-charts
    notify.py             Telegram messages and file uploads
tools/
    run_<strategy>.py     one runner per backtest
    run_rulebook.py       rulebook runner (DEV / --final)
    build_site.py         builds the Reports website
    send_telegram.py, status_heartbeat.py   status updates while work is running
strategies/
    RULEBOOK.md           exact spec of the rulebook strategies
    MY_STRATEGY_RULES.md, FIB_R3_QA.md
results/NN_<name>/        one numbered folder per backtest (report.html, trades.csv)
site/                     Reports website (reports.json -> index.html)
```

Read data in backtest code through `backtest.fast` only (no `pd.read_csv` loops over `data/equity`):

```python
from backtest import fast
df    = fast.read_day(day, ("1m", "15m"))
panel = fast.add_features(fast.panel(start, end))   # pick stocks here first, then load minutes only for them
```

Run a backtest, e.g.:

```powershell
cd Algo_Backtesting
.venv\Scripts\activate
python tools/run_rulebook.py --strategy orb                        # DEV 2021-10 -> 2023-12 (2023 = validation)
python tools/run_rulebook.py --strategy orb --final --stop-atr 0.25  # FINAL 2024 -> latest, once per frozen setting
python tools/run_vol5l.py                                          # any other strategy has its own runner
```

More detail: [Algo_Backtesting/README.md](Algo_Backtesting/README.md).

### Strategies tested so far

`results/` holds 25 numbered reports (01 → 25). Main families:

- **Intraday equity**: gap + volume, VMU, vol5l, CPR / VWAP / open-low, gap RSI-EMA (all caps and small cap), call-vs-future, PDH/PDL on Nifty, rulebook ORB / noise / VWAP / first-half-hour / gap-fade.
- **Options**: short straddles (layered, risk layers, slippage studies), STBT options, R3/S3 option buying, breakout put selling.
- **Calendar / arbitrage**: calendar arbitrage and calendar spreads on futures.
- **Swing**: VCP, gap breakout long (daily data from 2011), SuperTrend + MA on Nifty.

### Honest-testing rules

- Costs and slippage are always on; every report also shows a 2x-slippage stress test.
- Tune only on DEV (Oct 2021 → Dec 2023); the FINAL period (2024 → latest) is run once per frozen setting.
- Every rulebook run is logged in `results/RULEBOOK_RUNS.csv`.
- The stock list is the AMFI Jun-2026 list, so results carry **survivorship bias** — every report says so.
- Prices are split/bonus adjusted via `store/adjustments.csv`; reports mention it when a strategy turns this off.

### Reports

Each backtest writes an interactive HTML dashboard in simple Hinglish: KPI cards, charts, year/month tables with Return %,
every stock and every trade, a Trade viewer with candle charts, a "Test ki settings" strip, and Rules + Formulas tabs.
All reports are listed on one private Reports website (Claude Artifact), URL in
[Algo_Backtesting/site/site.json](Algo_Backtesting/site/site.json). After a new report: update `site/reports.json` →
`python tools/build_site.py` → republish to the same URL.

## 3. Algo_Live_Trading — broker layer

`Firstock_Utils.py` is a utility layer for the **Firstock** API (login with TOTP, symbol master download, orders),
written with the same interface as an earlier FlatTrade utility so strategies can switch brokers with few changes.
`Firstock_Config.py` holds the settings. Credentials and key files stay local and are git-ignored.

---

## Setup

Windows, Python 3.11+. Each project has its own virtual environment:

```powershell
cd Algo_Historical_Data          # and the same for Algo_Backtesting
python -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt
copy .env.example .env           # Fyers keys, Telegram bot token
python tools/check_secrets.py --install-hook   # Algo_Historical_Data: blocks commits with secrets
```

## What git does not track

`.env`, `.venv/`, all of `data/`, broker credential/key files, and every `*.csv`
(except `results/RULEBOOK_RUNS.csv`) — trade data already lives inside each `report.html`.

## Team workflow (from CLAUDE.md)

- Every backtest sends Telegram updates: start, progress every 10 minutes, and a finish message with the HTML report and website link.
- Trading questions go to the trading lead, technical issues (code, data, login, errors) to the technical lead.
- Results folders are created only through `backtest.config.result_dir(name)` so the numbering stays in order.
