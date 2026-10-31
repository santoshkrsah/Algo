"""Calendar spread gap study: near month FUT buy, next month FUT sell. How often were both at the same price,
and how often was the gap 0.50 or more?

daily  : exact contracts from the NSE F&O bhavcopy (expired ones too), one close per day.
minute : 1m candles. Near leg = Fyers continuous series (it is the near contract up to its expiry day),
         next leg = one live contract from fno/1m/contracts/. Only minutes in which BOTH legs traded.
Gap = next - near. same = gap 0.00, up = gap >= +0.50 (next dearer), down = gap <= -0.50 (near dearer).
"""
import numpy as np
import pandas as pd

from backtest.config import FNO

STEP = 0.50
KINDS = ["same", "up", "down"]
BHAV_COLS = {"TradDt": "date", "TckrSymb": "symbol", "FinInstrmNm": "contract", "XpryDt": "expiry", "HghPric": "high",
             "LwPric": "low", "ClsPric": "close", "LastPric": "ltp", "TtlTradgVol": "lots", "TtlNbOfTxsExctd": "trades",
             "NewBrdLotQty": "lot"}


def classify(gap: pd.Series) -> pd.Series:
    g = gap.round(2)
    return pd.Series(np.select([g == 0, g >= STEP, g <= -STEP], KINDS, "small"), index=gap.index)


def load_bhav(symbols: list[str], start: str, end: str) -> pd.DataFrame:
    files = [p for p in sorted((FNO / "bhavcopy").glob("*.csv")) if start <= p.stem <= end]
    if not files:
        raise FileNotFoundError(f"no bhavcopy files in {FNO / 'bhavcopy'} for {start}..{end}")
    b = pd.concat([pd.read_csv(p) for p in files], ignore_index=True)
    b = b[b.TckrSymb.isin(symbols) & (b.FinInstrmTp == "STF")]
    return b.rename(columns=BHAV_COLS)[list(BHAV_COLS.values())]


def daily(b: pd.DataFrame, near: str, far: str, start: str, end: str) -> pd.DataFrame:
    """near/far = contract name endings, e.g. '26SEPFUT' / '26OCTFUT'. One row per stock-day."""
    b = b[(b.date >= start) & (b.date <= end)]
    n = b[b.contract.str.endswith(near)].set_index(["date", "symbol"])[["close", "ltp", "trades", "lot"]]
    f = b[b.contract.str.endswith(far)].set_index(["date", "symbol"])[["close", "ltp", "trades"]]
    d = n.add_suffix("_near").join(f.add_suffix("_far"), how="inner").reset_index()
    d["gap"] = (d.close_far - d.close_near).round(2)
    d["gap_ltp"] = (d.ltp_far - d.ltp_near).round(2)
    d["kind"], d["kind_ltp"] = classify(d.gap), classify(d.gap_ltp)
    return d.sort_values(["symbol", "date"]).reset_index(drop=True)


def load_1m(path, start: str, end: str) -> pd.DataFrame:
    """Candles with volume > 0 only: Fyers fills empty minutes with the previous price and volume 0."""
    m = pd.read_csv(path, parse_dates=["datetime"])
    m = m[(m.datetime >= start) & (m.datetime < pd.Timestamp(end) + pd.Timedelta(days=1)) & (m.volume > 0)]
    return m.set_index("datetime")


def minute(symbol: str, near_file: str, far_file: str, start: str, end: str) -> pd.DataFrame:
    n = load_1m(FNO / "1m" / "futures" / near_file, start, end)[["close", "volume"]]
    f = load_1m(FNO / "1m" / "contracts" / far_file, start, end)[["close", "volume"]]
    m = n.add_suffix("_near").join(f.add_suffix("_far"), how="inner").reset_index()
    m["symbol"] = symbol
    m["date"] = m.datetime.dt.strftime("%Y-%m-%d")
    m["gap"] = (m.close_far - m.close_near).round(2)
    m["kind"] = classify(m.gap)
    return m


def episodes(m: pd.DataFrame) -> pd.DataFrame:
    """Back-to-back matched minutes of the same kind on the same day = one time ("1 baar")."""
    m = m.sort_values(["symbol", "datetime"]).reset_index(drop=True)
    new = (m.kind != m.kind.shift()) | (m.date != m.date.shift()) | (m.symbol != m.symbol.shift())
    m["run"] = new.cumsum()
    m = m[m.kind != "small"]
    g = m.groupby("run")
    best = m.loc[m.gap.abs().groupby(m.run).idxmax(), ["run", "gap"]].set_index("run").gap
    e = g.agg(symbol=("symbol", "first"), date=("date", "first"), start=("datetime", "first"), end=("datetime", "last"),
              minutes=("gap", "size"), kind=("kind", "first"), gap_start=("gap", "first"),
              near_start=("close_near", "first"), far_start=("close_far", "first"))
    e["gap_best"] = best
    e["span"] = ((e.end - e.start).dt.total_seconds() // 60 + 1).astype(int)
    return e.reset_index(drop=True)


def per_stock(rows: pd.DataFrame, eps: pd.DataFrame | None) -> pd.DataFrame:
    """rows: one row per observation (stock-day or matched minute) with symbol, date, gap, kind."""
    out = []
    for s, g in rows.groupby("symbol"):
        r = {"symbol": s, "obs": len(g), "days": g.date.nunique(), "avg": g.gap.mean(), "median": g.gap.median(),
             "min": g.gap.min(), "max": g.gap.max()}
        for k in KINDS:
            hit = g[g.kind == k]
            r[k] = len(hit)
            r[k + "_pct"] = len(hit) / len(g) * 100
            r[k + "_days"] = hit.date.nunique()
            r[k + "_times"] = int((eps.symbol.eq(s) & eps.kind.eq(k)).sum()) if eps is not None else len(hit)
        out.append(r)
    return pd.DataFrame(out)


def per_day(m: pd.DataFrame, eps: pd.DataFrame) -> pd.DataFrame:
    """Per stock-day: matched minutes, minutes of each kind (<kind>) and how many separate times (<kind>_times)."""
    g = m.groupby(["date", "symbol"])
    d = g.gap.agg(obs="size", min="min", max="max", avg="mean")
    for k in KINDS:
        d[k] = g.kind.apply(lambda x, k=k: int((x == k).sum()))
        d[k + "_times"] = eps[eps.kind == k].groupby(["date", "symbol"]).size().reindex(d.index, fill_value=0)
    return d.reset_index()


def data_check(symbol: str, path, b: pd.DataFrame, start: str, end: str, contract: str | None) -> dict:
    """Fyers 1m rolled up to days vs the NSE bhavcopy. contract=None: compare with the near (earliest expiry) contract."""
    m = load_1m(path, start, end).reset_index()
    m["date"] = m.datetime.dt.strftime("%Y-%m-%d")
    fy = m.groupby("date").agg(high=("high", "max"), low=("low", "min"), last=("close", "last"), vol=("volume", "sum"))
    x = b[(b.symbol == symbol) & (b.date >= start) & (b.date <= end)]
    x = x[x.contract == contract] if contract else x[x.expiry == x.groupby("date").expiry.transform("min")]
    j = fy.join(x.set_index("date")[["high", "low", "ltp", "lots", "lot"]].add_suffix("_nse"), how="inner")
    return {"days": len(j), "high": (j.high == j.high_nse).mean() * 100, "low": (j.low == j.low_nse).mean() * 100,
            "last": (j["last"].round(2) == j.ltp_nse).mean() * 100,
            "vol": (j.vol / (j.lots_nse * j.lot_nse)).median() * 100}
