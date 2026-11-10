"""Module B: moving-average crossover (positional, daily and 1-hour bars).

Grid 1 (per slice = timeframe x MA type x version x exit): fast x slow lengths, neighbours = next length up / down.
Grid 2 (the chosen slice, stop / target exits only): stop % x target %.
Index futures + stock futures (continuous, own roll), 2018-01 .. 2026-11.
"""
import numpy as np
import pandas as pd

from backtest.podcast import engine, indicators as ind
from backtest.podcast.config import CONFIG

KEY = "B"
NAME = "MA crossover"
FOLDER = "podcast_b_ma_cross"
P = CONFIG["modules"]["B"]
EXIT_TXT = {"sltp": "SL / target", "cross": "ulta cross pe exit + reverse", "sltp_eod": "SL / target + 15:15 exit"}


def slices() -> list[dict]:
    out = []
    for tf in P["tfs"]:
        for ma in P["ma_types"]:
            for v in P["versions"]:
                for ex in P["exits"]:
                    if ex == "sltp_eod" and tf == "1D":
                        continue
                    out.append({"tf": tf, "ma": ma, "version": v, "exit": ex})
    return out


def grid1(sl: dict) -> tuple[list, list]:
    cells = [{"fast": f, "slow": s, "stop": P["stop_pct"], "target": P["target_pct"]} for f in P["grid_fast"] for s in P["grid_slow"] if f < s]
    return ["fast", "slow"], cells


def grid2(sl: dict, chosen: dict) -> tuple[list, list] | None:
    if sl["exit"] == "cross":
        return None
    cells = [{"fast": chosen["fast"], "slow": chosen["slow"], "stop": s, "target": t} for s in P["grid_stop"] for t in P["grid_target"] if t > s]
    return ["stop", "target"], cells


def label(sl: dict, p: dict) -> str:
    base = f"{sl['tf']} {sl['ma']} {p['fast']}/{p['slow']} v{sl['version']} {EXIT_TXT[sl['exit']]}"
    return base + (f" {p['stop']:g}%/{p['target']:g}%" if sl["exit"] != "cross" else "")


def _mas(b, kind: str, f: int, s: int, cache: dict):
    k1, k2 = (b.name, kind, f), (b.name, kind, s)
    if k1 not in cache:
        cache[k1] = ind.ma(kind, b.c, f)
    if k2 not in cache:
        cache[k2] = ind.ma(kind, b.c, s)
    return cache[k1], cache[k2]


def trades(bars: dict, sl: dict, p: dict, slip_mult: float = 1.0, cache: dict | None = None) -> list:
    cache = {} if cache is None else cache
    out = []
    for b in bars.values():
        f, s = _mas(b, sl["ma"], p["fast"], p["slow"], cache)
        out += engine.run_b(b, f, s, sl["version"], sl["exit"], p["stop"], p["target"], engine.slip_array(b, slip_mult),
                            CONFIG["eod_exit"], P["cross_risk_pct"])
    return out


def random_trades(bars: dict, sl: dict, p: dict, rule, rng: np.random.Generator) -> list:
    """Same number of trades per instrument as the rule, random entry bar and side, same exits: stop / target (+15:15)
    or, for 'cross' exits, a holding time drawn from the rule's own holding times."""
    out = []
    eod = sl["exit"] == "sltp_eod"
    for name, b in bars.items():
        r = rule[rule["inst"] == name]
        if not len(r):
            continue
        warm = p["slow"]
        ok = np.arange(warm, b.n - 1)
        if eod:
            ok = ok[b.minute[ok + 1] < CONFIG["eod_exit"]]
        es = rng.choice(ok, size=len(r)) + 1
        sides = rng.choice([-1, 1], size=len(r))
        holds = r["bars"].to_numpy()
        slip = engine.slip_array(b)
        for e, side in zip(es, sides):
            px = b.o[e] + side * slip[e]
            raw = px - b.off[e]
            if sl["exit"] == "cross":
                t = engine.new_trade(b, side, e - 1, e, b.o[e], slip, np.nan, np.nan, risk_pts=raw * P["cross_risk_pct"] / 100.0)
                x = min(b.n - 1, e + int(rng.choice(holds)) - 1)
                engine.add_rolls(t, b, e, x, 1.0, slip)
                engine.close_leg(t, b, x, b.o[x] if x > e else b.c[x], 1.0, "random hold", slip, when="open" if x > e else "bar")
                out.append(t)
                continue
            stop = px - side * raw * p["stop"] / 100.0
            target = px + side * raw * p["target"] / 100.0
            t = engine.new_trade(b, side, e - 1, e, b.o[e], slip, stop, target)
            end, eod_bar = b.n, -1
            if eod:
                day_last = b.last[b.di[e]]
                cand = np.nonzero(b.minute[e:day_last + 1] >= CONFIG["eod_exit"])[0]
                eod_bar = e + int(cand[0]) if len(cand) else -1
                end = eod_bar if eod_bar >= 0 else day_last + 1
            j, lvl, why = engine.walk(b.o, b.h, b.l, e, end, side, stop, target)
            if j < 0:
                if eod:
                    j, lvl, why = (eod_bar, b.o[eod_bar], "15:15 exit") if eod_bar >= 0 else (end - 1, b.c[end - 1], "day close")
                else:
                    j, lvl, why = b.n - 1, b.c[b.n - 1], "end of data"
            engine.add_rolls(t, b, e, j, 1.0, slip)
            engine.close_leg(t, b, j, lvl, 1.0, why, slip)
            out.append(t)
    return out


def long_history(sl: dict, p: dict) -> dict | None:
    """Same setting on the NIFTY 50 and Bank Nifty INDEX daily candles from 2005 (signal check only: an index cannot be
    traded; charges and slippage as for futures). Only for daily slices (1-hour data starts Dec 2017)."""
    if sl["tf"] != "1D":
        return None
    from backtest.podcast import data, evaluate, portfolio
    bars = {n: data.bars(n, "1D", kind="index", start=CONFIG["data"]["long_start"]) for n in ("NIFTY50", "BANKNIFTY")}
    df = engine.frame(trades(bars, sl, p), bars)
    if not len(df):
        return None
    acc = portfolio.apply(df)
    tk = acc[acc["taken"]]
    days = np.unique(np.concatenate([b.days for b in bars.values()]))
    dp = portfolio.daily_equity(tk, bars, days)
    pre = tk[pd.to_datetime(tk["entry_dt"]) < "2018-01-01"]
    y = evaluate.yearly(tk, dp, CONFIG["capital"])
    return {"period": f"{str(days[0])} .. {str(days[-1])}", "full": {**evaluate.trade_stats(tk), **evaluate.equity_stats(dp, CONFIG["capital"])},
            "before_2018": evaluate.trade_stats(pre), "yearly": y[["year", "net", "return_pct", "trades", "pf"]].to_dict("records")}


# ------------------------------------------------------------------------------------------- report texts (Hinglish)
def rules(sl: dict, p: dict, split: str) -> list[str]:
    ver = ("Version 1 (sirf cross): fast MA slow MA ko neeche se upar kaate to BUY, upar se neeche kaate to SELL (short)."
           if sl["version"] == 1 else
           "Version 2 (state): koi position nahi hai aur fast > slow to BUY, fast < slow to SELL; stop / target ke baad agli candle pe dobara entry ho sakti hai.")
    ex = {"sltp": f"Exit: stop-loss entry price ka {p['stop']:g}% aur target {p['target']:g}% (short me ulta). Ulta cross pe exit nahi.",
          "sltp_eod": f"Exit: stop-loss {p['stop']:g}%, target {p['target']:g}%, aur 15:15 ki candle khulte hi sab band (intraday). 15:15 wali candle pe nayi entry nahi.",
          "cross": "Exit: ulta cross hote hi agli candle ke open pe exit aur ulti side me nayi entry (hamesha market me). Stop / target nahi; size aise jaise stop 1% ho."}[sl["exit"]]
    return [
        f"Instruments: NIFTY aur BANKNIFTY futures + 10 bade stocks ke futures ({', '.join(CONFIG['modules']['B']['instruments'][2:])}). Stocks me short sirf futures me ho sakta hai, isliye futures.",
        f"Candle: {'daily' if sl['tf'] == '1D' else '1 ghante (60 min, 09:15 se)'}. Period 2018 se 9 Nov 2026. Pehla 60% (2018 - {split} se pehle) = settings chunne ke liye, baaki 40% = test (out-of-sample).",
        f"Moving average: {sl['ma']} {p['fast']} (fast) aur {sl['ma']} {p['slow']} (slow), candle ke close pe.",
        ver,
        "Signal candle ke close pe, order agli candle ke open pe (aage ka data kabhi nahi dekha).",
        ex,
        "Stop aur target ek hi candle me lage to stop maana. Candle stop ke paar khule to us open pe exit (bura price).",
        "Futures ka roll: expiry se 3 trading din pehle agle contract me; position ho to purana band + naya shuru (2 order ka kharcha + slippage). Roll ka gap P&L me nahi.",
        "Size: stop lage to capital ka 0.25% (Rs 2,500) jaye; quantity = risk / (entry - stop) units me. Ek position max 5x capital.",
        "Din ka loss limit: din ka band hua loss Rs 5,000 (0.5%) ho jaye to us din nayi trade nahi. Profit compound nahi hota.",
    ]


def setting_chips(sl: dict, p: dict) -> list:
    return [["Candle", "daily" if sl["tf"] == "1D" else "1 ghanta", 1], ["MA", f"{sl['ma']} {p['fast']} / {p['slow']}", 1],
            ["Version", "1 (sirf cross)" if sl["version"] == 1 else "2 (state, re-entry)", 1], ["Exit", EXIT_TXT[sl["exit"]], 1],
            ["Stop / target", f"{p['stop']:g}% / {p['target']:g}% of entry" if sl["exit"] != "cross" else "nahi (size 1% stop jaisa)", 1],
            ["Signal", "candle close pe, entry agli candle ke open pe", 0]]


def formulas(sl: dict, p: dict) -> list:
    f = [["Moving average", "SMA(n) = pichhle n close ka average; EMA(n) = EMA_kal + 2/(n+1) x (close - EMA_kal), pehla EMA = SMA(n)"],
         ["Cross (version 1)", "BUY: fast[t] > slow[t] aur fast[t-1] <= slow[t-1];  SELL: fast[t] < slow[t] aur fast[t-1] >= slow[t-1]"],
         ["State (version 2)", "position nahi hai: fast[t] > slow[t] -> BUY, fast[t] < slow[t] -> SELL"],
         ["Entry fill", "agli candle ka open + side x slippage   (side: long +1, short -1)"],
         ["Stop / target", "stop = entry - side x asli_entry x stop%;  target = entry + side x asli_entry x target%"],
         ["Exit fill", "stop / target level - side x slippage; candle stop ke paar khule to us candle ka open"],
         ["Slippage", "NIFTY 1.5 pt, BANKNIFTY 3 pt, stock futures 0.03% x price, har fill pe"],
         ["Qty", "floor( min( capital x 0.25% / |entry - stop| , 5 x capital / asli_entry ) )  units"],
         ["Futures roll", "expiry se 3 trading din pehle agla contract; purane hisse = price + (naya close - purana close) roll se pehle din; position ho to roll pe 2 order + 2 slippage"],
         ["Gross P&L", "qty x side x (exit fill - entry fill) - roll slippage"],
         ["Charges", "brokerage Rs 20/order + STT sell (0.01% / 0.0125% Apr23 / 0.02% Oct24 / 0.05% Apr26) + NSE 0.00173% + SEBI 0.0001% + stamp buy 0.002% + GST 18% (brokerage+NSE+SEBI)"],
         ["Net P&L", "gross - charges"],
         ["R multiple", "net / (qty x |entry - stop|)"],
         ["Win rate", "net > 0 wali trades / kul trades"],
         ["Avg win / avg loss, risk-reward", "profit trades ka average / loss trades ka average;  RR = avg win / |avg loss|"],
         ["Expectancy", "kul net / trades (Rs aur R me)"],
         ["Profit factor", "sab profit ka jod / sab loss ka jod (net)"],
         ["Return %", "net / Rs 10 lakh x 100 (compounding nahi)"],
         ["CAGR", "(aakhri equity / 10 lakh)^(365 / din) - 1"],
         ["Max drawdown", "max( (peak equity - equity) / peak equity ); equity roz ke close pe (khuli position close pe mark)"],
         ["Time drawdown", "do equity high ke beech sabse lamba samay (trading din, mahine)"],
         ["Best 5% share", "sabse acchi 5% trades ka net / kul net"],
         ["Din ka loss limit", "us din band hui trades ka net <= -Rs 5,000 -> us din nayi trade nahi"],
         ["Kill switch", "realised equity <= peak x 0.95 -> 20 trading din nayi trade nahi, phir peak = us waqt ki equity"],
         ["Plateau", "setting + har padosi (ek step upar / neeche) ka fit PF > 1 -> PLATEAU; plateau score = inme sabse kam PF"],
         ["Random control", "1,000 baar: wahi instrument, wahi trade count, random candle + random side, wahi exit; p = (random >= rule + 1) / (1,000 + 1)"]]
    return f


def podcast_rows(grid) -> list:
    g = grid[(grid["grid"] == 1) & grid.apply(lambda r: (r["fast"], r["slow"]) in [tuple(x) for x in P["pairs"]], axis=1)]
    out = []
    for r in g.itertuples(index=False):
        sl = {"tf": r.tf, "ma": r.ma, "version": r.version, "exit": r.exit}
        out.append({"label": label(sl, {"fast": r.fast, "slow": r.slow, "stop": r.stop, "target": r.target}), "is_trades": r.is_trades, "is_pf": r.is_pf,
                    "is_exp_R": r.is_exp_R, "oos_trades": r.oos_trades, "oos_pf": r.oos_pf, "oos_exp_R": r.oos_exp_R, "oos_net": r.oos_net, "status": r.status})
    return out


def quick():
    """Small grid for a code check (tools/run_podcast.py --quick)."""
    P.update({"tfs": ["1D"], "ma_types": ["EMA"], "versions": [1], "exits": ["sltp", "cross"], "grid_fast": [15, 20, 25], "grid_slow": [45, 50, 55],
              "grid_stop": [0.9, 1.0, 1.1], "grid_target": [1.8, 2.0, 2.2]})
