"""Gap + RSI(10) + 21-EMA strategy on daily candles (rulebook): long gap-up breakout above the 21-EMA with a strong RSI and a
monthly-trend confirmation; short gap-down reversal after an overbought RSI. Stops: close back through the 21-EMA or the gap.
Sizing: 1% of capital risked per trade, at most 10 stocks open, no leverage.

    signals(ind, p, "long") -> candidate gap days;  trades(ind, p, "long", "ema") -> walked trades;  size(trades, p) -> sized P&L
"""
from dataclasses import dataclass, replace

import numpy as np
import pandas as pd

# key, label, target R (None = no target), time cap (trading days), mode ("rule" = EMA/gap-fill stop, "monthly" = gap-fill + monthly EMA break)
EXITS = [("ema", "21-EMA trail (koi target nahi)", None, 250, "rule"),
         ("rr2", "Target 1:2", 2, 60, "rule"), ("rr3", "Target 1:3", 3, 60, "rule"), ("rr5", "Target 1:5", 5, 60, "rule"),
         ("monthly", "Monthly trend toote tab exit", None, 250, "monthly"),
         ("t20", "20 din time stop", None, 20, "rule"), ("t40", "40 din time stop", None, 40, "rule"), ("t60", "60 din time stop", None, 60, "rule")]
EXIT = {e[0]: e for e in EXITS}
GAPS = (1.0, 2.0, 3.0)


@dataclass(frozen=True)
class Params:
    gap: float = 2.0               # minimum gap % of the previous close
    resist: bool = True            # long: open must be above the highest high of the previous 20 days
    monthly_n: int = 10            # long: close above the monthly EMA of this length (0 = off)
    rsi_lo: float = 60.0           # long: smoothed RSI above this ...
    rsi_hi: float = 90.0           # ... and below this
    rsi_short: float = 70.0        # short: previous day's smoothed RSI above this
    fill: str = "close"            # gap-fill level: previous "close" or the previous "hl" (high for longs, low for shorts)
    entry: str = "close"           # "close" of the gap day or the "next_open"
    slip: float = 0.001            # each fill
    capital: float = 1_000_000.0
    risk: float = 0.01             # share of capital risked per trade
    max_pos: int = 10
    max_val: float = 0.2           # max share of capital in one stock
    min_fill: float = 0.5          # skip the trade if cash / caps allow less than this share of the 1%-risk size
    min_turn: float = 0.0          # universe: 20-day average turnover (Rs) at least this
    max_gap: float = 25.0          # bigger gaps are corporate actions (demerger ...), not trades
    jump_wait: int = 60            # no signal for this many days after an unexplained >35% jump (indicators are broken)
    universe: frozenset | None = None


def with_params(base: Params, **kw) -> Params:
    return replace(base, **kw)


def signals(ind: pd.DataFrame, p: Params, side: str) -> pd.DataFrame:
    x = ind
    if p.universe is not None:
        x = x[x.symbol.isin(p.universe)]
    if p.min_turn:
        x = x[x.turn20 >= p.min_turn]
    x = x[(x.gap_pct.abs() <= p.max_gap) & (x.since_jump.isna() | (x.since_jump >= p.jump_wait))]
    if side == "long":
        c = (x.open > x.p_high) & (x.gap_pct >= p.gap) & x.ema.notna() & (x.close > x.ema) & (x.rsi_s > p.rsi_lo) & (x.rsi_s < p.rsi_hi)
        if p.resist:
            c &= x.open > x.resist
        if p.monthly_n:
            c &= x.close > x[f"mema{p.monthly_n}"]
        fill = x.p_close if p.fill == "close" else x.p_high
        c &= x.close > fill
    else:
        c = (x.open < x.p_low) & (x.gap_pct <= -p.gap) & (x.p_rsi_s > p.rsi_short) & x.ema.notna()
        fill = x.p_close if p.fill == "close" else x.p_low
        c &= x.close < fill
    s = x[c].copy()
    s["fill_lvl"] = fill[c]
    return s


def _arrays(ind: pd.DataFrame, monthly_n: int):
    out = {}
    for sym, g in ind.groupby("symbol", sort=False):
        out[sym] = {k: g[k].to_numpy() for k in ("open", "high", "low", "close", "ema", "date")}
        out[sym]["mema"] = g[f"mema{monthly_n}"].to_numpy() if monthly_n else np.full(len(g), np.nan)
        out[sym]["pos"] = {d: i for i, d in enumerate(g.date)}
    return out


def trades(ind: pd.DataFrame, p: Params, side: str, exit_key: str, arrs: dict | None = None, sig: pd.DataFrame | None = None) -> pd.DataFrame:
    """Walk every signal to its exit (per-share prices, before sizing). One row per trade."""
    _, _, rr, cap, mode = EXIT[exit_key]
    sg = 1 if side == "long" else -1
    arrs = arrs or _arrays(ind, p.monthly_n or 10)
    sig = signals(ind, p, side) if sig is None else sig
    rows = []
    for r in sig.itertuples():
        a = arrs[r.symbol]
        i = a["pos"][r.date]
        n = len(a["close"])
        if i + 1 >= n:
            continue
        j = i if p.entry == "close" else i + 1
        raw = a["close"][i] if p.entry == "close" else a["open"][i + 1]
        ema_i = a["ema"][i]
        ema_on = (a["close"][i] > ema_i) if sg == 1 else (a["close"][i] < ema_i)       # EMA stop only if the price is on the right side of it
        fill = r.fill_lvl
        levels = [fill] + ([ema_i] if ema_on and mode == "rule" else [])
        stop = max(levels) if sg == 1 else min(levels)
        risk = (raw - stop) * sg
        if risk <= 0 or risk / raw < 0.002:
            continue
        tgt = raw + sg * rr * risk if rr else None
        x_i, px, why = None, None, None
        last = min(i + cap, n - 1)
        for t in range(i + 1, last + 1):
            if tgt is not None and ((a["high"][t] >= tgt) if sg == 1 else (a["low"][t] <= tgt)):
                o = a["open"][t]
                px = (o if (o >= tgt if sg == 1 else o <= tgt) else tgt)
                x_i, why = t, f"target 1:{rr}"
                break
            if abs(a["close"][t] / a["close"][t - 1] - 1) > 0.35:                       # demerger / split not in the data: leave at the day before
                x_i, px, why = max(t - 1, j), a["close"][max(t - 1, j)], "corporate action jump"
                break
            cl = a["close"][t]
            if (cl < fill) if sg == 1 else (cl > fill):
                x_i, px, why = t, cl, "gap fill (close)"
                break
            if mode == "rule" and ema_on and ((cl < a["ema"][t]) if sg == 1 else (cl > a["ema"][t])):
                x_i, px, why = t, cl, "21 EMA ke paar close"
                break
            if mode == "monthly" and sg == 1 and not np.isnan(a["mema"][t]) and cl < a["mema"][t]:
                x_i, px, why = t, cl, "monthly trend toota"
                break
        if x_i is None:
            x_i, px = last, a["close"][last]
            why = f"{cap} din time stop" if last - i >= cap else "data khatam"
        buy = raw * (1 + p.slip) if sg == 1 else raw * (1 - p.slip)          # entry fill
        out = px * (1 - p.slip) if sg == 1 else px * (1 + p.slip)             # exit fill
        rows.append({"symbol": r.symbol, "side": side, "signal_date": r.date, "entry_date": a["date"][j], "exit_date": a["date"][x_i], "hold": int(x_i - j),
                     "gap_pct": round(r.gap_pct, 2), "rsi_s": round(float(r.rsi_s), 1), "p_rsi_s": round(float(r.p_rsi_s), 1) if not np.isnan(r.p_rsi_s) else None,
                     "entry": round(float(buy), 2), "exit": round(float(out), 2), "stop": round(float(stop), 2), "fill": round(float(fill), 2),
                     "ema": round(float(ema_i), 2), "risk_ps": round(float(risk * (1 + p.slip)), 2), "reason": why, "target": None if tgt is None else round(float(tgt), 2),
                     "ema_on": bool(ema_on)})
    return pd.DataFrame(rows)


def _costs(side: str, bv: float, sv: float) -> float:
    if side == "long":      # equity delivery: no brokerage, STT 0.1% both sides, exchange, SEBI, stamp on buy, GST, DP charge on sell
        exch, sebi = 0.0000307 * (bv + sv), 1e-6 * (bv + sv)
        return 0.001 * (bv + sv) + exch + sebi + 0.00015 * bv + 0.18 * (exch + sebi) + 15.34 * 1.18
    brk = 40.0              # short via stock futures: Rs 20 x 2 orders
    exch, sebi = 0.0000173 * (bv + sv), 1e-6 * (bv + sv)
    return brk + exch + sebi + 0.18 * (brk + exch + sebi) + 0.0002 * sv + 0.00002 * bv


def size(t: pd.DataFrame, p: Params, nifty: pd.DataFrame | None = None) -> pd.DataFrame:
    """Position size (1% risk, caps, max open positions, cash) in entry-date order, then Rs P&L and R multiples."""
    if t.empty:
        return t
    t = t.assign(_g=t.gap_pct.abs()).sort_values(["entry_date", "_g"], ascending=[True, False]).drop(columns="_g").reset_index(drop=True)   # bigger gap first
    open_pos: list[tuple[str, float]] = []        # (exit_date, notional)
    qtys, keep = [], []
    realized_by_exit: list[tuple[str, float]] = []
    for r in t.itertuples():
        open_pos = [o for o in open_pos if o[0] >= r.entry_date]
        used = sum(o[1] for o in open_pos)
        qty = 0
        if len(open_pos) < p.max_pos:
            want = min(p.capital * p.risk / r.risk_ps, p.capital * p.max_val / r.entry)
            q = min(want, (p.capital - used) / r.entry)
            qty = int(np.floor(q)) if q >= p.min_fill * want else 0
        qtys.append(qty)
        if qty >= 1:
            open_pos.append((r.exit_date, qty * r.entry))
    t["qty"] = qtys
    t = t[t.qty >= 1].copy()
    sg = np.where(t.side == "long", 1, -1)
    bv = np.where(sg == 1, t.qty * t.entry, t.qty * t.exit)           # buy value
    sv = np.where(sg == 1, t.qty * t.exit, t.qty * t.entry)           # sell value
    t["gross"] = np.round(sg * t.qty * (t.exit - t.entry), 2)
    t["costs"] = np.round([_costs(s, b, v) for s, b, v in zip(t.side, bv, sv)], 2)
    t["net"] = np.round(t.gross - t.costs, 2)
    t["R"] = np.round(t.net / (t.qty * t.risk_ps), 2)
    t["value"] = np.round(t.qty * t.entry, 0)
    if nifty is not None:
        ok = t.entry_date.map(nifty.nifty_sma200).notna()
        t["bull"] = pd.Series(np.where(ok, t.entry_date.map(nifty.nifty_bull).fillna(False).astype(bool), None), index=t.index, dtype="object")
    return t.sort_values(["entry_date", "symbol"]).reset_index(drop=True)


def stats(t: pd.DataFrame, capital: float) -> dict:
    if t.empty:
        return {"trades": 0, "net": 0.0, "win_pct": None, "profit_factor": None, "max_dd": 0.0, "avg_r": None}
    by_exit = t.groupby("exit_date").net.sum().sort_index()
    cum = by_exit.cumsum()
    dd = float((cum - cum.cummax().clip(lower=0)).min())
    w, l = t[t.net > 0], t[t.net <= 0]
    k = max(1, int(round(len(t) * 0.05)))
    return {"trades": len(t), "net": float(t.net.sum()), "ret_pct": float(t.net.sum() / capital * 100), "win_pct": float((t.net > 0).mean() * 100),
            "avg_win_r": float(w.R.mean()) if len(w) else None, "avg_loss_r": float(l.R.mean()) if len(l) else None, "avg_r": float(t.R.mean()),
            "profit_factor": float(w.net.sum() / -l.net.sum()) if l.net.sum() < 0 else None, "max_dd": dd,
            "top5_share": float(t.net.nlargest(k).sum() / t.net.sum() * 100) if t.net.sum() > 0 else None, "avg_hold": float(t.hold.mean())}
