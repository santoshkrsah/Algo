"""Every folder and file location used by the project, in one place.

All downloaded or generated files live under data/ (git-ignored). Change a location here, nowhere else.
"""
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]          # project root (the folder holding src/, data/, .env)

ENV_FILE = ROOT / ".env"                            # secrets; never committed (see .env.example)

DATA = ROOT / "data"
FNO = DATA / "fno"                                  # fno/<1m|5m|15m|30m|60m>/<futures|options>/<SYMBOL>.csv
FNO_CONTRACTS = FNO / "1m" / "contracts"            # one live contract each, NOT continuous (cont_flag=0)
FNO_BHAV = FNO / "bhavcopy"                         # bhavcopy/<YYYY-MM-DD>.csv  NSE F&O daily, futures rows only
FNO_BHAV_OPT = FNO / "bhavcopy_opt"                 # bhavcopy_opt/<YYYY-MM-DD>.csv  NSE stock option rows, 2 nearest expiries
FNO_BHAV_IDX = FNO / "bhavcopy_idx"                 # bhavcopy_idx/<YYYY-MM-DD>.csv  NSE NIFTY / BANKNIFTY option rows, 3 nearest expiries
FNO_BHAV_HIST = FNO / "bhavcopy_hist"               # bhavcopy_hist/<YYYY-MM-DD>.csv  stock futures + options (2 nearest), Oct 2021 - Oct 2024
FNO_BHAV_IDX_HIST = FNO / "bhavcopy_idx_hist"       # bhavcopy_idx_hist/<YYYY-MM-DD>.csv  NIFTY/BANKNIFTY option rows (3 nearest), Oct 2021 - Oct 2024
EQUITY = DATA / "equity"                            # equity/<YYYY-MM-DD>.csv  (all stocks, all timeframes)
STORE = DATA / "store"                              # fast Parquet copy of equity/ + daily panel (fyers_data.store); always rebuildable
REFERENCE = DATA / "reference"                      # lists that define what is downloaded
REPORTS = DATA / "reports"                          # generated PDFs, statistics, per-contract / per-stock tables
CACHE = DATA / ".cache"                             # daily Fyers access token

AMFI_DIR = REFERENCE / "amfi"                       # AMFI market-cap PDF used to filter the equity universe
UNIVERSE_CSV = REFERENCE / "equity_universe.csv"
SELECTED_CONTRACTS = REFERENCE / "selected_contracts.csv"

INDEX = DATA / "index"                              # index/NIFTY50_1m.csv
DAILY = DATA / "daily"                              # daily/equity/<SYMBOL>.csv, daily/index/<NAME>.csv (Fyers daily candles, 2010 on), daily/equity_daily.parquet
PRICE_BANDS = REFERENCE / "price_bands"             # price_bands/<YYYY-MM-DD>.csv  NSE sec_list: series, band, GSM remark
CORP_ACTIONS = REFERENCE / "corporate_actions.csv"  # NSE corporate actions (equities): symbol, ex-date, subject (dividend, bonus, split ...)
SYMBOL_CHANGES = REFERENCE / "symbol_changes.csv"   # NSE ticker changes: company, old, new, date (old bhavcopy rows use the old ticker)
