"""IPO base / VCP swing + ROC market cycle (Financially Free podcast with Harsh Singh, 30 Oct 2025).
Rules (video = V, assumption = A): strategies/IPO_VCP_SWING_RULES.md. Daily candles, NSE cash delivery, long only.

    U = load()                          # stocks (daily, split / bonus adjusted), IPO list, Smallcap 100 / Nifty 50 / GOLDBEES
    r = run(U, p)                       # portfolio test -> {"trades", "equity", "skipped", "n_signals"}
    s = stats(r, p[, lo, hi])           # numbers for the whole test or one period
    a = alloc(U, q)                     # ROC allocation test: index while ON, GOLDBEES while OFF

Strategies (Params.strat):
    "ipo"  NSE IPOs (mainboard; SME optional) up to 500 trading days after listing. Entry 1 = VCP breakout (1 unit),
           entry 2 = close above the listing-day high after 20 closes below it (top-up or new, 1 unit), max 2 units a stock.
    "ath"  VCP breakout on stocks within 5% of their all-time high (data from 2010) that beat the Smallcap 100 over 63 days,
           one entry of 2 units.
Both: stop = average price - 10%, exit after two red candles in a row that close below the 21 EMA (next open), re-entry
after two green closes above it (within 60 days, max 3), max 5 stocks, no new trade while the monthly ROC state is OFF,
5 stop-losses in a row -> new units at 35% size until a trade closes in profit.

Timing (no look-ahead): every signal uses data up to the close of day t, the order fills at the stock's next bar open.
The stop is checked on every bar's low (a gap below the stop fills at that open). The monthly ROC state of month m's
close is used from the first trading day of month m+1.
"""
from dataclasses import dataclass, replace

import numpy as np
import pandas as pd

from backtest.config import DATA

DAILY = DATA / "daily"
IDX_FILES = {"NIFTY50": DAILY / "index" / "NIFTY50.csv", "SMALLCAP100": DAILY / "index" / "NIFTYSMLCAP100.csv",
             "GOLDBEES": DAILY / "etf" / "GOLDBEES.csv"}
REGIMES = {"small": ("SMALLCAP100", 20, 100.0), "nifty": ("NIFTY50", 18, 45.0)}   # index, ROC length (months), sell level
PERIODS = {"ipo": [("DEV (build)", "2016-01-01", "2021-12-31"), ("Validation", "2022-01-01", "2023-12-31"), ("Latest", "2024-01-01", "2099-12-31")],
           "ath": [("DEV (build)", "2011-01-01", "2021-12-31"), ("Validation", "2022-01-01", "2023-12-31"), ("Latest", "2024-01-01", "2099-12-31")]}


@dataclass(frozen=True)
class Params:
    strat: str = "ipo"                  # "ipo" or "ath"
    start: str = "2016-01-01"
    end: str = "2026-12-31"
    # money
    capital: float = 1_000_000.0
    compound: bool = False              # False: unit = 10% of the starting capital; True: 10% of yesterday's equity
    unit_pct: float = 0.10
    max_units: int = 2                  # per stock
    entry_units: int = 1                # units of a new entry (ath: 2)
    max_stocks: int = 5
    # universe
    sme: bool = False                   # ipo: SME IPOs too
    ipo_days: int = 500                 # new setups only this many bars after listing
    first_gap_days: int = 7             # first Fyers candle at most 7 calendar days after the NSE listing date
    min_turnover: float = 1e7           # 20-day average traded value, Rs
    min_hist: int = 250                 # ath: bars of history before a signal counts
    # setups
    base_below: int = 20                # listing breakout: the previous 20 closes were below the listing-day high
    topup: str = "breakout"             # "breakout" (close > listing high) or "retest" (back to it within 20 days, green close above)
    depth_c_max: float = 0.10
    depth_a_min: float = 0.10
    pivot_near: float = 0.90
    ath_near: float = 0.95
    rs: bool = True
    # exits
    stop_pct: float = 0.10
    ema: int = 21
    reentry: bool = True
    reentry_days: int = 60
    max_reentries: int = 3
    streak_n: int = 5
    streak_size: float = 0.35           # 1.0 = rule off
    # market cycle filter
    regime: str = "small"               # "small", "nifty" or "off"
    regime_mode: str = "level"          # "level": OFF from ROC >= sell level until ROC <= 0; "cross": until ROC crosses 0 upwards
    # costs
    slip: float = 0.001
    slip_sme: float = 0.005
    costs: bool = True


def with_params(p: Params, **kw) -> Params:
    return replace(p, **kw)


# ------------------------------------------------------------------------------------------------------------- data
def load() -> dict:
    eq = pd.read_parquet(DAILY / "equity_daily.parquet")
    f = DAILY / "ipo_daily.parquet"
    ip = pd.read_parquet(f) if f.exists() else eq.iloc[:0]
    in_eq = set(eq.symbol.unique())
    px = pd.concat([eq, ip[~ip.symbol.isin(in_eq)]], ignore_index=True)
    px["date"] = px.date.astype(str)
    px = px[(px.open > 0) & (px.close > 0)]
    stocks = {s: g.drop(columns="symbol").sort_values("date").reset_index(drop=True) for s, g in px.groupby("symbol")}
    lst = pd.read_csv(DAILY / "ipo" / "_list.csv", dtype=str)
    idx = {k: pd.read_csv(v, dtype={"date": str}).sort_values("date").reset_index(drop=True) for k, v in IDX_FILES.items()}
    sme = set(lst[lst.type == "SME"].now) - in_eq                       # SME that are not (yet) in the AMFI list
    return {"stocks": stocks, "ipo": lst, "idx": idx, "in_equity": in_eq, "sme": sme}


# ---------------------------------------------------------------------------------------------------- market cycle
def roc_monthly(ix: pd.DataFrame, n: int) -> pd.DataFrame:
    m = ix.assign(month=ix.date.str[:7]).groupby("month").close.last().to_frame()
    m["roc"] = (m.close / m.close.shift(n) - 1) * 100
    return m


def roc_state(m: pd.DataFrame, sell: float, mode: str) -> pd.Series:
    """ON / OFF per month (True = ON), from that month's close; starts ON."""
    on, prev, out = True, None, []
    for r in m.roc:
        if r == r:
            if on and r >= sell:
                on = False
            elif not on and (r <= 0 if mode == "level" else (prev is not None and prev < 0 <= r)):
                on = True
        out.append(on)
        prev = r if r == r else prev
    return pd.Series(out, index=m.index)


def regime_days(U: dict, p: Params, days: list[str]) -> np.ndarray:
    if p.regime == "off":
        return np.ones(len(days), dtype=bool)
    name, n, sell = REGIMES[p.regime]
    st = roc_state(roc_monthly(U["idx"][name], n), sell, p.regime_mode)
    months = sorted(st.index)
    prev_month = {m: months[i - 1] for i, m in enumerate(months) if i}
    return np.array([bool(st.get(prev_month.get(d[:7], ""), True)) for d in days])


# ---------------------------------------------------------------------------------------------------------- features
def features(b: pd.DataFrame, p: Params, lh: float | None = None) -> dict:
    o, h, l, c, v = b.open, b.high, b.low, b.close, b.volume
    ema = c.ewm(span=p.ema, adjust=False).mean()
    hC, lC = h.rolling(10).max().shift(1), l.rolling(10).min().shift(1)
    hB, lB = h.rolling(20).max().shift(11), l.rolling(20).min().shift(11)
    hA, lA = h.rolling(30).max().shift(31), l.rolling(30).min().shift(31)
    dA, dB, dC = 1 - lA / hA, 1 - lB / hB, 1 - lC / hC
    h60 = h.rolling(60).max().shift(1)
    vcp = (dA > dB) & (dB > dC) & (dC <= p.depth_c_max) & (dA >= p.depth_a_min) & (lC >= lB) & (lB >= lA) & (hC >= p.pivot_near * h60)
    f = {"o": o.to_numpy(), "h": h.to_numpy(), "l": l.to_numpy(), "c": c.to_numpy(), "ema": ema.to_numpy(),
         "turn": (c * v).rolling(20).mean().fillna(0).to_numpy(), "ret63": (c / c.shift(63) - 1).fillna(-9).to_numpy(),
         "vcp": (vcp & (c > hC) & (c > ema)).to_numpy(), "pivot": hC.to_numpy(), "dA": dA.to_numpy(), "dB": dB.to_numpy(), "dC": dC.to_numpy()}
    red = ((c < o) & (c < ema)).to_numpy()
    green = ((c > o) & (c > ema)).to_numpy()
    f["exit"] = red & np.r_[False, red[:-1]]
    f["reent"] = green & np.r_[False, green[:-1]]
    if lh is not None:
        below = (c.shift(1).rolling(p.base_below).max() < lh).to_numpy()
        lb = (c.to_numpy() > lh) & below
        recent = pd.Series(lb).shift(1).rolling(20, min_periods=1).max().fillna(0).to_numpy() > 0
        rt = recent & (l.to_numpy() <= lh * 1.03) & (c.to_numpy() > lh) & (c.to_numpy() > o.to_numpy())
        f["lb"], f["rt"] = lb, rt
    else:
        f["ath"] = (c >= p.ath_near * h.cummax().shift(1)).to_numpy()
    return f


def universe(U: dict, p: Params) -> dict:
    """{symbol: {"b": bars, "f": features, "dates": bar dates, "sme": bool, "lh", "listing", "first"}}"""
    out = {}
    if p.strat == "ipo":
        lst = U["ipo"]
        lst = lst[lst.type.isin(["Equity", "SME"] if p.sme else ["Equity"]) & (lst.listing_date >= p.start)]
        for r in lst.itertuples(index=False):
            b = U["stocks"].get(r.now)
            if b is None or b.empty:
                continue
            gap = (pd.Timestamp(b.date.iloc[0]) - pd.Timestamp(r.listing_date)).days
            if gap < -3 or gap > p.first_gap_days:
                continue
            lh = float(b.high.iloc[0])
            out[r.now] = {"b": b, "f": features(b, p, lh), "sme": r.type == "SME" and r.now not in U["in_equity"], "lh": lh,
                          "listing": r.listing_date, "first": b.date.iloc[0], "issue": r.issue_price, "type": r.type}
    else:
        for s, b in U["stocks"].items():
            if s in U["sme"] or len(b) < p.min_hist + 1:
                continue
            out[s] = {"b": b, "f": features(b, p), "sme": False, "lh": None, "listing": None, "first": b.date.iloc[0], "issue": None, "type": "Equity"}
    for s, x in out.items():
        x["dates"] = x["b"].date.to_numpy(dtype=object)
    return out


def row(x: dict, d: str):
    """Bar index of day d in this stock (None if it did not trade that day)."""
    i = int(np.searchsorted(x["dates"], d))
    return i if i < len(x["dates"]) and x["dates"][i] == d else None


def signals(V: dict, U: dict, p: Params, days: list[str]) -> dict:
    """{date: [(symbol, row, kind, rank)]} of every entry signal (before the portfolio rules)."""
    sm = U["idx"]["SMALLCAP100"].set_index("date").close
    sm63 = (sm / sm.shift(63) - 1)
    dayset = set(days)
    out = {}
    for s, x in V.items():
        f, dates = x["f"], x["dates"]
        n = len(dates)
        i_all = np.arange(n)
        ok = (f["turn"] >= p.min_turnover)
        if p.strat == "ipo":
            young = i_all <= p.ipo_days
            e1 = f["vcp"] & ok & young & (i_all >= 60)
            e2 = (f["lb"] if p.topup == "breakout" else f["rt"]) & ok & young & (i_all >= p.base_below)
            for i in np.flatnonzero(e1 | e2):
                if dates[i] in dayset:
                    kind = "VCP" if e1[i] else ("Listing breakout" if p.topup == "breakout" else "Listing retest")
                    out.setdefault(dates[i], []).append((s, int(i), kind, bool(e2[i]), float(f["ret63"][i])))
        else:
            rs = f["ret63"] > sm63.reindex(dates).fillna(9).to_numpy() if p.rs else np.ones(n, dtype=bool)
            e = f["vcp"] & f["ath"] & ok & rs & (i_all >= p.min_hist)
            for i in np.flatnonzero(e):
                if dates[i] in dayset:
                    out.setdefault(dates[i], []).append((s, int(i), "ATH VCP", False, float(f["ret63"][i])))
    return out


# ------------------------------------------------------------------------------------------------------------ money
def order_cost(val: float, side: str, p: Params) -> dict:
    """Angel One delivery charges of one order (Rs)."""
    if not p.costs:
        return {k: 0.0 for k in ("brok", "stt", "exch", "sebi", "stamp", "gst", "dp")}
    brok = min(20.0, 0.001 * val)
    exch, sebi = 0.0000297 * val, 0.000001 * val
    return {"brok": brok, "stt": 0.001 * val, "exch": exch, "sebi": sebi, "stamp": 0.00015 * val if side == "buy" else 0.0,
            "gst": 0.18 * (brok + exch + sebi), "dp": 23.6 if side == "sell" else 0.0}


def run(U: dict, p: Params, V: dict | None = None) -> dict:
    V = V if V is not None else universe(U, p)
    cal = U["idx"]["NIFTY50"].date
    days = cal[(cal >= p.start) & (cal <= p.end)].tolist()
    on = regime_days(U, p, days)
    sig = signals(V, U, p, days)
    cash, eq_prev = p.capital, p.capital
    pos, pend, watch, trades, skipped, eqrows = {}, [], {}, [], [], []
    streak, small_mode, tid = 0, False, 0
    n_signals = sum(len(v) for v in sig.values())

    def slip_of(s):
        return p.slip_sme if V[s]["sme"] else p.slip

    def unit_value():
        base = eq_prev if p.compound else p.capital
        return base * p.unit_pct * (p.streak_size if small_mode else 1.0)

    def buy(s, i, units, why, d, sig_day, extra):
        nonlocal cash
        x = V[s]
        raw = float(x["f"]["o"][i])
        fill = raw * (1 + slip_of(s))
        qty = int(unit_value() * units // fill)
        if qty * fill + 60 > cash:
            qty = int(max(0.0, cash - 60) // (fill * 1.0013))
        if qty < 1:
            skipped.append({"date": d, "symbol": s, "kind": why, "why": "cash kam"})
            return False
        val = qty * fill
        c = order_cost(val, "buy", p)
        cash -= val + sum(c.values())
        e = {"date": d, "raw": raw, "fill": fill, "qty": qty, "why": why, "sig": sig_day, "slip": qty * raw * slip_of(s), "cost": c}
        if s in pos:
            q = pos[s]
            q["entries"].append(e)
            q["qty"] += qty
            q["buy_val"] += val
            q["units"] += units
        else:
            q = pos[s] = {"sym": s, "kind": why, "entries": [e], "qty": qty, "buy_val": val, "units": units, "fi": i, "first_day": d,
                          "lo": float(x["f"]["l"][i]), "hi": float(x["f"]["h"][i]), **extra}
        q["avg"] = q["buy_val"] / q["qty"]
        q["stop"] = q["avg"] * (1 - p.stop_pct)
        return True

    def sell(s, i, raw, why, d):
        nonlocal cash, streak, small_mode, tid
        q = pos.pop(s)
        fill = raw * (1 - slip_of(s))
        val = q["qty"] * fill
        c = order_cost(val, "sell", p)
        cash += val - sum(c.values())
        costs = {k: sum(e["cost"][k] for e in q["entries"]) + c[k] for k in c}
        slip = sum(e["slip"] for e in q["entries"]) + q["qty"] * raw * slip_of(s)
        gross = val - q["buy_val"]
        net = gross - sum(costs.values())
        tid += 1
        x = V[s]
        trades.append({"id": tid, "symbol": s, "kind": q["kind"], "base": q.get("base", q["kind"]), "sme": x["sme"], "reent_no": q.get("reent_no", 0),
                       "sig_date": q["entries"][0]["sig"], "entry_date": q["first_day"], "exit_date": d,
                       "entry_raw": q["entries"][0]["raw"], "entry": q["entries"][0]["fill"], "avg": q["avg"],
                       "topup_date": q["entries"][1]["date"] if len(q["entries"]) > 1 else None,
                       "topup_raw": q["entries"][1]["raw"] if len(q["entries"]) > 1 else None,
                       "exit_raw": raw, "exit": fill, "reason": why, "units": q["units"], "qty": q["qty"], "buy_val": q["buy_val"],
                       "gross": gross, "slip": slip, **{"c_" + k: v for k, v in costs.items()}, "costs": sum(costs.values()), "net": net,
                       "ret_pct": net / q["buy_val"] * 100, "days": int(i - q["fi"]), "stop": q["stop"],
                       "mae_pct": (q["lo"] / q["avg"] - 1) * 100, "mfe_pct": (q["hi"] / q["avg"] - 1) * 100,
                       "lh": x["lh"], "pivot": q.get("pivot"), "listing": x["listing"], "fi": q["fi"], "xi": int(i),
                       "small_size": q.get("small_size", False)})
        if why.startswith("Stop loss"):
            streak += 1
            if streak >= p.streak_n and p.streak_size < 1:
                small_mode = True
        if net > 0:
            streak, small_mode = 0, False
        if why.startswith("21 EMA") or why.startswith("63 EMA"):
            if p.reentry and q.get("reent_no", 0) < p.max_reentries:
                watch[s] = {"until": i + p.reentry_days, "units": q["units"], "n": q.get("reent_no", 0), "kind": q.get("base", q["kind"])}

    ema_label = f"{p.ema} EMA: 2 red close neeche"
    sl_label = f"Stop loss {p.stop_pct * 100:.0f}%"
    for di, d in enumerate(days):
        # 1) orders from yesterday's close fill at today's open (exits first)
        keep = []
        for o in pend:
            x = V[o["sym"]]
            i = row(x, d)
            if i is None:
                o["wait"] = o.get("wait", 0) + 1
                if o["wait"] <= 10:
                    keep.append(o)
                elif o["type"] != "exit":
                    skipped.append({"date": d, "symbol": o["sym"], "kind": o["why"], "why": "stock trade nahi hua (10 din)"})
                continue
            if o["type"] == "exit":
                if o["sym"] in pos:
                    sell(o["sym"], i, float(x["f"]["o"][i]), o["why"], d)
            else:
                buy(o["sym"], i, o["units"], o["why"], d, o["sig"], o["extra"])
        pend = [o for o in keep]
        # 2) stop loss on today's bar, then path tracking
        for s in list(pos):
            x = V[s]
            i = row(x, d)
            if i is None:
                continue
            q = pos[s]
            lo, op = float(x["f"]["l"][i]), float(x["f"]["o"][i])
            if lo <= q["stop"]:
                q["lo"] = min(q["lo"], lo)
                sell(s, i, min(op, q["stop"]), sl_label, d)
                continue
            q["lo"], q["hi"] = min(q["lo"], lo), max(q["hi"], float(x["f"]["h"][i]))
        # 3) mark to market
        mv = 0.0
        for s, q in pos.items():
            x = V[s]
            i = row(x, d)
            if i is None:
                i = int(np.searchsorted(x["dates"], d, side="right")) - 1
            mv += q["qty"] * float(x["f"]["c"][i])
        eq_prev = cash + mv
        eqrows.append({"date": d, "equity": eq_prev, "cash": cash, "deployed": sum(q["buy_val"] for q in pos.values()),
                       "positions": len(pos), "on": bool(on[di])})
        if di == len(days) - 1:
            break
        # 4) exit signals at today's close
        pend_exit = {o["sym"] for o in pend if o["type"] == "exit"}
        for s, q in pos.items():
            x = V[s]
            i = row(x, d)
            if i is not None and x["f"]["exit"][i] and s not in pend_exit:
                pend.append({"type": "exit", "sym": s, "why": ema_label})
        exiting = {o["sym"] for o in pend if o["type"] == "exit"}
        # 5) new entries / top-ups / re-entries for tomorrow's open
        cands = []
        for s, i, kind, is_lb, rank in sig.get(d, []):
            cands.append((0 if (s in pos and is_lb) else 2, -rank, s, i, kind, is_lb))
        for s, w in list(watch.items()):
            x = V[s]
            i = row(x, d)
            if i is None:
                continue
            if i > w["until"]:
                watch.pop(s)
                continue
            if x["f"]["reent"][i] and s not in pos:
                cands.append((1, -float(x["f"]["ret63"][i]), s, i, "Re-entry", False))
        cands.sort(key=lambda t: (t[0], t[1]))
        pend_new = {o["sym"] for o in pend if o["type"] == "buy"}
        n_stocks = len(set(pos) - exiting) + len(pend_new - set(pos))
        for pri, _, s, i, kind, is_lb in cands:
            if s in pend_new or s in exiting:
                continue
            if not on[di]:
                skipped.append({"date": d, "symbol": s, "kind": kind, "why": "market OFF (ROC)"})
                continue
            x = V[s]
            if s in pos:
                if p.strat != "ipo" or not is_lb:
                    continue                               # already held: only a listing breakout tops up
                if pos[s]["units"] >= p.max_units:
                    skipped.append({"date": d, "symbol": s, "kind": kind, "why": "already 2 unit"})
                    continue
                pend.append({"type": "buy", "sym": s, "units": 1, "why": "Top-up: " + kind, "sig": d, "extra": {}})
                pend_new.add(s)
                continue
            if n_stocks >= p.max_stocks:
                skipped.append({"date": d, "symbol": s, "kind": kind, "why": "5 stock full"})
                continue
            if kind == "Re-entry":
                w = watch.pop(s)
                extra = {"reent_no": w["n"] + 1, "pivot": None, "small_size": small_mode, "base": w["kind"]}
                units = w["units"]
                why = f"Re-entry {w['n'] + 1} ({w['kind']})"
            else:
                watch.pop(s, None)
                extra = {"reent_no": 0, "pivot": float(x["f"]["pivot"][i]) if kind in ("VCP", "ATH VCP") else None, "small_size": small_mode}
                units = p.entry_units
                why = kind
            pend.append({"type": "buy", "sym": s, "units": units, "why": why, "sig": d, "extra": extra})
            pend_new.add(s)
            n_stocks += 1
    # close what is still open at the last close
    d = days[-1]
    for s in list(pos):
        x = V[s]
        i = int(np.searchsorted(x["dates"], d, side="right")) - 1
        sell(s, i, float(x["f"]["c"][i]), "Test khatam (khuli thi)", d)
    t = pd.DataFrame(trades)
    e = pd.DataFrame(eqrows)
    return {"trades": t, "equity": e, "skipped": pd.DataFrame(skipped, columns=["date", "symbol", "kind", "why"]), "n_signals": n_signals,
            "universe": len(V), "V": V}


# ------------------------------------------------------------------------------------------------------------ stats
def stats(r: dict, p: Params, lo: str | None = None, hi: str | None = None) -> dict:
    t, e = r["trades"], r["equity"]
    if lo or hi:
        t = t[(t.exit_date >= (lo or "")) & (t.exit_date <= (hi or "9999"))] if len(t) else t
        e = e[(e.date >= (lo or "")) & (e.date <= (hi or "9999"))]
    n = t.net if len(t) else pd.Series(dtype=float)
    w, l = n[n > 0], n[n <= 0]
    eqv = e.equity.to_numpy()
    start_eq = float(eqv[0]) if len(eqv) else p.capital
    if lo:                                       # period: drawdown / CAGR from the equity at the period start
        prev = r["equity"][r["equity"].date < lo]
        start_eq = float(prev.equity.iloc[-1]) if len(prev) else p.capital
    peak = np.maximum.accumulate(np.maximum(eqv, start_eq)) if len(eqv) else eqv
    dd = eqv - peak
    years = max((pd.Timestamp(e.date.iloc[-1]) - pd.Timestamp(e.date.iloc[0])).days / 365.25, 1e-9) if len(e) else 1
    end_eq = float(eqv[-1]) if len(eqv) else start_eq
    net = float(n.sum())
    return {"trades": int(len(t)), "net": net, "ret_pct": net / p.capital * 100, "eq_ret_pct": (end_eq / start_eq - 1) * 100,
            "cagr": ((end_eq / start_eq) ** (1 / years) - 1) * 100 if len(e) and end_eq > 0 else None,
            "win_pct": float((n > 0).mean() * 100) if len(t) else None, "pf": float(w.sum() / -l.sum()) if l.sum() < 0 else None,
            "avg": float(n.mean()) if len(t) else None, "avg_win": float(w.mean()) if len(w) else None, "avg_loss": float(l.mean()) if len(l) else None,
            "avg_ret_pct": float(t.ret_pct.mean()) if len(t) else None, "best": float(n.max()) if len(t) else 0.0, "worst": float(n.min()) if len(t) else 0.0,
            "max_dd": float(dd.min()) if len(dd) else 0.0, "max_dd_pct": float((dd / peak).min() * 100) if len(dd) else 0.0,
            "gross": float(t.gross.sum()) if len(t) else 0.0, "slip": float(t.slip.sum()) if len(t) else 0.0, "costs": float(t.costs.sum()) if len(t) else 0.0,
            "avg_days": float(t.days.mean()) if len(t) else None, "med_days": float(t.days.median()) if len(t) else None,
            "max_days": int(t.days.max()) if len(t) else 0,
            "avg_mae": float(t.mae_pct.mean()) if len(t) else None, "worst_mae": float(t.mae_pct.min()) if len(t) else None,
            "avg_deployed": float(e.deployed.mean()) if len(e) else 0.0, "peak_deployed": float(e.deployed.max()) if len(e) else 0.0,
            "max_positions": int(e.positions.max()) if len(e) else 0, "avg_positions": float(e.positions.mean()) if len(e) else 0.0,
            "in_mkt": float((e.positions > 0).mean() * 100) if len(e) else 0.0, "off_pct": float((~e.on).mean() * 100) if len(e) else 0.0,
            "end_equity": end_eq}


def index_bh(U: dict, name: str, days: list[str], capital: float) -> pd.DataFrame:
    """capital in an index (or GOLDBEES) at the first day's open, held to the end (no dividends)."""
    ix = U["idx"][name].set_index("date")
    o0 = float(ix.open.get(days[0], np.nan))
    first = o0 if o0 > 0 else float(ix.close.get(days[0]))
    return pd.DataFrame({"date": days, "equity": (ix.close.reindex(days).ffill() / first * capital).to_numpy()})


# ------------------------------------------------------------------------------------------------- allocation test
@dataclass(frozen=True)
class Alloc:
    index: str = "NIFTY50"              # NIFTY50 (ROC 18, sell 45) or SMALLCAP100 (ROC 20, sell 100)
    n: int = 18
    sell: float = 45.0
    mode: str = "level"
    off_asset: str = "GOLDBEES"         # GOLDBEES or "CASH" (fixed 6% a year)
    cost: float = 0.002                 # per switch (sell + buy)
    start: str = "2007-04-01"
    end: str = "2026-12-31"
    capital: float = 1_000_000.0


def alloc(U: dict, q: Alloc) -> dict:
    m = roc_monthly(U["idx"][q.index], q.n)
    st = roc_state(m, q.sell, q.mode)
    cal = U["idx"]["NIFTY50"].date
    days = cal[(cal >= q.start) & (cal <= q.end)].tolist()
    months = sorted(st.index)
    prev_month = {mm: months[i - 1] for i, mm in enumerate(months) if i}
    px = {k: U["idx"][k].set_index("date") for k in (q.index, "GOLDBEES")}
    o = {k: v.open.reindex(days).ffill() for k, v in px.items()}
    c = {k: v.close.reindex(days).ffill() for k, v in px.items()}
    held, units, eq, rows, switches, cash_val = None, 0.0, q.capital, [], [], None
    for i, d in enumerate(days):
        want_on = bool(st.get(prev_month.get(d[:7], ""), True))
        want = q.index if want_on else q.off_asset
        if want != held:
            val = eq if held is None else (units * float(o[held].iloc[i]) if held != "CASH" else cash_val)
            if held is not None:
                val *= 1 - q.cost
                switches.append({"date": d, "from": held, "to": want, "roc": float(m.roc.get(prev_month.get(d[:7], ""), np.nan))})
            held = want
            if held == "CASH":
                cash_val, units = val, 0.0
            else:
                units = val / float(o[held].iloc[i])
        if held == "CASH":
            if i:
                cash_val *= 1.06 ** (1 / 252)
            eq = cash_val
        else:
            eq = units * float(c[held].iloc[i])
        rows.append({"date": d, "equity": eq, "held": held})
    e = pd.DataFrame(rows)
    v = e.equity.to_numpy()
    peak = np.maximum.accumulate(v)
    years = (pd.Timestamp(days[-1]) - pd.Timestamp(days[0])).days / 365.25
    bh = index_bh(U, q.index, days, q.capital)
    bv = bh.equity.to_numpy()
    bpeak = np.maximum.accumulate(bv)
    s = {"ret_pct": (v[-1] / q.capital - 1) * 100, "cagr": ((v[-1] / q.capital) ** (1 / years) - 1) * 100, "max_dd_pct": float((v / peak - 1).min() * 100),
         "switches": len(switches), "on_pct": float((e.held == q.index).mean() * 100), "end": float(v[-1]),
         "bh_ret_pct": (bv[-1] / q.capital - 1) * 100, "bh_cagr": ((bv[-1] / q.capital) ** (1 / years) - 1) * 100, "bh_max_dd_pct": float((bv / bpeak - 1).min() * 100)}
    return {"equity": e, "bh": bh, "switches": pd.DataFrame(switches), "stats": s, "roc": m.assign(on=st)}
