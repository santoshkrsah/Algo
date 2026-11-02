"""Checks for the full-spec breakout backtest (src/backtest/breakout2.py).

    python tools/test_breakout2.py             # all
    python tools/test_breakout2.py rules       # put-sell exit rules on a made-up stock + chain (no data needed)
    python tools/test_breakout2.py lots        # old-format lot sizes (turnover / contracts) vs NSE's own lot size, same contracts
    python tools/test_breakout2.py factor      # actual / adjusted price factor around known splits and bonuses
    python tools/test_breakout2.py lookahead   # future prices changed -> universe and signals up to that day stay the same
    python tools/test_breakout2.py hand        # one type A and one type B trade recomputed by hand
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

import numpy as np  # noqa: E402
import pandas as pd  # noqa: E402

from backtest import breakout2 as B  # noqa: E402

P = B.Params()


def close(a, b, tol=1e-6):
    return abs(a - b) <= tol * max(1.0, abs(a), abs(b))


# ---------------------------------------------------------------- made-up stock for the exit rules
class FakeChains:
    def __init__(self, frames, expiry, lot=100):
        self.frames, self.expiries, self.lots = frames, [expiry], {("XYZ", expiry): lot}

    def get(self, sym, exp):
        return self.frames

    def next_expiry(self, day):
        return self.expiries[0] if day < self.expiries[0] else None


def fake(closes, opens=None, put=None):
    """One stock XYZ over len(closes) days; expiry on the last day. put: {strike: [(o, h, l, c) or None per day]}."""
    n = len(closes)
    cal = [f"2024-01-{d + 1:02d}" for d in range(n)]
    c = pd.DataFrame({"XYZ": closes}, index=cal, dtype=float)
    o = pd.DataFrame({"XYZ": opens or closes}, index=cal, dtype=float)
    W = {"close": c, "open": o, "high": c * 1.01, "low": c * 0.99, "factor": c * 0 + 1.0, "sigma": c * 0 + 0.015}
    frames = {}
    for q, k in (("open", 0), ("high", 1), ("low", 2), ("close", 3), ("vol", 3)):
        frames[q] = pd.DataFrame({K: [np.nan if b is None else (1.0 if q == "vol" else b[k]) for b in bars] for K, bars in put.items()}, index=cal)
    D = {"W": W, "cal": cal, "pos": {d: i for i, d in enumerate(cal)}, "chains": FakeChains(frames, cal[-1])}
    sig = pd.DataFrame([{"i": 0, "j": 0, "date": cal[0], "symbol": "XYZ", "kind": "breakout", "res": 100.0, "lo": 92.0, "support": 100.0, "height": 0.08,
                         "vm": 2.5, "base_n": 20, "bull": True}])
    return D, sig


def test_rules() -> bool:
    ok = True
    flat = (2.0, 2.1, 1.9, 2.0)

    def run(closes, bars, opens=None, **kw):
        D, sig = fake(closes, opens, {100.0: bars, 95.0: [flat] * len(closes)})
        p = B.with_params(P, **kw)
        t = B.path_b(D, sig, p)
        return t.iloc[0]

    cases = []
    # 1. stock closes below support on day 3 -> bought back at that day's put close
    r = run([101, 101, 100.5, 99, 101, 102, 103], [flat, flat, (2.0, 2.6, 1.8, 2.4), (2.6, 3.5, 2.5, 3.2), flat, flat, flat])
    cases.append(("support stop -> day 3 put close", r.reason.startswith("SL: stock support") and r.exit_date == "2024-01-04" and close(r.exit, 3.2)))
    # 2. premium stop 2x and 50% target in the same candle -> stop first, at 2 x premium
    r = run([101, 101, 101, 101, 101, 101, 101], [flat, flat, (1.5, 4.2, 0.8, 1.2), flat, flat, flat, flat], b_stop="prem2", take=0.5)
    cases.append(("stop + target same candle -> stop at 4.0", r.reason == "SL: premium 2x" and close(r.exit, 4.0)))
    # 3. put opens above the stop level -> filled at the open (gap)
    r = run([101, 101, 101, 101, 101, 101, 101], [flat, flat, (5.0, 5.5, 4.5, 5.2), flat, flat, flat, flat], b_stop="prem2")
    cases.append(("gap through premium stop -> open 5.0", r.reason == "SL: premium 2x (gap)" and close(r.exit, 5.0)))
    # 4. 50% target touched by the low -> bought back at 1.0
    r = run([101, 101, 102, 102, 102, 102, 102], [flat, flat, (1.6, 1.7, 0.9, 1.1), flat, flat, flat, flat], take=0.5)
    cases.append(("50% target -> 1.0", r.reason.startswith("target: 50%") and close(r.exit, 1.0)))
    # 5. stays above the strike -> worthless at expiry, no buy-back
    r = run([101, 101, 102, 103, 104, 105, 106], [flat, flat, (1.5, 1.6, 1.2, 1.3), (1.0, 1.1, 0.6, 0.7), (0.5, 0.6, 0.3, 0.4), (0.2, 0.3, 0.1, 0.2), (0.1, 0.1, 0.05, 0.05)])
    cases.append(("OTM at expiry -> 0", r.reason == "expiry: OTM, bekaar khatam" and r.exit == 0.0))
    # 6. premium stop rule, stock below the 100 strike the day before expiry -> bought back that day (settle close)
    sm = [101, 101, 101, 101, 101, 99.5, 99.0]
    bars = [flat, flat, flat, flat, flat, (2.0, 2.5, 1.8, 2.2), (2.1, 2.3, 1.0, 1.0)]
    r = run(sm, bars, b_stop="prem2")
    cases.append(("ITM day before expiry -> closed at that close", r.reason == "ITM: expiry se 1 din pehle band" and r.exit_date == "2024-01-06" and close(r.exit, 2.2)))
    # 7. same, physical: held to expiry, delivery at strike, shares sold at the next open (needs one more day)
    D, sig = fake(sm + [98.0], sm + [97.0], {100.0: bars + [None], 95.0: [flat] * 8})
    D["chains"].expiries = [D["cal"][6]]
    D["chains"].lots = {("XYZ", D["cal"][6]): 100}
    r = B.path_b(D, sig, B.with_params(P, b_stop="prem2", settle="physical")).iloc[0]
    cases.append(("physical: strike - next open = 3.0", r.reason.startswith("physical delivery") and close(r.exit, 100.0 - 97.0)))
    # 8. sizing from the stop rule: prem2 risk = 1 x premium (+ slippage both fills)
    r = run([101] * 7, [flat] * 7, b_stop="prem2")
    cases.append(("prem2 risk / share = 2.0 + 0.10 + 0.10", close(r.risk_ps, 2.0 + 0.10 + max(0.02 * 4.0, 0.10))))
    for name, good in cases:
        ok &= bool(good)
        print(f"  {name}: {'OK' if good else 'FAIL'}")
    return ok


# ---------------------------------------------------------------- data checks
def test_lots(fut=None, puts=None) -> bool:
    """Contracts listed on 5 Jul 2024 (old format, no lot) and 8 Jul 2024 (UDiFF, NewBrdLotQty): the turnover estimate must match."""
    if fut is None:
        fut, puts = B.fo_data()
    old = puts[puts.date.astype(str) < "2024-07-08"]
    real = fut[(fut.date.astype(str) == "2024-07-08")].assign(symbol=lambda x: x.symbol.astype(str), expiry=lambda x: x.expiry.astype(str))
    real = real.groupby(["symbol", "expiry"]).lot.first()
    est = B.lot_sizes(fut[fut.date.astype(str) < "2024-07-08"], old)
    both = real.index.intersection(est.index)
    eq = (est.loc[both].to_numpy() == real.loc[both].to_numpy())
    bad = [(s, e, int(est.loc[(s, e)]), int(real.loc[(s, e)])) for (s, e), g in zip(both, eq) if not g]
    print(f"  lot sizes: {len(both)} contracts in both formats, {eq.sum()} same ({eq.mean() * 100:.1f}%); different: {bad[:10]}")
    return len(both) > 100 and eq.mean() >= 0.98


def test_factor(D=None) -> bool:
    if D is None:
        D = B.load()
    F = D["W"]["factor"]
    known = {"RELIANCE": ("2024-10-28", 2.0, 1.0), "TATASTEEL": ("2022-07-28", 10.0, 1.0), "BAJFINANCE": ("2025-06-16", 10.0, 1.0), "HDFCBANK": ("2025-08-26", 2.0, 1.0)}
    ok = True
    for sym, (day, before, after) in known.items():
        if sym not in F:
            print(f"  {sym}: not in the universe")
            continue
        s = F[sym]
        b, a = s[s.index < day].iloc[-5:].median(), s[s.index >= day].iloc[5:10].median()
        good = close(b, before, 0.02) and close(a, after, 0.02)
        ok &= good
        print(f"  {sym}: factor before {day} {b:.3f} (want {before:g}), after {a:.3f} (want {after:g}) -> {'OK' if good else 'FAIL'}")
    ch = (F.diff().abs() > 0.05 * F.shift()).sum()
    print("  stocks with a factor step:", {k: int(v) for k, v in ch[ch > 0].items()})
    return ok


def test_lookahead(D=None) -> bool:
    if D is None:
        D = B.load()
    W = D["W"]
    dates = D["cal"]
    ok = True
    for t in (300, 700, 1000):
        rng = np.random.default_rng(t)
        Wp = {k: v.copy() for k, v in W.items()}
        noise = rng.uniform(0.7, 1.3, size=(len(dates) - t - 1, W["close"].shape[1]))
        for k in ("open", "high", "low", "close"):
            Wp[k].iloc[t + 1:] = W[k].iloc[t + 1:].to_numpy() * noise
        Wp["volume"].iloc[t + 1:] = W["volume"].iloc[t + 1:].to_numpy() * rng.uniform(0.2, 5, size=noise.shape)
        Dp = {**D, "W": Wp}
        cut = dates[t]
        res = []
        for fn in (B.signals, B.selloffs):
            for cap in (False, True):
                a, b = fn(D, P, cap=cap), fn(Dp, P, cap=cap)
                a, b = a[a.date <= cut].reset_index(drop=True), b[b.date <= cut].reset_index(drop=True)
                res.append(len(a) == len(b) and a.drop(columns=["bull"]).equals(b.drop(columns=["bull"])))
        good = all(res)
        ok &= good
        print(f"  lookahead day {cut}: signals / selloffs same (no cap, cap) {res} -> {'OK' if good else 'FAIL'}")
    return ok


def test_hand(D=None) -> bool:
    if D is None:
        D = B.load()
    W = D["W"]
    sig = B.signals(D, P)
    a = B.book_a(D, B.path_a(D, sig, P), P)
    r = a.iloc[len(a) // 2]
    j = list(W["close"].columns).index(r.symbol)
    i = D["pos"][r.signal_date]
    H, L, C, V = (W[k].iloc[:, j].to_numpy() for k in ("high", "low", "close", "volume"))
    res = np.max(H[i - 20:i])
    vm = V[i] / np.mean(V[i - 20:i])
    entry = W["open"].iloc[i + 1, j]
    stop = res * 0.995
    bf = entry * 1.0005
    qty = int(min(P.capital * P.risk / (bf - stop), P.capital * P.max_val / bf))
    ok_a = close(r.res, res) and close(r.vm, vm) and close(r.entry, entry) and close(r.stop, stop) and (r.qty == qty or r.qty < qty) and C[i] > res
    print(f"  A {r.symbol} {r.signal_date}: base top {res:.2f} (engine {r.res:.2f}), vm {vm:.2f} ({r.vm:.2f}), entry {entry:.2f}, stop {stop:.2f}, qty {qty} ({r.qty}) "
          f"-> {'OK' if ok_a else 'FAIL'}")
    b = B.book_b(D, B.path_b(D, sig, P), P)
    if b.empty:
        print("  B: no trades")
        return ok_a
    r = b.iloc[len(b) // 2]
    ch = D["chains"].get(r.symbol, r.expiry)
    prem = ch["close"].at[r.entry_date, r.strike]
    strikes = ch["close"].loc[r.entry_date].dropna()
    k_want = strikes[strikes.index <= r.support].index.max()
    sell = prem - max(0.02 * prem, 0.10)
    ok_b = close(prem, r.prem) and close(k_want, r.strike) and close(sell, r.sell_fill) and r.lots == int(P.capital * P.risk // (r.risk_ps * r.lot)) or r.lots < int(P.capital * P.risk // (r.risk_ps * r.lot))
    print(f"  B {r.symbol} sold {r.entry_date} {r.strike} PE exp {r.expiry}: premium {prem} (engine {r.prem}), strike want {k_want}, sell fill {sell:.2f} ({r.sell_fill:.2f}), "
          f"lots {r.lots}, exit {r.exit_date} {r.reason} -> {'OK' if ok_b else 'FAIL'}")
    return ok_a and ok_b


if __name__ == "__main__":
    which = sys.argv[1:] or ["rules", "lots", "factor", "lookahead", "hand"]
    results = {}
    D = None
    for w in which:
        print(w)
        if w == "rules":
            results[w] = test_rules()
        elif w == "lots":
            results[w] = test_lots()
        else:
            D = D or B.load()
            results[w] = {"factor": test_factor, "lookahead": test_lookahead, "hand": test_hand}[w](D)
    print("ALL OK" if all(results.values()) else f"FAILED: {[k for k, v in results.items() if not v]}")
    sys.exit(0 if all(results.values()) else 1)
