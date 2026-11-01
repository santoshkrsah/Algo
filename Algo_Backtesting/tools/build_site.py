"""Reports website: one page listing every results/NN_<name>/report.html (filter, sort). Each report opens in a new tab on
the site's own link + #report-NN, which shows that report full screen.

    python tools/build_site.py

Reads site/reports.json (name, type, period, one-line result per report folder; add an entry for every new report),
writes site/index.html and site/files.json (published path -> report file). Claude then publishes site/index.html as
the Claude Artifact in site/site.json ("url") with those files, so the website link never changes.
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


def main() -> int:
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
                     "updated": upd.strftime("%Y-%m-%d %H:%M"), "mb": round(f.stat().st_size / 1e6, 1),
                     "href": f"reports/{folder}.html", "hash": f"report-{int(folder.split('_', 1)[0])}"})
        files[f"reports/{folder}.html"] = str(f.relative_to(ROOT)).replace("\\", "/")
    site = SITE / "site.json"
    url = json.loads(site.read_text(encoding="utf-8"))["url"] if site.exists() else ""
    data = {"reports": rows, "site": url, "generated": datetime.now().strftime("%d %b %Y %H:%M")}
    blob = json.dumps(data, ensure_ascii=False, separators=(",", ":")).replace("</", "<\\/")
    (SITE / "index.html").write_text(TEMPLATE.read_text(encoding="utf-8").replace("__DATA__", blob), encoding="utf-8")
    (SITE / "files.json").write_text(json.dumps(files, indent=1), encoding="utf-8")
    print(f"site/index.html: {len(rows)} reports, {sum(r['mb'] for r in rows):.1f} MB")
    if missing:
        print("site/reports.json me entry nahi hai (naam/type/result khaali dikhega):", ", ".join(missing))
    return 0


if __name__ == "__main__":
    sys.exit(main())
