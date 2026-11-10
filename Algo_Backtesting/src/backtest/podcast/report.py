"""HTML report (new look, simple Hinglish) for a podcast module run: runner.run(...) result -> results/NN_<name>/report.html.

Tabs: Saransh (all metrics together, equity + drawdown, R histogram, investment), Grid (plateau heatmaps), Checks
(risk / kill switch / stress / random control / long history), Saal-Mahine, Har instrument, Trade viewer, Saare trades,
Data (coverage + quality), Rules (+ Formulas, both with Copy karo).
"""
import json
import re
from datetime import datetime
from pathlib import Path

import numpy as np
import pandas as pd

from backtest import viewer as viewer_assets
from backtest.podcast.config import ASSUMPTIONS, CONFIG
from backtest.report import ASSETS

CANDLE_BUDGET = 7_500_000


def _clean(v):
    if isinstance(v, dict):
        return {str(k): _clean(x) for k, x in v.items()}
    if isinstance(v, (list, tuple)):
        return [_clean(x) for x in v]
    if isinstance(v, (np.bool_,)):
        return bool(v)
    if hasattr(v, "item"):
        v = v.item()
    if isinstance(v, float) and not np.isfinite(v):
        return None
    if isinstance(v, (pd.Timestamp, np.datetime64)):
        return str(v)[:19]
    return v


def _encode(b, i0: int, i1: int) -> str:
    """Candles i0..i1-1 -> 't0,o,h,l,c,dt,o,h,l,c...' : t in minutes since epoch (deltas), prices in paise relative."""
    t = (b.dt[i0:i1].astype("datetime64[m]").astype(np.int64))
    p = [np.rint(x[i0:i1] * 100).astype(np.int64) for x in (b.o, b.h, b.l, b.c)]
    dt = np.r_[t[0], np.diff(t)]
    prev = np.r_[0, p[3][:-1]]
    flat = np.column_stack([dt, p[0] - prev, p[1] - p[0], p[2] - p[0], p[3] - p[0]]).ravel()
    return ",".join(map(str, flat.tolist()))


def candles(res: dict, budget: float = CANDLE_BUDGET) -> tuple[dict, str]:
    """{instrument: encoded candles}: the whole series when it fits, else only windows around the taken trades (newest
    first) until the budget is used."""
    bars, tk = res["bars"], res["taken"]
    out, size = {}, 0
    for name, b in bars.items():
        out[name] = {"segs": [[0, _encode(b, 0, b.n)]] if b.n else []}
        size += len(out[name]["segs"][0][1]) if b.n else 0
    if size <= budget:
        return out, "full"
    out = {}
    per = budget / max(1, len(bars))                  # equal share per instrument, newest trades first
    for name, b in bars.items():
        t = tk[tk["inst"] == name].sort_values("entry_i")
        segs = []
        for r in t.itertuples(index=False):
            a, z = max(0, int(r.entry_i) - 60), min(b.n, int(r.exit_i) + 20)
            if segs and a <= segs[-1][1]:
                segs[-1][1] = max(segs[-1][1], z)
                continue
            segs.append([a, z])
        enc, used = [], 0
        for a, z in reversed(segs):
            s = _encode(b, a, z)
            if used + len(s) > per:
                break
            enc.append([a, s])
            used += len(s)
        out[name] = {"segs": sorted(enc)}
    return out, "windows"


def investment(tk: pd.DataFrame) -> dict:
    if not len(tk):
        return {}
    ev = pd.concat([pd.DataFrame({"t": pd.to_datetime(tk["entry_dt"]), "v": tk["notional"], "n": 1}),
                    pd.DataFrame({"t": pd.to_datetime(tk["exit_dt"]), "v": -tk["notional"], "n": -1})]).sort_values(["t", "n"])
    run_v, run_n = ev["v"].cumsum(), ev["n"].cumsum()
    return {"avg_position": float(tk["notional"].mean()), "biggest_position": float(tk["notional"].max()),
            "peak_deployed": float(run_v.max()), "max_open": int(run_n.max())}


def hist(values, bins) -> dict:
    v = np.asarray(values, float)
    v = v[np.isfinite(v)]
    c, e = np.histogram(np.clip(v, bins[0], bins[-1]), bins=bins)
    return {"edges": [round(x, 3) for x in e.tolist()], "counts": c.tolist()}


def per_instrument(tk: pd.DataFrame) -> list:
    rows = []
    for name, g in tk.groupby("inst"):
        n = g["net"]
        gl = -n[n <= 0].sum()
        rows.append([name, g["kind"].iloc[0], len(g), float((n > 0).mean() * 100), float(n.sum()), float(n[n > 0].sum() / gl) if gl > 0 else None,
                     float(g["R"].mean()), float(g.loc[g["side"] > 0, "net"].sum()), float(g.loc[g["side"] < 0, "net"].sum())])
    return rows


def grid_dims(mod, res) -> dict:
    """'<grid> :: <slice values joined by | >' -> numeric dims of that heatmap (they differ per slice in some modules)."""
    def js(v):                                   # same text as JavaScript's String(v)
        if isinstance(v, (bool, np.bool_)):
            return "true" if v else "false"
        if isinstance(v, float) and v.is_integer():
            return str(int(v))
        return str(v)
    out = {}
    for sl in mod.slices():
        out["1 :: " + " | ".join(js(v) for v in sl.values())] = mod.grid1(sl)[0]
    g2 = mod.grid2(res["slice"], res["params"])
    if g2:
        out["2 :: " + " | ".join(js(v) for v in res["slice"].values())] = g2[0]
    return out


def write_html(res: dict, mod, out_dir: Path, *, settings: list, formulas: list, data_card: dict, title: str, extra: dict | None = None) -> Path:
    out_dir = Path(out_dir)
    tk = res["taken"]
    cap = CONFIG["capital"]
    dp = res["day_pnl"]
    eq = cap + dp.cumsum()
    peak = np.maximum.accumulate(np.r_[cap, eq.to_numpy()])[1:]
    dd = (peak - eq.to_numpy()) / peak * 100
    grid = res["grid"]
    gcols = [c for c in grid.columns if c not in ("index",)]
    tcols = ["entry_dt", "inst", "kind", "side", "entry_raw", "stop_px", "target_px", "exit_dt", "exit_raw", "reason", "units", "notional",
             "gross", "slippage", "fees", "net", "R", "R_gross", "n_rolls", "entry_i", "exit_i", "entry_px", "exit_px"]
    trows = []
    for r in tk[tcols + ["legs"]].itertuples(index=False):
        b = res["bars"][r.inst]
        off = float(b.off[r.entry_i])
        trows.append([str(r.entry_dt)[:16], r.inst, r.kind, "LONG" if r.side > 0 else "SHORT", round(r.entry_raw, 2),
                      round(r.stop_px - off, 2) if np.isfinite(r.stop_px) else None, round(r.target_px - off, 2) if np.isfinite(r.target_px) else None,
                      str(r.exit_dt)[:16], round(r.exit_raw, 2), r.reason, int(r.units), round(r.notional), round(r.gross, 2), round(r.slippage, 2),
                      round(r.fees, 2), round(r.net, 2), round(r.R, 3) if np.isfinite(r.R) else None, round(r.R_gross, 3), int(r.n_rolls),
                      int(r.entry_i), int(r.exit_i), round(r.entry_px, 2), round(r.exit_px, 2), round(off, 2),
                      round(r.stop_px, 2) if np.isfinite(r.stop_px) else None, round(r.target_px, 2) if np.isfinite(r.target_px) else None,
                      [[str(x[5])[:16], round(x[2], 2), round(x[3], 3), x[4]] for x in r.legs]])
    tsize = len(json.dumps(_clean(trows), separators=(",", ":"), default=str))
    if tsize > 9_000_000:                                    # keep the page under 16 MB: per-leg detail only in trades.csv
        for row in trows:
            row[-1] = []
        tsize = len(json.dumps(_clean(trows), separators=(",", ":"), default=str))
    cand, cmode = candles(res, budget=max(1_500_000, 13_500_000 - tsize))
    rnd = res["random"]
    sims = rnd["sims"][np.isfinite(rnd["sims"])] if len(rnd["sims"]) else np.array([])
    lo = float(min(np.min(sims) if len(sims) else 0, rnd["rule_R"])) - 0.02
    hi = float(max(np.max(sims) if len(sims) else 0, rnd["rule_R"])) + 0.02
    m = res["metrics"]
    first, last = str(res["days"][0]), str(res["days"][-1])
    inv = investment(tk)
    data = {
        "title": title, "module": mod.KEY, "label": res["label"], "generated": datetime.now().strftime("%d %b %Y %H:%M"),
        "data_range": [first, last], "split": str(res["split"].date()), "capital": cap, "risk_pct": CONFIG["risk_pct"],
        "settings": settings, "verdict": {"pass": res["verdict"]["pass"], "text": res["verdict"]["text"], "checks": res["verdict"]["checks"]},
        "warnings": res["warnings"], "metrics": m, "stress": res["stress"], "variants": res["variants"],
        "random": {"rule_R": rnd["rule_R"], "rule_R_oos": rnd["rule_R_oos"], "p": rnd["p"], "mean": rnd["mean"], "p95": rnd["p95"], "n": int(len(sims)),
                   "hist": hist(sims, np.linspace(lo, hi, 41)) if len(sims) else None},
        "long": res["long"],
        "equity": [[d.strftime("%Y-%m-%d"), round(float(e), 2), round(float(x), 3)] for d, e, x in zip(eq.index, eq.to_numpy(), dd)],
        "hist_R": hist(tk["R"], np.linspace(-3, 4, 36)),
        "yearly": res["yearly"].to_dict("records"), "monthly": res["monthly"].to_dict("records"),
        "instruments": per_instrument(tk),
        "grid_cols": gcols, "grid": grid[gcols].astype(object).where(grid[gcols].notna(), None).values.tolist(),
        "grid_keys": list(res["slice"].keys()), "grid_dims": grid_dims(mod, res),
        "chosen": {**res["slice"], **res["params"]},
        "trade_cols": ["entry_dt", "inst", "kind", "side", "entry", "stop", "target", "exit_dt", "exit", "reason", "units", "notional", "gross",
                       "slippage", "fees", "net", "R", "R_gross", "rolls", "entry_i", "exit_i", "entry_adj", "exit_adj", "off", "stop_adj", "target_adj", "legs"],
        "trades": trows, "skipped": res["acc"]["skip_reason"].value_counts().to_dict(),
        "candles": cand, "candle_mode": cmode, "tf": res["slice"].get("tf", ""),
        "investment": {**inv, "capital": cap, "risk_rs": cap * CONFIG["risk_pct"] / 100, "risk_alt_rs": cap * CONFIG["risk_pct_alt"] / 100},
        "rules": mod.rules(res["slice"], res["params"], str(res["split"].date())), "formulas": formulas,
        "assumptions": ASSUMPTIONS, "data_card": data_card, "elapsed_min": res["elapsed_min"],
    }
    if extra:
        data.update(extra)
    css = re.search(r"<style>.*?</style>", (ASSETS / "report_template.html").read_text(encoding="utf-8"), re.S).group(0)
    tpl = viewer_assets.apply((ASSETS / "podcast_template.html").read_text(encoding="utf-8")).replace("__CSS__", css).replace("__TITLE__", title)
    js = (ASSETS / "chart.umd.min.js").read_text(encoding="utf-8")
    blob = json.dumps(_clean(data), separators=(",", ":"), default=str).replace("</", "<\\/")
    path = out_dir / "report.html"
    path.write_text(tpl.replace("__CHARTJS__", js).replace("__DATA__", blob), encoding="utf-8")
    return path
