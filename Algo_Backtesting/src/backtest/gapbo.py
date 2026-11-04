"""Gap breakout (long) on NSE stocks with daily candles: a gap-up open above the highest high of the last 60 days, close above
the 21 EMA, smoothed RSI(10) between 60 and the overbought line, and the stock's monthly close above its 20-month EMA.
Entry at the next open; exit on the first close below the 21 EMA or back inside the gap (below the gap-fill level).

    D = load()                          # Fyers daily candles 2010+ (split / bonus adjusted), Nifty 50 / 200, AMFI market caps
    I = indicators(D, p)                # EMA 21, RSI 10 + SMA, n-day high, 20-month EMA trend, top-200 universe, Nifty 200-DMA regime
    S = signals(I, p)                   # every gap day that passes the setup, with the planned entry (or why there is none)
    X = paths(I, S, p)                  # per-share exit of every planned entry (no sizing yet)
    t, m = portfolio(D, I, X, p)        # sizing (1% risk, 20% cap, max positions, cash), costs -> trades in Rs + daily mark-to-market
    stats(t, m, D["cal"], p.capital, start, end)

Timing (no look-ahead): every decision uses data up to the close of day t; orders fill on day t+1 from its OHLC. Close-based
exits (21 EMA, gap fill, monthly trend, time stop) are known at a close and fill at the next open (an open below the stop is the
fill). Intraday orders (R targets, intraday gap-fill stop): when the stop and the target are both inside one daily bar the stop
is taken first; an open beyond the stop fills at the open.
"""
from dataclasses import dataclass, replace

import numpy as np
import pandas as pd

from backtest.config import DATA, UNIVERSE_CSV
from backtest.vcp import costs

DAILY = DATA / "daily"
CORP_CSV = DATA / "reference" / "corporate_actions.csv"
START = "2010-01-01"
# Demergers that Fyers' daily history does not adjust for (a one-day fake fall). NSE's corporate actions (Oct 2021 on) are read from
# CORP_CSV; older ones were found by the one-day-jump health check and are listed by hand here.
CA_MANUAL = [("STAR", "2013-12-19"),               # Strides Arcolab Rs 500 special dividend: 882 -> 351
             ("ADANIENT", "2015-06-03"),           # Adani Transmission demerger: 617 -> 106
             ("NIACL", "2018-06-27"),              # New India Assurance 1:1 bonus not adjusted: 652 -> 325
             ("TATACOMM", "2019-09-17"),           # Hemisphere Properties demerger: 427 -> 265
             ("TATACHEM", "2020-03-04")]           # consumer business demerger (to Tata Consumer): 724 -> 315
CA_BLOCK = 60                                     # no new setup from the day before an ex-date until 60 trading days after it
EXITS = {"ema": "21 EMA trail (sirf stop, koi target nahi)", "rr2": "Target 2R", "rr3": "Target 3R", "rr5": "Target 5R",
         "monthly": "Monthly trend toote tab (21 EMA stop nahi)", "t20": "Time stop 20 din", "t40": "Time stop 40 din", "t60": "Time stop 60 din"}


@dataclass(frozen=True)
class Params:
    """Every number of the test in one place (the run script changes them with with_params)."""
    # money
    capital: float = 1_000_000.0
    compound: bool = False                 # False: size on the starting capital; True: on capital + booked P&L
    risk: float = 0.01                     # share of capital risked per trade (entry fill - stop level)
    max_pos_pct: float = 0.20              # one position at most 20% of capital
    max_open: int = 8                      # concurrent positions
    min_fill: float = 0.5                  # skip when the free cash buys less than half of the wanted quantity
    adv_pct: float = 0.05                  # quantity at most 5% of the 50-day average volume
    # universe
    top_n: int = 200                       # Nifty 200 proxy: top N by approximate market cap (120-day average, fixed each month-end)
    # setup (gap day t)
    gap: float = 0.02                      # open >= previous close x (1 + gap) and open > previous high
    max_gap: float = 0.30                  # bigger opening gaps are corporate-action / data events, not trades
    hh_n: int = 60                         # open above the highest high of the previous hh_n days
    ema_n: int = 21
    rsi_n: int = 10
    rsi_sma: int = 3                       # smoothing of RSI(10) with a simple average
    rsi_lo: float = 60.0                   # smoothed RSI above this ...
    rsi_ob: float = 80.0                   # ... and below the overbought line
    month_ema: int = 20
    monthly: str = "close"                 # "close": last completed monthly close > its 20-month EMA; "live": today's close > last month's EMA; "off"
    fill: str = "high"                     # gap-fill level: previous day's "high" or "close"
    # entry / exit
    entry: str = "open"                    # "open" = next day's open; "close" = the gap day's close
    exit: str = "ema"                      # key of EXITS
    fill_stop: str = "close"               # "close": a close below the gap-fill level -> next open; "intraday": stop order at the level
    # costs
    slip: float = 0.0015                   # slippage per fill (0.3% round trip)


def with_params(p: Params, **kw) -> Params:
    return replace(p, **kw)


def exit_kind(key: str) -> tuple[str, float]:
    """"ema" -> ("ema", 0), "rr3" -> ("rr", 3), "monthly" -> ("monthly", 0), "t40" -> ("time", 40)."""
    if key.startswith("rr"):
        return "rr", float(key[2:])
    if key.startswith("t") and key[1:].isdigit():
        return "time", float(key[1:])
    return key, 0.0


# ---------------------------------------------------------------- data
def load(start: str = START, end: str | None = None, path=None) -> dict:
    """Wide daily frames (date x symbol) from data/daily/equity_daily.parquet, Nifty 50 / 200 closes and the AMFI market caps."""
    df = pd.read_parquet(path or DAILY / "equity_daily.parquet")
    df = df[(df.date >= start) & (df.date <= (end or "9999-12-31"))]
    W = {k: df.pivot(index="date", columns="symbol", values=k).astype(float) for k in ("open", "high", "low", "close", "volume")}
    n = W["close"].notna().sum(axis=1)
    odd = n < 0.2 * n.rolling(21, center=True, min_periods=1).median()          # e.g. Sat 13 Aug 2022: one stock, a stray candle
    W = {k: v[~odd] for k, v in W.items()}
    bad = clean(W)
    idx = {n: pd.read_csv(DAILY / "index" / f"{n}.csv", dtype={"date": str}).set_index("date").close for n in ("NIFTY50", "NIFTY200")}
    u = pd.read_csv(UNIVERSE_CSV)
    u["sym"] = u.symbol.str.replace(r"^NSE:|-(EQ|BE|BZ|SM|ST)$", "", regex=True)
    D = {"W": W, "cal": [str(d) for d in W["close"].index], "syms": [str(s) for s in W["close"].columns], "idx": idx,
         "mcap": dict(zip(u.sym, u.mcap_cr)), "cat": dict(zip(u.sym, u.category)), "bad_rows": bad, "odd_days": [str(d) for d in n.index[odd]]}
    D["shares"] = shares(D)
    D["ca"], D["ca_list"] = corp_events(D)
    return D


def corp_events(D: dict) -> tuple[np.ndarray, list]:
    """(T, N) True on demerger / scheme-of-arrangement ex-dates (NSE list + CA_MANUAL) of the stocks in the data."""
    ev = list(CA_MANUAL)
    if CORP_CSV.exists():
        c = pd.read_csv(CORP_CSV, usecols=["symbol", "ex_date", "purpose"])
        c = c[c.purpose.str.contains("demerger|arrangement", case=False, na=False)]
        ev += list(zip(c.symbol, c.ex_date))
    cal, col = D["cal"], {s: j for j, s in enumerate(D["syms"])}
    ca, used = np.zeros((len(cal), len(col)), dtype=bool), []
    for sym, day in ev:
        i = int(np.searchsorted(cal, str(day)))
        if sym in col and i < len(cal):
            ca[i, col[sym]] = True
            used.append((sym, cal[i]))
    return ca, sorted(set(used), key=lambda x: x[1])


def shares(D: dict) -> np.ndarray:
    """Share count proxy per stock (Rs crore of market cap per Rs of adjusted price): AMFI market cap (Jan-Jun 2026 average)
    / average adjusted close Jan-Jun 2026. Splits / bonuses are inside the adjusted prices; new issues / buybacks are not seen."""
    C, cal = D["W"]["close"], D["cal"]
    mc = np.array([D["mcap"].get(s, np.nan) for s in D["syms"]])
    a, b = np.searchsorted(cal, "2026-01-01"), np.searchsorted(cal, "2026-07-01")
    anchor = C.iloc[a:b].mean().to_numpy() if b > a else np.full(len(mc), np.nan)
    anchor = np.where(np.isnan(anchor), C.ffill().iloc[-1].to_numpy(), anchor)
    return mc / anchor


def clean(W: dict) -> int:
    """Impossible candles (a price <= 0, high < low, open or close outside high-low) become NaN (= no trade that day)."""
    O, H, L, C = (W[k] for k in ("open", "high", "low", "close"))
    bad = (O <= 0) | (L <= 0) | (H < L) | (O > H * 1.0001) | (O < L * 0.9999) | (C > H * 1.0001) | (C < L * 0.9999)
    for k in W:
        W[k] = W[k].mask(bad)
    return int(bad.to_numpy().sum())


def ema_tv(X: np.ndarray, n: int, alpha: float | None = None) -> np.ndarray:
    """EMA the way TradingView draws it, column by column on a (T, N) array: the first value is the simple average of the
    first n values, then a x value + (1 - a) x previous with a = 2 / (n + 1) (alpha = 1 / n gives Wilder's RMA).
    NaN rows (no trade that day) are skipped and stay NaN."""
    a = 2 / (n + 1) if alpha is None else alpha
    T, N = X.shape
    out = np.full((T, N), np.nan)
    cnt, acc, prev = np.zeros(N, dtype=np.int64), np.zeros(N), np.full(N, np.nan)
    for t in range(T):
        x = X[t]
        ok = ~np.isnan(x)
        cnt[ok] += 1
        warm = ok & (cnt <= n)
        acc[warm] += x[warm]
        init = ok & (cnt == n)
        prev[init] = acc[init] / n
        upd = ok & (cnt > n)
        prev[upd] = a * x[upd] + (1 - a) * prev[upd]
        done = ok & (cnt >= n)
        out[t, done] = prev[done]
    return out


def rsi_tv(C: np.ndarray, n: int) -> np.ndarray:
    """Wilder RSI on the close (change from the last traded close), TradingView rules: 100 if no down move, 0 if no up move."""
    prev = np.vstack([np.full((1, C.shape[1]), np.nan), pd.DataFrame(C).ffill().to_numpy()[:-1]])
    d = C - prev
    up, dn = np.where(d > 0, d, 0.0), np.where(d < 0, -d, 0.0)
    up[np.isnan(d)] = dn[np.isnan(d)] = np.nan
    ru, rd = ema_tv(up, n, 1 / n), ema_tv(dn, n, 1 / n)
    with np.errstate(invalid="ignore", divide="ignore"):
        r = np.where(rd == 0, 100.0, np.where(ru == 0, 0.0, 100 - 100 / (1 + ru / rd)))
    r[np.isnan(ru) | np.isnan(rd)] = np.nan
    return r


def _prev(X: np.ndarray) -> np.ndarray:
    """Last traded value before day t (a suspension day does not break 'previous close / high')."""
    return np.vstack([np.full((1, X.shape[1]), np.nan), pd.DataFrame(X).ffill().to_numpy()[:-1]])


def _on_cal(s: pd.Series, cal: list[str]) -> np.ndarray:
    return s.reindex(sorted(set(s.index) | set(cal))).ffill().reindex(cal).to_numpy()


def indicators(D: dict, p: Params) -> dict:
    W, cal = D["W"], D["cal"]
    O, H, L, C, V = (W[k].to_numpy() for k in ("open", "high", "low", "close", "volume"))
    T, N = C.shape
    Cf = pd.DataFrame(C).ffill().to_numpy()
    I = {"O": O, "H": H, "L": L, "C": C, "V": V, "Cf": Cf, "Cp": _prev(C), "Hp": _prev(H)}
    I["ca"] = D["ca"] if "ca" in D else np.zeros((T, N), dtype=bool)
    cs = np.vstack([np.zeros((1, N)), np.cumsum(I["ca"], axis=0)])       # cs[t] = ex-dates before row t
    lo, hi = np.clip(np.arange(T) - CA_BLOCK, 0, T), np.clip(np.arange(T) + 2, 0, T)
    I["ca_block"] = (cs[hi] - cs[lo]) > 0                                 # an ex-date in rows t - 60 .. t + 1
    I["ema"] = ema_tv(C, p.ema_n)
    I["rsi"] = rsi_tv(C, p.rsi_n)
    I["rsi_s"] = pd.DataFrame(I["rsi"]).rolling(p.rsi_sma, min_periods=p.rsi_sma).mean().to_numpy()
    I["hh"] = pd.DataFrame(H).shift(1).rolling(p.hh_n, min_periods=p.hh_n).max().to_numpy()   # previous hh_n days, day t excluded
    I["adv"] = pd.DataFrame(V).shift(1).rolling(50, min_periods=20).mean().to_numpy()

    # monthly candles: close of the last trading day of each month; the data's final month counts as not finished
    mon = np.array([d[:7] for d in cal])
    _, mid = np.unique(mon, return_inverse=True)
    ends = np.flatnonzero(np.r_[mid[1:] != mid[:-1], True])
    last = np.zeros(T, dtype=bool)
    last[ends[:-1]] = True
    MC = Cf[ends]
    ME = ema_tv(MC, p.month_ema)
    with np.errstate(invalid="ignore"):
        m_up = MC > ME
    m_eff = np.where(last, mid, mid - 1)                     # newest month whose close is known at day t's close
    ok_m = (m_eff >= 0)[:, None]
    if p.monthly == "close":
        I["mstate"] = ok_m & m_up[np.maximum(m_eff, 0)]
        I["mdist"] = np.where(ok_m, MC[np.maximum(m_eff, 0)] / ME[np.maximum(m_eff, 0)] - 1, np.nan)
    elif p.monthly == "live":                                # today's close vs the EMA of the last finished month
        ref = ME[np.maximum(mid - 1, 0)]
        with np.errstate(invalid="ignore"):
            I["mstate"] = ((mid >= 1)[:, None]) & (C > ref)
        I["mdist"] = np.where((mid >= 1)[:, None], C / ref - 1, np.nan)
    else:
        I["mstate"], I["mdist"] = np.ones((T, N), dtype=bool), np.full((T, N), np.nan)
    I["mid"], I["MC"], I["ME"] = mid, MC, ME

    # universe: market cap ~ close x share count proxy (see shares()). Rank on the 120-day average at each month-end;
    # the rank holds for the whole next month.
    I["mcap"] = Cf * D["shares"]
    avg = pd.DataFrame(I["mcap"]).rolling(120, min_periods=60).mean().to_numpy()
    rk = pd.DataFrame(avg[ends]).rank(axis=1, ascending=False, method="first").to_numpy()
    rank = np.full((T, N), np.nan)
    rank[mid >= 1] = rk[mid[mid >= 1] - 1]
    I["rank"] = rank
    with np.errstate(invalid="ignore"):
        I["uni"] = rank <= p.top_n

    # market regime: Nifty 50 close vs its 200-day simple average (on the index's own days)
    n50 = D["idx"]["NIFTY50"]
    sma = n50.rolling(200).mean()
    I["bull"] = _on_cal((n50 > sma).astype(float).where(sma.notna()), cal)
    I["n50"] = _on_cal(n50, cal)
    I["n200"] = _on_cal(D["idx"]["NIFTY200"], cal) if "NIFTY200" in D["idx"] else np.full(T, np.nan)
    return I


# ---------------------------------------------------------------- setup + planned entry
def signals(I: dict, p: Params) -> pd.DataFrame:
    """Gap days (t) that pass every setup rule, one row per (day, stock), with the planned entry (row k, price e) and the stop
    level s0 = max(gap-fill level, 21 EMA) (just the gap-fill level when the 21 EMA is not an exit), or why there is no entry."""
    O, H, L, C, Cp, Hp = (I[k] for k in ("O", "H", "L", "C", "Cp", "Hp"))
    T = C.shape[0]
    F = Hp if p.fill == "high" else Cp
    with np.errstate(invalid="ignore", divide="ignore"):
        gap = O / Cp - 1
        ok = (I["uni"] & (O > Hp) & (gap >= p.gap) & (gap <= p.max_gap) & (O > I["hh"]) & (C > I["ema"])
              & (I["rsi_s"] > p.rsi_lo) & (I["rsi_s"] < p.rsi_ob) & I["mstate"] & (C > F) & ~I["ca_block"])
    rows = []
    for i, j in zip(*np.nonzero(ok)):
        f, ema = F[i, j], I["ema"][i, j]
        s0 = f if p.exit == "monthly" else max(f, ema)
        r = {"i": i, "j": j, "gap_pct": gap[i, j] * 100, "fill_lvl": f, "s0": s0, "outcome": "entry", "why": ""}
        if p.entry == "open":
            k = i + 1
            while k < T and O[k, j] != O[k, j]:
                k += 1
            if k >= T:
                r.update(outcome="none", why="data khatam (agla din nahi)")
            else:
                r.update(k=k, e=O[k, j])
                if H[k, j] == L[k, j]:
                    r.update(outcome="none", why="agle din circuit lock (ek hi price), fill nahi")
                elif O[k, j] <= s0:
                    r.update(outcome="none", why="agle din open stop level ke neeche / barabar")
        else:
            r.update(k=i, e=C[i, j])
        rows.append(r)
    S = pd.DataFrame(rows, columns=["i", "j", "gap_pct", "fill_lvl", "s0", "outcome", "why", "k", "e"])
    if S.empty:
        return S
    ii, jj = S.i.to_numpy(), S.j.to_numpy()
    for name, arr in (("rsi", I["rsi"]), ("rsi_s", I["rsi_s"]), ("ema", I["ema"]), ("hh", I["hh"]), ("mdist", I["mdist"]), ("rank", I["rank"])):
        S[name] = arr[ii, jj]
    S["dist_ema"] = (C[ii, jj] / S.ema - 1) * 100
    S["bull"] = I["bull"][ii]
    return S.sort_values(["i", "j"]).reset_index(drop=True)


# ---------------------------------------------------------------- exit (per share)
def _walk(I: dict, j: int, k: int, e: float, f: float, s0: float, p: Params) -> tuple:
    """From the fill on row k: (exit row, exit price, reason). Close-based exits fill at the next open; an exit on a
    locked limit-down day (one price all day, below the last close) waits for the next day. A demerger ex-date while
    holding: sold at the close before it (the data's fall on that day is not a real loss)."""
    O, H, L, C, E, MS = (I[x][:, j] for x in ("O", "H", "L", "C", "ema", "mstate"))
    CA = I["ca"][:, j] if "ca" in I else np.zeros(len(C), dtype=bool)
    T = len(C)
    kind, val = exit_kind(p.exit)
    tgt = e + val * (e - s0) if kind == "rr" else None
    pend, n_in, last_c, last_d, d = None, 0, np.nan, k, k
    while d < T:
        o, h, l, c = O[d], H[d], L[d], C[d]
        if c != c:
            d += 1
            continue
        if d > k and CA[d]:
            return last_d, last_c, "corporate action (demerger) se pehle, pichhle close pe becha"
        if d > k and pend:
            if h == l and o < last_c:
                last_c, last_d = c, d
                d += 1
                continue
            return d, o, pend
        if d > k or p.entry == "open":                       # the position is held on this bar (after the fill)
            if p.fill_stop == "intraday":                    # stop first: before any target on the same bar
                if d > k and o <= f:
                    return d, o, "gap fill stop (open stop ke neeche, open pe)"
                if l <= f:
                    return d, f, "gap fill stop (din me level chhua)"
            if tgt is not None and h >= tgt:
                return d, (max(o, tgt) if d > k else tgt), f"target {val:g}R"
            n_in += 1
            if c < f:
                pend = "gap bhar gaya (close gap-fill level ke neeche)"
            elif kind != "monthly" and c < E[d]:
                pend = "close 21 EMA ke neeche"
            elif kind == "monthly" and not MS[d]:
                pend = "monthly trend toota (mahina 20-month EMA ke neeche band)"
            elif kind == "time" and n_in >= val:
                pend = f"time stop ({val:g} din)"
        last_c, last_d = c, d
        d += 1
    x = T - 1
    while C[x] != C[x]:
        x -= 1
    return x, C[x], "data khatam (trade khula, aakhri close pe)"


def paths(I: dict, S: pd.DataFrame, p: Params) -> pd.DataFrame:
    s = S[S.outcome == "entry"].copy() if len(S) else S
    if s.empty:
        return s
    s["k"] = s.k.astype(int)
    out = [_walk(I, int(r.j), int(r.k), float(r.e), float(r.fill_lvl), float(r.s0), p) for r in s.itertuples()]
    s["x"], s["px"], s["reason"] = [o[0] for o in out], [o[1] for o in out], [o[2] for o in out]
    return s.reset_index(drop=True)


# ---------------------------------------------------------------- portfolio
def portfolio(D: dict, I: dict, X: pd.DataFrame, p: Params) -> tuple[pd.DataFrame, dict]:
    """Takes the entries in date order (bigger gap first on the same day) while there is room: max_open positions, one
    position per stock, cash (no leverage), 20% of capital per stock, 5% of the average volume. qty = capital x risk /
    (entry fill - stop level). Returns trades in Rs (plus the skipped entries) and the daily mark-to-market."""
    cal, syms, T = D["cal"], D["syms"], len(D["cal"])
    skipped = {}
    if X is None or X.empty:
        return pd.DataFrame(), {**_mtm([], T, p, I), "skipped": skipped}
    X = X.sort_values(["k", "gap_pct"], ascending=[True, False]).reset_index(drop=True)
    live, booked, trades = [], 0.0, []

    def skip(why):
        skipped[why] = skipped.get(why, 0) + 1

    for r in X.itertuples():
        k = r.k
        booked += sum(q["net"] for q in live if q["x"] < k)
        live = [q for q in live if q["x"] >= k]              # an exit at day k's open frees its cash only from day k + 1
        if any(q["j"] == r.j for q in live):
            skip("stock pehle se portfolio me")
            continue
        if len(live) >= p.max_open:
            skip(f"{p.max_open} position pehle se khuli")
            continue
        eq = p.capital + (booked if p.compound else 0.0)
        cash = eq - sum(q["cost"] for q in live)
        ef = r.e * (1 + p.slip)
        rps = ef - r.s0
        if rps <= 0:
            skip("slippage ke baad entry stop ke neeche")
            continue
        adv = I["adv"][k, r.j]
        want = min(eq * p.risk / rps, eq * p.max_pos_pct / ef, p.adv_pct * adv if adv == adv else 0.0)
        qty = int(np.floor(min(want, max(cash, 0.0) / ef)))
        if qty < 1 or qty < p.min_fill * want:
            skip("paisa kam (cash / volume limit)")
            continue
        xf = r.px * (1 - p.slip)
        c = costs(qty * ef, [qty * xf])
        gross = qty * (xf - ef)
        net = gross - c["costs"]
        risk_rs = qty * rps
        i = int(r.i)
        t = {"symbol": syms[r.j], "signal_date": cal[i], "entry_date": cal[k], "exit_date": cal[r.x], "i": i, "j": int(r.j), "k": k, "x": int(r.x),
             "gap_pct": r.gap_pct, "rsi": r.rsi, "rsi_s": r.rsi_s, "ema": r.ema, "dist_ema": r.dist_ema, "hh": r.hh, "mdist": r.mdist * 100 if r.mdist == r.mdist else None,
             "rank": r.rank, "bull": None if r.bull != r.bull else ("upar" if r.bull else "neeche"),
             "prev_high": I["Hp"][i, r.j], "prev_close": I["Cp"][i, r.j], "gap_open": I["O"][i, r.j], "gap_close": I["C"][i, r.j],
             "fill_lvl": r.fill_lvl, "entry": r.e, "entry_fill": ef, "stop": r.s0, "stop_pct": (r.e - r.s0) / r.e * 100,
             "qty": qty, "notional": qty * ef, "risk_rs": risk_rs, "risk_pct": risk_rs / p.capital * 100, "exit": r.px, "exit_fill": xf, "reason": r.reason,
             "hold": int(r.x) - k, "beyond_rs": qty * max(0.0, r.s0 - r.px), "gross": gross, "slip": qty * (r.e + r.px) * p.slip, **c, "net": net}
        t["R"] = net / risk_rs
        t["R_cap"] = net / (p.capital * p.risk)               # P&L in units of the planned 1% risk
        trades.append(t)
        live.append({"j": r.j, "x": int(r.x), "net": net, "cost": qty * ef})
    m = _mtm(trades, T, p, I)
    m["skipped"] = skipped
    return pd.DataFrame(trades), m


def _mtm(trades: list, T: int, p: Params, I: dict) -> dict:
    """Daily mark-to-market P&L (closed trades at net, open ones at close - entry fill) and money deployed (cost basis)."""
    pnl, dep, n = np.zeros(T), np.zeros(T), np.zeros(T)
    for t in trades:
        k, x, j, ef, q = t["k"], t["x"], t["j"], t["entry_fill"], t["qty"]
        if x > k:
            pnl[k:x] += q * (I["Cf"][k:x, j] - ef)
            dep[k:x] += q * ef
            n[k:x] += 1
        pnl[x:] += t["net"]
    return {"pnl": pnl, "dep": dep, "n_open": n}


# ---------------------------------------------------------------- numbers
def stats(t: pd.DataFrame, m: dict, cal: list[str], capital: float, start: str, end: str) -> dict:
    """Trade metrics + mark-to-market curve metrics for trades entered in [start, end]."""
    a, b = int(np.searchsorted(cal, start)), int(np.searchsorted(cal, end, side="right")) - 1
    yrs = (pd.Timestamp(cal[b]) - pd.Timestamp(cal[a])).days / 365.25
    out = {"trades": 0, "net": 0.0, "ret_pct": 0.0, "cagr": 0.0, "avg_year_pct": 0.0, "max_dd": 0.0, "max_dd_pct": 0.0, "win_pct": None, "avg_win_r": None,
           "avg_loss_r": None, "exp_r": None, "med_r": None, "pf": None, "avg_hold": None, "exposure": 0.0, "in_mkt": 0.0, "top5_share": None, "top5_net": None,
           "rest_net": None, "worst": 0.0, "best": 0.0, "gross": 0.0, "costs": 0.0, "slip": 0.0, "years": yrs, "max_r": None, "r15": 0, "r5": 0, "r15_tight": 0,
           "beyond_n": 0, "beyond_rs": 0.0, "below_m1": 0, "worst_r": None, "avg_win_rs": None, "avg_loss_rs": None, "avg_net": None, "exp_rcap": None, "med_rcap": None}
    if t is None or t.empty:
        return out
    t = t[(t.entry_date >= start) & (t.entry_date <= end)]
    if t.empty:
        return out
    n, R = t.net, t.R
    w, l = n[n > 0], n[n <= 0]
    eq = capital + m["pnl"][a:b + 1] - (m["pnl"][a - 1] if a > 0 else 0)
    peak = np.maximum.accumulate(np.r_[capital, eq])[1:]
    dd = eq - peak
    net = float(n.sum())
    top = n.sort_values(ascending=False).head(max(1, int(np.ceil(len(n) * 0.05))))
    out.update({"trades": int(len(t)), "net": net, "ret_pct": net / capital * 100, "max_dd": float(dd.min()), "max_dd_pct": float((dd / peak).min() * 100),
                "cagr": ((eq[-1] / capital) ** (1 / yrs) - 1) * 100 if eq[-1] > 0 and yrs > 0 else -100.0,
                "avg_year_pct": float(eq[-1] / capital - 1) / yrs * 100 if yrs > 0 else 0.0,
                "win_pct": float((n > 0).mean() * 100), "avg_win_r": float(R[n > 0].mean()) if len(w) else None, "avg_loss_r": float(R[n <= 0].mean()) if len(l) else None,
                "avg_win_rs": float(w.mean()) if len(w) else None, "avg_loss_rs": float(l.mean()) if len(l) else None,
                "exp_r": float(R.mean()), "med_r": float(R.median()), "pf": float(w.sum() / -l.sum()) if l.sum() < 0 else None, "avg_hold": float(t.hold.mean()),
                "exposure": float(np.mean(m["dep"][a:b + 1] / np.maximum(eq, 1)) * 100), "in_mkt": float(np.mean(m["dep"][a:b + 1] > 0) * 100),
                "top5_net": float(top.sum()), "rest_net": net - float(top.sum()), "top5_share": float(top.sum() / net * 100) if net > 0 else None,
                "worst": float(n.min()), "best": float(n.max()), "gross": float(t.gross.sum()), "costs": float(t.costs.sum()), "slip": float(t.slip.sum()),
                "max_r": float(R.max()), "r15": int((R >= 15).sum()), "r5": int((R >= 5).sum()), "worst_r": float(R.min()),
                "r15_tight": int(((R >= 15) & (t.stop_pct < 1)).sum()), "avg_net": float(n.mean()), "exp_rcap": float(t.R_cap.mean()), "med_rcap": float(t.R_cap.median()),
                "beyond_n": int((t.beyond_rs > 0).sum()), "beyond_rs": float(t.beyond_rs.sum()), "below_m1": int((R < -1).sum())})
    return out


def buy_hold(I: dict, cal: list[str], capital: float, start: str, end: str, key: str = "n50") -> dict:
    """Index buy-and-hold (price index, no dividends) over the same dates."""
    a, b = int(np.searchsorted(cal, start)), int(np.searchsorted(cal, end, side="right")) - 1
    c = pd.Series(I[key][a:b + 1]).dropna()
    if c.empty:
        return {"ret_pct": None, "cagr": None, "max_dd_pct": None, "net": None, "years": None}
    yrs = (pd.Timestamp(cal[b]) - pd.Timestamp(cal[a])).days / 365.25
    ret = c.iloc[-1] / c.iloc[0] - 1
    return {"ret_pct": ret * 100, "cagr": ((1 + ret) ** (1 / yrs) - 1) * 100, "max_dd_pct": float((c / c.cummax() - 1).min() * 100), "net": capital * ret, "years": yrs}
