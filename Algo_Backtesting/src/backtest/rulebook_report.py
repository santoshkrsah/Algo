"""HTML report (new look, simple Hinglish) for the rulebook strategies (orb / noise / vwap / fh).

    write_html(out_dir, data) -> Path      # called by tools/run_rulebook.py after report_data.json is written

`data` = report_data.json; trades.csv / equity.csv / yearly.csv / breakdown.csv are read from out_dir.
Trade viewer candles come from the fast store (fast.day_1m); if the whole run would be too big for one HTML file,
the candles are rolled up to 5 minutes (the chart title says so).
"""
import json
import re
from pathlib import Path

import numpy as np
import pandas as pd

from backtest import fast
from backtest import viewer as viewer_assets
from backtest.report import ASSETS

CANDLE_BUDGET = 9_000_000          # bytes of candle text allowed in one report (16 MB file limit)
TRADE_COLS = ["date", "symbol", "side", "entry_time", "entry_px", "exit_time", "exit_px", "stop_px", "shares", "notional",
              "gross_pnl", "slippage", "fees", "net_pnl", "ret_pct", "R", "reason"]


def _clean(v):
    if isinstance(v, dict):
        return {k: _clean(x) for k, x in v.items()}
    if isinstance(v, (list, tuple)):
        return [_clean(x) for x in v]
    if hasattr(v, "item"):
        v = v.item()
    return None if isinstance(v, float) and not np.isfinite(v) else v


def _mins(hhmm: str) -> int:
    h, m = map(int, str(hhmm).split(":"))
    return h * 60 + m


def _encode(c: pd.DataFrame, step: int) -> str:
    """Candles -> 'minute0,step,o,h,l,c,...' ints in paise; o is relative to the previous close, h/l/c relative to o."""
    if step > 1:
        g = c.assign(b=(c["minute"] // step) * step).groupby("b", sort=True)
        c = pd.DataFrame({"minute": g["b"].first(), "open": g["open"].first(), "high": g["high"].max(), "low": g["low"].min(),
                          "close": g["close"].last()})
    o, h, l, cl = [np.rint(c[k].to_numpy() * 100).astype(np.int64) for k in ("open", "high", "low", "close")]
    prev = np.r_[0, cl[:-1]]
    flat = np.column_stack([o - prev, h - o, l - o, cl - o]).ravel()
    return f"{int(c['minute'].iloc[0])},{step}," + ",".join(map(str, flat.tolist()))


def _candles(trades: pd.DataFrame) -> tuple[dict, int]:
    """{trade index: encoded candles}, and the minutes per candle used (1, or 5 when the 1-minute text would be too big)."""
    if not fast.available() or not len(trades):
        return {}, 1
    win = {}
    for i, r in trades.iterrows():
        win[i] = (str(r["date"])[:10], r["symbol"], _mins(r["entry_time"]) - 20, _mins(r["exit_time"]) + 15)
    for step in (1, 5):
        out, size = {}, 0
        by_day: dict[str, list[int]] = {}
        for i, (d, *_rest) in win.items():
            by_day.setdefault(d, []).append(i)
        for d, idx in by_day.items():
            df = fast.day_1m(d, sorted({win[i][1] for i in idx}))
            if df.empty:
                continue
            for sym, g in df.groupby("symbol", sort=False):
                for i in idx:
                    if win[i][1] != sym:
                        continue
                    _, _, lo, hi = win[i]
                    c = g[(g["minute"] >= max(lo, 555)) & (g["minute"] <= hi)]
                    if len(c):
                        out[i] = _encode(c, step)
                        size += len(out[i])
            if step == 1 and size > CANDLE_BUDGET:
                break
        if size <= CANDLE_BUDGET or step == 5:
            return out, step
    return {}, 5


def _read(out_dir: Path, name: str) -> pd.DataFrame:
    p = out_dir / name
    return pd.read_csv(p) if p.exists() else pd.DataFrame()


def write_html(out_dir, data: dict) -> Path:
    out_dir = Path(out_dir)
    tr = _read(out_dir, "trades.csv")
    eq = _read(out_dir, "equity.csv")
    yr = _read(out_dir, "yearly.csv")
    bd = _read(out_dir, "breakdown.csv")
    cap = float(data["params"]["capital"])
    if len(tr):
        tr = tr.sort_values(["date", "entry_time"], kind="stable").reset_index(drop=True)
    candles, step = _candles(tr)
    cols = TRADE_COLS + (["target_px"] if "target_px" in tr else [])
    rows = tr[cols].astype(object).where(tr[cols].notna(), None).values.tolist() if len(tr) else []
    for i, r in enumerate(rows):
        r.append(candles.get(i))
    equity = ([[str(d)[:10], round(float(e), 2), round(float(p), 2)] for d, e, p in zip(eq["date"], eq["equity"], eq["day_pnl"])]
              if len(eq) else [])
    blob = {**data, "capital": cap, "trade_cols": cols + ["candles"], "trades": rows, "candle_step": step, "equity": equity,
            "yearly": yr.astype(object).where(yr.notna(), None).values.tolist(), "yearly_cols": list(yr.columns),
            "breakdown": bd.astype(object).where(bd.notna(), None).values.tolist(), "breakdown_cols": list(bd.columns)}
    css = re.search(r"<style>.*?</style>", (ASSETS / "report_template.html").read_text(encoding="utf-8"), re.S).group(0)
    tpl = viewer_assets.apply((ASSETS / "rulebook_template.html").read_text(encoding="utf-8")).replace("__CSS__", css)
    tpl = tpl.replace("__TITLE__", f"{data['strategy_name']} {data['mode']}")
    js = (ASSETS / "chart.umd.min.js").read_text(encoding="utf-8")
    text = json.dumps(_clean(blob), separators=(",", ":"), default=str).replace("</", "<\\/")
    path = out_dir / "report.html"
    path.write_text(tpl.replace("__CHARTJS__", js).replace("__DATA__", text), encoding="utf-8")
    return path
