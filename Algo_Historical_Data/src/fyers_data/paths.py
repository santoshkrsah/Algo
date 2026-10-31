"""Every folder and file location used by the project, in one place.

All downloaded or generated files live under data/ (git-ignored). Change a location here, nowhere else.
"""
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]          # project root (the folder holding src/, data/, .env)

ENV_FILE = ROOT / ".env"                            # secrets; never committed (see .env.example)

DATA = ROOT / "data"
FNO = DATA / "fno"                                  # fno/<1m|5m|15m|30m|60m>/<futures|options>/<SYMBOL>.csv
EQUITY = DATA / "equity"                            # equity/<YYYY-MM-DD>.csv  (all stocks, all timeframes)
REFERENCE = DATA / "reference"                      # lists that define what is downloaded
REPORTS = DATA / "reports"                          # generated PDFs, statistics, per-contract / per-stock tables
CACHE = DATA / ".cache"                             # daily Fyers access token

AMFI_DIR = REFERENCE / "amfi"                       # AMFI market-cap PDF used to filter the equity universe
UNIVERSE_CSV = REFERENCE / "equity_universe.csv"
SELECTED_CONTRACTS = REFERENCE / "selected_contracts.csv"

INDEX = DATA / "index"                              # index/NIFTY50_1m.csv
PRICE_BANDS = REFERENCE / "price_bands"             # price_bands/<YYYY-MM-DD>.csv  NSE sec_list: series, band, GSM remark
