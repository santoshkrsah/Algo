"""Checks for the gapfade strategy (strategies/RULEBOOK.md 7a). Plain script, no pytest:

    python tools/test_gapfade.py              # all three
    python tools/test_gapfade.py lookahead    # 1. selection features / entries do not use later candles
    python tools/test_gapfade.py hand         # 2. three trades (long, short, stop hit) recomputed by hand from raw 1m data
    python tools/test_gapfade.py csv          # 3. store vs CSV, workers=1 vs workers>1 give the same trades

Exit code 1 if any check fails.
"""
import math
import shutil
import sys
import tempfile
from dataclasses import replace
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

import numpy as np  # noqa: E402
import pandas as pd  # noqa: E402

from backtest import fast  # noqa: E402
from backtest import rulebook as rb  # noqa: E402

SYMS = ["ADANIENT", "ADANIPORTS", "AXISBANK", "BAJFINANCE", "BANKBARODA", "BHEL", "CANBK", "DLF", "HINDALCO", "ICICIBANK",
        "INFY", "IRCTC", "JINDALSTEL", "LT", "MARUTI", "ONGC", "PNB", "RELIANCE", "SAIL", "SBIN", "TATAMOTORS", "TATASTEEL",
        "VEDL", "ZEEL", "TATAPOWER", "HDFCBANK", "BPCL", "COALINDIA", "INDUSINDBK", "M&M"]
P = rb.with_defaults(replace(rb.Params(), strategy="gapfade", start="2021-10-01", end="2023-12-31", oos_start="2023-01-01"))
FAIL = []


def check(name, ok, detail=""):
    print(f"  [{'PASS' if ok else 'FAIL'}] {name}" + (f"  ({detail})" if detail and not ok else ""))
    if not ok:
        FAIL.append(name)


def syms_in_store():
    have = set(rb.store_symbols())
    return [s for s in SYMS if s in have]


# ------------------------------------------------------------------------------------------- 1. no lookahead
def test_lookahead():
    print("1. No-lookahead")
    rng = np.random.default_rng(1)
    feats = ["d_open", "prev_close", "atr", "avg_to", "adv", "rvol", "gap_atr", "ok"]
    n_days = n_tr = 0
    for sym in syms_in_store()[:8]:
        _, dates, O, H, L, C, V = rb.load_symbol(rb.STORE_PREFIX + sym, None)
        f0 = rb.daily_features(O, H, L, C, V, P)
        t0 = rb.gen_gapfade(O, H, L, C, V, f0, P)
        # (a) selection features of day k: change every candle after the decision minute (m >= 5) of day k and all later days
        for k in rng.choice(np.arange(30, len(dates) - 1), 6, replace=False):
            O2, H2, L2, C2, V2 = (a.copy() for a in (O, H, L, C, V))
            for a in (O2, H2, L2, C2):
                a[k, 5:] *= rng.uniform(0.7, 1.3, a[k, 5:].shape).astype(np.float32)
                a[k + 1:] *= np.float32(rng.uniform(0.5, 1.5))
            V2[k, 5:] *= 7
            V2[k + 1:] *= 3
            f2 = rb.daily_features(O2, H2, L2, C2, V2, P)
            same = all(np.array_equal(f0[x][k], f2[x][k], equal_nan=True) for x in feats)
            check(f"{sym} {str(dates[k])}: selection features same after changing later candles", same)
            n_days += 1
        # (b) entries: change candles after the entry minute of that day (and later days) -> entry / stop / target unchanged
        if t0 is None:
            continue
        for r in t0.sample(min(3, len(t0)), random_state=1).itertuples():
            d, em = int(r.day), int(r.entry_m)
            O2, H2, L2, C2 = (a.copy() for a in (O, H, L, C))
            for a in (O2, H2, L2, C2):
                a[d, em + 1:] *= rng.uniform(0.8, 1.2, a[d, em + 1:].shape).astype(np.float32)
                a[d + 1:] *= np.float32(1.1)
            t2 = rb.gen_gapfade(O2, H2, L2, C2, V, rb.daily_features(O2, H2, L2, C2, V, P), P)
            m = t2[(t2.day == d)] if t2 is not None else pd.DataFrame()
            ok = len(m) == 1 and int(m.side.iloc[0]) == r.side and int(m.entry_m.iloc[0]) == em and all(
                math.isclose(float(m[c].iloc[0]), getattr(r, c), rel_tol=1e-12) for c in ("entry_px", "stop_px", "target_px"))
            check(f"{sym} {str(dates[d])}: entry same after changing candles after {rb.hhmm(em)}", ok)
            n_tr += 1
    print(f"  checked {n_days} days + {n_tr} entries")


# ------------------------------------------------------------------------------------- 2. three trades by hand
def daily_by_hand(sym):
    """Day table straight from the 1m rows (own code, not rulebook.py): quality filter, missing minute = last close."""
    m1 = fast.symbol_1m(sym)
    m1 = m1[(m1.minute >= 555) & (m1.minute <= 929)]
    days = {}
    for d, g in m1.groupby("date", sort=True):
        g = g.set_index("minute").reindex(range(555, 930))
        if pd.isna(g.loc[555, "close"]) or g["close"].notna().mean() < 0.95:
            continue
        c = g["close"].ffill()
        for k in ("open", "high", "low"):
            g[k] = g[k].fillna(c)
        g["close"], g["volume"] = c, g["volume"].fillna(0)
        days[d] = g
    keys = list(days)
    rows = []
    for i, d in enumerate(keys):
        g = days[d]
        rows.append(dict(date=d, o=g.loc[555, "open"], h=g["high"].max(), l=g["low"].min(), c=g.loc[929, "close"], v=g["volume"].sum()))
    df = pd.DataFrame(rows)
    pc = df.c.shift(1)
    df["pc"] = pc
    tr = np.maximum(df.h - df.l, np.maximum((df.h - pc).abs(), (df.l - pc).abs()))
    df["atr"] = tr.rolling(14).mean().shift(1)
    df["adv"] = df.v.rolling(20).mean().shift(1)
    return df.set_index("date"), days


def costs_by_hand(buy, sell):
    brk = min(20, 0.0003 * buy) + min(20, 0.0003 * sell)
    exch, sebi = 0.0000307 * (buy + sell), 10 / 1e7 * (buy + sell)
    return brk + 0.00025 * sell + exch + sebi + 0.00003 * buy + 0.18 * (brk + exch + sebi)


def hand_trade(t, eq):
    d = str(t.date.date())
    dt, days = daily_by_hand(t.symbol)
    row, g = dt.loc[d], days[d]
    atr, pc = row.atr, row.pc
    orh, orl = g.loc[555:569, "high"].max(), g.loc[555:569, "low"].min()
    side = 1 if row.o < pc else -1
    entry = em = None
    for m in range(570, 675):                                   # m = 15 .. 119
        if side == 1 and g.loc[m, "high"] >= orh:
            entry, em = max(orh, g.loc[m, "open"]), m
            break
        if side == -1 and g.loc[m, "low"] <= orl:
            entry, em = min(orl, g.loc[m, "open"]), m
            break
    stop = orl - 0.1 * atr if side == 1 else orh + 0.1 * atr
    target = entry + P.tp_frac * (pc - entry)
    xp, xm, why = g.loc[915, "open"], 915, "eod"
    for m in range(em, 915):                                    # entry candle .. m = 359
        o, h, l_ = g.loc[m, "open"], g.loc[m, "high"], g.loc[m, "low"]
        s_hit = l_ <= stop if side == 1 else h >= stop
        t_hit = h >= target if side == 1 else l_ <= target
        if s_hit:
            xp = stop if m == em else (min(stop, o) if side == 1 else max(stop, o))
            xm, why = m, "stop"
            break
        if t_hit:
            xp = target if m == em else (max(target, o) if side == 1 else min(target, o))
            xm, why = m, "target"
            break
    prev = eq[eq.date < t.date]
    E0 = prev.equity.iloc[-1] if len(prev) else P.capital
    sh = math.floor(min(0.01 * E0 / abs(entry - stop), 5 * E0 / (5 * entry), 0.01 * row.adv))
    s = 0.0003
    if side == 1:
        ef, xf = entry * (1 + s), xp * (1 - s)
        buy, sell = ef * sh, xf * sh
    else:
        ef, xf = entry * (1 - s), xp * (1 + s)
        buy, sell = xf * sh, ef * sh
    net = side * (xp - entry) * sh - (abs(ef - entry) + abs(xf - xp)) * sh - costs_by_hand(buy, sell)
    return dict(side="LONG" if side == 1 else "SHORT", entry_time=rb.hhmm(em - 555), entry_px=entry, stop_px=stop, target_px=target,
                exit_time=rb.hhmm(xm - 555), exit_px=xp, reason=why, shares=sh, net_pnl=net)


def test_hand():
    print("2. Three trades by hand (long, short, stop hit)")
    files = [rb.STORE_PREFIX + s for s in syms_in_store()]
    _, eq, tr, _, _ = rb.run_once(files, None, P, verbose=False, workers=1)
    if len(tr) < 3:
        check("enough trades for the hand check", False, f"{len(tr)} trades")
        return
    picks = []
    for cond in (lambda x: (x.side == "LONG") & (x.reason != "stop"), lambda x: (x.side == "SHORT") & (x.reason != "stop"),
                 lambda x: x.reason == "stop"):
        c = tr[cond(tr) & ~tr.index.isin([i for i, _ in picks])]
        if len(c):
            picks.append((c.index[len(c) // 2], c.iloc[len(c) // 2]))
    check("found a long, a short and a stop-hit trade", len(picks) == 3, f"{len(picks)} found")
    for _, t in picks:
        h = hand_trade(t, eq)
        diffs = []
        for k, v in h.items():
            e = t[k]
            same = (str(e) == str(v)) if isinstance(v, str) else (abs(float(e) - float(v)) <= (0.011 if k != "net_pnl" else 0.02))
            if not same:
                diffs.append(f"{k}: engine {e} vs hand {v}")
        check(f"{t.symbol} {t.date.date()} {h['side']} {h['entry_time']}->{h['exit_time']} {h['reason']} "
              f"entry {h['entry_px']:.2f} SL {h['stop_px']:.2f} tgt {h['target_px']:.2f} exit {h['exit_px']:.2f} qty {h['shares']} "
              f"net {h['net_pnl']:.2f}", not diffs, "; ".join(diffs))


# ----------------------------------------------------------------------------- 3. store vs CSV, 1 vs n workers
def test_csv():
    print("3. Store vs CSV, workers=1 vs workers=4")
    adj = set(fast.adjustments()["symbol"])
    syms = [s for s in syms_in_store() if s not in adj][:20]
    lo, hi = "2022-10-01", "2023-03-31"
    tmp = Path(tempfile.mkdtemp(prefix="gapfade_csv_"))
    try:
        parts = {s: [] for s in syms}
        days = fast.days(lo, hi)
        for d in days:                                           # raw daily CSVs (not the store)
            raw = fast._csv_1m(d)
            raw = raw[raw.symbol.isin(syms)]
            for s, g in raw.groupby("symbol"):
                hh, mm = g.minute.to_numpy() // 60, g.minute.to_numpy() % 60
                parts[s].append(pd.DataFrame({"datetime": [f"{d} {a:02d}:{b:02d}:00" for a, b in zip(hh, mm)],
                                              **{k: g[k].to_numpy() for k in ("open", "high", "low", "close", "volume")}}))
        for s in syms:
            if parts[s]:
                pd.concat(parts[s]).to_csv(tmp / f"{s}.csv", index=False)
        q = replace(P, start="2022-12-01", end=hi, oos_start="2023-02-01")
        csv_files = rb.list_csvs(str(tmp))
        st_files = [rb.STORE_PREFIX + Path(f).stem for f in csv_files]
        res = {}
        for name, files, cache, w in (("store w1", st_files, None, 1), ("store w4", st_files, None, 4),
                                      ("csv w1", csv_files, str(tmp / ".cache"), 1), ("csv w4", csv_files, str(tmp / ".cache"), 4)):
            uni, cand = rb.build_tables(files, cache, q, verbose=False, workers=w)
            # the CSVs hold lo..hi only (the first weeks are feature warm-up); the store has every day -> compare q.start..hi
            uni = uni[(uni.date >= q.start) & (uni.date <= hi)].reset_index(drop=True)
            cand = cand[(cand.date >= q.start) & (cand.date <= hi)].reset_index(drop=True)
            eq, tr = rb.simulate(rb.attach_day_flags(uni, q), cand, q)
            res[name] = (uni, cand, eq, tr)

        def same_df(a, b):
            if len(a) != len(b) or list(a.columns) != list(b.columns):
                return False
            a, b = a.reset_index(drop=True), b.reset_index(drop=True)
            num = [c for c in a.columns if a[c].dtype.kind in "fib"]
            return (a.drop(columns=num).astype(str).equals(b.drop(columns=num).astype(str))
                    and np.allclose(a[num].to_numpy(float), b[num].to_numpy(float), equal_nan=True, rtol=0, atol=1e-6))
        b_uni, b_cand, b_eq, b_tr = res["store w1"]
        print(f"  {len(csv_files)} stocks, {len(days)} days: {len(b_uni)} stock-days of features, {len(b_cand)} trade candidates, "
              f"{len(b_tr)} selected trades (store, workers=1)")
        check("store run has candidates to compare", len(b_cand) > 0)
        for name, (uni, cand, eq, tr) in res.items():
            if name == "store w1":
                continue
            check(f"{name} == store w1: daily features", same_df(b_uni, uni), f"{len(uni)} vs {len(b_uni)} rows")
            check(f"{name} == store w1: trade candidates", same_df(b_cand, cand), f"{len(cand)} vs {len(b_cand)}")
            check(f"{name} == store w1: trades + equity", same_df(b_tr, tr) and np.allclose(eq.equity, b_eq.equity, rtol=0, atol=1e-4),
                  f"{len(tr)} vs {len(b_tr)}")
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


if __name__ == "__main__":
    which = sys.argv[1:] or ["lookahead", "hand", "csv"]
    for w in which:
        {"lookahead": test_lookahead, "hand": test_hand, "csv": test_csv}[w]()
    print("\nRESULT:", "ALL PASS" if not FAIL else f"{len(FAIL)} FAIL -> " + "; ".join(FAIL))
    sys.exit(1 if FAIL else 0)
