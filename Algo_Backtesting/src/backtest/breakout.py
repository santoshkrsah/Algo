"""Breakout + put selling (Mitesh Patel interview rulebook): volume-confirmed range breakouts in the top-50 liquid F&O stocks,
traded two ways - (A) stock long with stop / target / time stop, (B) put SELL at or below the support. Daily data only.

    D = load()                              # adjusted daily OHLCV of the F&O stocks (wide), raw/adjusted factor, top-50 universe, put chains
    sig = signals(D, p)                     # qualifying breakouts (monthly cap applied unless p.per_month is None)
    a = size_a(path_a(D, sig, p), p)        # type A trades in Rs
    b = size_b(path_b(D, sig, p), p)        # type B trades in Rs
    stats(a, p.capital, months)

[V] = stated in the interview summary, [A] = our assumption (see tools/run_breakout.py for the Hinglish rules).
"""
from bisect import bisect_right
from dataclasses import dataclass

import numpy as np
import pandas as pd

from backtest import fast
from backtest.config import FNO
from backtest.gaprsi import _costs as stock_costs
from backtest.straddle import fill_cost

START, END = "2021-10-01", "2026-10-30"
OPT_START = "2024-11-01"                       # first day of the stock option bhavcopy
OPT_COLS = {"TradDt": "date", "TckrSymb": "symbol", "XpryDt": "expiry", "StrkPric": "strike", "OptnTp": "type", "ClsPric": "close",
            "UndrlygPric": "und", "TtlTradgVol": "vol", "NewBrdLotQty": "lot"}


@dataclass(frozen=True)
class Params:
    capital: float = 1_000_000.0
    risk: float = 0.01                     # [V] 0.5-1% of capital per trade
    top_n: int = 50                        # [V] top ~50 liquid F&O stocks (re-ranked every month on 60-day turnover)
    # setup
    base_n: int = 20                       # [A] base length 20 / 40 / 60 days (40 gave 0.4 signals a month vs the stated 3-4, so 20)
    max_height: float = 0.12               # [A] range height (resistance - range low) / resistance: 8 / 12 / 15 %
    max_body: float | None = 0.015         # [A] "small candles": average |close - open| / close of the base
    vol_mult: float = 2.0                  # [A] breakout volume / 20-day average: 1.5 / 2 / 3
    vol_n: int = 20
    accum: bool = True                     # [A] average volume of the base's second half > first half
    candle: bool = True                    # [A] breakout candle closes in its top 25%, body >= 1.5x base average, day before not a big red candle
    close_top: float = 0.75
    body_mult: float = 1.5
    red_mult: float = 2.0
    morning_star: bool = False             # [V] optional filter: a Morning Star near the range low in the last 10 days of the base
    per_month: int | None = 4              # [V] 3-4 entries a month, strongest volume first
    # type A: stock long
    entry: str = "next_open"               # [A] "next_open" or "close" (breakout day's close)
    stop: str = "res"                      # [A] "res" = just below the resistance, "low" = below the breakout candle's low
    stop_buf: float = 0.005
    exit: str = "target"                   # [A] "target" (rr or tgt_pct) / "trail5" / "trail10" (n-day low trailing stop)
    rr: float | None = 2.0
    tgt_pct: float | None = None
    max_days: int = 10                     # [A] time stop (trading days in the trade)
    addon: bool = False                    # [V] add 50% when price makes a new high after entry, stop to entry price
    max_val: float = 0.5                   # [A] one position at most 50% of capital
    slip: float = 0.0005                   # [A] stock slippage per fill
    # type B: put sell
    dte: int = 7                           # [A] trading days to expiry at entry: 5 / 7 / 10
    wait: int = 15                         # [A] trading days a breakout stays valid while waiting for that expiry window
    strike: str = "support"                # [V/A] "support" = highest strike <= support, "below" = one strike lower, "otm5" = <= 95% of spot
    b_stop: str = "support"                # [A] "support" = stock closes below support, "prem2" = premium 2x, "loss15" = loss 1.5x premium
    take: float | None = None              # [A] close early once this share of the premium is earned (0.5 / 0.7); None = till expiry day
    min_prem: float = 1.0                  # [A] Rs, smaller premiums are skipped
    opt_slip: float = 0.02                 # [A] option slippage, share of the premium per fill
    margin_pct: float = 0.20               # [A] short put margin ~ 20% of strike x quantity


def with_params(p: Params, **kw) -> Params:
    from dataclasses import replace
    return replace(p, **kw)


# ---------------------------------------------------------------- data
def fno_months() -> dict[str, set]:
    """{YYYY-MM: stocks with futures on the month's first bhavcopy day} (point-in-time F&O list, Nov 2024 onward)."""
    out = {}
    for f in sorted((FNO / "bhavcopy").glob("*.csv")):
        m = f.stem[:7]
        if m not in out:
            x = pd.read_csv(f, usecols=["FinInstrmTp", "TckrSymb"])
            out[m] = set(x.loc[x.FinInstrmTp == "STF", "TckrSymb"])
    return out


def universe(W: dict, fm: dict, top_n: int, n: int = 60) -> pd.DataFrame:
    """True where the stock is in the month's top-n by average turnover of the previous n days. Months before the
    first F&O bhavcopy use every stock that was ever in F&O (survivorship bias, said in the report)."""
    tv = (W["close"] * W["volume"]).rolling(n, min_periods=n * 2 // 3).mean().shift(1)
    month = np.array([d[:7] for d in tv.index])
    starts = np.flatnonzero(np.r_[True, month[1:] != month[:-1]])
    allf, first = set().union(*fm.values()), min(fm)
    uni = np.zeros(tv.shape, dtype=bool)
    for a, b in zip(starts, np.r_[starts[1:], len(month)]):
        members = fm.get(month[a], allf if month[a] < first else fm[max(fm)])
        row = tv.iloc[a]
        row = row[row.index.isin(members)].dropna().sort_values(ascending=False)
        uni[a:b, tv.columns.get_indexer(row.index[:top_n])] = True
    return pd.DataFrame(uni, index=tv.index, columns=tv.columns)


class Chains:
    """Put chains from the daily NSE option bhavcopy (expired contracts too, only rows that traded)."""

    def __init__(self, symbols):
        parts = []
        for f in sorted((FNO / "bhavcopy_opt").glob("*.csv")):
            x = pd.read_csv(f, usecols=list(OPT_COLS)).rename(columns=OPT_COLS)
            parts.append(x[(x.type == "PE") & x.symbol.isin(symbols) & (x.vol > 0)].drop(columns="type"))
        d = pd.concat(parts, ignore_index=True)
        self.expiries = sorted(d.expiry.unique())
        self.p = d.set_index(["symbol", "expiry"]).sort_index()
        self.cache = {}

    def get(self, sym: str, exp: str):
        """(closes: date x strike, und: date -> spot, lot: date -> lot size) or None."""
        k = (sym, exp)
        if k not in self.cache:
            try:
                x = self.p.loc[k]
            except KeyError:
                self.cache[k] = None
                return None
            self.cache[k] = (x.pivot_table(index="date", columns="strike", values="close", aggfunc="last"), x.groupby("date").und.last(),
                             x.groupby("date").lot.last())
        return self.cache[k]

    def next_expiry(self, day: str):
        i = bisect_right(self.expiries, day)
        return self.expiries[i] if i < len(self.expiries) else None


def load(start: str = START, end: str = END, top_n: int = 50, options: bool = True) -> dict:
    fm = fno_months()
    allf = set().union(*fm.values())
    p = fast.panel(start, end, columns=["open", "high", "low", "close", "volume"])
    p = p[p.symbol.isin(allf)].assign(date=lambda x: x.date.astype(str))
    raw = fast.panel(start, end, columns=["close"], adjust=False)
    raw = raw[raw.symbol.isin(allf)].assign(date=lambda x: x.date.astype(str))
    W = {k: p.pivot(index="date", columns="symbol", values=k).astype(float) for k in ("open", "high", "low", "close", "volume")}
    rawc = raw.pivot(index="date", columns="symbol", values="close").reindex(index=W["close"].index, columns=W["close"].columns)
    W["factor"] = (rawc / W["close"]).ffill().fillna(1.0)          # raw price = adjusted x factor
    W["uni"] = universe(W, fm, top_n)
    syms = list(W["close"].columns[W["uni"].any().to_numpy()])
    return {"W": W, "fno": fm, "chains": Chains(syms) if options else None}


# ---------------------------------------------------------------- setup
def morning_star(O, H, L, C, avgb, lo, look: int = 10) -> pd.DataFrame:
    """Morning Star (big red, small body, green closing above the red body's middle) with its low within 3% of the range low,
    ending in the last <look> days before the signal day."""
    b = (C - O) / C
    ms = ((-b.shift(2) >= avgb) & (b.shift(1).abs() <= 0.5 * avgb) & (b > 0) & (C > (O.shift(2) + C.shift(2)) / 2)
          & (pd.concat([L, L.shift(1), L.shift(2)]).groupby(level=0).min().reindex(L.index) <= lo * 1.03))
    return ms.astype(float).rolling(look, min_periods=1).max().shift(1) > 0


def signals(D: dict, p: Params, cap: bool = True) -> pd.DataFrame:
    """Every breakout day that passes the setup filters (only data up to that day's close). A stock's breakout is ignored when
    the same stock had one in the previous base_n days (no new base yet). With cap: at most p.per_month a month, strongest
    volume first on a day, first come first served in the month."""
    W = D["W"]
    O, H, L, C, V = (W[k] for k in ("open", "high", "low", "close", "volume"))
    n = p.base_n
    res = H.rolling(n).max().shift(1)
    lo = L.rolling(n).min().shift(1)
    avgb = ((C - O).abs() / C).rolling(n).mean().shift(1)
    vm = V / V.rolling(p.vol_n).mean().shift(1)
    h = n // 2
    height = (res - lo) / res
    ok = W["uni"] & (C > res) & (height <= p.max_height) & (vm >= p.vol_mult)
    if p.max_body is not None:
        ok &= avgb <= p.max_body
    if p.accum:
        ok &= V.rolling(h).mean().shift(1) > V.rolling(n - h).mean().shift(h + 1)
    if p.candle:
        ok &= (C >= L + p.close_top * (H - L)) & ((C - O) / C >= p.body_mult * avgb) & ((O.shift(1) - C.shift(1)) / C.shift(1) <= p.red_mult * avgb)
    if p.morning_star:
        ok &= morning_star(O, H, L, C, avgb, lo)
    ii, jj = np.nonzero(ok.to_numpy(dtype=bool))
    dates, syms = W["close"].index.to_numpy(), W["close"].columns.to_numpy()
    s = pd.DataFrame({"i": ii, "j": jj, "date": dates[ii], "symbol": syms[jj], "res": res.to_numpy()[ii, jj], "lo": lo.to_numpy()[ii, jj],
                      "height": height.to_numpy()[ii, jj], "vm": vm.to_numpy()[ii, jj], "close": C.to_numpy()[ii, jj], "low": L.to_numpy()[ii, jj],
                      "high": H.to_numpy()[ii, jj]})
    s = s.sort_values(["i", "vm"], ascending=[True, False]).reset_index(drop=True)
    keep, last, per_m = [], {}, {}
    for r in s.itertuples():
        prev = last.get(r.symbol)
        last[r.symbol] = r.i
        if prev is not None and r.i - prev < n:
            continue
        m = r.date[:7]
        if cap and p.per_month is not None and per_m.get(m, 0) >= p.per_month:
            continue
        per_m[m] = per_m.get(m, 0) + 1
        keep.append(r.Index)
    return s.loc[keep].reset_index(drop=True)


# ---------------------------------------------------------------- type A: stock long
def path_a(D: dict, sig: pd.DataFrame, p: Params) -> pd.DataFrame:
    """Per-share path of every signal: entry, stop, target, exit (stop first when stop and target fall in the same candle;
    a gap through either fills at the open). Rows with `skip` set were not tradable."""
    W = D["W"]
    O, H, L, C = (W[k].to_numpy() for k in ("open", "high", "low", "close"))
    dates = W["close"].index.to_numpy()
    nd = len(dates)
    trail_n = {"trail5": 5, "trail10": 10}.get(p.exit)
    rows = []
    for r in sig.itertuples():
        s, j = r.i, r.j
        base = {"signal_date": r.date, "symbol": r.symbol, "res": r.res, "lo": r.lo, "height": r.height, "vm": r.vm}
        if p.entry == "next_open":
            e = s + 1
            if e >= nd or np.isnan(O[e, j]):
                rows.append({**base, "skip": "agla din data nahi"})
                continue
            entry, first = O[e, j], e
        else:
            e, entry, first = s, C[s, j], s + 1
        stop = (r.res if p.stop == "res" else L[s, j]) * (1 - p.stop_buf)
        if entry <= stop:
            rows.append({**base, "skip": "open stop ke neeche (gap)"})
            continue
        tgt = entry + p.rr * (entry - stop) if p.exit == "target" and p.rr else (entry * (1 + p.tgt_pct) if p.exit == "target" and p.tgt_pct else None)
        last = min(e + p.max_days - (1 if p.entry == "next_open" else 0), nd - 1)
        cur, why_stop, x, add, pend = stop, "SL", None, None, False
        for k in range(first, last + 1):
            o, h, l, c = O[k, j], H[k, j], L[k, j], C[k, j]
            if np.isnan(c):
                continue
            if pend:
                pend = False
                if o > entry and o > cur:
                    add = (k, o)
                    if cur < entry:
                        cur, why_stop = entry, "breakeven SL (add ke baad)"
            if not (p.entry == "next_open" and k == e):
                if o <= cur:
                    x = (k, o, why_stop + " (gap)")
                    break
                if tgt is not None and o >= tgt:
                    x = (k, o, "target (gap)")
                    break
            if l <= cur:
                x = (k, cur, why_stop)
                break
            if tgt is not None and h >= tgt:
                x = (k, tgt, "target")
                break
            if k == last:
                x = (k, c, f"{p.max_days} din time stop")
                break
            if trail_n:
                t = np.nanmin(L[max(0, k - trail_n + 1):k + 1, j])
                if t > cur:
                    cur, why_stop = t, f"trailing SL ({trail_n} din ka low)"
            if p.addon and add is None and not pend and c > np.nanmax(H[s:k, j]):
                pend = True
        if x is None:
            k = max(kk for kk in range(first - 1, last + 1) if not np.isnan(C[kk, j]))
            x = (k, C[k, j], "data khatam")
        rows.append({**base, "skip": None, "i": s, "e": e, "entry_date": dates[e], "entry": entry, "stop": stop, "target": tgt, "exit_date": dates[x[0]],
                     "exit": x[1], "reason": x[2], "hold": int(x[0] - e + (1 if p.entry == "next_open" else 0)),
                     "add_date": dates[add[0]] if add else None, "add_px": add[1] if add else None})
    return pd.DataFrame(rows)


def size_a(t: pd.DataFrame, p: Params) -> pd.DataFrame:
    """Rs sizes in entry order: qty = capital x risk / (entry fill - stop), at most max_val of capital in one stock, only
    with free cash (no leverage; add-on cash kept aside). Returns the traded rows."""
    if t.empty or "entry_date" not in t:
        return pd.DataFrame()
    t = t[t.skip.isna()].sort_values(["entry_date", "vm"], ascending=[True, False]).reset_index(drop=True)
    t["add_px"] = pd.to_numeric(t.add_px)
    bfill, xfill = t.entry * (1 + p.slip), t.exit * (1 - p.slip)
    afill = t.add_px * (1 + p.slip)
    mult = 1.5 if p.addon else 1.0
    open_pos, out = [], []
    for i, r in enumerate(t.itertuples()):
        open_pos = [o for o in open_pos if o[0] >= r.entry_date]
        if any(o[1] == r.symbol for o in open_pos):
            continue
        used = sum(o[2] for o in open_pos)
        rps = bfill[i] - r.stop
        want = min(p.capital * p.risk / rps, p.capital * p.max_val / bfill[i])
        q = min(want, (p.capital - used) / (bfill[i] * mult))
        qty = int(q) if q >= 1 and q >= 0.5 * want else 0
        if not qty:
            continue
        aq = int(qty * 0.5) if r.add_px == r.add_px else 0
        bv, sv = qty * bfill[i] + (aq * afill[i] if aq else 0.0), (qty + aq) * xfill[i]
        gross = sv - bv
        cost = stock_costs("long", bv, sv)
        slip = qty * (bfill[i] - r.entry) + (qty + aq) * (r.exit - xfill[i]) + (aq * (afill[i] - r.add_px) if aq else 0.0)
        open_pos.append((r.exit_date, r.symbol, qty * bfill[i] * mult))
        out.append({**r._asdict(), "qty": qty, "add_qty": aq, "entry_fill": bfill[i], "exit_fill": xfill[i], "notional": bv, "risk_rs": qty * rps,
                    "gross": gross, "slip": slip, "costs": cost, "net": gross - cost, "R": (gross - cost) / (qty * rps)})
    return pd.DataFrame(out).drop(columns="Index")


# ---------------------------------------------------------------- type B: put sell
def path_b(D: dict, sig: pd.DataFrame, p: Params) -> pd.DataFrame:
    """Per-share path of the put sale for every signal. The put is sold at the close of the first day (within p.wait days of
    the breakout, stock still closing at/above the support) when the nearest expiry is 2..p.dte trading days away."""
    W, ch = D["W"], D["chains"]
    C, F = W["close"].to_numpy(), W["factor"].to_numpy()
    dates = W["close"].index.to_numpy()
    pos = {d: i for i, d in enumerate(dates)}
    rows = []
    for r in sig.itertuples():
        s, j = r.i, r.j
        base = {"signal_date": r.date, "symbol": r.symbol, "res": r.res, "lo": r.lo, "height": r.height, "vm": r.vm}
        if r.date < OPT_START:
            continue
        ent = None
        for e in range(s, min(s + p.wait, len(dates) - 1) + 1):
            if not C[e, j] >= r.res:
                break
            X = ch.next_expiry(dates[e])
            if X is None or X not in pos:
                break
            if 2 <= pos[X] - e <= p.dte:
                ent = (e, X)
                break
        if ent is None:
            rows.append({**base, "skip": "expiry window se pehle setup toota / data khatam"})
            continue
        e, X = ent
        f = F[e, j]
        sup, spot_e = r.res * f, C[e, j] * f
        chain = ch.get(r.symbol, X)
        if chain is None or dates[e] not in chain[0].index:
            rows.append({**base, "skip": "us din put me trade nahi"})
            continue
        closes, und, lots = chain
        row = closes.loc[dates[e]].dropna()
        if p.strike == "otm5":
            cand = row[row.index <= 0.95 * spot_e]
        else:
            cand = row[row.index <= sup]
            if p.strike == "below":
                cand = cand.iloc[:-1]
        if cand.empty:
            rows.append({**base, "skip": "support ke neeche koi strike trade nahi hua"})
            continue
        K, prem = float(cand.index[-1]), float(cand.iloc[-1])
        if prem < p.min_prem:
            rows.append({**base, "skip": f"premium Rs {p.min_prem:g} se kam"})
            continue
        x, lastpx = None, prem
        for k in range(e + 1, pos[X] + 1):
            if np.isnan(C[k, j]):
                continue
            d, spot = dates[k], C[k, j] * f
            px = closes.at[d, K] if d in closes.index and K in closes.columns and closes.at[d, K] == closes.at[d, K] else None
            val = px if px is not None else max(K - spot, lastpx if spot < sup else 0.0, 0.0)
            if p.b_stop == "support" and spot < sup:
                x = (k, val, "SL: stock support ke neeche close")
            elif p.b_stop == "prem2" and val >= 2 * prem:
                x = (k, val, "SL: premium 2x")
            elif p.b_stop == "loss15" and val >= 2.5 * prem:
                x = (k, val, "SL: loss 1.5x premium")
            elif p.take and px is not None and px <= prem * (1 - p.take):
                x = (k, px, f"target: {p.take * 100:.0f}% premium kamaya")
            elif k == pos[X]:
                x = (k, px if px is not None else max(K - spot, 0.0), "expiry din" if px is not None or spot < K else "expiry (bekaar, OTM)")
            if x:
                break
            if px is not None:
                lastpx = px
        if x is None:
            rows.append({**base, "skip": "data khatam"})
            continue
        rows.append({**base, "skip": None, "i": s, "e": e, "entry_date": dates[e], "expiry": X, "dte": int(pos[X] - e), "strike": K, "prem": prem,
                     "lot": int(lots.get(dates[e], lots.iloc[-1])), "support": sup, "spot": spot_e, "factor": f, "exit_date": dates[x[0]], "exit": float(x[1]),
                     "reason": x[2], "hold": int(x[0] - e)})
    return pd.DataFrame(rows)


def size_b(t: pd.DataFrame, p: Params) -> pd.DataFrame:
    """Lots = capital x risk / (premium x lot)  (risk per lot taken as the premium again, i.e. the put doubling), margin
    (p.margin_pct x strike x qty) of open puts never above the capital. Returns the traded rows."""
    if t.empty or "e" not in t:
        return pd.DataFrame()
    t = t[t.skip.isna()].sort_values(["entry_date", "vm"], ascending=[True, False]).reset_index(drop=True)
    open_pos, out = [], []
    for r in t.itertuples():
        open_pos = [o for o in open_pos if o[0] >= r.entry_date]
        if any(o[1] == r.symbol for o in open_pos):
            continue
        sell = r.prem * (1 - p.opt_slip)
        risk_lot = r.prem * r.lot                  # sized on the quoted premium, so slippage does not change the lots
        lots = int(p.capital * p.risk // risk_lot)
        m_lot = p.margin_pct * r.strike * r.lot
        lots = min(lots, int((p.capital - sum(o[2] for o in open_pos)) // m_lot))
        if lots < 1:
            continue
        qty = lots * r.lot
        buy = r.exit * (1 + p.opt_slip)
        gross = qty * (sell - buy)
        c_in = fill_cost(sell * qty, False, False, r.entry_date)
        c_out = fill_cost(buy * qty, True, False, r.exit_date) if r.exit > 0 else {k: 0.0 for k in c_in}
        cost = c_in["total"] + c_out["total"]
        margin = m_lot * lots
        open_pos.append((r.exit_date, r.symbol, margin))
        net = gross - cost
        out.append({**r._asdict(), "lots": lots, "qty": qty, "sell_fill": sell, "buy_fill": buy if r.exit > 0 else 0.0, "premium_rs": sell * qty, "margin": margin,
                    "notional": r.strike * qty, "risk_rs": risk_lot * lots, "gross": gross, "slip": qty * (r.prem - sell) + (qty * (buy - r.exit) if r.exit > 0 else 0.0),
                    "costs": cost, "net": net, "R": net / (risk_lot * lots), "ret_prem": net / (sell * qty) * 100, "ret_margin": net / margin * 100,
                    "ret_notional": net / (r.strike * qty) * 100})
    return pd.DataFrame(out).drop(columns="Index") if out else pd.DataFrame()


# ---------------------------------------------------------------- numbers
def stats(t: pd.DataFrame, capital: float, months: float) -> dict:
    if t is None or t.empty:
        return {"trades": 0, "net": 0.0, "win_pct": None, "profit_factor": None, "max_dd": 0.0, "avg_r": None, "worst": 0.0, "best": 0.0, "avg_win": None,
                "avg_loss": None, "ret_pct": 0.0, "month_ret": 0.0, "per_month": 0.0, "gross": 0.0, "costs": 0.0}
    n = t.net
    w, l = n[n > 0], n[n <= 0]
    cum = t.groupby("exit_date").net.sum().sort_index().cumsum()
    dd = float((cum - cum.cummax().clip(lower=0)).min())
    return {"trades": int(len(t)), "net": float(n.sum()), "win_pct": float((n > 0).mean() * 100), "profit_factor": float(w.sum() / -l.sum()) if l.sum() < 0 else None,
            "max_dd": dd, "avg_r": float(t.R.mean()), "worst": float(n.min()), "best": float(n.max()), "avg_win": float(w.mean()) if len(w) else None,
            "avg_loss": float(l.mean()) if len(l) else None, "ret_pct": float(n.sum() / capital * 100), "month_ret": float(n.sum() / capital / months * 100),
            "per_month": len(t) / months, "gross": float(t.gross.sum()), "costs": float(t.costs.sum())}


def study(D: dict, p: Params) -> pd.DataFrame:
    """Every qualifying breakout (no monthly cap, volume >= 1.5x, other filters as p): did it hold above the resistance for
    5 days, return after 10 days, and the per-share R of the type A trade. For the '~80% success' claim."""
    from dataclasses import replace
    q = replace(p, vol_mult=1.5)
    s = signals(D, q, cap=False)
    C = D["W"]["close"].to_numpy()
    nd = C.shape[0]
    s["held5"] = [bool(np.all(C[i + 1:i + 6, j][~np.isnan(C[i + 1:i + 6, j])] >= r)) if i + 5 < nd else None for i, j, r in zip(s.i, s.j, s.res)]
    s["fwd10"] = [(C[i + 10, j] / C[i, j] - 1) * 100 if i + 10 < nd else np.nan for i, j in zip(s.i, s.j)]
    a = path_a(D, s, q)
    a = a[a.skip.isna()]
    a["r_ps"] = ((a.exit * (1 - q.slip)) - a.entry * (1 + q.slip)) / (a.entry * (1 + q.slip) - a.stop)
    return s.merge(a[["signal_date", "symbol", "r_ps", "reason"]].rename(columns={"signal_date": "date"}), on=["date", "symbol"], how="left")
