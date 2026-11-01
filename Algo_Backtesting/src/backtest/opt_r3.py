"""Option buyer "R3 momentum": buy a stock CALL when the option's own premium closes above its R3 pivot (from the option's
previous-day High / Low / Close), exit the next day (BTST). Daily NSE option bhavcopy, expired contracts included.

    rows = load("2024-11-01", "2026-10-29")
    sig = signals(rows, "classic", "CE")              # every signal of every strike class, with next-day OHLC
    trades = pick(sig, strike=0)                      # max 2 a day (highest turnover), all exit variants as columns
"""
import numpy as np
import pandas as pd

from backtest.config import FNO

OPT_DIR = FNO / "bhavcopy_opt"
COLS = {"TradDt": "date", "TckrSymb": "symbol", "XpryDt": "expiry", "StrkPric": "strike", "OptnTp": "type", "OpnPric": "open",
        "HghPric": "high", "LwPric": "low", "ClsPric": "close", "UndrlygPric": "und", "TtlTradgVol": "vol",
        "TtlNbOfTxsExctd": "trades", "NewBrdLotQty": "lot"}
MIN_TRADES, MIN_TRADES_PREV, MIN_PRICE = 200, 50, 1.0
PER_TRADE = 10_000.0             # Rs of premium bought per trade
MAX_PER_DAY = 2
# exit variants: (key, label, sl share of premium or None, reward:risk or None, time exit "open" / "close")
EXITS = [("open", "Agle din open (9:15)", None, None, "open"), ("close", "Agle din close (~3:15)", None, None, "close")]
EXITS += [(f"sl{int(s * 100)}_t{r}", f"SL {int(s * 100)}% + target 1:{r}", s, r, "close") for s in (0.2, 0.3) for r in (1, 2, 3)]


def load(start: str, end: str) -> pd.DataFrame:
    files = [p for p in sorted(OPT_DIR.glob("*.csv")) if start <= p.stem <= end]
    d = pd.concat([pd.read_csv(p, usecols=list(COLS)) for p in files], ignore_index=True).rename(columns=COLS)
    d["symbol"] = d.symbol.astype("category")
    return d


def r3(h, l, c, kind: str):
    if kind == "classic":
        p = (h + l + c) / 3
        return h + 2 * (p - l)
    return c + 1.1 * (h - l) / 4                         # camarilla


def signals(d: pd.DataFrame, pivot: str, typ: str = "CE") -> pd.DataFrame:
    """One row per contract-day where close > R3 (with liquidity), strike class k (0 = ATM, 1 = 1 OTM, 2 = 2 OTM) and the
    contract's next trading day OHLC. Nearest expiry after the signal day only."""
    days = sorted(d.date.unique())
    nxt = dict(zip(days[:-1], days[1:]))
    prv = dict(zip(days[1:], days[:-1]))
    x = d[d.type == typ].copy()
    first_exp = x[x.expiry > x.date].groupby(["date", "symbol"], observed=True).expiry.min().rename("near")
    x = x.join(first_exp, on=["date", "symbol"])
    x = x[x.expiry == x.near].drop(columns="near")
    # strike class from the strikes that traded that day
    x["atm_gap"] = (x.strike - x.und).abs()
    x = x.sort_values(["date", "symbol", "strike"])
    g = x.groupby(["date", "symbol"], observed=True)
    x["rank"] = g.cumcount()
    atm_rank = x.loc[x.groupby(["date", "symbol"], observed=True).atm_gap.idxmin(), ["date", "symbol", "rank"]]
    x = x.merge(atm_rank.rename(columns={"rank": "atm_rank"}), on=["date", "symbol"])
    x["k"] = (x["rank"] - x.atm_rank) * (1 if typ == "CE" else -1)
    key = ["symbol", "expiry", "strike"]
    x = x.sort_values(key + ["date"])
    c = x.groupby(key, observed=True)
    for col in ["date", "high", "low", "close", "trades"]:
        x["p_" + col] = c[col].shift(1)
    for col in ["date", "open", "high", "low", "close", "trades"]:
        x["n_" + col] = c[col].shift(-1)
    x = x[(x.p_date == x.date.map(prv)) & (x.n_date == x.date.map(nxt))]
    x["r3"] = r3(x.p_high, x.p_low, x.p_close, pivot)
    x["above_pct"] = (x.close / x.r3 - 1) * 100
    ok = (x.close > x.r3) & (x.trades >= MIN_TRADES) & (x.p_trades >= MIN_TRADES_PREV) & (x.close >= MIN_PRICE)
    s = x[ok & x.k.between(0, 2)].copy()
    s["turnover"] = s.vol * s.lot * s.close
    s["dte"] = (pd.to_datetime(s.expiry) - pd.to_datetime(s.date)).dt.days
    return s.reset_index(drop=True)


def exit_price(s: pd.DataFrame, sl: float | None, rr: int | None, when: str) -> tuple[np.ndarray, np.ndarray]:
    """Next-day exit for each signal. With SL/target: gap through either -> next open; both inside the day -> SL first."""
    e = s.close.to_numpy()
    o, h, l, c = (s["n_" + k].to_numpy() for k in ("open", "high", "low", "close"))
    if sl is None:
        x = o if when == "open" else c
        return x, np.full(len(s), "agle din open" if when == "open" else "agle din close")
    stop, tgt = e * (1 - sl), e * (1 + sl * rr)
    x = np.select([o <= stop, o >= tgt, l <= stop, h >= tgt], [o, o, stop, tgt], c)
    why = np.select([o <= stop, o >= tgt, l <= stop, h >= tgt], ["SL (gap)", "target (gap)", "SL", "target"], "agle din close")
    return x, why


def pnl(entry: np.ndarray, exit_: np.ndarray, slip: float) -> tuple[np.ndarray, np.ndarray]:
    buy, sell = entry * (1 + slip), exit_ * (1 - slip)
    qty = PER_TRADE / buy
    bv, sv = qty * buy, qty * sell
    brokerage = 40.0
    exch, sebi = 0.0003503 * (bv + sv), 0.000001 * (bv + sv)
    costs = brokerage + exch + sebi + 0.18 * (brokerage + exch + sebi) + 0.001 * sv + 0.00003 * bv
    return sv - bv, costs


def with_exits(s: pd.DataFrame, slip: float) -> pd.DataFrame:
    s = s.copy()
    for key, _, sl, rr, when in EXITS:
        x, why = exit_price(s, sl, rr, when)
        g, cst = pnl(s.close.to_numpy(), x, slip)
        s[key + "_px"], s[key + "_why"], s[key + "_net"] = np.round(x, 2), why, np.round(g - cst, 2)
    return s


def pick(s: pd.DataFrame, strike: int, max_per_day: int = MAX_PER_DAY) -> pd.DataFrame:
    """Signals of one strike class, at most max_per_day per day (highest Rs turnover first)."""
    x = s[s.k == strike].sort_values(["date", "turnover"], ascending=[True, False])
    return x.groupby("date").head(max_per_day).reset_index(drop=True)
