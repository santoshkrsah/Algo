"""Reports website: one page listing every results/NN_<name>/report.html (filter, sort). Each report opens in a new tab on
the site's own link + #report-NN, which shows that report full screen.

    python tools/build_site.py

Reads site/reports.json (name, type, period, one-line result per report folder; add an entry for every new report),
writes site/index.html and site/files.json (published path -> report file).
The live site is on our own web hosting: tools/upload_site.py builds the same page and uploads it by FTP (see there).
"""
import json
import re
import subprocess
import sys
from datetime import date, datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from backtest.config import RESULTS, ROOT  # noqa: E402

SITE = ROOT / "site"
TEMPLATE = ROOT / "src" / "backtest" / "assets" / "site_template.html"


def first_added(report: Path) -> date:
    """Day the report was first committed to git, else the day the file was written."""
    out = subprocess.run(["git", "log", "--diff-filter=A", "--format=%ad", "--date=short", "--", str(report.relative_to(ROOT))],
                         cwd=ROOT, capture_output=True, text=True).stdout.split()
    return date.fromisoformat(out[-1]) if out else datetime.fromtimestamp(report.stat().st_mtime).date()


def backtested(report: Path) -> datetime:
    """When the backtest ran: newest result file (trades, summary ...) in the folder. report.html alone is not used,
    because a UI-only rebuild rewrites it without rerunning the test."""
    out = [p.stat().st_mtime for p in report.parent.iterdir() if p.is_file() and p.name != "report.html"]
    return datetime.fromtimestamp(max(out) if out else report.stat().st_mtime)


def site_url() -> str:
    site = SITE / "site.json"
    return json.loads(site.read_text(encoding="utf-8"))["url"] if site.exists() else ""


def render(data: dict) -> str:
    """The site page (template + data). Same page for every host; hosts only change hrefs / parts in data."""
    blob = json.dumps(data, ensure_ascii=False, separators=(",", ":")).replace("</", "<\\/")
    return TEMPLATE.read_text(encoding="utf-8").replace("__DATA__", blob)


def main() -> int:
    data, files, missing = collect()
    (SITE / "index.html").write_text(render(data), encoding="utf-8")
    (SITE / "files.json").write_text(json.dumps(files, indent=1), encoding="utf-8")
    print(f"site/index.html: {len(data['reports'])} reports, {sum(r['mb'] for r in data['reports']):.1f} MB")
    if missing:
        print("site/reports.json me entry nahi hai (naam/type/result khaali dikhega):", ", ".join(missing))
    return 0


def collect() -> tuple[dict, dict, list]:
    """(page data, {published path: report file relative to ROOT}, folders missing in reports.json)."""
    meta = json.loads((SITE / "reports.json").read_text(encoding="utf-8"))
    rows, files, missing = [], {}, []
    for f in sorted(RESULTS.glob("[0-9]*_*/report.html")):
        folder = f.parent.name
        m = meta.get(folder)
        if m is None:
            missing.append(folder)
            title = re.search(r"<title>(.*?)</title>", f.read_text(encoding="utf-8")[:8000], re.S)
            m = {"name": title.group(1).strip() if title else folder, "type": "Baaki", "period": "", "result": "",
                 "net": None, "pf": None, "trades": None, "verdict": "study"}
        made, upd = first_added(f), datetime.fromtimestamp(f.stat().st_mtime)
        rows.append({"num": int(folder.split("_", 1)[0]), "folder": folder, **m, "made": str(min(made, upd.date())),
                     "updated": upd.strftime("%Y-%m-%d %H:%M"), "tested": backtested(f).strftime("%Y-%m-%d %H:%M"), "mb": round(f.stat().st_size / 1e6, 1),
                     "href": f"reports/{folder}.html", "hash": f"report-{int(folder.split('_', 1)[0])}"})
        files[f"reports/{folder}.html"] = str(f.relative_to(ROOT)).replace("\\", "/")
    data = {"reports": rows, "site": site_url(), "generated": datetime.now().strftime("%d %b %Y %H:%M")}
    return data, files, missing


if __name__ == "__main__":
    sys.exit(main())
