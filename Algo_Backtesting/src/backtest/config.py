"""Folder locations. The historical data is read-only input; results are written under results/NN_<name>/."""
import os
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
ENV_FILE = ROOT / ".env"
RESULTS = ROOT / "results"


def result_dir(name: str) -> Path:
    """results/NN_<name>, numbered in the order folders were made. A name that already has a folder reuses it
    (reruns, --cached); a new name gets the next number. 'vol5l_2021_2026' and '07_vol5l_2021_2026' both work."""
    RESULTS.mkdir(parents=True, exist_ok=True)
    numbered = [p for p in RESULTS.iterdir() if p.is_dir() and re.fullmatch(r"\d+_.+", p.name)]
    for p in numbered:
        if name in (p.name, p.name.split("_", 1)[1]):
            return p
    out = RESULTS / f"{max((int(p.name.split('_', 1)[0]) for p in numbered), default=0) + 1:02d}_{name}"
    out.mkdir()
    return out

# Sibling project Algo_Historical_Data, unless HISTORICAL_DATA_DIR in .env says otherwise.
DATA = Path(os.environ.get("HISTORICAL_DATA_DIR") or ROOT.parent / "Algo_Historical_Data" / "data")
EQUITY = DATA / "equity"                    # equity/<YYYY-MM-DD>.csv (all stocks, all timeframes)
FNO = DATA / "fno"                          # 1m/futures (continuous), 1m/contracts (one contract each), bhavcopy/
UNIVERSE_CSV = DATA / "reference" / "equity_universe.csv"
