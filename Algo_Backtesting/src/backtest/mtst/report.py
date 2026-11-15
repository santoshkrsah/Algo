"""HTML report (new look, simple Hinglish) + PNG charts for the NIFTY multi-timeframe Supertrend backtest."""
import json
import re
from datetime import datetime
from pathlib import Path

import numpy as np
import pandas as pd

from backtest import viewer as viewer_assets
from backtest.mtst.config import ASSUMPTIONS, BASE, CONFIG, TITLE_SUFFIX
from backtest.report import ASSETS


def _clean(v):
    if isinstance(v, dict):
        return {str(k): _clean(x) for k, x in v.items()}
    if isinstance(v, (list, tuple)):
        return [_clean(x) for x in v]
    if isinstance(v, np.bool_):
        return bool(v)
    if isinstance(v, (pd.Timestamp, np.datetime64)):
        return str(v)[:19].replace("T", " ")
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


def records(df: pd.DataFrame) -> list:
    return _clean(df.astype(object).where(df.notna(), None).to_dict("records"))


def pngs(res, out: Path) -> list:
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    main = res["main"]
    pts = main["pts_day"].cumsum()
    eq = CONFIG["capital"] + main["rs_day"].cumsum()
    bh = CONFIG["capital"] + res["buy_hold"]["rs_day"].cumsum()
    split = res["ctx"]["split"]
    files = []
    fig, ax = plt.subplots(2, 1, figsize=(11, 6.5), sharex=True)
    ax[0].plot(eq.index, eq / 1e5, lw=1.6, label="Strategy (1% risk, compounding)")
    ax[0].plot(bh.index, bh / 1e5, lw=1.0, color="grey", label="Buy-and-hold NIFTY futures (1x)")
    ax[0].axvline(split, color="orange", ls="--", lw=1, label=f"fit | test split {split.date()}")
    ax[0].set_ylabel("Rs lakh"); ax[0].legend(loc="upper left", fontsize=8); ax[0].grid(alpha=.3)
    ax[0].set_title("NIFTY MTF Supertrend (daily 7x3 filter + 1h 7x3): account equity")
    ax[1].plot(pts.index, pts, lw=1.4, color="tab:blue"); ax[1].axvline(split, color="orange", ls="--", lw=1)
    ax[1].set_ylabel("net points / unit"); ax[1].grid(alpha=.3)
    fig.tight_layout(); p = out / "equity.png"; fig.savefig(p, dpi=110); plt.close(fig); files.append(p)
    peak = np.maximum.accumulate(eq.to_numpy())
    fig, ax = plt.subplots(figsize=(11, 3.2))
    ax.fill_between(eq.index, -(peak - eq.to_numpy()) / peak * 100, 0, color="tab:red", alpha=.35)
    ax.set_ylabel("drawdown %"); ax.grid(alpha=.3); ax.set_title("Drawdown from the equity high (Rs account)")
    fig.tight_layout(); p = out / "drawdown.png"; fig.savefig(p, dpi=110); plt.close(fig); files.append(p)
    x = main["df"]["net_pts"].to_numpy()
    fig, ax = plt.subplots(figsize=(8, 3.6))
    ax.hist(np.clip(x, -500, 1500), bins=50, color="tab:blue", alpha=.8)
    ax.axvline(0, color="k", lw=.8); ax.set_xlabel("net points per trade (clipped -500..1500)"); ax.set_ylabel("trades")
    ax.set_title(f"Trade returns: {len(x)} trades, win {np.mean(x > 0) * 100:.0f}%")
    fig.tight_layout(); p = out / "trade_hist.png"; fig.savefig(p, dpi=110); plt.close(fig); files.append(p)
    return files


def chart_series(P) -> dict:
    H = P.H
    t = H.start.astype("datetime64[m]").astype(np.int64)
    r = lambda a: [None if not np.isfinite(x) else round(float(x), 1) for x in a]
    return {"t": t.tolist(), "o": r(H.o), "h": r(H.h), "l": r(H.l), "c": r(H.c), "st": r(P.h_line), "dst": r(P.dline),
            "hd": P.hd.astype(int).tolist(), "dd": P.dd.astype(int).tolist()}


def write_html(res: dict, out_dir: Path, *, settings, rules, formulas, data_card) -> Path:
    out_dir = Path(out_dir)
    ctx, main = res["ctx"], res["main"]
    fut = ctx["M"]["fut"]
    P = main["P"]
    sized = main["sized"]
    df = main["df"]
    pts = main["pts_day"]
    cum = pts.cumsum()
    dd = np.maximum.accumulate(np.r_[0, cum.to_numpy()])[1:] - cum.to_numpy()
    eq_rs = CONFIG["capital"] + main["rs_day"].cumsum()
    bh = res["buy_hold"]
    bh_cum = bh["pts_day"].reindex(pts.index).fillna(0).cumsum()
    bh_rs = CONFIG["capital"] + bh["rs_day"].reindex(pts.index).fillna(0).cumsum()
    eqs = [[d.strftime("%Y-%m-%d"), round(float(c), 1), round(float(x), 1), round(float(e)), round(float(bc), 1), round(float(br))]
           for d, c, x, e, bc, br in zip(pts.index, cum.to_numpy(), dd, eq_rs.to_numpy(), bh_cum.to_numpy(), bh_rs.to_numpy())]
    rows = []
    for r in sized.itertuples(index=False):
        rows.append([str(r.sig_in)[:16], str(r.entry_dt)[:16], round(r.entry_raw, 1), str(r.sig_out)[:16], str(r.exit_dt)[:16], round(r.exit_raw, 1),
                     "LONG" if r.side > 0 else "SHORT", r.reason, round(r.gross_pts, 1), round(r.slip_pts, 1), round(r.cost_pts, 1), round(r.net_pts, 1),
                     int(r.rolls), round(r.days_held, 1), int(r.lot), int(r.lots), round(r.pnl_rs), int(r.k_in), int(r.k_out), round(r.sig_close, 1),
                     round(r.stop_lvl, 1), round(r.stop_dist, 1), round(r.risk_rs), round(r.equity_in), r.trend_regime, r.vix_regime, bool(r.capped)])
    mon = pd.DataFrame({"pts": pts, "rs": main["rs_day"]}).groupby(pts.index.to_period("M")).sum()
    mon_eq0 = (CONFIG["capital"] + main["rs_day"].cumsum().shift(1).fillna(0)).groupby(pts.index.to_period("M")).first()
    mon_trades = df.groupby(df["exit_dt"].dt.to_period("M")).size()
    rnd = res["random"]
    lo = min(np.nanmin(rnd["trend"]["sims_mean"]), np.nanmin(rnd["coin"]["sims_mean"]), rnd["trend"]["rule_mean"]) - 3
    hi = max(np.nanmax(rnd["trend"]["sims_mean"]), np.nanmax(rnd["coin"]["sims_mean"]), rnd["trend"]["rule_mean"]) + 3
    jul = sized[(sized.entry_dt >= "2026-06-25") & (sized.entry_dt < "2026-08-05")]
    rs = main["rs"]
    t_ok = sized[sized.lots > 0]
    var = res["variants"]
    data = {
        "title": f"NIFTY multi-timeframe Supertrend (daily + 1 ghanta) - {TITLE_SUFFIX}", "generated": datetime.now().strftime("%d %b %Y %H:%M"),
        "data_range": [str(fut.days[0]), str(fut.days[-1])], "split": str(ctx["split"].date()), "base": BASE, "capital": CONFIG["capital"],
        "verdict": {"pass": res["verdict"]["pass"], "text": res["verdict"]["text"], "checks": res["verdict"]["checks"]}, "warnings": res["warnings"],
        "settings": settings, "metrics": main["st"], "curve": main["curve"], "rs": rs,
        "equity": eqs, "hist": hist(df["net_pts"], -500, 1500, 50),
        "variants": records(var), "sizing": records(res["sizing"]), "regimes": records(res["regimes"]), "periods": records(res["periods"]),
        "yearly": records(res["yearly"]), "validation": records(res["validation"]), "video_values": res["video_values"], "video": CONFIG["video_trade"],
        "cost_check": res["cost_check"], "july": [[str(r.sig_in)[:16], "LONG" if r.side > 0 else "SHORT", round(r.entry_raw, 1), str(r.sig_out)[:16],
                                                    round(r.exit_raw, 1), round(r.net_pts, 1), int(r.lots), round(r.pnl_rs)] for r in jul.itertuples()],
        "grid": records(res["grid"]), "base_cell": _clean(res["base_cell"]), "is_pick": _clean(res["is_pick"]), "is_metrics": res["is_run"]["st"],
        "random": {k: {"p": rnd[k]["p"], "mean": rnd[k]["mean"], "p95": rnd[k]["p95"], "hist": hist(rnd[k]["sims_mean"], lo, hi, 40)} for k in ("trend", "coin")},
        "rule_mean": rnd["trend"]["rule_mean"], "n_rule": rnd["trend"]["n_rule"],
        "buy_hold": {"net_pts": bh["net_pts"], "pts_curve": bh["pts_curve"], "rs_curve": bh["rs_curve"], "net_rs": bh["net_rs"]},
        "monthly": [[str(p), round(float(r.pts), 1), round(float(r.rs)), round(float(r.rs / mon_eq0.get(p, CONFIG["capital"]) * 100), 2), int(mon_trades.get(p, 0))]
                    for p, r in mon.iterrows()],
        "trade_cols": ["sig_in", "entry_dt", "entry", "sig_out", "exit_dt", "exit", "side", "reason", "points", "slip", "charges", "net", "rolls", "days",
                       "lot", "lots", "pnl_rs", "k_in", "k_out", "sig_close", "stop", "stop_dist", "risk_rs", "equity_in", "trend", "vix", "capped"],
        "trades": rows, "chart": chart_series(P), "lot_sizes": CONFIG["lot_sizes"], "rolls": records(res["rolls"].tail(40).iloc[::-1]),
        "investment": {"capital": CONFIG["capital"], "risk_pct": CONFIG["risk_pct"], "margin": CONFIG["margin_per_lot"],
                       "avg_lots": rs["avg_lots"], "max_lots": rs["max_lots"], "avg_notional": rs["avg_notional"], "peak_notional": rs["peak_notional"],
                       "peak_margin": rs["peak_margin"], "skipped": rs["skipped"], "capped": rs["capped"], "avg_risk_pct": rs["avg_risk_pct"],
                       "max_risk_pct": rs["max_risk_pct"], "avg_stop": float(t_ok["stop_dist"].mean()) if len(t_ok) else 0},
        "rules": rules, "formulas": formulas, "assumptions": ASSUMPTIONS, "data_card": data_card,
    }
    css = re.search(r"<style>.*?</style>", (ASSETS / "report_template.html").read_text(encoding="utf-8"), re.S).group(0)
    tpl = viewer_assets.apply((ASSETS / "mtst_template.html").read_text(encoding="utf-8")).replace("__CSS__", css).replace("__TITLE__", data["title"])
    js = (ASSETS / "chart.umd.min.js").read_text(encoding="utf-8")
    blob = json.dumps(_clean(data), separators=(",", ":"), default=str).replace("</", "<\\/")
    path = out_dir / "report.html"
    path.write_text(tpl.replace("__CHARTJS__", js).replace("__DATA__", blob), encoding="utf-8")
    return path
