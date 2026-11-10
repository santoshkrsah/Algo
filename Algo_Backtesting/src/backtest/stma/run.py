"""Module A end to end: grid (plateau) -> in-sample choice -> out-of-sample, every variant, controls, claims, files.

    res = run.module_a(out_dir, log=print)
"""
import json
import time

import numpy as np
import pandas as pd

from backtest.podcast import data as pdata
from backtest.stma import bars, engine, evaluate as ev
from backtest.stma.config import CONFIG

KEYS = ["last_candle", "exit_mode", "sides"]
DIMS = ["atr_n", "mult", "ma_len"]


def vix_daily() -> pd.Series:
    v = pdata.read("INDIAVIX", "1D")
    v = v[v["close"] > 0]
    return v.set_index(v["datetime"].dt.normalize())["close"]


def split_day(fut) -> pd.Timestamp:
    return pd.Timestamp(fut.days[int(len(fut.days) * CONFIG["is_frac"])])


def run_cell(sig, fut, vix, p: dict, sides: str, exit_mode: str, cache: dict, instrument="F", slip=None, rates=None, entries=None) -> pd.DataFrame:
    (line, up), ma = engine.indicators(sig, p["atr_n"], p["mult"], p["ma_type"], p["ma_len"], cache)
    tr = engine.run(sig, fut, line, up, ma, sides, exit_mode, CONFIG["short_size"], entries=entries)
    return ev.apply_costs(ev.trades_frame(tr, sig, fut, vix), fut, instrument, slip, rates)


def split_stats(df: pd.DataFrame, split: pd.Timestamp, span_is: float, span_oos: float) -> dict:
    e = df["entry_dt"] if len(df) else pd.Series(dtype="datetime64[ns]")
    return {"is": ev.stats(df[e < split], span_is) if len(df) else {"trades": 0},
            "oos": ev.stats(df[e >= split], span_oos) if len(df) else {"trades": 0},
            "full": ev.stats(df, span_is + span_oos) if len(df) else {"trades": 0}}


def plateau(g: pd.DataFrame, value: str = "is_pf") -> pd.DataFrame:
    """Status per cell: PLATEAU (cell + every neighbour one step away in atr_n / mult / ma_len, same MA type, fit PF > 1),
    OVERFIT (cell > 1 but a neighbour not), LOSS, FEW TRADES. plateau_score = lowest PF among them."""
    g = g.copy()
    lv = {d: sorted(g[d].unique()) for d in DIMS}
    pos = {d: {v: i for i, v in enumerate(lv[d])} for d in DIMS}
    key = {tuple(r[c] for c in KEYS + ["ma_type"] + DIMS): i for i, r in g.iterrows()}
    sc, st = [], []
    for i, r in g.iterrows():
        vals = [r[value]]
        for d in DIMS:
            for s in (-1, 1):
                j = pos[d][r[d]] + s
                if 0 <= j < len(lv[d]):
                    k = tuple(r[c] for c in KEYS + ["ma_type"]) + tuple(lv[x][j] if x == d else r[x] for x in DIMS)
                    if k in key:
                        vals.append(g.loc[key[k], value])
        v = np.nan_to_num(np.array(vals, float), nan=0.0, posinf=9.99)
        sc.append(float(v.min()))
        if r["is_trades"] < CONFIG["min_is_trades"]:
            st.append("FEW TRADES")
        elif not r[value] > 1:
            st.append("LOSS")
        elif (v > 1).all():
            st.append("PLATEAU")
        else:
            st.append("OVERFIT")
    g["plateau_score"], g["status"] = sc, st
    return g


def module_a(out, log=print) -> dict:
    t0 = time.time()
    fut, spot = bars.load()
    S = bars.signals(fut, spot)
    vix = vix_daily()
    split = split_day(fut)
    span_is = (split - pd.Timestamp(fut.days[0])).days / 365.25
    span_oos = (pd.Timestamp(fut.days[-1]) - split).days / 365.25
    G = CONFIG["grid"]
    cells = [{"atr_n": a, "mult": m, "ma_type": t, "ma_len": n} for a in G["atr_n"] for m in G["mult"] for t in G["ma_type"] for n in G["ma_len"]]
    rows, caches = [], {k: {} for k in S}
    for L in CONFIG["last_candle"]:
        for ex in CONFIG["exit_mode"]:
            for sd in CONFIG["sides"]:
                for p in cells:
                    df = run_cell(S[L], fut, vix, p, sd, ex, caches[L])
                    st = split_stats(df, split, span_is, span_oos)
                    row = {"last_candle": L, "exit_mode": ex, "sides": sd, **p}
                    for part in ("is", "oos", "full"):
                        s = st[part]
                        row.update({f"{part}_trades": s.get("trades", 0), f"{part}_pf": s.get("pf", np.nan), f"{part}_pts": s.get("net_pts", 0.0),
                                    f"{part}_pts_year": s.get("pts_per_year", 0.0), f"{part}_win": s.get("win_pct", np.nan)})
                    rows.append(row)
        log(f"grid {L} done ({len(rows)} runs)")
    grid = pd.concat([plateau(g) for _, g in pd.DataFrame(rows).groupby(KEYS, sort=False)], ignore_index=True)
    main = grid[(grid.last_candle == "L1") & (grid.exit_mode == "close") & (grid.sides == "long")]
    ok = main[main.status == "PLATEAU"]
    best = (ok if len(ok) else main[main.status != "FEW TRADES"]).sort_values(["plateau_score", "is_trades"], ascending=False).iloc[0]
    P = {"atr_n": int(best.atr_n), "mult": float(best.mult), "ma_type": best.ma_type, "ma_len": int(best.ma_len)}
    log(f"chosen (fit only): ATR {P['atr_n']} x {P['mult']:g}, {P['ma_type']} {P['ma_len']} | fit PF {best.is_pf:.2f}, plateau score {best.plateau_score:.2f}, {best.status}")
    res = {"P": P, "best": best.to_dict(), "grid": grid, "split": split, "span": (span_is, span_oos), "fut": fut, "spot": spot, "S": S}
    # ------------------------------------------------------------------ the chosen setting, main variant
    cache = caches["L1"]
    df = run_cell(S["L1"], fut, vix, P, "long", "close", cache)
    res["trades"] = df
    res["metrics"] = split_stats(df, split, span_is, span_oos)
    pts_day = ev.daily_points(df, fut)
    res["pts_day"] = pts_day
    res["curve"] = {"full": ev.curve_stats(pts_day), "is": ev.curve_stats(pts_day[pts_day.index < split]), "oos": ev.curve_stats(pts_day[pts_day.index >= split])}
    # ------------------------------------------------------------------ variants with the chosen parameters
    var = []
    for L in CONFIG["last_candle"]:
        for ex in CONFIG["exit_mode"]:
            for sd in CONFIG["sides"]:
                for ins in CONFIG["instrument"]:
                    for sl in CONFIG["slippage_pts"]:
                        for rt in ("current", "dated"):
                            if (ins, sl, rt) != ("F", CONFIG["slippage_main"], "current") and (L, ex, sd) != ("L1", "close", "long"):
                                continue                       # cost variants only on the main signal; signal variants only main costs
                            d = run_cell(S[L], fut, vix, P, sd, ex, caches[L], ins, sl, rt)
                            st = split_stats(d, split, span_is, span_oos)
                            cs = ev.curve_stats(ev.daily_points(d, fut))
                            var.append({"last_candle": L, "exit_mode": ex, "sides": sd, "instrument": ins, "slippage": sl, "rates": rt,
                                        **{f"{k}_{m}": st[k].get(m) for k in ("is", "oos", "full") for m in ("trades", "net_pts", "pf", "pts_per_year", "win_pct")},
                                        "trades_per_year": st["full"].get("trades_per_year"), "max_dd_pts": cs["max_dd"], "time_dd_days": cs["time_dd_days"],
                                        "long_pts": st["full"].get("long_pts"), "short_pts": st["full"].get("short_pts")})
    res["variants"] = pd.DataFrame(var)
    log(f"variants done ({len(var)})")
    # ------------------------------------------------------------------ closing-auction period, L1 vs L2 vs L3
    cas, _ = bars.closing_auction_start(spot)
    res["cas_start"] = cas
    cas_rows = []
    if cas is not None:
        for L in CONFIG["last_candle"]:
            d = run_cell(S[L], fut, vix, P, "long", "close", caches[L])
            for name, part in (("before", d[d.entry_dt < cas]), ("from " + str(cas.date()), d[d.entry_dt >= cas])):
                s = ev.stats(part, None) if len(part) else {"trades": 0}
                cas_rows.append({"last_candle": L, "period": name, "trades": s.get("trades", 0), "net_pts": s.get("net_pts", 0.0), "pf": s.get("pf"), "win_pct": s.get("win_pct")})
    res["cas"] = pd.DataFrame(cas_rows)
    # ------------------------------------------------------------------ Rs equity: leverage x drawdown rule
    eq_rows, eq_main = [], None
    for lev in CONFIG["leverage"]:
        for ddr in (False, True):
            t, rs_day = ev.rupee_equity(df, fut, lev, ddr)
            cs = ev.curve_stats(rs_day, CONFIG["capital"])
            eq_rows.append({"leverage": lev, "dd_rule": ddr, "net_rs": float(rs_day.sum()), "end": cs.get("end"), "cagr_pct": cs.get("cagr_pct"),
                            "max_dd_pct": cs.get("max_dd_pct"), "max_dd_rs": cs["max_dd"], "time_dd_days": cs["time_dd_days"],
                            "zero_lot_trades": int((t["lots"] == 0).sum()), "avg_lots": float(t.loc[t.lots > 0, "lots"].mean()) if (t.lots > 0).any() else 0,
                            "max_lots": int(t["lots"].max()), "ruin_date": cs.get("ruin_date")})
            if lev == CONFIG["leverage_main"] and not ddr:
                eq_main = (t, rs_day, cs)
    res["equity_table"] = pd.DataFrame(eq_rows)
    res["eq_main"] = eq_main
    # ------------------------------------------------------------------ controls
    bh = ev.buy_hold(fut, CONFIG["leverage_main"])
    res["buy_hold"] = {"net_pts": bh["net_pts"], "pts_curve": ev.curve_stats(bh["pts_day"]), "rs_curve": ev.curve_stats(bh["rs_day"], CONFIG["capital"]),
                       "pts_day": bh["pts_day"], "rs_day": bh["rs_day"], "net_rs": float(bh["rs_day"].sum())}
    sig = S["L1"]
    (line, up), ma = engine.indicators(sig, P["atr_n"], P["mult"], P["ma_type"], P["ma_len"], cache)
    valid = np.isfinite(line) & np.isfinite(ma) & (sig.fill >= 0)
    rng = np.random.default_rng(CONFIG["seed"])
    n_rule = len(df)
    rule_mean = float(df["net_pts"].mean())
    res["random"] = {"rule_mean": rule_mean, "rule_total": float(df["net_pts"].sum()), "n_rule": n_rule}
    for tag, any_ in (("any", True), ("up", False)):
        elig = np.nonzero(valid & (True if any_ else up))[0]
        means, totals, counts = [], [], []
        for _ in range(CONFIG["random_sims"]):
            perm = rng.permutation(elig)
            take = n_rule
            while True:                                  # top up until the same number of trades as the rule
                tr = engine.run(sig, fut, line, up, ma, "long", "close", entries=np.sort(perm[:take]), entries_any=any_)
                if len(tr) >= n_rule or take >= len(perm):
                    break
                take = min(len(perm), take + (n_rule - len(tr)) * 2 + 5)
            if len(tr) > n_rule:
                keep = np.sort(rng.choice(len(tr), size=n_rule, replace=False))
                tr = [tr[i] for i in keep]
            d = ev.apply_costs(ev.trades_frame(tr, sig, fut, vix), fut, "F")
            means.append(float(d["net_pts"].mean()) if len(d) else np.nan)
            totals.append(float(d["net_pts"].sum()) if len(d) else np.nan)
            counts.append(len(d))
        sm = np.array(means)
        res["random"][tag] = {"sims_mean": sm, "sims_total": np.array(totals), "avg_trades": float(np.mean(counts)),
                              "p": float((np.sum(sm >= rule_mean) + 1) / (np.isfinite(sm).sum() + 1)), "mean": float(np.nanmean(sm)),
                              "p95": float(np.nanpercentile(sm, 95))}
    res["random"]["p"] = res["random"]["any"]["p"]
    log(f"random control done: p(any candle) = {res['random']['any']['p']:.3f}, p(uptrend candles) = {res['random']['up']['p']:.3f}")
    # ------------------------------------------------------------------ years, stress, claims
    rs_day = eq_main[1]
    res["yearly"] = ev.yearly(df, pts_day, rs_day)
    st_rows = []
    for name, a, b in CONFIG["stress"]:
        m = (pts_day.index >= a) & (pts_day.index <= b)
        bh_m = (bh["pts_day"].index >= a) & (bh["pts_day"].index <= b)
        st_rows.append({"period": name, "from": a, "to": b, "rule_pts": float(pts_day[m].sum()), "buy_hold_pts": float(bh["pts_day"][bh_m].sum()),
                        "trades": int(((df.entry_dt >= a) & (df.entry_dt <= pd.Timestamp(b) + pd.Timedelta(days=1))).sum())})
    res["stress"] = pd.DataFrame(st_rows)
    y = res["yearly"]
    top = df.sort_values("gross_u", ascending=False).head(3)
    cl = CONFIG["claims"]
    full = res["metrics"]["full"]
    res["claims"] = [
        ["~30-35 trades a year", f"{full['trades_per_year']:.1f} trades a year (range {int(y.trades.min())}-{int(y.trades.max())})",
         cl["trades_per_year"][0] <= full["trades_per_year"] <= cl["trades_per_year"][1] + 5],
        ["Break-even ~370 points a year after futures costs", f"charges + slippage {full['cost_pts_per_year']:.0f} points a year", abs(full["cost_pts_per_year"] - cl["breakeven_pts_year"]) < 120],
        ["Best trades ~800 and ~1,400 points", "best gross: " + ", ".join(f"{v:,.0f}" for v in top["gross_u"]), bool((top["gross_u"] >= 700).sum() >= 2)],
        ["Profitable every year", f"{int((y.net_pts > 0).sum())} of {len(y)} years in profit; worst {int(y.loc[y.net_pts.idxmin(), 'year'])}: {y.net_pts.min():,.0f} pts", bool((y.net_pts > 0).all())],
    ]
    res["elapsed_min"] = (time.time() - t0) / 60
    res["warnings"], res["verdict"] = judge(res)
    save(res, out)
    log(f"Module A done in {res['elapsed_min']:.1f} min: {res['verdict']['text']}")
    return res


def judge(res) -> tuple[list, dict]:
    m, g = res["metrics"], res["grid"]
    w = []
    if m["full"].get("trades", 0) < CONFIG["min_trades_warn"]:
        w.append(f"Sirf {m['full'].get('trades', 0)} trade (< {CONFIG['min_trades_warn']}).")
    if m["oos"].get("trades", 0) < CONFIG["min_trades_warn"]:
        w.append(f"Test period me sirf {m['oos'].get('trades', 0)} trade (< {CONFIG['min_trades_warn']}): test ka nateeja kam bharosemand.")
    main = g[(g.last_candle == "L1") & (g.exit_mode == "close") & (g.sides == "long")]
    share = (main.status == "PLATEAU").mean() * 100
    if share < 20:
        w.append(f"Main grid ke sirf {share:.0f}% cells PLATEAU: result patli parameter range pe tika ho sakta hai.")
    b5 = m["full"].get("best5_share", np.nan)
    if np.isfinite(b5) and b5 > 100:
        w.append(f"Sabse acche 5% trades = kul net points ka {b5:.0f}%: baaki trades milke loss me (trend following me aam, par dhyan do).")
    best = res["best"]
    for d in DIMS:
        lv = sorted(main[d].unique())
        if best[d] in (lv[0], lv[-1]):
            w.append(f"Chuni setting ka {d} = {best[d]:g} grid ke kinare pe hai ({lv[0]:g} .. {lv[-1]:g}).")
    r = res["random"]
    if r["up"]["p"] >= 0.05:
        w.append(f"Sirf uptrend candles pe random entry (wahi Supertrend exit) bhi itna hi kamati hai (p = {r['up']['p']:.2f}): "
                 "fayda zyada tar trailing Supertrend exit + badhte market se hai, MA wali entry timing se nahi.")
    oos_ok = m["oos"].get("net_pts", 0) > 0 and m["oos"].get("pf", 0) > 1
    v = res["variants"]
    slip5 = v[(v.last_candle == "L1") & (v.exit_mode == "close") & (v.sides == "long") & (v.instrument == "F") & (v.slippage == 5.0) & (v.rates == "current")]
    stress_ok = len(slip5) and slip5.iloc[0]["oos_net_pts"] > 0
    checks = [("Kharche + slippage ke baad poore period me profit (points)", m["full"].get("net_pts", 0) > 0),
              ("Test period (aakhri 40%) me profit, PF > 1", oos_ok), ("Chuni setting PLATEAU pe (aas-paas ki settings bhi profit)", best["status"] == "PLATEAU"),
              ("Random entry (kisi bhi candle pe, wahi Supertrend exit) se behtar (p < 0.05)", r["any"]["p"] < 0.05), ("Slippage 5 points pe bhi test period me profit", bool(stress_ok)),
              (f"Kam se kam {CONFIG['min_trades_warn']} trade", m["full"].get("trades", 0) >= CONFIG["min_trades_warn"])]
    ok = all(c for _, c in checks)
    return w, {"pass": ok, "checks": checks,
               "text": ("PASS: kharche, padosi settings, test period, random aur 5-pt slippage sab paar." if ok else
                        "FAIL: " + "; ".join(n for n, c in checks if not c) + " - nahi hua.")}


def save(res, out):
    out.mkdir(parents=True, exist_ok=True)
    res["grid"].to_csv(out / "grid.csv", index=False)
    res["variants"].to_csv(out / "variants.csv", index=False)
    res["equity_table"].to_csv(out / "equity_variants.csv", index=False)
    res["yearly"].to_csv(out / "yearly.csv", index=False)
    res["stress"].to_csv(out / "stress.csv", index=False)
    res["cas"].to_csv(out / "closing_auction.csv", index=False)
    t, rs_day, _ = res["eq_main"]
    log = t.copy()
    log["direction"] = np.where(log.side > 0, "LONG", "SHORT")
    log["instrument"] = "NIFTY futures"
    cols = ["sig_in", "entry_dt", "entry_raw", "sig_out", "exit_dt", "exit_raw", "direction", "instrument", "reason", "gross_u", "slip_pts", "cost_pts",
            "net_pts", "lot", "lots", "cost_rs_lot", "pnl_rs", "rolls", "days_held"]
    log[cols].rename(columns={"sig_in": "signal_candle_close", "entry_dt": "entry_time", "entry_raw": "entry_price", "sig_out": "exit_signal_close",
                              "exit_dt": "exit_time", "exit_raw": "exit_price", "reason": "exit_reason", "gross_u": "points", "slip_pts": "slippage_pts",
                              "cost_pts": "charges_pts", "net_pts": "net_points", "lot": "lot_size", "cost_rs_lot": "charges_rs_per_lot",
                              "pnl_rs": "net_pnl_rs", "days_held": "holding_days"}).to_csv(out / "trades_module_a.csv", index=False)
    pd.DataFrame({"date": res["pts_day"].index.strftime("%Y-%m-%d"), "pts_day": res["pts_day"].to_numpy(), "pts_cum": res["pts_day"].cumsum().to_numpy(),
                  "rs_day": rs_day.to_numpy(), "equity_rs": CONFIG["capital"] + rs_day.cumsum().to_numpy()}).to_csv(out / "equity.csv", index=False)
    pd.DataFrame({"any_mean_pts": res["random"]["any"]["sims_mean"], "any_total_pts": res["random"]["any"]["sims_total"],
                  "up_mean_pts": res["random"]["up"]["sims_mean"], "up_total_pts": res["random"]["up"]["sims_total"]}).to_csv(out / "random.csv", index=False)
    summ = {"P": res["P"], "split": str(res["split"].date()), "metrics": res["metrics"], "curve": res["curve"], "claims": res["claims"],
            "random": {k: ({kk: vv for kk, vv in v.items() if not isinstance(vv, np.ndarray)} if isinstance(v, dict) else v) for k, v in res["random"].items()},
            "buy_hold": {k: v for k, v in res["buy_hold"].items() if not isinstance(v, pd.Series)}, "cas_start": str(res["cas_start"]),
            "warnings": res["warnings"], "verdict": {"pass": res["verdict"]["pass"], "text": res["verdict"]["text"],
                                                     "checks": [[n, bool(c)] for n, c in res["verdict"]["checks"]]}}
    (out / "summary.json").write_text(json.dumps(summ, indent=1, default=lambda o: o.item() if hasattr(o, "item") else str(o)), encoding="utf-8")
