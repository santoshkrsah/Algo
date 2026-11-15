"""Position limits, quantity, slippage and charges -> trade table with Rs P&L (fixed capital, no compounding)."""
import numpy as np
import pandas as pd

from backtest.groww5.config import CONFIG, COST_RATES
from backtest.groww5.data import lot_size


def _stt(model, days):
    out = np.zeros(len(days))
    for d0, r in model["stt_sell"]:
        out[days >= d0] = r
    return out / 100


def charges(kind: str, buy_val, sell_val, days) -> dict:
    """Both legs of every trade. buy_val / sell_val in Rs (real prices x qty), days = 'YYYY-MM-DD' array."""
    m = COST_RATES[kind]
    bv, sv = np.asarray(buy_val, float), np.asarray(sell_val, float)
    if "brokerage_pct" in m:
        brk = np.minimum(m["brokerage_cap"], m["brokerage_pct"] / 100 * bv) + np.minimum(m["brokerage_cap"], m["brokerage_pct"] / 100 * sv)
    else:
        brk = np.full(len(bv), 2 * m["brokerage"])
    turn = bv + sv
    stt = _stt(m, np.asarray(days)) * sv
    exch = m["exch_pct"] / 100 * turn
    sebi = m["sebi_pct"] / 100 * turn
    stamp = m["stamp_buy_pct"] / 100 * bv
    gst = m["gst_pct"] / 100 * (brk + exch + sebi)
    return {"brokerage": brk, "stt": stt, "exchange": exch + sebi, "stamp": stamp, "gst": gst, "total": brk + stt + exch + sebi + stamp + gst}


def select(cands: pd.DataFrame, max_open: int, per_day: int | None) -> pd.DataFrame:
    """Portfolio pass, day by day in entry order: max `max_open` trades at once, one per stock, max per_day entries."""
    if not len(cands):
        return cands
    c = cands.sort_values(["date", "e", "prio", "sym"], kind="stable")
    keep = []
    for _, g in c.groupby("date", sort=True):
        opened = []        # (exit index, sym)
        n = 0
        for i, e, x, s in zip(g.index, g["e"].to_numpy(), g["x"].to_numpy(), g["sym"].to_numpy()):
            opened = [(xx, ss) for xx, ss in opened if xx > e]
            if len(opened) >= max_open or any(ss == s for _, ss in opened) or (per_day and n >= per_day):
                continue
            opened.append((x, s))
            keep.append(i)
            n += 1
    return c.loc[keep]


def size(t: pd.DataFrame, strat: str, slip_mult: float = 1.0, cfg=None) -> pd.DataFrame:
    """Adds qty, fills with slippage, gross / charges / net Rs, risk Rs, R multiple."""
    cfg = cfg or CONFIG
    mo = cfg["money"]
    t = t.copy()
    if not len(t):
        for k in ("qty", "entry_f", "exit_f", "gross", "slip_rs", "charges", "net", "risk_rs", "R", "value"):
            t[k] = []
        return t
    side = t["side"].to_numpy()
    entry, exit_, sl = t["entry"].to_numpy(float), t["exit"].to_numpy(float), t["sl"].to_numpy(float)
    risk_ps = np.abs(entry - sl)
    risk_rs = mo["capital"] * mo["risk_pct"] / 100
    if strat == "momo":
        lot = np.array([lot_size(d) for d in t["date"]])
        lots = np.clip(np.round(risk_rs / (risk_ps * lot)), 1, min(mo["nifty_max_lots"], mo["capital"] // mo["nifty_margin_lot"]))
        qty = lots * lot
        slip = cfg["slip_pts"] * slip_mult
        entry_f, exit_f = entry + side * slip, exit_ - side * slip
        off = np.array([i["off"] for i in t["info"]])
        real_in, real_out = entry_f - off, exit_f - off
        kind = "fut"
        t["lots"] = lots.astype(int)
        t["lot"] = lot
    else:
        qty = np.floor(np.minimum(risk_rs / risk_ps, mo["max_pos_value"] / entry))
        slip = cfg["slip_pct"] * slip_mult / 100
        entry_f, exit_f = entry * (1 + side * slip), exit_ * (1 - side * slip)
        real_in, real_out = entry_f, exit_f
        kind = "cash"
    buy_val = np.where(side > 0, real_in, real_out) * qty
    sell_val = np.where(side > 0, real_out, real_in) * qty
    ch = charges(kind, buy_val, sell_val, t["date"].to_numpy())
    t["qty"] = qty.astype(int)
    t["entry_f"], t["exit_f"] = entry_f, exit_f
    t["gross0"] = side * (exit_ - entry) * qty                   # before slippage and charges
    t["gross"] = side * (exit_f - entry_f) * qty                 # after slippage
    t["slip_rs"] = t["gross0"] - t["gross"]
    for k in ("brokerage", "stt", "exchange", "stamp", "gst"):
        t[f"c_{k}"] = ch[k]
    t["charges"] = ch["total"]
    t["net"] = t["gross"] - t["charges"]
    t["risk_rs"] = risk_ps * qty
    t["R"] = np.where(t["risk_rs"] > 0, t["net"] / t["risk_rs"], np.nan)
    t["value"] = (entry - (t["info"].map(lambda i: i.get("off", 0.0)).to_numpy() if strat == "momo" else 0)) * qty
    return t[t["qty"] > 0]


def book(cands: pd.DataFrame, strat: str, slip_mult: float = 1.0, cfg=None) -> pd.DataFrame:
    cfg = cfg or CONFIG
    per_day = {"scalp": cfg["scalp"]["per_day"], "news": cfg["news"]["per_day"], "momo": None}.get(strat)
    mx = 1 if strat == "momo" else cfg["money"]["max_open"]
    return size(select(cands, mx, per_day), strat, slip_mult, cfg).reset_index(drop=True)
