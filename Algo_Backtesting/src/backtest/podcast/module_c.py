"""Module C: Dow-theory swing breakout (intraday, 3- and 5-minute candles).

Swing high = a candle whose high is above the N candles on each side (swing low mirrored); it is only KNOWN N candles
later. LONG when price breaks the last known swing high, SHORT when it breaks the last known swing low ("fresh" break:
the candle before was on the other side, and a swing level is traded at most once a day). Stop = the opposite swing;
skip the trade if that stop is wider than max_stop % of price. Exits: 'thirds' (1/3 at 1R, 2R, 3R, stop to breakeven
after the first), 'trail' (1/3 at 1R, 1/3 at 2R, last third trails behind the latest swing), '2r' (all at 2R).
Everything closes at the open of the 15:15 candle; no new entry from 15:00. Max `attempts` entries per instrument per day.
Event filter (on/off): skip the day if the open gaps > 1.5% from the previous close; stop new entries once the day's
range so far exceeds 2 x the 20-day average range (known in real time, no look-ahead).
Grid 1 per slice (timeframe x entry x exit): N x max_stop (attempts 2, filter off). Grid 2: attempts x filter.
"""
import numpy as np

from backtest.podcast import data, engine, indicators as ind
from backtest.podcast.config import CONFIG

KEY = "C"
NAME = "Dow swing breakout"
FOLDER = "podcast_c_swing_breakout"
P = CONFIG["modules"]["C"]
EXIT_TXT = {"thirds": "1/3 @1R, 2R, 3R + breakeven", "trail": "1/3 @1R, 2R + baaki trail", "2r": "sab 2R pe"}
ENTRY_TXT = {"touch": "level chhoote hi (stop order)", "close": "candle close level ke paar, agli candle open"}
_SW: dict = {}
_DAY: dict = {}


def slices() -> list[dict]:
    return [{"tf": tf, "entry": e, "exit": x} for tf in P["tfs"] for e in P["entries"] for x in P["exits"]]


def grid1(sl: dict) -> tuple[list, list]:
    return ["n", "max_stop"], [{"n": n, "max_stop": m, "attempts": 2, "event_filter": 0} for n in P["grid_n"] for m in P["grid_max_stop"]]


def grid2(sl: dict, chosen: dict):
    return ["attempts", "event_filter"], [{"n": chosen["n"], "max_stop": chosen["max_stop"], "attempts": a, "event_filter": f}
                                          for a in P["grid_attempts"] for f in (0, 1)]


def label(sl: dict, p: dict) -> str:
    return (f"{sl['tf']} swing N={p['n']}, {sl['entry']} entry, {EXIT_TXT[sl['exit']]}, max stop {p['max_stop']:g}%, "
            f"{p['attempts']} try/din, filter {'on' if p['event_filter'] else 'off'}")


def warm():
    pass


def _swings(b, n: int):
    k = (b.name, b.kind, b.tf, b.n, n)
    if k not in _SW:
        _SW[k] = ind.swings(b.h, b.l, n)
    return _SW[k]


def _days(b):
    """Per day: gap % of the open vs the previous day's last close (real price), 20-day average range (points);
    per candle: the day's range up to the candle before it."""
    k = (b.name, b.kind, b.tf, b.n)
    if k in _DAY:
        return _DAY[k]
    nd = len(b.days)
    rng = np.array([b.h[b.first[d]:b.last[d] + 1].max() - b.l[b.first[d]:b.last[d] + 1].min() for d in range(nd)])
    adr = np.full(nd, np.nan)
    c = np.cumsum(np.r_[0.0, rng])
    adr[P["range_n"]:] = (c[P["range_n"]:-1] - c[:-P["range_n"] - 1]) / P["range_n"]
    prev_c = np.r_[np.nan, b.c[b.last[:-1]]]
    prev_raw = prev_c - np.r_[np.nan, b.off[b.last[:-1]]]
    gap = np.abs(b.o[b.first] - prev_c) / prev_raw * 100
    run = np.zeros(b.n)
    for d in range(nd):
        a, z = b.first[d], b.last[d] + 1
        hi = np.maximum.accumulate(b.h[a:z])
        lo = np.minimum.accumulate(b.l[a:z])
        run[a:z] = np.r_[0.0, (hi - lo)[:-1]]
    _DAY[k] = (gap, adr, run)
    return _DAY[k]


def _manage(t: dict, b, j: int, st: list, tg: list, slip, sh_low, entry_bar: int, mode: str) -> bool:
    """Process candle j for an open position; st = [stop, phase], tg = remaining [(level, frac, name)]. True when flat."""
    s = t["side"]
    o, h, l = b.o[j], b.h[j], b.l[j]
    rem = 1.0 - sum(x[2] for x in t["legs"])
    stop = st[0]
    why = "stop" if st[1] == 0 else ("breakeven" if st[1] == 1 else "trail")
    if j > entry_bar:
        if (o <= stop) if s > 0 else (o >= stop):
            engine.close_leg(t, b, j, o, rem, why + " gap", slip, when="open")
            return True
        if b.minute[j] >= CONFIG["eod_exit"]:
            engine.close_leg(t, b, j, o, rem, "15:15 exit", slip, when="open")
            return True
    if (l <= stop) if s > 0 else (h >= stop):
        engine.close_leg(t, b, j, stop, rem, why, slip)
        return True
    while tg and ((h >= tg[0][0]) if s > 0 else (l <= tg[0][0])):
        lvl, frac, name = tg.pop(0)
        engine.close_leg(t, b, j, lvl, frac, name, slip)
        rem -= frac
        if rem <= 1e-9:
            return True
        if st[1] == 0:                                  # first target -> stop to breakeven
            st[0], st[1] = t["entry_px"], 1
        if mode == "trail" and not tg:                  # both targets done -> trail the rest
            st[1] = 2
        if (l <= st[0]) if s > 0 else (h >= st[0]):     # the new stop touched in the same candle -> out (stop first)
            engine.close_leg(t, b, j, st[0], rem, "breakeven" if st[1] == 1 else "trail", slip)
            return True
    if st[1] == 2:                                      # trail behind the latest swing known at this close
        lv = sh_low[j]
        if np.isfinite(lv):
            st[0] = max(st[0], lv) if s > 0 else min(st[0], lv)
    return False


def _targets(entry: float, s: int, risk: float, mode: str) -> list:
    if mode == "2r":
        return [(entry + s * 2 * risk, 1.0, "2R target")]
    if mode == "thirds":
        return [(entry + s * k * risk, 1 / 3, f"{k}R target") for k in (1, 2, 3)]
    return [(entry + s * k * risk, 1 / 3, f"{k}R target") for k in (1, 2)]


def _run(b, sl: dict, p: dict, slip) -> list:
    n, maxs, att, filt = int(p["n"]), p["max_stop"] / 100, int(p["attempts"]), int(p["event_filter"])
    sh, slw = _swings(b, n)
    gap, adr, run = _days(b)
    o, h, l, c = b.o, b.h, b.l, b.c
    tick = CONFIG["tick"]
    eod, last_entry = CONFIG["eod_exit"], P["last_entry"]
    shp, slp, cp = np.r_[np.nan, sh[:-1]], np.r_[np.nan, slw[:-1]], np.r_[np.nan, c[:-1]]
    with np.errstate(invalid="ignore"):
        if sl["entry"] == "touch":
            cl = (h >= shp + tick) & (cp < shp)
            cs = (l <= slp - tick) & (cp > slp)
        else:
            cl = (c > shp) & (cp <= shp)
            cs = (c < slp) & (cp >= slp)
    cand = np.nonzero(cl | cs)[0]
    trades = []
    for d in range(len(b.days)):
        i0, i1 = int(b.first[d]), int(b.last[d])
        if filt and not (gap[d] <= P["gap_pct"]):
            continue
        k = int(np.searchsorted(cand, i0))
        attempts, used = 0, set()
        i = i0
        while attempts < att and k < len(cand) and cand[k] <= i1:
            i = int(cand[k])
            k += 1
            if b.minute[i] >= last_entry:
                break
            if filt and np.isfinite(adr[d]) and run[i] > P["range_x"] * adr[d]:
                break
            lo_ok, sh_ok = bool(cl[i]), bool(cs[i])
            if lo_ok and sh_ok:                          # both levels broken in one candle: take the side the open is already on
                lo_ok, sh_ok = (o[i] >= shp[i] + tick), (o[i] <= slp[i] - tick)
                if lo_ok == sh_ok:
                    continue
            s = 1 if lo_ok else -1
            lvl = shp[i] if s > 0 else slp[i]
            if lvl in used:
                continue
            if sl["entry"] == "touch":
                e = i
                fill_lvl = max(o[i], lvl + tick) if s > 0 else min(o[i], lvl - tick)
                stop = slp[i] if s > 0 else shp[i]
            else:
                e = i + 1
                if e > i1 or b.minute[e] >= eod:
                    continue
                fill_lvl = o[e]
                stop = slw[i] if s > 0 else sh[i]
            entry = fill_lvl + s * slip[e]
            risk = (entry - stop) * s
            if not np.isfinite(stop) or risk <= 0 or risk / (entry - b.off[e]) > maxs:
                continue
            used.add(lvl)
            attempts += 1
            t = engine.new_trade(b, s, i, e, fill_lvl, slip, stop, np.nan, stop_pct=risk / (entry - b.off[e]) * 100)
            tg = _targets(entry, s, risk, sl["exit"])
            t["target_px"] = tg[-1][0]
            st = [stop, 0]
            low_arr = slw if s > 0 else sh
            j = e
            while True:
                if _manage(t, b, j, st, tg, slip, low_arr, e if sl["entry"] == "touch" else e - 1, sl["exit"]):
                    break
                if j >= i1:
                    rem = 1.0 - sum(x[2] for x in t["legs"])
                    engine.close_leg(t, b, j, c[j], rem, "day close", slip)
                    break
                j += 1
            trades.append(t)
            k = int(np.searchsorted(cand, j + 1))          # next signal after the exit candle
    return trades


def trades(bars: dict, sl: dict, p: dict, slip_mult: float = 1.0, cache: dict | None = None) -> list:
    out = []
    for b in bars.values():
        out += _run(b, sl, p, engine.slip_array(b, slip_mult))
    return out


def random_trades(bars: dict, sl: dict, p: dict, rule, rng: np.random.Generator) -> list:
    """Same count per instrument as the rule; random candle (09:15-15:00) and side; stop distance drawn from the rule's
    own stop distances (% of price); same exits (targets in R, breakeven, trailing, 15:15)."""
    out = []
    stops = rule["stop_pct"].to_numpy() if "stop_pct" in rule else np.array([0.3])
    for name, b in bars.items():
        cnt = int((rule["inst"] == name).sum())
        if not cnt:
            continue
        slip = engine.slip_array(b)
        sh, slw = _swings(b, int(p["n"]))
        ok = np.nonzero((b.minute < P["last_entry"]))[0]
        for e, s, d in zip(rng.choice(ok, size=cnt), rng.choice([-1, 1], size=cnt), rng.choice(stops, size=cnt)):
            entry = b.o[e] + s * slip[e]
            risk = (entry - b.off[e]) * d / 100.0
            stop = entry - s * risk
            t = engine.new_trade(b, int(s), e, e, b.o[e], slip, stop, np.nan)
            tg = _targets(entry, int(s), risk, sl["exit"])
            st = [stop, 0]
            i1 = int(b.last[b.di[e]])
            j = e
            while True:
                if _manage(t, b, j, st, tg, slip, slw if s > 0 else sh, e - 1, sl["exit"]):
                    break
                if j >= i1:
                    engine.close_leg(t, b, j, b.c[j], 1.0 - sum(x[2] for x in t["legs"]), "day close", slip)
                    break
                j += 1
            out.append(t)
    return out


# ------------------------------------------------------------------------------------------- report texts (Hinglish)
def rules(sl: dict, p: dict, split: str) -> list[str]:
    ex = {"thirds": "Exit: 1/3 qty +1R pe, 1/3 +2R pe, 1/3 +3R pe; pehla target lagte hi stop entry pe (breakeven).",
          "trail": "Exit: 1/3 qty +1R pe, 1/3 +2R pe (pehle target ke baad stop breakeven), baaki 1/3 ka stop har naye swing ke peeche chalta (trail).",
          "2r": "Exit: poori qty +2R pe (single target)."}[sl["exit"]]
    return [
        "Instruments: NIFTY aur BANKNIFTY futures (apni continuous series) + 10 bade stocks cash me intraday (MIS).",
        f"Candle: {sl['tf']} (09:15 se). Period Feb 2018 se 9 Nov 2026; pehla 60% (2018 - {split} se pehle) = setting chunne ke liye, baaki 40% = test.",
        f"Swing high = wo candle jiska high dono taraf ki {p['n']} candles se upar; swing low ulta. Swing tabhi pakka maana jab uske baad {p['n']} candles band ho chuki hon (aage ka data nahi).",
        "LONG: price aakhri pakke swing high ko tode; SHORT: aakhri pakke swing low ko tode. Pichhli candle level ke doosri taraf honi chahiye (taaza break), aur ek level din me ek hi baar.",
        f"Entry: {ENTRY_TXT[sl['entry']]}" + (" - level + 1 tick pe stop order; candle level ke paar khule to us open pe." if sl["entry"] == "touch" else "."),
        f"Stop: long me aakhri swing low, short me aakhri swing high. Stop entry se {p['max_stop']:g}% se zyada door ho to trade nahi.",
        ex,
        "15:15 ki candle khulte hi sab band; 15:00 ke baad nayi entry nahi. Stop aur target ek candle me -> stop.",
        f"Ek instrument me din me max {p['attempts']} entry (stop ke baad naye swing break pe hi dobara).",
        "Event filter " + ("ON: open kal ke close se 1.5% se zyada door ho to us din trade nahi; din ki range ab tak 20 din ki average range ke 2 guna se zyada ho jaye to nayi entry band." if p["event_filter"] else "OFF."),
        "Size: stop lage to capital ka 0.25% (Rs 2,500); din ka loss limit 0.5% (band hui trades ka loss Rs 5,000 -> us din aur entry nahi).",
    ]


def setting_chips(sl: dict, p: dict) -> list:
    return [["Candle", sl["tf"], 1], ["Swing N", str(p["n"]), 1], ["Entry", ENTRY_TXT[sl["entry"]], 1], ["Exit", EXIT_TXT[sl["exit"]], 1],
            ["Max stop", f"{p['max_stop']:g}% of price", 1], ["Try / din", str(p["attempts"]), 0], ["Event filter", "on" if p["event_filter"] else "off", 0],
            ["Close", "15:15 sab band, 15:00 ke baad entry nahi", 0]]


def formulas(sl: dict, p: dict) -> list:
    return [["Swing high (pakka)", "high[p] > high[p-k] aur > high[p+k], k = 1..N;  candle p+N ke close pe pata chalta hai"],
            ["Break (touch)", "high[t] >= swing_high + 0.05 aur close[t-1] < swing_high -> long; entry = max(open[t], swing_high + 0.05) + slippage"],
            ["Break (close)", "close[t] > swing_high aur close[t-1] <= swing_high -> long; entry = open[t+1] + slippage"],
            ["Stop / R", "stop = aakhri swing low (long);  R = entry - stop;  R / asli_entry > max_stop% -> trade nahi"],
            ["Targets", "thirds: entry + 1R, 2R, 3R (1/3 har ek);  trail: 1R, 2R (1/3) + baaki trail;  2r: entry + 2R"],
            ["Breakeven / trail", "pehla target -> stop = entry;  trail: stop = max(stop, naya swing low) har candle close pe"],
            ["Exit fill", "level - side x slippage; candle stop ke paar khule -> us open pe; 15:15 candle ka open"],
            ["Event filter", "gap% = |open - kal ka close| / kal ka close > 1.5 -> din skip;  range_ab_tak > 2 x average(pichhle 20 din ki range) -> nayi entry nahi"],
            ["Qty", "floor( min( capital x 0.25% / R , 5 x capital / asli_entry ) )"],
            ["Charges", "futures: Rs 20/order + STT sell (date se) + NSE + SEBI + stamp + GST;  stock MIS: min(20, 0.03%) + STT sell 0.025% + NSE 0.00297% + stamp 0.003% + GST"],
            ["Net / R", "net = sum(qty x hissa x side x (exit - entry)) - charges;  R = net / (qty x R points)"],
            ["Win rate / PF / expectancy", "net > 0 trades %;  sab profit / sab loss;  kul net / trades"],
            ["Return % / CAGR / drawdown", "net / 10 lakh;  (aakhri / 10 lakh)^(365/din) - 1;  peak se sabse bada gir, do high ke beech sabse lamba samay"],
            ["Plateau", "setting + padosi (N aur max stop ek step upar-neeche) sab ka fit PF > 1"],
            ["Random control", "1,000 baar: wahi count, random candle + side, stop rule ki stop-distances me se random, wahi exits"]]


def podcast_rows(grid) -> list:
    out = []
    g = grid[(grid["grid"] == 1) & grid["n"].isin([2, 3, 5]) & (grid["max_stop"] == 0.5)]
    for r in g.itertuples(index=False):
        sl = {"tf": r.tf, "entry": r.entry, "exit": r.exit}
        p = {"n": int(r.n), "max_stop": r.max_stop, "attempts": int(r.attempts), "event_filter": int(r.event_filter)}
        out.append({"label": label(sl, p), "is_trades": r.is_trades, "is_pf": r.is_pf, "is_exp_R": r.is_exp_R, "oos_trades": r.oos_trades,
                    "oos_pf": r.oos_pf, "oos_exp_R": r.oos_exp_R, "oos_net": r.oos_net, "status": r.status})
    return out


def quick():
    P.update({"tfs": ["5m"], "entries": ["touch"], "exits": ["thirds"], "grid_n": [2, 3], "grid_max_stop": [0.4, 0.5], "grid_attempts": [1, 2]})
