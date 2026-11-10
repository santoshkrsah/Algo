"""Data-quality report for this backtest: the podcast checks (duplicates, session, short days, OHLC, outliers, futures
expiries / rolls / continuity) on the NIFTY data, plus holidays, the spot closing-auction change, VIX and MCX gold."""
import numpy as np
import pandas as pd

from backtest.podcast import data as pdata
from backtest.podcast import quality as pq
from backtest.stma import bars


def holidays(start="2018-02-01", end="2026-11-09") -> list:
    """Weekdays without a NIFTY session in the data (exchange holidays, plus any missing day)."""
    d = pdata.read("NIFTY50", "1D")
    have = set(d["datetime"].dt.normalize())
    wk = pd.bdate_range(start, end)
    return [x for x in wk if x not in have]


def gold_checks() -> list[dict]:
    rows = []
    for n in ("GOLD_F1", "GOLD_F2"):
        df = pdata.read(n, "1m")
        day = df["datetime"].dt.normalize()
        per = df.groupby(day).size()
        hrs = df.groupby(day)["datetime"].agg(["min", "max"])
        rows.append({"dataset": f"{n}_1m", "check": "MCX session (first / last candle, candles per day)", "count": int(len(per)),
                     "examples": f"first {hrs['min'].dt.strftime('%H:%M').mode().iloc[0]}, last {hrs['max'].dt.strftime('%H:%M').mode().iloc[0]}, median {int(per.median())} candles/day",
                     "handling": "own 09:00-anchored 1-hour candles; evening session kept"})
        p = df[pdata.PRICE].to_numpy()
        rows.append({"dataset": f"{n}_1m", "check": "zero / negative prices", "count": int((p <= 0).any(axis=1).sum()), "examples": "", "handling": "dropped"})
        rows.append({"dataset": f"{n}_1m", "check": "duplicate candles", "count": int(df["datetime"].duplicated().sum()), "examples": "", "handling": "dropped"})
    return rows


def report(save_to=None) -> tuple[pd.DataFrame, dict]:
    rows = []
    for n in ("NIFTY50", "INDIAVIX", "BANKNIFTY"):
        rows += pq.check_1m(n, "index")
    rows += pq.check_daily("NIFTY50", "index") + pq.check_daily("INDIAVIX", "index")
    for k in (1, 2):
        rows += pq.check_1m(f"NIFTY_F{k}", "fut")
    rows += pq.check_futures("NIFTY")
    hol = holidays()
    rows.append({"dataset": "NIFTY50_1D", "check": "weekday holidays (no session in the data)", "count": len(hol),
                 "examples": pq._ex(d.strftime("%Y-%m-%d") for d in hol[-6:]), "handling": "no trading those days (data has none)"})
    vix = pdata.read("INDIAVIX", "1D")
    bad = vix[(vix["close"] <= 5) | (vix["close"] > 100)]
    rows.append({"dataset": "INDIAVIX_1D", "check": "VIX outside 5-100", "count": len(bad), "examples": pq._ex(bad["datetime"].dt.strftime("%Y-%m-%d")),
                 "handling": "none" if not len(bad) else "checked"})
    try:
        rows += gold_checks()
    except FileNotFoundError:
        pass
    fut, spot = bars.load()
    cas, mon = bars.closing_auction_start(spot)
    rows.append({"dataset": "NIFTY50_1m", "check": "spot frozen 15:20-15:28 then jump at 15:29 (closing auction)", "count": int((mon["flat_share"] > 0.6).sum()),
                 "examples": f"from {cas.date() if cas is not None else 'never'}; 15:29 move {mon['jump_1529_pct'].iloc[:-12].mean():.3f}% before vs {mon['jump_1529_pct'].iloc[-6:].mean():.3f}% last 6 months",
                 "handling": "L2 ignores the 15:15 part candle, L3 uses futures; results split before / after"})
    q = pd.DataFrame(rows)
    if save_to is not None:
        q.to_csv(save_to, index=False)
    return q, {"cas_start": cas, "cas_monthly": mon, "holidays": hol}
