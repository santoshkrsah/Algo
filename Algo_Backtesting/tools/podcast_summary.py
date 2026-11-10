"""Compare the four podcast modules (reads results/NN_podcast_<module>/summary.json + grid.csv).

    python tools/podcast_summary.py            # prints the table, writes results/PODCAST_SUMMARY.csv
"""
import json
import sys
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
RES = ROOT / "results"
MODS = {"A": "podcast_a_open_gap", "B": "podcast_b_ma_cross", "C": "podcast_c_swing_breakout", "D": "podcast_d_level_breakout"}


def row(key: str, folder: Path) -> dict:
    s = json.loads((folder / "summary.json").read_text(encoding="utf-8"))
    g = pd.read_csv(folder / "grid.csv")
    g1 = g[g["grid"] == 1]
    pl = g1[g1["status"] == "PLATEAU"]
    m, r = s["metrics"], s["random"]
    return {"module": key, "report": folder.name, "chosen (in-sample)": s["label"],
            "grid cells": len(g1), "% fit PF>1": round((g1["is_pf"] > 1).mean() * 100, 1), "% test PF>1": round((g1["oos_pf"] > 1).mean() * 100, 1),
            "% both": round(((g1["is_pf"] > 1) & (g1["oos_pf"] > 1)).mean() * 100, 1), "plateau cells": len(pl),
            "plateau cells PF>1 in test": int((pl["oos_pf"] > 1).sum()),
            "fit trades": m["is"].get("trades", 0), "fit PF": round(m["is"].get("pf", 0), 2), "fit R/trade": round(m["is"].get("exp_R", 0), 3),
            "test trades": m["oos"].get("trades", 0), "test PF": round(m["oos"].get("pf", 0), 2), "test R/trade": round(m["oos"].get("exp_R", 0), 3),
            "test net Rs": round(m["oos"].get("net", 0)), "full net Rs": round(m["full"].get("net", 0)), "max DD Rs": round(m["full"].get("max_dd_rs", 0)),
            "time DD days": m["full"].get("time_dd_days"), "random p": round(r["p"], 3), "stress test net Rs": round(s["stress"]["oos"].get("net", 0)),
            "verdict": "PASS" if s["verdict"]["pass"] else "FAIL"}


NAMES = {"A": ("Podcast A: Gap direction (open vs prev close)", "Intraday"), "B": ("Podcast B: MA crossover (daily / 1 ghanta)", "Swing"),
         "C": ("Podcast C: Dow swing breakout (3m / 5m)", "Intraday"), "D": ("Podcast D: S/R level breakout (futures, options data nahi)", "Intraday")}


def site_entries(df: pd.DataFrame):
    """Add / update the four reports in site/reports.json (name, type, period, one-line Hinglish result, net, pf, trades, verdict)."""
    p = ROOT / "site" / "reports.json"
    d = json.loads(p.read_text(encoding="utf-8"))
    for r in df.to_dict("records"):
        s = json.loads((RES / r["report"] / "summary.json").read_text(encoding="utf-8"))
        full = s["metrics"]["full"]
        name, typ = NAMES[r["module"]]
        res = (f"{r['grid cells']} setting ka grid: fit me {r['% fit PF>1']}% profit, test me {r['% test PF>1']}%, dono me {r['% both']}%. "
               f"Chuni (sirf fit se): {r['chosen (in-sample)']}. Test ({s['split']} se) {r['test trades']} trade, PF {r['test PF']}, "
               f"{r['test R/trade']:+.3f}R/trade, net Rs {r['test net Rs']:,.0f}. Random entry p = {r['random p']}. Kharche + slippage ke baad. {r['verdict']}")
        verdict = "profit" if r["verdict"] == "PASS" else ("study" if full.get("net", 0) > 0 else "loss")
        d[r["report"]] = {"name": name, "type": typ, "period": "Jan 2018 - Nov 2026" if r["module"] == "B" else "Feb 2018 - Nov 2026", "result": res,
                          "net": round(full.get("net", 0)), "pf": round(full.get("pf", 0), 2), "trades": int(full.get("trades", 0)), "verdict": verdict}
    p.write_text(json.dumps(d, indent=1, ensure_ascii=False), encoding="utf-8")
    print("site/reports.json updated:", ", ".join(df["report"]))


def main() -> int:
    rows = []
    for k, name in MODS.items():
        hits = sorted(RES.glob(f"[0-9]*_{name}"))
        if hits and (hits[-1] / "summary.json").exists():
            rows.append(row(k, hits[-1]))
    df = pd.DataFrame(rows)
    df.to_csv(RES / "PODCAST_SUMMARY.csv", index=False)
    with pd.option_context("display.width", 250, "display.max_colwidth", 80):
        print(df.T.to_string())
    if "--site" in sys.argv:
        site_entries(df)
    return 0


if __name__ == "__main__":
    sys.exit(main())
