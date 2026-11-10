"""Data-quality checks on data/bars before any backtest. One row per (dataset, check) with a count, examples and what
the framework does about it.

    from backtest.podcast import quality
    q = quality.report()          # DataFrame; also written to <bars>/quality_report.csv
"""
import numpy as np
import pandas as pd

from backtest.podcast import data
from backtest.podcast.config import CONFIG, INDEX_FUT, SPOT_OF, STOCKS

OUTLIER_1M = {"index": 1.5, "fut": 1.5, "stock": 4.0}        # % range of one 1-minute candle
JUMP_1M = {"index": 2.0, "fut": 2.0, "stock": 5.0}           # % close-to-close inside a day
GAP_DAILY = 10.0                                             # % stock overnight move with the index calm (< 3%)


def _ex(s, n=4) -> str:
    s = list(s)
    return ", ".join(str(x) for x in s[:n]) + (f" ... (+{len(s) - n})" if len(s) > n else "")


def _raw(name: str, tf: str) -> pd.DataFrame:
    df = pd.read_parquet(data.BARS / f"{name}_{tf}.parquet")
    df["datetime"] = pd.to_datetime(df["datetime"])
    return df


def check_1m(name: str, kind: str) -> list[dict]:
    rows = []
    df = _raw(name, "1m")
    add = lambda chk, n, ex, how: rows.append({"dataset": f"{name}_1m", "check": chk, "count": int(n), "examples": ex, "handling": how})
    dup = df["datetime"].duplicated()
    add("duplicate candles", dup.sum(), _ex(df.loc[dup, "datetime"].dt.strftime("%Y-%m-%d %H:%M")), "dropped (last copy kept)")
    df = df[~dup]
    m = df["datetime"].dt.hour * 60 + df["datetime"].dt.minute
    lo, hi = CONFIG["data"]["session"]
    out = df[(m < lo) | (m > hi)]
    late = out[(out["datetime"].dt.hour * 60 + out["datetime"].dt.minute).between(930, 945)]
    odd = out.drop(late.index)
    add("candles 15:30-15:45 (after the close)", len(late), f"on {late['datetime'].dt.normalize().nunique()} days; volume {int(late['volume'].sum()):,}",
        "dropped: the session is 09:15-15:29")
    add("candles at other hours (Muhurat / time-zone)", len(odd), _ex(sorted(odd["datetime"].dt.strftime("%Y-%m-%d %H:%M").unique())),
        "dropped (special evening sessions); no time-zone shift found" if len(odd) < 2000 else "CHECK time zone")
    s = df[(m >= lo) & (m <= hi)]
    day = s["datetime"].dt.normalize()
    n = s.groupby(day).size()
    first = s.groupby(day)["datetime"].min().dt.strftime("%H:%M")
    add("days not starting 09:15", (first != "09:15").sum(), _ex(first[first != "09:15"].index.strftime("%Y-%m-%d")), "kept (first candle missing)")
    short = n[n < CONFIG["data"]["min_day_bars"]]
    add(f"short days (< {CONFIG['data']['min_day_bars']} of 375 candles)", len(short), _ex(f"{d:%Y-%m-%d} ({k})" for d, k in short.items()),
        "dropped from intraday tests (special / Muhurat / broken sessions)")
    gaps = n[(n >= CONFIG["data"]["min_day_bars"]) & (n < 375)]
    add("days with missing candles (300-374)", len(gaps), f"{int((375 - gaps).sum()):,} candles missing in all", "kept; missing minutes are simply absent")
    p = s[data.PRICE].to_numpy()
    z = s[(p <= 0).any(axis=1)]
    add("zero / negative prices", len(z), _ex(z["datetime"].dt.strftime("%Y-%m-%d %H:%M")), "none found" if not len(z) else "candles dropped")
    bad = (s["high"] < s[["open", "close"]].max(axis=1) - 1e-9) | (s["low"] > s[["open", "close"]].min(axis=1) + 1e-9)
    add("high/low not covering open/close", bad.sum(), _ex(s.loc[bad, "datetime"].dt.strftime("%Y-%m-%d %H:%M")), "kept (tiny rounding)")
    k = "stock" if kind in ("cash", "sfut") else ("fut" if kind == "fut" else "index")
    rng = (s["high"] / s["low"] - 1) * 100
    big = s[rng > OUTLIER_1M[k]]
    add(f"outlier candles (1-min range > {OUTLIER_1M[k]}%)", len(big), _ex(f"{t:%Y-%m-%d %H:%M} {r:.1f}%" for t, r in zip(big["datetime"], rng[rng > OUTLIER_1M[k]])),
        "kept (real fast moves: news, budget, Mar 2020); reviewed")
    ch = (s["close"] / s["close"].shift() - 1).abs() * 100
    same = day.eq(day.shift())
    jmp = s[(ch > JUMP_1M[k]) & same]
    add(f"1-min jumps > {JUMP_1M[k]}% inside a day", len(jmp), _ex(jmp["datetime"].dt.strftime("%Y-%m-%d %H:%M")), "kept; reviewed")
    return rows


def check_daily(name: str, kind: str) -> list[dict]:
    rows = []
    df = _raw(name, "1D")
    add = lambda chk, n, ex, how: rows.append({"dataset": f"{name}_1D", "check": chk, "count": int(n), "examples": ex, "handling": how})
    d = df["datetime"].dt.normalize()
    wk = d.dt.weekday >= 5
    add("weekend days (Muhurat / special Saturday)", wk.sum(), _ex(d[wk].dt.strftime("%Y-%m-%d")), "Muhurat / short sessions dropped; full Budget Saturdays kept")
    p = df[data.PRICE].to_numpy()
    add("zero / negative prices", (p <= 0).any(axis=1).sum(), "", "none" if not (p <= 0).any() else "rows dropped")
    if kind == "cash":
        idx = _raw("NIFTY50", "1D")
        idx = idx.set_index(idx["datetime"].dt.normalize())["close"]
        x = df.set_index(d)
        gap = (x["open"] / x["close"].shift() - 1) * 100
        im = (idx / idx.shift() - 1).reindex(x.index).abs() * 100
        j = gap[(gap.abs() > GAP_DAILY) & (im < 3)]
        fixed = CONFIG["data"]["corp_fixes"].get(name)
        add(f"overnight jumps > {GAP_DAILY}% with the index calm (unadjusted split / bonus / demerger?)", len(j),
            _ex(f"{t:%Y-%m-%d} {v:+.1f}%" for t, v in j.items()),
            (f"{fixed}: fixed (older prices x open/prev close)" if fixed else "none in the 2018+ test window") +
            ("; all before 2018 = only the long daily check" if len(j) and (j.index < pd.Timestamp("2018-01-01")).all() else ""))
    return rows


def check_futures(root: str) -> list[dict]:
    rows = []
    add = lambda chk, n, ex, how: rows.append({"dataset": f"{root}_F1/F2", "check": chk, "count": int(n), "examples": ex, "handling": how})
    ex = data.expiries(root)
    src = ex["source"].str.split(" \\(").str[0].value_counts().to_dict()
    add("monthly expiries (NSE bhavcopy from Oct 2021, NSE rule before)", len(ex), ", ".join(f"{k}: {v}" for k, v in src.items()),
        "used to find the roll days")
    early = ex[ex["regime"] != "switch after expiry"]
    add("months Fyers switched contract on the expiry day itself (or no candle that day)", len(early), _ex(early["nse_expiry"].dt.strftime("%Y-%m")),
        "roll counted from the last day Fyers still had the old contract")
    held = ex[ex["regime"] == "switch after expiry"]
    add("near futures vs spot at the expiry close (|F1/S - 1|)", 0, f"median {held['conv_pct'].median():.3f}%, max {held['conv_pct'].max():.3f}%",
        "near 0 = the old contract really expired that day")
    rc = data.roll_check(root)
    p95 = rc.attrs["normal_abs_p95"]
    big = rc[rc["diff_pct"].abs() > max(0.5, 2 * p95)]
    add("continuity at roll / switch days (futures move - spot move)", len(big),
        f"median {rc['diff_pct'].abs().median():.2f}%, p95 {rc['diff_pct'].abs().quantile(.95):.2f}% (normal days p95 {p95:.2f}%); "
        + _ex(f"{d:%Y-%m-%d} {v:+.2f}%" for d, v in zip(big["date"], big["diff_pct"])),
        "no contract mix-up: boundary days look like normal days (outliers = COVID days, dividend ex-dates)")
    spot = SPOT_OF.get(root, root)
    fac = data.split_factors(f"{root}_F1", root)
    if len(fac):
        add("stock futures NOT adjusted for a split / bonus (futures = k x cash)", len(fac),
            f"{fac.index.min():%Y-%m-%d} .. {fac.index.max():%Y-%m-%d}, k = {', '.join(f'{x:g}' for x in sorted(set(fac)))}",
            "fixed: those futures prices divided by k (F1 and F2)")
    f1 = data.apply_split_factors(data.daily_clean(data.read(f"{root}_F1", "1D")), f"{root}_F1", root).set_index("datetime")["close"]
    s = data.daily_clean(data.read(spot, "1D")).set_index("datetime")["close"]
    b = (f1 / s - 1).dropna() * 100
    far = b[b.abs() > 3]
    add("futures basis to spot beyond +-3% after the split fix", len(far), _ex(f"{t:%Y-%m-%d} {v:+.1f}%" for t, v in far.items()),
        "none" if not len(far) else "single days (data glitch / ex-dividend); kept")
    r = data.rolls(root)
    add("roll days (next contract from 3 trading days before expiry)", len(r), _ex(r["roll_day"].dt.strftime("%Y-%m-%d")),
        "positions over a roll pay exit + entry; gap removed by back-adjustment")
    return rows


def report(save: bool = True) -> pd.DataFrame:
    rows = []
    for n in ("NIFTY50", "BANKNIFTY"):
        rows += check_1m(n, "index") + check_daily(n, "index")
    for r in INDEX_FUT:
        for k in (1, 2):
            rows += check_1m(f"{r}_F{k}", "fut")
        rows += check_futures(r)
    for st in STOCKS:
        rows += check_1m(st, "cash") + check_daily(st, "cash")
        if (data.BARS / f"{st}_F1_1D.parquet").exists():
            rows += check_futures(st)
    q = pd.DataFrame(rows)
    if save:
        q.to_csv(data.BARS / "quality_report.csv", index=False)
    return q


def summary(q: pd.DataFrame) -> pd.DataFrame:
    """Checks summed over datasets (for the report's data card)."""
    g = q.groupby("check", sort=False).agg(datasets=("dataset", "nunique"), count=("count", "sum"), handling=("handling", "first")).reset_index()
    return g
