"""Charges per order, vectorised over many orders. Rates: CONFIG costs (verify with your broker)."""
import numpy as np

from backtest.podcast.config import CONFIG

KIND_COST = {"fut": "fut", "sfut": "fut", "index": "fut", "cash": "cash_intraday", "opt": "opt"}   # index (long check) charged like futures


def stt_rate(model: dict, days: np.ndarray) -> np.ndarray:
    """STT % on the sell side for each order date (datetime64[D])."""
    out = np.zeros(len(days))
    for d0, r in model["stt_sell_pct"]:
        out[days >= np.datetime64(d0, "D")] = r
    return out / 100.0


def order_costs(kind: str, value: np.ndarray, is_buy: np.ndarray, days: np.ndarray) -> dict:
    """value: order value in Rs (real price x quantity); is_buy: bool; days: datetime64[D]. Returns arrays per order."""
    m = CONFIG["costs"][KIND_COST[kind]]
    value = np.asarray(value, float)
    if "brokerage_pct" in m:
        brk = np.minimum(m["brokerage_cap"], m["brokerage_pct"] / 100.0 * value)
    else:
        brk = np.full(len(value), m["brokerage"])
    brk = np.where(value > 0, brk, 0.0)
    stt = np.where(is_buy, 0.0, stt_rate(m, days) * value)
    exch = m["exch_pct"] / 100.0 * value
    sebi = m["sebi_pct"] / 100.0 * value
    stamp = np.where(is_buy, m["stamp_buy_pct"] / 100.0 * value, 0.0)
    gst = m["gst_pct"] / 100.0 * (brk + exch + sebi)
    return {"brokerage": brk, "stt": stt, "exchange": exch + sebi, "stamp": stamp, "gst": gst,
            "total": brk + stt + exch + sebi + stamp + gst}
