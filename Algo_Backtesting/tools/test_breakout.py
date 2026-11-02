"""Checks for the breakout + put selling backtest (src/backtest/breakout.py).

    python tools/test_breakout.py            # all three
    python tools/test_breakout.py lookahead  # future prices changed -> universe and signals up to that day stay the same
    python tools/test_breakout.py hand       # one type A and one type B trade recomputed by hand from the raw data
"""
import csv
import math
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

import numpy as np  # noqa: E402
import pandas as pd  # noqa: E402

from backtest import breakout as B  # noqa: E402
from backtest import fast  # noqa: E402
from backtest.config import FNO  # noqa: E402

P = B.Params()


def close(a, b, tol=1e-6):
    return abs(a - b) <= tol * max(1.0, abs(a), abs(b))


def test_lookahead(D) -> bool:
    W = D["W"]
    dates = list(W["close"].index)
    ok = True
    for t in (300, 700, 1000):
        rng = np.random.default_rng(t)
        Wp = {k: v.copy() for k, v in W.items()}
        noise = rng.uniform(0.7, 1.3, size=(len(dates) - t - 1, W["close"].shape[1]))
        for k in ("open", "high", "low", "close"):
            Wp[k].iloc[t + 1:] = W[k].iloc[t + 1:].to_numpy() * noise
        Wp["volume"].iloc[t + 1:] = W["volume"].iloc[t + 1:].to_numpy() * rng.uniform(0.2, 5, size=noise.shape)
        Wp["uni"] = B.universe(Wp, D["fno"], P.top_n)
        same_uni = bool((Wp["uni"].iloc[:t + 1] == W["uni"].iloc[:t + 1]).all().all())
        Dp = {**D, "W": Wp}
        cut = dates[t]
        res = []
        for cap in (False, True):
            a = B.signals(D, P, cap=cap)
            b = B.signals(Dp, P, cap=cap)
            a, b = a[a.date <= cut].reset_index(drop=True), b[b.date <= cut].reset_index(drop=True)
            res.append(len(a) == len(b) and a.equals(b))
        good = same_uni and all(res)
        ok &= good
        print(f"  lookahead day {cut}: universe same {same_uni}, signals same (no cap / cap) {res} -> {'OK' if good else 'FAIL'}")
    return ok


def test_hand_a(D) -> bool:
    sig = B.signals(D, P)
    a = B.size_a(B.path_a(D, sig, P), P)
    r = a.iloc[0]
    sym, s_day = r.symbol, r.signal_date
    x = fast.panel("2021-10-01", "2026-10-30", columns=["open", "high", "low", "close", "volume"])
    x = x[x.symbol == sym].assign(date=lambda d: d.date.astype(str)).reset_index(drop=True)
    i = int(x.index[x.date == s_day][0])
    base = x.iloc[i - 20:i]
    res, lo = base.high.max(), base.low.min()
    avgb = ((base.close - base.open).abs() / base.close).mean()
    vm = x.volume[i] / x.volume.iloc[i - 20:i].mean()
    d = x.iloc[i]
    filt = {"close > res": d.close > res, "height <= 12%": (res - lo) / res <= 0.12, "volume >= 2x": vm >= 2, "small candles": avgb <= 0.015,
            "accumulation": x.volume.iloc[i - 10:i].mean() > x.volume.iloc[i - 20:i - 10].mean(), "close near high": d.close >= d.low + 0.75 * (d.high - d.low),
            "big body": (d.close - d.open) / d.close >= 1.5 * avgb, "no big red before": (x.open[i - 1] - x.close[i - 1]) / x.close[i - 1] <= 2 * avgb}
    entry = x.open[i + 1]
    stop = res * 0.995
    tgt = entry + 2 * (entry - stop)
    ex = None
    for k in range(i + 1, i + 11):
        o, h, l, c = x.open[k], x.high[k], x.low[k], x.close[k]
        if k > i + 1 and o <= stop:
            ex = (k, o, "SL (gap)")
        elif k > i + 1 and o >= tgt:
            ex = (k, o, "target (gap)")
        elif l <= stop:
            ex = (k, stop, "SL")
        elif h >= tgt:
            ex = (k, tgt, "target")
        elif k == i + 10:
            ex = (k, c, "10 din time stop")
        if ex:
            break
    bf, xf = entry * 1.0005, ex[1] * 0.9995
    qty = math.floor(min(10000 / (bf - stop), 500000 / bf))
    bv, sv = qty * bf, qty * xf
    cost = 0.001 * (bv + sv) + 0.0000307 * (bv + sv) + 1e-6 * (bv + sv) + 0.00015 * bv + 0.18 * (0.0000307 + 1e-6) * (bv + sv) + 15.34 * 1.18
    net = sv - bv - cost
    checks = {**{f"filter {k}": bool(v) for k, v in filt.items()}, "resistance": close(res, r.res), "volume multiple": close(vm, r.vm), "entry": close(entry, r.entry),
              "stop": close(stop, r.stop), "target": close(tgt, r.target), "exit day": x.date[ex[0]] == r.exit_date, "exit price": close(ex[1], r.exit), "reason": ex[2] == r.reason,
              "qty": qty == r.qty, "net": close(net, r.net, 1e-6)}
    print(f"  A: {sym} breakout {s_day}, entry {entry:.2f} stop {stop:.2f} target {tgt:.2f}, exit {x.date[ex[0]]} @ {ex[1]:.2f} ({ex[2]}), qty {qty}, net {net:,.2f} vs engine {r.net:,.2f}")
    bad = [k for k, v in checks.items() if not v]
    print("  A hand check:", "OK" if not bad else f"FAIL {bad}")
    return not bad


def test_hand_b(D) -> bool:
    sig = B.signals(D, P)
    b = B.size_b(B.path_b(D, sig, P), P)
    r = b.sort_values("entry_date").iloc[0]
    sym = r.symbol

    def rows(day):
        with open(FNO / "bhavcopy_opt" / f"{day}.csv", newline="") as f:
            return [x for x in csv.DictReader(f) if x["TckrSymb"] == sym and x["OptnTp"] == "PE" and int(float(x["TtlTradgVol"])) > 0]
    days = fast.days(B.OPT_START, B.END)
    e = r.entry_date
    ent = rows(e)
    expiries = sorted({x["XpryDt"] for x in ent})
    X = next(x for x in expiries if x > e)
    tde = days.index(X) - days.index(e)
    raw_close = fast.panel(e, e, columns=["close"], adjust=False)
    spot = float(raw_close.loc[raw_close.symbol == sym, "close"].iloc[0])
    factor = spot / float(fast.panel(e, e, columns=["close"]).query("symbol == @sym").close.iloc[0])
    sup = r.res * factor
    ks = sorted(float(x["StrkPric"]) for x in ent if x["XpryDt"] == X and float(x["StrkPric"]) <= sup)
    K = ks[-1]
    prem = next(float(x["ClsPric"]) for x in ent if x["XpryDt"] == X and float(x["StrkPric"]) == K)
    lot = next(int(float(x["NewBrdLotQty"])) for x in ent if x["XpryDt"] == X and float(x["StrkPric"]) == K)
    out = rows(r.exit_date)
    px = next((float(x["ClsPric"]) for x in out if x["XpryDt"] == X and float(x["StrkPric"]) == K), None)
    lots = math.floor(10000 / (prem * lot))
    qty = lots * lot
    sell = prem * 0.98
    buy = (px if px is not None else r.exit) * 1.02
    new_in, new_out = e >= "2026-04-01", r.exit_date >= "2026-04-01"

    def fee(v, is_buy, new):
        exch, sebi = 0.0003503 * v, 1e-6 * v
        return 20 + exch + sebi + (0 if is_buy else (0.0015 if new else 0.001) * v) + (0.00003 * v if is_buy else 0) + 0.18 * (20 + exch + sebi)
    cost = fee(sell * qty, False, new_in) + (fee(buy * qty, True, new_out) if r.exit > 0 else 0)
    net = qty * (sell - (buy if r.exit > 0 else 0)) - cost
    checks = {"expiry": X == r.expiry, "trading days to expiry 2..7": 2 <= tde <= 7 and tde == r.dte, "strike": K == r.strike, "premium": close(prem, r.prem), "lot": lot == r.lot,
              "lots": lots == r.lots, "exit price": px is None or close(px, r.exit), "net": close(net, r.net, 1e-6)}
    print(f"  B: {sym} breakout {r.signal_date}, put {K:g} PE exp {X} sold {e} @ {prem} (support {sup:.2f}, spot {spot}), {lots} x {lot}, "
          f"exit {r.exit_date} @ {px} ({r.reason}), net {net:,.2f} vs engine {r.net:,.2f}")
    bad = [k for k, v in checks.items() if not v]
    print("  B hand check:", "OK" if not bad else f"FAIL {bad}")
    return not bad


def main() -> int:
    which = sys.argv[1:] or ["lookahead", "hand"]
    D = B.load()
    ok = True
    if "lookahead" in which:
        print("lookahead:")
        ok &= test_lookahead(D)
    if "hand" in which:
        print("hand:")
        ok &= test_hand_a(D)
        ok &= test_hand_b(D)
    print("ALL OK" if ok else "SOME CHECKS FAILED")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
