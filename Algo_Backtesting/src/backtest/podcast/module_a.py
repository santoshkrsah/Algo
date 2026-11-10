"""Module A: opening price vs previous close (intraday).

09:15 open > previous close -> BUY at the open; open < previous close -> SELL. Stop / target from the entry price;
neither hit -> exit at the day's close (15:29 candle close). Exits checked on 1-minute candles (stop first when one
candle touches both; a candle opening beyond the stop fills at its open).
Slices: stop / target in % of price; in ATR(14) multiples; in multiples of the 20-day standard deviation of daily returns.
Index futures (own continuous series) + 10 stocks cash (MIS), 2018-02 .. 2026-11.
"""
import numpy as np
import pandas as pd

from backtest.podcast import data, engine, indicators as ind
from backtest.podcast.config import CONFIG

KEY = "A"
NAME = "Opening vs previous close"
FOLDER = "podcast_a_open_gap"
P = CONFIG["modules"]["A"]
M = 375
TYPE_TXT = {"pct": "% of price", "atr": "x ATR(14)", "std": "x stdev(20) of daily returns"}
_PREP: dict = {}


def slices() -> list[dict]:
    return [{"stop_type": s, "tf": "1m"} for s in ("pct", "atr", "std")]


def grid1(sl: dict) -> tuple[list, list]:
    if sl["stop_type"] == "pct":
        return ["stop", "target"], [{"stop": s, "target": t} for s in P["grid_stop"] for t in P["grid_target"]]
    return ["k_stop", "k_target"], [{"k_stop": s, "k_target": t} for s in P["grid_k_stop"] for t in P["grid_k_target"]]


def grid2(sl: dict, chosen: dict):
    return None


def label(sl: dict, p: dict) -> str:
    if sl["stop_type"] == "pct":
        return f"gap side, stop {p['stop']:g}% / target {p['target']:g}%, exit close"
    return f"gap side, stop {p['k_stop']:g} / target {p['k_target']:g} {TYPE_TXT[sl['stop_type']]}, exit close"


def _prep(b) -> dict:
    """Per instrument: minute matrices (days x 375) of open / high / low / bar index, and per day the 09:15 open, previous
    close (same contract, adjusted space), ATR(14) and stdev(20) of daily returns from the previous days only."""
    key = (b.name, b.kind, b.n)
    if key in _PREP:
        return _PREP[key]
    nd = len(b.days)
    mi = b.minute - 555
    O = np.full((nd, M), np.nan)
    H = np.full((nd, M), np.nan)
    L = np.full((nd, M), np.nan)
    IX = np.full((nd, M), -1, dtype=np.int64)
    O[b.di, mi], H[b.di, mi], L[b.di, mi], IX[b.di, mi] = b.o, b.h, b.l, np.arange(b.n)
    d1 = data.bars(b.name, "1D", kind=b.kind, start="2017-06-01")
    k = np.searchsorted(d1.days, b.days)
    ok = (k >= 21) & (k < len(d1.days))
    ok[ok] &= d1.days[k[ok]] == b.days[ok]
    kk = np.where(ok, k, 21)
    first = IX[:, 0]
    ok &= first >= 0                                              # the 09:15 candle must exist
    f0 = np.where(first >= 0, first, 0)
    prev_raw = d1.c[kk - 1] - d1.off[kk]                          # previous close of TODAY's contract (real)
    prev_adj = prev_raw + b.off[f0]
    atr = ind.atr(d1.h, d1.l, d1.c, P["atr_n"])[kk - 1]
    raw_c = d1.c - d1.off
    ret = np.r_[np.nan, np.diff(d1.c) / raw_c[:-1]]
    sd = ind.rolling_std(ret, P["std_n"])[kk - 1]
    last = np.where(IX >= 0, np.arange(M)[None, :], -1).max(axis=1)
    out = {"O": O, "H": H, "L": L, "IX": IX, "ok": ok & np.isfinite(atr) & np.isfinite(sd) & np.isfinite(prev_adj), "o0": O[:, 0], "first": f0,
           "prev_adj": prev_adj, "prev_raw": prev_raw, "atr": atr, "sd": sd, "last": last}
    _PREP[key] = out
    return out


def warm():
    """Build the daily caches in the main process (the workers only read them)."""
    for n, kd in data.universe(KEY):
        data.bars(n, "1D", kind=kd, start="2017-06-01")


def _cell(b, sl: dict, p: dict, slip: np.ndarray, side_override: np.ndarray | None = None) -> list:
    q = _prep(b)
    ok = q["ok"].copy()
    gap = q["o0"] - q["prev_adj"]
    side = np.sign(gap) if side_override is None else side_override
    ok &= side != 0
    if side_override is None and P["min_gap_pct"] > 0:
        ok &= np.abs(gap) / q["prev_raw"] * 100 > P["min_gap_pct"]
    days = np.nonzero(ok)[0]
    if not len(days):
        return []
    s = side[days].astype(int)
    f0 = q["first"][days]
    entry = q["o0"][days] + s * slip[f0]
    raw = entry - b.off[f0]
    if sl["stop_type"] == "pct":
        sd_, td_ = raw * p["stop"] / 100.0, raw * p["target"] / 100.0
    elif sl["stop_type"] == "atr":
        sd_, td_ = q["atr"][days] * p["k_stop"], q["atr"][days] * p["k_target"]
    else:
        base = q["sd"][days] * q["prev_raw"][days]
        sd_, td_ = base * p["k_stop"], base * p["k_target"]
    stop, target = entry - s * sd_, entry + s * td_
    Hm, Lm, Om = q["H"][days], q["L"][days], q["O"][days]
    long = s > 0
    hs = np.where(long[:, None], Lm <= stop[:, None], Hm >= stop[:, None])
    ht = np.where(long[:, None], Hm >= target[:, None], Lm <= target[:, None])
    fs = np.where(hs.any(axis=1), hs.argmax(axis=1), M)
    ft = np.where(ht.any(axis=1), ht.argmax(axis=1), M)
    out = []
    IX, last = q["IX"][days], q["last"][days]
    for n in range(len(days)):
        e = int(f0[n])
        t = engine.new_trade(b, int(s[n]), e, e, q["o0"][days[n]], slip, float(stop[n]), float(target[n]))
        if fs[n] < M and fs[n] <= ft[n]:
            j = int(fs[n])
            gap_fill = j > 0 and ((Om[n, j] <= stop[n]) if s[n] > 0 else (Om[n, j] >= stop[n]))
            engine.close_leg(t, b, int(IX[n, j]), float(Om[n, j]) if gap_fill else float(stop[n]), 1.0, "stop gap" if gap_fill else "stop", slip)
        elif ft[n] < M:
            engine.close_leg(t, b, int(IX[n, int(ft[n])]), float(target[n]), 1.0, "target", slip)
        else:
            xi = int(IX[n, int(last[n])])
            engine.close_leg(t, b, xi, float(b.c[xi]), 1.0, "day close", slip)
        out.append(t)
    return out


def trades(bars: dict, sl: dict, p: dict, slip_mult: float = 1.0, cache: dict | None = None) -> list:
    out = []
    for b in bars.values():
        out += _cell(b, sl, p, engine.slip_array(b, slip_mult))
    return out


def random_trades(bars: dict, sl: dict, p: dict, rule, rng: np.random.Generator) -> list:
    """Same days, same entry (09:15 open), same stop / target, but the side is a coin flip instead of the gap side."""
    out = []
    for b in bars.values():
        q = _prep(b)
        out += _cell(b, sl, p, engine.slip_array(b), side_override=rng.choice([-1, 1], size=len(q["ok"])))
    return out


def long_history(sl: dict, p: dict) -> dict | None:
    """Same rule on NIFTY 50 / Bank Nifty INDEX daily candles from 2005 (signal check; an index cannot be traded). Only
    daily candles here, so a day that touches both stop and target counts as a stop (worst case)."""
    from backtest.podcast import evaluate, portfolio
    bars = {n: data.bars(n, "1D", kind="index", start=CONFIG["data"]["long_start"]) for n in ("NIFTY50", "BANKNIFTY")}
    out = []
    for b in bars.values():
        slip = engine.slip_array(b)
        atr = ind.atr(b.h, b.l, b.c, P["atr_n"])
        ret = np.r_[np.nan, np.diff(b.c) / b.c[:-1]]
        sd = ind.rolling_std(ret, P["std_n"])
        for i in range(21, b.n):
            g = b.o[i] - b.c[i - 1]
            if g == 0:
                continue
            s = 1 if g > 0 else -1
            entry = b.o[i] + s * slip[i]
            if sl["stop_type"] == "pct":
                d1, d2 = entry * p["stop"] / 100, entry * p["target"] / 100
            elif sl["stop_type"] == "atr":
                d1, d2 = atr[i - 1] * p["k_stop"], atr[i - 1] * p["k_target"]
            else:
                d1, d2 = sd[i - 1] * b.c[i - 1] * p["k_stop"], sd[i - 1] * b.c[i - 1] * p["k_target"]
            if not (np.isfinite(d1) and np.isfinite(d2)):
                continue
            stop, target = entry - s * d1, entry + s * d2
            t = engine.new_trade(b, s, i, i, b.o[i], slip, stop, target)
            hs = b.l[i] <= stop if s > 0 else b.h[i] >= stop
            ht = b.h[i] >= target if s > 0 else b.l[i] <= target
            if hs:
                engine.close_leg(t, b, i, stop, 1.0, "stop", slip)
            elif ht:
                engine.close_leg(t, b, i, target, 1.0, "target", slip)
            else:
                engine.close_leg(t, b, i, b.c[i], 1.0, "day close", slip)
            out.append(t)
    df = engine.frame(out, bars)
    if not len(df):
        return None
    acc = portfolio.apply(df)
    tk = acc[acc["taken"]]
    days = np.unique(np.concatenate([b.days for b in bars.values()]))
    dp = portfolio.daily_equity(tk, bars, days)
    pre = tk[pd.to_datetime(tk["entry_dt"]) < "2018-01-01"]
    y = evaluate.yearly(tk, dp, CONFIG["capital"])
    return {"period": f"{str(days[0])} .. {str(days[-1])}", "full": {**evaluate.trade_stats(tk), **evaluate.equity_stats(dp, CONFIG["capital"])},
            "before_2018": evaluate.trade_stats(pre), "yearly": y[["year", "net", "return_pct", "trades", "pf"]].to_dict("records"),
            "note": "daily candles: stop aur target dono same din -> stop (sabse bura maan ke)"}


# ------------------------------------------------------------------------------------------- report texts (Hinglish)
def _dist_txt(sl, p):
    if sl["stop_type"] == "pct":
        return f"stop entry ka {p['stop']:g}%, target {p['target']:g}%"
    return f"stop {p['k_stop']:g} x {('ATR(14)' if sl['stop_type'] == 'atr' else 'stdev(20) of daily returns x pichhla close')}, target {p['k_target']:g} x wahi"


def rules(sl: dict, p: dict, split: str) -> list[str]:
    return [
        "Instruments: NIFTY aur BANKNIFTY futures (apni continuous series, roll 3 din pehle) + 10 bade stocks cash me intraday (MIS).",
        f"Period Feb 2018 se 9 Nov 2026. Pehla 60% (2018 - {split} se pehle) = setting chunne ke liye, baaki 40% = test.",
        "Har din 09:15 ka open dekho: open > kal ka close -> BUY 09:15 open pe; open < kal ka close -> SELL (short). Barabar ho to trade nahi.",
        "Kal ka close = kal ka official daily close (futures me aaj wale contract ka).",
        f"Exit: {_dist_txt(sl, p)}. Dono nahi lage to din ke close (15:29 candle) pe exit.",
        "Stop / target 1-minute candles pe check: ek candle me dono lage to stop maana; candle stop ke paar khule to us open pe exit.",
        "Har fill pe slippage (NIFTY 1.5 pt, BANKNIFTY 3 pt, stocks 0.03%); charges futures / intraday cash ke hisaab se.",
        "Size: stop lage to capital ka 0.25% (Rs 2,500). Din ka loss limit 0.5%: saari trades 09:15 pe hi khulti hain, isliye ye limit yahan kabhi nahi rokti.",
    ]


def setting_chips(sl: dict, p: dict) -> list:
    return [["Entry", "09:15 open, gap ki side", 1], ["Stop / target", _dist_txt(sl, p), 1], ["Exit", "stop / target ya din ka close", 0],
            ["Exit check", "1-minute candles", 0]]


def formulas(sl: dict, p: dict) -> list:
    return [["Gap", "gap = open(09:15) - kal ka close;  side = +1 agar gap > 0, -1 agar gap < 0"],
            ["Entry fill", "open(09:15) + side x slippage"],
            ["Stop distance", {"pct": "asli_entry x stop%", "atr": "k_stop x ATR(14) (kal tak ke din)", "std": "k_stop x stdev(20 din ke daily return) x kal ka close"}[sl["stop_type"]]],
            ["ATR(14)", "Wilder: ATR = (ATR_kal x 13 + TR) / 14;  TR = max(H - L, |H - kal C|, |L - kal C|)"],
            ["Stop / target", "stop = entry - side x stop distance;  target = entry + side x target distance"],
            ["Exit", "pehli 1-minute candle jo stop ya target chhue (dono -> stop); warna 15:29 candle ka close"],
            ["Exit fill", "level - side x slippage; candle stop ke paar khule to us candle ka open"],
            ["Qty", "floor( min( capital x 0.25% / stop distance , 5 x capital / asli_entry ) )"],
            ["Charges (futures)", "Rs 20/order + STT sell 0.01-0.05% (date se) + NSE 0.00173% + SEBI + stamp 0.002% + GST 18%"],
            ["Charges (stock MIS)", "min(Rs 20, 0.03%)/order + STT sell 0.025% + NSE 0.00297% + SEBI + stamp 0.003% + GST 18%"],
            ["Net P&L / R", "net = qty x side x (exit - entry) - charges;  R = net / (qty x stop distance)"],
            ["Win rate / PF / expectancy", "net > 0 trades %;  sab profit / sab loss;  kul net / trades"],
            ["Return % / CAGR", "net / 10 lakh;  (aakhri equity / 10 lakh)^(365/din) - 1"],
            ["Max / time drawdown", "peak equity se sabse bada gir; do equity high ke beech sabse lamba samay"],
            ["Plateau", "setting + padosi (stop / target ek step upar-neeche) sab ka fit PF > 1"],
            ["Random control", "wahi din, wahi stop / target, side sikka uchhaal ke (1,000 baar); p = (random >= rule + 1) / 1001"]]


def podcast_rows(grid) -> list:
    out = []
    g = grid[grid["grid"] == 1]
    for r in g.itertuples(index=False):
        sl = {"stop_type": r.stop_type, "tf": r.tf}
        if r.stop_type == "pct" and (r.stop, r.target) in [tuple(x) for x in P["pairs"]]:
            p = {"stop": r.stop, "target": r.target}
        elif r.stop_type != "pct" and (r.k_stop, r.k_target) == (1.0, 2.0):
            p = {"k_stop": r.k_stop, "k_target": r.k_target}
        else:
            continue
        out.append({"label": f"{sl['stop_type']}: " + label(sl, p), "is_trades": r.is_trades, "is_pf": r.is_pf, "is_exp_R": r.is_exp_R, "oos_trades": r.oos_trades,
                    "oos_pf": r.oos_pf, "oos_exp_R": r.oos_exp_R, "oos_net": r.oos_net, "status": r.status})
    return out


def quick():
    P.update({"grid_stop": [0.9, 1.0, 1.1], "grid_target": [1.8, 2.0, 2.2], "grid_k_stop": [0.9, 1.0], "grid_k_target": [1.5, 2.0]})
