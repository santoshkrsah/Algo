"""Daily data for the Gap + RSI + 21-EMA study: daily OHLCV built from the 15-minute equity candles, adjusted for splits and
bonuses (NSE corporate actions), plus the indicators (EMA 21, RSI 10 smoothed, monthly EMA, previous-day levels).

    daily = build_daily()                         # reads every equity day file once (parallel), ~5 min
    adj, log = adjust(daily)                      # split / bonus adjusted
    ind = indicators(adj)                         # one row per stock-day with every column the strategy needs
"""
import re
from concurrent.futures import ProcessPoolExecutor

import numpy as np
import pandas as pd

from backtest.config import DATA, EQUITY, FNO, UNIVERSE_CSV
from backtest.data import COLUMNS, trading_days

CORP_CSV = DATA / "reference" / "corporate_actions.csv"
WORKERS = 6


def _one_day(day: str) -> pd.DataFrame:
    d = pd.read_csv(EQUITY / f"{day}.csv", usecols=COLUMNS)
    d = d[d.timeframe == "15m"].sort_values(["symbol", "datetime"])
    g = d.groupby("symbol")
    out = pd.DataFrame({"open": g.open.first(), "high": g.high.max(), "low": g.low.min(), "close": g.close.last(), "volume": g.volume.sum()})
    out["date"] = day
    return out.reset_index()


def build_daily() -> pd.DataFrame:
    days = trading_days()
    with ProcessPoolExecutor(WORKERS) as ex:
        parts = list(ex.map(_one_day, days, chunksize=8))
    return pd.concat(parts, ignore_index=True).sort_values(["symbol", "date"]).reset_index(drop=True)


def nifty_daily() -> pd.DataFrame:
    n = pd.read_csv(DATA / "index" / "NIFTY50_1m.csv", usecols=["datetime", "close"])
    n["date"] = n.datetime.str[:10]
    c = n.groupby("date").close.last().rename("nifty").to_frame()
    c["nifty_sma200"] = c.nifty.rolling(200).mean()
    c["nifty_bull"] = c.nifty > c.nifty_sma200
    return c


def fno_symbols() -> list[str]:
    """Stocks that have a futures contract in the NSE F&O bhavcopy (2024-11 .. 2026-10): the 'liquid F&O' universe."""
    syms = set()
    for p in sorted((FNO / "bhavcopy").glob("*.csv"))[::40]:
        syms |= set(pd.read_csv(p, usecols=["TckrSymb", "FinInstrmTp"]).query("FinInstrmTp == 'STF'").TckrSymb)
    return sorted(syms)


def split_factors() -> pd.DataFrame:
    """(symbol, ex_date, factor, kind): factor > 1 means old prices must be divided by it (split 10 -> 2 = 5, bonus 1:1 = 2)."""
    c = pd.read_csv(CORP_CSV)
    c = c[c.series.isin(["EQ", "BE", "BZ", "SM", "ST"]) & c.ex_date.notna()]
    rows = []
    for r in c.itertuples():
        p = str(r.purpose).lower()
        if "split" in p or "sub-division" in p or "sub division" in p:
            m = re.search(r"from\s+(?:rs\.?|re\.?|inr)?\s*([\d.]+)\s*/?-?.*?to\s+(?:rs\.?|re\.?|inr)?\s*([\d.]+)", p)
            if m and float(m[2]) > 0 and float(m[1]) > float(m[2]):
                rows.append((r.symbol, r.ex_date, float(m[1]) / float(m[2]), "split"))
        elif "bonus" in p:
            m = re.search(r"bonus\s+(\d+)\s*:\s*(\d+)", p)
            if m and int(m[2]) > 0:
                rows.append((r.symbol, r.ex_date, (int(m[1]) + int(m[2])) / int(m[2]), "bonus"))
    return pd.DataFrame(rows, columns=["symbol", "ex_date", "factor", "kind"]).drop_duplicates(["symbol", "ex_date"])


def drop_spikes(daily: pd.DataFrame, jump: float = 0.30, back: float = 0.15) -> tuple[pd.DataFrame, pd.DataFrame]:
    """One-day bad prints: the close moves by more than `jump` and the next close is back within `back` of the old level. Row dropped."""
    d = daily.sort_values(["symbol", "date"]).reset_index(drop=True)
    g = d.groupby("symbol").close
    prev, nxt = g.shift(1), g.shift(-1)
    bad = ((d.close / prev - 1).abs() > jump) & ((nxt / prev - 1).abs() < back)
    return d[~bad].reset_index(drop=True), d.loc[bad, ["symbol", "date", "close"]].assign(prev_close=prev[bad])


def adjust(daily: pd.DataFrame) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Back-adjust prices (and volume) before every split / bonus ex-date. Returns (adjusted daily, applied-factor log)."""
    daily = daily.astype({"volume": "float64"})
    sf = split_factors()
    sf = sf[sf.symbol.isin(daily.symbol.unique())]
    out, log = [], []
    by = {s: g for s, g in sf.groupby("symbol")}
    for sym, g in daily.groupby("symbol", sort=False):
        g = g.copy()
        for r in by.get(sym, pd.DataFrame(columns=["ex_date", "factor", "kind"])).itertuples():
            before = g.date < r.ex_date
            if before.any() and (~before).any():
                last_before, first_after = g.loc[before, "close"].iloc[-1], g.loc[~before, "open"].iloc[0]
                implied = last_before / first_after
                # only adjust when the data really jumped by about the announced factor (unadjusted source)
                if 0.9 * r.factor <= implied <= 1.1 * r.factor and abs(implied - 1) >= 0.5 * (r.factor - 1):
                    g.loc[before, ["open", "high", "low", "close"]] /= r.factor
                    g.loc[before, "volume"] *= r.factor
                    log.append((sym, r.ex_date, r.kind, r.factor, round(implied, 3)))
        out.append(g)
    return pd.concat(out, ignore_index=True), pd.DataFrame(log, columns=["symbol", "ex_date", "kind", "factor", "implied_jump"])


def _rsi(close: pd.Series, n: int) -> pd.Series:
    d = close.diff()
    up, dn = d.clip(lower=0), -d.clip(upper=0)
    ru, rd = up.ewm(alpha=1 / n, adjust=False, min_periods=n).mean(), dn.ewm(alpha=1 / n, adjust=False, min_periods=n).mean()
    return 100 - 100 / (1 + ru / rd.replace(0, np.nan))


def indicators(adj: pd.DataFrame, ema_n: int = 21, rsi_n: int = 10, rsi_smooth: int = 3, month_ema: tuple = (10, 20), resist_n: int = 20) -> pd.DataFrame:
    parts = []
    for sym, g in adj.groupby("symbol", sort=False):
        g = g.sort_values("date").reset_index(drop=True)
        if len(g) < 60:
            continue
        c = g.close
        g["ema"] = c.ewm(span=ema_n, adjust=False, min_periods=ema_n).mean()
        g["rsi"] = _rsi(c, rsi_n)
        g["rsi_s"] = g.rsi.rolling(rsi_smooth).mean()
        g["p_close"], g["p_high"], g["p_low"] = c.shift(1), g.high.shift(1), g.low.shift(1)
        g["resist"] = g.high.shift(1).rolling(resist_n).max()          # highest high of the previous resist_n days
        g["p_rsi_s"] = g.rsi_s.shift(1)
        g["gap_pct"] = (g.open / g.p_close - 1) * 100
        g["turn20"] = (c * g.volume).rolling(20).mean().shift(1)
        mon = g.assign(m=g.date.str[:7]).groupby("m").close.last()           # month-end close
        for n in month_ema:
            me = mon.ewm(span=n, adjust=False, min_periods=n).mean()
            prev = me.shift(1)                                               # value known at the start of each month
            g[f"mema{n}"] = g.date.str[:7].map(prev)
        g["month"] = g.date.str[:7]
        idx = np.arange(len(g))
        last = pd.Series(np.where(c.pct_change().abs() > 0.35, idx, np.nan)).ffill()     # last day with an unexplained >35% jump (demerger etc.)
        g["since_jump"] = idx - last.to_numpy()
        parts.append(g)
    return pd.concat(parts, ignore_index=True)


def universe_table() -> pd.DataFrame:
    """equity_universe.csv (AMFI list of 30 Jun 2026): sym, mcap_cr, category (Large = top 100, Mid = next 150, Small = the rest)."""
    u = pd.read_csv(UNIVERSE_CSV)
    u["sym"] = u.symbol.str.replace(r"^NSE:|-(EQ|BE|BZ|SM|ST)$", "", regex=True)
    return u[["sym", "mcap_cr", "category"]]


def add_mcap(ind: pd.DataFrame, anchor: tuple = ("2026-01-01", "2026-06-30")) -> pd.DataFrame:
    """Market cap on each day = AMFI market cap (average of Jan-Jun 2026) x close / average close of Jan-Jun 2026.
    Prices are split / bonus adjusted, so the share count behind the ratio stays constant (new issues / buybacks are not seen).
    rank_pit = rank of that market cap among all stocks of the data on that day (1 = biggest); AMFI small cap = rank 251 and below."""
    u = universe_table()
    ind = ind.copy()
    ind["mcap_now"] = ind.symbol.map(dict(zip(u.sym, u.mcap_cr)))
    ind["cat_now"] = ind.symbol.map(dict(zip(u.sym, u.category)))
    win = ind[(ind.date >= anchor[0]) & (ind.date <= anchor[1])].groupby("symbol").close.mean()
    ind["anchor"] = ind.symbol.map(win).fillna(ind.symbol.map(ind.groupby("symbol").close.mean()))
    ind["mcap_pit"] = ind.mcap_now * ind.close / ind.anchor
    ind["rank_pit"] = ind.groupby("date").mcap_pit.rank(ascending=False, method="first")
    return ind
