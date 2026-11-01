"""Telegram status every 10 minutes while Claude works: what it is doing now (first line of a status file it keeps
updating) plus progress it can count on its own (files in a folder, last line of a log).

    python tools/status_heartbeat.py --status-file status.txt --count-dir ../Algo_Historical_Data/data/fno/bhavcopy
    python tools/status_heartbeat.py --status-file status.txt --log results/run.log --every 600

Stops when the status file's first line starts with DONE (sends that line as the last message), or after --max-hours.
"""
import argparse
import sys
import time
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from backtest.notify import send  # noqa: E402


def _read(path: Path) -> list[str]:
    try:
        return [l.rstrip() for l in path.read_text(encoding="utf-8").splitlines() if l.strip()]
    except OSError:
        return []


def message(status: Path, count_dirs: list[Path], logs: list[Path], t0: float) -> str:
    lines = _read(status)
    now = lines[0] if lines else "(status file khaali hai)"
    age = (time.time() - status.stat().st_mtime) / 60 if status.exists() else None
    out = [f"Claude status {datetime.now():%H:%M} ({(time.time() - t0) / 60:.0f} min se kaam chal raha hai)",
           f"Abhi: {now}"] + [f"  {l}" for l in lines[1:6]]
    if age is not None and age >= 20:
        out.append(f"  (ye step {age:.0f} min se chal raha hai)")
    for d in count_dirs:
        files = sorted(d.glob("*.*"), key=lambda p: p.stat().st_mtime)
        if files:
            out.append(f"Progress {d.name}: {len(files)} files, sabse naya {files[-1].stem}")
    for f in logs:
        last = _read(f)
        if last:
            out.append(f"Log {f.name}: {last[-1][:300]}")
    return "\n".join(out)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--status-file", required=True, type=Path)
    ap.add_argument("--count-dir", action="append", default=[], type=Path)
    ap.add_argument("--log", action="append", default=[], type=Path)
    ap.add_argument("--every", type=int, default=600, help="seconds between messages")
    ap.add_argument("--max-hours", type=float, default=8)
    a = ap.parse_args()
    t0 = time.time()
    send("Claude status: ab se har 10 min me yahan update aayega ki kya chal raha hai.\n" + message(a.status_file, a.count_dir, a.log, t0))
    while time.time() - t0 < a.max_hours * 3600:
        for _ in range(a.every):
            time.sleep(1)
            lines = _read(a.status_file)
            if lines and lines[0].upper().startswith("DONE"):
                send(f"Claude status {datetime.now():%H:%M}: {lines[0]}")
                return 0
        send(message(a.status_file, a.count_dir, a.log, t0))
    send(f"Claude status: {a.max_hours:g} ghante ho gaye, 10-min updates band (naya kaam shuru hone pe phir chalu honge).")
    return 0


if __name__ == "__main__":
    sys.exit(main())
