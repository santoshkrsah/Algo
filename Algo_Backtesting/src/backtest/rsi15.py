"""RSI(14) swing on 15-minute candles, NIFTY 50 stocks, long only, cash delivery (no margin).

    D = load(SYMBOLS)                       # 15m bars per stock (split / bonus / demerger adjusted) + RSI
    r = run(D, Params())                    # trades, skipped signals, daily mark-to-market equity
    s = stats(r, Params())                  # net, win %, profit factor, drawdown, CAGR ...

Rules (user, 18 Nov 2026):
    buy  = RSI crosses back UP through 30   (previous bar RSI < 30, this bar RSI >= 30)
    sell = RSI crosses back DOWN through 70 (previous bar RSI > 70, this bar RSI <= 70); no stop loss
    Rs 1 lakh per trade, max 10 positions, Rs 10 lakh capital, no compounding, no margin.

Timing (no look-ahead): a signal uses the close of a 15m bar; the order fills at the OPEN of that stock's next 15m bar
(the next morning's 09:15 open when the signal is on the 15:15 bar). Exits fill before entries at the same time stamp.
RSI = Wilder's RSI (RMA, alpha 1/14) on 15m closes, continuous across days (TradingView style), warm-up from Jan 2023.
"""
from dataclasses import dataclass, replace

import numpy as np
import pandas as pd

from backtest import fast
from backtest.config import DATA

START, END = "2023-06-01", "2026-06-30"        # test period (signals from START, everything closed on END)
WARMUP = "2023-01-01"
NIFTY_DAILY = DATA / "daily" / "index" / "NIFTY50.csv"

# NIFTY 50 constituents after the Sep 2025 rebalance (latest list known). Survivorship bias: today's list for the whole test.
SYMBOLS = ("ADANIENT ADANIPORTS APOLLOHOSP ASIANPAINT AXISBANK BAJAJ-AUTO BAJFINANCE BAJAJFINSV BEL BHARTIARTL CIPLA "
           "COALINDIA DRREDDY EICHERMOT ETERNAL GRASIM HCLTECH HDFCBANK HDFCLIFE HINDALCO HINDUNILVR ICICIBANK INDIGO INFY "
           "ITC JIOFIN JSWSTEEL KOTAKBANK LT M&M MARUTI MAXHEALTH NESTLEIND NTPC ONGC POWERGRID RELIANCE SBILIFE "
           "SHRIRAMFIN SBIN SUNPHARMA TCS TATACONSUM TMPV TATASTEEL TECHM TITAN TRENT ULTRACEMCO WIPRO").split()

# Demergers the data does not adjust (Fyers): prices before `ex` are multiplied by factor = 1 - value of the new share /
# previous close (NSE special pre-open price discovery). Holding through the ex-date then gives the same P&L as holding the
# old share plus the demerged one.
DEMERGERS = {
    "RELIANCE": ("2023-07-20", 1 - 261.85 / 2841.85),   # Jio Financial 1:1, discovered Rs 261.85 vs RIL close Rs 2,841.85
    "ITC": ("2025-01-06", 1 - 27.00 / 483.95),           # ITC Hotels 1:10, discovered Rs 270 -> Rs 27 per ITC share
}


@dataclass(frozen=True)
class Params:
    capital: float = 10_00_000
    per_trade: float = 1_00_000
    max_pos: int = 10
    rsi_n: int = 14
    buy_level: float = 30.0
    sell_level: float = 70.0
    rule: str = "cross"            # "cross" = cross back (user's choice) | "touch" = buy when RSI goes below 30, sell above 70
    slip: float = 0.0005           # per side, on the fill price
    costs: bool = True
    always_delivery: bool = False  # True = delivery charges even on same-day round trips (check)
    start: str = START
    end: str = END


def with_params(p: Params, **kw) -> Params:
    return replace(p, **kw)


# ------------------------------------------------------------------------------------------------------------ data
def rsi(close: np.ndarray, n: int = 14) -> np.ndarray:
    d = np.diff(close, prepend=np.nan)
    up = pd.Series(np.where(d > 0, d, 0.0)); dn = pd.Series(np.where(d < 0, -d, 0.0))
    up.iloc[0] = dn.iloc[0] = np.nan
    au = up.ewm(alpha=1 / n, adjust=False, min_periods=n).mean().to_numpy()
    ad = dn.ewm(alpha=1 / n, adjust=False, min_periods=n).mean().to_numpy()
    with np.errstate(divide="ignore", invalid="ignore"):
        r = np.where(ad == 0, np.where(au == 0, 50.0, 100.0), 100 - 100 / (1 + au / ad))
    r[np.isnan(au) | np.isnan(ad)] = np.nan
    return r


def load(symbols=SYMBOLS, start=WARMUP, end=END, n: int = 14) -> dict[str, pd.DataFrame]:
    """{symbol: DataFrame(datetime, date, open, high, low, close, volume, rsi)} of 15m bars."""
    out = {}
    for s in symbols:
        b = fast.load_equity(s, "15m", start, end).reset_index()
        if b.empty:
            continue
        b = b[b.datetime.dt.time <= pd.Timestamp("15:15").time()].reset_index(drop=True)   # no post-close bars
        if s in DEMERGERS:
            ex, f = DEMERGERS[s]
            m = b.datetime < pd.Timestamp(ex)
            b.loc[m, ["open", "high", "low", "close"]] *= f
        b["date"] = b.datetime.dt.strftime("%Y-%m-%d")
        b["rsi"] = rsi(b.close.to_numpy(dtype=float), n)
        out[s] = b[["datetime", "date", "open", "high", "low", "close", "volume", "rsi"]]
    return out


# ------------------------------------------------------------------------------------------------------------ costs
def charges(buy_val: float, sell_val: float, same_day: bool, p: Params) -> dict:
    """Angel One equity charges for one round trip (Rs). Delivery unless bought and sold the same day."""
    if not p.costs:
        return {k: 0.0 for k in ("brok", "stt", "exch", "sebi", "stamp", "gst", "dp")} | {"total": 0.0}
    intraday = same_day and not p.always_delivery
    if intraday:
        brok = min(20.0, 0.0003 * buy_val) + min(20.0, 0.0003 * sell_val)
        stt = 0.00025 * sell_val
        stamp = 0.00003 * buy_val
        dp = 0.0
    else:
        brok = min(20.0, 0.001 * buy_val) + min(20.0, 0.001 * sell_val)
        stt = 0.001 * (buy_val + sell_val)
        stamp = 0.00015 * buy_val
        dp = 20.0 * 1.18
    exch = 0.0000297 * (buy_val + sell_val)
    sebi = 0.000001 * (buy_val + sell_val)
    gst = 0.18 * (brok + exch + sebi)
    c = {"brok": brok, "stt": stt, "exch": exch, "sebi": sebi, "stamp": stamp, "gst": gst, "dp": dp}
    c["total"] = sum(c.values())
    return c


# ------------------------------------------------------------------------------------------------------------ signals
def signals(D: dict, p: Params) -> pd.DataFrame:
    """Every buy / sell signal: symbol, kind, signal bar index, fill bar index, fill time, dip (lowest RSI before the buy)."""
    rows = []
    for s, b in D.items():
        r = b.rsi.to_numpy(); prev = np.r_[np.nan, r[:-1]]
        if p.rule == "cross":
            buy = (prev < p.buy_level) & (r >= p.buy_level)
            sell = (prev > p.sell_level) & (r <= p.sell_level)
        else:
            buy = (prev >= p.buy_level) & (r < p.buy_level)
            sell = (prev <= p.sell_level) & (r > p.sell_level)
        # lowest RSI of the dip that ended at the buy bar (priority when slots are short)
        low = pd.Series(np.where(r < p.buy_level, r, np.nan))
        grp = (~(r < p.buy_level)).cumsum()
        dip = low.groupby(grp).cummin().to_numpy()
        dip_prev = np.r_[np.nan, dip[:-1]]
        n = len(b)
        for kind, m in (("buy", buy), ("sell", sell)):
            for i in np.flatnonzero(m):
                if i + 1 >= n:
                    continue
                rows.append((s, kind, i, i + 1, b.datetime.iat[i + 1], b.datetime.iat[i],
                             float(dip_prev[i]) if kind == "buy" and p.rule == "cross" else float(r[i])))
    sig = pd.DataFrame(rows, columns=["symbol", "kind", "i", "fi", "fill_time", "sig_time", "dip"])
    return sig.sort_values(["fill_time", "kind", "dip"], ascending=[True, False, True], kind="stable").reset_index(drop=True)
    # kind descending: "sell" before "buy" at the same time stamp (exits free cash / slots first)


# ------------------------------------------------------------------------------------------------------------ portfolio
def run(D: dict, p: Params) -> dict:
    t0, t1 = pd.Timestamp(p.start), pd.Timestamp(p.end) + pd.Timedelta(hours=23)
    sig = signals(D, p)
    sig = sig[(sig.sig_time >= t0) & (sig.fill_time <= t1)]
    cash, open_, trades, skipped = p.capital, {}, [], []
    for r in sig.itertuples(index=False):
        b = D[r.symbol]
        if r.kind == "sell":
            pos = open_.get(r.symbol)
            if pos is None or r.fi <= pos["fi"]:
                continue
            trades.append(_close(pos, b, r.fi, "RSI 70 cross down", p))
            cash += trades[-1]["sell_val"] - trades[-1]["sell_side_cost"]
            del open_[r.symbol]
            continue
        if r.symbol in open_:
            continue
        px = float(b.open.iat[r.fi]) * (1 + p.slip)
        qty = int(p.per_trade // px)
        need = qty * px * 1.002 + 30
        why = None
        if len(open_) >= p.max_pos:
            why = "10 position pehle se khule"
        elif qty < 1:
            why = "share Rs 1 lakh se mehnga"
        elif cash < need:
            why = "cash kam (margin nahi)"
        if why:
            skipped.append({"symbol": r.symbol, "time": str(r.fill_time), "why": why, "dip": r.dip})
            continue
        pos = {"symbol": r.symbol, "si": r.i, "fi": r.fi, "entry_time": r.fill_time, "sig_time": r.sig_time, "open_px": float(b.open.iat[r.fi]),
               "entry": px, "qty": qty, "dip": r.dip, "n_open": len(open_) + 1}
        cash -= qty * px + _buy_side_cost(qty * px, p)
        open_[r.symbol] = pos
    # period end: close what is still open at the last bar's close on/before END
    for s, pos in list(open_.items()):
        b = D[s]
        last = int(np.flatnonzero(b.datetime <= t1)[-1])
        trades.append(_close(pos, b, last, "Test khatam (open position band)", p, at_close=True))
    t = pd.DataFrame(trades).sort_values("entry_time").reset_index(drop=True)
    t["id"] = np.arange(1, len(t) + 1)
    eq = equity(D, t, p)
    return {"trades": t, "skipped": pd.DataFrame(skipped, columns=["symbol", "time", "why", "dip"]), "equity": eq, "signals": int((sig.kind == "buy").sum())}


def _buy_side_cost(val: float, p: Params) -> float:
    """Cash set aside at entry (delivery buy side); the exact round-trip charges are booked at exit."""
    if not p.costs:
        return 0.0
    brok = min(20.0, 0.001 * val)
    return brok + 0.001 * val + 0.00015 * val + 0.0000307 * val + 0.18 * (brok + 0.0000307 * val)


def _close(pos: dict, b: pd.DataFrame, xi: int, reason: str, p: Params, at_close: bool = False) -> dict:
    raw = float(b.close.iat[xi] if at_close else b.open.iat[xi])
    xpx = raw * (1 - p.slip)
    q, e = pos["qty"], pos["entry"]
    buy_val, sell_val = q * e, q * xpx
    et = pos["entry_time"]; xt = b.datetime.iat[xi]
    same = et.date() == xt.date()
    c = charges(buy_val, sell_val, same, p)
    seg = b.iloc[pos["fi"]: xi + 1]
    mae = float(seg.low.min() / e - 1) * 100       # worst point while holding (% from entry fill)
    mfe = float(seg.high.max() / e - 1) * 100
    gross = sell_val - buy_val
    slip = q * (pos["open_px"] * p.slip + raw * p.slip)
    return {"symbol": pos["symbol"], "sig_time": pos["sig_time"], "entry_time": et, "exit_time": xt, "exit_sig_time": b.datetime.iat[xi - 1] if not at_close else xt,
            "entry_raw": pos["open_px"], "entry": e, "exit_raw": raw, "exit": xpx, "qty": q, "buy_val": buy_val, "sell_val": sell_val,
            "gross": gross, "slip": slip, **{"c_" + k: v for k, v in c.items()}, "costs": c["total"], "net": gross - c["total"],
            "ret_pct": (gross - c["total"]) / buy_val * 100, "bars": xi - pos["fi"], "days": int(seg.date.nunique() - 1),
            "cal_days": (xt.normalize() - et.normalize()).days, "same_day": same, "reason": reason, "dip": pos["dip"],
            "rsi_entry": float(b.rsi.iat[pos["si"]]), "rsi_exit": float(b.rsi.iat[xi - 1] if not at_close else b.rsi.iat[xi]),
            "mae_pct": mae, "mfe_pct": mfe, "n_open": pos["n_open"], "si": pos["si"], "fi": pos["fi"], "xi": xi,
            "sell_side_cost": c["total"] - _buy_side_cost(buy_val, p)}


def equity(D: dict, t: pd.DataFrame, p: Params) -> pd.DataFrame:
    """Daily mark-to-market: capital + closed net + open positions at that day's close. Also money deployed and positions."""
    days = sorted(set().union(*[set(b.date[(b.datetime >= pd.Timestamp(p.start)) & (b.datetime <= pd.Timestamp(p.end) + pd.Timedelta(hours=23))]) for b in D.values()]))
    closes = pd.DataFrame({s: b.groupby("date").close.last() for s, b in D.items()}).reindex(days).ffill()
    idx = pd.Index(days)
    realised = pd.Series(0.0, index=idx); unreal = pd.Series(0.0, index=idx); dep = pd.Series(0.0, index=idx); npos = pd.Series(0, index=idx)
    for r in t.itertuples(index=False):
        ed, xd = r.entry_time.strftime("%Y-%m-%d"), r.exit_time.strftime("%Y-%m-%d")
        held = (idx >= ed) & (idx < xd)
        c = closes[r.symbol].to_numpy()
        unreal[held] += r.qty * c[held] - r.buy_val - (_buy_side_cost(r.buy_val, p))
        dep[held] += r.buy_val; npos[held] += 1
        realised[idx >= xd] += r.net
    eq = p.capital + realised + unreal
    return pd.DataFrame({"date": days, "equity": eq.to_numpy(), "deployed": dep.to_numpy(), "positions": npos.to_numpy()})


# ------------------------------------------------------------------------------------------------------------ stats
def stats(r: dict, p: Params, lo: str | None = None, hi: str | None = None) -> dict:
    t, e = r["trades"], r["equity"]
    if lo or hi:
        x = t.exit_time.dt.strftime("%Y-%m-%d")
        t = t[(x >= (lo or "")) & (x <= (hi or "9999"))]
        e = e[(e.date >= (lo or "")) & (e.date <= (hi or "9999"))]
    n = t.net
    w, l = n[n > 0], n[n <= 0]
    eqv = e.equity.to_numpy()
    peak = np.maximum.accumulate(np.maximum(eqv, p.capital)) if len(eqv) else eqv
    dd = eqv - peak
    years = max((pd.Timestamp(e.date.iloc[-1]) - pd.Timestamp(e.date.iloc[0])).days / 365.25, 1e-9) if len(e) else 1
    net = float(n.sum())
    return {"trades": int(len(t)), "net": net, "ret_pct": net / p.capital * 100,
            "cagr": ((p.capital + net) / p.capital) ** (1 / years) * 100 - 100 if len(e) else None,
            "win_pct": float((n > 0).mean() * 100) if len(t) else None, "pf": float(w.sum() / -l.sum()) if l.sum() < 0 else None,
            "avg": float(n.mean()) if len(t) else None, "avg_win": float(w.mean()) if len(w) else None, "avg_loss": float(l.mean()) if len(l) else None,
            "avg_ret_pct": float(t.ret_pct.mean()) if len(t) else None,
            "best": float(n.max()) if len(t) else 0.0, "worst": float(n.min()) if len(t) else 0.0,
            "max_dd": float(dd.min()) if len(dd) else 0.0, "max_dd_pct": float((dd / peak).min() * 100) if len(dd) else 0.0,
            "gross": float(t.gross.sum()), "slip": float(t.slip.sum()), "costs": float(t.costs.sum()),
            "avg_days": float(t.days.mean()) if len(t) else None, "med_days": float(t.days.median()) if len(t) else None,
            "max_days": int(t.days.max()) if len(t) else 0, "same_day": int(t.same_day.sum()),
            "avg_mae": float(t.mae_pct.mean()) if len(t) else None, "worst_mae": float(t.mae_pct.min()) if len(t) else None,
            "avg_deployed": float(e.deployed.mean()) if len(e) else 0.0, "peak_deployed": float(e.deployed.max()) if len(e) else 0.0,
            "max_positions": int(e.positions.max()) if len(e) else 0, "avg_positions": float(e.positions.mean()) if len(e) else 0.0,
            "in_mkt": float((e.positions > 0).mean() * 100) if len(e) else 0.0}


def nifty_bh(p: Params, dates: list[str]) -> pd.DataFrame:
    """Rs capital in the NIFTY 50 index at the first day's open, held to the end (no dividends)."""
    n = pd.read_csv(NIFTY_DAILY)
    n = n[(n.date >= dates[0]) & (n.date <= dates[-1])].set_index("date")
    first = float(n.open.iloc[0])
    return pd.DataFrame({"date": dates, "equity": (n.close.reindex(dates).ffill() / first * p.capital).to_numpy()})
