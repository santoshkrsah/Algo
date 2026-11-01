"""Calendar spread trading (stock futures): near month vs next month of the same stock. When the gap moves far from the
stock's normal gap, trade it back to normal: wide -> SELL far + BUY near, narrow -> BUY far + SELL near.
Scale in 2 + 1 lots, half out at half the way back, rest at normal, roll once, risk stop. Daily NSE bhavcopy closes.

    table, px = load("2024-11-01", "2026-10-30")       # one row per stock-day: near / far / third contract, gap, normal
    trades, equity, stats = simulate(table, px, Params(threshold=1.5))
"""
import bisect
import math
from dataclasses import dataclass, field, replace

import numpy as np
import pandas as pd

from backtest.config import DATA, FNO

CORP_CSV = DATA / "reference" / "corporate_actions.csv"
COLS = {"TradDt": "date", "TckrSymb": "symbol", "FinInstrmNm": "contract", "FininstrmActlXpryDt": "expiry", "ClsPric": "close",
        "TtlTradgVol": "vol", "TtlNbOfTxsExctd": "trades", "OpnIntrst": "oi", "NewBrdLotQty": "lot"}


@dataclass(frozen=True)
class Params:
    threshold: float = 2.0         # entry when |gap% - normal gap%| >= this (percentage points)
    entry_on: str = "dev"          # "dev" = distance from the stock's normal gap; "raw" = gap% itself >= threshold / <= -threshold
    sides: str = "both"            # "both", "wide" (only SELL far + BUY near) or "narrow" (only BUY far + SELL near)
    wait: int = 1                  # sessions the gap must stop expanding before entry (0 = enter on the first day)
    lookback: int = 120            # trading days for the stock's normal (median) gap
    consist_tol: float = 0.25      # a past series "came back" if the gap at near expiry was within this of normal (pp)
    consist_need: int | None = 4   # ... in at least this many of the last 6 series (None = no filter)
    min_trades: int = 100          # liquidity: both contracts need this many trades that day
    ca_filter: bool = True         # no entry / roll when a corporate action (dividend, bonus, split ...) ex-date is before the far expiry
    lots1: int = 2                 # first tranche (pairs)
    lots2: int = 1                 # second tranche (0 = no scale-in)
    scale_step: float = 0.5        # second tranche when the gap is this much (pp) further away than at entry
    partial: bool = True           # exit half at half the way back to normal
    roll: bool = True              # roll once, ROLL_DAYS before near expiry
    roll_days: int = 2
    stop_mult: float | None = 1.5  # risk stop: gap distance >= 1.5 x entry distance ...
    stop_loss: float | None = 0.02  # ... or trade loss >= 2% of capital
    no_new_days: int = 3           # no new entries in the last 3 trading days before near expiry
    max_lots: int = 5              # all open pairs together
    capital: float = 200_000.0
    margin_per_pair: float = 17_000.0
    slip: float = 0.0              # slippage, share of price, every leg every fill


# ---------------------------------------------------------------- data
def load(start: str, end: str) -> tuple[pd.DataFrame, dict]:
    files = [p for p in sorted((FNO / "bhavcopy").glob("*.csv")) if start <= p.stem <= end]
    b = pd.concat([pd.read_csv(p, usecols=list(COLS) + ["FinInstrmTp"]) for p in files], ignore_index=True)
    b = b[b.FinInstrmTp == "STF"].rename(columns=COLS)[list(COLS.values())]
    b = b.sort_values(["date", "symbol", "expiry"]).reset_index(drop=True)
    px = {(d, c): (cl, lot, tr, ex) for d, c, cl, lot, tr, ex in zip(b.date, b.contract, b.close, b.lot, b.trades, b.expiry)}
    b["rank"] = b.groupby(["date", "symbol"]).cumcount()
    legs = []
    for r, name in enumerate(["near", "far", "third"]):
        x = b[b["rank"] == r].set_index(["date", "symbol"])[["contract", "close", "lot", "trades", "expiry"]]
        legs.append(x.add_prefix(name + "_"))
    t = legs[0].join(legs[1], how="inner").join(legs[2], how="left").reset_index()
    t["gap"] = (t.far_close - t.near_close).round(2)
    t["gap_pct"] = t.gap / t.near_close * 100
    t["liquid"] = (t.near_trades >= 1) & (t.far_trades >= 1)
    t = t.sort_values(["symbol", "date"]).reset_index(drop=True)
    ca = pd.read_csv(CORP_CSV) if CORP_CSV.exists() else pd.DataFrame(columns=["symbol", "series", "ex_date", "purpose"])
    ca = ca[ca.series.isin(["EQ", "BE"]) & ca.ex_date.notna()]
    by = {s: sorted(g.ex_date) for s, g in ca.groupby("symbol")}

    def ca_until(col):     # True if a corporate action ex-date falls in [date, <col> expiry]
        out = []
        for sym, d, e in zip(t.symbol, t.date, t[col]):
            x = by.get(sym)
            out.append(bool(x) and isinstance(e, str) and bisect.bisect_right(x, e) > bisect.bisect_left(x, d))
        return out

    t["ca_far"], t["ca_third"] = ca_until("far_expiry"), ca_until("third_expiry")
    return t, px


def prepare(t: pd.DataFrame, p: Params) -> pd.DataFrame:
    """Adds normal gap, distance from normal (dev), past-series check and the entry signal for these parameters."""
    t = t.copy()
    t["liquid"] = (t.near_trades >= p.min_trades) & (t.far_trades >= p.min_trades)
    g = t.gap_pct.where(t.liquid)
    t["normal"] = g.groupby(t.symbol).transform(lambda s: s.shift(1).rolling(p.lookback, min_periods=20).median())
    t["dev"] = (t.gap_pct - t.normal).where(t.liquid)
    # past series: gap on the near contract's last day vs normal
    last = t.groupby(["symbol", "near_contract"]).tail(1)
    if p.ca_filter:                # a series with a corporate action between the two expiries says nothing about the gap's habit
        last = last[~last.ca_far]
    ok = (last.gap_pct - last.normal).abs() <= p.consist_tol
    series = pd.DataFrame({"symbol": last.symbol, "expiry": last.near_expiry, "ok": ok.astype(int)})
    t["consist"], t["n_series"] = 0, 0
    for sym, s in series.groupby("symbol"):
        s = s.sort_values("expiry")
        exps, oks = s.expiry.tolist(), s.ok.tolist()
        m = t.symbol == sym
        k = [bisect.bisect_left(exps, d) for d in t.loc[m, "date"]]     # series that expired before this day
        t.loc[m, "n_series"] = [min(6, i) for i in k]
        t.loc[m, "consist"] = [sum(oks[max(0, i - 6):i]) for i in k]
    x = t.gap_pct if p.entry_on == "raw" else t.dev
    t["dir"] = np.select([x >= p.threshold, x <= -p.threshold], [1, -1], 0)
    t.loc[t.dev.isna(), "dir"] = 0
    if p.sides != "both":
        t.loc[t["dir"] != (1 if p.sides == "wide" else -1), "dir"] = 0
    sig = t["dir"] != 0
    a = t.dev.abs()
    for k in range(1, p.wait + 1):
        same = t.groupby("symbol")["dir"].shift(k) == t["dir"]
        calmer = a.groupby(t.symbol).shift(k - 1) <= a.groupby(t.symbol).shift(k)
        sig &= same & calmer
    if p.consist_need is not None:
        sig &= (t.n_series >= 6) & (t.consist >= p.consist_need)
    sig &= (t.near_lot == t.far_lot) & t.normal.notna()
    if p.ca_filter:
        sig &= ~t.ca_far
    t["signal"] = sig
    return t


def _costs(buy: float, sell: float, orders: int) -> float:
    """Fyers/NSE futures charges on Rs turnover (buy value, sell value)."""
    brokerage = 20.0 * orders
    exchange = 0.0000173 * (buy + sell)
    sebi = 0.000001 * (buy + sell)
    return brokerage + exchange + sebi + 0.18 * (brokerage + exchange + sebi) + 0.0002 * sell + 0.00002 * buy


# ---------------------------------------------------------------- simulation
@dataclass
class Pos:
    symbol: str
    side: int                      # +1: SELL far, BUY near (gap wide); -1: BUY far, SELL near (gap narrow)
    near_c: str
    far_c: str
    near_exp: str
    normal: float
    a0: float                      # distance from normal at entry (pp, positive)
    lot: int
    entry_date: str
    entry_gap: float
    entry_gap_pct: float
    first_pair: str
    tranches: list = field(default_factory=list)   # [lots, near_px, far_px]
    plan: list = field(default_factory=list)       # "2", "+1"
    realized: float = 0.0
    costs: float = 0.0
    scaled: bool = False
    half_done: bool = False
    rolled: bool = False
    roll_date: str = "-"
    max_a: float = 0.0
    exit1: str = "-"
    max_lots: int = 0

    def lots(self) -> int:
        return sum(t[0] for t in self.tranches)


def _pnl(pos: Pos, n: int, lot: int, near_in: float, far_in: float, near_out: float, far_out: float) -> float:
    return pos.side * n * lot * ((far_in - far_out) + (near_out - near_in))


def simulate(t: pd.DataFrame, px: dict, p: Params, start: str | None = None) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Returns (trades, daily equity). Fills at the day's NSE close +/- slippage."""
    days = sorted(t.date.unique())
    rows = {d: g for d, g in t.groupby("date")}
    open_: dict[str, Pos] = {}
    trades, equity, realized_total = [], [], 0.0
    s = p.slip

    def fill(price, buy):
        return price * (1 + s) if buy else price * (1 - s)

    def close_lots(pos: Pos, d: str, n: int, near_px: float, far_px: float) -> float:
        """Close n pairs FIFO at today's closes. Returns realized P&L (costs added to pos.costs)."""
        out, left = 0.0, n
        near_buy = pos.side == -1          # closing: side +1 had BUY near -> now SELL near
        near_o, far_o = fill(near_px, near_buy), fill(far_px, not near_buy)
        while left:
            tr = pos.tranches[0]
            k = min(left, tr[0])
            out += _pnl(pos, k, pos.lot, tr[1], tr[2], near_o, far_o)
            tr[0] -= k
            left -= k
            if tr[0] == 0:
                pos.tranches.pop(0)
        q = n * pos.lot
        buy_v, sell_v = (q * near_o, q * far_o) if near_buy else (q * far_o, q * near_o)
        pos.costs += _costs(buy_v, sell_v, 2)
        pos.realized += out
        return out

    def open_lots(pos: Pos, n: int, near_px: float, far_px: float):
        near_buy = pos.side == 1
        near_i, far_i = fill(near_px, near_buy), fill(far_px, not near_buy)
        pos.tranches.append([n, near_i, far_i])
        q = n * pos.lot
        buy_v, sell_v = (q * near_i, q * far_i) if near_buy else (q * far_i, q * near_i)
        pos.costs += _costs(buy_v, sell_v, 2)
        pos.max_lots = max(pos.max_lots, pos.lots())

    def finish(pos: Pos, d: str, gap: float, reason: str):
        nonlocal realized_total
        net = pos.realized - pos.costs
        realized_total += net
        held = days.index(d) - days.index(pos.entry_date)
        trades.append({
            "symbol": pos.symbol, "side": "Far SELL + Near BUY" if pos.side == 1 else "Far BUY + Near SELL",
            "entry_date": pos.entry_date, "pair": pos.first_pair, "lot": pos.lot, "entry_gap": round(pos.entry_gap, 2),
            "entry_gap_pct": round(pos.entry_gap_pct, 3), "normal_pct": round(pos.normal, 3), "entry_dist": round(pos.a0, 3),
            "lots": "+".join(pos.plan), "max_lots": pos.max_lots, "exit1": pos.exit1, "exit_date": d, "exit_gap": round(gap, 2),
            "exit_reason": reason, "rolled": "haan" if pos.rolled else "nahi", "roll_date": pos.roll_date,
            "max_adverse_dist": round(pos.max_a, 3), "days": held, "gross_pnl": round(pos.realized, 2), "costs": round(pos.costs, 2),
            "net_pnl": round(net, 2), "margin": pos.max_lots * p.margin_per_pair})

    for di, d in enumerate(days):
        today = rows[d].set_index("symbol")
        used = sum(x.lots() for x in open_.values())
        exited = set()
        for sym in list(open_):
            pos = open_[sym]
            n_ = px.get((d, pos.near_c))
            f_ = px.get((d, pos.far_c))
            if n_ is None or f_ is None:
                continue
            near_px, far_px = n_[0], f_[0]
            gap = far_px - near_px
            a = pos.side * (gap / near_px * 100 - pos.normal)
            pos.max_a = max(pos.max_a, a)
            unreal = sum(_pnl(pos, tr[0], pos.lot, tr[1], tr[2], near_px, far_px) for tr in pos.tranches)
            total = pos.realized + unreal - pos.costs
            exp_i = bisect.bisect_right(days, pos.near_exp) - 1
            at_window = pos.near_exp <= days[-1] and di >= exp_i - p.roll_days
            reason = None
            if (p.stop_mult is not None and a >= p.stop_mult * pos.a0) or (p.stop_loss is not None and total <= -p.stop_loss * p.capital):
                reason = "risk stop"
            elif a <= 0:
                reason = "gap normal (exit 2)"
            elif d == days[-1]:
                reason = "data khatam"
            if reason:
                close_lots(pos, d, pos.lots(), near_px, far_px)
                finish(pos, d, gap, reason)
                del open_[sym]
                exited.add(sym)
                continue
            if p.partial and not pos.half_done and pos.lots() >= 2 and a <= pos.a0 / 2:
                close_lots(pos, d, pos.lots() // 2, near_px, far_px)
                pos.half_done, pos.exit1 = True, f"{d} ({gap:.2f})"
            if p.lots2 and not pos.scaled and a >= pos.a0 + p.scale_step and used < p.max_lots and not at_window:
                k = min(p.lots2, p.max_lots - used)
                open_lots(pos, k, near_px, far_px)
                pos.scaled = True
                pos.plan.append(f"{k}")
                used += k
            if at_window:
                third = None
                if sym in today.index and today.loc[sym, "far_contract"] == pos.far_c:
                    r = today.loc[sym]
                    if (isinstance(r.third_contract, str) and r.third_lot == r.far_lot == pos.lot and r.third_trades >= p.min_trades
                            and not (p.ca_filter and r.ca_third)):
                        third = (r.third_contract, r.third_close, r.third_expiry)
                if p.roll and not pos.rolled and third is not None:
                    n_open = pos.lots()
                    close_lots(pos, d, n_open, near_px, far_px)
                    pos.near_c, pos.far_c, pos.near_exp = pos.far_c, third[0], f_[3]
                    open_lots(pos, n_open, far_px, third[1])
                    pos.rolled, pos.roll_date = True, d
                else:
                    close_lots(pos, d, pos.lots(), near_px, far_px)
                    finish(pos, d, gap, "2nd expiry tak nahi bhara" if pos.rolled else "expiry aa gayi (roll nahi)")
                    del open_[sym]
                    exited.add(sym)
        used = sum(x.lots() for x in open_.values())
        if (start is None or d >= start) and d != days[-1]:
            cand = rows[d][rows[d].signal & ~rows[d].symbol.isin(list(open_) + list(exited))]
            for r in cand.assign(_a=cand.dev.abs()).sort_values("_a", ascending=False).itertuples():
                if used >= p.max_lots:
                    break
                exp_i = bisect.bisect_right(days, r.near_expiry) - 1
                if r.near_expiry <= days[-1] and di >= exp_i - p.no_new_days:
                    continue
                k = min(p.lots1, p.max_lots - used)
                side = int(r.dir)
                pos = Pos(r.symbol, side, r.near_contract, r.far_contract, r.near_expiry, float(r.normal),
                          float(side * r.dev), int(r.near_lot), d, float(r.gap), float(r.gap_pct),
                          f"{r.near_contract.replace('FUT', '')}/{r.far_contract[len(r.symbol):].replace('FUT', '')}")
                open_lots(pos, k, r.near_close, r.far_close)
                pos.plan.append(f"{k}")
                pos.max_a = pos.a0
                open_[r.symbol] = pos
                used += k
        unreal = 0.0
        for pos in open_.values():
            n_, f_ = px.get((d, pos.near_c)), px.get((d, pos.far_c))
            if n_ and f_:
                unreal += sum(_pnl(pos, tr[0], pos.lot, tr[1], tr[2], n_[0], f_[0]) for tr in pos.tranches) + pos.realized - pos.costs
        equity.append((d, realized_total, realized_total + unreal, sum(x.lots() for x in open_.values())))
    tr = pd.DataFrame(trades)
    eq = pd.DataFrame(equity, columns=["date", "realized", "mtm", "open_lots"])
    return tr, eq


def with_params(base: Params, **kw) -> Params:
    return replace(base, **kw)
