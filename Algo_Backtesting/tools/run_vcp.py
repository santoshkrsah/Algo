"""VCP swing (Volatility Contraction Pattern, Minervini style) on the Nifty 500 proxy (AMFI Jun 2026 top 500 by market cap),
daily candles, Feb 2022 - Oct 2026: baseline with the market filter ON and OFF, one-at-a-time sensitivity, a 720-setting grid
fitted on the first ~60% (in-sample) and checked on the last ~40% (out-of-sample); report + trade log CSV + Telegram.

    python tools/run_vcp.py
    python tools/run_vcp.py --no-telegram

Needs the Nifty 500 index:  (Algo_Historical_Data) python -m fyers_data.download_reference index NSE:NIFTY500-INDEX NIFTY500 --start 2020-06-01
"""
import argparse
import itertools
import sys
import time
import traceback
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

import numpy as np  # noqa: E402
import pandas as pd  # noqa: E402

from backtest import vcp as V  # noqa: E402
from backtest.config import result_dir  # noqa: E402
from backtest.notify import send, send_document  # noqa: E402
from backtest.vcp_report import series_for, write_html  # noqa: E402

TAG_TRADING, TAG_TECH = "@ayushanand440", "@albert_max"
NAME = "vcp_swing"
TITLE = "VCP Swing (Volatility Contraction)"
P = V.Params()
W_ = V.with_params
TEST_START, SPLIT, END = "2022-02-24", "2024-12-01", V.END      # first day with 100 days of history; split = ~60% of the trading days
MIN_FIT_TRADES = 30

SCAN_F = ("vol_mult", "rng_mult", "close_top", "young_n", "ext_max", "universe", "min_value_cr", "min_hist", "stage_n")
WATCH_F = SCAN_F + ("watch_n", "contr", "ema_hold", "trigger", "buf", "gap_max", "rising_n", "mkt_filter")
PATH_F = WATCH_F + ("stop", "stop_pct", "entry_day_stop", "exit", "rr_part", "part", "leg_mult", "gain_day", "ext10", "max_days")

# one thing changed at a time; every row runs with the market filter ON and OFF. grp = which breakdown it belongs to
CHECKS = [
    ["Stop"],
    ("stop", "Stop 2%", dict(stop_pct=0.02)), ("stop", "Stop 2.5% (baseline)", {}), ("stop", "Stop 3%", dict(stop_pct=0.03)),
    ("stop", "Candle-low stop (trigger bar ka low)", dict(stop="candle")), ("stop", "Hybrid: 2.5% aur candle low me jo tight", dict(stop="hybrid")),
    ["Risk har trade"],
    ("risk", "Risk 0.5%", dict(risk=0.005)), ("risk", "Risk 0.75% (baseline)", {}), ("risk", "Risk 1%", dict(risk=0.01)),
    ["Volume multiple (expansion candle)"],
    ("vol", "Volume 2x", dict(vol_mult=2.0)), ("vol", "Volume 3x (baseline)", {}), ("vol", "Volume 4x", dict(vol_mult=4.0)),
    ["Watch din (base kitna lamba)"],
    ("watch", "Watch 10 din", dict(watch_n=10)), ("watch", "Watch 20 din (baseline)", {}), ("watch", "Watch 30 din", dict(watch_n=30)),
    ["Extension limit (50 EMA se kitna upar)"],
    ("ext", "Extension 10%", dict(ext_max=0.10)), ("ext", "Extension 15% (baseline)", {}), ("ext", "Extension 20%", dict(ext_max=0.20)),
    ["Exit variant"],
    ("exit", "A: 50% @ 2R, breakeven, 10 EMA trail (baseline)", {}), ("exit", "B: 50% @ 2R, breakeven, 20 EMA trail", dict(exit="B")),
    ("exit", "C: 1/3 at T1 / T2 / T3 (E range x 1/2/3)", dict(exit="C")), ("exit", "D: taakat me becho (8% din / 10 EMA se 10%)", dict(exit="D")),
    ["Entry ke variant"],
    ("entry", "Trigger = expansion candle ka high", dict(trigger="exp")), ("entry", "Gap limit 1%", dict(gap_max=0.01)),
    ("entry", "Base variant: 50 EMA ke upar rahe (20 EMA nahi)", dict(ema_hold=50)),
    ["Doosre checks (rules ka hissa nahi)"],
    ("other", "Entry din stop sirf close pe (stop-pehle niyam ke bina)", dict(entry_day_stop="close")),
    ("other", "Entry din 1-min data se (asli order: fill ke baad stop / target)", dict(entry_day_stop="1m")),
    ("other", "Slippage 0", dict(slip=0.0)), ("other", "Slippage 2x (0.1% har fill)", dict(slip=0.001)),
    ("other", "Compounding ON (booked profit se size)", dict(compound=True)), ("other", "Position cap 30% (asli risk 0.75% tak)", dict(max_pos_pct=0.30)),
    ("other", "Saare stocks (top 500 nahi), liquidity Rs 5 Cr", dict(universe="all")), ("other", "Liquidity Rs 10 Cr", dict(min_value_cr=10.0)),
]
GRID = dict(exit=["A", "B", "C", "D"], stop=[("fixed", 0.02), ("fixed", 0.025), ("fixed", 0.03), ("candle", 0.025), ("hybrid", 0.025)],
            vol_mult=[2.0, 3.0, 4.0], watch_n=[10, 20, 30], ema_hold=[20, 50], mkt_filter=[True, False])

SETTINGS_BASE = [["Stock list", "AMFI Jun 2026 ke top 500 market cap (Nifty 500 jaisa)", 1], ["Liquidity", "20 din avg traded value >= Rs 5 Cr"],
                 ["Data", "daily candle, split / bonus adjusted"], ["Capital", "Rs 10,00,000 fixed, compounding nahi", 1], ["Risk / trade", "0.75% (Rs 7,500)", 1],
                 ["Limit", "max 20% / stock, max 8 position, qty <= 5% avg volume, leverage nahi"], ["Slippage", "0.05% har fill", 1],
                 ["Kharcha", "STT 0.1% x2, exchange, SEBI, stamp, GST, DP (~0.23% round trip)"], ["Fit / verify", "Feb 2022 - Nov 2024 / Dec 2024 - Oct 2026"]]
RULES = [
    "Stock list: AMFI (30 Jun 2026) list me market cap ke hisaab se top 500 stocks (Nifty 500 jaisa; sabse chhota ~Rs 11,448 Cr). Us din tak ke 20 din ka average traded value Rs 5 Cr ya zyada. "
    "Daily candles, split / bonus adjusted. Test 24 Feb 2022 se 30 Oct 2026 (pehle 100 din indicators ke liye).",
    "Market filter: Nifty 500 ka close uske 50 EMA ke upar aur 50 EMA uske 200 EMA ke upar ho, tabhi naya trade. Har setup filter ON aur OFF dono pe chalaya.",
    "Expansion candle (din E): volume pichhle 50 din ke average ka 3 guna ya zyada; range (high - low) pichhle 20 din ki average range ka 1.5 guna ya zyada; hari candle (close > open), "
    "close din ki range ke upar wale 1/3 hisse me; pichhle 90 trading din me koi aur expansion candle nahi (young base); close 50 EMA se 15% se zyada upar nahi.",
    "Watchlist (contraction): E ke baad 20 trading din. Contraction bar = range E ki range ke 50% se kam, ya inside bar (high <= kal ka high aur low >= kal ka low). "
    "Contraction bars ka average volume 50 din ke average volume se kam ho. Watchlist se bahar: close 50 EMA ke neeche, close E ke low ke neeche, close 20 EMA ke neeche (tight), ya 20 din me trigger nahi.",
    "Entry: aakhri contraction / inside bar ke high se 0.1% upar buy-stop, sirf agle din ke liye (roz naya). Order tabhi jab us din close 20 aur 50 EMA dono ke upar ho aur dono EMA 5 din pehle se upar hon. "
    "Agle din open trigger se 2% se zyada upar = trade nahi (setup khatam); open trigger ke upar par 2% ke andar = open pe kharida; warna din me trigger chhua to trigger pe.",
    "Stop: entry se 2.5% neeche. Open stop ke neeche (gap down) = open pe becha. Ek candle me stop aur target dono = stop pehle maana. Entry wale din low stop tak gaya = stop.",
    "Size: qty = capital x 0.75% / (entry - stop). Par ek stock me max 20% capital, max 8 position ek saath, qty <= 50 din ke average volume ka 5%, sab positions milake capital tak. "
    "Ek din me kai entry = jiska volume multiple zyada, wo pehle. Capital Rs 10 lakh fixed (profit dobara nahi lagaya, compounding nahi).",
    "Exit A (main): +2R pe 50% becho aur stop entry price pe (breakeven, agle din se); uske baad close 10 EMA ke neeche = baaki becho. B: wahi, 20 EMA. "
    "C: trigger + 1x / 2x / 3x E ki range pe 1/3 - 1/3 - 1/3. D: ek din me 8% ya zyada badha, ya close 10 EMA se 10% upar = sab becho.",
    "Hamesha bahar: close 50 EMA ke neeche (setup fail) ya 20 trading din pure (time stop). Close wale saare exit agle din ke open pe (din t ke close pe signal, order t+1).",
    "Kharcha: STT 0.1% kharid aur bech dono pe, NSE 0.00307%, SEBI 0.0001%, stamp 0.015% (kharid), GST 18% (exchange + SEBI pe), DP Rs 15.34 + GST har bechne wale din; brokerage 0 (delivery). "
    "Slippage 0.05% har fill. Kul ~0.33% round trip.",
    "Fit / verify: 24 Feb 2022 - 30 Nov 2024 (~60%) pe setting chuni, 1 Dec 2024 - 30 Oct 2026 (~40%) sirf check. Chunne ka niyam pehle se tay: 720 setting me se fit hisse ka sabse zyada net "
    f"(kam se kam {MIN_FIT_TRADES} trade).",
]
NOTES = [
    "Data Oct 2021 se hi hai: 2018 aur 2020 (COVID crash) test nahi hue. Is period me 2022 (choppy / girawat), 2023-24 (tezi) aur Oct 2024 - Mar 2025 (girawat) hain.",
    "Stock list aaj (Jun 2026) ki hai: jo stocks beech me gire, delist hue ya chhote ho gaye, wo list me nahi (survivorship bias). Asli result isse kharab ho sakta hai.",
    "Nifty 500 ki asli point-in-time list nahi hai, isliye AMFI ke top 500 market cap liye. Nifty 500 index Fyers ke 1-min data se (din ka aakhri minute = close).",
    "'90 din' aur '20 din' = trading din. 'Late stage' = E se 91-250 trading din pehle bhi expansion candle tha; 'early' = nahi tha (2022 ke shuru me 250 din ka history nahi tha, isliye wahan 'early' zyada).",
    "Exit A/B: 10/20 EMA trailing sirf +2R pe 50% bechne ke baad chalu; breakeven stop agle din se. Usse pehle sirf stop, 50 EMA aur time stop.",
    "Entry wale din low stop tak gaya to stop maana, chahe low entry se pehle aaya ho (daily candle me order pata nahi; stop pehle niyam). Bina is niyam ka result 'Extra checks' me.",
    "2.5% stop par 0.75% risk ke liye 30% position chahiye, par 20% cap hai, isliye asli risk ~0.5% / trade. Cap 30% wala result 'Extra checks' me.",
    "Trigger ke 2% se zyada upar gap = setup khatam (dobara entry nahi). Paisa / position limit ki wajah se trade nahi liya to bhi setup khatam.",
    "Partial exit me qty neeche round (jaise 7 ka 50% = 3). Drawdown roz ke close pe khule trades ko mark karke (mark-to-market).",
    "Nifty 500 buy-and-hold price index hai (dividend ke bina, ~1-1.5% saal kam).",
    "Kharcha ke hisse broker ke hisaab se badal sakte hain (brokerage, DP charge) - apne broker se milao.",
]
FORMULAS = [
    ["EMA n", "EMA(aaj) = close x 2/(n+1) + EMA(kal) x (1 - 2/(n+1))   (n = 10, 20, 50; Nifty 500: 50, 200)"],
    ["Average volume", "avg50 = pichhle 50 din ke volume ka average (aaj ko chhod ke)"], ["Average range", "avg range20 = pichhle 20 din ke (high - low) ka average (aaj ko chhod ke)"],
    ["Inside bar", "high <= kal ka high aur low >= kal ka low"],
    ["Expansion candle", "volume >= 3 x avg50; (high - low) >= 1.5 x avg range20; close > open; close >= low + 2/3 x (high - low)"],
    ["Young base", "pichhle 90 trading din me expansion candle = 0"], ["Extension", "close / EMA50 - 1 <= 15%"], ["Liquidity", "average(close x volume, 20 din) >= Rs 5 Cr"],
    ["Contraction bar", "(high - low) < 50% x E ki range, ya inside bar; average(contraction bars ka volume) < 50 din ka average volume"],
    ["Market filter", "Nifty500 close > EMA50 aur EMA50 > EMA200"], ["Confirmation", "close > EMA20, close > EMA50, EMA20 > EMA20 (5 din pehle), EMA50 > EMA50 (5 din pehle)"],
    ["Trigger", "aakhri contraction bar ka high x 1.001"], ["Entry price", "open > trigger x 1.02: skip; open >= trigger: open; high >= trigger: trigger"],
    ["Entry fill", "entry price x (1 + 0.05%)"],
    ["Stop", "fixed = entry x (1 - 2.5%); candle = trigger bar ka low; hybrid = max(fixed, candle)"], ["R (har share)", "entry - stop"],
    ["Qty", "floor(min(capital x 0.75% / (entry fill - stop), capital x 20% / entry fill, khali cash / entry fill, 5% x avg50))"],
    ["Exit A / B", "high >= entry + 2R: 50% target pe (gap ho to open pe); stop = entry; phir close < EMA10 (A) / EMA20 (B): agle din open"],
    ["Exit C", "T1/T2/T3 = trigger + 1/2/3 x (E ka high - E ka low); har target pe qty ka 1/3"],
    ["Exit D", "close / kal ka close - 1 >= 8% ya close > 1.10 x EMA10: agle din open"],
    ["Hamesha", "close < EMA50 ya 20 din trade me: agle din open; open <= stop: open; low <= stop: stop"],
    ["Exit fill", "price x (1 - 0.05%)"],
    ["Kharcha", "STT 0.1% x (kharid + bech) + NSE 0.00307% x (kharid + bech) + SEBI 0.0001% x (kharid + bech) + stamp 0.015% x kharid + GST 18% x (NSE + SEBI) + Rs 15.34 x 1.18 har bechne wala din"],
    ["P&L", "sum(qty bechi x (exit fill - entry fill)) - kharcha"], ["R multiple", "net P&L / (qty x (entry fill - stop))"],
    ["Win rate", "net > 0 wale trade / kul trade x 100"], ["Average win / loss (R)", "jeete trades ka average R / hare trades ka average R"],
    ["Expectancy", "sab trades ka average R"], ["Profit factor", "jeete trades ka kul net / hare trades ka kul loss"],
    ["Equity", "Rs 10,00,000 + band trades ka net + khule trades ka (aaj ka close - entry fill) x qty"],
    ["Max drawdown", "min(equity / us din tak ka sabse uncha equity - 1)"], ["Total return", "net / Rs 10,00,000 x 100"],
    ["CAGR", "(aakhri equity / Rs 10,00,000)^(1 / saal) - 1"], ["Exposure", "average(lagaya paisa / equity) roz ka"], ["Market me time", "jin din kam se kam 1 position khuli / kul din"],
    ["3 din me stop", "stop se bahar aur din trade me <= 3 / kul trade"], ["Top 5% ka hissa", "sabse acche 5% trades ka net / kul net"],
    ["Nifty 500 buy-hold", "close(aakhri din) / close(pehla din) - 1; CAGR = (1 + return)^(1 / saal) - 1"],
]


class Runner:
    """Caches each stage on the parameters it depends on (scan -> watch -> paths -> portfolio)."""

    def __init__(self, D, I):
        self.D, self.I, self.c = D, I, {}

    def _get(self, stage, fields, q, fn):
        key = (stage,) + tuple(getattr(q, f) for f in fields)
        if key not in self.c:
            self.c[key] = fn()
        return self.c[key]

    def run(self, q):
        E = self._get("E", SCAN_F, q, lambda: V.scan(self.D, self.I, q))
        S = self._get("S", WATCH_F, q, lambda: V.watch(self.I, E, q))
        if q.entry_day_stop == "1m":
            V.entry_1m(self.D, self.I, S)
        X = self._get("X", PATH_F, q, lambda: V.paths(self.I, S, q))
        return self._get("T", tuple(q.__dataclass_fields__), q, lambda: V.portfolio(self.D, self.I, X, q))


def parts(R: Runner, q) -> dict:
    t, m = R.run(q)
    cal = R.D["cal"]
    end_is = cal[int(np.searchsorted(cal, SPLIT)) - 1]
    return {"all": V.stats(t, m, cal, q.capital, TEST_START, END, q.compound), "is": V.stats(t, m, cal, q.capital, TEST_START, end_is, q.compound),
            "oos": V.stats(t, m, cal, q.capital, SPLIT, END, q.compound)}


def check_row(grp, label, q, R) -> list:
    s = parts(R, q)
    a, i, o = s["all"], s["is"], s["oos"]
    return [label, "ON" if q.mkt_filter else "OFF", a["trades"], a["win_pct"], a["net"], a["ret_pct"], a["cagr"], a["pf"], a["exp_r"], a["max_dd_pct"],
            i["net"], i["pf"], o["net"], o["pf"], a["stop3_pct"], grp]


def stop_txt(q) -> str:
    return {"fixed": f"{q.stop_pct * 100:g}% fixed", "candle": "candle low (trigger bar)", "hybrid": f"hybrid ({q.stop_pct * 100:g}% / candle low, jo tight)"}[q.stop]


def exit_txt(q) -> str:
    return {"A": "A: 50% @ 2R, breakeven, 10 EMA trail", "B": "B: 50% @ 2R, breakeven, 20 EMA trail", "C": "C: 1/3 @ T1 / T2 / T3",
            "D": "D: 8% din ya 10 EMA se 10% upar"}[q.exit]


def label_of(q) -> str:
    return f"Exit {q.exit}, stop {stop_txt(q)}, volume {q.vol_mult:g}x, watch {q.watch_n} din, {'tight (20 EMA)' if q.ema_hold == 20 else 'base (50 EMA)'}, filter {'ON' if q.mkt_filter else 'OFF'}"


def chips_of(q) -> list:
    return [["Market filter", "ON (Nifty 500 > 50 EMA > 200 EMA)" if q.mkt_filter else "OFF", 1],
            ["Expansion", f"volume >= {q.vol_mult:g}x, range >= {q.rng_mult:g}x, upar 1/3, young base {q.young_n} din, 50 EMA se <= {q.ext_max * 100:g}%", 1],
            ["Watch", f"{q.watch_n} din, contraction < {q.contr * 100:g}% range, {'tight: 20 EMA' if q.ema_hold == 20 else 'base: 50 EMA'}"],
            ["Entry", ("contraction high" if q.trigger == "cb" else "expansion high") + f" + {q.buf * 100:g}%, gap > {q.gap_max * 100:g}% skip", 1],
            ["Stop", stop_txt(q), 1], ["Exit", exit_txt(q), 1], ["Time stop", f"{q.max_days} din, 50 EMA fail"]]


def inv_of(q, t: pd.DataFrame, m: dict) -> list:
    if t is None or t.empty:
        return [["Shuruaati capital", f"Rs {q.capital:,.0f}"], ["Trade", "koi nahi"]]
    T = len(m["dep"])
    n = np.zeros(T)
    for k, x in zip(t.k, t.x):
        n[k:x] += 1
    return [["Shuruaati capital", f"Rs {q.capital:,.0f} ({'compounding ON' if q.compound else 'fixed, compounding nahi'})"],
            ["Risk har trade", f"{q.risk * 100:g}% = Rs {q.capital * q.risk:,.0f} (cap ki wajah se asli average {t.risk_rs.mean() / q.capital * 100:.2f}%)"],
            ["Qty ka rule", "capital x risk / (entry fill - stop)"], ["Max ek stock me", f"{q.max_pos_pct * 100:g}% capital = Rs {q.capital * q.max_pos_pct:,.0f}; qty <= {q.adv_pct * 100:g}% avg volume"],
            ["Max khuli position", f"{q.max_open} (sabse zyada ek saath: {int(n.max())})"],
            ["Average / sabse badi position", f"Rs {t.notional.mean():,.0f} / Rs {t.notional.max():,.0f}"],
            ["Sabse zyada paisa ek din me laga", f"Rs {m['dep'].max():,.0f} ({m['dep'].max() / q.capital * 100:.0f}% capital)"]]


def trade_dicts(t: pd.DataFrame, key: str) -> list:
    if t is None or t.empty:
        return []
    keep = ["symbol", "exp_date", "signal_date", "entry_date", "exit_date", "vm", "rm", "dist50", "stage", "value_cr", "base_len", "n_cb", "mkt_t", "trigger_type", "trigger", "entry",
            "at_open", "entry_fill", "stop", "cb_date", "cb_high", "cb_low", "exp_high", "exp_low", "targets", "qty", "notional", "risk_rs", "exit", "exit_avg", "reason", "legs", "hold",
            "gross", "slip", "c_stt", "c_exch", "c_stamp", "c_gst", "c_dp", "costs", "net", "R"]
    x = t[keep].copy()
    num = x.select_dtypes("number").columns
    x[num] = x[num].round(4)
    x["targets"] = x.targets.map(lambda a: [round(float(v), 2) for v in a])
    out = x.astype(object).where(x.notna(), None).to_dict("records")
    for i, r in enumerate(out):
        r["id"] = f"{key}{i}"
    return out


LOG_COLS = {"entry_date": "date", "symbol": "stock", "exp_date": "expansion_date", "vm": "volume_multiple", "base_len": "base_length_days", "dist50": "dist_from_50ema_pct",
            "mkt_filter": "market_filter", "mkt_t": "market_filter_state_at_signal", "trigger_type": "trigger_type", "entry": "entry", "stop": "stop", "reason": "exit_reason",
            "R": "r_multiple", "hold": "holding_days", "costs": "costs", "net": "net_pnl"}


def trade_log(t: pd.DataFrame) -> pd.DataFrame:
    x = t.rename(columns=LOG_COLS)
    first = list(LOG_COLS.values())
    rest = [c for c in x.columns if c not in first and c not in ("i", "j", "k", "x", "targets")]
    x["legs"] = x.legs.map(lambda a: " | ".join(f"{d} {q} @ {px} ({why})" for d, q, px, why in a))
    return x[first + rest]


def run(args, status) -> int:
    t0 = time.time()
    out = result_dir(NAME)
    say = (lambda m: None) if args.no_telegram else send
    say(f"Run shuru: {TITLE}, Nifty 500 proxy (top 500 market cap), {TEST_START} se {END}. Baseline ON/OFF + {sum(len(c) > 1 for c in CHECKS)} checks x 2 + grid. Folder {out.name}.")
    status(f"Data load | {out.name}")
    D = V.load()
    I = V.indicators(D)
    R = Runner(D, I)
    cal = D["cal"]
    a, b = int(np.searchsorted(cal, TEST_START)), int(np.searchsorted(cal, END, side="right")) - 1
    print(f"load {time.time() - t0:.0f}s", flush=True)

    status("Sensitivity checks (filter ON + OFF)")
    checks = []
    for c in CHECKS:
        if len(c) == 1:
            checks.append(list(c))
            continue
        grp, label, kw = c
        for f in (True, False):
            checks.append(check_row(grp, label, W_(P, mkt_filter=f, **kw), R))
    print(f"checks {time.time() - t0:.0f}s", flush=True)

    status("Grid: 720 setting, fit (in-sample) pe chunna")
    grid = []
    keys = list(GRID)
    for n, combo in enumerate(itertools.product(*GRID.values())):
        kw = dict(zip(keys, combo))
        st, sp = kw.pop("stop")
        q = W_(P, stop=st, stop_pct=sp, **kw)
        s = parts(R, q)
        grid.append({"q": q, "label": label_of(q), "is": s["is"], "oos": s["oos"], "all": s["all"]})
        if n % 120 == 0:
            status(f"Grid {n}/720")
            print(f"grid {n} {time.time() - t0:.0f}s", flush=True)
    ok = [g for g in grid if g["is"]["trades"] >= MIN_FIT_TRADES]
    fit = max(ok, key=lambda g: g["is"]["net"])
    isn, oosn = np.array([g["is"]["net"] for g in grid]), np.array([g["oos"]["net"] for g in grid])
    rank_corr = float(pd.Series(isn).rank().corr(pd.Series(oosn).rank()))
    oos_rank = int((oosn > fit["oos"]["net"]).sum()) + 1
    fq = fit["q"]
    neigh = [g for g in grid if sum(getattr(g["q"], f) != getattr(fq, f) for f in ("exit", "stop", "stop_pct", "vol_mult", "watch_n", "ema_hold", "mkt_filter")) == 1]
    robust = {"n": len(grid), "is_pos": float((isn > 0).mean() * 100), "oos_pos": float((oosn > 0).mean() * 100), "both_pos": float(((isn > 0) & (oosn > 0)).mean() * 100),
              "is_pf_med": float(np.nanmedian([g["is"]["pf"] or np.nan for g in grid])), "oos_pf_med": float(np.nanmedian([g["oos"]["pf"] or np.nan for g in grid])),
              "rank_corr": rank_corr, "fit_label": fit["label"], "fit_is": fit["is"], "fit_oos": fit["oos"], "oos_rank": oos_rank,
              "neigh_n": len(neigh), "neigh_oos_pos": int(sum(g["oos"]["net"] > 0 for g in neigh)), "neigh_is_pos": int(sum(g["is"]["net"] > 0 for g in neigh)),
              "top": [[g["label"], g["is"]["trades"], g["is"]["net"], g["is"]["pf"], g["oos"]["trades"], g["oos"]["net"], g["oos"]["pf"]]
                      for g in sorted(ok, key=lambda g: -g["is"]["net"])[:15]],
              "best_oos": max(grid, key=lambda g: g["oos"]["net"])["label"]}
    print(f"grid done {time.time() - t0:.0f}s; fit {fit['label']} IS {fit['is']['net']:.0f} OOS {fit['oos']['net']:.0f}", flush=True)

    status("Views + report")
    views_def = [("main", "Baseline, filter ON", P), ("off", "Baseline, filter OFF", W_(P, mkt_filter=False)),
                 ("m1", "Baseline, entry din 1-min se (filter ON)", W_(P, entry_day_stop="1m")), ("exB", "Exit B (20 EMA), filter ON", W_(P, exit="B")),
                 ("exC", "Exit C (T1/T2/T3), filter ON", W_(P, exit="C")), ("exD", "Exit D (taakat me becho), filter ON", W_(P, exit="D")),
                 ("fit", "Fit pe chuna (IS best)", fq)]
    bh = V.buy_hold(D, P.capital, TEST_START, END)
    end_is = cal[int(np.searchsorted(cal, SPLIT)) - 1]
    bh_is, bh_oos = V.buy_hold(D, P.capital, TEST_START, end_is), V.buy_hold(D, P.capital, SPLIT, END)
    vdata, all_trades = [], []
    for key, label, q in views_def:
        t, m = R.run(q)
        s = parts(R, q)
        eq = (q.capital + m["pnl"][a:b + 1]).round(0).tolist()
        sub = f"{label} | {exit_txt(q)}, stop {stop_txt(q)}, volume {q.vol_mult:g}x, watch {q.watch_n} din"
        vdata.append({"key": key, "label": label if key != "fit" else f"Fit pe chuna: exit {fq.exit}, {stop_txt(fq)}, vol {fq.vol_mult:g}x, filter {'ON' if fq.mkt_filter else 'OFF'}",
                      "sub": sub, "chips": chips_of(q), "inv": inv_of(q, t, m), "stats": s, "eq": eq, "trades": trade_dicts(t, key), "compound": q.compound})
        all_trades.append(t)
        if t is not None and len(t):
            trade_log(t).to_csv(out / f"trades_{key}.csv", index=False)
    S0 = R._get("S", WATCH_F, P, lambda: None)
    t_main = R.run(P)[0]
    funnel = S0.why.replace("", "entry mili").value_counts().to_dict()
    data_info = [["Stock data", f"Daily OHLCV (fast store, split / bonus adjusted), {cal[0]} se {cal[-1]}; {len(D['syms'])} stocks, top 500 = market cap >= Rs {D['top500_min_cr']:,.0f} Cr"],
                 ["Nifty 500", f"Fyers 1-min index (Jun 2020 se) -> daily; filter ON {I['mkt'][a:b + 1].mean() * 100:.0f}% din"],
                 ["Expansion candles (baseline)", f"{len(S0)}: " + ", ".join(f"{k} {v}" for k, v in funnel.items())],
                 ["Trade (baseline)", f"{(S0.outcome == 'entry').sum()} entry signal -> {len(t_main)} trade (baaki paisa / 8 position limit / same stock)"],
                 ["Survivorship", "stock list Jun 2026 ki (aaj ke bade stocks); delisted / gire hue stocks nahi"],
                 ["Grid", f"{len(grid)} setting (exit 4 x stop 5 x volume 3 x watch 3 x EMA 2 x filter 2), {len(ok)} me fit hisse me {MIN_FIT_TRADES}+ trade"]]
    blob = {"title": TITLE, "capital": P.capital, "data_range": [TEST_START, END], "split": SPLIT, "settings": SETTINGS_BASE, "views": vdata, "checks": checks, "robust": robust,
            "rules": RULES, "notes": NOTES, "formulas": FORMULAS, "data_info": data_info, "series": series_for(D, all_trades), "dates": cal[a:b + 1],
            "bh": (P.capital * D["idx"].close.iloc[a:b + 1] / D["idx"].close.iloc[a]).round(0).tolist(), "bh_stats": {"all": bh, "is": bh_is, "oos": bh_oos},
            "data_note": "Daily stock candles (fast store, adjusted) + Nifty 500 index (Fyers)"}
    path = write_html(out / "report.html", blob)
    pd.DataFrame([c for c in checks if len(c) > 1], columns=["check", "filter", "trades", "win_pct", "net", "ret_pct", "cagr", "pf", "exp_r", "max_dd_pct", "is_net", "is_pf",
                                                              "oos_net", "oos_pf", "stop3_pct", "group"]).to_csv(out / "checks.csv", index=False)
    pd.DataFrame([{"setting": g["label"], **{f"is_{k}": g["is"][k] for k in ("trades", "net", "pf", "exp_r", "max_dd_pct")},
                   **{f"oos_{k}": g["oos"][k] for k in ("trades", "net", "pf", "exp_r", "max_dd_pct")}} for g in grid]).to_csv(out / "grid.csv", index=False)

    # ---- summary
    def line(lb, s):
        return (f"{lb}: {s['trades']} trade, win {s['win_pct'] or 0:.0f}%, net Rs {s['net']:,.0f} ({s['ret_pct']:.1f}%), CAGR {s['cagr']:.1f}%, PF {s['pf'] or 0:.2f}, "
                f"expectancy {s['exp_r'] or 0:.2f}R, max DD {s['max_dd_pct']:.1f}%")
    sm, so = vdata[0]["stats"], vdata[1]["stats"]
    fs = vdata[-1]["stats"]
    lines = [line("Baseline ON (poora)", sm["all"]), line("  fit", sm["is"]), line("  verify", sm["oos"]), line("Baseline OFF (poora)", so["all"]),
             line(f"Fit pe chuna [{fit['label']}] fit", fs["is"]), line("  verify", fs["oos"]),
             f"Nifty 500 buy-hold: {bh['ret_pct']:.1f}% (CAGR {bh['cagr']:.1f}%, max DD {bh['max_dd_pct']:.1f}%); fit {bh_is['ret_pct']:.1f}%, verify {bh_oos['ret_pct']:.1f}%",
             f"Grid: {robust['n']} setting, fit me profit {robust['is_pos']:.0f}%, verify me profit {robust['oos_pos']:.0f}%, dono me {robust['both_pos']:.0f}%, rank corr {rank_corr:.2f}, "
             f"chuna setting verify me rank {oos_rank}/{len(grid)}; padosi {robust['neigh_oos_pos']}/{robust['neigh_n']} verify me profit"]
    summary = "\n".join(lines + ["", "Checks:"] + [c[0] if len(c) == 1 else f"  {c[0]} [{c[1]}]: {c[2]} trade, net Rs {c[4]:,.0f}, PF {c[7] or 0:.2f}, exp {c[8] or 0:.2f}R, DD {c[9]:.1f}%, "
                                                   f"fit Rs {c[10]:,.0f}, verify Rs {c[12]:,.0f}" for c in checks])
    (out / "summary.txt").write_text(summary, encoding="utf-8")
    print(summary, flush=True)
    msg = (f"{TITLE} ka result {TAG_TRADING}\n" + "\n".join(lines[:4] + lines[4:]) +
           f"\nStop 3 din me: {sm['all']['stop3_pct']:.0f}% trade. Capital Rs 10L fixed, compounding nahi, risk 0.75%, kharcha + slippage (~0.33% round trip) chalu.")
    if not args.no_telegram:
        send(msg)
        send_document(path, f"{TITLE} report ({out.name})")
    print(f"done {time.time() - t0:.0f}s -> {out}", flush=True)
    return 0


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--no-telegram", action="store_true")
    ap.add_argument("--status-file")
    args = ap.parse_args()
    status = (lambda s: Path(args.status_file).write_text(s + "\n", encoding="utf-8")) if args.status_file else (lambda s: None)
    try:
        return run(args, status)
    except Exception:
        err = traceback.format_exc()
        print(err, flush=True)
        if not args.no_telegram:
            send(f"{TITLE} run me error {TAG_TECH}:\n{err[-1500:]}")
        return 1


if __name__ == "__main__":
    sys.exit(main())
