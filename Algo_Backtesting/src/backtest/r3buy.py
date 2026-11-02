"""Option BUYING on pivot R3 / S3 breaks, BTST, on real NSE daily option bhavcopy (expired contracts included).

What daily data can test honestly (signal known when the day's last candle closes, entry at that close = last traded
price, exit the next trading day):
    A    CALL premium closes above its own R3 (from the option's previous-day H / L / C)        -> buy that CALL
    C    underlying closes above its R3 -> buy a CALL;  below its S3 -> buy a PUT   (from the underlying's previous-day H / L / C)
    AC   A and C together (one pick list)
On daily data setup D (15-min + 60-min + daily all beyond the level) is the same as C: the day's last 15-min, 60-min and
daily candles all close at the same price. Setup B, intraday C / D and every intraday exit (9:30 / 10:30, close back
below R3, trailing 15-min low) need intraday candles of expired option contracts, which the data does not have.

    d = load_options(CFG)                 # near-expiry contract-days (+ the next day of each contract)
    pool = build_pool(d, load_underlying(CFG, syms), load_vix(CFG), CFG)
    t = pick(pool, "A", "index", "classic", 0, gap=False)
"""
from pathlib import Path

import numpy as np
import pandas as pd

from backtest import fast
from backtest.config import DATA, FNO

INDEX = {"NIFTY": "NIFTY50", "BANKNIFTY": "BANKNIFTY"}        # option symbol -> data/index/<name>_1m.csv

# every parameter of the test (the report prints these)
CFG = {
    "start": "2024-11-01", "end": "2026-10-30",
    "capital": 1_000_000.0,          # Rs, fixed (no compounding): risk and Return % always on this
    "risk_pct": 1.0,                 # % of capital lost if the stop is hit
    "risk_grid": [0.5, 1.0, 1.5],
    "sizing_sl": 0.30,               # stop used for the qty when the exit itself has no stop (next-day open / close)
    "min_stop": 0.05,                # "signal-day low" stop is at least 5% below entry
    "max_premium_pct": 10.0,         # one trade's premium at most 10% of capital
    "max_per_day": 2,                # = max 2 open trades (BTST: yesterday's trades are closed before today's close)
    "slip": 0.01,                    # share of premium per fill, buy and sell
    "slip_grid": [0.005, 0.01, 0.02],
    "min_trades": 200, "min_trades_prev": 50, "min_price": 3.0,     # liquidity: signal day / previous day trades, premium
    "max_vol_share": 0.05,           # buy at most 5% of the contracts traded on the signal day
    "min_session_bars": 300,         # days with fewer NIFTY 1-min bars (muhurat sessions) are left out
    "strike_band": 0.12,             # strikes within 12% of the underlying are read (ATM .. 2 OTM always inside)
    "gap_min": 0.0025,               # gap filter: underlying opened >= 0.25% beyond yesterday's close, in signal direction
    "is_frac": 0.60,                 # first 60% of the trading days = in-sample (fit), rest = out-of-sample
    "min_is_trades": 30,
    "n_random": 1000, "seed": 7,
    "pivots": ["classic", "camarilla"], "strikes": [0, 1, 2],
    "setups": ["A", "C", "AC"], "universes": ["index", "stocks", "all"],
    # charges (Fyers / NSE): per order, and shares of premium value
    "brokerage": 20.0, "exch": 0.0003503, "sebi": 0.000001, "stamp_buy": 0.00003, "gst": 0.18,
    "stt_sell": 0.001, "stt_sell_new": 0.0015, "stt_new_from": "2026-04-01",
}

# exit grid: key, Hinglish label, family (sizing stop), stop share (None / "low"), reward:risk, time exit
EXITS = [("open", "Agle din open (9:15)", "t", None, None, "open"), ("close", "Agle din close (3:30)", "t", None, None, "close")]
EXITS += [(f"sl{int(s * 100)}_t{r}", f"SL {int(s * 100)}% + target 1:{r}", f"sl{int(s * 100)}", s, r, "close") for s in (0.2, 0.3) for r in (1, 2, 3)]
EXITS += [(f"low_t{r}", f"SL signal din ka low + target 1:{r}", "low", "low", r, "close") for r in (1, 2, 3)]
WHY = ["agle din open", "agle din close", "SL (gap)", "target (gap)", "SL", "target"]

OPT_COLS = {"TradDt": "date", "TckrSymb": "symbol", "XpryDt": "expiry", "StrkPric": "strike", "OptnTp": "type", "OpnPric": "open",
            "HghPric": "high", "LwPric": "low", "LastPric": "close", "ClsPric": "nse_close", "UndrlygPric": "und", "OpnIntrst": "oi",
            "TtlTradgVol": "vol", "TtlNbOfTxsExctd": "trades", "NewBrdLotQty": "lot"}


# ---------------------------------------------------------------- data
def _read_day(path: Path, band: float) -> pd.DataFrame:
    """One bhavcopy day: the 2 nearest expiries >= today of every symbol, strikes within <band> of the underlying."""
    d = pd.read_csv(path, usecols=list(OPT_COLS)).rename(columns=OPT_COLS)
    d = d[d.expiry >= d.date]
    first2 = d.groupby("symbol").expiry.transform(lambda e: e.isin(sorted(e.unique())[:2]))
    return d[first2 & ((d.strike / d.und - 1).abs() <= band)]


def load_options(cfg: dict = CFG, cache: Path | None = None) -> pd.DataFrame:
    """Stock (bhavcopy_opt) + index (bhavcopy_idx) option rows. close = last traded price (tradeable at the close), not
    NSE's 30-min average close. near = nearest expiry AFTER the day (the contract a signal buys)."""
    if cache is not None and cache.exists():
        d = pd.read_parquet(cache)
        return d[~d.date.isin(short_days(cfg))]
    files = [p for sub in ("bhavcopy_opt", "bhavcopy_idx") for p in sorted((FNO / sub).glob("*.csv"))
             if cfg["start"] <= p.stem <= cfg["end"]]
    d = pd.concat([_read_day(p, cfg["strike_band"]) for p in files], ignore_index=True)
    d["near"] = d.join(d[d.expiry > d.date].groupby(["date", "symbol"]).expiry.min().rename("near"), on=["date", "symbol"]).near
    for c in ("symbol", "type"):
        d[c] = d[c].astype("category")
    if cache is not None:
        d.to_parquet(cache)
    return d[~d.date.isin(short_days(cfg))]


def short_days(cfg: dict) -> set:
    """Special short sessions (Diwali muhurat, ~1 hour): their candles make no sense as "yesterday" for a pivot."""
    m = pd.read_csv(DATA / "index" / "NIFTY50_1m.csv", usecols=[0], names=["dt"], header=0)
    n = m.dt.str[:10].value_counts()
    return set(n[(n < cfg["min_session_bars"]) & (n.index >= cfg["start"]) & (n.index < cfg["end"])].index)


def load_underlying(cfg: dict, symbols) -> pd.DataFrame:
    """Daily OHLC of every underlying (stocks: fast store panel, split-adjusted; NIFTY / BANKNIFTY: index 1-min candles)
    with yesterday's H / L / C, R3 / S3 (both pivot types) and the opening gap."""
    lo = (pd.Timestamp(cfg["start"]) - pd.Timedelta(days=15)).strftime("%Y-%m-%d")
    p = fast.panel(lo, cfg["end"], columns=["date", "symbol", "open", "high", "low", "close"])
    p = p[p.symbol.isin(set(symbols))]
    idx = []
    for sym, name in INDEX.items():
        m = pd.read_csv(DATA / "index" / f"{name}_1m.csv", usecols=[0, 1, 2, 3, 4], names=["dt", "open", "high", "low", "close"], header=0)
        m = m[m.dt >= lo]
        m["date"] = m.dt.str[:10]
        g = m.groupby("date")
        idx.append(pd.DataFrame({"open": g.open.first(), "high": g.high.max(), "low": g.low.min(), "close": g.close.last()}).reset_index().assign(symbol=sym))
    u = pd.concat([p.astype({"symbol": str})] + idx, ignore_index=True).sort_values(["symbol", "date"])
    u = u[~u.date.isin(short_days(cfg))]
    g = u.groupby("symbol")
    for c in ("high", "low", "close"):
        u["y" + c[0]] = g[c].shift(1)
    u["gap_pct"] = (u.open / u.yc - 1) * 100
    for pv in cfg["pivots"]:
        u[f"ur3_{pv}"], u[f"us3_{pv}"] = r3(u.yh, u.yl, u.yc, pv), s3(u.yh, u.yl, u.yc, pv)
    return u[u.date >= cfg["start"]].rename(columns={"open": "u_open", "high": "u_high", "low": "u_low", "close": "u_close"})


def load_vix(cfg: dict) -> pd.Series:
    """India VIX close of every day (date -> value)."""
    m = pd.read_csv(DATA / "index" / "INDIAVIX_1m.csv", usecols=[0, 4], names=["dt", "close"], header=0)
    m = m[m.dt >= cfg["start"]]
    return m.groupby(m.dt.str[:10]).close.last()


# ---------------------------------------------------------------- pivots
def r3(h, l, c, kind: str):
    if kind == "classic":
        p = (h + l + c) / 3
        return h + 2 * (p - l)
    return c + 1.1 * (h - l) / 4                          # camarilla


def s3(h, l, c, kind: str):
    if kind == "classic":
        p = (h + l + c) / 3
        return l - 2 * (h - p)
    return c - 1.1 * (h - l) / 4


# ---------------------------------------------------------------- pool: every candidate contract-day
def build_pool(d: pd.DataFrame, und: pd.DataFrame, vix: pd.Series, cfg: dict = CFG) -> tuple[pd.DataFrame, dict]:
    """One row per liquid, near-expiry ATM / 1 OTM / 2 OTM contract-day (CE and PE) with yesterday's and the next day's
    candle of the same contract, signal flags (A_<pivot>, C_<pivot>, gapdir) and the exit / qty / net P&L of every exit.
    Returns (pool, counts) - counts says how many rows were dropped and why."""
    days = sorted(d.date.unique())
    nxt, prv = dict(zip(days[:-1], days[1:])), dict(zip(days[1:], days[:-1]))
    key = ["symbol", "expiry", "strike", "type"]
    d = d.sort_values(key + ["date"]).reset_index(drop=True)
    g = d.groupby(key, observed=True, sort=False)
    for c in ("date", "high", "low", "close", "trades"):
        d["p_" + c] = g[c].shift(1)
    for c in ("date", "open", "high", "low", "close"):
        d["n_" + c] = g[c].shift(-1)
    x = d[d.expiry == d.near].drop(columns="near").copy()
    # strike class: rank among the strikes that traded today, ATM = nearest to the underlying's close
    x["atm_gap"] = (x.strike - x.und).abs()
    x = x.sort_values(["date", "symbol", "type", "strike"]).reset_index(drop=True)
    gg = x.groupby(["date", "symbol", "type"], observed=True)
    x["rank"] = gg.cumcount()
    x["atm_rank"] = x["rank"].where(x.atm_gap == gg.atm_gap.transform("min")).groupby([x.date, x.symbol, x.type], observed=True).transform("min")
    x["k"] = ((x["rank"] - x.atm_rank) * np.where(x.type == "CE", 1, -1)).astype(int)
    x = x[x.k.between(0, 2)].copy()
    counts = {"contract_days": len(x)}
    x = x[x.p_date == x.date.map(prv)]
    liquid = (x.trades >= cfg["min_trades"]) & (x.p_trades >= cfg["min_trades_prev"]) & (x.close >= cfg["min_price"])
    counts["liquid"] = int(liquid.sum())
    x = x[liquid]
    has_next = x.n_date == x.date.map(nxt)
    counts["no_next_day"] = int((~has_next).sum())
    x = x[has_next].drop(columns=["atm_gap", "rank", "atm_rank"])
    x["symbol"], x["type"] = x.symbol.astype(str), x.type.astype(str)

    x = x.merge(und[["date", "symbol", "u_open", "u_close", "yc", "gap_pct"] + [c for c in und if c.startswith(("ur3_", "us3_"))]],
                on=["date", "symbol"], how="left")
    counts["no_underlying"] = int(x.u_close.isna().sum())
    x["vix"] = x.date.map(vix)
    ce = x.type == "CE"
    for pv in cfg["pivots"]:
        x[f"r3_{pv}"] = r3(x.p_high, x.p_low, x.p_close, pv)
        x[f"A_{pv}"] = ce & (x.close > x[f"r3_{pv}"])
        x[f"C_{pv}"] = np.where(ce, x.u_close > x[f"ur3_{pv}"], x.u_close < x[f"us3_{pv}"])
    x["gapdir"] = np.where(ce, x.gap_pct >= cfg["gap_min"] * 100, x.gap_pct <= -cfg["gap_min"] * 100)
    x["is_index"] = x.symbol.isin(list(INDEX))
    x["turnover"] = x.vol * x.lot * x.close
    x["dte"] = (pd.to_datetime(x.expiry) - pd.to_datetime(x.date)).dt.days
    iso_d, iso_e = pd.to_datetime(x.date).dt.isocalendar(), pd.to_datetime(x.expiry).dt.isocalendar()
    x["exp_week"] = (iso_d.year.values == iso_e.year.values) & (iso_d.week.values == iso_e.week.values)
    x = add_exits(x, cfg)
    return x.reset_index(drop=True), counts


# ---------------------------------------------------------------- exits, qty, costs
def exit_fill(e, stop, o, h, l, c, rr, when):
    """Exit price and reason code (index into WHY) for every row. Next-day candle only (entry was at the signal day's
    close). Gap through the stop / target -> next-day open; stop and target both inside the day -> STOP first."""
    if rr is None:
        return (o, np.zeros(len(e), int)) if when == "open" else (c, np.ones(len(e), int))
    tgt = e + rr * (e - stop)
    conds = [o <= stop, o >= tgt, l <= stop, h >= tgt]
    return np.select(conds, [o, o, stop, tgt], c), np.select(conds, [2, 3, 4, 5], 1)


def lots_for(e, stop, lot, vol, cfg: dict, risk_pct: float, slip: float) -> np.ndarray:
    """Whole lots so that hitting the stop loses about risk_pct of capital; premium capped at max_premium_pct of capital
    and at max_vol_share of the contracts traded on the signal day."""
    risk = cfg["capital"] * risk_pct / 100
    by_risk = np.floor(risk / ((e - stop) * lot))
    by_cap = np.floor(cfg["capital"] * cfg["max_premium_pct"] / 100 / (e * (1 + slip) * lot))
    by_vol = np.floor(vol * cfg["max_vol_share"])
    return np.maximum(np.minimum(np.minimum(by_risk, by_cap), by_vol), 0)


def costs(bv, sv, sell_day, cfg: dict = CFG) -> dict:
    """All charges of one round trip (buy value bv, sell value sv). Same rates as straddle.fill_cost (STT on option sells
    0.1%, 0.15% from 1 Apr 2026 - Budget 2026)."""
    stt_rate = np.where(np.asarray(sell_day) >= cfg["stt_new_from"], cfg["stt_sell_new"], cfg["stt_sell"])
    brk = 2 * cfg["brokerage"]
    exch = cfg["exch"] * (bv + sv)
    sebi = cfg["sebi"] * (bv + sv)
    out = {"brokerage": np.full(len(bv), brk), "stt": stt_rate * sv, "exch": exch + sebi, "stamp": cfg["stamp_buy"] * bv, "gst": cfg["gst"] * (brk + exch + sebi)}
    out["total"] = sum(out.values())
    return out


def stops(x: pd.DataFrame, cfg: dict) -> dict:
    """Sizing stop of each family: t (time exits, nominal sizing_sl), sl20, sl30, low."""
    e = x.close.to_numpy()
    return {"t": e * (1 - cfg["sizing_sl"]), "sl20": e * 0.8, "sl30": e * 0.7, "low": np.minimum(x.low.to_numpy(), e * (1 - cfg["min_stop"]))}


def add_exits(x: pd.DataFrame, cfg: dict, risk_pct: float | None = None, slip: float | None = None) -> pd.DataFrame:
    """Per exit: <key>_why (code), <key>_px, <key>_net (Rs, NaN when 0 lots); per family: lots_<fam>."""
    risk_pct = cfg["risk_pct"] if risk_pct is None else risk_pct
    slip = cfg["slip"] if slip is None else slip
    e, lot = x.close.to_numpy(), x.lot.to_numpy()
    o, h, l, c = (x["n_" + k].to_numpy() for k in ("open", "high", "low", "close"))
    st = stops(x, cfg)
    cols = {}
    for fam, stop in st.items():
        cols["lots_" + fam] = lots_for(e, stop, lot, x.vol.to_numpy(), cfg, risk_pct, slip)
    for key, _, fam, _, rr, when in EXITS:
        px, why = exit_fill(e, st[fam], o, h, l, c, rr, when)
        qty = cols["lots_" + fam] * lot
        bv, sv = qty * e * (1 + slip), qty * px * (1 - slip)
        net = sv - bv - costs(bv, sv, x.n_date.to_numpy(), cfg)["total"]
        cols[key + "_px"], cols[key + "_why"], cols[key + "_net"] = px, why, np.where(qty > 0, net, np.nan)
    return x.drop(columns=[c for c in cols if c in x]).assign(**cols)


# ---------------------------------------------------------------- picking trades
def signal_mask(pool: pd.DataFrame, setup: str, univ: str, pivot: str, k: int, gap: bool) -> pd.Series:
    m = pool.k == k
    if univ != "all":
        m &= pool.is_index == (univ == "index")
    sig = pool[f"A_{pivot}"] if setup == "A" else pool[f"C_{pivot}"] if setup == "C" else pool[f"A_{pivot}"] | pool[f"C_{pivot}"]
    m &= sig
    if gap:
        m &= pool.gapdir
    return m


def pick(pool: pd.DataFrame, setup: str, univ: str, pivot: str, k: int, gap: bool, per_day: int | None = None,
         lots_col: str = "lots_t") -> pd.DataFrame:
    """Signals of one combination that can be bought (>= 1 lot at the nominal stop), max per_day a day, highest Rs
    turnover first (known at the close). One signal per contract per day, so no re-entry on the same level."""
    per_day = per_day or CFG["max_per_day"]
    m = signal_mask(pool, setup, univ, pivot, k, gap) & (pool[lots_col] > 0)
    s = pool[m].sort_values(["date", "turnover"], ascending=[True, False])
    return s.groupby("date").head(per_day)


# ---------------------------------------------------------------- numbers
def stats(net: pd.Series, dates: pd.Series, capital: float, prem: pd.Series | None = None) -> dict:
    """Metrics of a list of trades (fixed capital, no compounding). NaN nets (0 lots for that exit) are left out."""
    ok = net.notna()
    net, dates = net[ok], dates[ok]
    if net.empty:
        return {"trades": 0, "net": 0.0, "win_pct": None, "pf": None, "max_dd": 0.0, "avg": None, "avg_win": None, "avg_loss": None, "ret_pct": 0.0, "avg_ret": None}
    cum = net.groupby(dates.values).sum().sort_index().cumsum()
    w, l = net[net > 0], net[net <= 0]
    out = {"trades": int(len(net)), "net": float(net.sum()), "ret_pct": float(net.sum() / capital * 100), "win_pct": float((net > 0).mean() * 100),
           "avg": float(net.mean()), "avg_win": float(w.mean()) if len(w) else None, "avg_loss": float(l.mean()) if len(l) else None,
           "pf": float(w.sum() / -l.sum()) if l.sum() < 0 else None, "max_dd": float((cum - cum.cummax().clip(lower=0)).min())}
    out["avg_ret"] = float((net / prem[ok]).mean() * 100) if prem is not None else None
    return out


def premium(t: pd.DataFrame, fam: str, slip: float) -> pd.Series:
    return t["lots_" + fam] * t.lot * t.close * (1 + slip)


def split_day(pool: pd.DataFrame, frac: float) -> str:
    days = sorted(pool.date.unique())
    return days[int(len(days) * frac)]


def random_check(pool: pd.DataFrame, t: pd.DataFrame, setup: str, univ: str, k: int, rng: np.random.Generator, n: int) -> dict:
    """Random entries: n simulations, each buys as many random contract-days as the strategy (same CE / PE mix) from the
    same liquid pool (same universe, strike class, expiry rule, filters, sizing, exits) - any day, no R3 / S3 condition.
    Returns per exit the share of simulations whose average net per trade is BELOW the strategy's (100 = beats every one)."""
    base = (pool.k == k) & (pool.lots_t > 0)
    if univ != "all":
        base &= pool.is_index == (univ == "index")
    out = {}
    draws = []
    for typ in ("CE", "PE"):
        need = int((t.type == typ).sum())
        if not need:
            continue
        p = pool[base & (pool.type == typ)]
        if len(p) < need:
            return {}
        idx = rng.random((n, len(p))).argsort(axis=1)[:, :need] if len(p) <= 5000 else rng.integers(0, len(p), (n, need))
        draws.append((p, idx))
    for key, *_ in EXITS:
        strat = t[key + "_net"].mean()
        if not draws or np.isnan(strat):
            continue
        tot = np.zeros(n)
        cnt = np.zeros(n)
        for p, idx in draws:
            v = p[key + "_net"].to_numpy()[idx]
            tot += np.nansum(v, axis=1)
            cnt += np.sum(~np.isnan(v), axis=1)
        avg = tot / np.maximum(cnt, 1)
        out[key] = float((avg < strat).mean() * 100)
    return out


# ---------------------------------------------------------------- the whole grid
COMBO = ["setup", "univ", "pivot", "k", "gap"]


def combos(cfg: dict = CFG):
    for setup in cfg["setups"]:
        for univ in cfg["universes"]:
            for pivot in cfg["pivots"]:
                for k in cfg["strikes"]:
                    for gap in (False, True):
                        yield setup, univ, pivot, k, gap


def grid(pool: pd.DataFrame, cfg: dict = CFG, progress=None) -> tuple[pd.DataFrame, dict]:
    """One row per combination x exit: raw signals, trades, full / in-sample / out-of-sample metrics and the random-entry
    percentile. Also returns the picked trades of every combination ({combo tuple: DataFrame})."""
    cut = split_day(pool, cfg["is_frac"])
    months = pool.date.str[:7].nunique()
    rng = np.random.default_rng(cfg["seed"])
    rows, picks = [], {}
    all_combos = list(combos(cfg))
    for i, c in enumerate(all_combos):
        setup, univ, pivot, k, gap = c
        raw = int(signal_mask(pool, *c).sum())
        t = pick(pool, *c)
        picks[c] = t
        rnd = random_check(pool, t, setup, univ, k, rng, cfg["n_random"]) if len(t) >= 10 else {}
        is_ = t.date < cut
        for key, label, fam, *_ in EXITS:
            prem = premium(t, fam, cfg["slip"])
            f, a, b = (stats(t[key + "_net"][m], t.date[m], cfg["capital"], prem[m]) for m in (slice(None), is_, ~is_))
            rows.append({**dict(zip(COMBO, c)), "exit": key, "exit_label": label, "signals": raw, "sig_per_month": raw / months,
                         **{f"{p}_{m}": v for p, s in (("all", f), ("is", a), ("oos", b)) for m, v in s.items()},
                         "rand_pct": rnd.get(key)})
        if progress and (i + 1) % 12 == 0:
            progress(f"grid {i + 1}/{len(all_combos)}")
    return pd.DataFrame(rows), picks


def choose_in_sample(g: pd.DataFrame, cfg: dict = CFG) -> pd.DataFrame:
    """Per setup x universe: the combination (pivot, strike, gap, exit) with the best IN-SAMPLE net (>= min_is_trades
    trades there), and how it then did out-of-sample. Nothing from the out-of-sample part is used to choose."""
    ok = g[g.is_trades >= cfg["min_is_trades"]]
    best = ok.loc[ok.groupby(["setup", "univ"]).is_net.idxmax()]
    return best.reset_index(drop=True)


def robustness(g: pd.DataFrame, cfg: dict = CFG) -> dict:
    """How much the good in-sample results carry over: share of combinations profitable in each half, share of the
    in-sample-profitable ones that stay profitable, and the rank correlation of in-sample vs out-of-sample average P&L."""
    ok = g[(g.is_trades >= cfg["min_is_trades"]) & (g.oos_trades >= 10)]
    pos_is = ok.is_net > 0
    return {"combos": int(len(ok)), "is_pos_pct": float(pos_is.mean() * 100), "oos_pos_pct": float((ok.oos_net > 0).mean() * 100),
            "stay_pos_pct": float((ok.oos_net[pos_is] > 0).mean() * 100) if pos_is.any() else None,
            "rank_corr": float(ok.is_avg.rank().corr(ok.oos_avg.rank())) if len(ok) > 2 else None,
            "beat_random_pct": float((ok.rand_pct >= 95).mean() * 100), "all_pf_gt1_pct": float((ok.all_pf > 1).mean() * 100)}


def sensitivity(pool: pd.DataFrame, chosen: list, cfg: dict = CFG) -> list[list]:
    """Each chosen (combination, exit) at other risk % and slippage, one thing changed at a time.
    Rows: [combination, exit, what changed, trades, win %, net, pf, max dd, return %]."""
    variants = ([[f"Risk {r:g}% (slippage {cfg['slip'] * 100:g}%)", r, cfg["slip"]] for r in cfg["risk_grid"]]
                + [[f"Slippage {s * 100:g}% (risk {cfg['risk_pct']:g}%)", cfg["risk_pct"], s] for s in cfg["slip_grid"] if s != cfg["slip"]])
    out = []
    for label, risk, slip in variants:
        p = add_exits(pool, cfg, risk, slip)
        for c, exit_key in chosen:
            t = pick(p, *c)
            fam = next(e[2] for e in EXITS if e[0] == exit_key)
            s = stats(t[exit_key + "_net"], t.date, cfg["capital"], premium(t, fam, slip))
            out.append([c, exit_key, label, s["trades"], s["win_pct"], s["net"], s["pf"], s["max_dd"], s["ret_pct"]])
    return out


# ---------------------------------------------------------------- outputs
def trade_log(t: pd.DataFrame, setup: str, pivot: str, exit_key: str, cfg: dict = CFG) -> pd.DataFrame:
    """Trade-log rows for one combination and exit: instrument, setup, pivot, level, signal time, entry, SL, target, exit,
    reason, lots, gross, costs, net, max adverse excursion (worst price seen while holding, before slippage)."""
    _, _, fam, _, rr, _ = next(e for e in EXITS if e[0] == exit_key)
    t = t[t[exit_key + "_net"].notna()]
    e = t.close.to_numpy()
    stop = stops(t, cfg)[fam] if rr is not None else np.full(len(t), np.nan)
    tgt = e + rr * (e - stop) if rr is not None else np.full(len(t), np.nan)
    px, why = t[exit_key + "_px"].to_numpy(), t[exit_key + "_why"].to_numpy()
    lots = t["lots_" + fam].to_numpy()
    qty = lots * t.lot.to_numpy()
    bv, sv = qty * e * (1 + cfg["slip"]), qty * px * (1 - cfg["slip"])
    c = costs(bv, sv, t.n_date.to_numpy(), cfg)
    worst = np.where(np.isin(why, [0, 2, 3]), t.n_open, np.where(why == 4, stop, t.n_low))
    on_option = np.full(len(t), setup == "A") | ((setup == "AC") & t[f"A_{pivot}"].to_numpy())     # A+C: A signal named first
    level = np.where(on_option, t[f"r3_{pivot}"], np.where(t.type == "CE", t[f"ur3_{pivot}"], t[f"us3_{pivot}"]))
    return pd.DataFrame({
        "date": t.date.values, "instrument": t.symbol + " " + t.strike.map("{:g}".format) + " " + t.type + " " + t.expiry,
        "setup": np.where(on_option, "A", "C"), "pivot": pivot,
        "level_on": np.where(on_option, "option R3", np.where(t.type == "CE", "underlying R3", "underlying S3")),
        "level": np.round(level, 2), "signal_time": "15:30 (din ka aakhri candle close)", "entry": e, "sl": np.round(stop, 2), "target": np.round(tgt, 2),
        "exit_date": t.n_date.values, "exit": np.round(px, 2), "exit_reason": [WHY[w] for w in why], "lots": lots, "qty": qty,
        "gross_pnl": np.round(sv - bv, 2), "costs": np.round(c["total"], 2), "net_pnl": np.round(t[exit_key + "_net"].to_numpy(), 2),
        "mae_rs": np.round(np.minimum(0, worst - e) * qty, 2), "mae_pct": np.round(np.minimum(0, worst / e - 1) * 100, 2),
        "dte": t.dte.values, "vix": t.vix.values, "gap_pct": np.round(t.gap_pct.to_numpy(), 2)})


def unconditional(pool: pd.DataFrame) -> list[list]:
    """'Buy anything': every liquid ATM contract-day, no signal - average raw premium change and net per trade."""
    p = pool[(pool.k == 0) & (pool.lots_t > 0)]
    out = []
    for (ix, typ), g in p.groupby(["is_index", "type"]):
        out.append(["Index" if ix else "Stocks", typ, int(len(g)), float((g.n_open / g.close - 1).mean() * 100), float(g.open_net.mean()),
                    float((g.open_net > 0).mean() * 100), float((g.n_close / g.close - 1).mean() * 100), float(g.close_net.mean())])
    return out


def open_fill_check() -> dict:
    """Real 1-minute option candles (only the contracts Fyers still had): how the price 1-5 min and 15 min after 9:15
    compares with the 9:15 open, i.e. whether selling 'at the open' is a fair assumption."""
    import re
    pat = re.compile(r"^(NIFTY|BANKNIFTY|[A-Z&-]+?)(\d\d[A-Z0-9]{3})(\d+(?:\.\d+)?)(CE|PE)$")
    rows = []
    for f in (FNO / "1m" / "options").glob("*.csv"):
        m = pat.match(f.stem)
        if not m:
            continue
        d = pd.read_csv(f, usecols=["datetime", "open", "close"])
        if d.empty:
            continue
        d["day"], d["hm"] = d.datetime.str[:10], d.datetime.str[11:16]
        o = d[d.hm == "09:15"].set_index("day").open
        w = d[(d.hm >= "09:16") & (d.hm <= "09:20")].groupby("day").close.mean()
        p30 = d[(d.hm >= "09:29") & (d.hm <= "09:31")].groupby("day").close.mean()
        x = pd.DataFrame({"o": o, "w": w, "p30": p30}).dropna()
        if len(x):
            rows.append(x.assign(grp="Index" if m[1] in INDEX else "Stocks"))
    x = pd.concat(rows).reset_index()
    out = {"days": [x["day"].min(), x["day"].max(), int(x["day"].nunique())], "rows": []}
    for grp, g in x.groupby("grp"):
        r5, r30 = (g.w / g.o - 1) * 100, (g.p30 / g.o - 1) * 100
        out["rows"].append([grp, int(len(g)), float(r5.mean()), float(r5.median()), float(r5.quantile(.25)), float(r5.quantile(.75)),
                            float(r30.mean()), float(r30.median())])
    return out
