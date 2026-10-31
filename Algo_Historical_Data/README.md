# Algo_Historical_Data

Downloads historical market data from the **Fyers API** for backtesting:

- **F&O**: futures and ATM +-5 strike options (stocks and indices), 1m / 5m / 15m / 30m / 60m
- **NSE equities**: stocks above a market-cap cut-off, 1 Oct 2021 onward, all five timeframes, one CSV per trading day

Only 1-minute candles are requested from Fyers; the other timeframes are resampled from them (anchored at the 09:15 open).

## Setup (Windows, Python 3.11+)

```powershell
python -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt
copy .env.example .env        # then edit .env with your own keys
python tools/check_secrets.py --install-hook
```

`.env` holds your Fyers credentials and (optional) Telegram bot token. It is git-ignored and must never be committed.

## Project layout

```
src/fyers_data/            code
    paths.py               every folder path, in one place
    auth.py                headless Fyers login (TOTP), daily token cache
    notify.py              Telegram messages and file uploads
    universe.py            builds the equity list from AMFI market-cap data
    download_fno.py        futures + options downloader
    download_equity.py     equity downloader + merge into daily files
    reports/               statistics and PDF summaries
    monitor/               Telegram status updates for long runs
tools/check_secrets.py     blocks commits that contain keys or data
data/                      EVERYTHING downloaded or generated (git-ignored)
    fno/<timeframe>/<futures|options>/<SYMBOL>.csv
    equity/<YYYY-MM-DD>.csv       all stocks and timeframes for that day
    reference/                    equity_universe.csv, selected_contracts.csv, amfi/
    reports/                      PDFs and statistics
    .cache/                       daily Fyers access token
```

## Running

Run from the project folder with the virtual environment active.

| Task | Command |
|---|---|
| Check the F&O contract selection (no download) | `python -m fyers_data.download_fno --dry-run` |
| Download F&O data (resumable) | `python -m fyers_data.download_fno` |
| Build the equity list (needs the AMFI PDF in `data/reference/amfi/`) | `python -m fyers_data.universe` |
| Download equity data (resumable, then merges to daily files) | `python -m fyers_data.download_equity` |
| F&O summary PDF | `python -m fyers_data.reports.fno_stats` then `python -m fyers_data.reports.fno_pdf` |
| Equity summary PDF | `python -m fyers_data.reports.equity_stats --final` then `python -m fyers_data.reports.equity_pdf` |

The equity downloader saves progress after every batch of 25 stocks. If it stops, run it again and it continues. When a full run finishes it merges the batches into daily files, builds the summary PDF and sends it to Telegram (if configured).

## Data format

Equity daily files: `symbol, timeframe, datetime, open, high, low, close, volume`, sorted by timeframe (1m first), symbol, time.
F&O files: `datetime, open, high, low, close, volume`. Timestamps are IST candle start times with no timezone suffix.

## Things to know

- Fyers has no expired F&O contracts. Options only exist from each contract's listing date; futures are a continuous series.
- Fyers history is adjusted for corporate actions, so prices are not the raw values of the time.
- The equity cut-off uses a market-cap list from one date (AMFI, six months to 30 Jun 2026), so it is survivorship-biased.
- Fyers rate-limits history requests (about 200 per minute); the downloaders stay under that and retry.

## Security

- Secrets are only in `.env` and `data/.cache/` (both ignored by git). `.env.example` has placeholders only.
- `python tools/check_secrets.py` scans everything git would commit for values from your `.env`, Telegram-token-shaped strings and large files. The pre-commit hook (`--install-hook`) runs it before every commit.
- If a secret is ever pasted somewhere public, rotate it: Fyers app secret and TOTP in your Fyers account, bot token with `/revoke` in @BotFather.
