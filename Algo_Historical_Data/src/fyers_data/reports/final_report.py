"""After the equity merge: rebuild stats from the daily files, regenerate the PDF, send it to Telegram.

    python -m fyers_data.reports.final_report
"""
import json
import subprocess
import sys
from pathlib import Path

from fyers_data.notify import send_document
from fyers_data.paths import REPORTS, ROOT

PDF = REPORTS / "Fyers_Equity_Data_Summary.pdf"


def run(module: str, *args: str):
    subprocess.run([sys.executable, "-m", f"fyers_data.reports.{module}", *args], cwd=ROOT, check=True)


def main():
    run("equity_stats", "--final")      # scans every daily file (about 15 minutes)
    run("equity_pdf")
    S = json.loads((REPORTS / "equity_stats.json").read_text())
    caption = (f"Final equity data summary (download complete): {S['stocks_done']:,} stocks, {S['days']:,} daily files "
               f"({S['first_day']} to {S['last_day']}), {sum(S['tf_rows'].values()) / 1e6:,.0f} million candles, "
               f"{S['size_gb']:.1f} GB.")
    if not send_document(PDF, caption):
        raise RuntimeError("PDF was generated but could not be sent to Telegram")
    print("final report sent", flush=True)


if __name__ == "__main__":
    main()
