"""Folder locations. The historical data is read-only input; results are written under results/."""
import os
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
ENV_FILE = ROOT / ".env"
RESULTS = ROOT / "results"

# Sibling project Algo_Historical_Data, unless HISTORICAL_DATA_DIR in .env says otherwise.
DATA = Path(os.environ.get("HISTORICAL_DATA_DIR") or ROOT.parent / "Algo_Historical_Data" / "data")
EQUITY = DATA / "equity"                    # equity/<YYYY-MM-DD>.csv (all stocks, all timeframes)
FNO = DATA / "fno"                          # 1m/futures (continuous), 1m/contracts (one contract each), bhavcopy/
UNIVERSE_CSV = DATA / "reference" / "equity_universe.csv"
