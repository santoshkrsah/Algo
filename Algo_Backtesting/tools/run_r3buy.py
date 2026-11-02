"""R3 / S3 option BUYING backtest (BTST) on NSE daily option bhavcopy (expired contracts included), Nov 2024 - Oct 2026:
setups A / C / A+C x index / stocks / all x classic / camarilla x ATM / 1 / 2 OTM x gap filter x 11 next-day exits,
random-entry check (1,000 runs), 60% in-sample / 40% out-of-sample, splits; HTML report + Telegram.

    python tools/run_r3buy.py
    python tools/run_r3buy.py --no-telegram
"""
import argparse
import json
import sys
import tempfile
import time
import traceback
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

import pandas as pd  # noqa: E402

from backtest import r3buy as r  # noqa: E402
from backtest import r3buy_report as rep  # noqa: E402
from backtest.config import ROOT, result_dir  # noqa: E402
from backtest.notify import send, send_document  # noqa: E402

TAG_TRADING, TAG_TECH = "@ayushanand440", "@albert_max"
CFG = r.CFG
KN = ["ATM", "1 OTM", "2 OTM"]
SS = {"A": "A (premium > R3)", "C": "C (underlying R3/S3)", "AC": "A + C"}
UN = {"index": "Index", "stocks": "Stocks", "all": "Index + stocks"}
EXL = {e[0]: e[1] for e in r.EXITS}
SETTINGS = [["Data", "NSE daily option bhavcopy, expired contracts bhi"], ["Capital", "Rs 10,00,000 fixed, compounding nahi", 1], ["Risk / trade", "1% = Rs 10,000 (SL lagne pe)", 1],
            ["Qty", "pure lots; bina SL wale exit me 30% SL maan ke"], ["Limit", "din me max 2 trade = max 2 khule; premium max 10% capital; volume ka max 5%"],
            ["Slippage", "1% premium, buy + sell dono", 1], ["Kharcha", "Rs 20/order, STT 0.1% sell (1 Apr 2026 se 0.15%), exchange, SEBI, stamp, GST", 1],
            ["Liquidity", "signal din 200+ trade, kal 50+, premium Rs 3+"], ["Entry", "din ka aakhri candle close (last price), BTST", 1],
            ["Gap filter", "underlying 0.25%+ gap signal ki direction me"], ["Check", "1,000 random entry; 60% in-sample / 40% out-of-sample"]]
RULES = [
    "Data: NSE ka daily option bhavcopy - har contract ka din ka Open, High, Low, Close, last price, volume, OI aur lot size. 5 Nov 2024 se 30 Oct 2026. "
    "Expired contracts bhi shamil. NIFTY, BANKNIFTY aur us din ke saare F&O stocks.",
    "Contract: signal ke din ke baad wali sabse paas ki expiry. Strike: underlying ke close ke sabse paas = ATM. CALL ke liye ek upar = 1 OTM, do upar = 2 OTM; "
    "PUT ke liye neeche wale. Teeno alag test kiye.",
    "Pivot kal ke High (H), Low (L), Close (C) se. Classic: P = (H + L + C) / 3, R3 = H + 2 x (P - L), S3 = L - 2 x (H - P). "
    "Camarilla: R3 = C + 1.1 x (H - L) / 4, S3 = C - 1.1 x (H - L) / 4. Dono alag test kiye.",
    "Setup A (option chart): CALL option ka premium din ke aakhri candle pe apne R3 ke upar band hua (R3 option ke apne kal ke H / L / C se) -> wahi CALL kharido.",
    "Setup C (underlying chart): NIFTY / BANKNIFTY / stock din ke aakhri candle pe apne R3 ke upar band -> CALL kharido; S3 ke neeche band -> PUT kharido.",
    "A + C: dono ke signal ek saath (ek hi list, din me max 2). Setup D (15-min, 60-min aur daily teeno level ke paar) daily data pe C jaisa hi hai, "
    "kyunki din ke aakhri 15-min, 60-min aur daily candle ka close ek hi hota hai.",
    "Gap filter (alag test): signal ke din underlying kal ke close se kam se kam 0.25% upar khula ho (CALL) ya 0.25% neeche (PUT).",
    "Signal din ke aakhri candle ke band hone pe (3:30) pata chalta hai. Entry usi close pe = option ka last traded price. "
    "(Bhavcopy ka 'close' aakhri 30 min ka average hai - us pe asli me nahi khareed sakte, isliye last price liya.)",
    "Liquidity: signal ke din us contract me kam se kam 200 trade, kal 50 trade, premium Rs 3 ya zyada. Contract agle din bhi trade hua ho.",
    "Exit agle trading din (BTST), 11 tarike alag: (1) agle din open 9:15, (2) agle din close 3:30, (3-8) SL 20% ya 30% + target 1:1 / 1:2 / 1:3, "
    "(9-11) SL = signal din ka low (kam se kam 5% neeche) + target 1:1 / 1:2 / 1:3. SL / target na lage to agle din close.",
    "Agle din open hi SL ke neeche (ya target ke upar) khula -> exit open pe. Din me SL aur target dono ki range aayi -> SL pehle maana (sabse bura case).",
    "Paisa: capital Rs 10,00,000 fixed (profit dobara nahi lagaya, compounding nahi). Har trade me risk capital ka 1% = Rs 10,000: "
    "lots = 10,000 / ((entry - SL) x lot size), neeche round. Jin exits me SL nahi (open / close) unme qty 30% SL maan ke. "
    "Ek trade me premium max 10% capital aur us din ke volume ka max 5%. 1 lot bhi na ho sake to trade nahi.",
    "Din me max 2 trade = max 2 khule trade (kal wale aaj band ho jaate hain). Zyada signal ho to jinka us din Rs turnover sabse zyada. "
    "Ek contract pe din me ek hi signal, isliye same level pe dobara entry nahi.",
    "Kharcha: Rs 20 har order, STT sell pe 0.1% (1 Apr 2026 se 0.15%), exchange 0.03503%, SEBI 0.0001%, stamp 0.003% buy pe, GST 18% "
    "(brokerage + exchange + SEBI pe). Slippage premium ka 1%, buy aur sell dono pe.",
    "Check 1: har setting ko 1,000 random entry se milaya (wahi strike, expiry, liquidity, qty aur exit - bas din aur contract random, R3 / S3 ki shart nahi). "
    "Check 2: pehle 60% din pe best setting chuni, baaki 40% din (out-of-sample) pe bina badle chalayi.",
]
NOT_TESTED = [
    "Intraday option candles (5 / 15 / 30 / 60 min) expired contracts ke data me NAHI hain (Fyers sirf chalu contracts deta hai). Isliye ye nahi ho saka - "
    "aur nakli data se bhara bhi nahi:",
    "Setup A me 'din ke beech ka koi bhi 30-min / 60-min candle' (sirf din ka aakhri candle test hua).",
    "Setup B (pehli 15-min candle 9:15-9:30 R3 ke upar) - option premium ka 9:30 ka price chahiye.",
    "Setup C / D din ke beech (koi bhi 15-min candle) aur 'touch' wala version - signal ke samay ka option price chahiye.",
    "Exit 9:30 / 10:30 / 3:15 agle din, 'candle close wapas R3 ke neeche' wala SL aur 'pichhle 15-min low' wala trailing SL.",
    "Bid-ask spread ka data nahi hai, isliye spread filter nahi; uski jagah slippage 1% (aur 0.5% / 2% bhi check kiya).",
]


def faisla(g: pd.DataFrame, chosen: pd.DataFrame, rob: dict, sens: list, uncond: list, fill: dict) -> list[str]:
    """Plain-Hinglish verdict, built only from the numbers."""
    ok = g[g.all_trades >= 30]
    by_exit = ok.groupby("exit").agg(n=("all_net", "size"), pos=("all_net", lambda s: (s > 0).mean() * 100),
                                     oos_pos=("oos_net", lambda s: (s > 0).mean() * 100), rnd=("rand_pct", lambda s: (s >= 95).mean() * 100))
    op = by_exit.loc["open"]
    rest = by_exit.drop(index="open")
    good = chosen[(chosen.oos_net > 0) & (chosen.rand_pct >= 95)]
    bad = chosen[~chosen.index.isin(good.index)]
    win_lo, win_hi = chosen.all_win_pct.min(), chosen.all_win_pct.max()
    out = [
        f"<b>{len(g):,} setting</b> try ki (3 setup x 3 jagah x 2 pivot x 3 strike x gap on/off x 11 exit). Kharche ke baad poore samay me sirf "
        f"<b>{(ok.all_net > 0).mean() * 100:.0f}%</b> profit me, aur out-of-sample (aakhri 40%) me sirf <b>{rob['oos_pos_pct']:.0f}%</b>. "
        f"In-sample me jo profit me thi unme se sirf {rob['stay_pos_pct']:.0f}% baad me bhi profit me rahi.",
        f"Jo exit din bhar rukte hain (agle din close, ya SL / target) wo lagbhag sab loss me hain: {len(rest)} exit tarikon me average sirf "
        f"<b>{rest.pos.mean():.0f}%</b> setting profit me. Sirf <b>'agle din open (9:15)'</b> pe bechna baar baar kaam karta hai: "
        f"{op.pos:.0f}% setting profit me, {op.oos_pos:.0f}% out-of-sample me bhi, {op.rnd:.0f}% random entry se saaf behtar.",
    ]
    for c in good.itertuples():
        out.append(f"Paas: <b>{SS[c.setup]}, {UN[c.univ]}, {c.pivot}, {KN[c.k]}, {'sirf gap din' if c.gap else 'har din'}, {EXL[c.exit]}</b> - "
                   f"in-sample pe chuni, out-of-sample {rs(c.oos_net)} (PF {c.oos_pf or 0:.2f}, {c.oos_trades} trade), random se {c.rand_pct or 0:.0f}% behtar.")
    if len(bad):
        out.append("Fail (in-sample ki best setting out-of-sample me loss ya random jaisi): "
                   + "; ".join(f"{SS[c.setup]} {UN[c.univ]} ({rs(c.oos_net)})" for c in bad.itertuples()) + ".")
    stress = [s for s in sens if s[2].startswith("Slippage 2")]
    if stress:
        out.append("Slippage 2% (dugna) pe: " + "; ".join(f"{SS[s[0][0]]} {UN[s[0][1]]} {rs(s[5])} (PF {s[6] or 0:.2f})" for s in stress)
                   + ". Index wale tik jaate hain, stocks wale nahi - stock options ka spread subah zyada hota hai.")
    out.append(f"'Loss bahut kam hote hain' wala daawa sach NAHI: in-sample se chuni settings me bhi win rate {win_lo:.0f}% - {win_hi:.0f}% hai, "
               f"yaani lagbhag <b>{100 - win_hi:.0f}-{100 - win_lo:.0f}% trade loss</b> me. Profit bade jeet se aata hai, kam loss se nahi.")
    out.append("Dhyan do: camarilla ke R3 / S3 itne paas hote hain ki index pe lagbhag 70% din signal aata hai. Ye asal me 'din ke strong close ki taraf "
               "option kharido, agle subah 9:15 pe becho' (raat bhar ka momentum) hai, R3 'breakout' kam. Classic R3 / S3 door hai, signal kam aur result kamzor.")
    idx = next((x for x in fill["rows"] if x[0] == "Index"), None)
    if idx:
        out.append(f"9:15 pe bechna kitna sach hai: asli 1-min data ({fill['days'][2]} din) me index option ka 9:16-9:20 average 9:15 open se "
                   f"average {idx[2]:+.1f}% tha (median {idx[3]:+.1f}%), yaani open ka price nakli uchhal nahi tha. Par ye sirf 2 mahine ka data hai, "
                   "aur asli me 9:15 ka pehla fill mushkil hota hai - chhote size pe paper / live test zaroori.")
    out.append("Agla kadam: intraday (Setup B, 9:30 / 10:30 exit) ke liye expired contracts ka 1-min option data chahiye - wo is data me nahi hai.")
    return out


def rs(v) -> str:
    return ("-" if v < 0 else "+") + f"Rs {abs(v):,.0f}"


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--name", default="r3s3_optbuy_2024_2026")
    ap.add_argument("--no-telegram", action="store_true")
    ap.add_argument("--cache", type=Path, default=Path(tempfile.gettempdir()) / "r3buy_options.parquet", help="parsed bhavcopy cache (rebuilt if missing)")
    a = ap.parse_args()
    tell = (lambda msg: None) if a.no_telegram else send
    out = result_dir(a.name)
    t0 = time.time()
    log = lambda m: print(f"[{(time.time() - t0) / 60:5.1f} min] {m}", flush=True)  # noqa: E731
    tell("R3 / S3 OPTION BUYING BACKTEST STARTED (BTST, NSE daily bhavcopy Nov 2024 - Oct 2026, expired contracts bhi). Setup A / C / A+C, "
         "index + stocks, classic + camarilla, ATM / 1 / 2 OTM, gap filter, 11 exit, 1,000 random check, 60/40 out-of-sample.")
    try:
        d = r.load_options(CFG, cache=a.cache)
        log(f"options {len(d):,} rows")
        und = r.load_underlying(CFG, d.symbol.unique())
        pool, counts = r.build_pool(d, und, r.load_vix(CFG), CFG)
        log(f"pool {len(pool):,} contract-days {counts}")
        g, picks = r.grid(pool, CFG, progress=log)
        g.to_csv(out / "grid.csv", index=False)
        chosen = r.choose_in_sample(g, CFG)
        rob = r.robustness(g, CFG)
        tell(f"R3/S3: grid poora ({len(g):,} setting). Ab random / out-of-sample / report.")
        sens = r.sensitivity(pool, [(tuple(getattr(c, k) for k in r.COMBO), c.exit) for c in chosen.itertuples()], CFG)
        log("sensitivity done")
        uncond, fill = r.unconditional(pool), r.open_fill_check()
        cut = r.split_day(pool, CFG["is_frac"])
        days = sorted(pool.date.unique())
        logs = [r.trade_log(picks[tuple(getattr(c, k) for k in r.COMBO)], c.setup, c.pivot, c.exit, CFG).assign(combination=f"{c.setup}|{c.univ}|{c.pivot}|{KN[c.k]}|{'gap' if c.gap else 'har din'}|{c.exit}")
                for c in chosen.itertuples()]
        pd.concat(logs).to_csv(out / "trade_log.csv", index=False)
        # trades in the report: setups A / C x index / stocks (A+C and "all" only as numbers in the grid)
        rows, runs, meta = rep.trade_table(picks, lambda c: c[0] in ("A", "C") and c[1] in ("index", "stocks"))
        series = rep.series_from_options(d, rep.contract_series(pool, rows, meta["cols"]))
        main_rows = chosen[chosen.setup.isin(["A", "C"]) & chosen.univ.isin(["index", "stocks"])]
        b = main_rows.loc[main_rows.is_net.idxmax()]
        base = f"{b.setup}|{b.univ}|{b.pivot}|{b.k}|{int(b.gap)}|{b.exit}"
        cols, grid_rows = rep.grid_rows(g)
        sens_rows = [[f"{SS[s[0][0]]}, {UN[s[0][1]]}, {s[0][2]}, {KN[s[0][3]]}, {'gap' if s[0][4] else 'har din'}, {EXL[s[1]]}"] + s[2:] for s in sens]
        rob_text = [
            f"{rob['combos']:,} setting me kam se kam {CFG['min_is_trades']} in-sample aur 10 out-of-sample trade the. In-sample me {rob['is_pos_pct']:.0f}% profit me, "
            f"out-of-sample me {rob['oos_pos_pct']:.0f}%.",
            f"In-sample me profit wali settings me se {rob['stay_pos_pct']:.0f}% hi out-of-sample me profit me rahi. Rank correlation (in-sample vs out-of-sample "
            f"average P&L) = {rob['rank_corr']:.2f} (1 = poora tikta hai, 0 = koi rishta nahi).",
            f"{rob['beat_random_pct']:.0f}% settings random entry se 95%+ behtar; lagbhag sab 'agle din open' exit wali.",
            "Warning: itni settings me se best chunne pe kuch sirf kismat se achhi dikhti hain. Bharosa sirf us result pe karo jo (1) out-of-sample me bhi profit me ho, "
            "(2) random se behtar ho, aur (3) aas paas ki settings (dusra strike / pivot) me bhi chale - ek akeli setting pe nahi.",
        ]
        data = {
            "settings": SETTINGS, "rules": RULES, "not_tested": NOT_TESTED, "notes": NOTES, "formulas": FORMULAS, "cost_rates": COST_RATES,
            "data_info": [
                ["Option data", f"NSE bhavcopy (stock options + NIFTY / BANKNIFTY), {len(days)} trading din ({days[0]} se {days[-1]}), {pool.symbol.nunique()} underlying, "
                                f"{len(d):,} contract-din padhe (2 sabse paas wali expiry, strike underlying ke 12% andar)"],
                ["Candidate contract-din", f"{counts['contract_days']:,} (ATM / 1 / 2 OTM, sabse paas expiry) -> liquid {counts['liquid']:,} -> agle din trade na hone se chhode "
                                           f"{counts['no_next_day']:,} -> {len(pool):,}. Underlying data nahi mila (C ke liye): {counts['no_underlying']:,}."],
                ["Underlying", "Stocks: fast store daily (split / bonus adjusted). NIFTY / BANKNIFTY: index 1-min candles se daily. India VIX: 1-min se din ka close."],
                ["Short sessions", "Muhurat trading (1 Nov 2024, 21 Oct 2025, ~1 ghanta) chhode - unke candle 'kal' ke pivot ke liye theek nahi."],
                ["Stock list", "Har din ki bhavcopy me jo F&O stocks the wahi - baad me F&O se bahar hue stocks bhi shamil (survivorship bias nahi)."],
                ["In-sample / out-of-sample", f"{days[0]} se {days[days.index(cut) - 1]} (60%) / {cut} se {days[-1]} (40%)"],
            ],
            "faisla": faisla(g, chosen, rob, sens, uncond, fill), "robust_text": rob_text,
            "capital": CFG["capital"], "slip": CFG["slip"], "risk_pct": CFG["risk_pct"], "sizing_sl": CFG["sizing_sl"], "min_stop": CFG["min_stop"],
            "max_premium_pct": CFG["max_premium_pct"], "max_vol_share": CFG["max_vol_share"], "max_per_day": CFG["max_per_day"], "gap_min": CFG["gap_min"],
            "min_is_trades": CFG["min_is_trades"], **{k: CFG[k] for k in ("brokerage", "exch", "sebi", "stamp_buy", "gst", "stt_sell", "stt_sell_new", "stt_new_from")},
            "cut": cut, "cut_prev": days[days.index(cut) - 1], "data_range": [days[0], days[-1]],
            "data_note": "NSE daily option bhavcopy (expired contracts bhi); signal din 5 Nov 2024 se, exit 30 Oct 2026 tak",
            "grid_cols": cols, "grid": grid_rows, "chosen": chosen[r.COMBO + ["exit", "is_trades", "is_net", "is_pf", "oos_trades", "oos_net", "oos_pf", "oos_win_pct", "rand_pct", "all_win_pct"]].to_dict("records"),
            "sens": sens_rows, "uncond": uncond, "fill": fill, "trade_cols": meta["cols"], "trades": rows, "runs": runs, "series": series, "base": base,
        }
        rep.write_html(out / "report.html", data)
        mb = (out / "report.html").stat().st_size / 1e6
        log(f"report {mb:.1f} MB, {len(rows):,} unique trades, {len(series):,} contracts")
    except Exception:
        tell(f"{TAG_TECH} R3/S3 option buying backtest FAILED:\n{traceback.format_exc()[-1500:]}")
        raise
    site = json.loads((ROOT / "site" / "site.json").read_text(encoding="utf-8"))["url"]
    num = out.name.split("_", 1)[0]
    lines = [f"{SS[c.setup]} {UN[c.univ]}: {c.pivot} {KN[c.k]} {'gap' if c.gap else ''} {EXL[c.exit]} | in-sample {rs(c.is_net)} | "
             f"OUT-OF-SAMPLE {rs(c.oos_net)} PF {c.oos_pf or 0:.2f} | win {c.all_win_pct:.0f}% | random se {c.rand_pct or 0:.0f}% behtar" for c in chosen.itertuples()]
    msg = (f"{TAG_TRADING} R3 / S3 OPTION BUYING BACKTEST DONE ({(time.time() - t0) / 60:.1f} min). Capital Rs 10 L fixed, risk 1% / trade, din me max 2, slippage 1%.\n"
           f"{len(g):,} setting: poore samay {(g[g.all_trades >= 30].all_net > 0).mean() * 100:.0f}% profit me, out-of-sample {rob['oos_pos_pct']:.0f}%.\n"
           "In-sample pe chuni best (har setup x jagah):\n" + "\n".join(lines)
           + "\nSirf 'agle din open 9:15' exit kaam karta hai; win rate ~45-50% (loss kam nahi). Intraday setups ke liye data nahi (report me Rules tab).\n"
           f"Website: {site}\nReport: {site}#report-{num}")
    tell(msg)
    print(msg)
    if not a.no_telegram:
        send_document(out / "report.html", "R3 / S3 option buying report (browser me kholo). Rules tab me rules, neeche Formulas.")
    print("done ->", out)
    return 0


NOTES = [
    "Video / source me exit, SL, target aur position size ki poori baat nahi thi, isliye tumhare diye grid ke saare tarike alag alag test kiye.",
    "'Agle din open' = 9:15 ka pehla trade (bhavcopy ka open), 9:30 nahi - daily data me 9:30 ka price nahi hota.",
    "'Agle din close' = agle din ka last price (~3:30), 3:15 nahi.",
    "'Signal din ka low' = option ka poore din ka low (aakhri 30 / 60-min candle ka low pata nahi). Agar wo entry se 5% se kam neeche ho to SL 5% neeche rakha.",
    "MAE (sabse bura price) agle din ke low se: target wale trade me low target ke baad bhi aa sakta hai, isliye ye sabse bura maana.",
    "Turnover = contracts x lot size x price. Din me 2 se zyada signal ho to sabse zyada turnover wale 2 (close pe pata hota hai, look-ahead nahi).",
    "Lot size har din ki bhavcopy se (jaise NIFTY 25 -> 75 -> 65, BANKNIFTY 15 -> 30 -> 35 -> 30).",
    "Setup C ke 5 stocks ka underlying data nahi mila (HFCL, ICICIPRULI, LTIM, PEL, TATAMOTORS - naam badle / demerge), unke C signal nahi bane; A me wo shamil hain.",
    "India VIX = signal din ka close. 'Expiry ka hafta' = signal din aur expiry ek hi calendar hafte me. NIFTY ki weekly expiry hai, isliye NIFTY ke lagbhag sab trade expiry hafte me.",
    "Random check me contract poore pool se barabar mauke se chuna; strategy din ke sabse zyada turnover wale chunti hai, isliye random wale thode kam liquid ho sakte hain.",
]
FORMULAS = [
    ["Kal ka H, L, C", "Option (Setup A) ya underlying (Setup C) ka pichhle trading din ka High, Low, Close"],
    ["Classic", "P = (H + L + C) / 3;   R3 = H + 2 x (P - L);   S3 = L - 2 x (H - P)"],
    ["Camarilla", "R3 = C + 1.1 x (H - L) / 4;   S3 = C - 1.1 x (H - L) / 4"],
    ["Signal A", "CALL ka aaj ka last price > option R3"],
    ["Signal C", "Underlying ka aaj ka close > R3 -> CALL;   close < S3 -> PUT"],
    ["Gap filter", "Gap % = (aaj ka open / kal ka close - 1) x 100;   CALL: Gap % >= 0.25;   PUT: Gap % <= -0.25"],
    ["Liquidity", "aaj ke trades >= 200  AUR  kal ke trades >= 50  AUR  price >= Rs 3  AUR  agle din trade hua"],
    ["ATM / OTM", "ATM = aaj traded strikes me |Strike - underlying close| sabse kam;  CALL: 1 OTM = agla upar, 2 OTM = do upar;  PUT: neeche"],
    ["Din me 2 trade", "Turnover = contracts x lot size x price;  sabse zyada turnover wale 2"],
    ["Entry", "E = signal din ka last price;   Buy = E x (1 + 0.01)"],
    ["SL", "SL 20%: E x 0.80;   SL 30%: E x 0.70;   din ka low: min(din ka low, E x 0.95);   time exit: qty ke liye E x 0.70 maana"],
    ["Target", "Target = E + R x (E - SL)   (R = 1, 2, 3)"],
    ["Lots", "Lots = neeche round( min( 10,000 / ((E - SL) x lot),   1,00,000 / (Buy x lot),   aaj ke contracts x 5% ) );   Qty = Lots x lot"],
    ["Exit (SL wale)", "Agle din Open <= SL -> Open;  Open >= Target -> Open;  Low <= SL -> SL;  High >= Target -> Target;  warna agle din close"],
    ["Sell", "Sell = Exit x (1 - 0.01)"],
    ["Gross P&L", "Qty x (Sell - Buy)"],
    ["Kharcha", "Rs 40 brokerage + STT (0.1%, 1 Apr 2026 se 0.15%) x sell value + 0.03503% exchange + 0.0001% SEBI (buy + sell) + 0.003% stamp (buy) + 18% GST (brokerage + exchange + SEBI)"],
    ["Net P&L", "Gross - Kharcha"],
    ["Return % (trade)", "Net / (Qty x Buy) x 100"],
    ["Win rate / loss rate", "Net > 0 wale trade / kul trade x 100;   loss rate = 100 - win rate"],
    ["Average jeet / haar", "Jeete trades ka average Net;  hare trades ka average Net"],
    ["Expectancy", "Kul Net / kul trade"],
    ["Profit factor", "Jeete trades ka jod / hare trades ke loss ka jod (bina minus)"],
    ["Max drawdown", "Roz ke Net ka jod = Kul;  Peak = max(0, ab tak ka sabse uncha Kul);  Max DD = (Kul - Peak) ka sabse bada minus"],
    ["Return on capital", "Kul Net / 10,00,000 x 100 (compounding nahi)"],
    ["Signal / mahina", "Din me 2 ki limit se pehle ke saare signal / mahine"],
    ["MAE", "Sabse bura price = (open exit: agle din open;  SL: SL;  baaki: agle din low);   MAE = min(0, sabse bura - E) x Qty"],
    ["Random check", "1,000 baar: utne hi random contract-din (same CE / PE ginti) pool se;   Random se behtar % = random average/trade < strategy average/trade wale runs / 1,000 x 100"],
    ["In / out-of-sample", "Trading din ka pehla 60% = in-sample (setting chuni), baaki 40% = out-of-sample (check)"],
]
COST_RATES = [
    ["Brokerage", "Rs 20 har order (buy + sell = Rs 40)", "poora samay (Fyers flat)"],
    ["STT", "0.1% sell premium pe", "31 Mar 2026 tak"],
    ["STT", "0.15% sell premium pe", "1 Apr 2026 se (Budget 2026)"],
    ["Exchange (NSE options)", "0.03503% premium pe, buy + sell", "poora samay"],
    ["SEBI fee", "Rs 10 / crore = 0.0001%", "poora samay"],
    ["Stamp duty", "0.003% buy pe", "poora samay"],
    ["GST", "18% (brokerage + exchange + SEBI) pe", "poora samay"],
    ["Slippage", "1% premium ka, buy aur sell dono (0.5% aur 2% bhi check)", "poora samay"],
    ["Expiry pe exercise STT", "nahi laga - har trade expiry se pehle market me bech diya", "-"],
]


if __name__ == "__main__":
    sys.exit(main())
