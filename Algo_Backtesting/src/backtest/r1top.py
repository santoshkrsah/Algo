"""Rule No 1: top gainer + Standard Pivot R3 breakout, 5-min, long only, MIS intraday (one or more days).

Rules (user's spec, 19 Nov 2026; unclear points = assumptions sent to Ayush, listed in ASSUMPTIONS):
    watchlist   top N % gainers (vs yesterday's close) at the 5-min candle close, among stocks that pass the filters
    filters     price >= 30, avg volume (20 previous days) >= 5 lakh shares, ATR14 >= 2%, gap-up >= 2%
    signal      first 5-min candle closing at 09:20 / 09:25 / 09:30 above R3 (Standard pivot from yesterday's H/L/C)
    entry       market buy at the next minute's open (no new trade after 09:30)
    exits       SL 2%, target 4% (1:2), sell 50% at +2%, trailing (+1% -> SL -1%, then SL up 1% per 1%), 15:00 time exit
    money       Rs 1 crore, risk 2% -> qty = 2,00,000 / (2% of entry), MIS 5x, max 3 open, 1 trade / stock / day,
                daily loss 2% of capital (realised + open) -> close everything, no new trade
    costs       slippage 0.10% per fill + Angel One intraday equity charges

    days = fast.days(d0, d1); t, wl = run(days)      # trade table, watchlist table
"""
import numpy as np
import pandas as pd

from backtest import fast

OPEN = fast.OPEN_MIN                      # 555 = 09:15

CFG = {
    "capital": 10_000_000.0,              # Rs 1 crore, same every day (no compounding)
    "leverage": 5.0,                      # MIS
    "risk_pct": 2.0,
    "top_n": 20,                          # "top gainer" list size
    "min_price": 30.0,
    "min_avg_vol": 500_000,               # shares, average of the previous 20 days
    "min_atr_pct": 2.0, "min_gap_pct": 2.0,
    "checks": [5, 10, 15],                # 5-min candles closing at 09:20, 09:25, 09:30 (minutes after 09:15)
    "sl_pct": 2.0, "rr": 2.0,             # target = rr x SL = 4%
    "part_pct": 2.0, "part_frac": 0.5,    # sell 50% at +2%
    "trail_trig": 1.0, "trail_step": 1.0,
    "exit_min": 15 * 60 - OPEN,           # 15:00 -> minute index 345 (exit at that minute's open)
    "max_open": 3, "daily_loss_pct": 2.0,
    "slip_pct": 0.10,                     # every fill, buy and sell
}
# Angel One intraday equity (NSE): brokerage Rs 20 or 0.03% per order (lower), STT 0.025% sell, NSE txn 0.00297%,
# SEBI Rs 10 / crore, stamp 0.003% buy, GST 18% on brokerage + txn + SEBI
COST = {"brk_pct": 0.03, "brk_cap": 20.0, "stt_sell": 0.025, "exch": 0.00297, "sebi": 0.0001, "stamp_buy": 0.003, "gst": 18.0}


def hhmm(i) -> str:
    m = OPEN + int(i)
    return f"{m // 60:02d}:{m % 60:02d}"


def r3_standard(h, l, c):
    p = (h + l + c) / 3
    return h + 2 * (p - l)


def charges(buy_val: float, sell_vals: list[float]) -> dict:
    """One buy order + one or two sell orders (partial exit = separate order)."""
    b = lambda v: min(COST["brk_cap"], COST["brk_pct"] / 100 * v)
    sv = sum(sell_vals)
    brk = b(buy_val) + sum(b(v) for v in sell_vals)
    turn = buy_val + sv
    exch, sebi = COST["exch"] / 100 * turn, COST["sebi"] / 100 * turn
    stt, stamp = COST["stt_sell"] / 100 * sv, COST["stamp_buy"] / 100 * buy_val
    gst = COST["gst"] / 100 * (brk + exch + sebi)
    return {"brokerage": brk, "stt": stt, "exchange": exch + sebi, "stamp": stamp, "gst": gst, "total": brk + stt + exch + sebi + stamp + gst}


# ---------------------------------------------------------------- data
def features(days: list[str]) -> pd.DataFrame:
    """Panel rows of the test days with yesterday's H/L/C, gap, ATR14 %, avg volume (previous days only) and R3."""
    lo = (pd.Timestamp(days[0]) - pd.Timedelta(days=60)).strftime("%Y-%m-%d")
    p = fast.add_features(fast.panel(lo, days[-1], columns=["open", "high", "low", "close", "volume", "value", "n_bars"]))
    p["date"] = pd.to_datetime(p["date"]).dt.strftime("%Y-%m-%d")
    p = p[p["date"].isin(set(days))].copy()
    p["r3"] = r3_standard(p["prev_high"], p["prev_low"], p["prev_close"])
    p["pivot"] = (p["prev_high"] + p["prev_low"] + p["prev_close"]) / 3
    return p


def grid(m1: pd.DataFrame, syms) -> tuple:
    """1-min candles of one day -> arrays [stock, minute 0..374] (missing minutes = previous close, no range)."""
    m1 = m1[m1["symbol"].isin(set(syms))]
    S = sorted(m1["symbol"].unique())
    ix = {s: i for i, s in enumerate(S)}
    o, h, l, c = (np.full((len(S), 375), np.nan) for _ in range(4))
    r = m1["symbol"].map(ix).to_numpy()
    k = m1["minute"].to_numpy().astype(int) - OPEN
    ok = (k >= 0) & (k < 375)
    for arr, col in ((o, "open"), (h, "high"), (l, "low"), (c, "close")):
        arr[r[ok], k[ok]] = m1[col].to_numpy()[ok]
    c = pd.DataFrame(c).T.ffill().T.to_numpy()
    miss = np.isnan(o)
    o[miss], h[miss], l[miss] = c[miss], c[miss], c[miss]
    return S, o, h, l, c


# ---------------------------------------------------------------- one day
def watchlist(day: str, P: pd.DataFrame, S, o, h, l, c, cfg=CFG) -> pd.DataFrame:
    """Every check candle: the stocks that pass the filters, their % gain at the candle close, rank, R3 and signal flag."""
    ix = {s: i for i, s in enumerate(S)}
    p = P[(P["date"] == day) & P["symbol"].isin(ix)].copy()
    rows = []
    for k in cfg["checks"]:
        i = p["symbol"].map(ix).to_numpy()
        cl = c[i, k - 1]                                         # close of the 5-min candle ending at 09:15 + k
        hi5, lo5, op5 = h[i, k - 5:k].max(1), l[i, k - 5:k].min(1), o[i, k - 5]
        q = p.assign(t=hhmm(k), k=k, c5=cl, o5=op5, h5=hi5, l5=lo5, chg=(cl / p["prev_close"] - 1) * 100)
        q["f_price"] = q["c5"] >= cfg["min_price"]
        q["f_vol"] = q["avg_vol"] >= cfg["min_avg_vol"]
        q["f_atr"] = q["atr_pct"] >= cfg["min_atr_pct"]
        q["f_gap"] = q["gap_pct"] >= cfg["min_gap_pct"]
        q["ok"] = q[["f_price", "f_vol", "f_atr", "f_gap"]].all(1) & q["c5"].notna() & q["r3"].notna()
        q = q[q["ok"]].sort_values("chg", ascending=False).head(cfg["top_n"])
        q["rank"] = np.arange(1, len(q) + 1)
        q["above_r3"] = q["c5"] > q["r3"]
        rows.append(q)
    w = pd.concat(rows, ignore_index=True) if rows else pd.DataFrame()
    if len(w):
        first = w[w["above_r3"]].groupby("symbol")["k"].min()
        w["signal"] = w["above_r3"] & (w["k"] == w["symbol"].map(first))
    return w


def simulate(day: str, w: pd.DataFrame, S, o, h, l, c, cfg=CFG) -> tuple[list, dict]:
    """Minute-by-minute portfolio for one day. Returns (trades, day info)."""
    ix = {s: i for i, s in enumerate(S)}
    cap, slip = cfg["capital"], cfg["slip_pct"] / 100
    sig = w[w.get("signal", pd.Series(False, index=w.index))].sort_values(["k", "rank"]) if len(w) else w
    pend = {}                                                    # entry minute -> list of signal rows
    for r in sig.itertuples(index=False):
        pend.setdefault(int(r.k), []).append(r)
    openp, done, realised, killed, skipped = [], [], 0.0, None, []

    def close_leg(t, qty, px_raw, m, why):
        fill = px_raw * (1 - slip)
        t["legs"].append((m, qty, px_raw, fill, why))
        t["left"] -= qty

    def finish(t):
        legs = t["legs"]
        sells = [q * f for _, q, _, f, _ in legs]
        ch = charges(t["qty"] * t["entry_f"], sells)
        gross0 = sum(q * (p - t["entry"]) for _, q, p, _, _ in legs)
        net_before = sum(q * (f - t["entry_f"]) for _, q, _, f, _ in legs)
        t.update(charges=ch, gross0=gross0, slip_rs=gross0 - net_before, net=net_before - ch["total"],
                 x=legs[-1][0], exit=sum(q * p for _, q, p, _, _ in legs) / t["qty"], exit_f=sum(sells) / t["qty"],
                 reason=" + ".join(dict.fromkeys(x[4] for x in legs)))
        done.append(t)
        return t["net"]

    for m in range(375):
        # 1) new entries at this minute's open (signals of the candle that closed at 09:15 + m)
        for r in pend.get(m, []):
            if killed is not None:
                skipped.append((r.symbol, r.t, "daily loss limit hit"))
                continue
            if len(openp) >= cfg["max_open"]:
                skipped.append((r.symbol, r.t, f"max {cfg['max_open']} positions open"))
                continue
            i = ix[r.symbol]
            e = o[i, m]
            ef = e * (1 + slip)
            qty = int(cap * cfg["risk_pct"] / 100 // (e * cfg["sl_pct"] / 100))
            used = sum(t["qty"] * t["entry"] for t in openp) / cfg["leverage"]
            if used + qty * e / cfg["leverage"] > cap:
                qty = int((cap - used) * cfg["leverage"] // e)
            if qty <= 0:
                skipped.append((r.symbol, r.t, "margin nahi bacha"))
                continue
            sl0 = e * (1 - cfg["sl_pct"] / 100)
            openp.append({"date": day, "sym": r.symbol, "i": i, "sig": m - 5, "e": m, "entry": e, "entry_f": ef, "qty": qty, "left": qty,
                          "sl0": sl0, "sl": sl0, "tgt": e * (1 + cfg["sl_pct"] * cfg["rr"] / 100), "part": e * (1 + cfg["part_pct"] / 100),
                          "part_done": False, "hi": e, "legs": [], "r3": r.r3, "chg": r.chg, "rank": int(r.rank), "gap": r.gap_pct,
                          "atr": r.atr_pct, "sig_close": r.c5, "t_sig": r.t, "trail_moves": 0})
        if killed is not None and m > killed:
            break
        # 2) exits inside this minute: stop first (conservative), then partial, then target; then trail from the high
        for t in list(openp):
            i = t["i"]
            op, hi, lo = o[i, m], h[i, m], l[i, m]
            if m >= cfg["exit_min"]:
                close_leg(t, t["left"], op, m, "Time exit 15:00")
            elif lo <= t["sl"]:
                px = min(op, t["sl"]) if m > t["e"] else t["sl"]
                close_leg(t, t["left"], px, m, "SL" if t["sl"] < t["entry"] else ("Trailing SL (entry pe)" if abs(t["sl"] - t["entry"]) < 1e-9 else "Trailing SL (profit)"))
            else:
                if not t["part_done"] and hi >= t["part"]:
                    pq = int(t["qty"] * cfg["part_frac"])
                    if pq > 0:
                        close_leg(t, pq, max(op, t["part"]) if m > t["e"] else t["part"], m, "50% @ +2%")
                    t["part_done"] = True
                if hi >= t["tgt"]:
                    close_leg(t, t["left"], max(op, t["tgt"]) if m > t["e"] else t["tgt"], m, "Target 4%")
            if t["left"] <= 0:
                openp.remove(t)
                realised += finish(t)
                continue
            t["hi"] = max(t["hi"], hi)
            up = (t["hi"] / t["entry"] - 1) * 100
            if up >= cfg["trail_trig"]:
                n = int(np.floor((up - cfg["trail_trig"]) / cfg["trail_step"] + 1e-9)) + 1
                new = t["entry"] * (1 + (-cfg["sl_pct"] + n * cfg["trail_step"]) / 100)
                if new > t["sl"]:
                    t["sl"], t["trail_moves"] = new, n
        # 3) daily loss limit on realised + open P&L at this minute's close
        if killed is None:
            mtm = sum(t["left"] * (c[t["i"], m] * (1 - slip) - t["entry_f"]) + sum(q * (f - t["entry_f"]) for _, q, _, f, _ in t["legs"]) for t in openp)
            if realised + mtm <= -cap * cfg["daily_loss_pct"] / 100:
                killed = m
                for t in list(openp):
                    nm = min(m + 1, 374)
                    close_leg(t, t["left"], o[t["i"], nm], nm, "Daily loss limit")
                    openp.remove(t)
                    realised += finish(t)
    for t in openp:                                              # data ended early (should not happen)
        close_leg(t, t["left"], c[t["i"], 374], 374, "Din khatam")
        realised += finish(t)
    return done, {"date": day, "net": realised, "killed": None if killed is None else hhmm(killed + 1), "skipped": skipped, "signals": len(sig)}


def run(days: list[str], cfg=CFG, log=print) -> tuple[pd.DataFrame, pd.DataFrame, list, dict]:
    """Every day: filters -> watchlist -> signals -> portfolio. Returns trades, watchlist, day infos, 1-min grids of traded stocks."""
    P = features(days)
    T, W, info, G = [], [], [], {}
    for d in days:
        cand = P[(P["date"] == d) & (P["prev_close"] > 0)]["symbol"].tolist()
        m1 = fast.day_1m(d, symbols=cand)
        if m1.empty:
            log(f"{d}: 1-min data nahi")
            continue
        S, o, h, l, c = grid(m1, cand)
        w = watchlist(d, P, S, o, h, l, c, cfg)
        tr, di = simulate(d, w, S, o, h, l, c, cfg)
        di["passed_filters"] = int(w.drop_duplicates("symbol").shape[0]) if len(w) else 0
        T += tr
        W.append(w.assign(date=d) if len(w) else w)
        info.append(di)
        for t in tr:
            i = S.index(t["sym"])
            G[(d, t["sym"])] = (o[i], h[i], l[i], c[i])
        log(f"{d}: watchlist {di['passed_filters']} stocks, {di['signals']} signal, {len(tr)} trade, net Rs {di['net']:,.0f}")
    W = pd.concat([x for x in W if len(x)], ignore_index=True) if any(len(x) for x in W) else pd.DataFrame()
    return pd.DataFrame(T), W, info, G
