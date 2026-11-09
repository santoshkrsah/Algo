"""Breadth + Sector + lowest-Volume candle (BVS) intraday strategy on NSE F&O stocks, 5-minute bars.

Day direction  : advances / declines of the F&O stocks vs previous close at the close of the 9:25 bar (= 9:30).
Sector         : worst sector on a bearish day, best on a bullish day (A = average % move, B = share of stocks moving with the trend).
Stocks         : top N stocks of that sector by move in the trend direction.
Setup          : reference candle = the lowest-volume 5-min bar so far (running minimum, from the 9:30 bar). Short: ref must be green, long: red.
Entry          : stop order beyond the reference extreme (+/- 1 tick), or a 5-min close beyond it and entry at the next open.
Stop           : opposite extreme of the reference candle +/- 1 tick. Exit: half at +2R, rest on EMA(10) trail / step trail; square-off 15:15.

Everything uses data up to the close of the current bar. Stop before target inside one bar; a gap through a stop fills at the open.
`run(days, v)` -> (trades list of dict, counters). `variant(**kw)` builds the setting dict.
"""
import math
import random
from collections import Counter, defaultdict

import numpy as np

from backtest.bvs_data import NB, SECTOR_NAMES
from backtest.pdh import CFG as PCFG, order_cost

TICK = 0.05
BREADTH_BAR = 2            # bar index of the 9:25 bar (closes 9:30)
FIRST_REF_BAR = 3          # 9:30 bar
SQUARE_BAR = 72            # 15:15
CUT = {"14:30": 63, "15:00": 69}   # last bar index (start time) a new entry may be made

BASE = {
    "capital": 1_000_000, "risk_pct": 0.01, "breadth": (0.7, 1.4), "measure": "A", "top_sectors": 1, "min_sector_move": 0.0,
    "top_stocks": 2, "min_value_cr": 20.0, "color": True, "expire": 12, "exec": 1, "stop": "ref", "trail": "ema5",
    "partial_r": 2.0, "partial_frac": 0.5, "attempts": 2, "max_pos": 3, "slip": 0.0005, "cutoff": "14:30", "max_lots": 50,
    "margin_pct": 0.20, "margin_util": 0.9, "cost_max": 0.25, "max_chg": 20.0,
    # controls: breadth_on / sector_on off, sector_random, ref_mode "random"
    "breadth_on": True, "sector_on": True, "sector_random": False, "ref_mode": "lowvol", "seed": 1, "random_lookback": 12,
    "start": None, "end": None,
}
TRAILS = {"ema5": "EMA(10) 5-min", "ema15": "EMA(10) 15-min", "step": "Step trail 1R", "f1": "Pura exit 1R", "f2": "Pura exit 2R", "f3": "Pura exit 3R"}


def variant(**kw) -> dict:
    bad = set(kw) - set(BASE)
    if bad:
        raise KeyError(bad)
    return dict(BASE, **kw)


def hm(bar: int) -> str:
    t = 555 + 5 * bar
    return f"{t // 60:02d}:{t % 60:02d}"


def _ema(x: np.ndarray, n: int = 10) -> np.ndarray:
    a = 2.0 / (n + 1)
    out = np.empty(len(x))
    out[0] = x[0]
    for i in range(1, len(x)):
        out[i] = a * x[i] + (1 - a) * out[i - 1]
    return out


# ------------------------------------------------------------------------------------------------ day selection
def select(d, v: dict, rng: random.Random):
    """-> (info dict, [stock indexes]) or (info, []) when no trade today. Uses only data up to the close of the 9:25 bar."""
    c = d.c[:, BREADTH_BAR].astype(np.float64)
    chg = (c / d.prev_close - 1) * 100
    ok = np.isfinite(chg) & (np.abs(chg) < v["max_chg"]) & (d.v[:, :BREADTH_BAR + 1].sum(axis=1) > 0)
    adv, dec = int((chg[ok] > 0).sum()), int((chg[ok] < 0).sum())
    ratio = adv / dec if dec else float("inf")
    info = {"adv": adv, "dec": dec, "ratio": ratio, "dir": 0, "sector": "", "sector_move": float("nan")}
    liquid = ok & (d.avg_value_cr >= v["min_value_cr"])
    # sector stats
    secs = {}
    for s in range(len(SECTOR_NAMES)):
        ix = np.flatnonzero(ok & (d.sec == s))
        if len(ix) >= 3:
            secs[s] = (float(chg[ix].mean()), ix)
    lo, hi = v["breadth"]
    if v["breadth_on"]:
        dirn = -1 if ratio <= lo else (1 if ratio >= hi else 0)
    else:
        if not secs:
            return info, [], chg
        s_best = max(secs, key=lambda s: abs(secs[s][0]))
        dirn = 1 if secs[s_best][0] > 0 else -1
    info["dir"] = dirn
    if dirn == 0:
        return info, [], chg
    if not v["sector_on"]:
        pool = np.flatnonzero(liquid & (chg * dirn > 0))
        pool = pool[np.argsort(-(chg[pool] * dirn), kind="stable")]
        return info, list(pool[: v["top_stocks"]]), chg
    if not secs:
        return info, [], chg
    if v["sector_random"]:
        chosen = rng.sample(sorted(secs), min(v["top_sectors"], len(secs)))
    else:
        def score(s):
            a, ix = secs[s]
            if v["measure"] == "A":
                return a * dirn
            share = float((chg[ix] * dirn > 0).mean())
            return share + 1e-6 * a * dirn
        chosen = sorted(secs, key=score, reverse=True)[: v["top_sectors"]]
        chosen = [s for s in chosen if secs[s][0] * dirn >= v["min_sector_move"] and secs[s][0] * dirn > 0]
    if not chosen:
        return info, [], chg
    info["sector"] = "+".join(SECTOR_NAMES[s] for s in chosen)
    info["sector_move"] = float(np.mean([secs[s][0] for s in chosen]))
    stocks = []
    for s in chosen:
        ix = np.array([i for i in secs[s][1] if liquid[i] and chg[i] * dirn > 0], dtype=int)
        ix = ix[np.argsort(-(chg[ix] * dirn), kind="stable")]
        stocks += list(ix[: v["top_stocks"]])
    return info, stocks, chg


# ------------------------------------------------------------------------------------------------ one stock, one day
def _fill_cost(qty, px, buy, day):
    return order_cost(qty * px, buy, day)["total"]


def sim_stock(d, i: int, dirn: int, v: dict, rng: random.Random, cnt: Counter, rank: int, info: dict, chg_i: float) -> list:
    O, H, L, C, V = (x[i].astype(np.float64) for x in (d.o, d.h, d.l, d.c, d.v))
    lot = int(d.lot[i])
    cap, last_entry = v["capital"], CUT[v["cutoff"]]
    emaC5 = _ema(C) if v["trail"] == "ema5" else None
    if v["trail"] == "ema15":
        c15 = C[2::3]
        e15 = _ema(c15)
    trades, attempts = [], 0
    seen = set()           # skipped setups are counted once per reference candle
    ref, k = None, FIRST_REF_BAR
    side = "LONG" if dirn > 0 else "SHORT"
    random_ref = v["ref_mode"] == "random"
    while k < min(last_entry, NB - 2) and attempts < v["attempts"]:
        # --- update the reference candle with the bar that just closed (k)
        if random_ref:
            pool = list(range(max(FIRST_REF_BAR, k - v["random_lookback"] + 1), k + 1))
            ref_use = ref_prev = rng.choice(pool)
        else:
            ref_prev = ref
            if V[k] > 0 and (ref is None or V[k] < V[ref]):
                ref = k
            elif ref is not None and k - ref > v["expire"]:
                ref = None
            ref_use = ref
        if v["exec"] == 1:
            if ref_use is None:
                k += 1
                continue
            use_ref, ebar = ref_use, k + 1
        else:       # exec 2: the bar that just closed (k) must have closed beyond the PREVIOUS reference
            if ref_prev is None or ref_prev >= k:
                k += 1
                continue
            use_ref, ebar = ref_prev, k + 1
        if v["color"] and not random_ref:
            green = C[use_ref] > O[use_ref]
            if (dirn < 0 and not green) or (dirn > 0 and green):
                k += 1
                continue
        rh, rl = H[use_ref], L[use_ref]
        if dirn < 0:
            trig, stop0 = rl - TICK, rh + TICK
        else:
            trig, stop0 = rh + TICK, rl - TICK
        if ebar > last_entry or ebar >= SQUARE_BAR:
            break
        slip = v["slip"]
        if v["exec"] == 1:
            if dirn < 0:
                if L[ebar] > trig:
                    k += 1
                    continue
                fill = min(O[ebar], trig)
            else:
                if H[ebar] < trig:
                    k += 1
                    continue
                fill = max(O[ebar], trig)
            first_bar = ebar              # the entry bar itself is managed (stop first)
        else:
            if (dirn < 0 and C[k] >= rl) or (dirn > 0 and C[k] <= rh):
                k += 1
                continue
            fill = O[ebar]
            if v["stop"] == "break":     # breakout bar's extreme (bar k is complete, so this is known)
                stop0 = (H[k] + TICK) if dirn < 0 else (L[k] - TICK)
            first_bar = ebar + 0          # entered at the open of ebar
        entry = fill * (1 - slip) if dirn < 0 else fill * (1 + slip)       # worse for the trader
        risk = (stop0 - entry) if dirn < 0 else (entry - stop0)
        if risk <= 0:
            if (use_ref, "skip_bad_risk") not in seen:
                seen.add((use_ref, "skip_bad_risk"))
                cnt["skip_bad_risk"] += 1
            k += 1
            continue
        # --- size
        risk_amt = v["risk_pct"] * cap
        lots = int(risk_amt // (risk * lot))
        mlots = int(cap * v["margin_util"] / v["max_pos"] // (v["margin_pct"] * lot * entry))
        lots = min(lots, v["max_lots"], mlots)
        if lots < 1:
            if (use_ref, "skip_lots") not in seen:
                seen.add((use_ref, "skip_lots"))
                cnt["skip_lots"] += 1
            k += 1
            continue
        qty = lots * lot
        cost_rt = (_fill_cost(qty, entry, dirn > 0, d.day) + _fill_cost(qty, entry, dirn < 0, d.day)) / qty + 2 * slip * entry
        if cost_rt > v["cost_max"] * risk:
            if (use_ref, "skip_cost") not in seen:
                seen.add((use_ref, "skip_cost"))
                cnt["skip_cost"] += 1
            k += 1
            continue
        attempts += 1
        cnt["entries"] += 1
        tr = _manage(d, i, dirn, v, O, H, L, C, entry, fill, stop0, risk, lots, lot, first_bar, ebar, v["exec"] == 1,
                     emaC5 if v["trail"] == "ema5" else (e15 if v["trail"] == "ema15" else None))
        tr.update({"date": d.day, "symbol": d.syms[i], "side": side, "rank": rank, "attempt": attempts, "breadth": info["ratio"],
                   "adv": info["adv"], "dec": info["dec"], "sector": info["sector"], "sector_move": info["sector_move"],
                   "chg925": chg_i, "ref_t": hm(use_ref), "ref_vol": float(V[use_ref]), "ref_color": "green" if C[use_ref] > O[use_ref] else "red",
                   "signal_t": hm(k), "entry_t": hm(ebar), "lot": lot, "lots": lots, "stop_pct": risk / entry * 100,
                   "risk_px": risk, "ref_lo": float(rl), "ref_hi": float(rh)})
        trades.append(tr)
        if tr["reason"] == "SL":
            ref = None                       # fresh setup: running minimum restarts after the stop-out
            k = tr["exit_bar"] + 1
            continue
        break
    return trades


def _manage(d, i, dirn, v, O, H, L, C, entry, fill, stop0, risk, lots, lot, first_bar, ebar, same_bar, ema):
    """Walk the bars after entry. Returns the trade dict (exit prices include slippage, all costs included)."""
    day, slip, qty = d.day, v["slip"], lots * lot
    sgn = dirn
    tgt = lambda r: entry + sgn * r * risk                       # noqa: E731
    stop = stop0
    part_lots = 0
    part = None                                                   # (bar, px, lots)
    trail = v["trail"]
    fixed = {"f1": 1.0, "f2": 2.0, "f3": 3.0}.get(trail)
    pr = v["partial_r"]
    can_part = fixed is None and lots >= 2
    be_armed = False          # stop moved to entry (after +pr R)
    best = entry
    mae = 0.0
    exit_bar, exit_px, reason = None, None, None
    pending_exit = None       # bar at whose OPEN we exit (signal on a close)
    for b in range(first_bar, SQUARE_BAR + 1):
        o, h, l, c = O[b], H[b], L[b], C[b]
        if pending_exit is not None and b == pending_exit[0]:
            exit_bar, exit_px, reason = b, o * (1 - sgn * slip), pending_exit[1]
            break
        if b == SQUARE_BAR:
            exit_bar, exit_px, reason = b, o * (1 - sgn * slip), "EOD"
            break
        # stop first (gap through the stop fills at the open)
        in_entry_bar = same_bar and b == ebar
        if not in_entry_bar and ((sgn < 0 and o >= stop) or (sgn > 0 and o <= stop)):
            exit_bar, exit_px, reason = b, o * (1 - sgn * slip), "SL" if not be_armed else "BE"
            break
        if (sgn < 0 and h >= stop) or (sgn > 0 and l <= stop):
            exit_bar, exit_px, reason = b, stop * (1 - sgn * slip), "SL" if not be_armed else "BE"
            break
        # adverse excursion
        adv = (h - entry) if sgn < 0 else (entry - l)
        mae = max(mae, adv)
        fav_px = l if sgn < 0 else h
        best = min(best, fav_px) if sgn < 0 else max(best, fav_px)
        # targets
        if fixed is not None:
            t = tgt(fixed)
            if (sgn < 0 and l <= t) or (sgn > 0 and h >= t):
                px = min(o, t) if sgn < 0 else max(o, t)
                exit_bar, exit_px, reason = b, px * (1 - sgn * slip), f"T{int(fixed)}R"
                break
        elif not be_armed:
            t = tgt(pr)
            if (sgn < 0 and l <= t) or (sgn > 0 and h >= t):
                px = min(o, t) if sgn < 0 else max(o, t)
                if can_part:
                    part_lots = max(1, int(lots * v["partial_frac"]))
                    part = (b, px * (1 - sgn * slip), part_lots)
                be_armed = True
                stop = entry          # breakeven from the NEXT bar on
                # (the stop move is effective from b+1 because the loop checks the stop first on each new bar)
        # trailing on close of this bar (only after the partial)
        if be_armed and fixed is None and b < SQUARE_BAR - 1:
            if trail == "step":
                favR = ((entry - best) if sgn < 0 else (best - entry)) / risk
                lvl = max(0, math.floor(favR - pr))
                new = entry + sgn * lvl * risk
                if (sgn < 0 and new < stop) or (sgn > 0 and new > stop):
                    stop = new
            elif trail == "ema5":
                if (sgn < 0 and c > ema[b]) or (sgn > 0 and c < ema[b]):
                    pending_exit = (b + 1, "EMA")
            elif trail == "ema15":
                if b % 3 == 2 and ((sgn < 0 and c > ema[b // 3]) or (sgn > 0 and c < ema[b // 3])):
                    pending_exit = (b + 1, "EMA")
    if exit_bar is None:
        exit_bar, exit_px, reason = SQUARE_BAR, O[SQUARE_BAR] * (1 - sgn * slip), "EOD"
    # --- P&L
    rest = lots - part_lots
    legs = []                      # (qty, exit px, bar)
    if part:
        legs.append((part_lots * lot, part[1], part[0]))
    legs.append((rest * lot, exit_px, exit_bar))
    gross = sum(q * (p - entry) * sgn for q, p, _ in legs)
    # entry fill is already worse by slippage: slippage cost in Rs = distance between the raw fill and the filled price, both ends
    slip_rs = qty * abs(entry - fill) + sum(q * abs(p - p / (1 - sgn * slip)) for q, p, _ in legs)
    fees = _fill_cost(qty, entry, sgn > 0, day) + sum(_fill_cost(q, p, sgn < 0, day) for q, p, _ in legs)
    net = gross - fees
    wavg_exit = sum(q * p for q, p, _ in legs) / qty
    return {"entry_px": entry, "stop_px": stop0, "exit_px": wavg_exit, "exit_t": hm(exit_bar), "exit_bar": exit_bar, "reason": reason,
            "part_t": hm(part[0]) if part else "", "part_px": part[1] if part else float("nan"), "part_lots": part_lots,
            "shares": qty, "notional": qty * entry, "gross_pnl": gross, "slippage": slip_rs, "fees": fees, "net_pnl": net,
            "R": net / (risk * qty), "R_gross": gross / (risk * qty), "mae_r": mae / risk, "entry_bar": ebar,
            "ret_pct": net / (qty * entry) * 100, "gross_pct": gross / (qty * entry) * 100, "fill_raw": fill}


# ------------------------------------------------------------------------------------------------ all days
def run(days: list, v: dict):
    cnt = Counter()
    out = []
    for d in days:
        if (v["start"] and d.day < v["start"]) or (v["end"] and d.day > v["end"]):
            continue
        rng = random.Random(f"{v['seed']}-{d.day}")
        cnt["days"] += 1
        res = select(d, v, rng)
        info, stocks, chg = res
        if info["dir"] == 0:
            cnt["days_no_dir"] += 1
            continue
        if not stocks:
            cnt["days_no_sector"] += 1
            continue
        cnt["days_traded_sel"] += 1
        dtr = []
        for rk, i in enumerate(stocks, 1):
            r2 = random.Random(f"{v['seed']}-{d.day}-{d.syms[i]}")
            dtr += sim_stock(d, i, info["dir"], v, r2, cnt, rk, info, float(chg[i]))
        # max concurrent positions: process by entry time, drop entries when the book is full
        dtr.sort(key=lambda t: (t["entry_bar"], t["rank"]))
        book = []
        for t in dtr:
            book = [e for e in book if e > t["entry_bar"]]
            if len(book) >= v["max_pos"]:
                cnt["skip_maxpos"] += 1
                continue
            book.append(t["exit_bar"])
            out.append(t)
    return out, cnt
