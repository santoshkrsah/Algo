"""HTML report (new look, simple Hinglish) for the 5 Groww intraday strategies: one page, a strategy picker on top."""
import json
import re
from datetime import datetime
from pathlib import Path

import numpy as np
import pandas as pd

from backtest import fast
from backtest import viewer as viewer_assets
from backtest.groww5.config import CONFIG, STRATS, TITLE_SUFFIX, VIDEO
from backtest.groww5.data import OPEN
from backtest.report import ASSETS

TRADE_COLS = ["date", "sym", "side", "t_in", "entry", "t_out", "exit", "sl", "tgt", "reason", "qty", "value", "gross0", "slip", "charges", "net", "R", "info",
              "c_brokerage", "c_stt", "c_exchange", "c_stamp", "c_gst", "entry_f", "exit_f", "lines"]


def _clean(v):
    if isinstance(v, dict):
        return {str(k): _clean(x) for k, x in v.items()}
    if isinstance(v, (list, tuple)):
        return [_clean(x) for x in v]
    if isinstance(v, np.bool_):
        return bool(v)
    if hasattr(v, "item"):
        v = v.item()
    if isinstance(v, float):
        if not np.isfinite(v):
            return None
        return round(v, 4)
    return v


def hhmm(i) -> str:
    m = OPEN + int(i)
    return f"{m // 60:02d}:{m % 60:02d}"


def info_text(strat, i: dict) -> str:
    if strat in ("brk", "rev"):
        z = "Resistance" if i.get("zone") == "R" else "Support"
        return f"{z} zone {i['lo']:.2f} - {i['hi']:.2f}, {i['touch']} touch"
    if strat == "news":
        return f"Gap {i['gap']:+.2f}%, pehli 5-min candle O/H/L/C {' / '.join(f'{x:.2f}' for x in i['o5'])}"
    if strat == "scalp":
        return f"PSAR {i['psar']:.2f}"
    if strat == "momo":
        return f"Aakhri trail SL {i['trail'] - i['off']:.1f}"
    return ""


def extra_lines(strat, i: dict, off: float) -> list:
    if strat in ("brk", "rev"):
        return [[i["lo"], "Zone low"], [i["hi"], "Zone high"]]
    if strat == "news":
        return [[i["o5"][1], "Pehli candle high"], [i["o5"][2], "Pehli candle low"]]
    if strat == "momo":
        return [[round(i["trail"] - off, 1), "Aakhri trail SL"]]
    return []


def trade_rows(t: pd.DataFrame, strat: str) -> list:
    rows = []
    off = t["info"].map(lambda i: i.get("off", 0.0)).to_numpy() if strat == "momo" else np.zeros(len(t))
    for r, o in zip(t.itertuples(index=False), off):
        tg = None if not np.isfinite(r.tgt) else round(r.tgt - o, 2)
        rows.append([r.date, r.sym, "LONG" if r.side > 0 else "SHORT", hhmm(r.e), round(r.entry - o, 2), hhmm(r.x), round(r.exit - o, 2), round(r.sl - o, 2), tg,
                     r.reason, int(r.qty), round(r.value), round(r.gross0), round(r.slip_rs), round(r.charges), round(r.net), None if not np.isfinite(r.R) else round(r.R, 2),
                     info_text(strat, r.info), round(r.c_brokerage, 2), round(r.c_stt, 2), round(r.c_exchange, 2), round(r.c_stamp, 2), round(r.c_gst, 2),
                     round(r.entry_f - o, 2), round(r.exit_f - o, 2), extra_lines(strat, r.info, o)])
    return rows


def _pack(o, h, l, c, t0, tf):
    """Candles -> [tf, first minute index, base, flat ints (o, h, l, c) - base], prices in ticks of Rs 0.05. Gaps = previous close."""
    df = pd.DataFrame({"o": o, "h": h, "l": l, "c": c}, dtype=float)
    df["c"] = df["c"].ffill()
    for k in ("o", "h", "l"):
        df[k] = df[k].fillna(df["c"])
    df = df.dropna()
    a = np.round(df.to_numpy() * 20).astype(np.int64)
    base = int(a[0, 0]) if len(a) else 0
    return [tf, int(t0), base, (a - base).ravel().tolist()]


def candles_stocks(t: pd.DataFrame, strat: str) -> list:
    """Per trade: scalp = 1-min candles (signal - 10 .. exit + 3); others = 5-min candles (entry - 1 hour .. exit + 20 min)."""
    out = [None] * len(t)
    if not len(t):
        return out
    t = t.reset_index(drop=True)
    for sym, g in t.groupby("sym"):
        days = sorted(g["date"].unique())
        df = fast.symbol_1m(sym, days[0], days[-1])
        df = df[df["date"].astype(str).isin(set(days))]
        by = {d: x for d, x in df.groupby(df["date"].astype(str))}
        for i, r in g.iterrows():
            x = by.get(r["date"])
            if x is None:
                continue
            m = x["minute"].to_numpy().astype(int) - OPEN
            if strat == "scalp":
                a, b = max(0, r["sig"] - 10), r["x"] + 3
                k = (m >= a) & (m <= b)
                out[i] = _pack(x["open"].to_numpy()[k], x["high"].to_numpy()[k], x["low"].to_numpy()[k], x["close"].to_numpy()[k], m[k][0] if k.any() else a, 1)
            else:
                a, b = max(0, (r["e"] // 5 - 12) * 5), min(374, r["x"] + 20)
                k = (m >= a) & (m <= b)
                xx = x[k].assign(bk=m[k] // 5)
                gg = xx.groupby("bk").agg(o=("open", "first"), h=("high", "max"), l=("low", "min"), c=("close", "last"))
                full = gg.reindex(range(int(gg.index.min()), int(gg.index.max()) + 1)).ffill()
                out[i] = _pack(full["o"].to_numpy(), full["h"].to_numpy(), full["l"].to_numpy(), full["c"].to_numpy(), int(gg.index.min()) * 5, 5)
    return out


def candles_momo(t: pd.DataFrame, F) -> list:
    out = []
    for r in t.itertuples(index=False):
        i_e, i_x = r.info["m_e"], r.info["m_x"]
        d = F.di[i_e]
        a, b = max(F.first[d], i_e - 60), min(F.last[d], i_x + 20)
        sl = slice(a, b + 1)
        bk = (F.minute[sl] - OPEN) // 5
        df = pd.DataFrame({"bk": bk, "o": F.o[sl] - F.off[sl], "h": F.h[sl] - F.off[sl], "l": F.l[sl] - F.off[sl], "c": F.c[sl] - F.off[sl]})
        gg = df.groupby("bk").agg(o=("o", "first"), h=("h", "max"), l=("l", "min"), c=("c", "last"))
        out.append(_pack(gg["o"].to_numpy(), gg["h"].to_numpy(), gg["l"].to_numpy(), gg["c"].to_numpy(), int(gg.index.min()) * 5, 5))
    return out


def equity(t: pd.DataFrame, days) -> list:
    s = t.groupby("date")["net"].sum().reindex(days, fill_value=0.0).cumsum()
    peak = np.maximum.accumulate(np.r_[0.0, s.to_numpy()])[1:]
    step = max(1, len(s) // 700)
    return [[d, round(float(v)), round(float(v - p))] for i, (d, v, p) in enumerate(zip(s.index, s.to_numpy(), peak)) if i % step == 0 or i == len(s) - 1]


def write_html(out_dir: Path, *, E: dict, B: dict, F, stock_days, fut_days, settings, rules, formulas, assumptions, data_rows, invest, extra) -> Path:
    S = {}
    for k, (num, short, name) in STRATS.items():
        t = B[(k, "base", 1.0)].sort_values(["date", "e"]).reset_index(drop=True)
        days = fut_days if k == "momo" else stock_days
        cd = candles_momo(t, F) if k == "momo" else candles_stocks(t, k)
        S[k] = {"num": num, "short": short, "name": name, **_clean(E[k]), "equity": equity(t, days), "trades": trade_rows(t, k), "candles": cd,
                "rules": rules[k], "invest": invest[k]}
    comb = pd.concat([B[(k, "base", 1.0)][["date", "net"]] for k in STRATS])
    comb_eq = equity(comb[comb["date"] >= CONFIG["data"]["start"]], stock_days)
    data = {"title": f"5 Intraday Strategies (Groww video) - {TITLE_SUFFIX}", "generated": datetime.now().strftime("%d %b %Y %H:%M"),
            "video": VIDEO, "data_range": [str(fut_days[0]), str(stock_days[-1])], "stock_range": [stock_days[0], stock_days[-1]],
            "capital": CONFIG["money"]["capital"], "settings": settings, "strats": S, "order": list(STRATS), "combined": comb_eq,
            "common_rules": rules["common"], "formulas": formulas, "assumptions": assumptions, "data_rows": data_rows, "trade_cols": TRADE_COLS,
            **_clean(extra)}
    css = re.search(r"<style>.*?</style>", (ASSETS / "report_template.html").read_text(encoding="utf-8"), re.S).group(0)
    tpl = viewer_assets.apply((ASSETS / "groww5_template.html").read_text(encoding="utf-8")).replace("__CSS__", css).replace("__TITLE__", "5 Intraday Strategies")
    js = (ASSETS / "chart.umd.min.js").read_text(encoding="utf-8")
    blob = json.dumps(_clean(data), separators=(",", ":"), default=str).replace("</", "<\\/")
    path = Path(out_dir) / "report.html"
    path.write_text(tpl.replace("__CHARTJS__", js).replace("__DATA__", blob), encoding="utf-8")
    return path
