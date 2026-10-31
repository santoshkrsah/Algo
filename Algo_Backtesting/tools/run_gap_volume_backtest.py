"""Gap + 10-minute volume-spike intraday backtest (rules: user's "Backtest Rules" message, Jan-Oct 2026).

    python tools/run_gap_volume_backtest.py --start 2026-01-01 --end 2026-10-23 --name gapvol_2026

Universe each day = equity symbols whose F&O futures did not exist yet on that day (first futures candle date is
used as the F&O entry date; no historical F&O membership list exists in the data). Nifty gap uses NIFTY futures.
Trade paths are simulated once (slippage-free), then slippage 0 / 0.3% / 0.5% is applied to every fill.
"""
import argparse
import re
import sys
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from backtest.config import DATA, EQUITY, RESULTS  # noqa: E402
from backtest.data import trading_days  # noqa: E402

CAPITAL, PER_TRADE_MARGIN, LEVERAGE, MAX_TRADES = 70_000, 35_000, 5, 2
EXPOSURE = PER_TRADE_MARGIN * LEVERAGE
SL_PCT, GAP_LIMIT, VOL_MULT, MIN_TURNOVER, COST_PCT = 0.03, 1.5, 7.0, 5e7, 0.001
SLIPPAGES = (0.0, 0.003, 0.005)
M0, SIG_END, ENTRY_M, EXIT_M = 9 * 60 + 15, 9 * 60 + 24, 9 * 60 + 25, 15 * 60 + 15


def fno_start_dates() -> dict:
    out = {}
    for f in (DATA / "fno" / "1m" / "futures").glob("*.csv"):
        sym = re.sub(r"\d\d[A-Z]{3}FUT$", "", f.stem)
        out[sym] = pd.read_csv(f, usecols=["datetime"], nrows=1)["datetime"].iloc[0][:10]
    return out


def nifty_gap() -> pd.Series:
    d = pd.read_csv(DATA / "fno" / "1m" / "futures" / "NIFTY26NOVFUT.csv")
    d["day"] = d["datetime"].str[:10]
    g = d.groupby("day").agg(open=("open", "first"), close=("close", "last"))
    return ((g["open"] - g["close"].shift()) / g["close"].shift() * 100).dropna()


def load_day(day: str) -> pd.DataFrame:
    df = pd.read_csv(EQUITY / f"{day}.csv", usecols=["symbol", "timeframe", "datetime", "open", "high", "low", "close", "volume"])
    df = df[df["timeframe"].eq("1m")].drop(columns="timeframe")
    df["m"] = df["datetime"].str[11:13].astype(int) * 60 + df["datetime"].str[14:16].astype(int)
    return df.sort_values(["symbol", "m"], kind="stable").reset_index(drop=True)


def signal_stats(df: pd.DataFrame) -> pd.DataFrame:
    w = df[(df["m"] >= M0) & (df["m"] <= SIG_END)].copy()
    w["turn"] = w["close"] * w["volume"]
    g = w.groupby("symbol")
    s = pd.DataFrame({"open": g["open"].first(), "high": g["high"].max(), "low": g["low"].min(),
                      "close": g["close"].last(), "vol": g["volume"].sum(), "turn": g["turn"].sum(),
                      "first_m": g["m"].min()})
    return s[s["first_m"] == M0]


def simulate(c: pd.DataFrame, side: int):
    """c = candles of one symbol from 9:25 on. Returns (entry, exit, reason, exit_time) slippage-free."""
    c = c[c["m"] >= ENTRY_M]
    entry = c["open"].iloc[0]
    sl = entry * (1 - SL_PCT * side)
    for r in c.itertuples():
        if r.m >= EXIT_M:
            return entry, r.open, "SQUAREOFF", r.datetime
        if r.m > ENTRY_M and (r.open <= sl if side == 1 else r.open >= sl):
            return entry, r.open, "SL", r.datetime  # gap through the stop: fill at open
        if (r.low <= sl) if side == 1 else (r.high >= sl):
            return entry, sl, "SL", r.datetime
    last = c.iloc[-1]
    return entry, last["close"], "SQUAREOFF", last["datetime"]


def collect_trades(start: str, end: str) -> pd.DataFrame:
    fno, gaps = fno_start_dates(), nifty_gap()
    days = trading_days(None, end)
    i0 = next(i for i, d in enumerate(days) if d >= start)
    hist, rows = {}, []
    for i in range(max(0, i0 - 3), len(days)):
        day = days[i]
        df = load_day(day)
        st = signal_stats(df)
        hist[day] = st["vol"]
        if i < i0:
            continue
        gap = gaps.get(day, np.nan)
        if not (-GAP_LIMIT <= gap <= GAP_LIMIT):
            print(day, "skip: nifty gap", gap)
            continue
        prev = pd.concat([hist[d] for d in days[i - 3:i]], axis=1)
        avg3 = prev.mean(axis=1).where(prev.notna().all(axis=1) & (prev > 0).all(axis=1))
        st = st.join(avg3.rename("avg3"))
        in_fno = st.index.map(lambda s: fno.get(s, "9999") <= day)
        st = st[~np.asarray(in_fno)]
        q = st[(st["vol"] >= VOL_MULT * st["avg3"]) & (st["turn"] >= MIN_TURNOVER) & (st["close"] != st["open"])].copy()
        if q.empty:
            continue
        q["range_pct"] = (q["high"] - q["low"]) / q["open"] * 100
        q = q.sort_values("range_pct", ascending=False)
        n = 0
        for sym, r in q.iterrows():
            c = df[df["symbol"] == sym]
            if not (c["m"] == ENTRY_M).any():
                continue
            side = 1 if r["close"] > r["open"] else -1
            entry, exit_, reason, xt = simulate(c, side)
            rows.append(dict(date=day, symbol=sym, side="BUY" if side == 1 else "SELL", entry_raw=entry, exit_raw=exit_,
                             exit_reason=reason, exit_time=xt, range_pct=r["range_pct"], vol_mult=r["vol"] / r["avg3"],
                             turnover_cr=r["turn"] / 1e7))
            n += 1
            if n == MAX_TRADES:
                break
        print(day, "qualified", len(q), "traded", n)
    return pd.DataFrame(rows)


def apply_slippage(t: pd.DataFrame, s: float) -> pd.DataFrame:
    t = t.copy()
    side = np.where(t["side"] == "BUY", 1, -1)
    t["qty"] = np.floor(EXPOSURE / t["entry_raw"]).astype(int)
    t["entry"] = t["entry_raw"] * (1 + side * s)
    t["exit"] = t["exit_raw"] * (1 - side * s)
    t["gross"] = (t["exit"] - t["entry"]) * side * t["qty"]
    t["cost"] = COST_PCT * t["qty"] * (t["entry"] + t["exit"])  # 0.10% of turnover, both legs
    t["pnl"] = t["gross"] - t["cost"]
    return t


def stats(t: pd.DataFrame):
    t = t.sort_values(["date", "exit_time"]).reset_index(drop=True)
    cum = t["pnl"].cumsum()
    dd = (cum - np.maximum.accumulate(np.maximum(cum, 0))).min()
    daily = t.groupby("date")["pnl"].sum()
    win, loss = t["pnl"] > 0, t["pnl"] <= 0

    def streak(m):
        best = cur = 0
        for v in m:
            cur = cur + 1 if v else 0
            best = max(best, cur)
        return best

    gw, gl = t.loc[win, "pnl"].sum(), -t.loc[loss, "pnl"].sum()
    aw, al = t.loc[win, "pnl"].mean(), -t.loc[loss, "pnl"].mean()
    month = t.assign(month=t["date"].str[:7]).groupby("month")
    mrows = []
    for m, g in month:
        c = g.sort_values(["date", "exit_time"])["pnl"].cumsum()
        mdd = (c - np.maximum.accumulate(np.maximum(c, 0))).min()
        mrows.append(dict(month=m, net=g["pnl"].sum(), ret_pct=g["pnl"].sum() / CAPITAL * 100, trades=len(g),
                          win_pct=(g["pnl"] > 0).mean() * 100, max_dd=mdd))
    tot = {
        "Net profit (Rs)": t["pnl"].sum(), "Return % on 70k": t["pnl"].sum() / CAPITAL * 100,
        "Avg per month (Rs)": np.mean([r["net"] for r in mrows]), "Profit months": f"{sum(r['net'] > 0 for r in mrows)}/{len(mrows)}",
        "Trades": len(t), "Win %": win.mean() * 100, "Avg per trade (Rs)": t["pnl"].mean(),
        "Avg win (Rs)": aw, "Avg loss (Rs)": -al, "Profit factor": gw / gl if gl else np.inf, "Payoff ratio": aw / al if al else np.inf,
        "Max drawdown (Rs)": dd, "Max drawdown % of 70k": dd / CAPITAL * 100,
        "Best trade (Rs)": t["pnl"].max(), "Worst trade (Rs)": t["pnl"].min(),
        "Best day (Rs)": daily.max(), "Worst day (Rs)": daily.min(),
        "Max consecutive wins": streak(win), "Max consecutive losses": streak(loss),
        "SL hit %": (t["exit_reason"] == "SL").mean() * 100,
    }
    return tot, pd.DataFrame(mrows), t


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--start", default="2026-01-01")
    ap.add_argument("--end", default="2026-10-23")
    ap.add_argument("--name", default="gapvol_2026")
    a = ap.parse_args()
    out = RESULTS / a.name
    out.mkdir(parents=True, exist_ok=True)
    raw = collect_trades(a.start, a.end)
    raw.to_csv(out / "raw_trades.csv", index=False)
    for s in SLIPPAGES:
        tag = f"slip_{s * 100:.1f}pct"
        tot, months, t = stats(apply_slippage(raw, s))
        cols = ["date", "symbol", "side", "qty", "entry", "exit", "exit_reason", "gross", "cost", "pnl", "range_pct", "vol_mult", "turnover_cr"]
        t[cols].round(2).to_csv(out / f"trades_{tag}.csv", index=False)
        months.round(2).to_csv(out / f"monthly_{tag}.csv", index=False)
        print(f"\n===== SLIPPAGE {s:.1%} =====")
        print(months.round(1).to_string(index=False))
        for k, v in tot.items():
            print(f"{k:28s} {v:,.2f}" if isinstance(v, (float, np.floating)) else f"{k:28s} {v}")


if __name__ == "__main__":
    main()
