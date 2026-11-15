"""End to end: data -> validation (video trade) -> main run -> grid (plateau) -> fit / test split -> variants, sizing,
controls, random entries, regimes, periods -> verdict -> files.

    ctx = run.context(); res = run.everything(ctx, out_dir, log=print)
"""
import json
import time

import numpy as np
import pandas as pd

from backtest.mtst import data, engine, evaluate as ev, money
from backtest.mtst.config import BASE, CONFIG, run_params
from backtest.stma import evaluate as sev


# ---------------------------------------------------------------------------------------------------------- context
def context(log=print) -> dict:
    t = time.time()
    M = data.load()
    Hs = {}
    for c in ("spot", "fut"):
        Hs[(c, "include")] = data.hourly(M, c, "include")
        Hs[(c, "drop")] = data.hourly(M, c, "drop")
        Hs[(c, "defer")] = Hs[(c, "include")]
    Ds = {c: data.daily(M, c) for c in ("spot", "fut")}
    fut = M["fut"]
    split = pd.Timestamp(fut.days[int(len(fut.days) * CONFIG["is_frac"])])
    span_is = (split - pd.Timestamp(fut.days[0])).days / 365.25
    span_oos = (pd.Timestamp(fut.days[-1]) - split).days / 365.25
    log(f"data loaded in {time.time() - t:.0f}s: futures 1m {fut.n:,} bars {fut.days[0]} -> {fut.days[-1]}; split {split.date()}")
    return {"M": M, "Hs": Hs, "Ds": Ds, "split": split, "span_is": span_is, "span_oos": span_oos, "start": CONFIG["data"]["start"]}


def trades(ctx, p: dict, slip=None, mode=None) -> tuple[pd.DataFrame, object]:
    P = engine.prepare(ctx["M"], ctx["Hs"], ctx["Ds"], p)
    df = engine.run(ctx["M"], P, p, ctx["start"])
    return money.apply_costs(df, ctx["M"]["fut"], slip, mode), P


def evaluate_run(ctx, p, slip=None, mode=None, risk=None, rounding=None, dd_aware=False, with_rs=True) -> dict:
    df, P = trades(ctx, p, slip, mode)
    st = ev.split_stats(df, ctx["split"], ctx["span_is"], ctx["span_oos"])
    out = {"p": p, "df": df, "P": P, "st": st}
    if len(df):
        pts_day = ev.daily_points(df, ctx["M"]["fut"])
        out["pts_day"] = pts_day
        out["curve"] = {"full": ev.curve_stats(pts_day), "is": ev.curve_stats(pts_day[pts_day.index < ctx["split"]]),
                        "oos": ev.curve_stats(pts_day[pts_day.index >= ctx["split"]])}
        if with_rs:
            sized = money.size(df, risk, rounding, dd_aware)
            rs, rs_day = ev.rupee_stats(sized, ctx["M"]["fut"])
            out.update({"sized": sized, "rs": rs, "rs_day": rs_day})
    return out


def row_of(r: dict, **tag) -> dict:
    st = r["st"]
    row = dict(tag)
    for part in ("is", "oos", "full"):
        s = st[part]
        row.update({f"{part}_trades": s.get("trades", 0), f"{part}_net_pts": s.get("net_pts", 0.0), f"{part}_pf": s.get("pf", np.nan),
                    f"{part}_pts_year": s.get("pts_per_year", 0.0), f"{part}_win": s.get("win_pct", np.nan), f"{part}_exp": s.get("exp_pts", np.nan)})
    f = st["full"]
    row.update({"trades_per_year": f.get("trades_per_year"), "long_pts": f.get("long_pts"), "short_pts": f.get("short_pts"),
                "avg_win": f.get("avg_win"), "avg_loss": f.get("avg_loss"), "best5_share": f.get("best5_share"),
                "max_dd_pts": r.get("curve", {}).get("full", {}).get("max_dd", np.nan), "time_dd_days": r.get("curve", {}).get("full", {}).get("time_dd_days", np.nan)})
    if "rs" in r:
        rs = r["rs"]
        row.update({"net_rs": rs["net_rs"], "cagr_pct": rs.get("cagr_pct"), "max_dd_pct": rs.get("max_dd_pct"), "end_rs": rs.get("end")})
    return row


# ---------------------------------------------------------------------------------------------------------- validation
def validation(ctx) -> pd.DataFrame:
    """Every chart / ATR / entry / last-candle / fill combination at 7 x 3: the short trade of 2 Sep 2026 vs the video."""
    V = CONFIG["video_trade"]
    fut = ctx["M"]["fut"]
    rows = []
    for chart in ("spot", "fut"):
        for atr in ("rma", "sma"):
            for entry in ("E1", "E2"):
                for last in ("include", "drop"):
                    for fill in ("close", "next"):
                        p = run_params(chart=chart, atr=atr, entry=entry, last=last, fill=fill)
                        df, P = trades(ctx, p)
                        H = P.H
                        cand = df[(df.side == V["side"]) & (df.sig_in >= "2026-09-01") & (df.sig_in < "2026-09-08")]
                        # real price on the signal chart: futures chart prices are back-adjusted -> minus the offset then
                        off = (lambda k: float(fut.off[H.fill_close[k]]) if (chart == "fut" and H.fill_close[k] >= 0) else 0.0)
                        dflip = None
                        dd = P.d_dir_all
                        days = P.D.day
                        sel = np.nonzero((days >= np.datetime64("2026-08-25")) & (days <= np.datetime64("2026-09-10")))[0]
                        for i in sel:
                            if dd[i] == -1 and dd[i - 1] == 1:
                                dflip = str(days[i])
                        r = {"chart": chart, "atr": atr, "entry": entry, "last": last, "fill": fill, "daily_flip": dflip}
                        if len(cand):
                            t = cand.iloc[0]
                            ki, ko = int(t.k_in), int(t.k_out)
                            ein = float(H.c[ki] - off(ki))
                            stp = float(t.stop_lvl - off(ki))
                            ex = float(H.c[ko] - off(ko))
                            r.update({"signal_candle": str(H.start[ki])[:16].replace("T", " "), "entry_close": ein, "stop": stp, "stop_dist": abs(ein - stp),
                                      "exit_candle": str(H.start[ko])[:16].replace("T", " "), "exit_close": ex, "chart_points": V["side"] * (ex - ein),
                                      "fill_in": float(t.entry_raw), "fill_out": float(t.exit_raw), "fut_points": float(t.gross_pts), "net_pts": float(t.net_pts),
                                      "reason": t.reason})
                            miss = abs(ein - V["entry"]) + abs(stp - V["stop"]) + abs(ex - V["exit"])
                            miss += 0 if r["signal_candle"] == V["entry_time"] else 300
                            miss += 0 if r["exit_candle"] == V["exit_time"] else 300
                            r["miss_score"] = miss
                        else:
                            r["miss_score"] = np.inf
                        rows.append(r)
    return pd.DataFrame(rows).sort_values("miss_score").reset_index(drop=True)


def video_values(ctx) -> dict:
    """The speaker's setup (spot, RMA 7 x 3, E1): Supertrend values on the key candles, real prices."""
    p = run_params()
    _, P = trades(ctx, p)
    H = P.H
    out = []
    m = (H.day >= np.datetime64("2026-09-01")) & (H.day <= np.datetime64("2026-09-03")) | (H.day == np.datetime64("2026-09-21")) | (H.day == np.datetime64("2026-09-18"))
    for k in np.nonzero(m)[0]:
        out.append([str(H.start[k])[:16].replace("T", " "), round(float(H.c[k]), 2), round(float(P.h_line[k]), 2), int(P.hd[k]), round(float(P.dline[k]), 2), int(P.dd[k])])
    return {"cols": ["candle", "close", "1h Supertrend", "1h dir", "daily Supertrend (last completed)", "daily dir"], "rows": out}


# ---------------------------------------------------------------------------------------------------------- grid
def grid(ctx, log=print) -> pd.DataFrame:
    G = CONFIG["grid"]
    rows = []
    for hn in G["n"]:
        for hm in G["mult"]:
            for dn in G["n"]:
                for dm in G["mult"]:
                    p = run_params(h_n=hn, h_mult=hm, d_n=dn, d_mult=dm)
                    r = evaluate_run(ctx, p, with_rs=False)
                    rows.append(row_of(r, h_n=hn, h_mult=hm, d_n=dn, d_mult=dm))
        log(f"grid: 1h ATR {hn} done ({len(rows)} / 256)")
    g = pd.DataFrame(rows)
    return ev.plateau(g)


# ---------------------------------------------------------------------------------------------------------- variants
VARIANTS = [("Main (video setup)", {}, {}), ("Futures chart", {"chart": "fut"}, {}), ("SMA ATR", {"atr": "sma"}, {}),
            ("Entry E2 (sirf 1h flip)", {"entry": "E2"}, {}), ("Fill: signal candle close", {"fill": "close"}, {}),
            ("Stop: daily band", {"sl": "daily"}, {}), ("Exit: intrabar touch", {"exit": "touch"}, {}),
            ("Exit: + daily flip", {"exit": "close_daily"}, {}), ("15:15 candle: defer (close fill)", {"last": "defer", "fill": "close"}, {}),
            ("15:15 candle: drop", {"last": "drop"}, {}), ("Long only", {"sides": "long"}, {}), ("Short only", {"sides": "short"}, {}),
            ("No daily filter (1h alone)", {"sides": "nofilter"}, {}), ("Slippage 1 pt", {}, {"slip": 1.0}), ("Slippage 5 pt", {}, {"slip": 5.0}),
            ("Dated STT rates", {}, {"mode": "dated"}), ("Futures chart + SMA ATR", {"chart": "fut", "atr": "sma"}, {}),
            ("Touch exit + close fill", {"exit": "touch", "fill": "close"}, {}), ("Long only, no daily filter", {"sides": "nofilter_long"}, {})]


def variants(ctx, log=print) -> tuple[pd.DataFrame, dict]:
    rows, keep = [], {}
    for name, over, kw in VARIANTS:
        if over.get("sides") == "nofilter_long":
            p = run_params(sides="nofilter")
            r = evaluate_run(ctx, p, **kw)
            df = r["df"][r["df"].side > 0].reset_index(drop=True)
            r = _rerun_on(ctx, df, p)
        else:
            p = run_params(**over)
            r = evaluate_run(ctx, p, **kw)
        rows.append(row_of(r, variant=name, **{k: p[k] for k in BASE}, slip=kw.get("slip", CONFIG["slippage"]), rates=kw.get("mode", CONFIG["rates_mode"])))
        keep[name] = r
    log(f"variants done ({len(rows)})")
    return pd.DataFrame(rows), keep


def _rerun_on(ctx, df, p) -> dict:
    st = ev.split_stats(df, ctx["split"], ctx["span_is"], ctx["span_oos"])
    out = {"p": p, "df": df, "st": st}
    if len(df):
        pts_day = ev.daily_points(df, ctx["M"]["fut"])
        out["pts_day"] = pts_day
        out["curve"] = {"full": ev.curve_stats(pts_day)}
        sized = money.size(df)
        rs, rs_day = ev.rupee_stats(sized, ctx["M"]["fut"])
        out.update({"sized": sized, "rs": rs, "rs_day": rs_day})
    return out


def sizing_table(ctx, df) -> pd.DataFrame:
    rows = []
    for risk in [0.5, CONFIG["risk_pct"], 2.0]:
        for rnd in ("round", "floor"):
            for dda in (False, True):
                s = money.size(df, risk, rnd, dda)
                rs, _ = ev.rupee_stats(s, ctx["M"]["fut"])
                rows.append({"risk_pct": risk, "rounding": rnd, "dd_aware": dda, **{k: rs.get(k) for k in
                             ("net_rs", "end", "cagr_pct", "max_dd_pct", "max_dd", "time_dd_days", "trades_taken", "skipped", "capped", "dd_half",
                              "avg_lots", "max_lots", "avg_risk_pct", "max_risk_pct", "win_pct_rs", "pf_rs", "exp_rs", "ruin_date")}})
    return pd.DataFrame(rows)


# ---------------------------------------------------------------------------------------------------------- periods
def periods(ctx, main, bh) -> pd.DataFrame:
    df, sized, pts_day, rs_day = main["df"], main["sized"], main["pts_day"], main["rs_day"]
    cap = CONFIG["capital"]
    rows = []
    for name, a, b in CONFIG["periods"]:
        a_, b_ = pd.Timestamp(a), pd.Timestamp(b)
        m = (pts_day.index >= a_) & (pts_day.index <= b_)
        eq_start = cap + rs_day[rs_day.index < a_].sum()
        tr = df[(df.entry_dt >= a_) & (df.entry_dt <= b_ + pd.Timedelta(days=1))]
        fresh = money.size(tr) if len(tr) else tr
        fresh_net = float(fresh["pnl_rs"].sum()) if len(tr) else 0.0
        seg = pts_day[m].cumsum()
        dd_pts = float((np.maximum.accumulate(np.r_[0, seg.to_numpy()])[1:] - seg.to_numpy()).max()) if m.any() else 0.0
        seg_rs = eq_start + rs_day[m].cumsum()
        dd_rs = float(((np.maximum.accumulate(np.r_[eq_start, seg_rs.to_numpy()])[1:] - seg_rs.to_numpy()) /
                       np.maximum.accumulate(np.r_[eq_start, seg_rs.to_numpy()])[1:]).max() * 100) if m.any() else 0.0
        bm = (bh["pts_day"].index >= a_) & (bh["pts_day"].index <= b_)
        rows.append({"period": name, "from": a, "to": b, "trades": len(tr), "wins": int((tr.net_pts > 0).sum()) if len(tr) else 0,
                     "net_pts": float(pts_day[m].sum()), "max_dd_pts": dd_pts, "net_rs_main": float(rs_day[m].sum()),
                     "return_pct_main": float(rs_day[m].sum() / eq_start * 100), "max_dd_pct_main": dd_rs,
                     "fresh_net_rs": fresh_net, "fresh_return_pct": fresh_net / cap * 100, "buy_hold_pts": float(bh["pts_day"][bm].sum()),
                     "buy_hold_pct": float(bh["rs_day"][bm].sum() / (cap + bh["rs_day"][bh["rs_day"].index < a_].sum()) * 100)})
    return pd.DataFrame(rows)


# ---------------------------------------------------------------------------------------------------------- everything
def everything(ctx, out, log=print) -> dict:
    t0 = time.time()
    fut = ctx["M"]["fut"]
    res = {"ctx": ctx}
    res["validation"] = validation(ctx)
    res["video_values"] = video_values(ctx)
    best = res["validation"].iloc[0]
    log(f"validation: best match {best.chart}/{best.atr}/{best.entry}/{best.last}/{best.fill} (miss {best.miss_score:.0f} pts)")
    main = evaluate_run(ctx, run_params())
    main["sized"] = ev.regimes(main["sized"], main["P"], ctx["M"]["vix"])
    res["main"] = main
    s = main["st"]["full"]
    log(f"main (video setup): {s['trades']} trades, net {s['net_pts']:,.0f} pts/unit, PF {s['pf']:.2f}, Rs {main['rs']['net_rs']:,.0f}, CAGR {main['rs']['cagr_pct']:.1f}%")
    res["grid"] = grid(ctx, log)
    g = res["grid"]
    me = g[(g.h_n == BASE["h_n"]) & (g.h_mult == BASE["h_mult"]) & (g.d_n == BASE["d_n"]) & (g.d_mult == BASE["d_mult"])].iloc[0]
    res["base_cell"] = me.to_dict()
    okc = g[g.status == "PLATEAU"]
    pick = (okc if len(okc) else g[g.status != "FEW TRADES"]).sort_values(["plateau_score", "is_trades"], ascending=False).iloc[0]
    res["is_pick"] = pick.to_dict()
    Pp = {k: (int(pick[k]) if k.endswith("_n") else float(pick[k])) for k in ev.DIMS}
    res["is_run"] = evaluate_run(ctx, run_params(**Pp))
    log(f"fit-chosen: 1h {Pp['h_n']}x{Pp['h_mult']:g}, daily {Pp['d_n']}x{Pp['d_mult']:g} ({pick.status}, score {pick.plateau_score:.2f}); "
        f"test PF {res['is_run']['st']['oos'].get('pf', float('nan')):.2f}")
    res["variants"], res["var_runs"] = variants(ctx, log)
    res["sizing"] = sizing_table(ctx, main["df"])
    bh = sev.buy_hold(fut, 1.0, CONFIG["capital"], CONFIG["slippage"])
    res["buy_hold"] = {"net_pts": bh["net_pts"], "pts_curve": ev.curve_stats(bh["pts_day"]), "rs_curve": ev.curve_stats(bh["rs_day"], CONFIG["capital"]),
                       "pts_day": bh["pts_day"], "rs_day": bh["rs_day"], "net_rs": float(bh["rs_day"].sum())}
    rng = np.random.default_rng(CONFIG["seed"])
    n_rule, mean_rule = len(main["df"]), float(main["df"]["net_pts"].mean())
    res["random"] = {k: ev.random_control(ctx["M"], main["P"], main["p"], ctx["start"], n_rule, mean_rule, k, CONFIG["random_sims"], rng) for k in ("trend", "coin")}
    log(f"random: p(trend dir) = {res['random']['trend']['p']:.3f}, p(coin) = {res['random']['coin']['p']:.3f}")
    res["regimes"] = ev.regime_table(main["sized"])
    res["periods"] = periods(ctx, main, res["buy_hold"])
    res["yearly"] = sev.yearly(main["df"], main["pts_day"], main["rs_day"], CONFIG["capital"])
    res["rolls"] = data.roll_log()
    res["cost_check"] = money.video_cost_check()
    res["warnings"], res["verdict"] = judge(res)
    res["elapsed_min"] = (time.time() - t0) / 60
    save(res, out)
    log(f"done in {res['elapsed_min']:.1f} min: {res['verdict']['text']}")
    return res


def judge(res) -> tuple[list, dict]:
    main, g, V = res["main"], res["grid"], res["variants"].set_index("variant")
    s, oos = main["st"]["full"], main["st"]["oos"]
    w = []
    if s["trades"] < CONFIG["min_trades_warn"]:
        w.append(f"Sirf {s['trades']} trade (< {CONFIG['min_trades_warn']}).")
    if oos.get("trades", 0) < CONFIG["min_trades_warn"]:
        w.append(f"Test period me sirf {oos.get('trades', 0)} trade (< {CONFIG['min_trades_warn']}): test ka nateeja kam bharosemand.")
    share = (g.status == "PLATEAU").mean() * 100
    w.append(f"256 grid settings me se {int((g.status == 'PLATEAU').sum())} ({share:.0f}%) PLATEAU, {int((g.full_net_pts > 0).sum())} poore period me profit, "
             f"{int((g.oos_net_pts > 0).sum())} test period me profit.")
    if np.isfinite(s.get("best5_share", np.nan)) and s["best5_share"] > 100:
        w.append(f"Sabse acche 5% trades = kul net points ka {s['best5_share']:.0f}%: baaki 95% trades milke loss me. Kuch bade trend miss hue to poora saal kharab.")
    nf = V.loc["No daily filter (1h alone)"]
    filt_adds = (main["st"]["full"]["exp_pts"] > nf["full_exp"]) and (oos.get("exp_pts", -1e9) > nf["oos_exp"])
    if not filt_adds:
        w.append("Daily filter ka fayda pakka nahi: bina filter (sirf 1h Supertrend) ki expectancy kisi period me barabar ya behtar.")
    r = res["random"]["trend"]
    if r["p"] >= 0.05:
        w.append(f"Random entry (1h trend ki direction me, wahi exit) bhi lagbhag itna hi deti hai (p = {r['p']:.2f}): entry timing ka edge saabit nahi.")
    rs = main["rs"]
    if rs.get("capped", 0):
        w.append(f"{rs['capped']} trades me lots margin (Rs {CONFIG['margin_per_lot']:,}/lot) ki wajah se kam kiye: chhota stop = bahut lots.")
    checks = [("Kharche + slippage ke baad poore period me profit (points)", s["net_pts"] > 0),
              ("Test period (aakhri 40%) me profit, PF > 1 (video setup 7x3 / 7x3)", oos.get("net_pts", 0) > 0 and oos.get("pf", 0) > 1),
              ("Video setup PLATEAU pe (aas-paas ki settings bhi fit me profit)", res["base_cell"]["status"] == "PLATEAU"),
              ("Fit se chuni setting test period me bhi profit", res["is_run"]["st"]["oos"].get("net_pts", 0) > 0),
              ("Random entry (1h direction, wahi exit) se behtar (p < 0.05)", r["p"] < 0.05),
              ("Daily filter fayda deta hai (expectancy > bina filter, poora + test)", bool(filt_adds)),
              ("Slippage 5 pt pe bhi poore period me profit", V.loc["Slippage 5 pt", "full_net_pts"] > 0),
              (f"Kam se kam {CONFIG['min_trades_warn']} trade", s["trades"] >= CONFIG["min_trades_warn"])]
    ok = all(c for _, c in checks)
    return w, {"pass": ok, "checks": checks, "text": ("PASS: saare checks paar." if ok else "FAIL: " + "; ".join(n for n, c in checks if not c) + " - nahi hua.")}


def save(res, out):
    out.mkdir(parents=True, exist_ok=True)
    main = res["main"]
    res["validation"].to_csv(out / "validation.csv", index=False)
    res["grid"].to_csv(out / "grid.csv", index=False)
    res["variants"].to_csv(out / "variants.csv", index=False)
    res["sizing"].to_csv(out / "sizing.csv", index=False)
    res["regimes"].to_csv(out / "regimes.csv", index=False)
    res["periods"].to_csv(out / "periods.csv", index=False)
    res["yearly"].to_csv(out / "yearly.csv", index=False)
    res["rolls"].to_csv(out / "rolls.csv", index=False)
    t = main["sized"].copy()
    t["direction"] = np.where(t.side > 0, "LONG", "SHORT")
    log = pd.DataFrame({"signal_candle_close": t.sig_in, "entry_time": t.entry_dt, "entry_price": t.entry_raw.round(2), "exit_signal_close": t.sig_out,
                        "exit_time": t.exit_dt, "exit_price": t.exit_raw.round(2), "direction": t.direction, "signal_close": t.sig_close.round(2),
                        "initial_stop": t.stop_lvl.round(2), "stop_distance_pts": t.stop_dist.round(2), "lot_size": t.lot, "lots": t.lots,
                        "risk_rs": t.risk_rs.round(0), "exit_reason": t.reason, "points": t.gross_pts.round(2), "slippage_pts": t.slip_pts.round(2),
                        "charges_pts": t.cost_pts.round(2), "net_points": t.net_pts.round(2), "charges_rs_per_lot": t.cost_rs_lot.round(2),
                        "net_rupees": t.pnl_rs.round(0), "equity_before": t.equity_in.round(0), "rolls": t.rolls, "holding_days": t.days_held.round(2),
                        "skip": t.skip, "lots_capped_by_margin": t.capped, "daily_flips_60d": t.daily_flips, "trend_regime": t.trend_regime,
                        "vix_prev": t.vix_prev.round(2), "vix_regime": t.vix_regime})
    log.to_csv(out / "trades.csv", index=False)
    pd.DataFrame({"date": main["pts_day"].index.strftime("%Y-%m-%d"), "pts_day": main["pts_day"].to_numpy(), "pts_cum": main["pts_day"].cumsum().to_numpy(),
                  "rs_day": main["rs_day"].to_numpy(), "equity_rs": CONFIG["capital"] + main["rs_day"].cumsum().to_numpy()}).to_csv(out / "equity.csv", index=False)
    pd.DataFrame({k + "_" + m: res["random"][k]["sims_" + m] for k in ("trend", "coin") for m in ("mean", "total")}).to_csv(out / "random.csv", index=False)
    clean = lambda o: o.item() if hasattr(o, "item") else str(o)
    summ = {"split": str(res["ctx"]["split"].date()), "base": BASE, "metrics": main["st"], "curve": main["curve"], "rs": main["rs"],
            "is_pick": res["is_pick"], "is_metrics": res["is_run"]["st"], "base_cell": res["base_cell"], "cost_check": res["cost_check"],
            "random": {k: {kk: vv for kk, vv in v.items() if not isinstance(vv, np.ndarray)} for k, v in res["random"].items()},
            "buy_hold": {k: v for k, v in res["buy_hold"].items() if not isinstance(v, pd.Series)},
            "warnings": res["warnings"], "verdict": {"pass": res["verdict"]["pass"], "text": res["verdict"]["text"],
                                                     "checks": [[n, bool(c)] for n, c in res["verdict"]["checks"]]}}
    (out / "summary.json").write_text(json.dumps(summ, indent=1, default=clean), encoding="utf-8")
