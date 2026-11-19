"""Rule No 1.1: Standard Pivot R3, 5-min, long (R3 breakout) + short (R3 reversal), MIS intraday.

Rules (user's spec + answers, 19 Nov 2026):
    universe    market cap >= Rs 5000 cr (AMFI half-yearly list that was public on that day), price >= 30,
                avg volume (20 previous days) >= 5 lakh shares, ATR14 >= 2%, gap-up >= 2% (both sides)
    buy signal  first GREEN 5-min candle (closing 09:20 / 09:25 / 09:30) with close > R3 -> buy-stop at that candle's high
    sell signal first RED 5-min candle (closing 09:20 / 09:25 / 09:30) with close > R3  -> sell-stop at that candle's low
    trigger     order stays live till 15:00 (SL-M = fill at trigger, or at the minute open if price jumps past it)
    pick        max 3 buy + 3 sell trades a day; buy = whichever triggers first; sell = the 3 biggest gaps
                (the live sell orders are re-ranked by gap at 09:20 / 09:25 / 09:30)
    exits       buy: SL entry - 2%; sell: SL = high of the first 5-min candle (09:15-09:20)
                target = entry +/- 2 x risk; 50% qty out at 2% profit; trailing: every 1% profit -> SL 1% (of entry) closer;
                15:00 time exit
    money       Rs 1 crore every day (no compounding), risk 2% -> qty = 2,00,000 / risk per share,
                capped at 1 crore x 5 (MIS) / 6 per position; max 6 open; 1 trade / stock / day;
                daily loss (booked + open) 2% of capital -> close everything, no new trade
    costs       slippage 0.10% per fill + Angel One intraday equity charges
"""
from pathlib import Path

import numpy as np
import pandas as pd

from backtest import fast

OPEN = fast.OPEN_MIN                      # 555 = 09:15
AMFI = Path(__file__).resolve().parents[3] / "Algo_Historical_Data" / "data" / "reference" / "amfi_mcap_history.csv"

CFG = {
    "capital": 10_000_000.0, "leverage": 5.0, "risk_pct": 2.0,
    "min_mcap_cr": 5000.0, "min_price": 30.0, "min_avg_vol": 500_000, "min_atr_pct": 2.0, "min_gap_pct": 2.0,
    "checks": [5, 10, 15],                # 5-min candles closing at 09:20, 09:25, 09:30
    "sl_pct": 2.0, "rr": 2.0,
    "part_pct": 2.0, "part_frac": 0.5,
    "trail_trig": 1.0, "trail_step": 1.0,
    "exit_min": 15 * 60 - OPEN,           # 15:00
    "max_open": 6, "max_side": 3, "daily_loss_pct": 2.0,
    "slip_pct": 0.10,
    "amfi_lag_days": 10,                  # a half-year list (ends 30 Jun / 31 Dec) is used from the 10th of the next month
}
COST = {"brk_pct": 0.03, "brk_cap": 20.0, "stt_sell": 0.025, "exch": 0.00297, "sebi": 0.0001, "stamp_buy": 0.003, "gst": 18.0}


def hhmm(i) -> str:
    m = OPEN + int(i)
    return f"{m // 60:02d}:{m % 60:02d}"


def r3_standard(h, l, c):
    p = (h + l + c) / 3
    return h + 2 * (p - l)


def charges(buy_vals: list[float], sell_vals: list[float]) -> dict:
    b = lambda v: min(COST["brk_cap"], COST["brk_pct"] / 100 * v)
    bv, sv = sum(buy_vals), sum(sell_vals)
    brk = sum(b(v) for v in buy_vals) + sum(b(v) for v in sell_vals)
    turn = bv + sv
    exch, sebi = COST["exch"] / 100 * turn, COST["sebi"] / 100 * turn
    stt, stamp = COST["stt_sell"] / 100 * sv, COST["stamp_buy"] / 100 * bv
    gst = COST["gst"] / 100 * (brk + exch + sebi)
    return {"brokerage": brk, "stt": stt, "exchange": exch + sebi, "stamp": stamp, "gst": gst, "total": brk + stt + exch + sebi + stamp + gst}


# ---------------------------------------------------------------- data
def mcap_asof(days: list[str], cfg=CFG) -> pd.DataFrame:
    """date, symbol, mcap_cr: market cap from the latest AMFI list that was out on that day (no look-ahead)."""
    a = pd.read_csv(AMFI).dropna(subset=["sym", "mcap_cr"])
    a["period_end"] = pd.to_datetime(a["period_end"])
    a["live_from"] = (a["period_end"] + pd.offsets.MonthBegin(1)) + pd.Timedelta(days=cfg["amfi_lag_days"] - 1)
    a = a.groupby(["live_from", "sym"], as_index=False)["mcap_cr"].max()
    lives = sorted(a["live_from"].unique())
    rows = []
    for d in days:
        t = pd.Timestamp(d)
        ok = [x for x in lives if x <= t]
        if ok:
            rows.append((d, ok[-1]))
    m = pd.DataFrame(rows, columns=["date", "live_from"]).merge(a, on="live_from")
    return m.rename(columns={"sym": "symbol", "live_from": "amfi_list"})[["date", "symbol", "mcap_cr", "amfi_list"]]


def features(days: list[str], cfg=CFG) -> pd.DataFrame:
    lo = (pd.Timestamp(days[0]) - pd.Timedelta(days=60)).strftime("%Y-%m-%d")
    p = fast.add_features(fast.panel(lo, days[-1], columns=["open", "high", "low", "close", "volume", "value", "n_bars"]))
    p["date"] = pd.to_datetime(p["date"]).dt.strftime("%Y-%m-%d")
    p = p[p["date"].isin(set(days))].copy()
    p = p.merge(mcap_asof(days, cfg), on=["date", "symbol"], how="left")
    p["r3"] = r3_standard(p["prev_high"], p["prev_low"], p["prev_close"])
    p["ok"] = ((p["mcap_cr"] >= cfg["min_mcap_cr"]) & (p["open"] >= cfg["min_price"]) & (p["avg_vol"] >= cfg["min_avg_vol"])
               & (p["atr_pct"] >= cfg["min_atr_pct"]) & (p["gap_pct"] >= cfg["min_gap_pct"]) & p["r3"].notna())
    return p


def grid(m1: pd.DataFrame, syms) -> tuple:
    m1 = m1[m1["symbol"].isin(set(syms))]
    S = sorted(m1["symbol"].unique())
    ix = {s: i for i, s in enumerate(S)}
    o, h, l, c = (np.full((len(S), 375), np.nan) for _ in range(4))
    r = m1["symbol"].map(ix).to_numpy()
    k = m1["minute"].to_numpy().astype(int) - OPEN
    ok = (k >= 0) & (k < 375)
    for arr, col in ((o, "open"), (h, "high"), (l, "low"), (c, "close")):
        arr[r[ok], k[ok]] = m1[col].to_numpy()[ok]
    c = pd.DataFrame(c).T.ffill().bfill().T.to_numpy()
    miss = np.isnan(o)
    o[miss], h[miss], l[miss] = c[miss], c[miss], c[miss]
    return S, o, h, l, c


# ---------------------------------------------------------------- one day
def signals(day: str, P: pd.DataFrame, S, o, h, l, c, cfg=CFG) -> pd.DataFrame:
    """Per stock: first green 5-min candle closing above R3 (buy) and first red one closing above R3 (sell)."""
    ix = {s: i for i, s in enumerate(S)}
    p = P[(P["date"] == day) & P["ok"] & P["symbol"].isin(ix)]
    rows = []
    for r in p.itertuples(index=False):
        i = ix[r.symbol]
        fh = h[i, 0:5].max()                                   # first 5-min candle high (09:15-09:20)
        got = {}
        for k in cfg["checks"]:
            o5, h5, l5, c5 = o[i, k - 5], h[i, k - 5:k].max(), l[i, k - 5:k].min(), c[i, k - 1]
            if c5 > r.r3:
                side = "BUY" if c5 > o5 else ("SELL" if c5 < o5 else None)
                if side and side not in got and side in cfg.get("sides", ("BUY", "SELL")):
                    got[side] = 1
                    trig = h5 if side == "BUY" else l5
                    rows.append({"date": day, "symbol": r.symbol, "side": side, "k": k, "t": hhmm(k), "trig": trig,
                                 "o5": o5, "h5": h5, "l5": l5, "c5": c5, "r3": r.r3, "first_high": fh, "gap": r.gap_pct,
                                 "atr": r.atr_pct, "avg_vol": r.avg_vol, "mcap": r.mcap_cr})
    return pd.DataFrame(rows)


def simulate(day: str, sig: pd.DataFrame, S, o, h, l, c, cfg=CFG) -> tuple[list, dict]:
    ix = {s: i for i, s in enumerate(S)}
    cap, slip = cfg["capital"], cfg["slip_pct"] / 100
    max_val = cap * cfg["leverage"] / cfg["max_open"]       # per-position value cap (5x buying power / max open)
    risk_rs = cap * cfg["risk_pct"] / 100
    by_k = {}
    for r in sig.itertuples(index=False):
        by_k.setdefault(int(r.k), []).append(r)
    pend = []                                                  # live signals (not filled, not cancelled)
    openp, done, realised, killed, skipped = [], [], 0.0, None, []
    taken = {"BUY": 0, "SELL": 0}
    traded = set()

    def close_leg(t, qty, px_raw, m, why):
        fill = px_raw * (1 - slip) if t["side"] == "BUY" else px_raw * (1 + slip)
        t["legs"].append((m, qty, px_raw, fill, why))
        t["left"] -= qty

    def finish(t):
        legs, d = t["legs"], (1 if t["side"] == "BUY" else -1)
        outs = [q * f for _, q, _, f, _ in legs]
        inn = t["qty"] * t["entry_f"]
        ch = charges([inn], outs) if d == 1 else charges(outs, [inn])
        gross0 = d * sum(q * (p - t["entry"]) for _, q, p, _, _ in legs)
        net_before = d * sum(q * (f - t["entry_f"]) for _, q, _, f, _ in legs)
        t.update(charges=ch, gross0=gross0, slip_rs=gross0 - net_before, net=net_before - ch["total"],
                 x=legs[-1][0], exit=sum(q * p for _, q, p, _, _ in legs) / t["qty"], exit_f=sum(outs) / t["qty"],
                 reason=" + ".join(dict.fromkeys(x[4] for x in legs)))
        done.append(t)
        return t["net"]

    def mtm(m):
        tot = 0.0
        for t in openp:
            d = 1 if t["side"] == "BUY" else -1
            cl = c[t["i"], m] * (1 - slip * d)
            tot += d * (t["left"] * (cl - t["entry_f"]) + sum(q * (f - t["entry_f"]) for _, q, _, f, _ in t["legs"]))
        return tot

    def active_sells():
        """Live sell orders = top (3 - sells already taken) by gap among pending sell signals."""
        s = sorted([r for r in pend if r.side == "SELL"], key=lambda r: -r.gap)
        return s[:max(0, cfg["max_side"] - taken["SELL"])]

    for m in range(375):
        if m >= cfg["exit_min"] and pend:
            for r in pend:
                skipped.append((r.symbol, r.side, r.t, "trigger nahi hua (15:00 tak)"))
            pend = []
        if killed is None:
            pend += by_k.get(m, [])                            # signal candle closed at 09:15 + m -> order live from minute m
        # 1) entries: stop orders triggered inside this minute (time order by touch is unknown; take buys first by
        #    symbol order only when several trigger in the same minute)
        if pend and killed is None:
            live = [r for r in pend if r.side == "BUY"] + active_sells()
            for r in [r for r in pend if r.side == "SELL" and r not in live and l[ix[r.symbol], m] < r.trig]:
                pend.remove(r)                                 # low broke while this order was not live
                skipped.append((r.symbol, r.side, r.t, "gap rank top 3 me nahi tha" if taken["SELL"] < cfg["max_side"] else "SELL side ke 3 trade ho chuke"))
            for r in sorted(live, key=lambda r: (r.side, -r.gap if r.side == "SELL" else 0, r.symbol)):
                i = ix[r.symbol]
                if r.side == "BUY" and h[i, m] > r.trig:
                    e = max(o[i, m], r.trig)
                elif r.side == "SELL" and l[i, m] < r.trig:
                    e = min(o[i, m], r.trig)
                else:
                    continue
                pend.remove(r)
                if r.symbol in traded:
                    skipped.append((r.symbol, r.side, r.t, "is stock me aaj trade ho chuka"))
                    continue
                if taken[r.side] >= cfg["max_side"]:
                    skipped.append((r.symbol, r.side, r.t, f"{r.side} side ke 3 trade ho chuke"))
                    continue
                if len(openp) >= cfg["max_open"]:
                    skipped.append((r.symbol, r.side, r.t, f"max {cfg['max_open']} position khuli"))
                    continue
                if r.side == "BUY":
                    sl0 = e * (1 - cfg["sl_pct"] / 100)
                else:
                    sl0 = r.first_high
                    if sl0 <= e:
                        skipped.append((r.symbol, r.side, r.t, "pehli candle ka high entry se neeche (SL galat side)"))
                        continue
                risk_ps = abs(e - sl0)
                qty = int(min(risk_rs // risk_ps, max_val // e))
                if qty <= 0:
                    skipped.append((r.symbol, r.side, r.t, "qty 0"))
                    continue
                d = 1 if r.side == "BUY" else -1
                taken[r.side] += 1
                traded.add(r.symbol)
                for x in [x for x in pend if x.symbol == r.symbol]:
                    skipped.append((x.symbol, x.side, x.t, "is stock me aaj trade ho chuka"))
                pend = [x for x in pend if x.symbol != r.symbol]
                openp.append({"date": day, "sym": r.symbol, "side": r.side, "i": i, "sig": r.k - 5, "e": m, "entry": e,
                              "entry_f": e * (1 + slip * d), "qty": qty, "left": qty, "sl0": sl0, "sl": sl0, "risk_ps": risk_ps,
                              "tgt": e + d * cfg["rr"] * risk_ps, "part": e * (1 + d * cfg["part_pct"] / 100), "part_done": False,
                              "best": e, "legs": [], "r3": r.r3, "gap": r.gap, "atr": r.atr, "mcap": r.mcap, "trig": r.trig,
                              "t_sig": r.t, "c5": r.c5, "o5": r.o5, "first_high": r.first_high, "trail_moves": 0})
        # 2) exits: stop first (safe side), then 50%, then target; trail from the best price
        for t in list(openp):
            i, d = t["i"], (1 if t["side"] == "BUY" else -1)
            op, hi, lo = o[i, m], h[i, m], l[i, m]
            adv, fav = (lo, hi) if d == 1 else (hi, lo)        # adverse / favourable extreme of the minute
            first = m == t["e"]
            if m >= cfg["exit_min"]:
                close_leg(t, t["left"], op, m, "Time exit 15:00")
            elif d * (adv - t["sl"]) <= 0:
                px = t["sl"] if first else (min(op, t["sl"]) if d == 1 else max(op, t["sl"]))
                gain = d * (t["sl"] - t["entry"])
                close_leg(t, t["left"], px, m, "SL" if gain < -1e-9 else ("Trailing SL (entry pe)" if abs(gain) <= 1e-9 else "Trailing SL (profit)"))
            else:
                if not t["part_done"] and d * (fav - t["part"]) >= 0:
                    pq = int(t["qty"] * cfg["part_frac"])
                    if pq > 0:
                        px = t["part"] if first else (max(op, t["part"]) if d == 1 else min(op, t["part"]))
                        close_leg(t, pq, px, m, "50% @ 2%")
                    t["part_done"] = True
                if t["left"] > 0 and d * (fav - t["tgt"]) >= 0:
                    px = t["tgt"] if first else (max(op, t["tgt"]) if d == 1 else min(op, t["tgt"]))
                    close_leg(t, t["left"], px, m, "Target 1:2")
            if t["left"] <= 0:
                openp.remove(t)
                realised += finish(t)
                continue
            t["best"] = max(t["best"], hi) if d == 1 else min(t["best"], lo)
            gain = d * (t["best"] / t["entry"] - 1) * 100
            if gain >= cfg["trail_trig"]:
                n = int(np.floor((gain - cfg["trail_trig"]) / cfg["trail_step"] + 1e-9)) + 1
                new = t["sl0"] + d * n * cfg["trail_step"] / 100 * t["entry"]
                if d * (new - t["sl"]) > 0:
                    t["sl"], t["trail_moves"] = new, n
        # 3) daily loss limit on booked + open P&L at this minute's close
        if killed is None and (done or openp):
            if realised + mtm(m) <= -cap * cfg["daily_loss_pct"] / 100:
                killed = m
                for r in pend:
                    skipped.append((r.symbol, r.side, r.t, "daily loss limit hit (order cancel)"))
                pend = []
                nm = min(m + 1, 374)
                for t in list(openp):
                    close_leg(t, t["left"], o[t["i"], nm], nm, "Daily loss limit")
                    openp.remove(t)
                    realised += finish(t)
    for r in pend:
        skipped.append((r.symbol, r.side, r.t, "trigger nahi hua (15:00 tak)"))
    for t in openp:
        close_leg(t, t["left"], c[t["i"], 374], 374, "Din khatam")
        realised += finish(t)
    return done, {"date": day, "net": realised, "killed": None if killed is None else hhmm(killed + 1), "skipped": skipped,
                  "signals": len(sig), "buy_sig": int((sig["side"] == "BUY").sum()) if len(sig) else 0,
                  "sell_sig": int((sig["side"] == "SELL").sum()) if len(sig) else 0}


def run(days: list[str], cfg=CFG, log=print) -> tuple:
    P = features(days, cfg)
    T, SG, info, G = [], [], [], {}
    for n, d in enumerate(days):
        cand = P[(P["date"] == d) & P["ok"]]["symbol"].tolist()
        di = {"date": d, "net": 0.0, "killed": None, "skipped": [], "signals": 0, "buy_sig": 0, "sell_sig": 0, "passed": len(cand)}
        if cand:
            m1 = fast.day_1m(d, symbols=cand)
            if m1.empty:
                log(f"{d}: 1-min data nahi")
            else:
                S, o, h, l, c = grid(m1, cand)
                sg = signals(d, P, S, o, h, l, c, cfg)
                tr, x = simulate(d, sg, S, o, h, l, c, cfg)
                di.update(x)
                T += tr
                if len(sg):
                    SG.append(sg)
                for t in tr:
                    i = S.index(t["sym"])
                    G[(d, t["sym"])] = (o[i], h[i], l[i], c[i])
        info.append(di)
        if n % 50 == 0 or n == len(days) - 1:
            log(f"{n + 1}/{len(days)} {d}: filter pass {len(cand)}, trades ab tak {len(T)}, net ab tak Rs {sum(t['net'] for t in T):,.0f}")
    SG = pd.concat(SG, ignore_index=True) if SG else pd.DataFrame()
    return pd.DataFrame(T), SG, info, G
