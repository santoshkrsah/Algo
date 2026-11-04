"""RSI(14) swing on 2-hour candles, long only (NIFTY 50 and RELIANCE), Jan 2021 - Oct 2026.

    D = load("NIFTY50")                     # 2-hour bars + RSI / ATR / EMA200 (warm-up from Jul 2020)
    r = run(D, Params(version="B"))         # trades, mark-to-market equity per bar, skipped signals
    s = stats(D, r, lo, hi, capital)        # return, CAGR, drawdown, Sharpe, win rate, PF, expectancy ... for bars lo..hi
    b = buy_hold(D, Params())               # same capital bought on the first bar, held to the end (dividends in cash)

Data: Fyers 1-minute candles (Algo_Historical_Data/data/index/<NAME>_FROM2020_1m.csv, split / bonus adjusted by Fyers).
2-hour bars: 09:15-11:15, 11:15-13:15, 13:15-15:30 (the last bar is 135 min, so there is no 15-minute stub bar);
build="tv" gives TradingView's split instead (09:15, 11:15, 13:15, 15:15 stub). Special sessions (Muhurat, Saturday drills,
the 24 Feb 2021 extended day) fall into the same clock slots.
Cleaning: 1-min high / low widened to cover open / close, then clipped to the official daily range (Fyers daily file);
demergers (Fyers leaves them unadjusted) are adjusted backwards by first open on the ex-date / previous official close.
Dividends (NSE corporate actions) are paid in cash to an open position on the ex-date, per adjusted share.
"""
import math
import re
from dataclasses import dataclass, replace

import numpy as np
import pandas as pd

from backtest.config import DATA

START, END = "2021-01-01", "2026-10-30"
IS_SHARE = 0.70
INSTR = {
    "NIFTY50": {"label": "NIFTY 50", "file": "NIFTY50_FROM2020_1m.csv", "daily": "daily/index/NIFTY50.csv", "mult": 0.01, "corp": None},
    "RELIANCE": {"label": "RELIANCE", "file": "RELIANCE_EQ_FROM2020_1m.csv", "daily": "daily/equity/RELIANCE.csv", "mult": 1.0, "corp": "RELIANCE"},
}
CORP_FILES = ("reference/corporate_actions_2020-07_2021-10.csv", "reference/corporate_actions.csv")
EXIT_RSI, EXIT_SL, EXIT_GAP, EXIT_END = "RSI 70 exit", "SL hit", "SL gap (open pe)", "Period khatam"
SKIP_POS, SKIP_DAY, SKIP_WEEK, SKIP_STREAK, SKIP_QTY = "Position pehle se khula", "Din ka 3% loss", "Hafte ka 6% loss", "3 loss ke baad skip", "Qty 0"


@dataclass(frozen=True)
class Params:
    capital: float = 1_000_000.0
    version: str = "A"                 # A = base, B = buy only when close > EMA200
    buy_lvl: float = 30.0
    sell_lvl: float = 70.0
    stop_atr: float = 2.0
    use_stop: bool = True
    risk: float = 0.01                 # share of current capital risked per trade
    max_open_risk: float = 0.05
    day_loss: float = 0.03
    week_loss: float = 0.06
    streak_n: int = 3
    risk_rules: bool = True            # day / week loss stop and the skip after 3 losses
    slip: float = 0.0005               # per fill, share of price
    brokerage: float = 20.0            # Rs per order
    costs: bool = True


def with_params(p: Params, **kw) -> Params:
    return replace(p, **kw)


# ------------------------------------------------------------------------------------------------------------ data
def corp_actions(symbol: str) -> pd.DataFrame:
    parts = [pd.read_csv(DATA / f, dtype=str) for f in CORP_FILES if (DATA / f).exists()]
    a = pd.concat(parts, ignore_index=True)
    a = a[(a.symbol == symbol) & (a.series == "EQ")].copy()
    a["purpose"] = a.purpose.str.strip()
    return a.drop_duplicates(["ex_date", "purpose"]).sort_values("ex_date").reset_index(drop=True)


def _share_factor(purpose: str) -> float:
    """Per-share factor of a bonus or split (Fyers prices already carry it): 'Bonus 1:1' -> 0.5."""
    m = re.search(r"Bonus\s+(\d+)\s*:\s*(\d+)", purpose)
    if m:
        a, b = int(m.group(1)), int(m.group(2))
        return b / (a + b)
    m = re.search(r"Split.*?Rs\.?\s*([\d.]+).*?Rs\.?\s*([\d.]+)", purpose)
    if m and "Split" in purpose:
        return float(m.group(2)) / float(m.group(1))
    return 1.0


def rma(x: np.ndarray, n: int) -> np.ndarray:
    """Wilder smoothing, seeded with the simple average of the first n values (TradingView ta.rma)."""
    out = np.full(len(x), np.nan)
    if len(x) < n:
        return out
    out[n - 1] = x[:n].mean()
    for i in range(n, len(x)):
        out[i] = out[i - 1] + (x[i] - out[i - 1]) / n
    return out


def rsi_wilder(c: np.ndarray, n: int = 14) -> np.ndarray:
    d = np.diff(c)
    up, dn = rma(np.clip(d, 0, None), n), rma(np.clip(-d, 0, None), n)
    with np.errstate(divide="ignore", invalid="ignore"):
        r = np.where(dn == 0, 100.0, np.where(up == 0, 0.0, 100 - 100 / (1 + up / dn)))
    r[np.isnan(up)] = np.nan
    return np.r_[np.nan, r]


def atr_wilder(h: np.ndarray, l: np.ndarray, c: np.ndarray, n: int = 14) -> np.ndarray:
    pc = np.r_[np.nan, c[:-1]]
    tr = np.nanmax(np.c_[h - l, np.abs(h - pc), np.abs(l - pc)], axis=1)
    return rma(tr, n)


def ema(x: np.ndarray, n: int) -> np.ndarray:
    out = np.full(len(x), np.nan)
    if len(x) < n:
        return out
    out[n - 1], k = x[:n].mean(), 2 / (n + 1)
    for i in range(n, len(x)):
        out[i] = out[i - 1] + k * (x[i] - out[i - 1])
    return out


def load(sym: str, build: str = "3bar", start: str = START, end: str = END) -> dict:
    meta = INSTR[sym]
    m = pd.read_csv(DATA / "index" / meta["file"], parse_dates=["datetime"])
    m = m[m.datetime < pd.Timestamp(end) + pd.Timedelta(days=1)].reset_index(drop=True)
    m["date"] = m.datetime.dt.strftime("%Y-%m-%d")
    m["minute"] = m.datetime.dt.hour * 60 + m.datetime.dt.minute
    px = ["open", "high", "low", "close"]
    bad_ohlc = int(((m.high < m[["open", "close"]].max(axis=1)) | (m.low > m[["open", "close"]].min(axis=1))).sum())
    m["high"], m["low"] = m[px].max(axis=1), m[px].min(axis=1)
    daily = pd.read_csv(DATA / meta["daily"], dtype={"date": str})
    daily = daily[(daily.date >= m.date.iloc[0]) & (daily.date <= end)].reset_index(drop=True)

    # demergers (not adjusted by Fyers) -> backward price factor; dividends -> cash per adjusted share
    adj_notes, divs = [], {}
    if meta["corp"]:
        ca = corp_actions(meta["corp"])
        for r in ca[ca.purpose.str.contains("Demerger", case=False)].itertuples():
            prev = daily[daily.date < r.ex_date].iloc[-1]
            first = m[m.date == r.ex_date].iloc[0]
            f = float(first.open) / float(prev.close)
            m.loc[m.date < r.ex_date, px] *= f
            daily.loc[daily.date < r.ex_date, px] *= f
            adj_notes.append({"ex_date": r.ex_date, "kind": "demerger", "factor": f, "prev_close": float(prev.close), "first_open": float(first.open)})
        shares = [(r.ex_date, _share_factor(r.purpose)) for r in ca.itertuples() if _share_factor(r.purpose) != 1.0]
        dem = [(a["ex_date"], a["factor"]) for a in adj_notes]
        for r in ca.itertuples():
            mm = re.search(r"Dividend\s*-\s*Rs\.?\s*([\d.]+)", r.purpose)
            if not mm or r.ex_date > end:
                continue
            raw = float(mm.group(1))
            f = math.prod(x for d, x in shares if d > r.ex_date) * math.prod(x for d, x in dem if d > r.ex_date)
            divs[r.ex_date] = divs.get(r.ex_date, 0.0) + raw * f
            adj_notes.append({"ex_date": r.ex_date, "kind": "dividend", "raw": raw, "adjusted": raw * f})
        for d, x in shares:
            adj_notes.append({"ex_date": d, "kind": "bonus/split (Fyers ne pehle se adjust kiya)", "factor": x})

    # clip 1-min extremes to the official daily range (bad ticks, e.g. NIFTY 24 Feb 2021)
    dd = daily.set_index("date")
    dh, dl = m.date.map(dd.high), m.date.map(dd.low)
    clipped_days = sorted(set(m.date[(m.high > dh * 1.0005) | (m.low < dl * 0.9995)]))
    m["high"] = np.where(dh.notna(), np.minimum(m.high, dh), m.high)
    m["low"] = np.where(dl.notna(), np.maximum(m.low, dl), m.low)
    m["open"], m["close"] = m.open.clip(m.low, m.high), m.close.clip(m.low, m.high)

    # 2-hour bars
    mins = m.minute.to_numpy()
    slot = np.where(mins < 675, 0, np.where(mins < 795, 1, 2)) if build == "3bar" else (mins - 555) // 120
    m["slot"] = slot
    g = m.groupby(["date", "slot"], sort=True)
    bars = g.agg(t0=("datetime", "first"), t1=("datetime", "last"), open=("open", "first"), high=("high", "max"),
                 low=("low", "min"), close=("close", "last"), n=("close", "size")).reset_index()
    o, h, l, c = (bars[k].to_numpy(dtype=float) for k in px)
    dates = bars.date.to_numpy(dtype=object)
    iso = pd.to_datetime(bars.date).dt.isocalendar()
    D = {"sym": sym, "label": meta["label"], "mult": meta["mult"], "build": build, "bars": bars, "date": dates, "t0": bars.t0.to_numpy(),
         "o": o, "h": h, "l": l, "c": c, "rsi": rsi_wilder(c, 14), "atr": atr_wilder(h, l, c, 14), "ema": ema(c, 200),
         "week": (iso.year * 100 + iso.week).to_numpy(), "divs": divs, "adj": adj_notes, "clipped_days": clipped_days, "bad_ohlc": bad_ohlc,
         "daily": daily, "first_1m": str(m.datetime.iloc[0]), "last_1m": str(m.datetime.iloc[-1]), "n_1m": len(m)}
    D["a"] = int(np.argmax(dates >= start))
    D["b"] = int(len(dates) - 1)
    days = sorted(set(dates[D["a"]:]))
    D["split"] = days[int(round(len(days) * IS_SHARE))]          # first out-of-sample day
    D["split_i"] = int(np.argmax(dates >= D["split"]))
    return D


# ----------------------------------------------------------------------------------------------------------- costs
def costs(buy_val: float, sell_val: float, intraday: bool, p: Params) -> dict:
    """Brokerage Rs 20 per order; delivery: STT 0.1% buy + sell, stamp 0.015% buy, DP Rs 15.34 + GST per sell;
    same-day exit (intraday): STT 0.025% sell, stamp 0.003% buy, no DP. NSE 0.00307%, SEBI Rs 10 / crore, GST 18% on
    brokerage + exchange + SEBI."""
    if not p.costs:
        return {"c_brk": 0.0, "c_stt": 0.0, "c_exch": 0.0, "c_stamp": 0.0, "c_gst": 0.0, "c_dp": 0.0, "costs": 0.0}
    tv = buy_val + sell_val
    brk = 2 * p.brokerage
    exch = 0.0000307 * tv + 1e-6 * tv
    c = {"c_brk": brk, "c_stt": 0.00025 * sell_val if intraday else 0.001 * tv, "c_exch": exch,
         "c_stamp": (0.00003 if intraday else 0.00015) * buy_val, "c_gst": 0.18 * (brk + exch), "c_dp": 0.0 if intraday else 15.34 * 1.18}
    c["costs"] = sum(c.values())
    return c


BUY_RATE = 0.001 + 0.00015 + 0.18 * 0.0000317 + 0.0000317      # worst-case buy-side % charges (delivery)


# ------------------------------------------------------------------------------------------------------------- run
def signals(D: dict, p: Params) -> tuple[np.ndarray, np.ndarray]:
    r = D["rsi"]
    rp = np.r_[np.nan, r[:-1]]
    buy = (rp <= p.buy_lvl) & (r > p.buy_lvl)
    if p.version == "B":
        buy &= D["c"] > D["ema"]
    sell = (rp <= p.sell_lvl) & (r > p.sell_lvl)
    return buy, sell


def run(D: dict, p: Params, buy: np.ndarray | None = None) -> dict:
    """Bar by bar: pending exit at the open, pending entry at the open (risk gates checked there), stop inside the bar,
    then signals at the close for the next bar's open."""
    o, h, l, c, atr, dates, week, mult = D["o"], D["h"], D["l"], D["c"], D["atr"], D["date"], D["week"], D["mult"]
    b_sig, sell = signals(D, p)
    if buy is None:
        buy = b_sig
    a, b = D["a"], D["b"]
    cash, pos, pend_in, pend_out = p.capital, None, None, False
    eq = np.full(len(c), np.nan)
    inpos = np.zeros(len(c), dtype=bool)
    day_ref = week_ref = p.capital
    streak = 0
    trades, skipped = [], []

    def close(i, raw, why):
        nonlocal cash, pos, streak
        q = pos
        fill = raw * (1 - p.slip)
        buy_val, sell_val = q["qty"] * q["fill"] * mult, q["qty"] * fill * mult
        cst = costs(buy_val, sell_val, dates[i] == dates[q["i"]], p)
        gross = sell_val - buy_val
        net = gross - cst["costs"] + q["div"]
        cash += sell_val - cst["costs"]
        streak = streak + 1 if net < 0 else 0
        trades.append({**{k: q[k] for k in ("sig", "i", "raw", "fill", "stop", "atr", "qty", "eq0", "risk_rs", "capped", "div", "rsi_sig", "ema_sig")},
                       "x": i, "exit_raw": raw, "exit": fill, "reason": why, "gross": gross, "slip_rs": q["qty"] * mult * (q["raw"] * p.slip + raw * p.slip),
                       **cst, "net": net, "notional": buy_val})
        pos = None

    for i in range(a, b + 1):
        if i > a and dates[i] != dates[i - 1]:
            day_ref = eq[i - 1]
            if week[i] != week[i - 1]:
                week_ref = eq[i - 1]
        if pos is not None and (i == a or dates[i] != dates[i - 1]):
            dv = D["divs"].get(dates[i])
            if dv:
                pos["div"] += pos["qty"] * dv * mult
                cash += pos["qty"] * dv * mult
        if pend_out and pos is not None:
            close(i, o[i], EXIT_RSI)
        pend_out = False
        if pend_in is not None:
            s, pend_in = pend_in, None
            e = cash
            why = None
            if p.risk_rules and e / day_ref - 1 <= -p.day_loss:
                why = SKIP_DAY
            elif p.risk_rules and e / week_ref - 1 <= -p.week_loss:
                why = SKIP_WEEK
            elif p.risk_rules and streak >= p.streak_n:
                why, streak = SKIP_STREAK, 0
            else:
                fill = o[i] * (1 + p.slip)
                stop = fill - p.stop_atr * atr[s]
                rps = (fill - stop) * mult
                q_risk = math.floor(p.risk * e / rps)
                q_open = math.floor(p.max_open_risk * e / rps)
                q_cash = math.floor((cash - 2.5 * p.brokerage) / (fill * mult * (1 + BUY_RATE)))
                qty = max(0, min(q_risk, q_open, q_cash))
                if qty <= 0:
                    why = SKIP_QTY
                else:
                    pos = {"sig": s, "i": i, "raw": o[i], "fill": fill, "stop": stop if p.use_stop else -math.inf, "atr": atr[s], "qty": qty, "eq0": e,
                           "risk_rs": qty * rps, "capped": q_cash < q_risk, "div": 0.0, "rsi_sig": D["rsi"][s], "ema_sig": D["ema"][s]}
                    cash -= qty * fill * mult
            if why:
                skipped.append({"sig": s, "i": i, "why": why})
        if pos is not None:
            if o[i] <= pos["stop"]:
                close(i, o[i], EXIT_GAP)
            elif l[i] <= pos["stop"]:
                close(i, pos["stop"], EXIT_SL)
        eq[i] = cash + (pos["qty"] * c[i] * mult if pos is not None else 0.0)
        inpos[i] = pos is not None
        if pos is not None:
            if sell[i]:
                pend_out = True
            if buy[i]:
                skipped.append({"sig": i, "i": None, "why": SKIP_POS})
        elif buy[i] and i < b:
            pend_in = i
    if pos is not None:
        close(b, c[b], EXIT_END)                                    # sold at the last close (slippage + costs as usual)
        eq[b] = cash
    return {"p": p, "trades": trades, "skipped": skipped, "eq": eq, "inpos": inpos, "n_signals": int(buy[a:b + 1].sum())}


def buy_hold(D: dict, p: Params) -> dict:
    """Buy on the first bar's open with the whole capital (slippage + delivery costs), hold, dividends in cash, sell at the last close."""
    o, c, dates, mult, a, b = D["o"], D["c"], D["date"], D["mult"], D["a"], D["b"]
    fill = o[a] * (1 + p.slip)
    qty = math.floor((p.capital - 2.5 * p.brokerage) / (fill * mult * (1 + BUY_RATE)))
    cash = p.capital - qty * fill * mult
    eq = np.full(len(c), np.nan)
    div = 0.0
    for i in range(a, b + 1):
        if i == a or dates[i] != dates[i - 1]:
            dv = D["divs"].get(dates[i])
            if dv and i > a:
                cash += qty * dv * mult
                div += qty * dv * mult
        eq[i] = cash + qty * c[i] * mult
    sell = c[b] * (1 - p.slip)
    cst = costs(qty * fill * mult, qty * sell * mult, False, p)
    eq[b] = cash + qty * sell * mult - cst["costs"]
    return {"eq": eq, "qty": qty, "fill": fill, "div": div, "costs": cst["costs"]}


# ----------------------------------------------------------------------------------------------------------- stats
def trade_frame(D: dict, r: dict) -> pd.DataFrame:
    t = pd.DataFrame(r["trades"])
    if t.empty:
        return t
    dates, t0 = D["date"], D["t0"]
    di = {d: k for k, d in enumerate(sorted(set(dates)))}
    t["entry_date"] = [dates[i] for i in t.i]
    t["exit_date"] = [dates[i] for i in t.x]
    t["entry_t"] = [pd.Timestamp(t0[i]).strftime("%Y-%m-%d %H:%M") for i in t.i]
    t["exit_t"] = [pd.Timestamp(t0[i]).strftime("%Y-%m-%d %H:%M") for i in t.x]
    t["sig_t"] = [pd.Timestamp(t0[i]).strftime("%Y-%m-%d %H:%M") for i in t.sig]
    t["bars"] = t.x - t.i + 1
    t["days"] = [di[dates[x]] - di[dates[i]] for i, x in zip(t.i, t.x)]
    t["R"] = t.net / t.risk_rs
    t["ret_eq"] = t.net / t.eq0 * 100                         # % of capital before the trade
    t["ret_pos"] = t.net / t.notional * 100                   # % of money put into the trade
    t["risk_pct"] = t.risk_rs / t.eq0 * 100
    t["pos_pct"] = t.notional / t.eq0 * 100
    t["period"] = np.where(t.entry_date < D["split"], "IS", "OOS")
    return t


def _streak(x: np.ndarray) -> int:
    best = cur = 0
    for v in x:
        cur = cur + 1 if v < 0 else 0
        best = max(best, cur)
    return best


def curve_stats(D: dict, eq: np.ndarray, lo: int, hi: int, base: float) -> dict:
    e = np.r_[base, eq[lo:hi + 1]]
    peak = np.maximum.accumulate(e)
    dd = e / peak - 1
    dates = D["date"][lo:hi + 1]
    last = np.r_[dates[1:] != dates[:-1], True]
    de = np.r_[base, eq[lo:hi + 1][last]]
    dr = de[1:] / de[:-1] - 1
    yrs = (pd.Timestamp(dates[-1]) - pd.Timestamp(dates[0])).days / 365.25
    ret = e[-1] / base - 1
    sd = dr.std(ddof=1) if len(dr) > 2 else np.nan
    neg = dr[dr < 0]
    # longest time under water (trading days)
    uw, cur = 0, 0
    for v in (de / np.maximum.accumulate(de) - 1)[1:]:
        cur = cur + 1 if v < -1e-12 else 0
        uw = max(uw, cur)
    return {"ret_pct": ret * 100, "net_eq": e[-1] - base, "cagr": ((e[-1] / base) ** (1 / yrs) - 1) * 100 if yrs > 0 and e[-1] > 0 else None,
            "max_dd_pct": float(dd.min() * 100), "max_dd": float((e - peak).min()), "years": yrs, "end_eq": float(e[-1]), "start_eq": float(base),
            "sharpe": float(dr.mean() / sd * np.sqrt(252)) if sd and sd > 0 else None,
            "sharpe_rf": float((dr.mean() - 0.065 / 252) / sd * np.sqrt(252)) if sd and sd > 0 else None,
            "sortino": float(dr.mean() / np.sqrt((neg ** 2).sum() / len(dr)) * np.sqrt(252)) if len(neg) else None,
            "vol_pct": float(sd * np.sqrt(252) * 100) if sd == sd else None, "uw_days": uw, "n_days": int(len(dr))}


def stats(D: dict, r: dict, t: pd.DataFrame, lo: int, hi: int) -> dict:
    """Curve numbers on bars lo..hi (start = equity just before lo) + trade numbers for trades entered in lo..hi."""
    base = r["p"].capital if lo <= D["a"] else float(r["eq"][lo - 1])
    s = curve_stats(D, r["eq"], lo, hi, base)
    s["exposure"] = float(r["inpos"][lo:hi + 1].mean() * 100)
    tt = t[(t.i >= lo) & (t.i <= hi)] if len(t) else t
    n = len(tt)
    s["trades"] = n
    if not n:
        s.update({k: None for k in ("win_pct", "avg_win", "avg_loss", "avg_win_pct", "avg_loss_pct", "avg_win_r", "avg_loss_r", "pf", "exp_rs", "exp_r",
                                    "exp_pct", "avg_days", "avg_bars", "best", "worst", "payoff")})
        s.update({"net": 0.0, "gross": 0.0, "costs": 0.0, "slip": 0.0, "div": 0.0, "loss_streak": 0, "capped_pct": None, "avg_risk_pct": None, "avg_pos_pct": None,
                  "wins": 0, "losses": 0})
        return s
    w, lo_ = tt[tt.net > 0], tt[tt.net <= 0]
    gl = -lo_.net.sum()
    s.update({"wins": int(len(w)), "losses": int(len(lo_)), "win_pct": len(w) / n * 100, "net": float(tt.net.sum()), "gross": float(tt.gross.sum()),
              "costs": float(tt.costs.sum()), "slip": float(tt.slip_rs.sum()), "div": float(tt["div"].sum()),
              "avg_win": float(w.net.mean()) if len(w) else None, "avg_loss": float(lo_.net.mean()) if len(lo_) else None,
              "avg_win_pct": float(w.ret_eq.mean()) if len(w) else None, "avg_loss_pct": float(lo_.ret_eq.mean()) if len(lo_) else None,
              "avg_win_r": float(w.R.mean()) if len(w) else None, "avg_loss_r": float(lo_.R.mean()) if len(lo_) else None,
              "pf": float(w.net.sum() / gl) if gl > 0 else None, "exp_rs": float(tt.net.mean()), "exp_r": float(tt.R.mean()), "exp_pct": float(tt.ret_eq.mean()),
              "avg_days": float(tt.days.mean()), "avg_bars": float(tt.bars.mean()), "best": float(tt.net.max()), "worst": float(tt.net.min()),
              "loss_streak": _streak(tt.net.to_numpy()), "capped_pct": float(tt.capped.mean() * 100), "avg_risk_pct": float(tt.risk_pct.mean()),
              "avg_pos_pct": float(tt.pos_pct.mean()),
              "payoff": float(w.net.mean() / -lo_.net.mean()) if len(w) and len(lo_) and lo_.net.mean() < 0 else None})
    return s


def periods(D: dict) -> dict:
    return {"all": (D["a"], D["b"]), "is": (D["a"], D["split_i"] - 1), "oos": (D["split_i"], D["b"])}


def all_stats(D: dict, r: dict, t: pd.DataFrame) -> dict:
    return {k: stats(D, r, t, lo, hi) for k, (lo, hi) in periods(D).items()}


def bh_stats(D: dict, bh: dict, capital: float) -> dict:
    out = {}
    for k, (lo, hi) in periods(D).items():
        base = capital if lo <= D["a"] else float(bh["eq"][lo - 1])
        out[k] = curve_stats(D, bh["eq"], lo, hi, base)
    return out


def bootstrap(x: np.ndarray, n: int = 10000, seed: int = 11) -> dict:
    """Mean of x (trade return, % of capital): 95% interval from resampling trades, share of resamples above 0, t-stat."""
    if len(x) < 3:
        return {"mean": float(x.mean()) if len(x) else None, "lo": None, "hi": None, "p_pos": None, "t": None}
    rng = np.random.default_rng(seed)
    m = rng.choice(x, size=(n, len(x)), replace=True).mean(axis=1)
    sd = x.std(ddof=1)
    return {"mean": float(x.mean()), "lo": float(np.percentile(m, 2.5)), "hi": float(np.percentile(m, 97.5)), "p_pos": float((m > 0).mean() * 100),
            "t": float(x.mean() / (sd / np.sqrt(len(x)))) if sd > 0 else None}


def random_entries(D: dict, p: Params, k: int, sims: int = 300, seed: int = 7) -> np.ndarray:
    """Final equity of `sims` runs where the k buy signals are put on random bars (B: only bars with close > EMA200);
    exits, stop, sizing, risk rules and costs unchanged."""
    a, b = D["a"], D["b"]
    elig = np.arange(a, b)
    if p.version == "B":
        elig = elig[D["c"][a:b] > D["ema"][a:b]]
    rng = np.random.default_rng(seed)
    out = np.empty(sims)
    for s in range(sims):
        sig = np.zeros(len(D["c"]), dtype=bool)
        sig[rng.choice(elig, size=min(k, len(elig)), replace=False)] = True
        out[s] = run(D, p, sig)["eq"][b]
    return out
