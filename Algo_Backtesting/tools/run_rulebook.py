"""Rulebook strategies (orb / noise / vwap / fh) on the fast data store, the project way:
results/NN_<name>/, Telegram (start, every 10 min, finish), run log, DEV / FINAL protocol.

    python tools/run_rulebook.py --strategy orb                        # DEV 2021-10-01 -> 2023-12-31 (2023 = validation)
    python tools/run_rulebook.py --strategy orb --grid "stop_atr=0.1,0.25,0.5;rvol_min=1,1.5,2"    # DEV grid
    python tools/run_rulebook.py --strategy orb --final --stop-atr 0.25 --rvol-min 1.5   # FINAL 2024 -> latest, once per setting
    python tools/run_rulebook.py --strategy noise --workers 6 --no-telegram

Every engine flag works (see python src/backtest/rulebook.py -h). Rules: strategies/RULEBOOK.md.
Writes trades.csv, equity.csv, yearly.csv, breakdown.csv, summary.json, equity.png and report_data.json
(rules / formulas / settings / months / investment, in Hinglish) for the HTML report; if src/backtest/rulebook_report.py
exists (write_html(out_dir, data)), the HTML report is built and sent too.
"""
import argparse
import json
import math
import os
import re
import sys
import time
import traceback
from dataclasses import asdict, replace
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

import numpy as np  # noqa: E402
import pandas as pd  # noqa: E402

from backtest import rulebook as rb  # noqa: E402
from backtest.config import RESULTS, result_dir  # noqa: E402
from backtest.notify import send, send_document  # noqa: E402

TAG_TRADING, TAG_TECH = "@ayushanand440", "@albert_max"
PERIODS = {"DEV": ("2021-10-01", "2023-12-31", "2023-01-01"), "FINAL": ("2021-10-01", "2099-12-31", "2024-01-01")}
FINAL_START = "2024-01-01"
RUN_LOG = RESULTS / "RULEBOOK_RUNS.csv"
LOG_COLS = ["when", "name", "strategy", "mode", "params", "is_sharpe", "oos_sharpe", "oos_trades", "oos_return_pct",
            "stress_return_pct", "all_pass", "folder"]
NAMES = {"orb": "ORB-5 Stocks in Play", "noise": "Noise-band momentum", "vwap": "VWAP trend", "fh": "First half-hour momentum"}


# ------------------------------------------------------------------------------------------------- Hinglish texts
def rules_text(p: rb.Params, mode: str, data_range: tuple[str, str]) -> list[str]:
    uni = (f"Universe (har din, sirf pichhle din tak ka data): pichhle 20 din ka average turnover >= Rs {p.min_turnover_cr:g} Cr, "
           f"aaj ka open >= Rs {p.min_price:g}, ATR14 >= {p.min_atr_pct:g}% of price.")
    size_vol = (f"Qty: har stock ko equity x min({p.max_leverage:g}, {p.sigma_target:g} / daily sigma) / {p.top_n} rupaye; "
                f"20-din average volume ka max {p.liq_cap_pct:g}%.")
    by_s = {
        "orb": [uni,
                f"RVOL = aaj 9:15-9:20 ki volume / pichhle 14 din ki 9:15-9:20 average volume. RVOL >= {p.rvol_min:g} wale stocks me "
                f"se top {p.top_n} (sabse zyada RVOL).",
                "Direction: pehli 5-min candle (9:15-9:20) green -> sirf BUY, red -> sirf SELL, barabar -> trade nahi.",
                f"Entry: 9:20 se {p.entry_cutoff} tak price OR high (BUY) / OR low (SELL) cross kare -> us level par fill; candle level "
                "ke paar khule to us candle ke open par. Ek stock me din me ek trade.",
                f"Stop-loss = entry - {p.stop_atr:g} x ATR14 (BUY), entry + {p.stop_atr:g} x ATR14 (SELL). Entry wali candle me SL "
                "touch = SL hit. SL ke paar gap se khule to candle ke open par exit.",
                f"Exit: SL, warna {p.exit_time} candle ke open par. Target nahi.",
                f"Qty = min(equity ka {p.risk_pct:g}% risk / SL doori, {p.max_leverage:g} x equity / ({p.top_n} x entry)); "
                f"20-din average volume ka max {p.liq_cap_pct:g}%."],
        "noise": [uni + f" Top {p.top_n} sabse zyada turnover wale.",
                  f"sigma(time) = pichhle {p.lookback} din me usi time par |close / din ka open - 1| ka average.",
                  f"Upper band = max(aaj open, kal close) x (1 + {p.vm:g} x sigma); Lower band = min(aaj open, kal close) x "
                  f"(1 - {p.vm:g} x sigma).",
                  "Har 30 min (9:45, 10:15 ... 14:45) check: close > upper band -> BUY, close < lower band -> SELL; fill agli "
                  "candle ke open par.",
                  f"Exit: BUY jab close < max(upper band, VWAP); SELL jab close > min(lower band, VWAP); baaki {p.exit_time} par.",
                  size_vol],
        "vwap": [uni + f" RVOL (9:15-9:20) >= {p.vwap_rvol_min:g}, top {p.top_n} by RVOL.",
                 f"{p.vwap_bar}-min bars, 9:30 se: bar close > VWAP x (1 + {p.vwap_buffer_pct:g}%) -> BUY; < VWAP x "
                 f"(1 - {p.vwap_buffer_pct:g}%) -> SELL; fill agli candle ke open par.",
                 f"Exit: BUY jab bar close < VWAP, SELL jab bar close > VWAP. Din me max {p.vwap_max_entries} entry, "
                 f"{p.entry_cutoff} ke baad naya entry nahi, sab {p.exit_time} par band.",
                 size_vol],
        "fh": [uni + f" Top {p.top_n} sabse zyada turnover wale.",
               "r = 9:45 ka close / kal ka close - 1. 9:15-9:45 ki volume >= uska 14-din average.",
               f"|r| >= {p.fh_k_sigma:g} x daily sigma ho to r ki direction me trade: entry {p.fh_entry} candle ke open par, "
               f"exit {p.exit_time} candle ke open par.",
               size_vol],
    }[p.strategy]
    period = (f"Period: {data_range[0]} -> {data_range[1]}. " + (
        "DEV test: 2021-22 par rules dekhe, 2023 = validation (out-of-sample). 2024 ke baad ka data is test me nahi."
        if mode == "DEV" else "FINAL test: 2024 se aaj tak ka data sirf ek baar, setting pehle se fix (DEV se)."))
    common = [f"Daily loss limit: din ka realized loss {p.daily_loss_limit_pct:g}% equity tak pahunche to us din naya trade nahi.",
              f"Profit compound hota hai: har din ki position size us din ki shuruati equity se (start Rs {p.capital:,.0f}).",
              f"Charges: brokerage min(Rs {p.brokerage_cap:g}, {p.brokerage_pct:g}%) har order, STT {p.stt_pct:g}% sell, NSE "
              f"{p.exch_pct:g}%, SEBI Rs {p.sebi_per_cr:g}/Cr, stamp {p.stamp_pct:g}% buy, GST {p.gst_pct:g}%. Slippage "
              f"{p.slippage_pct:g}% har side; stress test me {2 * p.slippage_pct:g}%.",
              "Koi lookahead nahi: din ke features sirf pichhle din tak ke; signal candle ke baad agli candle ke open par fill.",
              f"Bade gap (> {p.gap_skip_pct:g}%) ke baad us stock ko {max(20, p.lookback) + 1} din skip (shayad unadjusted split).",
              period,
              "Stock list = AMFI Jun-2026 list (1,386 stocks): jo stocks 2021-26 me delist/merge hue ya chhote ho gaye woh data me "
              "nahi (survivorship bias) - results thode optimistic ho sakte hain."]
    return by_s + common


def formulas_text(p: rb.Params) -> list[str]:
    f = ["TR = max(H - L, |H - kal close|, |L - kal close|); ATR14 = pichhle 14 din ke TR ka average",
         "Avg turnover20 = pichhle 20 din ke (close x volume) ka average",
         "Daily sigma = pichhle 14 din ke daily return (close / kal close - 1) ka standard deviation"]
    if p.strategy in ("orb", "vwap"):
        f.append("RVOL = aaj 9:15-9:20 volume / pichhle 14 din ka 9:15-9:20 average volume")
    if p.strategy == "orb":
        f += [f"Entry (BUY) = max(OR high, candle open); SL = entry - {p.stop_atr:g} x ATR14 (SELL ulta)",
              f"Qty = floor(min({p.risk_pct:g}% x equity / |entry - SL|, {p.max_leverage:g} x equity / ({p.top_n} x entry)))",
              "SL exit = SL price, ya gap ho to candle open (jo bura ho); warna exit = 15:15 open",
              "R = net P&L / (|entry - SL| x qty)"]
    elif p.strategy == "noise":
        f += [f"sigma(t) = average(|close_t / open - 1|) pichhle {p.lookback} din", f"UB = max(open, kal close) x (1 + {p.vm:g} x sigma(t)); "
              f"LB = min(open, kal close) x (1 - {p.vm:g} x sigma(t))", "VWAP = sum(typical price x volume) / sum(volume), typical = (H+L+C)/3"]
    elif p.strategy == "vwap":
        f += [f"BUY jab bar close > VWAP x (1 + {p.vwap_buffer_pct / 100:g}); SELL jab < VWAP x (1 - {p.vwap_buffer_pct / 100:g})",
              "VWAP = sum(typical price x volume) / sum(volume), typical = (H+L+C)/3"]
    else:
        f += ["r = close(9:44 candle) / kal close - 1; trade jab |r| >= k x daily sigma aur 30-min volume >= 14-din average"]
    if p.strategy != "orb":
        f.append(f"Qty = floor(equity x min({p.max_leverage:g}, {p.sigma_target:g} / daily sigma) / {p.top_n} / entry)")
    f += [f"Max qty = {p.liq_cap_pct:g}% x 20-din average volume",
          f"Fill: BUY = price x (1 + {p.slippage_pct / 100:g}), SELL = price x (1 - {p.slippage_pct / 100:g})",
          "Brokerage = min(20, 0.03% x order value) har order; STT = 0.025% x sell; NSE = 0.00307% x (buy + sell); "
          "SEBI = 10 / 1 Cr x (buy + sell); stamp = 0.003% x buy; GST = 18% x (brokerage + NSE + SEBI)",
          "Net P&L = side x (exit - entry) x qty - slippage - charges",
          "Return % = net / us din ki shuruati equity x 100 (equity compound hoti hai)",
          "Win rate = profit wale trades / total trades x 100", "Profit factor = total profit / total loss",
          "Max drawdown = equity ki pichhli peak se sabse badi % girawat", "Sharpe = average(daily return) / std(daily return) x sqrt(252)",
          "t-stat = average(trade return) / std(trade return) x sqrt(trades)"]
    return f


def settings_chips(p: rb.Params, mode: str, n_stocks: int) -> list[list]:
    """[label, value, highlight] for the 'Test ki settings' strip."""
    s = [["Strategy", NAMES[p.strategy], True], ["Test", mode, True],
         ["Universe", f"turnover >= Rs {p.min_turnover_cr:g} Cr, price >= Rs {p.min_price:g}, ATR >= {p.min_atr_pct:g}%", True],
         ["Stocks checked", str(n_stocks), False], ["Top N / din", str(p.top_n), False],
         ["Capital", f"Rs {p.capital:,.0f}", False], ["Compounding", "haan (roz)", True],
         ["Leverage max", f"{p.max_leverage:g}x", False], ["Slippage", f"{p.slippage_pct:g}% / side (stress {2 * p.slippage_pct:g}%)", True],
         ["Charges", "Zerodha/Fyers intraday", False], ["Daily loss limit", f"{p.daily_loss_limit_pct:g}%", False],
         ["Max qty", f"{p.liq_cap_pct:g}% of avg volume", False], ["Exit", p.exit_time, False]]
    if p.strategy == "orb":
        s += [["Risk / trade", f"{p.risk_pct:g}%", True], ["SL", f"{p.stop_atr:g} x ATR14", True], ["RVOL min", f"{p.rvol_min:g}", False],
              ["Entry till", p.entry_cutoff, False], ["Side", "BUY + SELL (pehli candle ki direction)", False]]
    elif p.strategy == "noise":
        s += [["VM", f"{p.vm:g}", True], ["Lookback", f"{p.lookback} din", False], ["Vol target", f"{p.sigma_target:g}", False]]
    elif p.strategy == "vwap":
        s += [["Buffer", f"{p.vwap_buffer_pct:g}%", True], ["Bar", f"{p.vwap_bar} min", False], ["Max entries", str(p.vwap_max_entries), False],
              ["RVOL min", f"{p.vwap_rvol_min:g}", False]]
    else:
        s += [["k x sigma", f"{p.fh_k_sigma:g}", True], ["Entry", p.fh_entry, False]]
    if p.filters:
        s.append(["Filters", p.filters, True])
    return s


# ---------------------------------------------------------------------------------------------- numbers for report
def months_table(eq: pd.DataFrame, trades: pd.DataFrame) -> list[list]:
    if eq.empty:
        return []
    e = eq.assign(m=eq["date"].dt.strftime("%Y-%m"))
    rows = []
    for m, g in e.groupby("m"):
        start = g["equity"].iloc[0] - g["day_pnl"].iloc[0]
        t = trades[trades["date"].dt.strftime("%Y-%m") == m] if len(trades) else trades
        rows.append([m, round(float(g["day_pnl"].sum()), 2), round(float(g["equity"].iloc[-1] / start - 1) * 100, 3), int(len(t)),
                     round(float((t["net_pnl"] > 0).mean() * 100), 1) if len(t) else None])
    return rows


def investment(p: rb.Params, eq: pd.DataFrame, trades: pd.DataFrame) -> dict:
    out = {"capital": p.capital, "risk_per_trade": f"{p.risk_pct:g}% of equity" if p.strategy == "orb" else "vol target",
           "qty_rule": ("min(risk / SL doori, leverage cap)" if p.strategy == "orb" else "equity x min(L, target/sigma) / N"),
           "max_per_stock": f"{p.max_leverage:g} x equity / {p.top_n}, aur {p.liq_cap_pct:g}% of avg volume",
           "max_open_positions": p.top_n}
    if len(trades):
        out["avg_position"] = round(float(trades["notional"].mean()), 0)
        out["biggest_position"] = round(float(trades["notional"].max()), 0)
        peak = 0.0
        for _, g in trades.groupby("date"):
            ev = sorted([(rb.m_of(t), n) for t, n in zip(g["entry_time"], g["notional"])] +
                        [(rb.m_of(t), -n) for t, n in zip(g["exit_time"], g["notional"])], key=lambda x: (x[0], x[1]))
            run = 0.0
            for _, n in ev:
                run += n
                peak = max(peak, run)
        out["peak_deployed"] = round(peak, 0)
    return out


# ------------------------------------------------------------------------------------------------------- run log
def changed_params(p: rb.Params) -> str:
    base = asdict(rb.with_defaults(replace(rb.Params(), strategy=p.strategy)))
    now = asdict(p)
    diff = {k: v for k, v in now.items() if k not in ("strategy", "start", "end", "oos_start") and v != base[k]}
    return ";".join(f"{k}={diff[k]}" for k in sorted(diff))


def read_log() -> pd.DataFrame:
    return pd.read_csv(RUN_LOG, dtype=str).fillna("") if RUN_LOG.exists() else pd.DataFrame(columns=LOG_COLS)


def append_log(row: dict) -> int:
    RESULTS.mkdir(parents=True, exist_ok=True)
    log = pd.concat([read_log(), pd.DataFrame([{k: row.get(k, "") for k in LOG_COLS}])], ignore_index=True)
    log.to_csv(RUN_LOG, index=False)
    return int((log["strategy"] == row["strategy"]).sum())


def clean(v):
    """JSON-safe: inf / nan -> None, numpy -> python (JSON.parse in the browser rejects Infinity / NaN)."""
    if isinstance(v, dict):
        return {k: clean(x) for k, x in v.items()}
    if isinstance(v, (list, tuple)):
        return [clean(x) for x in v]
    if hasattr(v, "item"):
        v = v.item()
    if isinstance(v, float) and not math.isfinite(v):
        return None
    return v


def fnum(v, nd=2):
    return "-" if v is None or (isinstance(v, float) and not np.isfinite(v)) else f"{v:,.{nd}f}"


def line(tag: str, m: dict) -> str:
    if not m:
        return f"{tag}: no data"
    return (f"{tag}: {m.get('trades', 0)} trades | win {fnum(m.get('win_rate_pct'), 1)}% | return {fnum(m.get('total_return_pct'), 1)}% | "
            f"PF {fnum(m.get('profit_factor'))} | Sharpe {fnum(m.get('sharpe'))} | max DD {fnum(m.get('max_dd_pct'), 1)}%")


# ------------------------------------------------------------------------------------------------------------ main
def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--final", action="store_true", help="FINAL test 2024 -> latest (once per frozen setting)")
    ap.add_argument("--force-final", action="store_true", help="repeat a FINAL that already ran (logged; avoid)")
    ap.add_argument("--name", default=None, help="results folder name (default rb_<strategy>_<dev|final>[_<changed params>])")
    ap.add_argument("--grid", default=None, help='DEV only, e.g. "stop_atr=0.1,0.25,0.5;rvol_min=1,1.5,2"')
    ap.add_argument("--workers", type=int, default=max(1, min(6, (os.cpu_count() or 2) - 1)))
    ap.add_argument("--no-prefilter", action="store_true")
    ap.add_argument("--no-telegram", action="store_true")
    for fld in rb.Params.__dataclass_fields__.values():
        typ = str if fld.name in rb.STR_FIELDS else (int if fld.name in rb.INT_FIELDS else float)
        kw = dict(type=typ, default=None)
        if fld.name == "strategy":
            kw.update(choices=list(rb.GENERATORS), required=True)
        ap.add_argument("--" + fld.name.replace("_", "-"), **kw)
    a = ap.parse_args()
    mode = "FINAL" if a.final else "DEV"
    vals = {k: getattr(a, k) for k in rb.Params.__dataclass_fields__ if getattr(a, k) is not None}
    s, e, o = PERIODS[mode]
    vals.setdefault("start", s)
    vals.setdefault("end", e)
    vals.setdefault("oos_start", o)
    p = rb.with_defaults(replace(rb.Params(), **vals))
    if mode == "DEV" and p.end >= FINAL_START:
        sys.exit(f"DEV run 2024 ka data nahi chhoo sakta (end {p.end}). 2024+ sirf --final se.")
    if a.grid and mode == "FINAL":
        sys.exit("Grid sirf DEV par. FINAL me ek hi frozen setting chalti hai.")
    key = changed_params(p)
    if mode == "FINAL" and not a.force_final:
        log = read_log()
        prev = log[(log["strategy"] == p.strategy) & (log["mode"] == "FINAL") & (log["params"] == key)]
        if len(prev):
            sys.exit(f"Ye setting FINAL par pehle chal chuki hai ({prev['when'].iloc[-1]}, {prev['folder'].iloc[-1]}). "
                     "Dobara chalane se FINAL ka matlab khatam ho jaata hai. Naya idea -> DEV. (--force-final sirf zaroori ho to.)")
    suffix = re.sub(r"[^A-Za-z0-9._]+", "-", key.replace("=", "")).strip("-")[:60]
    name = a.name or f"rb_{p.strategy}_{mode.lower()}" + ("_grid" if a.grid else "") + (f"_{suffix}" if suffix else "")
    tell = (lambda m: print(m, flush=True)) if a.no_telegram else (lambda m: (print(m, flush=True), send(m)))
    out = result_dir(name)
    t0 = last = time.time()
    tell(f"RULEBOOK {p.strategy.upper()} ({NAMES[p.strategy]}) {mode}{' GRID' if a.grid else ''} STARTED -> {out.name}. "
         f"Period {p.start} -> {'latest' if p.end >= '2099' else p.end}, OOS from {p.oos_start}. Settings: {key or 'rulebook defaults'}. "
         "Update har 10 min.")

    def progress(done, total):
        nonlocal last
        if time.time() - last >= 600:
            last = time.time()
            tell(f"RULEBOOK {p.strategy.upper()} progress: {done}/{total} stocks, {(time.time() - t0) / 60:.0f} min")

    try:
        files, cache_dir, _ = rb.sources(None, True, p, prefilter=not a.no_prefilter)
        if a.grid:
            df = rb.run_grid(files, cache_dir, p, a.grid, str(out), workers=a.workers, progress=progress)
            when = datetime.now().strftime("%Y-%m-%d %H:%M")
            for r in df.to_dict("records"):
                q = replace(p, **{k: r[k] for k in df.attrs.get("keys", [])})
                n = append_log({"when": when, "name": out.name, "strategy": p.strategy, "mode": "DEV-grid", "params": changed_params(q),
                                "is_sharpe": r["IS_sharpe"], "oos_sharpe": r["OOS_sharpe"], "oos_trades": r["OOS_trades"],
                                "oos_return_pct": r["OOS_return_pct"], "stress_return_pct": r["OOS_stress_return_pct"],
                                "all_pass": r["all_checks_pass"], "folder": out.name})
            top = df.head(5).to_string(index=False, float_format=lambda v: f"{v:,.2f}")
            tell(f"{TAG_TRADING} RULEBOOK {p.strategy.upper()} DEV GRID DONE ({(time.time() - t0) / 60:.0f} min), {len(df)} settings. "
                 f"Top 5 by 2021-22 Sharpe (2023 = validation):\n{top}\nVariants tried so far for {p.strategy}: {n}. "
                 "Setting tab chuno jab aas-paas ki settings bhi achhi hon (plateau); 2023 bhi positive ho.")
            return 0
        uni, eq, trades, eq_s, tr_s = rb.run_once(files, cache_dir, p, workers=a.workers, progress=progress)
        if not len(eq):
            raise RuntimeError("is period me koi data nahi mila")
        m_is, m_oos, m_st, verdict = rb.report(eq, trades, eq_s, tr_s, p, str(out), False)
        checks = rb.pass_fail(m_is, m_oos, m_st)
        rng = (str(eq["date"].min().date()), str(eq["date"].max().date()))
        data = {"strategy": p.strategy, "strategy_name": NAMES[p.strategy], "mode": mode, "data_range": rng,
                "oos_start": p.oos_start, "params": asdict(p), "changed": key, "compounding": True,
                "settings": settings_chips(p, mode, len(files)), "rules": rules_text(p, mode, rng), "formulas": formulas_text(p),
                "metrics": {"full": rb.metrics(eq, trades), "in_sample": m_is, "out_of_sample": m_oos, "stress_oos": m_st},
                "checks": [[n, bool(ok)] for n, ok in checks], "verdict": "PASS" if verdict else "FAIL",
                "months": months_table(eq, trades), "investment": investment(p, eq, trades),
                "data_note": "Fyers 1-min data; stock list = AMFI Jun-2026 (survivorship bias). Prices adjusted with data/store/adjustments.csv.",
                "generated": datetime.now().strftime("%d %b %Y %H:%M")}
        (out / "report_data.json").write_text(json.dumps(clean(data), indent=1, default=str), encoding="utf-8")
        n = append_log({"when": datetime.now().strftime("%Y-%m-%d %H:%M"), "name": out.name, "strategy": p.strategy, "mode": mode,
                        "params": key, "is_sharpe": m_is.get("sharpe"), "oos_sharpe": m_oos.get("sharpe"), "oos_trades": m_oos.get("trades"),
                        "oos_return_pct": m_oos.get("total_return_pct"), "stress_return_pct": m_st.get("total_return_pct"),
                        "all_pass": verdict, "folder": out.name})
        html = None
        try:
            from backtest.rulebook_report import write_html          # built once by Claude per CLAUDE.md report rules
            html = write_html(out, data)
        except ImportError:
            pass
    except Exception:
        tell(f"{TAG_TECH} RULEBOOK {p.strategy.upper()} {mode} FAILED:\n{traceback.format_exc()[-1500:]}")
        raise
    oos_tag = "2023 validation" if mode == "DEV" else "FINAL 2024+"
    passed = sum(ok for _, ok in checks)
    msg = (f"{TAG_TRADING} RULEBOOK {p.strategy.upper()} {mode} DONE ({(time.time() - t0) / 60:.1f} min) -> {out.name}\n"
           f"Data {rng[0]} -> {rng[1]}, {len(files)} stocks checked. Settings: {key or 'rulebook defaults'}\n"
           + line("Build (in-sample)", m_is) + "\n" + line(oos_tag, m_oos) + "\n" + line("Stress 2x slippage", m_st) + "\n"
           f"Checks {passed}/{len(checks)} PASS -> {'PASS' if verdict else 'FAIL'}. Variants tried so far for {p.strategy}: {n}.")
    tell(msg)
    if html and not a.no_telegram:
        send_document(html, f"Rulebook {p.strategy} {mode} report (browser me kholo)")
    elif not html:
        print("HTML report abhi nahi bana: src/backtest/rulebook_report.py banana hai (UPGRADE_GUIDE_HINGLISH.md, prompt 2).")
    return 0


if __name__ == "__main__":
    sys.exit(main())
