"""Interactive HTML report (simple Hinglish) for the R3 / S3 option-buying backtest: pick setup x universe x pivot x strike x
gap x exit; edge check vs random entries, in-sample / out-of-sample, splits, every trade, trade viewer."""
import json
import math
import re
from datetime import datetime
from pathlib import Path

import pandas as pd

from backtest import viewer as viewer_assets
from backtest.r3buy import EXITS, WHY
from backtest.report import ASSETS

# one row per unique trade (contract-day); runs point into this table
BASE = ["date", "symbol", "type", "strike", "expiry", "dte", "exp_week", "vix", "gap_pct", "u_close",
        "ur3_classic", "us3_classic", "ur3_camarilla", "us3_camarilla", "r3_classic", "r3_camarilla",
        "close", "low", "p_high", "p_low", "p_close", "n_date", "n_open", "n_high", "n_low", "n_close", "lot",
        "lots_t", "lots_sl20", "lots_sl30", "lots_low"]
ROUND = {"vix": 2, "gap_pct": 2, "u_close": 2, "ur3_classic": 2, "us3_classic": 2, "ur3_camarilla": 2, "us3_camarilla": 2,
         "r3_classic": 2, "r3_camarilla": 2}


def _num(v):
    v = v.item() if hasattr(v, "item") else v
    return None if isinstance(v, float) and not math.isfinite(v) else v


def trade_table(picks: dict, keep) -> tuple[list, dict, dict]:
    """Unique trade rows of the runs in <keep> + {run key: [row indices]}. Per exit: why code and net (Rs)."""
    rows, index, runs = [], {}, {}
    cols = BASE + [f"{e[0]}_{x}" for e in EXITS for x in ("why", "net")]
    for c, t in picks.items():
        if not keep(c):
            continue
        ids = []
        for r in t[cols].itertuples(index=False):
            key = (r.date, r.symbol, r.type, r.strike, r.expiry)
            if key not in index:
                index[key] = len(rows)
                vals = []
                for name, v in zip(cols, r):
                    v = _num(v)
                    if isinstance(v, float):
                        v = round(v, ROUND.get(name, 2)) if not name.endswith("_net") else round(v)
                    vals.append(v)
                rows.append(vals)
            ids.append(index[key])
        runs["|".join(str(int(x) if isinstance(x, bool) else x) for x in c)] = ids
    return rows, runs, {"cols": cols}


def contract_series(pool: pd.DataFrame, rows: list, cols: list, before_days: int = 12, after_days: int = 3) -> dict:
    """Daily candles of every traded contract (symbol|expiry|strike|type) around its trade days (+ underlying close)."""
    t = pd.DataFrame(rows, columns=cols)[["symbol", "expiry", "strike", "type", "date"]]
    t = t.assign(date=pd.to_datetime(t.date)).groupby(["symbol", "expiry", "strike", "type"]).date.agg(["min", "max"]).reset_index()
    t["lo"] = (t["min"] - pd.Timedelta(days=before_days)).dt.strftime("%Y-%m-%d")
    t["hi"] = (t["max"] + pd.Timedelta(days=after_days)).dt.strftime("%Y-%m-%d")
    return t[["symbol", "expiry", "strike", "type", "lo", "hi"]]


def series_from_options(d: pd.DataFrame, want: pd.DataFrame) -> dict:
    x = d[["symbol", "expiry", "strike", "type", "date", "open", "high", "low", "close", "und"]].astype({"symbol": str, "type": str})
    x = x.merge(want, on=["symbol", "expiry", "strike", "type"])
    x = x[(x.date >= x.lo) & (x.date <= x.hi)].sort_values(["symbol", "expiry", "strike", "type", "date"])
    out = {}
    for (sym, exp, k, typ), g in x.groupby(["symbol", "expiry", "strike", "type"]):
        out[f"{sym}|{exp}|{k:g}|{typ}"] = g[["date", "open", "high", "low", "close", "und"]].round(2).values.tolist()
    return out


def grid_rows(g: pd.DataFrame) -> tuple[list, list]:
    cols = ["setup", "univ", "pivot", "k", "gap", "exit", "signals", "sig_per_month", "all_trades", "all_win_pct", "all_net", "all_pf",
            "all_max_dd", "all_avg", "all_avg_ret", "is_trades", "is_net", "is_pf", "oos_trades", "oos_net", "oos_pf", "oos_win_pct", "rand_pct"]
    out = []
    for r in g[cols].itertuples(index=False):
        out.append([(_num(v) if not isinstance(_num(v), float) else round(v, 2)) for v in r])
    return cols, out


def _clean(o):
    """NaN / inf -> None, numpy scalars -> Python, everywhere in the report data."""
    if isinstance(o, dict):
        return {k: _clean(v) for k, v in o.items()}
    if isinstance(o, (list, tuple)):
        return [_clean(v) for v in o]
    return _num(o)


def write_html(path, data: dict):
    data = dict(_clean(data), exits=[[e[0], e[1], e[2], e[3], e[4]] for e in EXITS], why=WHY, generated=datetime.now().strftime("%d %b %Y %H:%M"))
    css = re.search(r"<style>.*?</style>", (ASSETS / "report_template.html").read_text(encoding="utf-8"), re.S).group(0)
    tpl = viewer_assets.apply((ASSETS / "r3buy_template.html").read_text(encoding="utf-8")).replace("__CSS__", css)
    js = (ASSETS / "chart.umd.min.js").read_text(encoding="utf-8")
    blob = json.dumps(data, separators=(",", ":"), default=_num, allow_nan=False).replace("</", "<\\/")
    Path(path).write_text(tpl.replace("__CHARTJS__", js).replace("__DATA__", blob), encoding="utf-8")
