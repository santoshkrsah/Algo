"""Wait for already-running download_equity.py process(es) to exit, then send a Telegram message.
If the run finished completely, also build the final summary PDF and send it.

    python -m fyers_data.monitor.notify_when_done <pid> [<pid> ...]
Used for a run that was started before download_equity.py had built-in notifications.
"""
import json
import subprocess
import sys
import time
from pathlib import Path

from fyers_data.notify import send
from fyers_data.paths import EQUITY as OUT
from fyers_data.reports.final_report import main as final_report

TOTAL_BATCHES = 56


def alive(pid: int) -> bool:
    r = subprocess.run(["tasklist", "/FI", f"PID eq {pid}", "/NH"], capture_output=True, text=True)
    return str(pid) in r.stdout


def main():
    pids = [int(x) for x in sys.argv[1:]]
    while any(alive(p) for p in pids):
        time.sleep(30)
    done = len(json.loads((OUT / "_progress.json").read_text())["done"])
    files = list(OUT.glob("2*.csv"))
    if done == TOTAL_BATCHES and not (OUT / "_parts").exists() and files:
        gb = sum(f.stat().st_size for f in files) / 1e9
        send(f"Equity download FINISHED. {len(files)} daily files in data/equity/ ({gb:.1f} GB). "
             "Building the final summary PDF now (about 15-20 minutes); it will be sent here when ready.")
        try:
            final_report()
        except Exception as e:
            send(f"Final summary PDF failed: {type(e).__name__}: {str(e)[:200]}. The data itself is complete.")
    elif done == TOTAL_BATCHES:
        send("Equity download: all batches saved but the merge into daily files did not complete. "
             "Re-run download_equity.py to finish the merge.")
    else:
        send(f"Equity download STOPPED: {TOTAL_BATCHES - done} of {TOTAL_BATCHES} batches still not done. "
             "Re-run download_equity.py to continue.")


if __name__ == "__main__":
    main()
