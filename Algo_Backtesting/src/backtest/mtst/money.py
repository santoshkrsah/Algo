"""Cost model (charges + slippage, per unit of NIFTY) and fixed-fractional position sizing in rupees.

Per trade, per unit:
    gross points = side x (exit fill - entry fill)                 (back-adjusted futures: a roll gap is never P&L)
    slippage     = slip x (2 + 2 x rolls)
    charges      = Rs per lot of all orders (entry, exit, 2 per roll) / lot size
    net points   = gross - slippage - charges
Sizing (compounding): budget = risk% x sizing capital; lots = round(budget / (stop distance x lot)), min 1 (or floor);
lots <= floor(equity / margin per lot); skipped if 1 lot's margin > equity. DD-aware variant: sizing capital =
equity x (1 - planned DD) and half size while the drawdown from the equity high is deeper than the planned DD.
"""
import math

import numpy as np
import pandas as pd

from backtest.mtst.config import CONFIG


def lot_size(days) -> np.ndarray:
    days = np.asarray(days, "datetime64[D]")
    out = np.zeros(len(days), dtype=np.int64)
    for d0, lot in CONFIG["lot_sizes"]:
        out[days >= np.datetime64(d0, "D")] = lot
    return out


def stt_rate(days, mode: str) -> np.ndarray:
    tbl = CONFIG["costs"]["stt_sell_pct"]
    days = np.asarray(days, "datetime64[D]")
    if mode == "current":
        return np.full(len(days), tbl[-1][1] / 100)
    out = np.zeros(len(days))
    for d0, r in tbl:
        out[days >= np.datetime64(d0, "D")] = r / 100
    return out


def order_rs(value, buy, days, mode: str = "current", detail: bool = False):
    """Charges in Rs of futures orders of `value` Rs notional."""
    m = CONFIG["costs"]
    value = np.asarray(value, float)
    brk = np.full(len(value), m["brokerage"])
    stt = np.where(buy, 0.0, stt_rate(days, mode) * value)
    exch = m["exch_pct"] / 100 * value
    sebi = m["sebi_pct"] / 100 * value
    stamp = np.where(buy, m["stamp_buy_pct"] / 100 * value, 0.0)
    gst = m["gst_pct"] / 100 * (brk + exch + sebi)
    tot = brk + stt + exch + sebi + stamp + gst
    if detail:
        return {"brokerage": brk, "stt": stt, "exchange": exch, "sebi": sebi, "stamp": stamp, "gst": gst, "total": tot}
    return tot


def video_cost_check() -> dict:
    """Our rates at the video's ~Rs 15 lakh notional vs its ~Rs 85 buy / ~Rs 788 sell per lot."""
    v = CONFIG["video_costs"]
    d = np.array(["2026-11-13"], "datetime64[D]")
    b = order_rs([v["notional"]], np.array([True]), d, "current", True)
    s = order_rs([v["notional"]], np.array([False]), d, "current", True)
    return {"notional": v["notional"], "buy": float(b["total"][0]), "sell": float(s["total"][0]), "video_buy": v["buy"], "video_sell": v["sell"],
            "buy_parts": {k: float(x[0]) for k, x in b.items()}, "sell_parts": {k: float(x[0]) for k, x in s.items()}}


def apply_costs(df: pd.DataFrame, fut, slip: float | None = None, mode: str | None = None) -> pd.DataFrame:
    if not len(df):
        return df
    slip = CONFIG["slippage"] if slip is None else slip
    mode = mode or CONFIG["rates_mode"]
    df = df.copy()
    lot = lot_size(df["entry_dt"].to_numpy("datetime64[D]")).astype(float)
    long = df["side"].to_numpy() > 0
    d_in, d_out = df["entry_dt"].to_numpy("datetime64[D]"), df["exit_dt"].to_numpy("datetime64[D]")
    c = order_rs(df["entry_raw"].to_numpy() * lot, long, d_in, mode) + order_rs(df["exit_raw"].to_numpy() * lot, ~long, d_out, mode)
    # rolls: close the old + open the new contract at the real price of the roll minute
    seg = fut.seg
    roll_idx = np.nonzero(np.diff(seg))[0] + 1
    for t, (a, b, s, nr) in enumerate(zip(df["i_in"], df["i_out"], df["side"], df["rolls"])):
        if nr:
            ri = roll_idx[(roll_idx > a) & (roll_idx <= b)]
            v = (fut.o[ri] - fut.off[ri]) * lot[t]
            dd = fut.dt[ri].astype("datetime64[D]")
            c[t] += float(order_rs(v, np.full(len(ri), s < 0), dd, mode).sum() + order_rs(v, np.full(len(ri), s > 0), dd, mode).sum())
    df["lot"] = lot.astype(int)
    df["slip_pts"] = slip * (2 + 2 * df["rolls"].to_numpy())
    df["cost_rs_lot"] = c
    df["cost_pts"] = c / lot
    df["gross_u"] = df["gross_pts"]
    df["net_pts"] = df["gross_pts"] - df["slip_pts"] - df["cost_pts"]
    return df


def size(df: pd.DataFrame, risk_pct: float | None = None, rounding: str | None = None, dd_aware: bool = False,
         capital: float | None = None, margin_cap: bool | None = None) -> pd.DataFrame:
    """Lots and Rs P&L per trade (trades never overlap, so equity = capital + closed P&L at each entry)."""
    risk_pct = CONFIG["risk_pct"] if risk_pct is None else risk_pct
    rounding = rounding or CONFIG["lots_round"]
    capital = capital or CONFIG["capital"]
    margin_cap = CONFIG["margin_cap"] if margin_cap is None else margin_cap
    mpl = CONFIG["margin_per_lot"]
    plan = CONFIG["dd_sizing"]["planned_dd_pct"] / 100
    df = df.sort_values("entry_dt").reset_index(drop=True).copy()
    eq, peak = capital, capital
    out = {k: [] for k in ("equity_in", "budget", "risk_lot", "lots_raw", "lots", "skip", "capped", "dd_half", "pnl_rs", "risk_rs")}
    for r in df.itertuples(index=False):
        half = False
        base = eq
        if dd_aware:
            base = eq * (1 - plan)
            if peak > 0 and (peak - eq) / peak > plan:
                half = True
                base *= 0.5
        budget = base * risk_pct / 100
        risk_lot = r.stop_dist * r.lot
        x = budget / risk_lot if risk_lot > 0 else 0.0
        lots = math.floor(x + 0.5) if rounding == "round" else math.floor(x)
        if rounding == "round":
            lots = max(CONFIG["min_lots"], lots)
        skip, capped = "", False
        if eq <= 0 or mpl > eq:
            lots, skip = 0, "margin: 1 lot > equity"
        elif lots == 0:
            skip = "0 lots (floor)"
        elif margin_cap and lots * mpl > eq:
            lots, capped = int(eq // mpl), True
        u = lots * r.lot
        pnl = u * (r.gross_pts - r.slip_pts) - lots * r.cost_rs_lot
        eq += pnl
        peak = max(peak, eq)
        for k, v in (("equity_in", eq - pnl), ("budget", budget), ("risk_lot", risk_lot), ("lots_raw", x), ("lots", lots), ("skip", skip),
                     ("capped", capped), ("dd_half", half), ("pnl_rs", pnl), ("risk_rs", lots * risk_lot)):
            out[k].append(v)
    for k, v in out.items():
        df[k] = v
    return df


def daily_rs(df: pd.DataFrame, fut) -> pd.Series:
    """Rs P&L per trading day, open positions marked at each day's last futures close (costs booked on the exit day)."""
    day = np.zeros(len(fut.days))
    last_close = fut.c[fut.last]
    for r in df.itertuples(index=False):
        if r.lots == 0:
            continue
        u = r.lots * r.lot * r.side
        de, dx = int(fut.di[r.i_in]), int(fut.di[r.i_out])
        cost = u * (r.exit_px - r.entry_px) - r.pnl_rs
        if de == dx:
            day[de] += r.pnl_rs
            continue
        marks = np.r_[r.entry_px, last_close[de:dx]]
        day[de:dx] += u * np.diff(marks)
        day[dx] += u * (r.exit_px - last_close[dx - 1]) - cost
    return pd.Series(day, index=pd.DatetimeIndex(fut.days))
