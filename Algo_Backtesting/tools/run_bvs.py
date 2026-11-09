"""Breadth + Sector + lowest-Volume candle intraday backtest on NSE F&O stocks (5-min bars): baseline, one-at-a-time variants,
a 432-setting grid fitted on the first 60% of days, control tests, splits, claims, HTML report + Telegram.

    python tools/run_bvs.py
    python tools/run_bvs.py --no-telegram --cache path/to/bvs_days.pkl
"""
import argparse
import itertools
import json
import sys
import tempfile
import time
import traceback
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

import numpy as np  # noqa: E402
import pandas as pd  # noqa: E402

from backtest import bvs, bvs_data  # noqa: E402
from backtest import rulebook as rb  # noqa: E402
from backtest.config import ROOT, result_dir  # noqa: E402
from backtest.notify import send, send_document  # noqa: E402
from backtest.rulebook_report import write_html  # noqa: E402

TAG_TRADING, TAG_TECH = "@ayushanand440", "@albert_max"
CAP = bvs.BASE["capital"]
FEES_NOTE = "Rs 20/order, STT (futures, sell), NSE exchange, SEBI, stamp (buy), GST - same rates as the PDH/PDL backtest (see Rules)"


def rs(v):
    return ("-" if v < 0 else "+") + f"Rs {abs(v):,.0f}"


# ----------------------------------------------------------------------------------------------------- statistics
def stat(t: pd.DataFrame) -> dict:
    n = len(t)
    if not n:
        return dict(n=0, net=0.0, win=np.nan, loss=np.nan, be=np.nan, hit2=np.nan, pf=np.nan, expr=np.nan, exprg=np.nan, dd=0.0, aw=np.nan, al=np.nan, costr=np.nan)
    w, l = t[t.net_pnl > 0], t[t.net_pnl < 0]
    cum = t.sort_values(["date", "entry_bar"]).net_pnl.cumsum().to_numpy()
    dd = float((cum - np.maximum.accumulate(np.r_[0, cum])[1:]).min())
    gl = -l.net_pnl.sum()
    return dict(n=n, net=float(t.net_pnl.sum()), win=len(w) / n * 100, loss=len(l) / n * 100, be=(t.reason == "BE").mean() * 100,
                hit2=((t.part_t != "") | (t.reason == "BE")).mean() * 100, pf=float(w.net_pnl.sum() / gl) if gl > 0 else np.nan,
                expr=float(t.R.mean()), exprg=float(t.R_gross.mean()), dd=dd, aw=float(w.R.mean()) if len(w) else np.nan,
                al=float(l.R.mean()) if len(l) else np.nan, costr=float(((t.fees + t.slippage) / (t.risk_px * t.shares)).mean()))


class Ctx:
    def __init__(self, days):
        self.days = days
        self.cut = days[int(len(days) * 0.6)].day
        self.n_is = sum(d.day < self.cut for d in days)
        self.exp = None

    def trades(self, v):
        tr, cnt = bvs.run(self.days, v)
        t = pd.DataFrame(tr)
        if len(t):
            t["period"] = np.where(t.date < self.cut, "IS", "OOS")
            t["year"] = t.date.str[:4]
        return t, cnt

    def split(self, t):
        if not len(t):
            e = stat(t)
            return e, e, e
        return stat(t[t.period == "IS"]), stat(t[t.period == "OOS"]), stat(t)

    def row(self, label, v, extra=None):
        t, cnt = self.trades(v)
        i, o, a = self.split(t)
        return [label, i["n"], i["net"], i["pf"], i["expr"], o["n"], o["net"], o["pf"], o["expr"], a["win"]] + (extra or [])


VCOLS = ["Setting", "IS trades", "IS net Rs", "IS PF", "IS exp R", "OOS trades", "OOS net Rs", "OOS PF", "OOS exp R", "Win % (all)"]


def nz(x):
    return None if x is None or (isinstance(x, float) and not np.isfinite(x)) else x


def clean_rows(rows):
    return [[nz(x.item() if hasattr(x, "item") else x) for x in r] for r in rows]


# ----------------------------------------------------------------------------------------------------- experiments
def sweeps():
    S = []
    def add(g, lab, **kw):
        S.append((g, lab, bvs.variant(**kw)))
    add("Baseline", "Baseline: top 2 stocks, measure A, color ON, stop-entry, EMA(10) 5-min")
    for lo, hi in [(0.6, 1.6), (0.8, 1.25)]:
        add("Breadth limit", f"A/D <= {lo} / >= {hi}", breadth=(lo, hi))
    add("Sector measure", "Measure B (share of stocks in trend direction)", measure="B")
    add("Sector count", "Top 2 sectors", top_sectors=2)
    for m in (0.3, 0.5):
        add("Sector min move", f"Sector move >= {m}%", min_sector_move=m)
    for n in (1, 3):
        add("Stocks", f"Top {n} stock{'s' if n > 1 else ''}", top_stocks=n)
    add("Color rule", "Color rule OFF", color=False)
    for n in (6, 24):
        add("Reference expiry", f"Reference expires after {n} bars", expire=n)
    add("Execution", "Entry on 5-min close beyond level, next open", exec=2)
    add("Execution", "Close-entry + stop at breakout bar extreme", exec=2, stop="break")
    add("Trail", "EMA(10) on 15-min bars", trail="ema15")
    add("Trail", "Step trail (+1R per extra 1R)", trail="step")
    for r in (1, 2, 3):
        add("Fixed exit", f"Single exit at {r}R, no partial", trail=f"f{r}")
    add("Re-entry", "Max 3 attempts / stock / day", attempts=3)
    add("Re-entry", "Max 1 attempt (no re-entry)", attempts=1)
    add("Risk", "Risk 0.5% per trade", risk_pct=0.005)
    for s in (0.0002, 0.001):
        add("Slippage", f"Slippage {s * 100:.2f}% per side", slip=s)
    add("Entry cutoff", "New entries until 15:00", cutoff="15:00")
    add("Liquidity", "Min 20-day value Rs 50 cr", min_value_cr=50.0)
    return S


GRID = {"breadth": [(0.7, 1.4), (0.6, 1.6), (0.8, 1.25)], "measure": ["A", "B"], "top_stocks": [1, 2, 3], "color": [True, False], "exec": [1, 2],
        "trail": ["ema5", "ema15", "step", "f1", "f2", "f3"]}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--name", default="breadth_sector_lowvol_2021_2026")
    ap.add_argument("--no-telegram", action="store_true")
    ap.add_argument("--cache", type=Path, default=Path(tempfile.gettempdir()) / "bvs_days.pkl")
    ap.add_argument("--rebuild", action="store_true")
    ap.add_argument("--seeds", type=int, default=20)
    a = ap.parse_args()
    tell = (lambda m: print(m, flush=True)) if a.no_telegram else (lambda m: (print(m, flush=True), send(m)))
    t0 = time.time()
    log = lambda m: print(f"[{(time.time() - t0) / 60:5.1f} min] {m}", flush=True)  # noqa: E731
    out = result_dir(a.name)
    try:
        days = bvs_data.load_days(a.cache, a.rebuild, log=log)
        X = Ctx(days)
        log(f"{len(days)} days {days[0].day} -> {days[-1].day}, cut {X.cut} ({X.n_is} IS days)")
        tell(f"BVS BACKTEST: data ready - {len(days)} din ({days[0].day} -> {days[-1].day}), average {np.mean([len(d.syms) for d in days]):.0f} F&O stocks/din. "
             f"Build/IS = {days[0].day} -> {days[X.n_is - 1].day}, OOS = {X.cut} -> {days[-1].day}. Ab baseline + variants + grid.")

        # ---------------- baseline
        base_v = bvs.variant()
        bt, bcnt = X.trades(base_v)
        bi, bo, ba = X.split(bt)
        log(f"baseline {ba['n']} trades net {ba['net']:,.0f} IS {bi['net']:,.0f} OOS {bo['net']:,.0f} win {ba['win']:.1f}% exp {ba['expr']:.3f}R {dict(bcnt)}")
        tell(f"BVS: baseline {ba['n']} trade, net {rs(ba['net'])} (IS {rs(bi['net'])}, OOS {rs(bo['net'])}), win {ba['win']:.0f}%, exp {ba['expr']:+.2f}R.")

        # ---------------- one-at-a-time variants
        vrows = []
        for g, lab, v in sweeps():
            vrows.append([g] + X.row(lab, v))
            log(f"sweep {lab}")
        # ---------------- grid (fit on IS only)
        keys = list(GRID)
        gres = []
        combos = list(itertools.product(*GRID.values()))
        for j, vals in enumerate(combos):
            v = bvs.variant(**dict(zip(keys, vals)))
            t, _ = X.trades(v)
            i, o, a_ = X.split(t)
            gres.append(dict(zip(keys, vals), i_n=i["n"], i_net=i["net"], i_expr=i["expr"], i_pf=i["pf"], o_n=o["n"], o_net=o["net"], o_expr=o["expr"], o_pf=o["pf"], win=a_["win"], n=a_["n"]))
            if j % 40 == 0:
                log(f"grid {j}/{len(combos)}")
        g = pd.DataFrame(gres)
        g.to_csv(out / "grid.csv", index=False)
        ok = g[g.i_n >= 200]
        best_row = ok.loc[ok.i_net.idxmax()]
        best_kw = {k: (best_row[k].item() if hasattr(best_row[k], "item") else best_row[k]) for k in keys}
        best_kw["breadth"] = tuple(best_kw["breadth"])
        best_v = bvs.variant(**best_kw)
        rank_corr = float(g.i_expr.rank().corr(g.o_expr.rank()))
        tell(f"BVS: grid poora ({len(g)} setting, sirf IS pe chuni). IS me {int((g.i_net > 0).sum())} profit me, OOS me {int((g.o_net > 0).sum())}; dono me {int(((g.i_net > 0) & (g.o_net > 0)).sum())}. "
             f"Ab controls + report.")
        bst, _ = X.trades(best_v)
        si, so, sa = X.split(bst)
        log(f"best {best_kw}: IS {si['net']:,.0f} OOS {so['net']:,.0f} n {sa['n']}")

        # ---------------- controls (baseline and best), random ones averaged over seeds
        ctl = []
        for tag, v0 in (("baseline", base_v), ("IS-best", best_v)):
            r0 = X.row(f"{tag}: full system", v0)
            ctl.append([tag] + r0)
            ctl.append([tag] + X.row(f"{tag}: (i) breadth filter OFF", dict(v0, breadth_on=False)))
            ctl.append([tag] + X.row(f"{tag}: (ii) sector filter OFF", dict(v0, sector_on=False)))
            for lab, kw in (("(iii) RANDOM sector", {"sector_random": True}), ("(iv) RANDOM candle instead of lowest volume", {"ref_mode": "random"})):
                rr = []
                for s in range(a.seeds):
                    t, _ = X.trades(dict(v0, seed=s + 1, **kw))
                    i, o, a_ = X.split(t)
                    rr.append([i["n"], i["net"], i["pf"], i["expr"], o["n"], o["net"], o["pf"], o["expr"], a_["win"]])
                m = np.nanmean(np.array(rr, dtype=float), axis=0)
                beat = float(np.mean([r[5] >= (so["net"] if tag != "baseline" else bo["net"]) for r in rr]) * 100)
                ctl.append([tag, f"{tag}: {lab} (mean of {a.seeds} seeds)"] + [float(x) for x in m] + [f"{beat:.0f}% seeds >= system OOS"])
            log(f"controls {tag}")

        # ---------------- splits on baseline trades
        def grp(t, col, label):
            rows = []
            for k, x in t.groupby(col):
                s = stat(x)
                rows.append([label, str(k), s["n"], s["net"], s["win"], s["expr"], s["pf"]])
            return rows
        sp = bt.copy()
        sp["hour"] = sp.entry_t.str[:2] + ":00"
        sp["stop_bucket"] = pd.cut(sp.stop_pct, [0, 0.4, 0.7, 1.0, 100], labels=["<0.4%", "0.4-0.7%", "0.7-1.0%", ">1.0%"]).astype(str)
        sp["expiry_day"] = np.where(rb.nifty_expiry_flag(sp.date), "NIFTY expiry day", "other day")
        sp["dir"] = sp.side
        splits = []
        for col, lab in (("dir", "Day direction"), ("sector", "Sector"), ("hour", "Entry hour"), ("attempt", "Attempt no."), ("stop_bucket", "Stop size"),
                         ("year", "Year"), ("expiry_day", "Expiry vs other")):
            splits += grp(sp, col, lab)
        for lab, kw in (("Color rule", {"color": False}),):
            for name, v in (("ON", base_v), ("OFF", dict(base_v, **kw))):
                s = stat(X.trades(v)[0])
                splits.append([lab, name, s["n"], s["net"], s["win"], s["expr"], s["pf"]])
        for name, tr in (("EMA 5-min", "ema5"), ("EMA 15-min", "ema15")):
            s = stat(X.trades(dict(base_v, trail=tr))[0])
            splits.append(["EMA timeframe", name, s["n"], s["net"], s["win"], s["expr"], s["pf"]])

        # ---------------- claims
        n_weeks = len(days) / 5
        weeks = pd.to_datetime(bt.date).dt.strftime("%G-%V")
        per_week = bt.groupby(weeks).size().reindex(sorted(set(pd.to_datetime([d.day for d in days]).strftime("%G-%V"))), fill_value=0)
        claims = {
            "win": ba["win"], "loss": ba["loss"], "be": ba["be"], "hit2": ba["hit2"], "per_week": len(bt) / n_weeks, "per_week_med": float(per_week.median()),
            "stop_pct": float(bt.stop_pct.mean()), "stop_med": float(bt.stop_pct.median()), "costr": ba["costr"],
            "skip_cost": bcnt["skip_cost"], "skip_lots": bcnt["skip_lots"], "setups": bcnt["skip_cost"] + bcnt["entries"] + bcnt["skip_lots"],
        }
        ex_rows = []
        for lab, kw in (("EMA(10) trail 5-min (+ half at 2R)", {}), ("EMA(10) trail 15-min", {"trail": "ema15"}), ("Step trail", {"trail": "step"}),
                        ("Fixed exit 1R (no partial)", {"trail": "f1"}), ("Fixed exit 2R", {"trail": "f2"}), ("Fixed exit 3R", {"trail": "f3"})):
            ex_rows.append(X.row(lab, dict(base_v, **kw)))

        # ---------------- report artefacts for the baseline
        def to_rb(t):
            if not len(t):
                return t
            r = t.copy()
            r["date"] = pd.to_datetime(r.date)
            r["entry_time"] = r.entry_t
            r["entry_px"] = r.entry_px
            return r
        trd = to_rb(bt)
        all_days = [pd.Timestamp(d.day) for d in days]
        day_pnl = trd.groupby("date").net_pnl.sum().reindex(all_days, fill_value=0.0)
        eq = pd.DataFrame({"date": day_pnl.index, "day_pnl": day_pnl.values})
        eq["equity"] = CAP + eq.day_pnl.cumsum()
        eq["ret"] = eq.day_pnl / CAP
        cut_ts = pd.Timestamp(X.cut)
        stress_t, _ = X.trades(dict(base_v, slip=base_v["slip"] * 2))
        st = to_rb(stress_t)

        def eqof(tt):
            dp = tt.groupby("date").net_pnl.sum().reindex(all_days, fill_value=0.0)
            e = pd.DataFrame({"date": dp.index, "day_pnl": dp.values})
            e["equity"] = CAP + e.day_pnl.cumsum()
            e["ret"] = e.day_pnl / CAP
            return e
        eq_st = eqof(st)
        m_full = rb.metrics(eq, trd)
        m_is = rb.metrics(eq[eq.date < cut_ts], trd[trd.date < cut_ts])
        m_oos = rb.metrics(eq[eq.date >= cut_ts], trd[trd.date >= cut_ts])
        m_st = rb.metrics(eq_st[eq_st.date >= cut_ts], st[st.date >= cut_ts])
        for mm, tt in ((m_full, trd), (m_is, trd[trd.date < cut_ts]), (m_oos, trd[trd.date >= cut_ts]), (m_st, st[st.date >= cut_ts])):
            # fixed size, no compounding: return and drawdown are Rs / starting capital (rb.metrics compounds the equity curve)
            cum = tt.sort_values(["date", "entry_bar"]).net_pnl.cumsum().to_numpy()
            mm["total_return_pct"] = float(tt.net_pnl.sum() / CAP * 100)
            mm["max_dd_pct"] = float((np.maximum.accumulate(np.r_[0.0, cum])[1:] - cum).max() / CAP * 100) if len(cum) else 0.0
        checks = rb.pass_fail(m_is, m_oos, m_st)
        verdict_pass = all(ok_ for _, ok_ in checks)
        trd_out = trd.copy()
        trd_out["date"] = trd_out.date.dt.strftime("%Y-%m-%d")
        trd_out["target_px"] = np.where(trd_out.side == "LONG", trd_out.entry_px + 2 * trd_out.risk_px, trd_out.entry_px - 2 * trd_out.risk_px)
        trd_out["R"] = trd_out["R"]
        cols = ["date", "symbol", "side", "entry_time", "entry_px", "exit_t", "exit_px", "stop_px", "shares", "notional", "gross_pnl", "slippage", "fees", "net_pnl", "ret_pct", "R", "reason", "target_px"]
        tr_csv = trd_out[cols].rename(columns={"exit_t": "exit_time"})
        tr_csv.to_csv(out / "trades.csv", index=False)
        bt.drop(columns=["entry_bar", "exit_bar"]).to_csv(out / "trade_log_baseline.csv", index=False, float_format="%.4f")
        bst.drop(columns=["entry_bar", "exit_bar"]).to_csv(out / "trade_log_is_best.csv", index=False, float_format="%.4f")
        eq.to_csv(out / "equity.csv", index=False)
        yearly = rb.yearly_table(eq, trd)
        yearly.to_csv(out / "yearly.csv", index=False)
        bd = rb.breakdown_table(trd)
        bd.to_csv(out / "breakdown.csv", index=False)

        # ---------------- text
        d0, d1 = days[0].day, days[-1].day
        top_sec = bt.groupby("sector").net_pnl.sum().sort_values()
        c = ctl[:5]            # baseline: full, breadth off, sector off, random sector, random candle
        def part(name, i):
            better_is, better_oos = c[0][5] > c[i][5], c[0][9] > c[i][9]
            return (f"{name}: system IS {c[0][5]:+.2f}R / OOS {c[0][9]:+.2f}R vs control {c[i][5]:+.2f}R / {c[i][9]:+.2f}R -> "
                    + ("system behtar dono me" if better_is and better_oos else "system dono me behtar nahi" if not better_is and not better_oos else "mixed (ek period me behtar)"))
        ctl_line = ("<b>Kaun sa hissa kuch deta hai</b> (average R per trade, system vs control): " + "; ".join([
            part("Breadth filter", 1), part("Sector filter", 2), part("Sector chunav (random sector)", 3), part("Lowest-volume candle (random candle)", 4)])
            + ". Dhyan do: koi bhi hissa system ko profit me nahi laata; behtar-ya-kam-kharab ka matlab sirf 'kam loss' hai.")
        loss_note = (f" Fixed size par poora loss {rs(ba['net'])} = capital ka {ba['net'] / CAP * 100:.0f}% - yaani account Rs {CAP:,.0f} se zyada kha leta (live me size ghatana padta)."
                     if ba["net"] < -CAP else "")
        faisla = [
            f"<b>Baseline</b> (dono taraf, sector measure A, top 2 stocks, color rule ON, stop-entry, EMA(10) 5-min trail): <b>{ba['n']}</b> trade, net <b>{rs(ba['net'])}</b> "
            f"(Build/IS {rs(bi['net'])}, OOS {rs(bo['net'])}), win rate <b>{ba['win']:.1f}%</b>, average <b>{ba['expr']:+.2f}R</b> per trade, PF {ba['pf']:.2f}.",
            f"<b>Daava: win rate 60-62%</b> - {'sahi nahi' if ba['win'] < 55 else 'paas'}: win {ba['win']:.1f}%, loss {ba['loss']:.1f}%, breakeven exit {ba['be']:.1f}%. "
            f"Sirf <b>{ba['hit2']:.1f}%</b> trade 2R tak pahunche.",
            f"<b>Daava: hafte me 2-3 setup</b> - baseline me hafte ke <b>{claims['per_week']:.1f}</b> trade (median {claims['per_week_med']:.0f}) - "
            f"{'bahut zyada' if claims['per_week'] > 6 else 'lagbhag same'}. Koi trade lene se pehle {claims['skip_cost']:,} setup sirf is wajah se chhute ki kharcha 1R ke 25% se zyada tha.",
            f"<b>Stop aur kharcha</b>: average stop price ka {claims['stop_pct']:.2f}% (median {claims['stop_med']:.2f}%). Slippage + charges average <b>{claims['costr'] * 100:.0f}%</b> of 1R. "
            f"Bina kharche ke average {ba['exprg']:+.2f}R, kharche ke baad {ba['expr']:+.2f}R.",
            f"<b>Grid ({len(g)} setting, sirf IS pe fit)</b>: IS me {int((g.i_net > 0).sum())} profit me, OOS me {int((g.o_net > 0).sum())} profit me, dono me "
            f"{int(((g.i_net > 0) & (g.o_net > 0)).sum())}. IS aur OOS ranking ka correlation {rank_corr:.2f} (0 ke paas = IS ki jeet OOS me nahi chalti). "
            f"IS-best setting ({best_kw['measure']}, top {best_kw['top_stocks']}, color {'ON' if best_kw['color'] else 'OFF'}, exec {best_kw['exec']}, trail {best_kw['trail']}, "
            f"breadth {best_kw['breadth'][0]}/{best_kw['breadth'][1]}): IS {rs(si['net'])}, <b>OOS {rs(so['net'])}</b> ({so['n']} trade, {so['expr']:+.2f}R).",
            ctl_line,
            f"<b>Faisla</b>: {'Edge kharche aur OOS ke baad bacha' if (bo['net'] > 0 and bi['net'] > 0 and m_st.get('total_return_pct', -1) > 0) else 'Is form me edge kharche aur out-of-sample ke baad nahi bachta'} "
            f"(baseline OOS {rs(bo['net'])}, 2x slippage pe OOS return {m_st.get('total_return_pct', float('nan')):+.0f}% of capital).{loss_note} Warning: sector map static hai, data spot hai (futures nahi), "
            f"stock list survivorship se bacha hai par lot size 2024 se pehle estimated hai.",
        ]
        rules = [
            f"Data: Fyers 1-min spot (cash) candles of F&O stocks, {d0} se {d1}; 5-min candle me badle (9:15 se 15:25). Futures ka purana 5-min data nahi hai, isliye spot price futures jaisa maana; kharcha futures ka lagaya.",
            f"Stock list har din ki F&O bhavcopy se (point-in-time: baad me hataye gaye stocks bhi shamil). Roz average {np.mean([len(d.syms) for d in days]):.0f} stocks. "
            "Pehle 20 din warm-up (liquidity ke liye), isliye trading 1 Nov 2021 se.",
            "Din ki direction (9:25 wale 5-min candle ke close = 9:30 par): advances = stocks jo kal ke close se upar, declines = neeche (sirf F&O stocks, NSE ka A/D nahi). "
            "Ratio <= 0.7 = bearish din (sirf SHORT). Ratio >= 1.4 = bullish din (sirf LONG). Beech me = trade nahi.",
            "Sector: bearish din sabse kamzor sector, bullish din sabse majboot. Measure A = sector ke stocks ka average % change (kal ke close se, 9:25 close par). Static sector map (undated), 3 se kam stocks wale sector ignore.",
            "Stock: us sector ke F&O stocks, trend ki direction me sabse zyada chalne wale top 2, jinka 20-din ka average traded value Rs 20 crore se upar ho.",
            "Setup: 9:30 wale candle se aage har 5-min close par 'abhi tak ka sabse kam volume wala candle' = reference candle (running minimum, din ka aakhri nahi). Naya kam volume aaye to reference badal jaata hai. 12 candle baad reference expire. Short ke liye reference candle green, long ke liye red (color rule ON).",
            "Entry (variant 1): reference ke low ke neeche 1 tick (short) / high ke upar 1 tick (long) par stop-order; us candle ki range level tak pahunche tabhi fill; gap ho to open par fill. Slippage 0.05% har taraf. Pehle 3 candle (9:15-9:30) me entry nahi, aakhri entry 14:30 tak.",
            "Stop: reference candle ka doosra sira + 1 tick. Agar aane-jaane ka kharcha + slippage 1R ke 25% se zyada ho to trade chhodo (gina jaata hai).",
            "Exit: +2R par aadhi position becho, baaki ka stop entry (breakeven) par. Baaki par EMA(10) trail: 5-min candle close EMA ke paar band ho to agle candle ke open par exit. 15:15 par sab band. Fixed target koi nahi.",
            "Ek hi candle me stop aur target dono aaye to stop pehle maana. Gap se stop paar ho to open ke (bure) price par exit.",
            "Re-entry: stop-loss lagne par agar din ki direction / sector wahi hai to naya reference (stop ke baad ke candles se) bante tab dobara entry; ek stock par din me max 2 koshish.",
            f"Size: risk = capital ka 1% (Rs {CAP * 0.01:,.0f}). Lots = floor(risk / (stop doori x lot size)); 1 se kam to skip. Margin 20% maana, ek position ko capital ka 90%/3 se zyada margin nahi; max 50 lot; max 3 position ek saath. Capital Rs {CAP:,.0f} fixed, profit dobara nahi lagta.",
            "Kharcha: Rs 20/order, STT (sell side futures), NSE exchange fee, SEBI, stamp (buy), GST. Slippage 0.05% har fill par (0.02% aur 0.1% bhi test).",
            f"Honest testing: pehle 60% din ({d0} -> {days[X.n_is - 1].day}) me setting chuni, baaki 40% ({X.cut} -> {d1}) par bina badle test. Grid {len(g)} setting + {len(sweeps())} one-at-a-time variants + controls tried.",
            "Survivorship / data: F&O list point-in-time hai, par lot size 2024 se pehle estimated (pehli known lot size), sector map static, 11-12 F&O naam roz store me nahi (rename/data nahi) - ye chhut jaate hain. 20% se bade 9:25 move wale stocks (split jaisi data galti) hata diye.",
        ]
        formulas = [
            "5-min candle: Open = pehle minute ka open; High/Low = max/min; Close = aakhri minute ka close; Volume = jod",
            "Move % (9:25) = (9:25 candle ka close / kal ka close - 1) x 100",
            "Breadth ratio = advances / declines  (advances = stocks with move > 0, declines = move < 0)",
            "Sector A = sector ke stocks ke move % ka average;  Sector B = trend ki direction me chalne wale stocks / sector ke kul stocks",
            "Reference candle (bar k) = argmin Volume[bar 9:30 ... k]  (running minimum; naya strictly kam volume ho tabhi badle)",
            "Short entry level = Ref Low - 0.05;  Long = Ref High + 0.05;  Fill = min(Open, level) short / max(Open, level) long",
            "Entry fill price = fill x (1 - slip) short / x (1 + slip) long;   slip = 0.05%",
            "Stop = Ref High + 0.05 (short) / Ref Low - 0.05 (long);  1R = |Entry - Stop| per share",
            f"Lots = floor( {CAP * 0.01:,.0f} / (1R x lot size) ), margin cap = floor( {CAP:,.0f} x 0.9 / 3 / (0.2 x lot x price) ), max 50;  Qty = Lots x lot size",
            "Skip agar (round-trip charges per share + 2 x slip x price) > 0.25 x 1R",
            "Partial = +2R:  Target = Entry -/+ 2 x 1R;  half lots (neeche round) becho, baaki ka stop = Entry",
            "EMA(10): EMA[t] = 2/11 x Close[t] + 9/11 x EMA[t-1] (din ke pehle candle se shuru);  Exit jab Close short me EMA ke upar / long me neeche, agle candle ke open par",
            "Step trail: Stop = Entry +/- floor(favourable R - 2) x 1R (2R ke baad har 1R par 1R)",
            "Exit fill = exit price x (1 + slip) short (buy back) / x (1 - slip) long;  15:15 candle ka open par EOD exit",
            "Gross P&L = sum( qty x (exit fill - entry fill) ) long;  (entry fill - exit fill) short",
            "Charges = Rs 20 x orders + exchange + SEBI + STT (sell) + stamp (buy) + 18% GST (brokerage + exchange + SEBI)",
            "Net P&L = Gross - Charges;  R = Net P&L / (1R x qty)",
            "Win rate = trades with Net P&L > 0 / trades;  Loss = Net < 0;  BE exit = stop at entry after partial",
            "Profit factor = sum(profit trades) / sum(|loss trades|)",
            "Expectancy (R) = average Net R per trade",
            "Max drawdown = largest fall of cumulative Net P&L from its peak (Rs and % of capital)",
            "Return % = Net P&L / starting capital x 100 (compounding nahi)",
            "Trades per week = trades / (trading din / 5)",
            "MAE = entry se sabse bura price / 1R",
        ]
        n_st = int(bt.symbol.nunique())
        settings = [["Stocks", "F&O (point-in-time bhavcopy), 5-min spot candle", 1], ["Breadth", "A/D <= 0.7 short, >= 1.4 long (9:25 close)", 1],
                    ["Sector", "Measure A, top 1, static map", 1], ["Stocks / din", "Top 2 (liquidity >= Rs 20 cr)"], ["Color rule", "ON"],
                    ["Entry", "Stop-order at reference extreme +/-1 tick", 1], ["Exit", "Half at 2R + EMA(10) 5-min trail", 1], ["Slippage", "0.05% per side", 1],
                    ["Costs", "Futures: Rs 20/order, STT, exch, SEBI, stamp, GST", 1], ["Capital", f"Rs {CAP:,.0f} fixed, compounding nahi", 1],
                    ["Risk / trade", "1% = Rs 10,000", 1], ["Max positions", "3 ek saath"], ["Re-entry", "max 2 / stock / din"], ["Cutoff", "entry 14:30, square-off 15:15"],
                    ["Check", f"IS {d0} - {days[X.n_is - 1].day} / OOS {X.cut} - {d1}"]]
        inv = {"capital": CAP, "risk_per_trade": "1% of starting capital (Rs 10,000)", "qty_rule": "floor(risk / (stop x lot size)) lots, margin cap",
               "max_per_stock": "margin <= 90% capital / 3", "max_open_positions": 3, "avg_position": round(float(trd.notional.mean()), 0),
               "biggest_position": round(float(trd.notional.max()), 0)}
        peak = 0.0
        for _, gg in trd.groupby("date"):
            ev = sorted([(rb.m_of(t_), n_) for t_, n_ in zip(gg.entry_time, gg.notional)] + [(rb.m_of(t_), -n_) for t_, n_ in zip(gg.exit_t, gg.notional)], key=lambda x: (x[0], x[1]))
            run_ = 0.0
            for _, n_ in ev:
                run_ += n_
                peak = max(peak, run_)
        inv["peak_deployed"] = round(peak, 0)
        months = []
        e2 = eq.assign(m=eq.date.dt.strftime("%Y-%m"))
        for m_, gg in e2.groupby("m"):
            tt = trd[trd.date.dt.strftime("%Y-%m") == m_]
            months.append([m_, round(float(gg.day_pnl.sum()), 2), round(float(gg.day_pnl.sum() / CAP * 100), 3), int(len(tt)), round(float((tt.net_pnl > 0).mean() * 100), 1) if len(tt) else None])

        sc = ["Group", "Value", "Trades", "Net Rs", "Win %", "Exp R", "PF"]
        gtop = g.sort_values("i_net", ascending=False).head(15)
        tests = [
            {"title": "Controls - kaun sa hissa asli edge deta hai?", "hint": "Same system, ek hissa hataya ya random kiya. Random wale 20 alag seed ka average. IS = build period, OOS = baad ka 40%.",
             "cols": ["Test"] + VCOLS[1:] + ["Note"], "rows": clean_rows([[r[1]] + r[2:11] + (r[11:] if len(r) > 11 else [""]) for r in ctl])},
            {"title": "Ek-ek cheez badal ke variants (baseline ke aas-paas)", "hint": "Baki sab baseline jaisa. Parameters ek hi chhote combo pe depend na karen - yahan check karo.",
             "cols": ["Test"] + VCOLS, "rows": clean_rows(vrows)},
            {"title": "Exit ki tulna: EMA(10) trail vs fixed exit", "hint": "Claim (e). Fixed exit me partial nahi, seedha ek hi exit.", "cols": VCOLS, "rows": clean_rows(ex_rows)},
            {"title": "Baseline ke splits", "hint": "Direction, sector, entry time, attempt, stop size, saal, expiry din, color rule, EMA timeframe.", "cols": sc, "rows": clean_rows(splits)},
            {"title": f"Grid ke top 15 (IS net se) - {len(g)} me se", "hint": f"Sirf IS pe chuna, OOS alag. Kul {len(g)} setting; IS profit {int((g.i_net > 0).sum())}, OOS profit {int((g.o_net > 0).sum())}.",
             "cols": ["Breadth", "Measure", "Top stocks", "Color", "Exec", "Trail", "IS trades", "IS net Rs", "IS exp R", "OOS trades", "OOS net Rs", "OOS exp R"],
             "rows": clean_rows([[f"{r.breadth[0]}/{r.breadth[1]}", r.measure, r.top_stocks, "ON" if r.color else "OFF", r.exec, r.trail, r.i_n, r.i_net, r.i_expr, r.o_n, r.o_net, r.o_expr] for r in gtop.itertuples()])},
            {"title": "Daavon ke number", "hint": "Win rate, hafte ke setup, stop, kharcha.", "cols": ["Cheez", "Value"],
             "rows": [["Win rate (net > 0) %", ba["win"]], ["Loss rate %", ba["loss"]], ["Breakeven exit %", ba["be"]], ["Trades jo 2R tak pahunche %", ba["hit2"]],
                      ["Trades per week", claims["per_week"]], ["Average stop % of price", claims["stop_pct"]], ["Average kharcha (slippage + charges) as % of 1R", claims["costr"] * 100],
                      ["Setups skipped (kharcha > 25% of 1R)", claims["skip_cost"]], ["Setups skipped (lots < 1)", claims["skip_lots"]], ["Trades < 200?", "haan - overfitting ka khatra" if ba["n"] < 200 else "nahi"]]},
        ]
        data = {"strategy": "bvs", "strategy_name": "Breadth + Sector + Lowest-Volume candle", "mode": "BASELINE", "data_range": [d0, d1], "oos_start": X.cut,
                "params": {"capital": CAP}, "changed": "", "compounding": False, "settings": settings, "rules": rules, "formulas": formulas, "faisla": faisla, "tests": tests,
                "metrics": {"full": m_full, "in_sample": m_is, "out_of_sample": m_oos, "stress_oos": m_st}, "checks": [[n_, bool(o_)] for n_, o_ in checks],
                "verdict": "PASS" if verdict_pass else "FAIL", "months": months, "investment": inv,
                "data_note": "Fyers 1-min spot candles -> 5-min; F&O stock list from bhavcopy (point-in-time); sector map static; prices adjusted with data/store/adjustments.csv.",
                "generated": datetime.now().strftime("%d %b %Y %H:%M")}
        (out / "report_data.json").write_text(json.dumps(data, default=lambda o: None if (isinstance(o, float) and not np.isfinite(o)) else (o.item() if hasattr(o, "item") else str(o)), indent=1), encoding="utf-8")
        write_html(out, json.loads(json.dumps(data, default=lambda o: o.item() if hasattr(o, "item") else str(o))), template="bvs_template.html")
        log(f"report {(out / 'report.html').stat().st_size / 1e6:.1f} MB")
    except Exception:
        tell(f"{TAG_TECH} BVS backtest FAILED:\n{traceback.format_exc()[-1500:]}")
        raise
    site = json.loads((ROOT / "site" / "site.json").read_text(encoding="utf-8"))["url"]
    num = out.name.split("_", 1)[0]
    msg = (f"{TAG_TRADING} BVS BACKTEST DONE ({(time.time() - t0) / 60:.1f} min) -> {out.name}\n"
           f"Baseline {ba['n']} trade, win {ba['win']:.0f}%, net {rs(ba['net'])} (IS {rs(bi['net'])}, OOS {rs(bo['net'])}), {ba['expr']:+.2f}R/trade, PF {ba['pf']:.2f}.\n"
           f"Grid {len(g)}: IS profit {int((g.i_net > 0).sum())}, OOS profit {int((g.o_net > 0).sum())}. IS-best OOS {rs(so['net'])}.\n"
           f"Website: {site}\nReport: {site}#report-{num}")
    tell(msg)
    if not a.no_telegram:
        send_document(out / "report.html", "BVS report (browser me kholo)")
    print("done ->", out)
    return 0


if __name__ == "__main__":
    sys.exit(main())
