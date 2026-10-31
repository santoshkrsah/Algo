"""Send a Telegram status message every N minutes while the equity merge + final report run.

    python -m fyers_data.monitor.status_reporter <interval_minutes> <pid> [<pid> ...]
Stops by itself once the final summary PDF has been generated (the PDF itself is sent by final_report.py).
"""
import json
import re
import shutil
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path

from fyers_data.notify import send
from fyers_data.paths import EQUITY as OUT, REPORTS

TOTAL_DAYS = 1258
DAY_FILE = re.compile(r"^2\d{3}-\d\d-\d\d\.csv$")
MAX_HOURS = 6


def alive(pid: int) -> bool:
    r = subprocess.run(["tasklist", "/FI", f"PID eq {pid}", "/NH"], capture_output=True, text=True)
    return str(pid) in r.stdout


def daily_files():
    return sorted(f.name for f in OUT.iterdir() if DAY_FILE.match(f.name))


def report_done() -> bool:
    stats, pdf = REPORTS / "equity_stats.json", REPORTS / "Fyers_Equity_Data_Summary.pdf"
    try:
        return (json.loads(stats.read_text()).get("final") is True and pdf.exists()
                and pdf.stat().st_mtime >= stats.stat().st_mtime)
    except (OSError, ValueError):
        return False


def main():
    interval = float(sys.argv[1]) * 60
    pids = [int(x) for x in sys.argv[2:]]
    t_end = time.time() + MAX_HOURS * 3600
    while time.time() < t_end:
        now = datetime.now().strftime("%H:%M")
        free = shutil.disk_usage(OUT).free / 1e9
        files = daily_files()
        merging = any(alive(p) for p in pids)
        if report_done():
            return
        if merging:
            n = len(files)
            send(f"Status {now}: all 56 batches downloaded. Merging into daily files: {n:,}/{TOTAL_DAYS:,} "
                 f"({n / TOTAL_DAYS * 100:.0f}%), latest day {files[-1][:-4] if files else '-'}. "
                 f"Disk free {free:.0f} GB.")
        elif len(files) >= TOTAL_DAYS and not (OUT / "_parts").exists():
            send(f"Status {now}: merge complete ({len(files):,} daily files). Building the final summary PDF "
                 f"(scanning all files, then generating the PDF); it will be sent here when ready. Disk free {free:.0f} GB.")
        else:
            return          # download stopped early; notify_when_done.py already reports that
        time.sleep(interval)


if __name__ == "__main__":
    main()
