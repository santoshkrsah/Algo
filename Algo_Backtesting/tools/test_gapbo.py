"""Checks for the gap breakout backtest (src/backtest/gapbo.py).

    python tools/test_gapbo.py               # all
    python tools/test_gapbo.py walk          # exit rules on hand-made candles (21 EMA, gap fill, stop first, gap through, lock ...)
    python tools/test_gapbo.py ind           # TradingView EMA / RSI against a plain loop (with missing days)
    python tools/test_gapbo.py signals       # vectorised setup == plain per-stock code on a synthetic panel
    python tools/test_gapbo.py lookahead     # future prices changed -> indicators and setups up to that day stay the same
    python tools/test_gapbo.py portfolio     # sizing, 20% cap, cash, max positions, one position per stock
    python tools/test_gapbo.py lookahead --real   # look-ahead check on the real daily data (data/daily/equity_daily.parquet)
    python tools/test_gapbo.py hand          # real data: random baseline trades recomputed by hand from the stock's own CSV
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

import numpy as np  # noqa: E402
import pandas as pd  # noqa: E402

from backtest import gapbo as G  # noqa: E402

P = G.Params()


def same(a, b) -> bool:
    a, b = np.asarray(a, dtype=float), np.asarray(b, dtype=float)
    return a.shape == b.shape and bool(np.all((np.isnan(a) & np.isnan(b)) | (np.abs(a - b) <= 1e-9 * np.maximum(1, np.abs(a)))))


# ---------------------------------------------------------------- synthetic panel
def synth(n_stocks: int = 8, days: int = 900, seed: int = 1) -> dict:
    rng = np.random.default_rng(seed)
    cal = [d.strftime("%Y-%m-%d") for d in pd.bdate_range("2012-01-02", periods=days)]
    syms = [f"S{i}" for i in range(n_stocks)]
    cols = {k: np.empty((days, n_stocks)) for k in ("open", "high", "low", "close", "volume")}
    for j in range(n_stocks):
        r = rng.normal(0.0012, 0.017, days)
        gap = np.where(rng.random(days) < 0.04, rng.uniform(0.005, 0.07, days) * rng.choice([1, 1, 1, -1], days), 0.0)
        o, c, prev = np.empty(days), np.empty(days), 100.0
        for t in range(days):
            o[t] = prev * (1 + gap[t] + rng.normal(0, 0.002))
            c[t] = o[t] * (1 + r[t])
            prev = c[t]
        cols["open"][:, j], cols["close"][:, j] = o, c
        cols["high"][:, j] = np.maximum(o, c) * (1 + rng.uniform(0, 0.012, days))
        cols["low"][:, j] = np.minimum(o, c) * (1 - rng.uniform(0, 0.012, days))
        cols["volume"][:, j] = rng.uniform(1e5, 1e6, days)
    W = {k: pd.DataFrame(v, index=cal, columns=syms) for k, v in cols.items()}
    for k in W:
        W[k].iloc[400:403, 1] = np.nan          # S1 suspended three days
        W[k].iloc[:260, 2] = np.nan             # S2 listed late
    n50 = pd.Series(10000 * np.exp(np.cumsum(rng.normal(0.0004, 0.01, days))), index=cal)
    D = {"W": W, "cal": cal, "syms": syms, "idx": {"NIFTY50": n50, "NIFTY200": n50 * 0.6}, "mcap": {s: 1000.0 * (j + 1) for j, s in enumerate(syms)},
         "cat": {}, "bad_rows": 0}
    D["shares"] = G.shares(D)
    return D


# ---------------------------------------------------------------- plain reference code
def tv_ema(x, n, alpha=None):
    a = 2 / (n + 1) if alpha is None else alpha
    out, seen, prev = np.full(len(x), np.nan), [], None
    for t, v in enumerate(x):
        if v != v:
            continue
        seen.append(v)
        if len(seen) == n:
            prev = sum(seen) / n
        elif len(seen) > n:
            prev = a * v + (1 - a) * prev
        if len(seen) >= n:
            out[t] = prev
    return out


def tv_rsi(c, n):
    ups, dns, last = np.full(len(c), np.nan), np.full(len(c), np.nan), np.nan
    for t, v in enumerate(c):
        if v != v:
            continue
        if last == last:
            ups[t], dns[t] = max(v - last, 0.0), max(last - v, 0.0)
        last = v
    ru, rd = tv_ema(ups, n, 1 / n), tv_ema(dns, n, 1 / n)
    out = np.full(len(c), np.nan)
    for t in range(len(c)):
        if ru[t] == ru[t] and rd[t] == rd[t]:
            out[t] = 100.0 if rd[t] == 0 else 0.0 if ru[t] == 0 else 100 - 100 / (1 + ru[t] / rd[t])
    return out


def plain_setups(D: dict, p) -> set:
    """(day, stock, s0) of every setup, written stock by stock with pandas / loops, without the engine's arrays."""
    W, cal, syms = D["W"], D["cal"], D["syms"]
    months = [d[:7] for d in cal]
    month_last = {}                                         # month -> last calendar row
    for t, m in enumerate(months):
        month_last[m] = t
    order = sorted(month_last)
    complete = set(order[:-1])
    # universe: rank of the 120-day average market cap at each month end, used for the next month
    mcap = W["close"].ffill() * D["shares"]
    avg = mcap.rolling(120, min_periods=60).mean()
    in_uni = {}
    for n_m, m in enumerate(order[:-1]):
        row = avg.iloc[month_last[m]]
        ranked = sorted([s for s in syms if row[s] == row[s]], key=lambda s: -row[s])
        top = set(ranked[:p.top_n])
        in_uni[order[n_m + 1]] = top
    n50 = D["idx"]["NIFTY50"]
    out = set()
    for j, s in enumerate(syms):
        o, h, l, c = (W[k][s] for k in ("open", "high", "low", "close"))
        pc, ph = c.ffill().shift(1), h.ffill().shift(1)
        ema = tv_ema(c.to_numpy(), p.ema_n)
        rsi_s = pd.Series(tv_rsi(c.to_numpy(), p.rsi_n)).rolling(p.rsi_sma, min_periods=p.rsi_sma).mean().to_numpy()
        hh = h.shift(1).rolling(p.hh_n, min_periods=p.hh_n).max().to_numpy()
        cf = c.ffill()
        mclose = {m: cf.iloc[month_last[m]] for m in order}
        mema = dict(zip(order, tv_ema(np.array([mclose[m] for m in order]), p.month_ema)))
        for t in range(1, len(cal)):
            if c.iloc[t] != c.iloc[t]:
                continue
            m = months[t]
            ref = m if (t == month_last[m] and m in complete) else (order[order.index(m) - 1] if order.index(m) > 0 else None)
            mstate = ref is not None and mema[ref] == mema[ref] and mclose[ref] > mema[ref]
            f = ph.iloc[t] if p.fill == "high" else pc.iloc[t]
            g = o.iloc[t] / pc.iloc[t] - 1
            ok = (s in in_uni.get(m, set()) and o.iloc[t] > ph.iloc[t] and g >= p.gap and g <= p.max_gap and o.iloc[t] > hh[t] and c.iloc[t] > ema[t]
                  and p.rsi_lo < rsi_s[t] < p.rsi_ob and mstate and c.iloc[t] > f)
            if ok:
                out.add((cal[t], s, round(float(f if p.exit == "monthly" else max(f, ema[t])), 6)))
    return out


# ---------------------------------------------------------------- tests
def test_ind() -> bool:
    rng = np.random.default_rng(7)
    X = 100 + np.cumsum(rng.normal(0, 1, (400, 5)), axis=0)
    X[:37, 1] = np.nan
    X[[100, 101, 250], 2] = np.nan
    ok = True
    for n in (3, 21):
        a = G.ema_tv(X, n)
        b = np.column_stack([tv_ema(X[:, j], n) for j in range(X.shape[1])])
        ok &= same(a, b)
    a, b = G.rsi_tv(X, 10), np.column_stack([tv_rsi(X[:, j], 10) for j in range(X.shape[1])])
    ok &= same(a, b)
    print(f"  TradingView EMA (3, 21) and RSI(10) == plain loop (with missing days): {'OK' if ok else 'FAIL'}")
    return ok


def _I(rows, mstate=None, ca=None):
    a = np.array(rows, dtype=float)
    ms = np.array([True] * len(a) if mstate is None else mstate)
    cav = np.array([False] * len(a) if ca is None else ca)
    return {"O": a[:, [0]], "H": a[:, [1]], "L": a[:, [2]], "C": a[:, [3]], "ema": a[:, [4]], "mstate": ms[:, None], "ca": cav[:, None]}


def test_walk() -> bool:
    W_ = G.with_params
    up = [(100, 102, 99, 101, 95)]
    cases = [
        ("close < 21 EMA -> next open", _I(up + [(101, 103, 100, 102, 96), (102, 102, 94, 94.5, 96.5), (93, 95, 92, 94, 96)]), 100, 90, 95, P,
         (3, 93.0, "close 21 EMA ke neeche")),
        ("gap filled on a close -> next open (open below the stop = fill)", _I(up + [(100, 100.5, 97, 97.5, 96), (96, 97, 95, 96.5, 96)]), 100, 98, 98, P,
         (2, 96.0, "gap bhar gaya (close gap-fill level ke neeche)")),
        ("intraday stop and 2R target in one bar -> stop first", _I(up[:0] + [(100, 101, 99, 100.5, 95), (100.5, 104.5, 97.5, 103, 96)]), 100, 98, 98,
         W_(P, fill_stop="intraday", exit="rr2"), (1, 98.0, "gap fill stop (din me level chhua)")),
        ("open above the target -> fill at the open", _I([(100, 101, 99, 100.5, 95), (106, 107, 105, 106.5, 96)]), 100, 98, 98, W_(P, exit="rr2"),
         (1, 106.0, "target 2R")),
        ("target inside the entry day (after the open fill)", _I([(100, 104.2, 99.5, 103, 95), (103, 104, 102, 103, 96)]), 100, 98, 98, W_(P, exit="rr2"),
         (0, 104.0, "target 2R")),
        ("open below the intraday stop -> fill at the open", _I([(100, 101, 99, 100.5, 95), (96, 99, 95, 98, 96)]), 100, 98, 98, W_(P, fill_stop="intraday"),
         (1, 96.0, "gap fill stop (open stop ke neeche, open pe)")),
        ("locked limit-down day: exit waits a day", _I(up + [(100, 100, 94, 94, 96), (90, 90, 90, 90, 95), (88, 91, 87, 90, 94)]), 100, 90, 95, P,
         (3, 88.0, "close 21 EMA ke neeche")),
        ("monthly exit ignores the 21 EMA, leaves on the monthly break", _I(up + [(101, 103, 100, 94, 96), (94, 96, 93, 95, 96), (94.5, 95, 93, 94, 95)],
                                                                           [True, True, False, False]), 100, 90, 90, W_(P, exit="monthly"),
         (3, 94.5, "monthly trend toota (mahina 20-month EMA ke neeche band)")),
        ("entry at the gap-day close: no stop on the entry bar", _I([(95, 101, 94, 100, 95), (97, 99, 96.5, 98.5, 96)]), 100, 98, 98,
         W_(P, entry="close", fill_stop="intraday"), (1, 97.0, "gap fill stop (open stop ke neeche, open pe)")),
        ("no exit before the data ends -> last close", _I(up + [(101, 103, 100, 102, 96), (102, 104, 101, 103, 97)]), 100, 90, 95, P,
         (2, 103.0, "data khatam (trade khula, aakhri close pe)")),
        ("missing day inside the trade is skipped", _I(up + [(np.nan,) * 5, (99, 99, 93, 93.5, 96), (92, 93, 91, 92, 95)]), 100, 90, 95, P,
         (3, 92.0, "close 21 EMA ke neeche")),
        ("demerger ex-date while holding -> sold at the close before it", _I(up + [(101, 103, 100, 102, 96), (60, 62, 59, 61, 95)], ca=[False, False, True]),
         100, 90, 95, P, (1, 102.0, "corporate action (demerger) se pehle, pichhle close pe becha")),
    ]
    trend = [(100 + i, 101.5 + i, 99.5 + i, 101 + i, 95 + i) for i in range(25)]
    cases.append(("time stop 20 days (entry day = day 1)", _I(trend), 100, 90, 95, W_(P, exit="t20"), (20, 120.0, "time stop (20 din)")))
    ok = True
    for name, I, e, f, s0, p, want in cases:
        got = G._walk(I, 0, 0, e, f, s0, p)
        good = got[0] == want[0] and abs(got[1] - want[1]) < 1e-9 and got[2] == want[2]
        ok &= good
        print(f"  {name}: {'OK' if good else f'FAIL got {got} want {want}'}")
    return ok


def test_signals() -> bool:
    D = synth()
    ok = True
    for kw in ({}, {"gap": 0.01, "rsi_ob": 90.0, "hh_n": 40}, {"fill": "close", "rsi_sma": 5, "exit": "monthly", "top_n": 4}):
        p = G.with_params(P, **kw)
        S = G.signals(G.indicators(D, p), p)
        got = {(D["cal"][int(r.i)], D["syms"][int(r.j)], round(float(r.s0), 6)) for r in S.itertuples()}
        want = plain_setups(D, p)
        good = got == want
        ok &= good
        print(f"  setups {kw or 'baseline'}: engine {len(got)}, plain code {len(want)} -> {'OK' if good else 'FAIL ' + str(sorted(got ^ want)[:5])}")
    return ok


def test_lookahead(real: bool = False) -> bool:
    D = G.load() if real else synth(n_stocks=12, days=1100, seed=3)
    W, cal = D["W"], D["cal"]
    p = G.with_params(P, gap=0.01, rsi_ob=90.0) if not real else P
    I = G.indicators(D, p)
    S = G.signals(I, p)
    keys = ["i", "j", "gap_pct", "fill_lvl", "s0", "rsi", "rsi_s", "ema", "hh", "mdist", "rank", "bull"]
    ok = True
    for t in (len(cal) // 2, 2 * len(cal) // 3, 5 * len(cal) // 6):      # after the 20-month warm-up, so there are setups to compare
        rng = np.random.default_rng(t)
        Wp = {k: v.copy() for k, v in W.items()}
        noise = rng.uniform(0.7, 1.3, size=(len(cal) - t - 1, W["close"].shape[1]))
        for k in ("open", "high", "low", "close"):
            Wp[k].iloc[t + 1:] = W[k].iloc[t + 1:].to_numpy() * noise
        Wp["volume"].iloc[t + 1:] = W["volume"].iloc[t + 1:].to_numpy() * rng.uniform(0.2, 5, size=noise.shape)
        n50 = D["idx"]["NIFTY50"].copy()
        later = n50.index > cal[t]
        n50[later] = n50[later] * rng.uniform(0.7, 1.3, int(later.sum()))
        Dp = {**D, "W": Wp, "idx": {**D["idx"], "NIFTY50": n50}}          # same share-count proxy (see gapbo.shares)
        Ip = G.indicators(Dp, p)
        arr_ok = all(same(np.asarray(I[k][:t + 1], dtype=float), np.asarray(Ip[k][:t + 1], dtype=float))
                     for k in ("ema", "rsi_s", "hh", "mstate", "uni", "rank", "bull", "mdist"))
        Sp = G.signals(Ip, p)
        a, b = S[S.i <= t][keys].reset_index(drop=True), Sp[Sp.i <= t][keys].reset_index(drop=True)
        sig_ok = len(a) == len(b) and all(same(a[c], b[c]) for c in keys)
        good = arr_ok and sig_ok and len(a) > 0
        ok &= good
        print(f"  future changed after {cal[t]}: indicators same {arr_ok}, {len(a)} setups same {sig_ok} -> {'OK' if good else 'FAIL'}")
    return ok


def test_hand(n: int = 8) -> bool:
    """Real data: random baseline trades (21 EMA / gap-fill exits) recomputed from the stock's own CSV with plain pandas."""
    import math
    D = G.load()
    I = G.indicators(D, P)
    t, _ = G.portfolio(D, I, G.paths(I, G.signals(I, P), P), P)
    t = t[t.reason.isin(["close 21 EMA ke neeche", "gap bhar gaya (close gap-fill level ke neeche)"])]
    ok = True
    for r in t.sample(min(n, len(t)), random_state=7).itertuples():
        x = pd.read_csv(G.DAILY / "equity" / f"{r.symbol}.csv")
        i = int(x.index[x.date == r.signal_date][0])
        c = x.close.to_numpy()
        e21 = tv_ema(c, 21)
        rsi_s = pd.Series(tv_rsi(c, 10)).rolling(3).mean().to_numpy()
        m = x.assign(mo=x.date.str[:7]).groupby("mo").close.last()
        mema = pd.Series(tv_ema(m.to_numpy(), 20), index=m.index)
        pm = m.index[m.index.get_loc(r.signal_date[:7]) - 1]
        o, h = x.open.to_numpy(), x.high.to_numpy()
        setup = (o[i] > h[i - 1] and o[i] / c[i - 1] - 1 >= 0.02 and o[i] > h[i - 60:i].max() and c[i] > e21[i] and 60 < rsi_s[i] < 80
                 and m[pm] > mema[pm] and c[i] > h[i - 1])
        stop = max(h[i - 1], e21[i])
        k = i + 1
        while not (c[k] < h[i - 1] or c[k] < e21[k]):
            k += 1
        ef, xf = o[i + 1] * (1 + P.slip), o[k + 1] * (1 - P.slip)
        net = r.qty * (xf - ef) - G.costs(r.qty * ef, [r.qty * xf])["costs"]
        qmax = math.floor(min(P.capital * P.risk / (ef - stop), P.capital * P.max_pos_pct / ef))
        good = (setup and abs(o[i + 1] - r.entry) < 1e-9 and abs(stop - r.stop) < 1e-6 and x.date[k + 1] == r.exit_date and abs(o[k + 1] - r.exit) < 1e-9
                and abs(net - r.net) < 1e-6 and r.qty <= qmax)
        ok &= good
        print(f"  {r.symbol} {r.signal_date}: setup {setup}, entry {o[i + 1]}, stop {stop:.2f}, exit {x.date[k + 1]} @ {o[k + 1]}, net {net:,.2f} vs engine {r.net:,.2f} "
              f"-> {'OK' if good else 'FAIL'}")
    return ok


def test_portfolio() -> bool:
    T, N = 40, 8
    cal = [d.strftime("%Y-%m-%d") for d in pd.bdate_range("2020-01-01", periods=T)]
    D = {"cal": cal, "syms": [f"S{j}" for j in range(N)]}
    I = {"adv": np.full((T, N), 1e7), "Cf": np.full((T, N), 100.0), "Hp": np.full((T, N), 99.0), "Cp": np.full((T, N), 98.0), "O": np.full((T, N), 101.0),
         "C": np.full((T, N), 100.0)}
    base = {"gap_pct": 3.0, "fill_lvl": 95.0, "rsi": 70.0, "rsi_s": 68.0, "ema": 90.0, "dist_ema": 5.0, "hh": 97.0, "mdist": 0.2, "rank": 10.0, "bull": 1.0,
            "reason": "x", "outcome": "entry", "why": ""}
    X = pd.DataFrame([{**base, "i": 9, "j": 0, "k": 10, "x": 20, "e": 100.0, "s0": 95.0, "px": 110.0},     # A: 2000 sh (risk and cap both 2000)
                      {**base, "i": 10, "j": 1, "k": 11, "x": 15, "e": 50.0, "s0": 49.0, "px": 48.0},      # B: cap 4000 sh (risk wants 10000)
                      {**base, "i": 11, "j": 2, "k": 12, "x": 30, "e": 100.0, "s0": 90.0, "px": 100.0},    # C: 2 positions open -> skipped
                      {**base, "i": 15, "j": 0, "k": 16, "x": 25, "e": 100.0, "s0": 95.0, "px": 100.0},    # D: S0 still held -> skipped
                      {**base, "i": 15, "j": 3, "k": 16, "x": 25, "e": 100.0, "s0": 80.0, "px": 120.0}])   # E: B gone on day 15 -> 500 sh (risk)
    p = G.with_params(P, max_open=2, slip=0.0)
    t, m = G.portfolio(D, I, X, p)
    ok = list(t.symbol) == ["S0", "S1", "S3"] and list(t.qty) == [2000, 4000, 500]
    ok &= m["skipped"] == {"2 position pehle se khuli": 1, "stock pehle se portfolio me": 1}
    c = G.costs(2000 * 100.0, [2000 * 110.0])
    ok &= abs(t.net.iloc[0] - (2000 * 10.0 - c["costs"])) < 1e-6 and abs(t.R.iloc[0] - t.net.iloc[0] / (2000 * 5.0)) < 1e-9
    ok &= abs(t.beyond_rs.iloc[1] - 4000 * 1.0) < 1e-9                       # B left at 48 with the stop at 49
    ok &= abs(m["pnl"][12] - (2000 * 0 + 4000 * (100 - 50))) < 1e-6          # mark-to-market uses the close (100 in this toy data)
    # cash: six 20% positions on one day -> only five fit (no leverage)
    X2 = pd.DataFrame([{**base, "i": 9, "j": j, "k": 10, "x": 20, "e": 100.0, "s0": 95.0, "px": 100.0} for j in range(6)])
    t2, m2 = G.portfolio(D, I, X2, G.with_params(P, slip=0.0))
    ok2 = len(t2) == 5 and m2["skipped"] == {"paisa kam (cash / volume limit)": 1} and abs(m2["dep"][10] - 1_000_000) < 1e-6
    print(f"  risk / 20% cap / max positions / same stock / net, R, beyond-stop / mark-to-market: {'OK' if ok else 'FAIL'}")
    print(f"  cash limit (no leverage): {'OK' if ok2 else 'FAIL'}")
    return ok and ok2


def main() -> int:
    which = sys.argv[1] if len(sys.argv) > 1 and not sys.argv[1].startswith("--") else "all"
    real = "--real" in sys.argv
    tests = {"ind": test_ind, "walk": test_walk, "signals": test_signals, "lookahead": lambda: test_lookahead(real), "portfolio": test_portfolio, "hand": test_hand}
    ok = True
    for name, fn in tests.items():
        if which == name or (which == "all" and name != "hand"):
            print(name)
            ok &= fn()
    print("ALL OK" if ok else "SOME CHECKS FAILED")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
