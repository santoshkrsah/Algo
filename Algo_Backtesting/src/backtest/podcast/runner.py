"""Generic runner for one podcast module (module spec: slices, grid1, grid2, trades, random_trades, rules, label):

    1. grid 1 for every slice (parallel) -> in-sample / out-of-sample stats per cell -> plateau status per slice
    2. choice = in-sample cell with the best plateau score (never the single best cell); grid 2 around it (if any)
    3. chosen setting: all metrics in-sample / out-of-sample / full, risk 0.25% vs 0.5%, kill switch on / off,
       2x slippage stress, 1,000 random-entry runs, long-history check, year / month tables, warnings, verdict
    4. files in the result folder: grid.csv, trades.csv, equity.csv, yearly.csv, monthly.csv, random.csv, summary.json
"""
import concurrent.futures as cf
import importlib
import json
import time
from pathlib import Path

import numpy as np
import pandas as pd

from backtest.podcast import data, engine, evaluate, portfolio
from backtest.podcast.config import CONFIG

_BARS: dict = {}


def bars_for(module_key: str, tf: str, start=None, end=None) -> dict:
    k = (module_key, tf, start, end)
    if k not in _BARS:
        _BARS[k] = {n: data.bars(n, tf, kind=kd, start=start, end=end) for n, kd in data.universe(module_key)}
    return _BARS[k]


def trading_days(bars: dict) -> np.ndarray:
    return np.unique(np.concatenate([b.days for b in bars.values()]))


def _flat(sl: dict, p: dict, st: dict) -> dict:
    row = {**sl, **p}
    for part in ("is", "oos", "full"):
        s = st[part]
        row[f"{part}_trades"] = s.get("trades", 0)
        for k in ("pf", "exp_R", "net", "win_pct"):
            row[f"{part}_{k}"] = s.get(k, np.nan)
    return row


def cell_job(args) -> list:
    modname, sl, cells, split = args
    mod = importlib.import_module(modname)
    bars = bars_for(mod.KEY, sl["tf"])
    cache: dict = {}
    out = []
    for p in cells:
        df = engine.frame(mod.trades(bars, sl, p, cache=cache), bars)
        if not len(df):
            out.append(_flat(sl, p, {"is": {}, "oos": {}, "full": {}}))
            continue
        acc = portfolio.apply(df)
        out.append(_flat(sl, p, evaluate.split_stats(acc, split)))
    return out


def random_job(args) -> list:
    """n random-entry runs; each returns the mean net R per trade (charges + slippage, no daily cap)."""
    modname, sl, p, rule, seed, n = args
    mod = importlib.import_module(modname)
    bars = bars_for(mod.KEY, sl["tf"])
    rng = np.random.default_rng(seed)
    out = []
    for _ in range(n):
        df = engine.frame(mod.random_trades(bars, sl, p, rule, rng), bars)
        if not len(df):
            out.append(np.nan)
            continue
        r = portfolio.net_R(df)
        out.append(float(r.mean()) if len(r) else np.nan)
    return out


def run_grid(modname: str, jobs: list, workers: int, log=print) -> pd.DataFrame:
    rows = []
    with cf.ProcessPoolExecutor(max_workers=workers) as ex:
        futs = [ex.submit(cell_job, j) for j in jobs]
        for k, f in enumerate(cf.as_completed(futs), 1):
            rows += f.result()
            if k % max(1, len(futs) // 10) == 0:
                log(f"grid {k}/{len(futs)} jobs")
    return pd.DataFrame(rows)


def _jobs(modname: str, sl: dict, cells: list, split, chunk: int) -> list:
    return [(modname, sl, cells[i:i + chunk], split) for i in range(0, len(cells), chunk)]


def evaluate_setting(mod, sl: dict, p: dict, split, log=print, slip_mult: float = 1.0) -> dict:
    """Everything about one setting (main process)."""
    bars = bars_for(mod.KEY, sl["tf"])
    days = trading_days(bars)
    cap = CONFIG["capital"]
    df = engine.frame(mod.trades(bars, sl, p, slip_mult=slip_mult), bars)
    res = {"candidates": df}
    variants = {}
    for rp in (CONFIG["risk_pct"], CONFIG["risk_pct_alt"]):
        for kill in (False, True):
            acc = portfolio.apply(df, risk_pct=rp, kill=kill)
            tk = acc[acc["taken"]]
            dp = portfolio.daily_equity(tk, bars, days)
            variants[(rp, kill)] = (acc, dp)
    acc, dp = variants[(CONFIG["risk_pct"], False)]
    tk = acc[acc["taken"]]
    e_is, e_oos = dp[dp.index < split], dp[dp.index >= split]
    t_e = pd.to_datetime(tk["entry_dt"])
    res.update({
        "acc": acc, "taken": tk, "day_pnl": dp, "split": split,
        "metrics": {name: {**evaluate.trade_stats(t), **evaluate.equity_stats(d, cap)} for name, t, d in
                    (("is", tk[t_e < split], e_is), ("oos", tk[t_e >= split], e_oos), ("full", tk, dp))},
        "variants": [{"risk_pct": rp, "kill": kill, **evaluate.trade_stats(a[a["taken"]]), **evaluate.equity_stats(d, cap),
                      "kill_events": len(a.attrs.get("kill_events", [])), "skipped_kill": int((a["skip_reason"] == "kill switch (5% drawdown)").sum()),
                      "skipped_cap": int((a["skip_reason"] == "daily loss cap").sum())}
                     for (rp, kill), (a, d) in variants.items()],
        "yearly": evaluate.yearly(tk, dp, cap), "monthly": evaluate.monthly(tk, dp, cap),
        "bars": bars, "days": days,
    })
    return res


def run(mod, out_dir: Path, log=print, workers: int | None = None, sims: int | None = None) -> dict:
    t0 = time.time()
    workers = workers or CONFIG["workers"]
    sims = CONFIG["random_sims"] if sims is None else sims
    modname = mod.__name__
    tfs = sorted({s["tf"] for s in mod.slices()})
    split = {tf: evaluate.split_day(trading_days(bars_for(mod.KEY, tf))) for tf in tfs}     # also builds the caches here, before the workers
    if hasattr(mod, "warm"):
        mod.warm()
    # ---- grid 1
    jobs = []
    for sl in mod.slices():
        dims, cells = mod.grid1(sl)
        jobs += _jobs(modname, sl, cells, split[sl["tf"]], chunk=4 if sl["tf"] in ("1D",) else 2)
    log(f"grid 1: {len(mod.slices())} slices, {sum(len(j[2]) for j in jobs)} cells, {len(jobs)} jobs")
    g1 = run_grid(modname, jobs, workers, log)
    keys = list(mod.slices()[0].keys())
    parts = []
    for _, g in g1.groupby(keys, sort=False):
        sl0 = {k: (g[k].iloc[0].item() if hasattr(g[k].iloc[0], "item") else g[k].iloc[0]) for k in keys}
        parts.append(evaluate.plateau(g, mod.grid1(sl0)[0], keys))
    g1 = pd.concat(parts, ignore_index=True)
    g1["grid"] = 1
    best1 = evaluate.choose(g1)
    sl = {k: (best1[k].item() if hasattr(best1[k], "item") else best1[k]) for k in keys}
    p_cols = [c for c in ("fast", "slow", "stop", "target", "n", "max_stop", "attempts", "event_filter", "buffer", "rr", "k_stop", "k_target")
              if c in g1.columns]
    p = {c: (best1[c].item() if hasattr(best1[c], "item") else best1[c]) for c in p_cols if pd.notna(best1[c])}
    log(f"grid 1 choice: {mod.label(sl, p)} | IS PF {best1['is_pf']:.2f}, plateau score {best1['plateau_score']:.2f}, {best1['status']}")
    # ---- grid 2
    g2 = None
    gr2 = mod.grid2(sl, p)
    if gr2:
        dims2, cells2 = gr2
        g2 = run_grid(modname, _jobs(modname, sl, cells2, split[sl["tf"]], chunk=3), workers, log)
        g2 = evaluate.plateau(g2, dims2, keys)
        g2["grid"] = 2
        best2 = evaluate.choose(g2)
        p = {c: (best2[c].item() if hasattr(best2[c], "item") else best2[c]) for c in p_cols if c in best2 and pd.notna(best2[c])}
        log(f"grid 2 choice: {mod.label(sl, p)} | IS PF {best2['is_pf']:.2f}, plateau score {best2['plateau_score']:.2f}, {best2['status']}")
    grid = pd.concat([g1] + ([g2] if g2 is not None else []), ignore_index=True)
    edge = []
    for gnum, (dims, gg) in {1: (mod.grid1(sl)[0], g1), 2: ((gr2 or [[]])[0], g2)}.items():
        if gg is None:
            continue
        sub = gg[(gg[keys] == pd.Series(sl)).all(axis=1)]
        for d in dims:
            lv = sorted(sub[d].dropna().unique())
            if len(lv) > 2 and p.get(d) in (lv[0], lv[-1]):
                edge.append(f"{d} = {p[d]:g} (grid {lv[0]:g} .. {lv[-1]:g})")
    chosen_row = (g2 if g2 is not None else g1)
    chosen_row = evaluate.choose(chosen_row)
    # ---- the chosen setting
    log("evaluating the chosen setting (risk 0.25 / 0.5, kill switch, stress)")
    res = evaluate_setting(mod, sl, p, split[sl["tf"]], log)
    stress = evaluate_setting(mod, sl, p, split[sl["tf"]], log, slip_mult=2.0)
    res["stress"] = {k: stress["metrics"][k] for k in ("is", "oos", "full")}
    # ---- random control
    rule = res["candidates"]
    acc_all = portfolio.apply(rule, daily_cap=False)
    rule_R = float(acc_all.loc[acc_all["units"] >= 1, "R"].mean())
    rule_R_oos = float(acc_all.loc[(acc_all["units"] >= 1) & (pd.to_datetime(acc_all["entry_dt"]) >= split[sl["tf"]]), "R"].mean())
    sim = []
    if sims:
        log(f"random control: {sims} runs")
        per = max(1, sims // (workers * 2))
        cols = [c for c in ("inst", "bars", "stop_pct", "side") if c in rule]
        jobs = [(modname, sl, p, rule[cols], CONFIG["seed"] + k, min(per, sims - k * per)) for k in range(int(np.ceil(sims / per)))]
        with cf.ProcessPoolExecutor(max_workers=workers) as ex:
            for r in ex.map(random_job, jobs):
                sim += r
    sim = np.asarray(sim, float)
    res["random"] = {"rule_R": rule_R, "rule_R_oos": rule_R_oos, "sims": sim, "p": evaluate.pvalue(rule_R, sim),
                     "mean": float(np.nanmean(sim)) if len(sim) else np.nan, "p95": float(np.nanpercentile(sim, 95)) if len(sim) else np.nan}
    # ---- long history (module hook)
    res["long"] = mod.long_history(sl, p) if hasattr(mod, "long_history") else None
    res.update({"slice": sl, "params": p, "grid": grid, "chosen_row": chosen_row, "label": mod.label(sl, p), "module": mod.KEY, "edge": edge,
                "elapsed_min": (time.time() - t0) / 60})
    res["warnings"], res["verdict"] = judge(res)
    save(res, out_dir)
    log(f"done in {res['elapsed_min']:.1f} min: {res['verdict']['text']}")
    return res


def judge(res: dict) -> tuple[list, dict]:
    m = res["metrics"]
    w = []
    mt = CONFIG["min_trades_warn"]
    if m["full"].get("trades", 0) < mt:
        w.append(f"Sirf {m['full'].get('trades', 0)} trade (< {mt}): result pe bharosa kam.")
    if m["oos"].get("trades", 0) < mt:
        w.append(f"Out-of-sample me sirf {m['oos'].get('trades', 0)} trade (< {mt}).")
    cr = res["chosen_row"]
    if cr["status"] != "PLATEAU":
        w.append(f"Chuni hui setting {cr['status']} hai: aas-paas ki settings me profit nahi (sirf ek point pe kaam karti hai).")
    g = res["grid"]
    g1 = g[g["grid"] == 1]
    rd = m["full"].get("ruin_date")
    if rd:
        w.append(f"Account khatam: fixed risk (compounding nahi) ke saath equity {rd} ko Rs 0 se neeche chali gayi "
                 f"(sabse kam Rs {m['full'].get('min_equity', 0):,.0f}). Asli trading me yahin ruk jaate.")
    if res.get("edge"):
        w.append("Chuni setting grid ke kinare pe hai (" + ", ".join(res["edge"]) + "): wahan padosi kam hote hain aur sabse accha area grid ke bahar bhi ho sakta hai.")
    share = (g1["status"] == "PLATEAU").mean() * 100
    if share < 10:
        w.append(f"Grid ke sirf {share:.0f}% cells PLATEAU hain: result ek patli parameter range pe tika hai.")
    b5 = m["full"].get("best5_share", np.nan)
    if np.isfinite(b5) and b5 > 100:
        w.append(f"Sabse acche 5% trades ne kul profit ka {b5:.0f}% diya: baaki trades milke loss me.")
    y = res["yearly"]
    if len(y) and (y["flag"] != "").any():
        w.append("Bahut bada loss wala saal: " + ", ".join(str(v) for v in y.loc[y["flag"] != "", "year"]))
    rnd = res["random"]
    oos_ok = m["oos"].get("net", 0) > 0 and m["oos"].get("pf", 0) > 1
    cost_ok = m["full"].get("net", 0) > 0
    plateau_ok = cr["status"] == "PLATEAU"
    random_ok = np.isfinite(rnd["p"]) and rnd["p"] < 0.05
    stress_ok = res["stress"]["oos"].get("net", 0) > 0
    checks = [("Kharche + slippage ke baad poore period me profit", cost_ok), ("Out-of-sample (aakhri 40%) me profit, PF > 1", oos_ok),
              ("Chuni setting PLATEAU pe hai (aas-paas bhi profit)", plateau_ok), ("Random entry se behtar (p < 0.05)", random_ok),
              ("Slippage 2x pe bhi out-of-sample profit", stress_ok), (f"Kam se kam {mt} trade", m["full"].get("trades", 0) >= mt)]
    passed = all(ok for _, ok in checks)
    text = ("PASS: kharche, aas-paas ki settings, out-of-sample aur random test sab paar." if passed else
            "FAIL: " + "; ".join(n for n, ok in checks if not ok) + " - nahi hua.")
    return w, {"pass": passed, "checks": checks, "text": text}


def save(res: dict, out: Path):
    out.mkdir(parents=True, exist_ok=True)
    res["grid"].to_csv(out / "grid.csv", index=False)
    trade_log(res).to_csv(out / "trades.csv", index=False)
    eq = res["day_pnl"]
    pd.DataFrame({"date": eq.index.strftime("%Y-%m-%d"), "day_pnl": eq.to_numpy(), "equity": CONFIG["capital"] + eq.cumsum().to_numpy()}).to_csv(out / "equity.csv", index=False)
    res["yearly"].to_csv(out / "yearly.csv", index=False)
    res["monthly"].to_csv(out / "monthly.csv", index=False)
    pd.DataFrame({"sim_mean_R": res["random"]["sims"]}).to_csv(out / "random.csv", index=False)
    summ = {"label": res["label"], "slice": res["slice"], "params": res["params"], "split": str(res["split"].date()),
            "metrics": res["metrics"], "stress": res["stress"], "variants": res["variants"],
            "random": {k: v for k, v in res["random"].items() if k != "sims"}, "long": res["long"], "warnings": res["warnings"],
            "verdict": {"pass": res["verdict"]["pass"], "text": res["verdict"]["text"], "checks": [[n, bool(ok)] for n, ok in res["verdict"]["checks"]]}}
    (out / "summary.json").write_text(json.dumps(summ, indent=1, default=lambda o: o.item() if hasattr(o, "item") else str(o)), encoding="utf-8")


def trade_log(res: dict) -> pd.DataFrame:
    """One row per candidate trade of the chosen setting (taken or skipped) with real prices."""
    a = res["acc"]
    bars = res["bars"]
    rows = []
    for r in a.itertuples(index=False):
        b = bars[r.inst]
        off = b.off[r.entry_i]
        legs = "; ".join(f"{str(dt)[:16]} {px:.2f} x{f:.2f} {why}" for (_, _, px, f, why, dt) in r.legs)
        rows.append({"date": str(r.entry_dt)[:10], "entry_time": str(r.entry_dt)[11:16], "module": res.get("module", ""), "instrument": r.inst,
                     "kind": r.kind, "direction": "LONG" if r.side > 0 else "SHORT", "entry": round(r.entry_raw, 2),
                     "stop": round(r.stop_px - off, 2) if np.isfinite(r.stop_px) else None,
                     "target": round(r.target_px - off, 2) if np.isfinite(r.target_px) else None,
                     "exit_time": str(r.exit_dt)[:16], "exit": round(r.exit_raw, 2), "exit_reason": r.reason, "exits": legs,
                     "R_gross": round(r.R_gross, 3), "R_net": round(r.R, 3) if np.isfinite(r.R) else None, "units": int(r.units),
                     "notional": round(r.notional), "gross": round(r.gross, 2), "slippage": round(r.slippage, 2), "charges": round(r.fees, 2),
                     "net": round(r.net, 2), "rolls": int(r.n_rolls), "taken": bool(r.taken), "skip_reason": r.skip_reason})
    return pd.DataFrame(rows)
