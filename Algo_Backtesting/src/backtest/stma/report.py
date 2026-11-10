"""HTML report (new look, simple Hinglish) for the Supertrend + MA backtest (Big Bull Series Ep 94)."""
import json
import re
from datetime import datetime
from pathlib import Path

import numpy as np
import pandas as pd

from backtest import viewer as viewer_assets
from backtest.report import ASSETS
from backtest.stma import engine
from backtest.stma.config import ASSUMPTIONS, CONFIG, TITLE_SUFFIX


def _clean(v):
    if isinstance(v, dict):
        return {str(k): _clean(x) for k, x in v.items()}
    if isinstance(v, (list, tuple)):
        return [_clean(x) for x in v]
    if isinstance(v, np.bool_):
        return bool(v)
    if isinstance(v, (pd.Timestamp, np.datetime64)):
        return str(v)[:19]
    if hasattr(v, "item"):
        v = v.item()
    if isinstance(v, float) and not np.isfinite(v):
        return None
    return v


def hist(values, lo, hi, n=40) -> dict:
    v = np.asarray(values, float)
    v = v[np.isfinite(v)]
    c, e = np.histogram(np.clip(v, lo, hi), bins=np.linspace(lo, hi, n + 1))
    return {"edges": [round(x, 2) for x in e.tolist()], "counts": c.tolist()}


def series(sig, P) -> dict:
    """1-hour candles of the signal chart + Supertrend line + MA, compact (times in minutes since epoch)."""
    (line, up), ma = engine.indicators(sig, P["atr_n"], P["mult"], P["ma_type"], P["ma_len"])
    t = sig.start.astype("datetime64[m]").astype(np.int64)
    r = lambda a: np.round(a, 1).tolist()
    return {"t": t.tolist(), "o": r(sig.o), "h": r(sig.h), "l": r(sig.l), "c": r(sig.c), "st": [None if not np.isfinite(x) else round(float(x), 1) for x in line],
            "up": up.astype(int).tolist(), "ma": [None if not np.isfinite(x) else round(float(x), 1) for x in ma]}


def write_html(res: dict, out_dir: Path, *, settings: list, rules: list, formulas: list, data_card: dict, side: dict | None = None) -> Path:
    out_dir = Path(out_dir)
    P, fut = res["P"], res["fut"]
    sig = res["S"]["L1"]
    df = res["trades"]
    t, rs_day, rs_curve = res["eq_main"]
    pts = res["pts_day"]
    cum = pts.cumsum()
    dd = np.maximum.accumulate(np.r_[0, cum.to_numpy()])[1:] - cum.to_numpy()
    eq_rs = CONFIG["capital"] + rs_day.cumsum()
    bh = res["buy_hold"]
    bh_cum = bh["pts_day"].reindex(pts.index).fillna(0).cumsum()
    bh_rs = CONFIG["capital"] + bh["rs_day"].reindex(pts.index).fillna(0).cumsum()
    # trades: fills (futures, adjusted) + signal candle index for the viewer
    k_in = np.searchsorted(sig.end, df["sig_in"].to_numpy("datetime64[ns]"))
    k_out = np.searchsorted(sig.end, df["sig_out"].to_numpy("datetime64[ns]"))
    rows = []
    tt = t.set_index(t["entry_dt"]) if len(t) else t
    for n, r in enumerate(df.itertuples(index=False)):
        lots = int(tt.loc[r.entry_dt, "lots"]) if len(tt) and r.entry_dt in tt.index else 0
        prs = float(tt.loc[r.entry_dt, "pnl_rs"]) if len(tt) and r.entry_dt in tt.index else 0.0
        off_in = float(fut.off[r.i_in])
        rows.append([str(r.sig_in)[:16], str(r.entry_dt)[:16], round(r.entry_raw, 2), str(r.sig_out)[:16], str(r.exit_dt)[:16], round(r.exit_raw, 2),
                     "LONG" if r.side > 0 else "SHORT", r.reason, round(r.gross_u, 1), round(r.slip_pts, 1), round(r.cost_pts, 1), round(r.net_pts, 1),
                     int(r.rolls), round(r.days_held, 1), int(r.lot), lots, round(prs), int(k_in[n]), int(k_out[n]), round(off_in, 1)])
    m = res["metrics"]
    rnd = res["random"]
    lo = min(np.nanmin(rnd["any"]["sims_mean"]), np.nanmin(rnd["up"]["sims_mean"]), rnd["rule_mean"]) - 3
    hi = max(np.nanmax(rnd["any"]["sims_mean"]), np.nanmax(rnd["up"]["sims_mean"]), rnd["rule_mean"]) + 3
    mon = pd.DataFrame({"pts": pts, "rs": rs_day}).groupby(pts.index.to_period("M")).sum()
    mon_trades = df.groupby(df["exit_dt"].dt.to_period("M")).size()
    eqs = []
    eq_series = eq_rs.to_numpy()
    for d, c, x, e, bc, br in zip(pts.index, cum.to_numpy(), dd, eq_series, bh_cum.to_numpy(), bh_rs.to_numpy()):
        eqs.append([d.strftime("%Y-%m-%d"), round(float(c), 1), round(float(x), 1), round(float(e)), round(float(bc), 1), round(float(br))])
    g = res["grid"]
    gcols = [c for c in g.columns]
    data = {
        "title": f"NIFTY 1-hour Supertrend + 50 MA trend following - {TITLE_SUFFIX}", "generated": datetime.now().strftime("%d %b %Y %H:%M"),
        "data_range": [str(fut.days[0]), str(fut.days[-1])], "split": str(res["split"].date()), "P": P, "capital": CONFIG["capital"],
        "verdict": {"pass": res["verdict"]["pass"], "text": res["verdict"]["text"], "checks": res["verdict"]["checks"]}, "warnings": res["warnings"],
        "settings": settings, "metrics": m, "curve": res["curve"], "claims": res["claims"], "best": res["best"],
        "equity": eqs, "hist": hist(df["net_pts"], -800, 2000, 48),
        "variants": res["variants"].to_dict("records"), "equity_table": res["equity_table"].to_dict("records"), "rs_curve": rs_curve,
        "cas": res["cas"].to_dict("records"), "cas_start": str(res["cas_start"].date()) if res["cas_start"] is not None else None,
        "grid_cols": gcols, "grid": g.astype(object).where(g.notna(), None).values.tolist(),
        "random": {"rule_mean": rnd["rule_mean"], "n_rule": rnd["n_rule"],
                   **{k: {"p": rnd[k]["p"], "mean": rnd[k]["mean"], "p95": rnd[k]["p95"], "avg_trades": rnd[k]["avg_trades"],
                          "hist": hist(rnd[k]["sims_mean"], lo, hi, 40)} for k in ("any", "up")}},
        "buy_hold": {"net_pts": bh["net_pts"], "pts_curve": bh["pts_curve"], "rs_curve": bh["rs_curve"], "net_rs": bh["net_rs"]},
        "stress": res["stress"].to_dict("records"), "yearly": res["yearly"].to_dict("records"),
        "monthly": [[str(p), round(float(r.pts), 1), round(float(r.rs)), int(mon_trades.get(p, 0))] for p, r in mon.iterrows()],
        "trade_cols": ["sig_in", "entry_dt", "entry", "sig_out", "exit_dt", "exit", "side", "reason", "points", "slip", "charges", "net", "rolls", "days", "lot",
                       "lots", "pnl_rs", "k_in", "k_out", "off"],
        "trades": rows, "chart": series(sig, P), "lot_sizes": CONFIG["lot_sizes"],
        "investment": {"capital": CONFIG["capital"], "leverage": CONFIG["leverage_main"], "avg_lots": float(t.loc[t.lots > 0, "lots"].mean()) if (t.lots > 0).any() else 0,
                       "max_lots": int(t["lots"].max()) if len(t) else 0, "zero_lot": int((t["lots"] == 0).sum()) if len(t) else 0,
                       "peak_notional": float((t["lots"] * t["lot"] * t["entry_raw"]).max()) if len(t) else 0,
                       "avg_notional": float((t["lots"] * t["lot"] * t["entry_raw"])[t.lots > 0].mean()) if (t.lots > 0).any() else 0},
        "rules": rules, "formulas": formulas, "assumptions": ASSUMPTIONS, "data_card": data_card, "side": side or {},
    }
    css = re.search(r"<style>.*?</style>", (ASSETS / "report_template.html").read_text(encoding="utf-8"), re.S).group(0)
    tpl = viewer_assets.apply((ASSETS / "stma_template.html").read_text(encoding="utf-8")).replace("__CSS__", css).replace("__TITLE__", data["title"])
    js = (ASSETS / "chart.umd.min.js").read_text(encoding="utf-8")
    blob = json.dumps(_clean(data), separators=(",", ":"), default=str).replace("</", "<\\/")
    path = out_dir / "report.html"
    path.write_text(tpl.replace("__CHARTJS__", js).replace("__DATA__", blob), encoding="utf-8")
    return path
