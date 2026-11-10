"""Podcast strategy framework: run one module end to end (grid -> plateau -> in-sample choice -> out-of-sample, random
control, checks) and write results/NN_podcast_<module>/ (report.html, trades.csv, grid.csv, ...).

    python tools/run_podcast.py --module B                 # full run (Telegram start / progress)
    python tools/run_podcast.py --module B --quick         # small grid + 50 random runs (code check)
    python tools/run_podcast.py --module B --notify        # after the site update: finish message + HTML to Telegram
    python tools/run_podcast.py --quality                  # data-quality report only

Data: ../Algo_Historical_Data/data/bars (python -m fyers_data.download_bars). Settings: src/backtest/podcast/config.py.
"""
import argparse
import importlib
import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

import pandas as pd  # noqa: E402

from backtest.config import result_dir  # noqa: E402
from backtest.podcast import quality, report, runner  # noqa: E402
from backtest.podcast.config import CONFIG, STOCKS  # noqa: E402

MODULES = {"A": "backtest.podcast.module_a", "B": "backtest.podcast.module_b", "C": "backtest.podcast.module_c", "D": "backtest.podcast.module_d"}
SITE_URL = "https://claude.ai/artifact/T1tmyG1FNSZLH7BXfC5nSM"


def send(text: str):
    from backtest.notify import send as tg
    try:
        tg(text)
    except Exception as e:  # noqa: BLE001
        print("telegram failed:", e)


def settings(mod, res) -> list:
    sl, p = res["slice"], res["params"]
    s = CONFIG["slippage"]
    chips = [["Module", f"{mod.KEY}: {mod.NAME}", 1], ["Instruments", "NIFTY + BANKNIFTY futures, 10 stocks " + ("futures" if mod.KEY == "B" else "cash (MIS)"), 0],
             ["Period", f"{res['data_range'][0]} -> {res['data_range'][1]}", 0], ["Fit / test", f"pehle 60% fit, {res['split'].date()} se test", 1]]
    chips += mod.setting_chips(sl, p)
    chips += [["Slippage", f"NIFTY {s['NIFTY']:g} pt, BANKNIFTY {s['BANKNIFTY']:g} pt, stocks {s['stock_pct']:g}% har fill", 1],
              ["Charges", "brokerage, STT (date ke hisaab se), NSE, SEBI, stamp, GST", 0],
              ["Capital", f"Rs {CONFIG['capital']:,} (compounding nahi)", 0], ["Risk / trade", f"{CONFIG['risk_pct']}% (doosra test {CONFIG['risk_pct_alt']}%)", 1],
              ["Qty", "risk / stop distance, units me", 0], ["Max position", f"{CONFIG['max_notional_x']:g}x capital", 0],
              ["Din ka loss limit", f"{CONFIG['daily_loss_cap_pct']}% (realised)", 0], ["Kill switch", f"{CONFIG['dd_kill_pct']:g}% DD -> {CONFIG['dd_restart_days']} din ruko (alag test)", 0],
              ["Futures roll", f"expiry se {CONFIG['data']['roll_days']} din pehle, gap P&L me nahi", 0],
              ["Data fixes", "HDFCBANK/TCS futures split, RELIANCE/ITC demerger, 15:30+ candles hataye", 0]]
    return chips


def data_card() -> dict:
    cov = pd.read_csv(CONFIG["data"]["bars_dir"] / "coverage.csv", parse_dates=["first", "last"])
    rows = []
    for n, g in cov.groupby("name", sort=False):
        d = g.set_index("tf")
        rows.append([n, f"{d.loc['1D', 'first']:%Y-%m-%d} -> {d.loc['1D', 'last']:%Y-%m-%d}" if "1D" in d.index else "-",
                     f"{d.loc['1m', 'first']:%Y-%m-%d} -> {d.loc['1m', 'last']:%Y-%m-%d} ({int(d.loc['1m', 'days']):,} din)" if "1m" in d.index else "-"])
    qp = CONFIG["data"]["bars_dir"] / "quality_report.csv"
    q = pd.read_csv(qp) if qp.exists() else quality.report()
    qs = quality.summary(q)
    return {"coverage_cols": ["Series", "Daily candles", "1-min candles (3/5/60 min inse bane)"], "coverage": rows,
            "quality": qs[["check", "datasets", "count", "handling"]].values.tolist(),
            "note": "Sab Fyers se (python -m fyers_data.download_bars). _F1 = near month futures, _F2 = next month; inse apni continuous futures bani (roll 3 din pehle). Bina suffix = index / stock cash."}


def run_module(key: str, quick: bool, workers: int, sims: int | None, log_path: Path | None, tg: bool):
    mod = importlib.import_module(MODULES[key])
    out = result_dir(mod.FOLDER)
    logf = open(log_path, "a", encoding="utf-8") if log_path else None
    t0 = time.time()
    last_tg = [time.time()]

    def log(msg: str):
        line = f"[{time.strftime('%H:%M:%S')}] Module {key}: {msg}"
        print(line, flush=True)
        if logf:
            logf.write(line + "\n")
            logf.flush()

    if quick:
        mod.quick()
        sims = 50 if sims is None else sims
    if tg:
        send(f"Module {key} ({mod.NAME}) backtest shuru: grid -> plateau check -> fit (pehle 60%) pe setting -> test (aakhri 40%) + 1,000 random run. Folder {out.name}.")
    res = runner.run(mod, out, log=log, workers=workers, sims=sims)
    res["data_range"] = [str(res["days"][0]), str(res["days"][-1])]
    pods = mod.podcast_rows(res["grid"]) if hasattr(mod, "podcast_rows") else []
    extra = {"podcast": pods}
    if hasattr(mod, "points"):
        extra["points"] = mod.points(res["taken"])
        (out / "points.json").write_text(json.dumps(extra["points"], indent=1), encoding="utf-8")
    path = report.write_html(res, mod, out, settings=settings(mod, res), formulas=mod.formulas(res["slice"], res["params"]), data_card=data_card(),
                             title=f"Podcast Module {key}: {mod.NAME}", extra=extra)
    log(f"report {path} ({path.stat().st_size / 1e6:.1f} MB) in {(time.time() - t0) / 60:.1f} min")
    if logf:
        logf.close()
    return res, out


def notify(key: str):
    """Finish message (key numbers + website links) and the HTML report to Telegram. Run after the site is published."""
    from backtest.notify import send_document
    mod = importlib.import_module(MODULES[key])
    out = result_dir(mod.FOLDER)
    s = json.loads((out / "summary.json").read_text(encoding="utf-8"))
    m, r = s["metrics"], s["random"]
    num = int(out.name.split("_", 1)[0])
    txt = (f"Module {key} ({mod.NAME}) backtest khatam - {'PASS' if s['verdict']['pass'] else 'FAIL'} (@ayushanand440)\n"
           f"Chuni setting (sirf fit period se): {s['label']}\n"
           f"Test {s['split']} se: {m['oos'].get('trades', 0)} trade, net Rs {m['oos'].get('net', 0):,.0f}, PF {m['oos'].get('pf', 0):.2f}, {m['oos'].get('exp_R', 0):+.3f}R/trade\n"
           f"Poora 2018-2026: {m['full'].get('trades', 0)} trade, net Rs {m['full'].get('net', 0):,.0f}, PF {m['full'].get('pf', 0):.2f}, max DD {m['full'].get('max_dd_pct', 0):.1f}%, "
           f"time DD {m['full'].get('time_dd_days', 0)} din\n"
           f"Random entry se behtar? p = {r['p']:.3f} ({'haan' if r['p'] < 0.05 else 'nahi'})\n"
           f"{s['verdict']['text']}\n"
           f"Reports website: {SITE_URL}\nYe report: {SITE_URL}#report-{num}")
    send(txt)
    send_document(out / "report.html", caption=f"Podcast Module {key}: {mod.NAME} (report {num:02d})")


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--module", choices=list(MODULES))
    ap.add_argument("--quick", action="store_true")
    ap.add_argument("--workers", type=int, default=CONFIG["workers"])
    ap.add_argument("--sims", type=int)
    ap.add_argument("--log", type=Path)
    ap.add_argument("--no-telegram", action="store_true")
    ap.add_argument("--notify", action="store_true")
    ap.add_argument("--quality", action="store_true")
    a = ap.parse_args()
    if a.quality:
        print(quality.summary(quality.report()).to_string(index=False))
        return 0
    if a.notify:
        notify(a.module)
        return 0
    if not a.module:
        ap.error("--module needed")
    try:
        run_module(a.module, a.quick, a.workers, a.sims, a.log, not a.no_telegram and not a.quick)
    except Exception as e:
        if not a.no_telegram and not a.quick:
            send(f"Module {a.module} backtest me error (@albert_max): {type(e).__name__}: {str(e)[:300]}")
        raise
    return 0


if __name__ == "__main__":
    sys.exit(main())
