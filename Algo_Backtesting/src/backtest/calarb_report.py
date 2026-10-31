"""Interactive HTML report for the calendar spread gap study (vol5l look; switch between the daily and the 1m view)."""
import json
import re
from datetime import datetime
from pathlib import Path

import pandas as pd

from backtest.calarb import KINDS, STEP
from backtest.report import ASSETS
from backtest.vmu_report import _clean


def spec(heads, keys, fmts, rows, sort=(0, 1), q=None, filt=None, hl=False) -> dict:
    """One sortable table: headers, CSV keys, cell formats (''/int/n2/gap/pct/rs), rows, stock search col, filter col.
    hl: bold the gap cells that are same rate or 0.50+."""
    return {"h": heads, "k": keys, "f": fmts, "rows": rows, "sort": list(sort), "q": q, "filt": filt, "hl": hl}


def names(near: str, far: str) -> dict:
    return {"same": "Same rate", "up": f"{far} {STEP:.2f}+ upar", "down": f"{near} {STEP:.2f}+ upar", "small": "Chhota gap"}


def _num(df: pd.DataFrame) -> list:
    return [[_clean(x) for x in r] for r in df.astype(object).values.tolist()]


def view_data(v: dict) -> dict:
    """v: label, near, far, minute (bool), period, rows, eps, stocks, days, lots, verdict."""
    near, far, mins, st = v["near"], v["far"], v["minute"], v["stocks"].round(2)
    nm = names(near, far)
    unit = "minute" if mins else "din"
    lot = st.symbol.map(v["lots"])

    def cell(r, k):
        if not mins:
            return f"{r[k]} din ({r[k + '_pct']:.0f}%)"
        return f"{r[k]:,} min · {r[k + '_times']} baar · {r[k + '_days']} din"

    answer = [[r.symbol, int(lot[i]), int(r.obs)] + [cell(r, k) for k in KINDS] + [r["avg"], r["min"], r["max"]]
              for i, r in st.iterrows()]
    tot = {k: int(st[k].sum()) for k in KINDS}
    obs = int(st.obs.sum())

    def top(k):
        x = st[st[k] > 0].sort_values(k, ascending=False)
        return ", ".join(f"{r.symbol} {r[k]:,}" for _, r in x.iterrows()) or "kisi stock me nahi"

    kpis = [[nm["same"], f"{tot['same']:,} {unit}", top("same")],
            [f"{far} >= {near} + {STEP:.2f}", f"{tot['up']:,} {unit}", top("up")],
            [f"{near} >= {far} + {STEP:.2f}", f"{tot['down']:,} {unit}", top("down")],
            ["Kitna dekha", f"{obs:,} {'minute' if mins else 'stock-din'}",
             "jin minutes me dono contracts me trade hua" if mins else f"{st.symbol.nunique()} stocks x {int(st.days.max())} din"]]
    if mins:
        kpis[0][2] = f"{int(st.same_times.sum())} baar, {int((st.same > 0).sum())} stocks me | " + kpis[0][2]

    rows, days = v["rows"], v["days"]
    xs = rows.assign(x=rows.datetime.dt.strftime("%d %b %H:%M") if mins else pd.to_datetime(rows.date).dt.strftime("%d %b"))
    gaps = {s: xs[xs.symbol == s][["x", "gap", "close_near", "close_far"]].values.tolist() for s in st.symbol}
    bars = {"labels": st.symbol.tolist(), "series": [[nm[k], st[k + "_pct"].tolist(), st[k].tolist()] for k in KINDS]}

    if mins:
        e = v["eps"].copy()
        e["kind"] = e.kind.map(nm)
        e["start"], e["end"] = e.start.dt.strftime("%H:%M"), e.end.dt.strftime("%H:%M")
        cols = ["date", "symbol", "kind", "start", "end", "minutes", "span", "gap_start", "gap_best", "near_start", "far_start"]
        every = spec(["Date", "Stock", "Kya mila", "Shuru", "Khatam", "Minute (dono me trade)", "Kul samay (min)",
                      "Shuru me gap", "Sabse bada gap", f"{near} price (shuru)", f"{far} price (shuru)"],
                     cols, ["", "", "", "", "", "int", "int", "gap", "gap", "n2", "n2"], _num(e[cols]), (0, 1), 1, 2)
        dcols = ["date", "symbol", "obs", "same_times", "same", "up_times", "up", "down_times", "down", "min", "max", "avg"]
        dd = days.round(2)
        day = spec(["Date", "Stock", "Minute (dono me trade)", nm["same"] + " (baar)", nm["same"] + " (min)", nm["up"] + " (baar)",
                    nm["up"] + " (min)", nm["down"] + " (baar)", nm["down"] + " (min)", "Sabse kam gap", "Sabse zyada gap", "Average gap"],
                   dcols, ["", "", "int", "int", "int", "int", "int", "int", "int", "gap", "gap", "gap"], _num(dd[dcols]), (0, 1), 1)
        cell = days.same_times.astype(str) + " · " + days.up_times.astype(str) + " · " + days.down_times.astype(str)
        piv = days.assign(cell=cell).pivot(index="date", columns="symbol", values="cell").reindex(columns=st.symbol.tolist())
        piv = piv.fillna("-").reset_index()
        daypiv = spec(["Date"] + st.symbol.tolist(), list(piv.columns), [""] * len(piv.columns), _num(piv), (0, 1))
        labels = sorted(days.date.unique())
        daybars = {s: {"labels": pd.to_datetime(pd.Series(labels)).dt.strftime("%d %b").tolist(),
                       "series": [[nm[k], days[days.symbol == s].set_index("date")[k + "_times"].reindex(labels, fill_value=0).tolist()]
                                  for k in KINDS]} for s in st.symbol}
        scols = ["symbol", "obs", "days", "same", "same_times", "same_days", "up", "up_pct", "up_times", "up_days",
                 "down", "down_pct", "down_times", "down_days", "avg", "median", "min", "max", "lot", "rs_lot"]
        sh = ["Stock", "Minute dekhe", "Din", "Same (min)", "Same (baar)", "Same (din)", f"{far} upar (min)", f"{far} upar %",
              f"{far} upar (baar)", f"{far} upar (din)", f"{near} upar (min)", f"{near} upar %", f"{near} upar (baar)",
              f"{near} upar (din)", "Average gap", "Median gap", "Sabse kam", "Sabse zyada", "Lot size", "0.50 x lot (Rs)"]
        sf = ["", "int", "int", "int", "int", "int", "int", "pct", "int", "int", "int", "pct", "int", "int", "gap", "gap", "gap",
              "gap", "int", "rs"]
    else:
        daypiv = daybars = None
        r = rows.copy()
        r["kind"], r["kind_ltp"] = r.kind.map(nm), r.kind_ltp.map(nm)
        cols = ["date", "symbol", "close_near", "close_far", "gap", "kind", "ltp_near", "ltp_far", "gap_ltp", "kind_ltp",
                "trades_near", "trades_far"]
        every = spec(["Date", "Stock", f"{near} close", f"{far} close", "Gap (close)", "Kya mila (close)", f"{near} LTP",
                      f"{far} LTP", "Gap (LTP)", "Kya mila (LTP)", f"{near} trades", f"{far} trades"],
                     cols, ["", "", "n2", "n2", "gap", "", "n2", "n2", "gap", "", "int", "int"], _num(r[cols]), (0, 1), 1, 5)
        piv = rows.pivot(index="date", columns="symbol", values="gap")[st.symbol.tolist()].reset_index()
        day = spec(["Date"] + [f"{s} gap" for s in piv.columns[1:]], list(piv.columns), [""] + ["gap"] * (len(piv.columns) - 1),
                   _num(piv), (0, 1), hl=True)
        ltp = rows.groupby("symbol").kind_ltp.value_counts().unstack(fill_value=0).reindex(columns=KINDS, fill_value=0)
        st = st.join(ltp.add_suffix("_ltp"), on="symbol")
        scols = ["symbol", "obs", "same", "up", "up_pct", "down", "down_pct", "same_ltp", "up_ltp", "down_ltp", "avg", "median",
                 "min", "max", "far_trades", "lot", "rs_lot"]
        sh = ["Stock", "Din", "Same (din)", f"{far} upar (din)", f"{far} upar %", f"{near} upar (din)", f"{near} upar %",
              "LTP: same (din)", f"LTP: {far} upar", f"LTP: {near} upar", "Average gap", "Median gap", "Sabse kam", "Sabse zyada",
              f"{far} ke trades / din", "Lot size", "0.50 x lot (Rs)"]
        sf = ["", "int", "int", "int", "pct", "int", "pct", "int", "int", "int", "gap", "gap", "gap", "gap", "int", "int", "rs"]
        st["far_trades"] = st.symbol.map(rows.groupby("symbol").trades_far.mean().round())
    st["lot"] = st.symbol.map(v["lots"])
    st["rs_lot"] = st.lot * STEP
    stocks = spec(sh, scols, sf, _num(st[scols]), (0, 1), 0)
    return {"label": v["label"], "near": near, "far": far, "minute": mins, "period": v["period"], "verdict": v["verdict"],
            "kpis": kpis, "answer": answer, "gaps": gaps, "bars": bars, "every": every, "day": day, "stocks": stocks,
            "daypiv": daypiv, "daybars": daybars,
            "every_title": v["every_title"], "every_hint": v["every_hint"], "day_hint": v["day_hint"]}


def write_html(path, views: dict, rules: list[str], notes: list[str], check: dict, formulas: list[list], period: str):
    data = {"period": period, "rules": rules, "notes": notes, "check": check, "formulas": formulas, "step": STEP,
            "views": {k: view_data(v) for k, v in views.items()}, "generated": datetime.now().strftime("%d %b %Y %H:%M")}
    css = re.search(r"<style>.*?</style>", (ASSETS / "report_template.html").read_text(encoding="utf-8"), re.S).group(0)
    tpl = (ASSETS / "calarb_template.html").read_text(encoding="utf-8").replace("__CSS__", css)
    js = (ASSETS / "chart.umd.min.js").read_text(encoding="utf-8")
    blob = json.dumps(data, separators=(",", ":"), default=_clean).replace("</", "<\\/")
    Path(path).write_text(tpl.replace("__CHARTJS__", js).replace("__DATA__", blob), encoding="utf-8")
