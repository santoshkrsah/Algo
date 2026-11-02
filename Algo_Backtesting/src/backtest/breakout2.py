"""Volume breakout v2 - the full rulebook spec (Hinglish rules in tools/run_breakout2.py): top-50 liquid F&O stocks,
point-in-time from the NSE F&O bhavcopy (Oct 2021 on), traded two ways on the same daily signals:
(A) stock long (cash, no leverage; futures-lot variant) and (B) put SELL on NSE stock options incl. expired contracts,
plus a post-sell-off recovery signal for B. Daily data only.

    D = load()                              # adjusted daily panel, actual price factor, top-50 universe, Nifty regime, put chains
    sig = signals(D, p)                     # breakouts (monthly cap)    | selloffs(D, p) for the B variant
    a = book_a(D, path_a(D, sig, p), p)     # type A trades in Rs
    b = book_b(D, path_b(D, sig, p), p)     # type B trades in Rs
    stats(a, p.capital, months)

Prices: the store is split / bonus adjusted over the whole history (Fyers), option strikes are the prices of the day.
Support / spot go to the option scale with factor = actual price (bhavcopy underlying price, or near-month futures close
in the old format) / adjusted close, snapped to a clean ratio (1, 2, 10, 20 ...).
[A] = our assumption (the spec gave several values or none); see the runner for the Hinglish list.
"""
from bisect import bisect_right
from dataclasses import dataclass, replace

import numpy as np
import pandas as pd
from pandas.api.types import union_categoricals

from backtest import fast
from backtest.config import DATA, FNO, ROOT
from backtest.straddle import R as RATE, b76, implied_vol

START, END = "2021-10-01", "2026-10-30"
NEW_FROM = "2024-11-01"                     # fno/bhavcopy + bhavcopy_opt start here; fno/bhavcopy_hist has the days before
HIST, FUT_NEW, OPT_NEW = FNO / "bhavcopy_hist", FNO / "bhavcopy", FNO / "bhavcopy_opt"
SYMCHG = DATA / "reference" / "symbol_changes.csv"
NIFTY_1M, NIFTY_PRE = DATA / "index" / "NIFTY50_1m.csv", DATA / "index" / "NIFTY50_daily.csv"
CACHE = ROOT / ".cache" / "breakout2"
RATIOS = np.array(sorted({a / b for a in range(1, 61) for b in (1, 2, 3, 4, 5, 10)}))
DELIVERY = {4: 0.10, 3: 0.25, 2: 0.45, 1: 0.70}   # E-n: share of the stock's margin blocked on ITM short puts (physical delivery)
FUT_COLS = {"TradDt": "date", "TckrSymb": "symbol", "XpryDt": "expiry", "OpnPric": "open", "HghPric": "high", "LwPric": "low",
            "ClsPric": "close", "TtlTradgVol": "contracts", "TtlTrfVal": "turnover", "NewBrdLotQty": "lot", "UndrlygPric": "und"}
PUT_COLS = {"TradDt": "date", "TckrSymb": "symbol", "XpryDt": "expiry", "StrkPric": "strike", "OpnPric": "open", "HghPric": "high",
            "LwPric": "low", "ClsPric": "close", "TtlTradgVol": "vol", "TtlTrfVal": "turnover", "NewBrdLotQty": "lot", "UndrlygPric": "und"}


@dataclass(frozen=True)
class Params:
    capital: float = 1_000_000.0
    risk: float = 0.01                     # [V] 0.5 / 0.75 / 1% of capital per trade
    top_n: int = 50                        # [V] top ~50 F&O stocks, re-ranked every month on the previous 60 days' futures turnover
    turn_n: int = 60
    # setup (same meaning as breakout.Params)
    base_n: int = 20                       # [A] base 20 / 40 / 60 days (40 and 60 give far fewer than the stated 3-4 trades a month)
    max_height: float = 0.12               # [A] (base high - base low) / base high: 8 / 12 / 15 %
    max_body: float | None = 0.015         # [A] "mostly small candles": average |close - open| / close of the base
    vol_mult: float = 2.0                  # [A] breakout volume / previous 20-day average: 1.5 / 2 / 3
    vol_n: int = 20
    accum: bool = True                     # [A] volume rising in the base: average of its second half > first half
    close_top: float | None = 0.75         # [A] "closing near the day's high": close in the top 25% of the day's range
    red_mult: float | None = 2.0           # [A] "no large bearish candle the day before": its red body < 2x the base's average body
    body_mult: float | None = None         # breakout body >= x times the base's average body (not in the spec; report 18 used 1.5)
    morning_star: bool = False             # [V] optional filter: Morning Star near the base low in the base's last 10 days
    per_month: int | None = 4              # [V] at most 3-4 signals a month, strongest volume first on a day
    # type A
    entry: str = "next_open"               # [V] "next_open" / "close" (breakout day's close)
    stop: str = "res"                      # [V] "res" = below the base top, "low" = below the breakout candle's low
    stop_buf: float = 0.005                # [A] 0.5% below that level
    exit: str = "rr"                       # [V] "rr" (target rr x risk) / "pct" (target tgt_pct) / "trail5" / "trail10" (n-day low)
    rr: float = 2.0
    tgt_pct: float = 0.10
    max_days: int = 10                     # [V] time stop 5 / 10 / 20 trading days
    addon: bool = False                    # [V] add 50% at the next open after a close above every high since the signal, stop to entry
    max_val: float = 0.5                   # [A] one position at most 50% of capital (cash, no leverage)
    slip: float = 0.0005                   # [A] stock / futures slippage per fill
    a_inst: str = "stock"                  # "stock" (cash) / "fut" (whole lots, margin; prices = stock prices)
    # type B
    signal: str = "breakout"               # "breakout" / "selloff" (post sell-off recovery, support = sell-off low)
    so_n: int = 20                         # [A] sell-off: low of the last 20 days ...
    so_drop: float = 0.10                  # [A] ... at least 10% below the highest high of the 20 days before it ...
    so_hold: int = 5                       # [A] ... no lower low for 5 days ...
    so_bounce: float = 0.03                # [A] ... and the close at least 3% above the low
    dte: int = 7                           # [V] sell when the nearest expiry is 2 .. dte trading days away: 5 / 7 / 10
    wait: int = 15                         # [A] trading days a signal waits for that window (stock closing above support every day)
    strike: str = "support"                # [V] "support" = highest strike <= support, "below" = one strike lower, "otm5" = <= 95% of spot
    b_stop: str = "support"                # [V] "support" = stock closes below support, "prem2" = premium 2x (= loss 1x premium), "loss15"
    take: float | None = None              # [V] buy back once this share of the premium is earned (0.5 / 0.7); None = hold to expiry
    settle: str = "close"                  # [V] "close" = ITM put bought back on the day before expiry, "physical" = held, delivery taken
    min_prem: float = 1.0                  # [A] Rs per share; smaller premiums are skipped
    opt_slip: float = 0.02                 # [A] option slippage per fill: max(2% of the price, Rs 0.10)
    opt_slip_min: float = 0.10
    stop_gap: float = 0.01                 # [A] support stop: risk per lot = put value with the stock 1% below support (Black-76, entry IV)
    span_k: float = 10.0                   # [A] futures margin ~ 10 x daily sd (100 days), 15% .. 50%; ELM 3.5% of it
    elm: float = 0.035
    margin_mult: float = 1.0               # stress: margin x this
    phys_fee: float = 0.0025               # [A] broker fee on physically settled value (0.25%)


def with_params(p: Params, **kw) -> Params:
    return replace(p, **kw)


# ---------------------------------------------------------------- charges
def charges(kind: str, buy: float, sell: float, day: str, orders: int) -> float:
    """Rs charges of the fills of one trade. kind 'stock' (delivery), 'fut' or 'opt' (buy / sell = Rs value of those fills,
    premium for options). Rates change on 1 Oct 2024 (STT on F&O up, exchange charges down) and 1 Apr 2026 (project assumption:
    Budget 2026 STT options 0.15%, futures 0.05% on the sell side)."""
    old, new = day < "2024-10-01", day >= "2026-04-01"
    sebi = 1e-6 * (buy + sell)
    if kind == "stock":
        exch = (0.0000322 if old else 0.0000297) * (buy + sell)
        dp = 15.34 * 1.18 if sell else 0.0
        return 0.001 * (buy + sell) + 0.00015 * buy + exch + sebi + 0.18 * (exch + sebi) + dp
    brk = 20.0 * orders
    if kind == "fut":
        exch = (0.000019 if old else 0.0000173) * (buy + sell)
        stt = (0.000125 if old else 0.0005 if new else 0.0002) * sell
        return brk + exch + sebi + stt + 0.00002 * buy + 0.18 * (brk + exch + sebi)
    exch = (0.0005 if old else 0.0003503) * (buy + sell)
    stt = (0.000625 if old else 0.0015 if new else 0.001) * sell
    return brk + exch + sebi + stt + 0.00003 * buy + 0.18 * (brk + exch + sebi)


# ---------------------------------------------------------------- data
def _rename_map(day: str, ch: pd.DataFrame) -> dict:
    """{old ticker: today's ticker} for rows dated `day` (only changes that happen after it; chains ZOMATO -> ETERNAL, LTI -> LTIM -> LTM)."""
    later = ch[ch.date > day]
    m = {}
    for o, n in zip(later.old, later.new):
        for k, v in m.items():
            if v == o:
                m[k] = n
        m.setdefault(o, n)
    return m


def _cat(parts: list[pd.DataFrame], cols) -> pd.DataFrame:
    """Concatenate frames whose `cols` are categoricals with different categories (no object copy of millions of strings)."""
    out = pd.concat([x.drop(columns=list(cols)) for x in parts], ignore_index=True)
    for c in cols:
        out[c] = union_categoricals([x[c] for x in parts], ignore_order=True)
    return out


def _compact(x: pd.DataFrame, cols: dict, mp: dict) -> pd.DataFrame:
    x = x.reindex(columns=list(cols)).rename(columns=cols)
    x["symbol"] = x.symbol.replace(mp) if mp else x.symbol
    for c in ("date", "symbol", "expiry"):
        x[c] = x[c].astype(str).astype("category")
    for c in ("open", "high", "low", "close", "lot", "und", "contracts", "vol"):
        if c in x:
            x[c] = x[c].astype("float32")
    return x


def fo_data() -> tuple[pd.DataFrame, pd.DataFrame]:
    """Stock futures (every expiry) and stock puts (two nearest traded expiries) from the NSE F&O bhavcopy, Oct 2021 on,
    tickers renamed to today's. Cached in .cache/breakout2/ (rebuilt when the folders change)."""
    old = sorted(f for f in HIST.glob("*.csv") if f.stem < NEW_FROM)
    fut_new, opt_new = sorted(FUT_NEW.glob("*.csv")), sorted(OPT_NEW.glob("*.csv"))
    key = "|".join(f"{len(x)}:{x[0].stem}:{x[-1].stem}" for x in (old, fut_new, opt_new) if x)
    kf = CACHE / "key.txt"
    if kf.exists() and kf.read_text() == key:
        return pd.read_parquet(CACHE / "fut.parquet"), pd.read_parquet(CACHE / "puts.parquet")
    ch = pd.read_csv(SYMCHG).dropna(subset=["date"]).sort_values("date")
    maps: dict[str, dict] = {}

    def mp(day: str) -> dict:
        if day[:7] not in maps:
            maps[day[:7]] = _rename_map(day[:7] + "-01", ch)
        return maps[day[:7]]

    fut, puts = [], []
    for f in old:
        x = pd.read_csv(f)
        m = mp(f.stem)
        fut.append(_compact(x[x.FinInstrmTp == "STF"], FUT_COLS, m))
        puts.append(_compact(x[(x.FinInstrmTp == "STO") & (x.OptnTp == "PE")], PUT_COLS, m))
    for f in fut_new:
        x = pd.read_csv(f, usecols=["FinInstrmTp", *FUT_COLS])
        fut.append(_compact(x[x.FinInstrmTp == "STF"], FUT_COLS, mp(f.stem)))
    for f in opt_new:
        x = pd.read_csv(f, usecols=["OptnTp", *[c for c in PUT_COLS if c != "TtlTrfVal"]])
        puts.append(_compact(x[x.OptnTp == "PE"], PUT_COLS, mp(f.stem)))
    fut, puts = _cat(fut, ("date", "symbol", "expiry")), _cat(puts, ("date", "symbol", "expiry"))
    CACHE.mkdir(parents=True, exist_ok=True)
    fut.to_parquet(CACHE / "fut.parquet")
    puts.to_parquet(CACHE / "puts.parquet")
    kf.write_text(key)
    return fut, puts


def lot_sizes(fut: pd.DataFrame, puts: pd.DataFrame) -> pd.Series:
    """(symbol, expiry) -> lot size. UDiFF rows carry it; for the old format it is the median over the contract's option rows of
    turnover / (contracts x (strike + day's average premium)) (old-format option turnover = (strike + premium) x quantity)."""
    def plain(s: pd.Series) -> pd.Series:
        s.index = pd.MultiIndex.from_arrays([s.index.get_level_values(0).astype(str), s.index.get_level_values(1).astype(str)], names=["symbol", "expiry"])
        return s

    f = fut.dropna(subset=["lot"])
    real = plain(f.lot.groupby([f.symbol, f.expiry], observed=True).median())
    o = puts[puts.lot.isna() & (puts.vol > 0) & puts.turnover.notna()]
    est = o.turnover / (o.vol * (o.strike + (o.high.astype(float) + o.low + o.close) / 3))
    old = plain(est.groupby([o.symbol, o.expiry], observed=True).median())
    step = np.where(old >= 100, 25, np.where(old >= 20, 5, 1))                    # NSE lots are round numbers (1499 -> 1500)
    snapped = np.round(old / step) * step
    old = pd.Series(np.where(np.abs(snapped / old - 1) < 0.01, snapped, np.round(old)), index=old.index)
    return pd.concat([old[~old.index.isin(real.index)], real]).astype(int)


def _snap(r: np.ndarray, tol: float = 0.015) -> np.ndarray:
    with np.errstate(invalid="ignore"):
        i = np.clip(np.searchsorted(RATIOS, np.nan_to_num(r, nan=1.0)), 1, len(RATIOS) - 1)
        lo, hi = RATIOS[i - 1], RATIOS[i]
        c = np.where(np.abs(r / lo - 1) < np.abs(r / hi - 1), lo, hi)
        return np.where(np.abs(r / c - 1) < tol, c, np.nan)


def price_factor(fut: pd.DataFrame, close: pd.DataFrame) -> pd.DataFrame:
    """date x symbol: actual price of the day / adjusted close. Actual = underlying price (UDiFF) or the nearest futures close
    (old format). Snapped to a clean ratio; days that do not snap (e.g. dividend discount of the futures) take the snapped value
    of the days around them when it is the same before and after, else the raw ratio."""
    f = fut.assign(date=fut.date.astype(str), symbol=fut.symbol.astype(str), expiry=fut.expiry.astype(str))
    f = f[f.expiry >= f.date].sort_values(["date", "symbol", "expiry"])
    near = f.groupby(["date", "symbol"]).first()
    lvl = near.und.where(near.und > 0, near.close).astype(float).unstack().reindex(index=close.index, columns=close.columns)
    raw = lvl / close
    snapped = pd.DataFrame(_snap(raw.to_numpy()), index=raw.index, columns=raw.columns)
    prev, nxt = snapped.ffill(limit=15), snapped.bfill(limit=15)
    out = snapped.where(snapped.notna(), prev.where(prev == nxt, raw))
    return out.ffill().bfill()


def universe(fut: pd.DataFrame, close: pd.DataFrame, top_n: int, n: int = 60) -> tuple[pd.DataFrame, pd.DataFrame]:
    """True where the stock is among the month's top_n F&O stocks by average futures turnover of the previous n days
    (F&O list = stocks with futures on the month's first day). Stocks without stock data (merged / delisted, e.g. HDFC)
    are skipped and the next one moves up; they are listed in the second frame (month, symbol, rank)."""
    f = fut.assign(date=fut.date.astype(str), symbol=fut.symbol.astype(str))
    turn = f.groupby(["date", "symbol"]).turnover.sum().unstack().reindex(close.index)
    avg = turn.rolling(n, min_periods=n * 2 // 3).mean().shift(1)
    members = f.groupby("date").symbol.agg(set)
    month = np.array([d[:7] for d in close.index])
    starts = np.flatnonzero(np.r_[True, month[1:] != month[:-1]])
    have = set(close.columns)
    uni = np.zeros(close.shape, dtype=bool)
    missing = []
    for a, b in zip(starts, np.r_[starts[1:], len(month)]):
        day = close.index[a]
        if day not in members.index:
            continue
        row = avg.iloc[a]
        row = row[row.index.isin(members[day])].dropna().sort_values(ascending=False)
        if row.empty:
            continue
        ok = [s for s in row.index if s in have and close[s].iloc[a] == close[s].iloc[a]]
        cut = row.index.get_loc(ok[top_n - 1]) + 1 if len(ok) >= top_n else len(row)
        missing += [(month[a], s, k + 1) for k, s in enumerate(row.index[:cut]) if s not in ok]
        uni[a:b, close.columns.get_indexer(ok[:top_n])] = True
    return pd.DataFrame(uni, index=close.index, columns=close.columns), pd.DataFrame(missing, columns=["month", "symbol", "rank"])


def nifty() -> pd.DataFrame:
    """Nifty 50 daily close (NSE file before the 1m data starts) with its 200-day average; bull = close above it."""
    m = pd.read_csv(NIFTY_1M, usecols=["datetime", "close"])
    d = m.groupby(m.datetime.str[:10]).close.last()
    pre = pd.read_csv(NIFTY_PRE).set_index("date").close
    s = pd.concat([pre[pre.index < d.index[0]], d]).sort_index()
    sma = s.rolling(200, min_periods=200).mean()
    return pd.DataFrame({"close": s, "sma200": sma, "bull": (s > sma).where(sma.notna())})


class Chains:
    """Put chains (date x strike frames of open / high / low / close / contracts) from the bhavcopy, only traded strikes."""

    def __init__(self, puts: pd.DataFrame, lots: dict, expiries: list[str]):
        x = puts[["symbol", "expiry", "date", "strike", "open", "high", "low", "close", "vol"]]
        self.p = x.set_index(["symbol", "expiry"]).sort_index()
        self.lots = lots
        self.expiries = expiries
        self.cache = {}

    def get(self, sym: str, exp: str):
        k = (sym, exp)
        if k not in self.cache:
            try:
                x = self.p.loc[k]
            except KeyError:
                self.cache[k] = None
                return None
            if x.empty:
                self.cache[k] = None
                return None
            x = x.assign(date=x.date.astype(str))
            self.cache[k] = {c: x.pivot_table(index="date", columns="strike", values=c, aggfunc="last") for c in ("open", "high", "low", "close", "vol")}
        return self.cache[k]

    def next_expiry(self, day: str):
        i = bisect_right(self.expiries, day)
        return self.expiries[i] if i < len(self.expiries) else None


def load(start: str = START, end: str = END, top_n: int = 50, options: bool = True) -> dict:
    fut, puts = fo_data()
    p = fast.panel(start, end, columns=["open", "high", "low", "close", "volume"])
    p = p.assign(date=p.date.astype(str))
    full_close = p.pivot(index="date", columns="symbol", values="close").astype(float)
    uni, missing = universe(fut, full_close, top_n)
    syms = list(full_close.columns[uni.any().to_numpy()])
    p = p[p.symbol.isin(syms)]
    W = {k: p.pivot(index="date", columns="symbol", values=k).astype(float).reindex(columns=syms) for k in ("open", "high", "low", "close", "volume")}
    W["uni"] = uni[syms]
    W["factor"] = price_factor(fut[fut.symbol.isin(syms)], W["close"])
    W["sigma"] = W["close"].pct_change().rolling(100, min_periods=60).std().shift(1)
    cal = list(W["close"].index)
    nf = nifty().reindex(cal)
    mine = puts[puts.symbol.isin(syms)]
    mine = mine.assign(symbol=mine.symbol.cat.remove_unused_categories(), expiry=mine.expiry.cat.remove_unused_categories())
    lots = lot_sizes(fut[fut.symbol.isin(syms)], mine)
    lotmap = {(str(s), str(e)): int(v) for (s, e), v in lots.items()}
    expiries = sorted(set(fut.expiry.astype(str)))
    D = {"W": W, "cal": cal, "pos": {d: i for i, d in enumerate(cal)}, "nifty": nf, "missing": missing, "lots": lotmap, "expiries": expiries,
         "fut": fut[fut.symbol.isin(syms)]}
    D["chains"] = Chains(mine, lotmap, expiries) if options else None
    return D


def lot_on(D: dict, sym: str, day: str) -> int | None:
    """Lot size of the stock's nearest expiry on that day."""
    X = bisect_right(D["expiries"], day)
    for exp in D["expiries"][max(0, X - 1):X + 2]:
        if exp >= day and (sym, exp) in D["lots"]:
            return D["lots"][(sym, exp)]
    return None


# ---------------------------------------------------------------- setup
def morning_star(O, H, L, C, avgb, lo, look: int = 10) -> pd.DataFrame:
    """Morning Star (big red, small body, green closing above the red body's middle) with its low within 3% of the range low,
    ending in the last <look> days before the signal day."""
    b = (C - O) / C
    ms = ((-b.shift(2) >= avgb) & (b.shift(1).abs() <= 0.5 * avgb) & (b > 0) & (C > (O.shift(2) + C.shift(2)) / 2)
          & (pd.concat([L, L.shift(1), L.shift(2)]).groupby(level=0).min().reindex(L.index) <= lo * 1.03))
    return ms.astype(float).rolling(look, min_periods=1).max().shift(1) > 0


def _keep(s: pd.DataFrame, p: Params, cap: bool, gap: int, key: str) -> pd.DataFrame:
    """One signal per stock per `key` value / gap days, then at most p.per_month a month (first come; on one day the bigger
    `rank` first: volume multiple for breakouts, sell-off size for sell-offs)."""
    s = s.sort_values(["i", "rank"], ascending=[True, False]).reset_index(drop=True)
    keep, last, per_m = [], {}, {}
    for r in s.itertuples():
        prev = last.get(r.symbol)
        k = getattr(r, key)
        last[r.symbol] = (r.i, k)
        if prev is not None and (r.i - prev[0] < gap or prev[1] == k):
            continue
        m = r.date[:7]
        if cap and p.per_month is not None and per_m.get(m, 0) >= p.per_month:
            continue
        per_m[m] = per_m.get(m, 0) + 1
        keep.append(r.Index)
    return s.loc[keep].reset_index(drop=True)


def signals(D: dict, p: Params, cap: bool = True) -> pd.DataFrame:
    """Every breakout day that passes the setup filters (only data up to that day's close). A stock's breakout is ignored when
    it had one in the previous base_n days (no new base yet). Support for type B = the base top."""
    W = D["W"]
    O, H, L, C, V = (W[k] for k in ("open", "high", "low", "close", "volume"))
    n = p.base_n
    res = H.rolling(n).max().shift(1)
    lo = L.rolling(n).min().shift(1)
    avgb = ((C - O).abs() / C).rolling(n).mean().shift(1)
    vm = V / V.rolling(p.vol_n).mean().shift(1)
    height = (res - lo) / res
    ok = W["uni"] & (C > res) & (height <= p.max_height) & (vm >= p.vol_mult)
    if p.max_body is not None:
        ok &= avgb <= p.max_body
    if p.accum:
        h = n // 2
        ok &= V.rolling(h).mean().shift(1) > V.rolling(n - h).mean().shift(h + 1)
    if p.close_top is not None:
        ok &= C >= L + p.close_top * (H - L)
    if p.red_mult is not None:
        ok &= (O.shift(1) - C.shift(1)) / C.shift(1) <= p.red_mult * avgb
    if p.body_mult is not None:
        ok &= (C - O) / C >= p.body_mult * avgb
    if p.morning_star:
        ok &= morning_star(O, H, L, C, avgb, lo)
    ii, jj = np.nonzero(ok.to_numpy(dtype=bool))
    dates, syms = np.array(D["cal"]), W["close"].columns.to_numpy()
    bull = D["nifty"].bull.to_numpy()
    s = pd.DataFrame({"i": ii, "j": jj, "date": dates[ii], "symbol": syms[jj], "kind": "breakout", "res": res.to_numpy()[ii, jj],
                      "lo": lo.to_numpy()[ii, jj], "support": res.to_numpy()[ii, jj], "height": height.to_numpy()[ii, jj], "vm": vm.to_numpy()[ii, jj],
                      "close": C.to_numpy()[ii, jj], "low": L.to_numpy()[ii, jj], "high": H.to_numpy()[ii, jj], "base_n": n, "bull": bull[ii]})
    s["grp"], s["rank"] = -1, s.vm
    return _keep(s, p, cap, n, "grp")


def selloffs(D: dict, p: Params, cap: bool = True) -> pd.DataFrame:
    """Post sell-off recovery (B variant): the lowest low of the last so_n days is at least so_drop below the highest high of
    the so_n days before it, is at least so_hold days old (price held above it since) and today's close is at least so_bounce
    above it. One signal per sell-off low. Support = that low."""
    from numpy.lib.stride_tricks import sliding_window_view
    W = D["W"]
    H, L, C, V = (W[k].to_numpy() for k in ("high", "low", "close", "volume"))
    n = p.so_n
    T = C.shape[0]
    win = sliding_window_view(np.where(np.isnan(L), np.inf, L), n, axis=0)        # (T-n+1, S, n), window ends at t = idx + n - 1
    am = win.argmin(axis=2)
    low = np.take_along_axis(win, am[..., None], axis=2)[..., 0]
    t_idx = np.arange(n - 1, T)[:, None]
    low_day = t_idx - (n - 1) + am
    hmax = pd.DataFrame(H).rolling(n, min_periods=n // 2).max().to_numpy()       # max high of the n days ending at each day
    peak = np.take_along_axis(hmax, np.maximum(low_day - 1, 0), axis=0)          # ... ending the day before the low
    drop = 1 - low / peak
    age = t_idx - low_day
    tt = slice(n - 1, T)
    vm = (pd.DataFrame(V) / pd.DataFrame(V).rolling(p.vol_n).mean().shift(1)).to_numpy()
    ok = (np.isfinite(low) & (drop >= p.so_drop) & (age >= p.so_hold) & (C[tt] >= low * (1 + p.so_bounce)) & W["uni"].to_numpy()[tt])
    a, jj = np.nonzero(ok)
    ii = a + n - 1
    dates, syms = np.array(D["cal"]), W["close"].columns.to_numpy()
    s = pd.DataFrame({"i": ii, "j": jj, "date": dates[ii], "symbol": syms[jj], "kind": "selloff", "res": low[a, jj], "lo": low[a, jj],
                      "support": low[a, jj], "height": drop[a, jj], "vm": vm[ii, jj], "close": C[ii, jj], "low": L[ii, jj], "high": H[ii, jj],
                      "base_n": n, "bull": D["nifty"].bull.to_numpy()[ii], "grp": low_day[a, jj]})
    s["rank"] = s.height                                                          # bigger sell-off first
    return _keep(s, p, cap, 0, "grp")


# ---------------------------------------------------------------- type A: stock long
def path_a(D: dict, sig: pd.DataFrame, p: Params) -> pd.DataFrame:
    """Per-share path of every signal: entry, stop, target, exit (stop first when stop and target fall in the same candle;
    a gap through either fills at the open). Rows with `skip` set were not tradable."""
    W = D["W"]
    O, H, L, C = (W[k].to_numpy() for k in ("open", "high", "low", "close"))
    dates = D["cal"]
    nd = len(dates)
    trail_n = {"trail5": 5, "trail10": 10}.get(p.exit)
    rows = []
    for r in sig.itertuples():
        s, j = r.i, r.j
        base = {"signal_date": r.date, "symbol": r.symbol, "kind": r.kind, "base_n": r.base_n, "res": r.res, "lo": r.lo, "height": r.height, "vm": r.vm, "bull": r.bull}
        if p.entry == "next_open":
            e = s + 1
            if e >= nd or np.isnan(O[e, j]):
                rows.append({**base, "skip": "agla din data nahi"})
                continue
            entry, first = O[e, j], e
        else:
            e, entry, first = s, C[s, j], s + 1
        stop = (r.res if p.stop == "res" else L[s, j]) * (1 - p.stop_buf)
        if entry <= stop:
            rows.append({**base, "skip": "open stop ke neeche (gap)"})
            continue
        tgt = entry + p.rr * (entry - stop) if p.exit == "rr" else (entry * (1 + p.tgt_pct) if p.exit == "pct" else None)
        last = min(e + p.max_days - (1 if p.entry == "next_open" else 0), nd - 1)
        cur, why_stop, x, add, pend = stop, "SL", None, None, False
        for k in range(first, last + 1):
            o, h, l, c = O[k, j], H[k, j], L[k, j], C[k, j]
            if np.isnan(c):
                continue
            if pend:
                pend = False
                if o > entry and o > cur:
                    add = (k, o)
                    if cur < entry:
                        cur, why_stop = entry, "breakeven SL (add ke baad)"
            if not (p.entry == "next_open" and k == e):
                if o <= cur:
                    x = (k, o, why_stop + " (gap)")
                    break
                if tgt is not None and o >= tgt:
                    x = (k, o, "target (gap)")
                    break
            if l <= cur:
                x = (k, cur, why_stop)
                break
            if tgt is not None and h >= tgt:
                x = (k, tgt, "target")
                break
            if k == last:
                x = (k, c, f"{p.max_days} din time stop")
                break
            if trail_n:
                t = np.nanmin(L[max(0, k - trail_n + 1):k + 1, j])
                if t > cur:
                    cur, why_stop = t, f"trailing SL ({trail_n} din ka low)"
            if p.addon and add is None and not pend and c > np.nanmax(H[s:k, j]):
                pend = True
        if x is None:
            k = max(kk for kk in range(first - 1, last + 1) if not np.isnan(C[kk, j]))
            x = (k, C[k, j], "data khatam")
        rows.append({**base, "skip": None, "i": s, "j": j, "e": e, "x": x[0], "entry_date": dates[e], "entry": entry, "stop": stop, "target": tgt,
                     "exit_date": dates[x[0]], "exit": x[1], "reason": x[2], "hold": int(x[0] - e + (1 if p.entry == "next_open" else 0)),
                     "add_date": dates[add[0]] if add else None, "add_px": add[1] if add else np.nan})
    return pd.DataFrame(rows)


def fut_margin_rate(sigma: float, p: Params) -> float:
    """Futures margin (SPAN + exposure) as a share of the contract value, from the stock's daily sd."""
    return float(np.clip(p.span_k * (sigma if sigma == sigma else 0.02), 0.15, 0.50)) * p.margin_mult


def book_a(D: dict, t: pd.DataFrame, p: Params) -> pd.DataFrame:
    """Rs sizes in entry order: qty = capital x risk / (entry fill - stop), one stock at most max_val of capital, all open
    positions within the capital (stock: cash, no leverage; futures: whole lots, margin of open lots <= capital + booked P&L).
    One position per stock. Returns the traded rows (skipped ones in .attrs['skips'])."""
    if t.empty or "entry_date" not in t:
        return pd.DataFrame()
    t = t[t.skip.isna()].sort_values(["entry_date", "vm"], ascending=[True, False]).reset_index(drop=True)
    sig = D["W"]["sigma"].to_numpy()
    bfill, xfill, afill = t.entry * (1 + p.slip), t.exit * (1 - p.slip), t.add_px * (1 + p.slip)
    mult = 1.5 if p.addon else 1.0
    fut = p.a_inst == "fut"
    open_pos, out, booked, skips = [], [], [], {}
    for i, r in enumerate(t.itertuples()):
        open_pos = [o for o in open_pos if o[0] >= r.entry_date]
        if any(o[1] == r.symbol for o in open_pos):
            skips["same stock pehle se khula"] = skips.get("same stock pehle se khula", 0) + 1
            continue
        used = sum(o[2] for o in open_pos)
        rps = bfill[i] - r.stop
        if fut:
            lot = lot_on(D, r.symbol, r.entry_date)
            if not lot:
                skips["lot size nahi mila"] = skips.get("lot size nahi mila", 0) + 1
                continue
            lots = int(p.capital * p.risk // (rps * lot))
            mr = fut_margin_rate(sig[r.e, r.j], p)
            free = p.capital + sum(n for d, n in booked if d < r.entry_date) - used
            if lots < 1:
                skips["1 lot ka risk budget se zyada"] = skips.get("1 lot ka risk budget se zyada", 0) + 1
                continue
            lots = min(lots, int(free // (mr * lot * bfill[i] * mult)))
            if lots < 1:
                skips["margin kam"] = skips.get("margin kam", 0) + 1
                continue
            qty, lock = lots * lot, mr * lots * lot * bfill[i] * mult
        else:
            want = min(p.capital * p.risk / rps, p.capital * p.max_val / bfill[i])
            q = min(want, (p.capital - used) / (bfill[i] * mult))
            qty = int(q) if q >= 1 and q >= 0.5 * want else 0
            if not qty:
                skips["cash kam"] = skips.get("cash kam", 0) + 1
                continue
            lock = qty * bfill[i] * mult
        aq = (int(qty * 0.5) // (lot if fut else 1)) * (lot if fut else 1) if r.add_px == r.add_px else 0
        bv, sv = qty * bfill[i] + (aq * afill[i] if aq else 0.0), (qty + aq) * xfill[i]
        gross = sv - bv
        cost = charges("fut" if fut else "stock", bv, 0.0, r.entry_date, 1 + (1 if aq else 0)) + charges("fut" if fut else "stock", 0.0, sv, r.exit_date, 1)
        slip = qty * (bfill[i] - r.entry) + (qty + aq) * (r.exit - xfill[i]) + (aq * (afill[i] - r.add_px) if aq else 0.0)
        open_pos.append((r.exit_date, r.symbol, lock))
        net = gross - cost
        booked.append((r.exit_date, net))
        out.append({**r._asdict(), "qty": qty, "add_qty": aq, "add_fill": afill[i] if aq else np.nan, "entry_fill": bfill[i], "exit_fill": xfill[i],
                    "notional": qty * bfill[i], "margin": lock,
                    "risk_rs": qty * rps, "gross": gross, "slip": slip, "costs": cost, "net": net, "R": net / (qty * rps), "type": "A"})
    res = pd.DataFrame(out).drop(columns="Index") if out else pd.DataFrame()
    res.attrs["skips"] = skips
    return res


# ---------------------------------------------------------------- type B: put sell
def _years(day: str, exp: str) -> float:
    return max((pd.Timestamp(exp) - pd.Timestamp(day)).days, 0) / 365 + 0.5 / 365


def put_value(S: float, K: float, T: float, iv: float) -> float:
    return max(float(b76(S * np.exp(RATE * T), K, T, iv, "PE")[0]), K - S, 0.0)


def short_put_margin(S: float, K: float, sigma: float, p: Params, dte: int | None = None) -> float:
    """Approximate SPAN + exposure per share for a short put (actual prices): max(scan x spot - OTM amount, half the scan) +
    exposure 3.5% of spot; scan = futures margin rate - exposure. ITM in the last 4 days before expiry: + delivery margin."""
    m = fut_margin_rate(sigma, p) / p.margin_mult
    scan = m - p.elm
    v = max(scan * S - max(S - K, 0.0), 0.5 * scan * S) + p.elm * S
    if dte is not None and dte in DELIVERY and S < K:
        v += DELIVERY[dte] * m * K
    return v * p.margin_mult


def path_b(D: dict, sig: pd.DataFrame, p: Params) -> pd.DataFrame:
    """Per-share path of the put sale for every signal. The put is sold at the close of the first day after the signal
    (within p.wait days, the stock closing at/above support every day) when the nearest expiry is 2 .. p.dte trading days
    away. Each day after: premium stop (open / high of the put, stop first), support stop (stock close), take profit (low of
    the put), ITM on the day before expiry -> bought back (settle 'close'), expiry: OTM = worthless, ITM = bought back at the
    expiry close ('close') or delivery taken and sold at the next open ('physical'). Days the strike did not trade use a
    Black-76 value at the last traded IV."""
    W, ch = D["W"], D["chains"]
    C, O, F, SG = (W[k].to_numpy() for k in ("close", "open", "factor", "sigma"))
    dates, pos = D["cal"], D["pos"]
    nd = len(dates)
    mstop = {"prem2": 2.0, "loss15": 2.5}.get(p.b_stop)
    rows = []
    for r in sig.itertuples():
        s, j = r.i, r.j
        base = {"signal_date": r.date, "symbol": r.symbol, "kind": r.kind, "base_n": r.base_n, "res": r.res, "lo": r.lo, "height": r.height, "vm": r.vm, "bull": r.bull}
        ent = None
        for e in range(s + 1, min(s + p.wait, nd - 1) + 1):
            if not C[e, j] >= r.support:
                break
            X = ch.next_expiry(dates[e])
            if X is None or X not in pos:
                break
            if 2 <= pos[X] - e <= p.dte:
                ent = (e, X)
                break
        if ent is None:
            rows.append({**base, "skip": "expiry window se pehle setup toota / data khatam"})
            continue
        e, X = ent
        xi = pos[X]
        f = F[e, j]
        sup, spot_e = r.support * f, C[e, j] * f
        chain = ch.get(r.symbol, X)
        if chain is None or dates[e] not in chain["close"].index:
            rows.append({**base, "skip": "us din put me trade nahi"})
            continue
        row = chain["close"].loc[dates[e]].dropna()
        cand = row[row.index <= (0.95 * spot_e if p.strike == "otm5" else sup)]
        if p.strike == "below":
            cand = cand.iloc[:-1]
        if cand.empty:
            rows.append({**base, "skip": "support ke neeche koi strike trade nahi hua"})
            continue
        K, prem = float(cand.index[-1]), float(cand.iloc[-1])
        if prem < p.min_prem:
            rows.append({**base, "skip": f"premium Rs {p.min_prem:g} se kam"})
            continue
        lot = ch.lots.get((r.symbol, X))
        if not lot:
            rows.append({**base, "skip": "lot size nahi mila"})
            continue
        T_e = _years(dates[e], X)
        iv = float(implied_vol(prem, spot_e * np.exp(RATE * T_e), K, T_e, "PE"))
        if iv != iv:
            iv = float(np.clip((SG[e, j] if SG[e, j] == SG[e, j] else 0.02) * np.sqrt(252) * 1.2, 0.15, 1.5))
        slp = lambda v: max(p.opt_slip * v, p.opt_slip_min) if v > 0 else 0.0  # noqa: E731
        if mstop:
            risk_ps = (mstop - 1) * prem
        else:
            v_stop = put_value(sup * (1 - p.stop_gap), K, max(T_e - 1 / 365, 1 / 365), iv)
            risk_ps = max(v_stop - prem, 0.5 * prem)
        risk_ps += slp(prem) + slp(prem + risk_ps)
        days, vals, mps = [e], [prem], [short_put_margin(spot_e, K, SG[e, j], p, xi - e)]
        x, last_px, last_iv_day, est_n, cact = None, (e, prem), None, 0, False
        for k in range(e + 1, xi + 1):
            if np.isnan(C[k, j]):
                continue
            if abs(F[k, j] / f - 1) > 0.025:
                cact = True
                break
            d, spot = dates[k], C[k, j] * f
            T_k = _years(d, X)
            traded = d in chain["close"].index and K in chain["close"].columns and chain["close"].at[d, K] == chain["close"].at[d, K]
            if traded:
                o, h, l, c = (float(chain[q].at[d, K]) for q in ("open", "high", "low", "close"))
                o = o if o > 0 else c
                h, l = max(h, o, c), min(l if l > 0 else c, o, c)
                last_px = (k, c)
                val = c
            else:
                if last_iv_day != last_px[0]:
                    lk = last_px[0]
                    tl = _years(dates[lk], X)
                    v = float(implied_vol(last_px[1], C[lk, j] * f * np.exp(RATE * tl), K, tl, "PE"))
                    iv = v if v == v else iv
                    last_iv_day = lk
                val = put_value(spot, K, T_k, iv)
                o = h = l = c = val
                est_n += 1
            dte_k = xi - k
            days.append(k)
            vals.append(val)
            mps.append(short_put_margin(spot, K, SG[k, j], p, dte_k))
            sl_name = "SL: premium 2x" if mstop == 2 else "SL: loss 1.5x premium"
            if mstop and o >= mstop * prem:
                x = (k, o, sl_name + " (gap)")
            elif mstop and h >= mstop * prem:
                x = (k, mstop * prem, sl_name)
            elif p.b_stop == "support" and spot < sup:
                x = (k, c, "SL: stock support ke neeche close")
            elif p.take and l <= prem * (1 - p.take):
                x = (k, min(o, prem * (1 - p.take)), f"target: {p.take * 100:.0f}% premium kamaya")
            elif p.settle == "close" and dte_k == 1 and spot < K:
                x = (k, c, "ITM: expiry se 1 din pehle band")
            elif k == xi:
                if spot >= K:
                    x = (k, 0.0, "expiry: OTM, bekaar khatam")
                elif p.settle == "close":
                    x = (k, max(c, K - spot), "expiry din ITM, close pe band")
                elif xi + 1 < nd and not np.isnan(O[xi + 1, j]):
                    x = (k, K - O[xi + 1, j] * f, "physical delivery: share agle din open pe beche")
                else:
                    x = (k, K - spot, "physical delivery (agle din data nahi)")
            if x:
                if x[2].startswith("physical"):
                    vals[-1] = x[1]
                break
        if cact:
            rows.append({**base, "skip": "trade ke beech corporate action (split / bonus)"})
            continue
        if x is None:
            rows.append({**base, "skip": "data khatam"})
            continue
        rows.append({**base, "skip": None, "i": s, "j": j, "e": e, "x": x[0], "entry_date": dates[e], "expiry": X, "dte": int(xi - e), "strike": K, "prem": prem,
                     "lot": int(lot), "support": sup, "spot": spot_e, "factor": f, "otm_pct": (1 - K / spot_e) * 100, "iv": iv, "risk_ps": risk_ps,
                     "exit_date": dates[x[0]], "exit": float(x[1]), "reason": x[2], "hold": int(x[0] - e), "est_days": est_n,
                     "days": days, "vals": vals, "mps": mps})
    return pd.DataFrame(rows)


def book_b(D: dict, t: pd.DataFrame, p: Params) -> pd.DataFrame:
    """Lots = capital x risk / (risk per share from the stop rule x lot size); a trade is skipped when 1 lot is over the risk
    budget, or when the margin of all open puts on its entry day (recomputed every day: approximate SPAN, delivery margin when
    ITM near expiry) plus its own would be above capital + booked P&L. One put per stock. Returns the traded rows."""
    if t.empty or "e" not in t:
        return pd.DataFrame()
    t = t[t.skip.isna()].sort_values(["entry_date", "vm"], ascending=[True, False]).reset_index(drop=True)
    slp = lambda v: max(p.opt_slip * v, p.opt_slip_min) if v > 0 else 0.0  # noqa: E731
    open_pos, out, booked, skips = [], [], [], {}
    for r in t.itertuples():
        open_pos = [o for o in open_pos if o["x"] >= r.e]
        if any(o["symbol"] == r.symbol for o in open_pos):
            skips["same stock pehle se khula"] = skips.get("same stock pehle se khula", 0) + 1
            continue
        lots = int(p.capital * p.risk // (r.risk_ps * r.lot))
        if lots < 1:
            skips["1 lot ka risk budget se zyada"] = skips.get("1 lot ka risk budget se zyada", 0) + 1
            continue
        used = sum(o["mps"][o["days"].index(r.e)] * o["qty"] if r.e in o["days"] else 0.0 for o in open_pos)
        free = p.capital + sum(n for d, n in booked if d < r.entry_date) - used
        lots = min(lots, int(free // (r.mps[0] * r.lot)))
        if lots < 1:
            skips["margin kam"] = skips.get("margin kam", 0) + 1
            continue
        qty = lots * r.lot
        sell = r.prem - slp(r.prem)
        phys = r.reason.startswith("physical")
        buy = r.exit + slp(r.exit) if r.exit > 0 and not phys else (r.exit if phys else 0.0)
        gross = qty * (sell - buy)
        cost = charges("opt", 0.0, sell * qty, r.entry_date, 1)
        if phys:
            K, S1 = r.strike, r.strike - r.exit
            cost += charges("stock", K * qty, 0.0, r.exit_date, 0) + p.phys_fee * K * qty + charges("stock", 0.0, S1 * (1 - p.slip) * qty, r.exit_date, 0)
            gross -= p.slip * S1 * qty
        elif r.exit > 0:
            cost += charges("opt", buy * qty, 0.0, r.exit_date, 1)
        net = gross - cost
        margin = r.mps[0] * qty
        peak_m = max(r.mps) * qty
        o = {"x": r.x, "symbol": r.symbol, "qty": qty, "days": r.days, "mps": r.mps}
        open_pos.append(o)
        booked.append((r.exit_date, net))
        rec = {k: v for k, v in r._asdict().items() if k not in ("Index", "days", "vals", "mps")}
        out.append({**rec, "lots": lots, "qty": qty, "sell_fill": sell, "buy_fill": buy, "premium_rs": sell * qty, "margin": margin, "peak_margin": peak_m,
                    "notional": r.strike * qty, "risk_rs": r.risk_ps * qty, "gross": gross, "slip": qty * (r.prem - sell) + (qty * (buy - r.exit) if r.exit > 0 and not phys else 0.0),
                    "costs": cost, "net": net, "R": net / (r.risk_ps * qty), "loss_x_prem": net / (sell * qty), "ret_prem": net / (sell * qty) * 100,
                    "ret_margin": net / margin * 100, "ret_notional": net / (r.strike * qty) * 100, "deliver_rs": r.strike * qty if phys else 0.0,
                    "days": r.days, "vals": r.vals, "type": "B"})
    res = pd.DataFrame(out) if out else pd.DataFrame()
    res.attrs["skips"] = skips
    return res


# ---------------------------------------------------------------- numbers
def stats(t: pd.DataFrame, capital: float, months: float) -> dict:
    if t is None or t.empty:
        return {"trades": 0, "net": 0.0, "win_pct": None, "profit_factor": None, "max_dd": 0.0, "avg_r": None, "avg_win_r": None, "avg_loss_r": None,
                "worst": 0.0, "best": 0.0, "avg_win": None, "avg_loss": None, "ret_pct": 0.0, "month_ret": 0.0, "per_month": 0.0, "gross": 0.0, "costs": 0.0,
                "expectancy_rs": None}
    n = t.net
    w, l = n[n > 0], n[n <= 0]
    cum = t.groupby("exit_date").net.sum().sort_index().cumsum()
    dd = float((cum - cum.cummax().clip(lower=0)).min())
    return {"trades": int(len(t)), "net": float(n.sum()), "win_pct": float((n > 0).mean() * 100), "profit_factor": float(w.sum() / -l.sum()) if l.sum() < 0 else None,
            "max_dd": dd, "avg_r": float(t.R.mean()), "avg_win_r": float(t.R[n > 0].mean()) if len(w) else None, "avg_loss_r": float(t.R[n <= 0].mean()) if len(l) else None,
            "worst": float(n.min()), "best": float(n.max()), "avg_win": float(w.mean()) if len(w) else None, "avg_loss": float(l.mean()) if len(l) else None,
            "ret_pct": float(n.sum() / capital * 100), "month_ret": float(n.sum() / capital / months * 100), "per_month": len(t) / months,
            "gross": float(t.gross.sum()), "costs": float(t.costs.sum()), "expectancy_rs": float(n.mean())}


def daily_mtm(D: dict, t: pd.DataFrame) -> pd.Series:
    """Daily mark-to-market P&L of a book (Rs, by date): A from stock closes, B from the put's daily value; costs on the exit day."""
    cal = D["cal"]
    pnl = np.zeros(len(cal))
    if t is None or t.empty:
        return pd.Series(pnl, index=cal)
    if t.type.iloc[0] == "A":
        C = D["W"]["close"].to_numpy()
        for r in t.itertuples():
            add_i = D["pos"].get(r.add_date) if isinstance(r.add_date, str) and r.add_qty else None
            held, prev = r.qty, r.entry_fill
            for k in range(r.e, r.x + 1):
                px = r.exit_fill if k == r.x else C[k, r.j]
                if px != px:
                    continue
                pnl[k] += held * (px - prev)
                if k == add_i:
                    pnl[k] += r.add_qty * (px - r.add_fill)
                    held += r.add_qty
                prev = px
            pnl[r.x] -= r.costs
        return pd.Series(pnl, index=cal)
    for r in t.itertuples():
        prev = r.sell_fill
        for k, v in zip(r.days[1:], r.vals[1:]):
            v = r.buy_fill if k == r.x else v
            pnl[k] += r.qty * (prev - v)
            prev = v
        pnl[r.x] += r.gross - r.qty * (r.sell_fill - r.buy_fill) - r.costs
    return pd.Series(pnl, index=cal)


def breakout_study(D: dict, p: Params) -> pd.DataFrame:
    """Every qualifying breakout (no monthly cap, volume >= 1.5x, other filters as p): did it fail (a close back inside the base,
    below its top, within 5 days), the 5 / 10 / 20 day return, did +1R / +2R come before the stop (stop = base top - 0.5%,
    entry next open, no time limit inside 20 days), and the per-share R of the type A trade with p's exits."""
    q = replace(p, vol_mult=1.5)
    s = signals(D, q, cap=False)
    W = D["W"]
    O, H, L, C = (W[k].to_numpy() for k in ("open", "high", "low", "close"))
    nd = C.shape[0]
    out = []
    for r in s.itertuples():
        i, j = r.i, r.j
        fut = C[i + 1:i + 6, j]
        fut = fut[~np.isnan(fut)]
        fail5 = bool(np.any(fut < r.res)) if i + 5 < nd else None
        fwd = {n: (C[i + n, j] / C[i, j] - 1) * 100 if i + n < nd else np.nan for n in (5, 10, 20)}
        hit1 = hit2 = None
        if i + 1 < nd and not np.isnan(O[i + 1, j]):
            en, st = O[i + 1, j], r.res * (1 - q.stop_buf)
            if en > st:
                rk = en - st
                hit1 = hit2 = False
                for k in range(i + 1, min(i + 21, nd)):
                    if L[k, j] <= st:
                        break
                    if H[k, j] >= en + rk:
                        hit1 = True
                    if H[k, j] >= en + 2 * rk:
                        hit2 = True
                        break
        out.append({"date": r.date, "symbol": r.symbol, "vm": r.vm, "bull": r.bull, "fail5": fail5, "fwd5": fwd[5], "fwd10": fwd[10], "fwd20": fwd[20], "hit1R": hit1, "hit2R": hit2})
    st = pd.DataFrame(out)
    a = path_a(D, s, q)
    a = a[a.skip.isna()]
    a["r_ps"] = ((a.exit * (1 - q.slip)) - a.entry * (1 + q.slip)) / (a.entry * (1 + q.slip) - a.stop)
    return st.merge(a[["signal_date", "symbol", "r_ps"]].rename(columns={"signal_date": "date"}), on=["date", "symbol"], how="left")


def gap_stress(D: dict, b: pd.DataFrame, p: Params, gaps=(0.05, 0.10, 0.15, 0.20), vol_up: float = 0.5) -> pd.DataFrame:
    """Every B trade hit by an overnight gap-down the day after the sale: put value = Black-76 at spot x (1 - gap), one day less,
    IV x (1 + vol_up), bought back there (+ slippage). Loss in Rs and as a multiple of the premium collected / planned risk."""
    rows = []
    for r in b.itertuples():
        T1 = max(_years(D["cal"][min(r.e + 1, len(D["cal"]) - 1)], r.expiry), 1 / 365)
        for g in gaps:
            v = put_value(r.spot * (1 - g), r.strike, T1, r.iv * (1 + vol_up))
            buy = v + max(p.opt_slip * v, p.opt_slip_min)
            loss = r.qty * (r.sell_fill - buy) - charges("opt", 0.0, r.premium_rs, r.entry_date, 1) - charges("opt", buy * r.qty, 0.0, r.entry_date, 1)
            rows.append({"entry_date": r.entry_date, "symbol": r.symbol, "gap": g, "loss": loss, "x_prem": loss / r.premium_rs, "x_risk": loss / r.risk_rs,
                         "pct_cap": loss / p.capital * 100})
    return pd.DataFrame(rows)
