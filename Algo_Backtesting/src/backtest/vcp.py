"""VCP swing (Volatility Contraction Pattern, Minervini style) on NSE stocks with daily candles.

    D = load()                              # adjusted daily OHLCV (wide), Nifty 500 proxy universe, Nifty 500 index + market filter
    I = indicators(D)                       # EMA 10/20/50, 50-day average volume, 20-day average range, inside bars, liquidity
    E = scan(D, I, p)                       # expansion candles (day E) that pass every scanner rule
    S = watch(D, I, E, p)                   # watchlist: one entry (or the reason there was none) per expansion candle
    X = paths(I, S, p)                      # per-share exit path (legs) of every entry
    t, m = portfolio(D, I, X, p)            # sizing, caps, cash, costs -> trades in Rs + daily mark-to-market equity
    stats(t, m, p.capital, start, end)

Timing (no look-ahead): every decision uses data up to the close of day t; orders are for day t+1 and fill only from
day t+1's OHLC. Close-based exits (EMA, time stop, 50 EMA failure) therefore fill at the next day's open. When the stop
and a target are both inside one daily bar, the stop is taken first; a gap below the stop fills at the open.
"""
from dataclasses import dataclass, replace

import numpy as np
import pandas as pd

from backtest import fast
from backtest.config import DATA, UNIVERSE_CSV

START, END = "2021-10-01", "2026-10-30"
INDEX_1M = DATA / "index" / "NIFTY500_1m.csv"


@dataclass(frozen=True)
class Params:
    """Every number of the test in one place (the run script changes them with with_params)."""
    # money
    capital: float = 1_000_000.0
    compound: bool = False                 # False: size on the starting capital; True: on capital + booked P&L
    risk: float = 0.0075                   # share of capital risked per trade (entry - stop)
    max_pos_pct: float = 0.20              # one position at most 20% of capital
    max_open: int = 8                      # concurrent positions
    adv_pct: float = 0.05                  # quantity at most 5% of the stock's 50-day average volume
    # universe
    universe: str = "top500"               # "top500" = AMFI Jun 2026 top 500 by market cap (Nifty 500 proxy), "all" = every stock in the data
    min_value_cr: float = 5.0              # 20-day average traded value, Rs crore
    min_hist: int = 100                    # trading days of history a stock needs before its first expansion candle counts
    # scanner (day E)
    vol_mult: float = 3.0                  # volume >= 3x the 50-day average (previous 50 days)
    rng_mult: float = 1.5                  # range >= 1.5x the 20-day average range (previous 20 days)
    close_top: float = 2 / 3               # close in the upper third of the day's range
    young_n: int = 90                      # no other expansion candle in the previous 90 trading days
    ext_max: float = 0.15                  # close <= 15% above the 50 EMA
    stage_n: int = 250                     # "late stage" = another expansion candle 91-250 trading days before E
    # watchlist (contraction)
    watch_n: int = 20                      # trading days after E
    contr: float = 0.5                     # contraction bar: range < 50% of E's range (inside bars always count)
    ema_hold: int = 20                     # 20 = tight (a close below the 20 EMA ends the setup), 50 = base
    # entry
    trigger: str = "cb"                    # "cb" = high of the latest contraction / inside bar, "exp" = expansion candle high
    buf: float = 0.001                     # +0.1% above that high
    gap_max: float = 0.02                  # skip if the open is more than 2% above the trigger
    rising_n: int = 5                      # 20 and 50 EMA higher than 5 days ago
    mkt_filter: bool = True                # Nifty 500 close > its 50 EMA and 50 EMA > 200 EMA
    # stop
    stop: str = "fixed"                    # "fixed" (stop_pct below entry), "candle" (low of the trigger bar), "hybrid" (the tighter one)
    stop_pct: float = 0.025
    entry_day_stop: str = "low"            # "low": entry day low <= stop counts as stopped (stop first); "close": only a close below the stop;
                                           # "1m": 1-minute bars of the entry day after the fill decide (check only, needs entry_1m)
    # exits
    exit: str = "A"                        # A: 50% at +2R, stop to entry, trail 10 EMA; B: same with 20 EMA; C: 1/3 at T1/T2/T3; D: sell into strength
    rr_part: float = 2.0
    part: float = 0.5
    leg_mult: tuple = (1.0, 2.0, 3.0)      # C: targets = trigger + k x expansion candle range
    gain_day: float = 0.08                 # D: one-day gain >= 8%
    ext10: float = 0.10                    # D: close > 10% above the 10 EMA
    max_days: int = 20                     # time stop (trading days in the trade, entry day = day 1)
    # costs
    slip: float = 0.0005                   # slippage per fill (share of price)


def with_params(p: Params, **kw) -> Params:
    return replace(p, **kw)


# ---------------------------------------------------------------- data
def nifty500(cal: list[str]) -> pd.DataFrame:
    """Daily Nifty 500 (from Fyers 1-minute bars, Jun 2020 on) with its 50 / 200 EMA and the market filter, on the stock calendar."""
    x = pd.read_csv(INDEX_1M, usecols=["datetime", "open", "high", "low", "close"])
    x["date"] = x.datetime.str[:10]
    d = x.groupby("date").agg(open=("open", "first"), high=("high", "max"), low=("low", "min"), close=("close", "last"))
    d["ema50"] = d.close.ewm(span=50, adjust=False).mean()
    d["ema200"] = d.close.ewm(span=200, adjust=False).mean()
    d["on"] = (d.close > d.ema50) & (d.ema50 > d.ema200)
    return d.reindex(sorted(set(d.index) | set(cal))).ffill().loc[cal]


def load(start: str = START, end: str = END) -> dict:
    p = fast.panel(start, end, columns=["open", "high", "low", "close", "volume", "value"])
    p["date"] = p.date.astype(str)
    W = {k: p.pivot(index="date", columns="symbol", values=k).astype(float) for k in ("open", "high", "low", "close", "volume", "value")}
    cal, syms = [str(d) for d in W["close"].index], [str(s) for s in W["close"].columns]
    u = pd.read_csv(UNIVERSE_CSV)
    u["sym"] = u.symbol.str.replace(r"^NSE:", "", regex=True).str.replace(r"-[A-Z]{2}$", "", regex=True)
    u = u[u.sym.isin(syms)]
    top = u.nlargest(500, "mcap_cr")
    return {"W": W, "cal": cal, "syms": syms, "top500": set(top.sym), "mcap": dict(zip(u.sym, u.mcap_cr)), "cat": dict(zip(u.sym, u.category)),
            "idx": nifty500(cal), "top500_min_cr": float(top.mcap_cr.min())}


def indicators(D: dict) -> dict:
    W = D["W"]
    O, H, L, C, V, VAL = (W[k] for k in ("open", "high", "low", "close", "volume", "value"))
    R = H - L
    ema = lambda n: C.ewm(span=n, adjust=False).mean()
    av50 = V.rolling(50, min_periods=40).mean()
    I = {"O": O.to_numpy(), "H": H.to_numpy(), "L": L.to_numpy(), "C": C.to_numpy(), "V": V.to_numpy(), "R": R.to_numpy(),
         "E10": ema(10).to_numpy(), "E20": ema(20).to_numpy(), "E50": ema(50).to_numpy(),
         "av50": av50.to_numpy(),                                             # includes day t (known at its close)
         "av50p": av50.shift(1).to_numpy(),                                   # previous 50 days (day t excluded)
         "ar20p": R.rolling(20, min_periods=15).mean().shift(1).to_numpy(),   # previous 20 days
         "val20": VAL.rolling(20, min_periods=15).mean().to_numpy(),          # Rs, includes day t
         "cnt": C.notna().cumsum().to_numpy(),
         "Cf": C.ffill().to_numpy()}
    I["inside"] = (H <= H.shift(1)) & (L >= L.shift(1))
    I["inside"] = I["inside"].to_numpy()
    I["mkt"] = D["idx"]["on"].fillna(False).to_numpy().astype(bool)
    return I


# ---------------------------------------------------------------- scanner
def scan(D: dict, I: dict, p: Params) -> pd.DataFrame:
    """Expansion candles (day E) that pass every scanner rule: one row per (day, stock)."""
    O, H, L, C, V, R = (I[k] for k in ("O", "H", "L", "C", "V", "R"))
    with np.errstate(invalid="ignore", divide="ignore"):
        raw = (V >= p.vol_mult * I["av50p"]) & (R >= p.rng_mult * I["ar20p"]) & (C > O) & (C >= L + p.close_top * R) & (R > 0)
        rx = pd.DataFrame(raw.astype(float))
        young = rx.rolling(p.young_n, min_periods=1).sum().shift(1).fillna(0).to_numpy() == 0
        late = rx.rolling(p.stage_n, min_periods=1).sum().shift(1).fillna(0).to_numpy() > 0
        dist = C / I["E50"] - 1
        ok = raw & young & (dist <= p.ext_max) & (I["val20"] >= p.min_value_cr * 1e7) & (I["cnt"] >= p.min_hist)
    uni = np.array([s in D["top500"] for s in D["syms"]]) if p.universe == "top500" else np.ones(len(D["syms"]), dtype=bool)
    ok &= uni[None, :]
    ok[-3:] = False
    ii, jj = np.nonzero(ok)
    e = pd.DataFrame({"i": ii, "j": jj})
    e["date"] = [D["cal"][i] for i in ii]
    e["symbol"] = [D["syms"][j] for j in jj]
    e["vm"] = V[ii, jj] / I["av50p"][ii, jj]
    e["rm"] = R[ii, jj] / I["ar20p"][ii, jj]
    e["dist50"] = dist[ii, jj] * 100
    e["stage"] = np.where(late[ii, jj], "late", "early")
    e["value_cr"] = I["val20"][ii, jj] / 1e7
    return e.sort_values(["i", "j"]).reset_index(drop=True)


# ---------------------------------------------------------------- watchlist + entry
def watch(I: dict, E: pd.DataFrame, p: Params) -> pd.DataFrame:
    """Follow every expansion candle for watch_n days: contraction bars, removal rules, confirmation, market filter,
    buy-stop for the next day. Returns E with the outcome (entry or why not)."""
    O, H, L, C, V = I["O"], I["H"], I["L"], I["C"], I["V"]
    E20, E50, av50, mkt, inside = I["E20"], I["E50"], I["av50"], I["mkt"], I["inside"]
    T = C.shape[0]
    rows = []
    for i, j in zip(E.i.to_numpy(), E.j.to_numpy()):
        rngE, lowE, highE = H[i, j] - L[i, j], L[i, j], H[i, j]
        cbs, res = [], {"outcome": "expired", "why": f"{p.watch_n} din me trigger nahi"}
        for t in range(i + 1, min(i + p.watch_n, T - 2) + 1):
            c = C[t, j]
            if c != c:
                continue
            if c < E50[t, j] or c < lowE:
                res = {"outcome": "removed", "why": "close 50 EMA ke neeche" if c < E50[t, j] else "close expansion low ke neeche", "end": t}
                break
            if p.ema_hold == 20 and c < E20[t, j]:
                res = {"outcome": "removed", "why": "close 20 EMA ke neeche (tight)", "end": t}
                break
            if H[t, j] - L[t, j] < p.contr * rngE or inside[t, j]:
                cbs.append(t)
            if not cbs or not np.nanmean(V[cbs, j]) < av50[t, j]:
                continue
            if not (c > E20[t, j] and c > E50[t, j] and E20[t, j] > E20[t - p.rising_n, j] and E50[t, j] > E50[t - p.rising_n, j]):
                continue
            if p.mkt_filter and not mkt[t]:
                continue
            cb = cbs[-1]
            trig = (H[cb, j] if p.trigger == "cb" else highE) * (1 + p.buf)
            k = t + 1
            o, h = O[k, j], H[k, j]
            if o != o:
                continue
            if o > trig * (1 + p.gap_max):
                res = {"outcome": "gap", "why": f"open trigger se {p.gap_max * 100:g}% se zyada upar", "end": k}
                break
            if o >= trig or h >= trig:
                res = {"outcome": "entry", "why": "", "end": k, "t": t, "k": k, "cb": cb, "trig": trig, "e": o if o >= trig else trig,
                       "at_open": bool(o >= trig), "n_cb": len(cbs), "mkt_t": bool(mkt[t])}
                break
        rows.append(res)
    return pd.concat([E.reset_index(drop=True), pd.DataFrame(rows)], axis=1)


# ---------------------------------------------------------------- exits (per share)
def _path(I: dict, s, p: Params) -> dict:
    O, H, L, C, E10, E20, E50 = (I[k] for k in ("O", "H", "L", "C", "E10", "E20", "E50"))
    i, j, k, cb, e, trig = int(s.i), int(s.j), int(s.k), int(s.cb), float(s.e), float(s.trig)
    T = C.shape[0]
    fixed, low_cb = e * (1 - p.stop_pct), L[cb, j]
    stop0 = {"fixed": fixed, "candle": low_cb, "hybrid": max(fixed, low_cb)}[p.stop]
    if not stop0 < e * 0.999:
        stop0 = fixed
    rps = e - stop0
    if p.exit in ("A", "B"):
        tg = [[e + p.rr_part * rps, p.part, f"+{p.rr_part:g}R pe {p.part * 100:g}% becha"]]
    elif p.exit == "C":
        rngE = H[i, j] - L[i, j]
        tg = [[trig + m * rngE, 1 / len(p.leg_mult), f"T{n + 1} pe 1/{len(p.leg_mult)} becha"] for n, m in enumerate(p.leg_mult)]
    else:
        tg = []
    legs, rem, stop, trail, pend, prev_c = [], 1.0, stop0, False, None, C[k - 1, j]
    d = k
    while True:
        o, h, l, c = O[d, j], H[d, j], L[d, j], C[d, j]
        if c == c:
            if d > k:
                if pend:
                    legs.append([d, rem, o, pend])
                    break
                if o <= stop:
                    legs.append([d, rem, o, "stop (gap down, open pe)" if not trail else "breakeven stop (gap, open pe)"])
                    break
            m1 = I.get("ed1m", {}).get((k, j)) if d == k and p.entry_day_stop == "1m" else None
            if m1 is not None:                                  # entry day walked minute by minute after the fill (stop first inside a minute)
                hit = False
                for lo, hi in zip(*m1):
                    if lo <= stop:
                        hit = True
                        break
                    for g in tg:
                        if g[1] and hi >= g[0]:
                            legs.append([d, g[1], g[0], g[2]])
                            rem -= g[1]
                            g[1] = 0
                            trail = trail or p.exit in ("A", "B")
                if hit:
                    legs.append([d, rem, stop, "stop (entry din)"])
                    break
                if trail:
                    stop = max(stop, e)
            else:
                hit = (l <= stop) if (d > k or p.entry_day_stop in ("low", "1m")) else (c < stop)
                if hit:
                    legs.append([d, rem, stop, ("stop" if not trail else "breakeven stop") + (" (entry din)" if d == k else "")])
                    break
                for g in tg:
                    if g[1] and h >= g[0]:
                        legs.append([d, g[1], max(o, g[0]) if d > k else g[0], g[2]])
                        rem -= g[1]
                        g[1] = 0
                        if p.exit in ("A", "B"):
                            stop, trail = max(stop, e), True
            if rem <= 1e-9:
                break
            if c < E50[d, j]:
                pend = "50 EMA ke neeche close (setup fail)"
            elif p.exit == "A" and trail and c < E10[d, j]:
                pend = "10 EMA ke neeche close (trailing)"
            elif p.exit == "B" and trail and c < E20[d, j]:
                pend = "20 EMA ke neeche close (trailing)"
            elif p.exit == "D" and (c / prev_c - 1 >= p.gain_day or c > (1 + p.ext10) * E10[d, j]):
                pend = "taakat me becha (8% din / 10 EMA se 10% upar)"
            elif d - k + 1 >= p.max_days:
                pend = f"time stop ({p.max_days} din)"
            prev_c = c
        if d >= T - 1:
            legs.append([d, rem, I["Cf"][d, j], "data khatam (aakhri close)"])
            break
        d += 1
    return {"stop0": stop0, "rps": rps, "legs": legs, "x": legs[-1][0], "targets": [g[0] for g in tg]}


def entry_1m(D: dict, I: dict, S: pd.DataFrame) -> None:
    """For entry_day_stop="1m": the 1-minute lows / highs after the fill on every entry day of S (adjusted, from the fast store),
    stored in I["ed1m"][(k, j)]. Fill minute = first minute (open fill) or the first minute whose high reaches the trigger."""
    ed = I.setdefault("ed1m", {})
    s = S[S.outcome == "entry"]
    s = s[[(int(k), int(j)) not in ed for k, j in zip(s.k, s.j)]]
    for k, g in s.groupby("k"):
        k = int(k)
        m = fast.day_1m(D["cal"][k], [D["syms"][int(j)] for j in g.j])
        by = {sym: x for sym, x in m.groupby("symbol")}
        for r in g.itertuples():
            x = by.get(D["syms"][int(r.j)])
            if x is None or x.empty:
                continue
            hi, lo = x.high.to_numpy(), x.low.to_numpy()
            f = 0 if r.at_open else int(np.argmax(hi >= r.trig)) if (hi >= r.trig).any() else None
            if f is not None:
                ed[(k, int(r.j))] = (lo[f + 1:], hi[f + 1:])


def paths(I: dict, S: pd.DataFrame, p: Params) -> pd.DataFrame:
    s = S[S.outcome == "entry"].copy()
    if s.empty:
        return s
    s[["k", "cb", "t"]] = s[["k", "cb", "t"]].astype(int)
    out = [_path(I, r, p) for r in s.itertuples()]
    for f in ("stop0", "rps", "legs", "x", "targets"):
        s[f] = [o[f] for o in out]
    return s.reset_index(drop=True)


# ---------------------------------------------------------------- portfolio
def costs(bv: float, sells: list[float]) -> dict:
    """Equity delivery charges (discount broker, no brokerage): STT 0.1% buy and sell, NSE 0.00307%, SEBI 0.0001%,
    stamp 0.015% on buy, GST 18% on exchange + SEBI, DP Rs 15.34 + GST per sell day."""
    sv = sum(sells)
    tv = bv + sv
    exch, sebi = 0.0000307 * tv, 1e-6 * tv
    c = {"c_stt": 0.001 * tv, "c_exch": exch + sebi, "c_stamp": 0.00015 * bv, "c_gst": 0.18 * (exch + sebi), "c_dp": 15.34 * 1.18 * len(sells)}
    c["costs"] = sum(c.values())
    return c


def portfolio(D: dict, I: dict, X: pd.DataFrame, p: Params) -> tuple[pd.DataFrame, dict]:
    """Takes the entries in date order (bigger volume multiple first on the same day) while there is room: max_open
    positions, cash (no leverage), 20% of capital per stock, 5% of average volume. Returns trades in Rs and daily
    mark-to-market equity / money deployed."""
    cal, syms, T = D["cal"], D["syms"], len(D["cal"])
    if X is None or X.empty:
        return pd.DataFrame(), _mtm([], T, p)
    X = X.sort_values(["k", "vm"], ascending=[True, False]).reset_index(drop=True)
    live, booked, trades = [], 0.0, []
    for r in X.itertuples():
        k = r.k
        done = [q for q in live if q["x"] < k]
        booked += sum(q["net"] for q in done)
        live = [q for q in live if q["x"] >= k]
        if any(q["j"] == r.j for q in live):
            continue
        eq = p.capital + (booked if p.compound else 0.0)
        cash = eq - sum(q["qty_open"](k) * q["ef"] for q in live)     # no leverage: open positions together <= capital
        ef = r.e * (1 + p.slip)
        risk_ps = ef - r.stop0
        if len(live) >= p.max_open or risk_ps <= 0:
            continue
        adv = I["av50p"][k, r.j]
        qty = int(np.floor(min(eq * p.risk / risk_ps, eq * p.max_pos_pct / ef, max(cash, 0) / ef, p.adv_pct * adv if adv == adv else 0)))
        if qty < 1:
            continue
        legs, cum, q_prev = [], 0.0, 0
        for n, (d, f, px, why) in enumerate(r.legs):
            cum += f
            q = qty - q_prev if n == len(r.legs) - 1 else int(np.floor(qty * cum + 1e-9)) - q_prev
            if q > 0:
                legs.append([int(d), q, float(px), why])
                q_prev += q
        sells = [q * px * (1 - p.slip) for d, q, px, why in legs]
        gross = sum(q * (px * (1 - p.slip) - ef) for d, q, px, why in legs)
        slip = qty * r.e * p.slip + sum(q * px * p.slip for d, q, px, why in legs)
        c = costs(qty * ef, sells)
        net = gross - c["costs"]
        x_idx = legs[-1][0]
        exit_avg = sum(q * px for d, q, px, why in legs) / qty
        t = {"symbol": r.symbol, "exp_date": r.date, "signal_date": cal[r.t], "entry_date": cal[k], "exit_date": cal[x_idx], "i": r.i, "j": r.j, "k": k, "x": x_idx,
             "vm": r.vm, "rm": r.rm, "dist50": r.dist50, "stage": r.stage, "value_cr": r.value_cr, "base_len": k - r.i, "n_cb": r.n_cb,
             "mkt_filter": "ON" if p.mkt_filter else "OFF", "mkt_t": "ON" if r.mkt_t else "OFF", "trigger_type": "contraction bar high" if p.trigger == "cb" else "expansion high",
             "trigger": r.trig, "entry": r.e, "at_open": r.at_open, "entry_fill": ef, "stop": r.stop0, "stop_type": p.stop, "cb_date": cal[r.cb],
             "cb_high": I["H"][r.cb, r.j], "cb_low": I["L"][r.cb, r.j], "exp_high": I["H"][r.i, r.j], "exp_low": I["L"][r.i, r.j],
             "targets": r.targets, "qty": qty, "notional": qty * ef, "risk_rs": qty * risk_ps, "exit": legs[-1][2], "exit_avg": exit_avg, "reason": legs[-1][3],
             "legs": [[cal[d], q, round(px, 2), why] for d, q, px, why in legs], "_legs": [(d, q, px) for d, q, px, why in legs], "hold": x_idx - k,
             "gross": gross, "slip": slip, **c, "net": net}
        t["R"] = net / t["risk_rs"]
        trades.append(t)
        leg_days = [(d, q) for d, q, px, why in legs]
        live.append({"j": r.j, "x": x_idx, "net": net, "ef": ef, "qty_open": (lambda kk, qty=qty, ld=leg_days: qty - sum(q for d, q in ld if d < kk))})
    m = _mtm(trades, T, p, I)
    return pd.DataFrame(trades).drop(columns="_legs", errors="ignore"), m


def _mtm(trades: list, T: int, p: Params, I: dict | None = None) -> dict:
    """Daily mark-to-market P&L (closed trades at net, open ones at close - entry fill) and money deployed (cost basis)."""
    pnl, dep = np.zeros(T), np.zeros(T)
    for t in trades:
        k, x, j, ef, legs = t["k"], t["x"], t["j"], t["entry_fill"], t["_legs"]
        open_q, real = t["qty"], 0.0
        li = 0
        for d in range(k, x):
            while li < len(legs) and legs[li][0] <= d:
                real += legs[li][1] * (legs[li][2] * (1 - p.slip) - ef)
                open_q -= legs[li][1]
                li += 1
            pnl[d] += real + open_q * (I["Cf"][d, j] - ef)
            dep[d] += open_q * ef
        pnl[x:] += t["net"]
    return {"pnl": pnl, "dep": dep}


def stats(t: pd.DataFrame, m: dict, cal: list[str], capital: float, start: str, end: str, compound: bool = False) -> dict:
    """Trade metrics + mark-to-market curve metrics for trades entered in [start, end]."""
    a, b = int(np.searchsorted(cal, start)), int(np.searchsorted(cal, end, side="right")) - 1
    yrs = (pd.Timestamp(cal[b]) - pd.Timestamp(cal[a])).days / 365.25
    out = {"trades": 0, "net": 0.0, "ret_pct": 0.0, "cagr": 0.0, "max_dd": 0.0, "max_dd_pct": 0.0, "win_pct": None, "avg_win_r": None, "avg_loss_r": None, "exp_r": None,
           "pf": None, "avg_hold": None, "exposure": 0.0, "in_mkt": 0.0, "stop3_pct": None, "top5_share": None, "top5_net": None, "rest_net": None, "worst": 0.0, "best": 0.0,
           "gross": 0.0, "costs": 0.0, "slip": 0.0, "years": yrs}
    if t is None or t.empty:
        return out
    t = t[(t.entry_date >= start) & (t.entry_date <= end)]
    if t.empty:
        return out
    n, R = t.net, t.R
    w, l = n[n > 0], n[n <= 0]
    eq = capital + m["pnl"][a:b + 1] - (m["pnl"][a - 1] if a > 0 else 0)
    peak = np.maximum.accumulate(np.r_[capital, eq])[1:]
    dd = eq - peak
    ddp = dd / peak
    net = float(n.sum())
    top = n.sort_values(ascending=False).head(max(1, int(np.ceil(len(n) * 0.05))))
    out.update({"trades": int(len(t)), "net": net, "ret_pct": net / capital * 100, "max_dd": float(dd.min()), "max_dd_pct": float(ddp.min() * 100),
                "cagr": ((eq[-1] / capital) ** (1 / yrs) - 1) * 100 if eq[-1] > 0 else -100.0, "avg_year_pct": float(eq[-1] / capital - 1) / yrs * 100,
                "win_pct": float((n > 0).mean() * 100), "avg_win_r": float(R[n > 0].mean()) if len(w) else None, "avg_loss_r": float(R[n <= 0].mean()) if len(l) else None,
                "exp_r": float(R.mean()), "pf": float(w.sum() / -l.sum()) if l.sum() < 0 else None, "avg_hold": float(t.hold.mean()),
                "exposure": float(np.mean(m["dep"][a:b + 1] / np.maximum(eq, 1)) * 100), "in_mkt": float(np.mean(m["dep"][a:b + 1] > 0) * 100),
                "stop3_pct": float((t.reason.str.startswith("stop") & (t.hold <= 3)).mean() * 100),
                "top5_net": float(top.sum()), "rest_net": net - float(top.sum()), "top5_share": float(top.sum() / net * 100) if net > 0 else None,
                "worst": float(n.min()), "best": float(n.max()), "gross": float(t.gross.sum()), "costs": float(t.costs.sum()), "slip": float(t.slip.sum())})
    return out


def buy_hold(D: dict, capital: float, start: str, end: str) -> dict:
    """Nifty 500 buy-and-hold (price index, no dividends) over the same dates."""
    c = D["idx"].close
    c = c[(c.index >= start) & (c.index <= end)]
    yrs = (pd.Timestamp(c.index[-1]) - pd.Timestamp(c.index[0])).days / 365.25
    ret = c.iloc[-1] / c.iloc[0] - 1
    dd = (c / c.cummax() - 1).min()
    return {"ret_pct": ret * 100, "cagr": ((1 + ret) ** (1 / yrs) - 1) * 100, "max_dd_pct": dd * 100, "net": capital * ret, "years": yrs}
