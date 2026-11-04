"""RSI(14) swing on 2-hour candles (long only), NIFTY 50 and RELIANCE, Jan 2021 - Oct 2026: version A (base) and
B (buy only when close > EMA200), in-sample (first 70% of days) vs out-of-sample (last 30%), buy-and-hold, one-at-a-time
checks, a 36-setting neighbourhood (shown, never used to pick), random-entry test, trade logs, HTML report + Telegram.

    python tools/run_rsi2h.py
    python tools/run_rsi2h.py --no-telegram

Needs the 1-min files (Algo_Historical_Data):
    python -m fyers_data.download_reference index NSE:NIFTY50-INDEX NIFTY50_FROM2020 --start 2020-07-01
    python -m fyers_data.download_reference index NSE:RELIANCE-EQ RELIANCE_EQ_FROM2020 --start 2020-07-01
"""
import argparse
import itertools
import json
import sys
import time
import traceback
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

import numpy as np  # noqa: E402
import pandas as pd  # noqa: E402

from backtest import rsi2h as R  # noqa: E402
from backtest.config import ROOT, result_dir  # noqa: E402
from backtest.notify import send, send_document  # noqa: E402
from backtest.rsi2h_report import write_html  # noqa: E402

TAG_TRADING, TAG_TECH = "@ayushanand440", "@albert_max"
NAME = "rsi_swing_2h_2021_2026"
TITLE = "RSI Swing 2-hour (NIFTY 50 + RELIANCE)"
P = R.Params()
SYMS = ("NIFTY50", "RELIANCE")
VER = {"A": "A: base (jaisa likha)", "B": "B: sirf close > EMA200 par buy"}
CHECKS = [("Base (jaisa likha)", {}), ("Slippage 2x (0.10% har side) - stress", {"slip": 0.001}), ("Bina kharcha, bina slippage (sirf edge)", {"costs": False, "slip": 0.0}),
          ("Candle TradingView jaisi (15:15-15:30 ki alag chhoti candle)", {"_build": "tv"}), ("Stop 1.5 x ATR", {"stop_atr": 1.5}), ("Stop 3 x ATR", {"stop_atr": 3.0}),
          ("Stop-loss nahi (sirf RSI 70 exit)", {"use_stop": False}), ("Risk rules band (din / hafta loss stop, 3-loss skip)", {"risk_rules": False}),
          ("Risk 0.5% / trade", {"risk": 0.005}), ("Risk 2% / trade (cash ki limit zyada lagegi)", {"risk": 0.02})]
GRID = {"buy_lvl": (25.0, 30.0, 35.0), "sell_lvl": (65.0, 70.0, 75.0), "stop_atr": (1.5, 2.0, 2.5, 3.0)}
SIMS = 300


def rs(v) -> str:
    return ("-" if v < 0 else "+") + f"Rs {abs(v):,.0f}"


def f2(v) -> str:
    return "n/a" if v is None else f"{v:.2f}"


def ut(ts) -> int:
    """IST clock time as if it were UTC (what the chart shows)."""
    return int((pd.Timestamp(ts) - pd.Timestamp("1970-01-01")).total_seconds())


def check_row(label: str, s: dict) -> list:
    a, i, o = s["all"], s["is"], s["oos"]
    return [label, a["trades"], a["win_pct"], a["ret_pct"], a["cagr"], a["pf"], a["exp_r"], a["max_dd_pct"], a["sharpe"],
            i["trades"], i["ret_pct"], i["pf"], o["trades"], o["ret_pct"], o["pf"]]


def years_of(D: dict, r: dict, t: pd.DataFrame, bh: dict) -> list:
    out = []
    dates = D["date"]
    for y in sorted({d[:4] for d in dates[D["a"]:]}):
        idx = np.flatnonzero((dates >= f"{y}-01-01") & (dates <= f"{y}-12-31"))
        idx = idx[idx >= D["a"]]
        lo, hi = int(idx[0]), int(idx[-1])
        s = R.stats(D, r, t, lo, hi)
        b0 = P.capital if lo <= D["a"] else float(bh["eq"][lo - 1])
        bs = R.curve_stats(D, bh["eq"], lo, hi, b0)
        out.append([y, s["ret_pct"], s["net_eq"], s["start_eq"], s["trades"], s["win_pct"], s["pf"], s["max_dd_pct"], bs["ret_pct"], bs["max_dd_pct"]])
    return out


def months_of(D: dict, r: dict, t: pd.DataFrame) -> list:
    dates, eq = D["date"], r["eq"]
    out, prev = [], P.capital
    ym = np.array([d[:7] for d in dates])
    for m in sorted(set(ym[D["a"]:])):
        idx = np.flatnonzero(ym == m)
        idx = idx[idx >= D["a"]]
        end = float(eq[idx[-1]])
        n = int(((t.i >= idx[0]) & (t.i <= idx[-1])).sum()) if len(t) else 0
        out.append([m, (end / prev - 1) * 100, end - prev, n])
        prev = end
    return out


def trade_dicts(D: dict, t: pd.DataFrame, key: str, off: int) -> list:
    out = []
    for n, q in enumerate(t.itertuples(), 1):
        out.append({"id": f"{key}-{n}", "n": n, "entry_date": q.entry_date, "exit_date": q.exit_date, "sig_t": q.sig_t, "entry_t": q.entry_t, "exit_t": q.exit_t,
                    "si": int(q.sig) - off, "ei": int(q.i) - off, "xi": int(q.x) - off, "rsi_sig": q.rsi_sig, "ema_sig": q.ema_sig, "atr": q.atr,
                    "raw": q.raw, "fill": q.fill, "stop": q.stop if np.isfinite(q.stop) else None, "qty": int(q.qty), "notional": q.notional, "risk_rs": q.risk_rs,
                    "risk_pct": q.risk_pct, "pos_pct": q.pos_pct, "capped": bool(q.capped), "exit_raw": q.exit_raw, "exit": q.exit, "reason": q.reason,
                    "bars": int(q.bars), "days": int(q.days), "gross": q.gross, "slip": q.slip_rs, "c_brk": q.c_brk, "c_stt": q.c_stt, "c_exch": q.c_exch,
                    "c_stamp": q.c_stamp, "c_gst": q.c_gst, "c_dp": q.c_dp, "costs": q.costs, "div": q.div, "net": q.net, "R": q.R, "ret_eq": q.ret_eq,
                    "eq0": q.eq0, "period": q.period})
    return out


def trade_log(t: pd.DataFrame, D: dict) -> pd.DataFrame:
    if t.empty:
        return t
    x = t.copy()
    unit = " (ETF unit = index/100)" if D["mult"] != 1 else ""
    return pd.DataFrame({"period": x.period, "signal_candle": x.sig_t, "rsi_signal": x.rsi_sig.round(2), "ema200_signal": x.ema_sig.round(2), "atr14": x.atr.round(2),
                         "entry_time": x.entry_t, "entry_open": x.raw.round(2), "entry_fill": x.fill.round(2), "stop": x.stop.round(2), f"qty{unit}": x.qty,
                         "position_rs": x.notional.round(0), "risk_rs": x.risk_rs.round(0), "risk_pct_equity": x.risk_pct.round(3), "cash_capped": x.capped,
                         "exit_time": x.exit_t, "exit_price": x.exit_raw.round(2), "exit_fill": x.exit.round(2), "exit_reason": x.reason, "bars_2h": x.bars,
                         "trading_days": x.days, "gross_rs": x.gross.round(2), "slippage_rs": x.slip_rs.round(2), "brokerage": x.c_brk.round(2), "stt": x.c_stt.round(2),
                         "exch_sebi": x.c_exch.round(2), "stamp": x.c_stamp.round(2), "gst": x.c_gst.round(2), "dp": x.c_dp.round(2), "costs_rs": x.costs.round(2),
                         "dividend_rs": x["div"].round(2), "net_rs": x.net.round(2), "R": x.R.round(3), "return_pct_equity": x.ret_eq.round(3),
                         "equity_before": x.eq0.round(2)})


def inv_of(D: dict, t: pd.DataFrame, s: dict, r: dict) -> list:
    unit = "ETF unit (index / 100)" if D["mult"] != 1 else "share"
    if t.empty:
        return [["Capital", f"Rs {P.capital:,.0f}"], ["Trade", "0"]]
    dep = np.where(r["inpos"], r["eq"], 0)
    return [["Shuru ki capital", f"Rs {P.capital:,.0f} (compounding ON: har trade abhi ki equity pe)"],
            ["Risk / trade", f"1% of abhi ki equity; asal average {s['avg_risk_pct']:.2f}% (cash ki limit se kam)"],
            ["Qty ka rule", f"floor(1% equity / (entry - stop)), {unit} me; cash se zyada nahi (leverage nahi)"],
            ["Max per instrument / max khuli position", "100% equity (bina leverage) / 1 (add nahi)"],
            ["Kul khula risk limit", "5% (ek position me max 1%, isliye kabhi lagu nahi hua)"],
            ["Average position", f"Rs {t.notional.mean():,.0f} ({t.pos_pct.mean():.0f}% equity)"],
            ["Sabse badi position", f"Rs {t.notional.max():,.0f} ({t.pos_pct.max():.0f}% equity)"],
            ["Peak paisa laga (mark-to-market)", f"Rs {dep.max():,.0f}"],
            ["Trade jahan cash ki limit lagi", f"{s['capped_pct']:.0f}% (qty 1% risk se kam hui)"],
            ["Market me time", f"{s['exposure']:.0f}% candles pe position khuli"]]


def run(args, status) -> int:
    t0 = time.time()
    out = result_dir(NAME)
    tell = (lambda m: None) if args.no_telegram else send
    tell(f"{TITLE}: backtest run shuru ({out.name}). 2 instrument x 2 version, checks + 36 setting padosi + random entry test (300 x).")
    status("RSI swing: data load + 2-hour candle bana raha hoon")
    Ds = {s: R.load(s) for s in SYMS}
    Dtv = {s: R.load(s, build="tv") for s in SYMS}
    split = Ds["NIFTY50"]["split"]
    first, last = Ds["NIFTY50"]["date"][Ds["NIFTY50"]["a"]], Ds["NIFTY50"]["date"][Ds["NIFTY50"]["b"]]
    instr, views, all_checks, all_neigh, summary = {}, [], [], [], []

    for sym in SYMS:
        D = Ds[sym]
        bh = R.buy_hold(D, P)
        bhs = R.bh_stats(D, bh, P.capital)
        last_bar = np.r_[D["date"][1:] != D["date"][:-1], True]
        sel = np.flatnonzero(last_bar)
        sel = sel[sel >= D["a"]]
        off = max(0, D["a"] - 260)                                   # chart series start (~3 months before Jan 2021)
        bars = D["bars"]
        instr[sym] = {"label": D["label"], "days": list(D["date"][sel]), "bh": np.round(bh["eq"][sel], 0).tolist(), "bh_stats": bhs,
                      "bh_info": {"qty": bh["qty"], "fill": bh["fill"], "div": bh["div"], "costs": bh["costs"]},
                      "series": {"t": [ut(x) for x in bars.t0.iloc[off:]], "o": np.round(D["o"][off:], 2).tolist(), "h": np.round(D["h"][off:], 2).tolist(),
                                 "l": np.round(D["l"][off:], 2).tolist(), "c": np.round(D["c"][off:], 2).tolist(), "rsi": np.round(D["rsi"][off:], 2).tolist(),
                                 "ema": np.round(D["ema"][off:], 2).tolist()}}
        for ver in "AB":
            status(f"RSI swing: {D['label']} version {ver} - base, checks, padosi grid, random entry")
            key = f"{sym}_{ver}"
            p = R.with_params(P, version=ver)
            r = R.run(D, p)
            t = R.trade_frame(D, r)
            st = R.all_stats(D, r, t)
            boot = {k: R.bootstrap(t[(t.i >= lo) & (t.i <= hi)].ret_eq.to_numpy() if len(t) else np.array([])) for k, (lo, hi) in R.periods(D).items()}
            rnd = R.random_entries(D, p, r["n_signals"], SIMS)
            rnd_ret = (rnd / P.capital - 1) * 100
            act = st["all"]["ret_pct"]
            rand = {"sims": SIMS, "k": r["n_signals"], "median": float(np.median(rnd_ret)), "p5": float(np.percentile(rnd_ret, 5)), "p95": float(np.percentile(rnd_ret, 95)),
                    "pct_rank": float((rnd_ret < act).mean() * 100), "actual": act, "pos_share": float((rnd_ret > 0).mean() * 100)}
            checks = []
            for label, kw in CHECKS:
                kw = dict(kw)
                DD = Dtv[sym] if kw.pop("_build", None) == "tv" else D
                rr = R.run(DD, R.with_params(p, **kw))
                checks.append(check_row(label, R.all_stats(DD, rr, R.trade_frame(DD, rr))))
            neigh = []
            for b_, s_, k_ in itertools.product(*GRID.values()):
                rr = R.run(D, R.with_params(p, buy_lvl=b_, sell_lvl=s_, stop_atr=k_))
                ss = R.all_stats(D, rr, R.trade_frame(D, rr))
                neigh.append([b_, s_, k_, ss["is"]["trades"], ss["is"]["ret_pct"], ss["is"]["pf"], ss["oos"]["trades"], ss["oos"]["ret_pct"], ss["oos"]["pf"],
                              ss["all"]["ret_pct"], ss["all"]["max_dd_pct"]])
            ng = pd.DataFrame(neigh, columns=["buy", "sell", "stop", "is_n", "is_ret", "is_pf", "oos_n", "oos_ret", "oos_pf", "all_ret", "all_dd"])
            base_row = ng[(ng.buy == P.buy_lvl) & (ng.sell == P.sell_lvl) & (ng.stop == P.stop_atr)].iloc[0]
            nsum = {"n": len(ng), "is_pos": int((ng.is_ret > 0).sum()), "oos_pos": int((ng.oos_ret > 0).sum()), "both_pos": int(((ng.is_ret > 0) & (ng.oos_ret > 0)).sum()),
                    "all_pos": int((ng.all_ret > 0).sum()), "rank_corr": float(ng.is_ret.rank().corr(ng.oos_ret.rank())) if ng.oos_ret.std() > 0 else None,
                    "base_oos_rank": int((ng.oos_ret > base_row.oos_ret).sum() + 1), "best_is": ng.loc[ng.is_ret.idxmax()].tolist(),
                    "median_all": float(ng.all_ret.median()), "best_all": float(ng.all_ret.max()), "worst_all": float(ng.all_ret.min())}
            sk = pd.Series([x["why"] for x in r["skipped"]], dtype=object).value_counts().to_dict()
            sa = st["all"]
            chips = [["Instrument", D["label"], 1], ["Version", VER[ver], 1]]
            views.append({"key": key, "sym": sym, "ver": ver, "label": f"{D['label']} - {VER[ver]}", "stats": st, "boot": boot, "rand": rand, "checks": checks,
                          "neigh": neigh, "nsum": nsum, "skipped": sk, "n_signals": r["n_signals"], "inv": inv_of(D, t, sa, r), "chips": chips,
                          "eq": np.round(r["eq"][sel], 0).tolist(), "trades": trade_dicts(D, t, key, off), "years": years_of(D, r, t, bh), "months": months_of(D, r, t),
                          "off": off})
            all_checks += [[D["label"], ver] + c for c in checks]
            all_neigh += [[D["label"], ver] + n for n in neigh]
            trade_log(t, D).to_csv(out / f"trades_{key}.csv", index=False)
            skd = pd.DataFrame(r["skipped"], columns=["sig", "i", "why"])
            skd.insert(0, "signal_candle", [str(D["t0"][k]) for k in skd.sig])
            skd.drop(columns=["sig", "i"]).to_csv(out / f"skipped_{key}.csv", index=False)
            summary.append(f"{D['label']} {ver}: {sa['trades']} trade, win {sa['win_pct'] or 0:.1f}%, return {sa['ret_pct']:.1f}% (CAGR {sa['cagr']:.2f}%), "
                           f"PF {f2(sa['pf'])}, exp {f2(sa['exp_r'])}R, max DD {sa['max_dd_pct']:.1f}%, Sharpe {f2(sa['sharpe'])} | IS {st['is']['trades']} tr "
                           f"{st['is']['ret_pct']:.1f}% PF {f2(st['is']['pf'])} | OOS {st['oos']['trades']} tr {st['oos']['ret_pct']:.1f}% PF {f2(st['oos']['pf'])} | "
                           f"random entry rank {rand['pct_rank']:.0f}% | padosi {nsum['all_pos']}/{nsum['n']} profit")
        summary.append(f"{D['label']} buy-hold: {bhs['all']['ret_pct']:.1f}% (CAGR {bhs['all']['cagr']:.2f}%), max DD {bhs['all']['max_dd_pct']:.1f}%, Sharpe {f2(bhs['all']['sharpe'])} | "
                       f"IS {bhs['is']['ret_pct']:.1f}% | OOS {bhs['oos']['ret_pct']:.1f}%")

    pd.DataFrame(all_checks, columns=["instrument", "version", "check", "trades", "win_pct", "ret_pct", "cagr", "pf", "exp_r", "max_dd_pct", "sharpe",
                                      "is_trades", "is_ret_pct", "is_pf", "oos_trades", "oos_ret_pct", "oos_pf"]).to_csv(out / "checks.csv", index=False)
    pd.DataFrame(all_neigh, columns=["instrument", "version", "buy_lvl", "sell_lvl", "stop_atr", "is_trades", "is_ret_pct", "is_pf", "oos_trades", "oos_ret_pct",
                                     "oos_pf", "all_ret_pct", "all_max_dd_pct"]).to_csv(out / "neighbours.csv", index=False)
    status("RSI swing: report (HTML) bana raha hoon")

    V = {v["key"]: v for v in views}
    best = max(views, key=lambda v: v["stats"]["all"]["ret_pct"])
    edge = [v for v in views if (v["stats"]["all"]["pf"] or 0) > 1.2 and v["stats"]["oos"]["ret_pct"] > 0 and (v["boot"]["all"]["lo"] or -1) > 0 and v["rand"]["pct_rank"] >= 90]
    verdict = ("Kisi bhi instrument / version me pakka edge nahi mila." if not edge else "Edge ke sanket: " + ", ".join(v["label"] for v in edge))
    concl = conclusion(Ds, V, instr, verdict)
    D0 = Ds["RELIANCE"]
    dem = next((a for a in D0["adj"] if a["kind"] == "demerger"), None)
    divs = [a for a in D0["adj"] if a["kind"] == "dividend" and a["ex_date"] >= first]
    data_info = [
        ["Data kahan se", f"Fyers 1-minute candles (NSE:NIFTY50-INDEX, NSE:RELIANCE-EQ), 4 Nov 2026 ko download; {Ds['NIFTY50']['first_1m'][:10]} se {last} "
                          f"(Jul-Dec 2020 sirf indicator warm-up ke liye). {Ds['NIFTY50']['n_1m']:,} / {Ds['RELIANCE']['n_1m']:,} 1-min candle."],
        ["Data check", "Fyers daily file ke har trading din ka 1-min data hai (koi din missing nahi). Oct 2021 se purane store se minute-by-minute match (max 0.017% farak). "
                       "NIFTY Jan-Sep 2021: NSE ki official daily file se open / high 0.09% ke andar."],
        ["Galat tick saaf kiye", f"1-min high / low ko open / close tak badhaya (NIFTY {Ds['NIFTY50']['bad_ohlc']}, RELIANCE {Ds['RELIANCE']['bad_ohlc']} candle, max 0.2%), phir har din "
                                 f"official daily high-low ke andar clip. Asar wale din: NIFTY {', '.join(Ds['NIFTY50']['clipped_days']) or 'koi nahi'} (24 Feb 2021 = NSE outage, 1.5% galat low); "
                                 f"RELIANCE {', '.join(Ds['RELIANCE']['clipped_days']) or 'koi nahi'}."],
        ["2-hour candle kaise bani", "Har din 3 candle: 09:15-11:15, 11:15-13:15, 13:15-15:30 (aakhri 2h15m, taaki 15:15-15:30 ki 15-min ki chhoti candle alag na bane). "
                                     "Open = pehle minute ka open, high / low = sabse uncha / neecha, close = aakhri minute ka close (last traded price, NSE ka official closing price nahi). "
                                     "Muhurat / Saturday special session bhi isi ghadi ke hisaab se candle bane. TradingView wala tareeka (4th 15-min candle) Extra checks me."],
        ["Adjusted price", "Fyers ka data split / bonus adjusted hai (RELIANCE bonus 1:1, 28 Oct 2024 - koi jump nahi). "
                           + (f"Jio Financial demerger (20 Jul 2023) Fyers ne adjust nahi kiya tha (9.2% gap): uske pehle ke saare price x {dem['factor']:.5f} "
                              f"(= 20 Jul ka pehla open {dem['first_open']:.2f} / 19 Jul ka official close {dem['prev_close']:.2f}). " if dem else "")
                           + "NIFTY 50 index hai, adjust ki zarurat nahi."],
        ["Dividend", "RELIANCE (NSE corporate actions): " + ", ".join(f"{a['ex_date']} Rs {a['raw']:g} (adjusted share pe Rs {a['adjusted']:.2f})" for a in divs)
                     + ". Ex-date pe position khuli ho to dividend cash me (tax nahi kaata). NIFTY 50 price index hai (dividend nahi)."],
        ["Candles", f"NIFTY {len(Ds['NIFTY50']['c']) - Ds['NIFTY50']['a']:,}, RELIANCE {len(D0['c']) - D0['a']:,} candle test period me; indicator Jul 2020 se garam."],
        ["In-sample / out-of-sample", f"Trading din ke hisaab se: pehle 70% = {first} se {prev_day(Ds['NIFTY50'], split)}, aakhri 30% = {split} se {last}. Trade entry ke din se hisse me gina."],
        ["Survivorship", "Sirf 2 instrument (aapke brief ke example). RELIANCE aaj bhi bada stock hai - aise stock ko chunna khud ek bias hai."],
    ]
    blob = {"title": TITLE, "capital": P.capital, "data_range": [first, last], "split": split, "settings": SETTINGS, "views": views, "instr": instr,
            "rules": RULES, "notes": NOTES, "formulas": FORMULAS, "data_info": data_info, "verdict": verdict, "conclusion": concl,
            "checks_names": [c[0] for c in CHECKS], "best": best["key"],
            "data_note": "Fyers 1-min candles -> 2-hour candles (09:15 / 11:15 / 13:15-15:30); split / bonus / demerger adjusted"}
    path = write_html(out / "report.html", blob)
    mb = path.stat().st_size / 1e6
    text = "\n".join(summary + ["", "Faisla: " + verdict, ""] + [c.replace("<b>", "").replace("</b>", "") for c in concl])
    (out / "summary.txt").write_text(text, encoding="utf-8")
    print(text, flush=True)
    site = json.loads((ROOT / "site" / "site.json").read_text(encoding="utf-8"))["url"]
    num = out.name.split("_", 1)[0]
    a, b = V["NIFTY50_A"]["stats"], V["RELIANCE_A"]["stats"]
    msg = (f"{TAG_TRADING} RSI SWING 2-HOUR BACKTEST DONE ({(time.time() - t0) / 60:.1f} min). Long only, capital Rs 10L har instrument (compounding), risk 1%, "
           f"SL 2xATR, Rs 20/order + delivery charges, slippage 0.05%/side. Period {first} -> {last}, OOS {split} se.\n"
           + "\n".join(summary) + f"\nFaisla: {verdict}\nWebsite: {site}\nReport: {site}#report-{num}")
    if not args.no_telegram:
        send(msg)
        send_document(path, f"{TITLE} report ({out.name}). Browser me kholo; Rules tab me rules, neeche Formulas.")
    print(f"done {time.time() - t0:.0f}s, report {mb:.1f} MB -> {out}", flush=True)
    return 0


def prev_day(D: dict, day: str) -> str:
    ds = sorted(set(D["date"]))
    return ds[ds.index(day) - 1]


def conclusion(Ds: dict, V: dict, instr: dict, verdict: str) -> list:
    out = []
    for sym in SYMS:
        lab = Ds[sym]["label"]
        bh = instr[sym]["bh_stats"]
        for ver in "AB":
            v = V[f"{sym}_{ver}"]
            s, i, o, bt, rd = v["stats"]["all"], v["stats"]["is"], v["stats"]["oos"], v["boot"]["all"], v["rand"]
            ci = f"{bt['lo']:+.2f}% se {bt['hi']:+.2f}%" if bt["lo"] is not None else "n/a"
            out.append(f"<b>{lab} {ver}</b>: {s['trades']} trade ({i['trades']} in-sample, {o['trades']} out-of-sample), return {s['ret_pct']:+.1f}% "
                       f"(CAGR {s['cagr']:+.2f}%) vs buy-hold {bh['all']['ret_pct']:+.1f}% (CAGR {bh['all']['cagr']:+.2f}%); PF {f2(s['pf'])}, "
                       f"win {s['win_pct'] or 0:.0f}%, max DD {s['max_dd_pct']:.1f}%. Har trade ka average {bt['mean'] or 0:+.2f}% (95% range {ci}). "
                       f"Random entry ke {rd['sims']} run me se {rd['pct_rank']:.0f}% se behtar.")
    why, nostop = [], []
    for sym in SYMS:
        v = V[f"{sym}_A"]
        s, tr = v["stats"]["all"], v["trades"]
        if not tr:
            continue
        sl = sum(t["reason"] in (R.EXIT_SL, R.EXIT_GAP) for t in tr) / len(tr) * 100
        dist = float(np.mean([(t["fill"] - t["stop"]) / t["fill"] * 100 for t in tr]))
        be = 100 / (1 + s["payoff"]) if s["payoff"] else None
        why.append(f"{Ds[sym]['label']} A: {sl:.0f}% trade stop pe band (stop average sirf {dist:.1f}% door, RSI 70 tak ~{s['avg_days']:.0f} din lagte hain); "
                   f"win {s['win_pct']:.0f}% pe profit ke liye win {be:.0f}% chahiye tha (payoff {f2(s['payoff'])}); market me sirf {s['exposure']:.0f}% time")
        c = next(c for c in v["checks"] if c[0].startswith("Stop-loss nahi"))
        nostop.append(f"{Ds[sym]['label']} A {c[3]:+.1f}% (PF {f2(c[5])}, out-of-sample {c[13]:+.1f}%)")
    out.append("<b>Kyun nahi chala</b>: " + "; ".join(why) + ". Isliye bull market ka bada hissa chhoot gaya aur buy-and-hold se kaafi peeche.")
    out.append("<b>Ek idea (sabit nahi)</b>: stop hata ke sirf RSI 70 exit pe: " + ", ".join(nostop) + ". Ye result dekh ke nikala gaya hai (hindsight), "
               "isliye naye data pe alag se test karna hoga; aur tab bhi buy-and-hold se neeche, aur bina stop ka risk bahut zyada.")
    nA = V["NIFTY50_A"]["stats"]["all"]["trades"] + V["RELIANCE_A"]["stats"]["all"]["trades"]
    nB = V["NIFTY50_B"]["stats"]["all"]["trades"] + V["RELIANCE_B"]["stats"]["all"]["trades"]
    oB = V["NIFTY50_B"]["stats"]["oos"]["trades"] + V["RELIANCE_B"]["stats"]["oos"]["trades"]
    out.append(f"<b>Trade kam hain</b>: version A me dono instrument mila ke {nA}, version B me sirf {nB} trade ({oB} out-of-sample). "
               "B ke number par koi bhi faisla statistically kamzor hai.")
    out.append("<b>Overfitting</b>: koi parameter tune nahi kiya (sab brief se). Padosi settings (Extra checks tab) sirf dikhane ke liye; unme se best chunna "
               "out-of-sample ko dekh ke chunna hota, isliye nahi chuna.")
    out.append("<b>Data ki seema</b>: sirf 2 instrument, ~5.8 saal (ek hi bada bull + sideways daur). NIFTY index ko ETF jaisa maana (tracking error nahi, "
               "STT poora lagaya jo ETF pe kam hota hai). RELIANCE demerger khud adjust kiya. Close = aakhri trade ka price, official close nahi.")
    return out


SETTINGS = [["Candle", "2-hour: 09:15 / 11:15 / 13:15-15:30", 1], ["Period", "1 Jan 2021 - 30 Oct 2026"],
            ["In / out sample", "pehle 70% / aakhri 30% din", 1], ["Capital", "Rs 10 lakh har instrument, compounding ON", 1], ["Risk / trade", "1% abhi ki equity", 1],
            ["Indicator", "RSI 14 (Wilder) close pe"], ["Entry", "RSI 30 ke upar cross -> agli candle open"], ["Exit", "RSI 70 ke upar cross -> agli candle open"],
            ["Stop-loss", "entry - 2 x ATR(14), fix", 1], ["Leverage", "nahi; kul khula risk <= 5%"], ["Risk rules", "din 3% / hafta 6% loss pe entry band; 3 loss ke baad 1 signal skip"],
            ["Slippage", "0.05% har fill", 1], ["Kharcha", "Rs 20/order + STT, stamp, NSE, SEBI, GST, DP", 1], ["Data", "Fyers 1-min, split / bonus / demerger adjusted"],
            ["Dividend", "RELIANCE: cash me"]]

RULES = [
    "Candle: 2 ghante ki candle, 1-minute data se banayi. Har din 3 candle: 09:15-11:15, 11:15-13:15, 13:15-15:30.",
    "Indicator: RSI 14 (Wilder ka tareeka), close pe. Stop ke liye ATR 14 (Wilder). Version B me EMA 200 bhi (2-hour chart pe).",
    "Signal sirf candle band hone ke baad banta hai (aage ka data kabhi use nahi).",
    "Buy signal: pichhli candle ka RSI 30 ya usse kam, aur is candle ka RSI 30 se upar. Version B: saath me close EMA 200 se upar hona chahiye.",
    "Entry: signal ke baad agli candle ke open pe. Sirf long. Position pehle se khuli ho to naya buy nahi (add nahi karte).",
    "Sell signal: pichhli candle ka RSI 70 ya kam, aur is candle ka RSI 70 se upar. Exit agli candle ke open pe.",
    "Stop-loss: entry - 2 x ATR(14). Entry pe fix, kabhi neeche nahi. Candle ka low stop chhue to stop price pe bahar; candle stop ke neeche khule to usi candle ke open pe bahar.",
    "Stop lagne ke baad naye buy signal ka intezaar, tabhi dobara entry.",
    "Qty = (abhi ki capital x 1%) / (entry - stop), neeche round. Leverage nahi: position cash se badi ho to qty kam. Kul khula risk 5% se zyada nahi.",
    "Din me 3% loss ho jaye to us din naya entry nahi. Hafte me 6% loss to us hafte naya entry nahi. Lagatar 3 haar ke baad agla valid signal chhod do.",
    "Kharcha: brokerage Rs 20 har order + STT, stamp, NSE, SEBI, GST, DP charge; slippage 0.05% har buy / sell pe. Saare result kharche ke baad.",
    "In-sample = pehle 70% trading din, out-of-sample = aakhri 30%. Koi parameter tune nahi kiya; sab brief se fix.",
    "Version A = upar ke rules. Version B = wahi, bas buy tabhi jab close EMA 200 se upar. Dono ko buy-and-hold se milaya.",
]

NOTES = [
    "Brief me instrument, capital aur kharcha khaali the: NIFTY 50 aur RELIANCE (brief ke example), Rs 10 lakh har instrument (alag account), Rs 20 brokerage / order + poore delivery charges, 0.05% slippage liya.",
    "NIFTY 50 index seedha nahi khareed sakte: ETF jaisa unit maana (price = index / 100), tracking error nahi. STT poora 0.1% lagaya (ETF pe asal STT kam hai), isliye NIFTY ka result thoda kharab side pe hai.",
    "\"Abhi ki capital\" = mark-to-market equity. Entry ke waqt koi position khuli nahi hoti, isliye ye cash ke barabar hai. Compounding ON.",
    "Stop ka ATR = signal candle ka ATR (entry ke waqt yahi pata hota hai). Entry price = fill (slippage ke baad).",
    "Din ka loss = abhi ki equity vs pichhle din ke close ki equity; hafte ka loss = vs pichhle hafte ke aakhri close ki equity. Check entry ke waqt hota hai.",
    "3 haar ke baad ek signal chhodne ke baad ginti 0 se shuru (warna kabhi trade hi nahi hota). Haar = kharche ke baad net < 0.",
    "Jis candle me stop laga, usi candle ke close pe RSI 30 cross kare to wo naya signal maana (stop ke baad aaya).",
    "Usi din entry aur exit ho to intraday charges (STT 0.025% sell, stamp 0.003%, DP nahi). Kharcha exit pe kaata; isliye equity curve me khule trade ka kharcha exit tak nahi dikhta.",
    "Period ke end (30 Oct 2026) pe khula trade aakhri close pe band maana.",
    "Buy-and-hold: pehli candle ke open pe poori capital, aakhri close pe bechna, wahi kharcha + slippage; RELIANCE ka dividend cash me (reinvest nahi). NIFTY price index hai, dividend nahi (TRI thoda zyada hota).",
    "Sharpe: roz ki equity ka return, x root(252), risk-free 0 (6.5% risk-free wala bhi dikhaya). Max drawdown har 2-hour candle ke close pe.",
    "Ek instrument + add nahi = ek waqt me max 1% risk, isliye 5% kul risk wala rule kabhi lagu nahi hua.",
]

FORMULAS = [
    ["RSI (Wilder, 14)", "change = close - pichhla close; avg gain / avg loss: pehle 14 ka simple average, phir avg = avg + (aaj - avg) / 14; RSI = 100 - 100 / (1 + avg gain / avg loss)"],
    ["ATR (Wilder, 14)", "TR = max(high - low, |high - pichhla close|, |low - pichhla close|); ATR = TR ka Wilder average (14)"],
    ["EMA 200", "EMA = pichhla EMA + 2 / 201 x (close - pichhla EMA); shuru me pehle 200 close ka simple average"],
    ["Buy signal", "RSI[pichhli] <= 30 aur RSI[abhi] > 30 (B: aur close > EMA200)"],
    ["Sell signal", "RSI[pichhli] <= 70 aur RSI[abhi] > 70"],
    ["Entry fill", "agli candle ka open x (1 + 0.05%)"],
    ["Stop", "entry fill - 2 x ATR(signal candle)"],
    ["Qty", "floor( min( 1% x equity / (entry - stop), cash / (entry x (1 + buy charges)) ) ); NIFTY me price = index / 100"],
    ["Exit fill", "RSI exit: agla open x (1 - 0.05%); SL: stop x (1 - 0.05%); gap: us candle ka open x (1 - 0.05%)"],
    ["Gross P&L", "qty x (exit fill - entry fill)  (slippage isi me shamil)"],
    ["Kharcha", "brokerage 2 x Rs 20; STT 0.1% x (buy + sell) [same din: 0.025% x sell]; stamp 0.015% x buy [same din: 0.003%]; NSE 0.00307% + SEBI 0.0001% x (buy + sell); GST 18% x (brokerage + NSE + SEBI); DP Rs 15.34 + GST (delivery sell)"],
    ["Net P&L", "gross - kharcha + dividend"],
    ["R", "net / (qty x (entry - stop))"],
    ["Win rate", "net > 0 wale trade / kul trade"],
    ["Average jeet / haar", "jeete trades ka average net / haare trades ka average net (Rs, aur % of equity)"],
    ["Profit factor", "jeete trades ka kul net / |haare trades ka kul net|"],
    ["Expectancy", "average net per trade (Rs, R, aur % of trade se pehle ki equity)"],
    ["Total return / CAGR", "end equity / start equity - 1;  (end / start)^(1 / saal) - 1, saal = din / 365.25"],
    ["Max drawdown", "min( equity / ab tak ka sabse uncha equity - 1 ), har 2-hour candle ke close pe"],
    ["Sharpe", "average(roz ka return) / std(roz ka return) x root(252); dusra: roz ke return se 6.5% / 252 ghata ke"],
    ["Holding", "entry se exit tak trading din (same din = 0) aur 2-hour candle"],
    ["Sabse lambi haar", "lagatar kitne trade net < 0"],
    ["Saal / mahina Return %", "us saal / mahine ka equity badlav / us saal / mahine ki shuru ki equity (compounding ON)"],
    ["Buy-and-hold", "qty = floor(capital / pehla open), aakhri close tak rakha, dividend cash, wahi kharcha + slippage"],
    ["Bootstrap", "trade returns (% of equity) se 10,000 baar random dobara chuna; average ka 95% range"],
    ["Random entry test", "utne hi buy signal random candles pe (B: sirf close > EMA200 wali), baaki sab same, 300 baar; asli return kitne % run se behtar"],
]


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--no-telegram", action="store_true")
    ap.add_argument("--status-file")
    args = ap.parse_args()
    status = (lambda s: Path(args.status_file).write_text(s + "\n", encoding="utf-8")) if args.status_file else (lambda s: None)
    try:
        return run(args, status)
    except Exception:
        err = traceback.format_exc()
        print(err, flush=True)
        if not args.no_telegram:
            send(f"{TITLE} run me error {TAG_TECH}:\n{err[-1500:]}")
        return 1


if __name__ == "__main__":
    sys.exit(main())
