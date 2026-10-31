"""Strategy settings and the entry/exit rules (see strategies/MY_STRATEGY_RULES.md).

Rule: first 15m candle of the day closes above the Fibonacci R3 pivot (from the previous day's H/L/C) -> BUY at that
close. Stop-loss 3% below the buy rate. Otherwise square off at 15:00. Long only.
"""
from dataclasses import dataclass


@dataclass(frozen=True)
class Params:
    trade_value: float = 20_000_000        # Rs 2 cr bought per trade, every trade equal
    max_trades_per_day: int = 5
    min_price: float = 30.0                # skip stocks priced below this
    stop_loss_pct: float = 0.03            # below the buy rate
    squareoff: str = "15:00"               # exit at the open of this minute
    category_order: tuple = ("Small", "Mid", "Large")
    # costs: standard Fyers/NSE intraday equity rates + slippage on each side
    slippage_pct: float = 0.0005
    brokerage_pct: float = 0.0003          # 0.03% of turnover or Rs 20 per order, whichever is lower
    brokerage_cap: float = 20.0
    stt_sell_pct: float = 0.00025
    exchange_pct: float = 0.0000307        # NSE transaction charge, both sides
    sebi_pct: float = 0.000001
    stamp_buy_pct: float = 0.00003
    gst_pct: float = 0.18                  # on brokerage + exchange + SEBI


def fib_r3(high, low, close):
    """Fibonacci R3 = Pivot + 1.000 x (High - Low), Pivot = (H+L+C)/3."""
    return (high + low + close) / 3 + (high - low)


def costs(p: Params, qty: int, buy: float, sell: float) -> float:
    """Total Rs charges for one round trip (buy then sell), excluding slippage (already in prices)."""
    b, s = qty * buy, qty * sell
    brokerage = min(p.brokerage_cap, p.brokerage_pct * b) + min(p.brokerage_cap, p.brokerage_pct * s)
    exchange = p.exchange_pct * (b + s)
    sebi = p.sebi_pct * (b + s)
    gst = p.gst_pct * (brokerage + exchange + sebi)
    return brokerage + exchange + sebi + gst + p.stt_sell_pct * s + p.stamp_buy_pct * b
