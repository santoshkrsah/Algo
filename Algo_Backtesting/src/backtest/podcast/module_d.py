"""Module D: previous-session support / resistance breakout, run on the UNDERLYING (NIFTY / BANKNIFTY futures).

Fyers does not serve expired option contracts or their intraday history, so the option legs (ATM call / put, the
Rs 80-premium strike, premium stops of 25/35/50%) cannot be tested. The signal and the exits run on the futures and
results are reported in index points (and in Rs with the common 0.25%-risk sizing).

Levels from the previous session (same futures series): classic pivot R1 / S1, or previous day high / low.
Entry: a candle closes above the resistance (previous candle at or below it) -> LONG at the next open (= buy call);
closes below the support -> SHORT (= buy put). Each level once a day, max 2 trades a day, no entry from 15:00.
Stop modes: 'level' = hard stop `buffer`% of price beyond the level (R = entry - stop); 'close_back' = exit at the next
open after a candle closes back inside the level (R for sizing / targets = distance to level - buffer).
Target = rr x R. Everything closes at the 15:15 candle's open.
"""
import numpy as np
import pandas as pd

from backtest.podcast import engine, indicators as ind
from backtest.podcast.config import CONFIG

KEY = "D"
NAME = "S/R level breakout (underlying)"
FOLDER = "podcast_d_level_breakout"
P = CONFIG["modules"]["D"]
LEVEL_TXT = {"pivot": "pivot R1 / S1", "pdhl": "kal ka high / low"}
STOP_TXT = {"level": "level ke paar hard stop", "close_back": "candle level ke andar band ho to exit"}
_LV: dict = {}


def slices() -> list[dict]:
    return [{"tf": tf, "level": lv, "stop_mode": sm} for tf in P["tfs"] for lv in P["levels"] for sm in P["stop_modes"]]


def grid1(sl: dict) -> tuple[list, list]:
    return ["buffer", "rr"], [{"buffer": b, "rr": r} for b in P["grid_buffer"] for r in P["grid_rr"]]


def grid2(sl: dict, chosen: dict):
    return None


def label(sl: dict, p: dict) -> str:
    return f"{sl['tf']} {LEVEL_TXT[sl['level']]}, {STOP_TXT[sl['stop_mode']]}, buffer {p['buffer']:g}%, target {p['rr']:g}R"


def warm():
    pass


def _levels(b, kind: str):
    """Resistance / support per day from the previous day's candles of the same series (adjusted prices)."""
    k = (b.name, b.tf, b.n, kind)
    if k in _LV:
        return _LV[k]
    nd = len(b.days)
    H = np.array([b.h[b.first[d]:b.last[d] + 1].max() for d in range(nd)])
    L = np.array([b.l[b.first[d]:b.last[d] + 1].min() for d in range(nd)])
    C = b.c[b.last]
    ph, pl, pc = np.r_[np.nan, H[:-1]], np.r_[np.nan, L[:-1]], np.r_[np.nan, C[:-1]]
    if kind == "pivot":
        pv = ind.pivots(ph, pl, pc)
        res, sup = pv["R1"], pv["S1"]
    else:
        res, sup = ph, pl
    _LV[k] = (res, sup)
    return _LV[k]


def _manage(t, b, j, stop, target, level, mode, slip, entry_bar) -> bool:
    s = t["side"]
    if j > entry_bar:
        if np.isfinite(stop) and ((b.o[j] <= stop) if s > 0 else (b.o[j] >= stop)):
            engine.close_leg(t, b, j, b.o[j], 1.0, "stop gap", slip, when="open")
            return True
        if b.minute[j] >= CONFIG["eod_exit"]:
            engine.close_leg(t, b, j, b.o[j], 1.0, "15:15 exit", slip, when="open")
            return True
        if t.get("_exit_next"):
            engine.close_leg(t, b, j, b.o[j], 1.0, "back inside", slip, when="open")
            return True
    if np.isfinite(stop) and ((b.l[j] <= stop) if s > 0 else (b.h[j] >= stop)):
        engine.close_leg(t, b, j, stop, 1.0, "stop", slip)
        return True
    if (b.h[j] >= target) if s > 0 else (b.l[j] <= target):
        engine.close_leg(t, b, j, target, 1.0, "target", slip)
        return True
    if mode == "close_back" and ((b.c[j] < level) if s > 0 else (b.c[j] > level)):
        t["_exit_next"] = True                          # closed back inside: out at the next open
    return False


def _run(b, sl: dict, p: dict, slip) -> list:
    res, sup = _levels(b, sl["level"])
    buf = p["buffer"] / 100.0
    o, c = b.o, b.c
    trades = []
    for d in range(len(b.days)):
        i0, i1 = int(b.first[d]), int(b.last[d])
        r, s_ = res[d], sup[d]
        if not (np.isfinite(r) and np.isfinite(s_)):
            continue
        done = {1: False, -1: False}
        n_today = 0
        i = i0
        while i < i1 and n_today < P["max_trades_day"]:
            if b.minute[i + 1] >= P["last_entry"]:
                break
            prev = c[i - 1] if i > 0 else np.nan
            side = 0
            if not done[1] and c[i] > r and prev <= r:
                side = 1
            elif not done[-1] and c[i] < s_ and prev >= s_:
                side = -1
            if side == 0:
                i += 1
                continue
            e = i + 1
            level = r if side > 0 else s_
            entry = o[e] + side * slip[e]
            raw = entry - b.off[e]
            nominal = level - side * raw * buf                # level - buffer (long)
            risk = (entry - nominal) * side
            done[side] = True
            if risk <= 0:
                i += 1
                continue
            stop = nominal if sl["stop_mode"] == "level" else np.nan
            target = entry + side * p["rr"] * risk
            t = engine.new_trade(b, side, i, e, o[e], slip, stop, target, risk_pts=risk, level=level, stop_pct=risk / raw * 100)
            n_today += 1
            j = e
            while True:
                if _manage(t, b, j, stop, target, level, sl["stop_mode"], slip, e - 1):
                    break
                if j >= i1:
                    engine.close_leg(t, b, j, c[j], 1.0, "day close", slip)
                    break
                j += 1
            t.pop("_exit_next", None)
            trades.append(t)
            i = j + 1
    return trades


def trades(bars: dict, sl: dict, p: dict, slip_mult: float = 1.0, cache: dict | None = None) -> list:
    out = []
    for b in bars.values():
        out += _run(b, sl, p, engine.slip_array(b, slip_mult))
    return out


def random_trades(bars: dict, sl: dict, p: dict, rule, rng: np.random.Generator) -> list:
    """Same count per instrument; random candle (09:15-15:00) and side; risk drawn from the rule's own risk distances (% of
    price) with a hard stop there; same target (rr x R) and 15:15 exit."""
    out = []
    stops = rule["stop_pct"].to_numpy() if "stop_pct" in rule else np.array([0.3])
    for name, b in bars.items():
        cnt = int((rule["inst"] == name).sum())
        if not cnt:
            continue
        slip = engine.slip_array(b)
        ok = np.nonzero(b.minute < P["last_entry"])[0]
        for e, s, d in zip(rng.choice(ok, size=cnt), rng.choice([-1, 1], size=cnt), rng.choice(stops, size=cnt)):
            entry = b.o[e] + s * slip[e]
            risk = (entry - b.off[e]) * d / 100.0
            stop, target = entry - s * risk, entry + s * p["rr"] * risk
            t = engine.new_trade(b, int(s), e, e, b.o[e], slip, stop, target)
            i1 = int(b.last[b.di[e]])
            j = e
            while True:
                if _manage(t, b, j, stop, target, np.nan, "level", slip, e - 1):
                    break
                if j >= i1:
                    engine.close_leg(t, b, j, b.c[j], 1.0, "day close", slip)
                    break
                j += 1
            out.append(t)
    return out


def points(tk: pd.DataFrame) -> dict:
    """Index points per trade (1 unit, after slippage, before charges) - what Module D is asked to report."""
    out = {}
    for name, g in tk.groupby("inst"):
        pts = g["pnl_pts"]
        out[name] = {"trades": int(len(g)), "points": float(pts.sum()), "avg_points": float(pts.mean()), "win_pct": float((pts > 0).mean() * 100),
                     "per_year": {int(y): float(v) for y, v in pts.groupby(pd.to_datetime(g["entry_dt"]).dt.year).sum().items()}}
    return out


# ------------------------------------------------------------------------------------------- report texts (Hinglish)
def rules(sl: dict, p: dict, split: str) -> list[str]:
    st = (f"Stop: level ke {p['buffer']:g}% paar hard stop (long me R1 / kal ka high se neeche)." if sl["stop_mode"] == "level" else
          f"Stop: koi candle wapas level ke andar band ho (long me level ke neeche close) to agli candle ke open pe exit. R = entry se (level - {p['buffer']:g}%) tak.")
    return [
        "Option data nahi: Fyers expired option contracts aur unka intraday data nahi deta. Isliye signal aur exit NIFTY / BANKNIFTY futures pe; nateeja index points me (aur Rs me 0.25% risk size se).",
        "Jo test NAHI ho saka: ATM call / put ka premium, Rs 80 wala strike, premium pe 25 / 35 / 50% stop aur 1:1 / 1:2 / 1:3 premium target.",
        f"Level: {LEVEL_TXT[sl['level']]} - kal ke session (same futures series) se. Pivot = (H + L + C) / 3, R1 = 2 x Pivot - L, S1 = 2 x Pivot - H.",
        f"Candle {sl['tf']}: close resistance ke upar (pichhli candle neeche thi) -> LONG agli candle ke open pe (= call kharido). Close support ke neeche -> SHORT (= put kharido).",
        "Har level din me ek baar, din me max 2 trade, 15:00 ke baad nayi entry nahi, 15:15 pe sab band.",
        st,
        f"Target: {p['rr']:g} x R. Stop aur target ek candle me -> stop.",
        f"Period Feb 2018 se 9 Nov 2026; pehla 60% (2018 - {split} se pehle) = setting chunne ke liye, baaki 40% = test.",
    ]


def setting_chips(sl: dict, p: dict) -> list:
    return [["Underlying", "NIFTY + BANKNIFTY futures (options ka data nahi)", 1], ["Candle", sl["tf"], 1], ["Level", LEVEL_TXT[sl["level"]], 1],
            ["Stop", STOP_TXT[sl["stop_mode"]] + f", buffer {p['buffer']:g}%", 1], ["Target", f"{p['rr']:g} x R", 1], ["Trade / din", f"max {P['max_trades_day']}", 0]]


def formulas(sl: dict, p: dict) -> list:
    return [["Pivot", "P = (kal H + kal L + kal C) / 3;  R1 = 2P - kal L;  S1 = 2P - kal H"],
            ["PDH / PDL", "kal ka high / low (same futures series, roll ke baad bhi same contract jaisa)"],
            ["Signal", "close[t] > resistance aur close[t-1] <= resistance -> long; support ke liye ulta"],
            ["Entry fill", "open[t+1] + side x slippage (NIFTY 1.5 pt, BANKNIFTY 3 pt)"],
            ["R", "R = |entry - (level - side x asli_price x buffer%)|"],
            ["Stop", "level mode: stop = level - side x price x buffer%;  close_back: close level ke andar -> agla open"],
            ["Target", "entry + side x rr x R"],
            ["Points", "side x (exit - entry) per unit, slippage ke baad, charges se pehle"],
            ["Qty (Rs result)", "floor( capital x 0.25% / R )  units"],
            ["Charges", "Rs 20/order + STT sell (date se) + NSE 0.00173% + SEBI + stamp 0.002% + GST 18%"],
            ["Win rate / PF / expectancy", "net > 0 trades %;  sab profit / sab loss;  kul net / trades"],
            ["Plateau", "setting + padosi (buffer aur rr ek step upar-neeche) sab ka fit PF > 1"],
            ["Random control", "1,000 baar: wahi count, random candle + side, rule ki R-distances me se random stop, wahi target"]]


def podcast_rows(grid) -> list:
    out = []
    g = grid[(grid["grid"] == 1) & grid["rr"].isin([1.0, 2.0, 3.0]) & (grid["buffer"] == 0.1)]
    for r in g.itertuples(index=False):
        sl = {"tf": r.tf, "level": r.level, "stop_mode": r.stop_mode}
        out.append({"label": label(sl, {"buffer": r.buffer, "rr": r.rr}), "is_trades": r.is_trades, "is_pf": r.is_pf, "is_exp_R": r.is_exp_R,
                    "oos_trades": r.oos_trades, "oos_pf": r.oos_pf, "oos_exp_R": r.oos_exp_R, "oos_net": r.oos_net, "status": r.status})
    return out


def quick():
    P.update({"tfs": ["5m"], "levels": ["pivot"], "stop_modes": ["level"], "grid_buffer": [0.1, 0.15], "grid_rr": [1.0, 2.0]})
