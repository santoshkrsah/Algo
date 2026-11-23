"""Stock-option version of the layered-straddle engine (backtest.straddle.Sim / Smile / Day are reused unchanged).

Same model as report 10 (NIFTY): every option is priced minute by minute with Black-76 on the real 1-minute underlying (here the stock,
NOT split-adjusted, because the option strikes of that day are real prices), the IV per strike comes from that day's real NSE option
bhavcopy open and close. Stock options only have bhavcopy since 2021 (monthly expiry, some also weekly later) - nearest expiry, strict.

    mk = StockMarket("RELIANCE", "2021-10-25", "2026-11-03")
    sim = S.Sim(params);  for day in mk.days("2021-11-01"): sim.step(mk.day(day))
"""
import numpy as np
import pandas as pd

from backtest import fast
from backtest import straddle as S
from backtest.config import FNO

CACHE = FNO / "_stock_opt_cache"          # parquet per symbol (built from bhavcopy_hist + bhavcopy_opt); rebuildable
COLS = {"TradDt": "date", "XpryDt": "expiry", "StrkPric": "strike", "OptnTp": "type", "OpnPric": "open", "ClsPric": "close",
        "TtlNbOfTxsExctd": "trades", "NewBrdLotQty": "lot", "TckrSymb": "symbol", "TtlTradgVol": "vol", "TtlTrfVal": "turnover"}


def build_cache(symbols: list[str], start: str, end: str) -> None:
    """One pass over all daily option files (old NSE format 2021 - Oct 2024, UDiFF after), keep the given symbols."""
    CACHE.mkdir(exist_ok=True)
    parts = {s: [] for s in symbols}
    for folder in ("bhavcopy_hist", "bhavcopy_opt"):
        for p in sorted((FNO / folder).glob("2*.csv")):
            if not start <= p.stem <= end:
                continue
            head = pd.read_csv(p, nrows=0).columns
            use = [c for c in COLS if c in head] + (["FinInstrmTp"] if "FinInstrmTp" in head else [])
            c = pd.read_csv(p, usecols=use)
            c = c[c.TckrSymb.isin(symbols) & c.OptnTp.notna()]
            if "FinInstrmTp" in c:
                c = c[c.FinInstrmTp == "STO"].drop(columns="FinInstrmTp")
            for s, g in c.groupby("TckrSymb"):
                parts[s].append(g.rename(columns=COLS))
    for s, ps in parts.items():
        df = pd.concat(ps, ignore_index=True)
        for k in ("trades", "lot", "turnover"):
            if k not in df:
                df[k] = np.nan
        df.drop(columns="symbol").to_parquet(CACHE / f"{s}.parquet")


def load_chain(sym: str, start: str, end: str) -> pd.DataFrame:
    c = pd.read_parquet(CACHE / f"{sym}.parquet")
    return c[(c.date >= start) & (c.date <= end)]


def minutes(sym: str, start: str, end: str) -> dict:
    """{date: array (375, 4) o/h/l/c, 9:15 ... 15:29} of the NOT adjusted 1-minute stock price; days with < 300 bars are left out."""
    m = fast.symbol_1m(sym, start, end, adjust=False)
    m = m.assign(k=m.minute.astype(int) - 555)
    m = m[(m.k >= 0) & (m.k < 375)]
    out = {}
    for d, g in m.groupby("date"):
        if len(g) < 300:
            continue
        a = np.full((375, 4), np.nan)
        a[g.k.values] = g[["open", "high", "low", "close"]].values
        out[d] = pd.DataFrame(a).ffill().bfill().values
    return out


CLEAN = np.array([1, 1.5, 2, 2.5, 3, 4, 5, 10, 20])


def actual_factor(chain: pd.DataFrame, adj_close: float) -> float:
    """Fyers history is back-adjusted for splits / bonus, the option strikes of that day are real prices. Real price of the day from put-call parity
    (strike where CE ~ PE, liquid, nearest expiry) / Fyers close = factor; snapped to a clean ratio (2 for a 1:1 bonus, 10 for a 1:10 split ...) when within 3%."""
    g = chain[chain.expiry == chain.expiry.min()]
    liq = "trades" if g.trades.notna().any() else "vol"
    p = g.pivot_table(index="strike", columns="type", values=["close", liq], aggfunc="first")
    try:
        ok = (p[("close", "CE")] > 0) & (p[("close", "PE")] > 0) & (p[(liq, "CE")].fillna(0) >= 20) & (p[(liq, "PE")].fillna(0) >= 20)
    except KeyError:
        return 1.0
    p = p[ok]
    if not len(p):
        return float("nan")
    diff = (p[("close", "CE")] - p[("close", "PE")]).abs()
    k = diff.nsmallest(3).index
    est = float(np.median(k.values + (p.loc[k, ("close", "CE")] - p.loc[k, ("close", "PE")]).values))
    r = est / adj_close
    c = CLEAN[np.argmin(np.abs(CLEAN / r - 1))]
    return float(c) if abs(c / r - 1) < 0.03 else float(r)


def strike_step(chain: pd.DataFrame, spot: float) -> float:
    """Strike gap near the money on this day (most common gap of the listed strikes within +-8% of the price)."""
    ks = np.sort(chain.strike.unique())
    ks = ks[np.abs(ks / spot - 1) <= 0.08]
    if len(ks) < 3:
        return float(round(spot * 0.01, 1)) or 1.0
    d = np.round(np.diff(ks), 4)
    d = d[d > 0]
    vals, cnt = np.unique(d, return_counts=True)
    return float(vals[np.argmax(cnt)])


class StockMarket(S.Market):
    def __init__(self, sym: str, start: str, end: str):
        self.index = sym
        S.STEP.setdefault(sym, 1)
        S.STRICT_NEAREST.add(sym)                        # nearest expiry unusable that day -> no trade (never fall to the next month)
        self.bars = minutes(sym, start, end)
        self.vix = {}
        self.chain = {d: g for d, g in load_chain(sym, start, end).groupby("date")}
        self.factor = {}
        for d in list(self.bars):
            if d not in self.chain:
                continue
            f = actual_factor(self.chain[d], float(self.bars[d][345:375, 3].mean()))
            if f == f:
                self.factor[d] = f
                self.bars[d] = self.bars[d] * f
        self.all_days = sorted(set(self.bars) & set(self.chain))
        self.macd = {}
        self._last_lot = None

    def day(self, date: str) -> S.Day:
        ch = self.chain[date]
        S.STEP[self.index] = strike_step(ch[ch.expiry == ch.expiry.min()], float(self.bars[date][0, 0]))
        d = S.Market.day(self, date)
        return d
