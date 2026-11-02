"""CPR + VWAP + Open-Low intraday setup (NK Stock Talk, part 1).

First 10-minute candle (9:15-9:25) of an F&O stock: Open = Low (within a tolerance), green, tail < body, body not too tall,
today's CPR above yesterday's, and the open near the CPR -> BUY. Short = the mirror (Open = High, CPR stepping down).
Entry A: 9:25 open, SL = signal candle low. Entry B: first 10-min candle up to 12:30 that dips to VWAP and closes above it,
entry at the next minute's open, SL = that candle's low or just below VWAP. Exits: hold to 15:15, 1:2 / 1:3 target,
trail under the last 10-min low or under VWAP, or exit when a 10-min candle closes below VWAP.

Two steps:
    prepare()   reads every day file once (in parallel): daily H/L/C of every stock, the loose candidates and the result
                of every entry / SL / exit combination for them (raw prices, no slippage or costs yet)
    select()    applies one filter setting, keeps max_trades per day and prices them with slippage and costs
"""
import math
from concurrent.futures import ProcessPoolExecutor, as_completed
from typing import Callable

import numpy as np
import pandas as pd

from backtest.config import DATA, EQUITY
from backtest.data import COLUMNS, trading_days
from backtest import fast

BANDS = DATA / "reference" / "price_bands"   # NSE sec_list per day; "No Band" = stock has F&O (ETFs are not in our data)
T_OPEN = 9 * 60 + 15
T_ENTRY = 9 * 60 + 25         # signal = 1m bars 9:15..9:24; entry A at the 9:25 open
T_B_LAST = 12 * 60 + 30       # entry B: the pullback candle must close by 12:30
T_SQUARE = 15 * 60 + 15       # square-off at the 15:15 open
TOL_MAX = 0.001               # loosest Open = Low tolerance kept in prepare()
VWAP_BUF = 0.001              # "just below VWAP" = VWAP x (1 - 0.1%)
EXPOSURE = 175_000            # Rs 35,000 margin x 5 leverage per trade
CAPITAL = 70_000
MAX_TRADES = 2
COST_PCT = 0.001              # 0.10% of the trade value
SPLIT_DAY = "2025-01-01"      # before = in-sample (choose settings), from = out-of-sample (test)
WORKERS = 6
ENTRIES = {"A": "A: 9:25 pe", "B_low": "B: VWAP pullback, SL candle low", "B_vwap": "B: VWAP pullback, SL VWAP ke neeche"}
EXITS = {"hold": "3:15 tak hold", "rr2": "target 1:2", "rr3": "target 1:3", "trail_candle": "trail 10-min low",
         "trail_vwap": "trail VWAP", "vwap_close": "VWAP ke neeche close = exit"}


def fno_symbols(day: str) -> set[str]:
    """F&O stocks on that day (latest band file on or before it)."""
    files = sorted(p for p in BANDS.glob("????-??-??.csv") if p.stem <= day)
    if not files:
        return set()
    b = pd.read_csv(files[-1], usecols=["Symbol", "Series", "Band"])
    return set(b[(b.Series == "EQ") & b.Band.astype(str).str.strip().str.lower().eq("no band")].Symbol)


def _first(mask: np.ndarray) -> int:
    return int(mask.argmax()) if mask.any() else 10**9


def _exit(m, o, h, l, c, vw, k0, e, sl0, how):
    """One LONG trade (shorts come in with prices negated). Entry at o[k0]. Stops set from closed 10-min candles apply
    from the next minute. Same-minute SL and target: SL first. Returns exit price, exit index, reason, MFE, MAE."""
    n = _first(m >= T_SQUARE)
    n = len(m) if n == 10**9 else n
    mm, oo, hh, ll, cc, vv = m[k0:n], o[k0:n], h[k0:n], l[k0:n], c[k0:n], vw[k0:n]
    blk = (mm - T_OPEN) // 10
    new = np.r_[True, blk[1:] != blk[:-1]]
    starts = np.flatnonzero(new)
    ends = np.r_[starts[1:], len(mm)] - 1
    stop_b = np.full(len(starts), sl0)
    if how == "trail_candle":
        stop_b[1:] = np.maximum(sl0, np.maximum.accumulate(np.minimum.reduceat(ll, starts))[:-1])
    elif how == "trail_vwap":
        stop_b[1:] = np.maximum(sl0, np.maximum.accumulate(vv[ends])[:-1])
    stop = stop_b[np.cumsum(new) - 1]
    r = {"rr2": 2, "rr3": 3}.get(how)
    tgt = e + r * (e - sl0) if r else np.inf
    k_sl, k_tg, k_vc = _first(ll <= stop), _first(hh >= tgt), 10**9
    if how == "vwap_close":
        j = _first(cc[ends] < vv[ends])
        if j < len(starts) - 1:
            k_vc = int(starts[j + 1])
    k = min(k_sl, k_tg, k_vc)
    if k == 10**9:
        if n < len(m):
            px, kk, why = float(o[n]), n, "15:15 square-off"
        else:
            px, kk, why = float(c[n - 1]), n - 1, "last candle close"
        seen = slice(k0, kk)
    elif k == k_vc:
        px, kk, why, seen = float(oo[k]), k0 + k, "VWAP ke neeche close", slice(k0, k0 + k)
    elif k == k_sl:
        px, kk = float(min(oo[k], stop[k])), k0 + k
        why, seen = ("initial SL" if stop[k] == sl0 else "trailing SL"), slice(k0, kk + 1)
    else:
        px, kk, why, seen = float(max(oo[k], tgt)), k0 + k, f"target 1:{r}", slice(k0, k0 + k + 1)
    hi = max(float(h[seen].max()) if seen.stop > seen.start else e, px)
    lo = min(float(l[seen].min()) if seen.stop > seen.start else e, px)
    return px, kk, why, (hi - e) / abs(e), (lo - e) / abs(e)


def _entry_b(m, h, l, c, vw, k_from):
    """First 10-min candle after the signal that dips to VWAP and closes above it, closing by 12:30.
    Returns (entry index, candle low, VWAP at its close) or None."""
    mm = m[k_from:]
    blk = (mm - T_OPEN) // 10
    new = np.r_[True, blk[1:] != blk[:-1]]
    starts = np.flatnonzero(new)
    ends = np.r_[starts[1:], len(mm)] - 1
    lows = np.minimum.reduceat(l[k_from:], starts)
    end_m = (blk[starts] + 1) * 10 + T_OPEN          # candle close time
    ok = (end_m <= T_B_LAST) & (lows <= vw[k_from:][ends]) & (c[k_from:][ends] > vw[k_from:][ends])
    j = _first(ok)
    if j == 10**9 or k_from + ends[j] + 1 >= len(m) or m[k_from + ends[j] + 1] >= T_SQUARE:
        return None
    return k_from + int(ends[j]) + 1, float(lows[j]), float(vw[k_from + ends[j]])


def _hhmm(t) -> str:
    return f"{int(t) // 60:02d}:{int(t) % 60:02d}"


def _simulate(day, sym, side, g, sig) -> tuple[dict, list[dict]]:
    """All entry / SL / exit combinations for one candidate. side +1 long, -1 short (prices negated)."""
    m = g.m.to_numpy()
    s = 1.0 if side > 0 else -1.0
    o, c = g.open.to_numpy(float) * s, g.close.to_numpy(float) * s
    h, l = (g.high, g.low) if side > 0 else (g.low, g.high)
    h, l = h.to_numpy(float) * s, l.to_numpy(float) * s
    v = g.volume.to_numpy(float)
    tp = (g.high.to_numpy(float) + g.low.to_numpy(float) + g.close.to_numpy(float)) / 3
    vw = np.cumsum(tp * v) / np.maximum(np.cumsum(v), 1e-9) * s
    k_a = _first(m >= T_ENTRY)
    cand = {"date": day, "symbol": sym, "side": "BUY" if side > 0 else "SELL", **sig}
    rows = []
    if k_a == 10**9 or m[k_a] >= T_SQUARE:
        return cand, rows
    e_a = float(o[k_a])
    n = _first(m >= T_SQUARE)
    n = len(m) if n == 10**9 else n
    cand["entry_a"] = e_a * s
    cand["day_best_pct"] = (float(h[k_a:n].max()) - e_a) / abs(e_a) * 100 if n > k_a else 0.0
    cand["close_pct"] = ((float(o[n]) if n < len(m) else float(c[-1])) - e_a) / abs(e_a) * 100
    sl_sig = float(l[(m >= T_OPEN) & (m < T_ENTRY)].min())     # signal candle low (high for shorts)
    plans = [("A", k_a, e_a, sl_sig)]
    b = _entry_b(m, h, l, c, vw, k_a)
    if b:
        kb, blow, bvw = b
        plans += [("B_low", kb, float(o[kb]), blow), ("B_vwap", kb, float(o[kb]), bvw * (1 - VWAP_BUF * s))]
    for etype, k0, e, sl0 in plans:
        if e <= sl0:                       # opened at or through the stop: no trade
            continue
        for how in EXITS:
            px, kk, why, mfe, mae = _exit(m, o, h, l, c, vw, k0, e, sl0, how)
            rows.append({"date": day, "symbol": sym, "side": cand["side"], "entry_type": etype, "exit_type": how,
                         "entry": e * s, "entry_time": _hhmm(m[k0]), "sl": sl0 * s, "exit": px * s,
                         "exit_time": _hhmm(m[min(kk, len(m) - 1)]), "exit_reason": why, "risk_pct": (e - sl0) / abs(e) * 100,
                         "mfe_pct": mfe * 100, "mae_pct": mae * 100, "hold_hours": (m[min(kk, len(m) - 1)] - m[k0]) / 60})
    return cand, rows


def read_day(day: str) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    """Daily H/L/C of every stock (+ the 9:25 -> day move of F&O stocks), loose candidates and their trade results."""
    df = fast.read_day(day, ("1m",))
    df = df[df.timeframe == "1m"].copy()
    codes, uniq = pd.factorize(df.datetime)                      # ~375 distinct times a day: parse each once
    df["m"] = np.array([int(u[11:13]) * 60 + int(u[14:16]) for u in uniq])[codes]
    df = df[(df.m >= T_OPEN) & (df.m < 15 * 60 + 30)].sort_values(["symbol", "m"])
    g = df.groupby("symbol", sort=False)
    daily = g.agg(open=("open", "first"), high=("high", "max"), low=("low", "min"), close=("close", "last"))
    fno = fno_symbols(day) & set(daily.index)
    daily["fno"] = daily.index.isin(fno)
    f = df[df.symbol.isin(fno)]
    sig = f[f.m < T_ENTRY].groupby("symbol").agg(m0=("m", "first"), o=("open", "first"), h=("high", "max"), l=("low", "min"),
                                                c=("close", "last"), v=("volume", "sum"))
    sig = sig[sig.m0 == T_OPEN]                                   # the 9:15 candle must exist
    after = f[f.m >= T_ENTRY].groupby("symbol").agg(e=("open", "first"), hi=("high", "max"), lo=("low", "min"))
    base = after.join(sig[["o"]], how="inner")
    daily["up_pct"] = (base.hi / base.e - 1) * 100                # baseline: any F&O stock, 9:25 open -> best high
    daily["down_pct"] = (1 - base.lo / base.e) * 100
    body = (sig.c - sig.o).abs() / sig.o
    longs = sig[(sig.c > sig.o) & ((sig.o - sig.l) / sig.o <= TOL_MAX) & ((sig.o - sig.l) < (sig.c - sig.o))]
    shorts = sig[(sig.c < sig.o) & ((sig.h - sig.o) / sig.o <= TOL_MAX) & ((sig.h - sig.o) < (sig.o - sig.c))]
    cands, trades = [], []
    bars = dict(tuple(f[f.symbol.isin(set(longs.index) | set(shorts.index))].groupby("symbol")))
    for side, q in ((1, longs), (-1, shorts)):
        for sym, r in q.iterrows():
            wick = (r.o - r.l) if side > 0 else (r.h - r.o)
            s = {"o": r.o, "h": r.h, "l": r.l, "c": r.c, "v": r.v, "tol_pct": wick / r.o * 100,
                 "body_pct": body[sym] * 100, "tail_body": wick / abs(r.c - r.o)}
            cand, rows = _simulate(day, sym, side, bars[sym], s)
            cands.append(cand)
            trades += rows
    # DataFrames, not lists of dicts: ~2 million trade rows over 5 years must fit in RAM
    return (daily[["open", "high", "low", "close", "fno", "up_pct", "down_pct"]].assign(date=day).reset_index(), pd.DataFrame(cands),
            pd.DataFrame(trades))


def prepare(start: str, end: str, on_day: Callable[[int, int, int], None] | None = None):
    """-> daily (all stocks), candidates, trades (all combinations, raw prices)."""
    days = trading_days(start, end)
    daily, cands, trades = [], [], []
    with ProcessPoolExecutor(WORKERS) as ex:
        jobs = {ex.submit(read_day, d): d for d in days}
        for i, fut in enumerate(as_completed(jobs), 1):
            d, c, t = fut.result()
            daily.append(d)
            cands.append(c)
            trades.append(t)
            if on_day:
                on_day(i, len(days), sum(map(len, cands)))
    daily = pd.concat(daily, ignore_index=True).sort_values(["symbol", "date"]).reset_index(drop=True)
    trades = pd.concat(trades, ignore_index=True)
    for k in ("side", "entry_type", "exit_type"):
        trades[k] = trades[k].astype("category")
    return daily, add_cpr(pd.concat(cands, ignore_index=True), daily), trades


def cpr_levels(daily: pd.DataFrame) -> pd.DataFrame:
    """Today's CPR from yesterday's H/L/C, and yesterday's CPR, per stock-day. top/bot = max/min of TC and BC."""
    d = daily.sort_values(["symbol", "date"]).copy()
    prev = d.groupby("symbol")[["high", "low", "close", "date"]].shift(1)
    p = (prev.high + prev.low + prev.close) / 3
    bc = (prev.high + prev.low) / 2
    tc = 2 * p - bc
    d["pv"], d["top"], d["bot"], d["prev_close"], d["prev_date"] = p, np.maximum(tc, bc), np.minimum(tc, bc), prev.close, prev.date
    y = d.groupby("symbol")[["pv", "top", "bot"]].shift(1)
    d["y_pv"], d["y_top"], d["y_bot"] = y.pv, y.top, y.bot
    return d[["symbol", "date", "pv", "top", "bot", "y_pv", "y_top", "y_bot", "prev_close", "prev_date"]]


def add_cpr(cands: pd.DataFrame, daily: pd.DataFrame) -> pd.DataFrame:
    return cands.merge(cpr_levels(daily), on=["symbol", "date"], how="left")


def passes(c: pd.DataFrame, tol: float, cpr: str, near: str, body: float | None) -> pd.Series:
    """Morning filter on the candidates. tol / body as fractions; cpr 'pivot' or 'gap'; near 'tc' or 'inside'."""
    up = c.side.eq("BUY")
    ok = c.tol_pct <= tol * 100 + 1e-9
    ok &= c.pv.notna() & c.y_pv.notna() & ((c.o / c.prev_close - 1).abs() < 0.2)   # 20%+ gap = data / split jump
    if cpr == "pivot":
        ok &= np.where(up, c.pv > c.y_pv, c.pv < c.y_pv)
    else:
        ok &= np.where(up, c.bot > c.y_top, c.top < c.y_bot)
    if near == "tc":
        ok &= np.where(up, (c.o / c.top - 1).abs() <= 0.005, (c.o / c.bot - 1).abs() <= 0.005)
    else:
        ok &= np.where(up, (c.o >= c.bot) & (c.o <= c.top * 1.0025), (c.o <= c.top) & (c.o >= c.bot * 0.9975))
    if body is not None:
        ok &= c.body_pct < body * 100
    return ok


def price(t: pd.DataFrame, slip: float) -> pd.DataFrame:
    t = t.copy()
    t["qty"] = np.floor(EXPOSURE / t.entry).astype(int)
    sgn = np.where(t.side.eq("BUY"), 1, -1)
    t["gross_pnl"] = t.qty * sgn * (t.exit * (1 - slip * sgn) - t.entry * (1 + slip * sgn))
    t["costs"] = COST_PCT * t.qty * t.entry
    t["net_pnl"] = t.gross_pnl - t.costs
    return t[t.qty > 0]


CAND_COLS = ["date", "symbol", "o", "l", "h", "c", "tol_pct", "body_pct", "tail_body", "pv", "top", "bot", "y_pv", "y_top", "y_bot"]


def pick(c_ok: pd.DataFrame, t: pd.DataFrame, slip: float, max_trades: int) -> pd.DataFrame:
    """Trades of the passing candidates (c_ok[CAND_COLS]): first come first (then smallest tail), max_trades a day."""
    t = t.merge(c_ok, on=["date", "symbol"])
    t = t.sort_values(["date", "entry_time", "tol_pct", "symbol"]).groupby("date").head(max_trades)
    return price(t, slip).reset_index(drop=True)


def select(cands: pd.DataFrame, trades: pd.DataFrame, slip: float, side="BUY", tol=0.0005, cpr="pivot", near="tc", body=0.01,
           entry="A", exit="hold", max_trades=MAX_TRADES) -> pd.DataFrame:
    """Trades for one setting."""
    c = cands[cands.side.eq(side)]
    t = trades[trades.side.eq(side) & trades.entry_type.eq(entry) & trades.exit_type.eq(exit)]
    return pick(c[passes(c, tol, cpr, near, body)][CAND_COLS], t, slip, max_trades)


def stats(t: pd.DataFrame) -> dict:
    if t.empty:
        return {"trades": 0, "win_pct": None, "net": 0.0, "pf": None, "exp": None, "max_dd": 0.0}
    t = t.sort_values(["date", "entry_time"])
    cum = t.net_pnl.cumsum()
    w, l = t.net_pnl[t.net_pnl > 0].sum(), -t.net_pnl[t.net_pnl <= 0].sum()
    return {"trades": len(t), "win_pct": (t.net_pnl > 0).mean() * 100, "net": t.net_pnl.sum(), "pf": w / l if l > 0 else None,
            "exp": t.net_pnl.mean(), "max_dd": float((cum - cum.cummax().clip(lower=0)).min())}


def split_stats(t: pd.DataFrame) -> dict:
    ins, oos = t[t.date < SPLIT_DAY], t[t.date >= SPLIT_DAY]
    out = {f"is_{k}": v for k, v in stats(ins).items()}
    out.update({f"oos_{k}": v for k, v in stats(oos).items()})
    out.update({f"all_{k}": v for k, v in stats(t).items()})
    return out


def nifty(path, tol=0.0005, body=0.01) -> pd.DataFrame:
    """Index version on Nifty 50 (no volume, so no VWAP): the main morning filter, entry at the 9:25 open,
    exit at the signal candle's low/high (SL) or the 15:15 open. Points only - expired weekly option prices are not in our data."""
    x = pd.read_csv(path)
    x["date"] = x.datetime.str[:10]
    x["m"] = x.datetime.str[11:13].astype(int) * 60 + x.datetime.str[14:16].astype(int)
    x = x[(x.m >= T_OPEN) & (x.m < 15 * 60 + 30)]
    d = x.groupby("date").agg(open=("open", "first"), high=("high", "max"), low=("low", "min"), close=("close", "last"))
    lv = cpr_levels(d.reset_index().assign(symbol="NIFTY")).set_index("date")
    s = x[x.m < T_ENTRY].groupby("date").agg(o=("open", "first"), h=("high", "max"), l=("low", "min"), c=("close", "last"))
    a = x[(x.m >= T_ENTRY) & (x.m < T_SQUARE)].groupby("date").agg(e=("open", "first"), hi=("high", "max"), lo=("low", "min"))
    sq = x[x.m == T_SQUARE].groupby("date").open.first()
    s = s.join(lv).join(a).join(sq.rename("x")).dropna()
    rows = []
    for side in (1, -1):
        up = side > 0
        wick = (s.o - s.l) if up else (s.h - s.o)
        ok = (wick / s.o <= tol + 1e-12) & ((s.c - s.o) * side > 0) & (wick < (s.c - s.o).abs()) & ((s.c - s.o).abs() / s.o < body)
        ok &= (s.pv > s.y_pv) if up else (s.pv < s.y_pv)
        ok &= ((s.o / s.top - 1).abs() <= 0.005) if up else ((s.o / s.bot - 1).abs() <= 0.005)
        for day, r in s[ok].iterrows():
            sl = r.l if up else r.h
            hit = (r.lo <= sl) if up else (r.hi >= sl)
            pts = (sl - r.e) * side if hit else (r.x - r.e) * side
            rows.append({"date": day, "side": "BUY" if up else "SELL", "open": r.o, "entry": r.e, "sl": sl,
                         "best_pts": (r.hi - r.e) if up else (r.e - r.lo), "close_pts": (r.x - r.e) * side,
                         "sl_hit": bool(hit), "pts": pts})
    return pd.DataFrame(rows).sort_values("date").reset_index(drop=True) if rows else pd.DataFrame()
