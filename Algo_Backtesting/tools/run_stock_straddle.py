"""Report 10 ('Straddle + Risk Layer (2021 - 2026 retest)') ke rules 10 stocks ke OPTIONS pe: RELIANCE, SBIN, HDFCBANK, ICICIBANK, TATASTEEL, MARUTI,
APOLLOHOSP, ITC, BSE, LT. Har stock alag backtest (alag Rs 30 lakh), period 1 Nov 2021 - 3 Nov 2026. Engine = backtest.straddle.Sim (same as report 10),
stock ka market: backtest/straddle_stock.py (NSE stock option bhavcopy + Fyers 1-min stock price, Black-76 model).

    python tools/run_stock_straddle.py
    python tools/run_stock_straddle.py --no-telegram [--symbols RELIANCE,SBIN] [--workers 4]
"""
import argparse
import multiprocessing as mp
import sys
import time
import traceback
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import pandas as pd  # noqa: E402

import run_straddle as R  # noqa: E402
import run_straddle_risk as RR  # noqa: E402
from backtest import straddle as S  # noqa: E402
from backtest import straddle_stock as T  # noqa: E402
from backtest.config import result_dir  # noqa: E402
from backtest.notify import send, send_document  # noqa: E402
from backtest.straddle_report import check_row, index_series, premium_series, write_html  # noqa: E402

TAG_TRADING, TAG_TECH = "@ayushanand440", "@albert_max"
NAME = "stock_straddle_risk_2021_2026"
TITLE = "Stock Options Straddle + Risk Layer (2021 - 2026): 10 stocks"
SYMS = ["RELIANCE", "SBIN", "HDFCBANK", "ICICIBANK", "TATASTEEL", "MARUTI", "APOLLOHOSP", "ITC", "BSE", "LT"]
LOAD_FROM, START, END, SPLIT = "2021-10-25", "2021-11-01", "2026-11-03", "2024-11-01"
BASE = RR.BASE                                    # report 10 main test: straddle + risk layer (leg SL 75%, day loss 1.5%, trailing 50%), 9:20 entry, 3:15 exit
CORE = RR.CORE
EXTRA = {"core": ("Sirf straddle (risk layer band)", CORE), "slip2": ("Slippage double (option 1%)", S.with_params(BASE, slip_opt=0.01))}


def _hm(t: int) -> str:
    m = 555 + int(t)
    return f"{m // 60}:{m % 60:02d}"


def work(a) -> dict:
    """All variants of one stock through the same days."""
    sym, LOAD_FROM, START, END = a
    t0 = time.time()
    mk = T.StockMarket(sym, LOAD_FROM, END)
    variants = {"main": BASE, **{k: v[1] for k, v in EXTRA.items()}}
    sims = {k: S.Sim(S.with_params(p, index=sym)) for k, p in variants.items()}
    days = mk.days(START, END)
    series, prem = {}, {}
    traded = []
    for i, day in enumerate(days):
        d = mk.day(day)
        for k, s in sims.items():
            s.step(d)
        r = sims["main"].log[-1]
        if r["traded"]:
            traded.append(day)
            if r["expiry"] in d.smiles:
                prem[f"{sym}|{day}|{r['expiry']}|{r['strike']:g}"] = premium_series(d, r["expiry"], float(r["strike"]))
        if i % 200 == 0:
            print(f"{sym} {i}/{len(days)} {time.time() - t0:.0f}s", flush=True)
    series = {f"{sym}|{d}": v for d, v in index_series(mk.bars, traded).items()}
    logs = {k: S.frame(s) for k, s in sims.items()}
    for k, f in logs.items():
        assert float((f[["pnl_core", "pnl_avg", "pnl_tac", "pnl_hedge"]].sum(axis=1) - f.gross).abs().max()) < 1.0, (sym, k)
    fac = {d: f for d, f in mk.factor.items() if abs(f - 1) > 0.02}
    return {"sym": sym, "logs": logs, "series": series, "prem": prem, "factor_days": len(fac), "factors": sorted(set(round(f, 2) for f in fac.values())),
            "first_chain": min(mk.chain), "step": T.S.STEP[sym], "secs": time.time() - t0}


# ------------------------------------------------------------------------------------------------ texts
def chips_of(sym: str, p: S.Params) -> list:
    return [["Stock", f"{sym} (NSE stock option, sabse paas wali expiry)", 1], ["Entry", f"{_hm(p.entry_t)} pe ATM straddle SELL", 1], ["Exit", "3:15 pe square-off (intraday)"],
            ["Strike adjust / Averaging / Tactical / Delta", "sab band"], ["Leg SL", "band" if p.leg_sl is None else f"{p.leg_sl * 100:g}%", 1],
            ["Din ka loss limit", "band" if p.day_sl is None else f"{p.day_sl * 100:g}% capital", 1], ["Trailing", "band" if p.trail is None else f"{p.trail * 100:g}% ke baad", 1],
            ["Stop confirm", f"{p.confirm} lagatar 1-min close"], ["Drawdown scaling / Pause", "band"],
            ["Capital", f"Rs {p.capital:,.0f} har stock alag (fixed, compounding nahi)", 1], ["Slippage", f"option {p.slip_opt * 100:g}% har fill", 1],
            ["Option price", "model: asli 1-min stock price + Black-76 (strike-wise IV, bhavcopy se)", 1]]


def inv_of(p: S.Params) -> list:
    return [["Shuruaati capital", f"Rs {p.capital:,.0f} (fixed, compounding nahi)"], ["Margin me lagaya", f"{p.deploy * 100:.0f}% = Rs {p.capital * p.deploy:,.0f} (baaki buffer)"],
            ["Ek straddle ka margin", f"{p.margin_pct * 100:g}% x lot size x stock price (SPAN ka andaza)"], ["Lots", "margin budget / ek straddle ka margin, neeche round, kam se kam 1 lot"],
            ["Din ka loss limit", "band" if p.day_sl is None else f"{p.day_sl * 100:g}% = Rs {p.day_sl * p.capital:,.0f} (kill switch)"],
            ["Leg SL", "band" if p.leg_sl is None else f"premium {p.leg_sl * 100:g}% upar"], ["Slippage", f"option {p.slip_opt * 100:g}% har fill"]]


SETTINGS = [["Report", "report 10 (Straddle + Risk Layer 2021 - 2026 retest) ke rules 10 stocks ke options pe", 1],
            ["Stocks", "RELIANCE, SBIN, HDFCBANK, ICICIBANK, TATASTEEL, MARUTI, APOLLOHOSP, ITC, BSE (Ltd), LT (L&T) - har stock alag test", 1],
            ["Period", "1 Nov 2021 se 3 Nov 2026 (fit = Nov 2024 se pehle, verify = Nov 2024 se); BSE Ltd ke options Nov 2024 se hi hain", 1],
            ["Capital", "Rs 30,00,000 har stock ke liye alag (fixed, compounding nahi)", 1], ["Margin", "1/3 capital (Rs 10 lakh), ek straddle ka margin = 10% x lot x stock price"],
            ["Base", "9:20 ATM straddle SELL, leg SL 75%, din ka loss limit 1.5%, trailing 50%, 3:15 square-off", 1],
            ["Kharcha", "Rs 20/order, STT 4 slab (0.05% se 0.15%), exchange 0.03503% premium pe, stamp, GST", 1], ["Slippage", "option 0.5% har fill", 1],
            ["Option price", "model: asli 1-min stock price + Black-76 (strike-wise IV, NSE bhavcopy se)", 1],
            ["Expiry", "sabse paas wali (monthly; jis din expiry hai us din bhi); us din usable option data na ho to trade nahi"]]

LIVE_RULES = [
    "## Live trade : Rule No 1",
    "## Stock selection",
    "Watchlist : fixed 10 stocks (backtest wale): RELIANCE, SBIN, HDFCBANK, ICICIBANK, TATASTEEL, MARUTI, APOLLOHOSP, ITC, BSE, LT (har stock ka apna straddle, apna alag hisaab)",
    "Instrument : us stock ka Option (CE + PE), sabse paas wali expiry (stock options monthly; expiry wale din bhi wahi expiry)",
    "Min price range : koi nahi (sirf stock F&O me ho)",
    "Min avg volume / liquidity filter : ATM CE aur PE dono me bid-ask tight aur volume ho, nahi to us din us stock ko skip (Telegram pe batao)  [backtest me bid-ask data nahi, sirf volume / trade-count check laga]",
    "Min volatility (ATR % ya gap %) : koi filter nahi (gap / VIX pause band hai, roz trade)",
    "## Timing",
    "Timeframe: 1m (stop ke liye 1-min candle close dekhna)",
    "Market open: 09:15",
    "Entry window: sirf 09:20 (ek hi entry)",
    "No new trade after: 09:20 (averaging / dobara entry / tactical buy band)",
    "Square-off time: 15:15",
    "## Entry",
    "Indicator + period : koi indicator nahi, time based",
    "Condition : 09:20 pe stock ke price ke sabse paas wala strike (ATM) lo, usi strike ka CE aur PE dono sell karo",
    "Side : short straddle (CE sell + PE sell, same strike, same lots)",
    "## Exit",
    "Stop loss (leg SL): kisi leg ka premium entry se 75% upar (>= 1.75 x entry), 2 lagatar 1-min close -> sirf wo leg buy karke band. Dusra leg chalta rahe. Band leg dobara nahi becha jaata",
    "Target: fixed target nahi (trailing + 15:15 exit se band hota hai)",
    "Trailing: trigger = profit becha hua premium (Rs) ka 50% ; uske baad profit apne peak ka aadha reh jaye (2 lagatar 1-min close) -> sab band",
    "Day loss exit: din ka P&L (band leg + khule leg) capital ke -1.5% par ya neeche, 2 lagatar 1-min close -> sab band",
    "Partial exit: nahi (50% quantity wala rule nahi)",
    "Time exit: 15:15 pe sab close",
    "## Risk / sizing",
    "Total capital aur leverage : Rs 30 lakh capital, usme se sirf 1/3 (Rs 10 lakh) margin me, baaki 2/3 buffer",
    "Risk per trade: direct % nahi, qty margin se nikalti hai; worst din ka limit = capital ka 1.5% (Rs 45,000)",
    "Quantity = lots x lot size ; lots = floor( (capital x 1/3) / (10% x lot size x stock price) ), kam se kam 1 lot (example: 10,00,000 / (10% x 500 x 1,300) = 15 lots)",
    "Compounding : nahi, capital hamesha starting capital maano",
    "Max open positions : har stock ka ek straddle (CE+PE), ek time pe sab 10 stocks tak, har ek apne alag capital se",
    "Max trades per stock per day : 1 (leg SL ke baad band leg dobara nahi bechna)",
    "Max daily loss : 1.5% capital (kill switch), hit hone pe us stock ka din ka trading band",
    "## Execution",
    "Product: MIS (Intraday)",
    "Order type: Limit (LTP ke aas-paas) entry me; stop / exit ke liye bot candle close pe check karke order bhejta hai (exchange pe resting SL-M nahi, kyunki rule \"2 lagatar 1-min close\" hai)",
    "Slippage + brokerage/STT cost ka buffer : option premium ka 0.5% har fill + brokerage Rs 20/order (Angel One) + STT (option sell side) + exchange charge ~0.035% + SEBI + stamp + GST 18%",
]
RULES = LIVE_RULES if "--live-rules" in sys.argv else [
    "Ye report 10 ('Straddle + Risk Layer 2021 - 2026 retest') ke rules 10 stocks ke OPTIONS pe chalane ka test hai: RELIANCE, SBIN, HDFCBANK, ICICIBANK, TATASTEEL, MARUTI, APOLLOHOSP, ITC, BSE (Ltd), LT (L&T). "
    "Har stock alag test, alag Rs 30 lakh capital. Period 1 Nov 2021 se 3 Nov 2026 (BSE Ltd ke options ka data Nov 2024 se hai, isliye uska test chhota). Rules ke numbers report 10 ke hi hain, kuch badla nahi.",
    "Option: us stock ka option, har din sabse paas wali expiry (stock options monthly hote hain; jis din expiry hai us din bhi). Underlying stock ka 1-min price.",
    "Paisa: capital Rs 30 lakh, usme se 1/3 (Rs 10 lakh) margin me. Lots = Rs 10 lakh / (10% x lot size x stock price), neeche round, kam se kam 1 lot. Profit dobara invest nahi hota (compounding nahi).",
    "Entry: har trading din 9:20 pe ATM straddle SELL (CE aur PE, same strike, sabse paas wali expiry). Koi din chhoda nahi jaata (pause band).",
    "Exit: 3:15 pe jo bhi khula hai sab band (intraday, raat ko kuch nahi).",
    "Leg SL: kisi leg ka premium entry se 75% upar ho jaye (2 lagatar 1-min close) to sirf wo leg band. Doosra leg 3:15 tak ya agle stop tak chalta hai. Band leg dobara nahi becha jaata.",
    "Din ka loss limit (kill switch): din ka P&L (band leg + khule leg) capital ke 1.5% (Rs 45,000) ke loss tak ya usse neeche 2 lagatar 1-min close rahe to sab band.",
    "Trailing: profit becha hua premium (Rs) ka 50% ho jaye to trailing chalu. Uske baad profit apne sabse upar wale level ka aadha reh jaye (2 lagatar 1-min close) to sab band.",
    "Band layers (report 10 jaisa): strike adjustment, averaging, tactical buy, delta scalping, drawdown scaling, pause (gap / VIX / Budget).",
    "Kharcha: Rs 20 har order, STT 4 baar badla (Finance Act 2023, Budget 2024, Budget 2026), exchange charge 0.03503% premium pe, SEBI, stamp, GST 18%. Slippage option 0.5% har fill pe.",
]
NOTES = [
    "SABSE ZARURI: expired stock options ka din ke andar ka (1-min) price kahin nahi milta. Isliye option ka price model: asli 1-min stock price + Black-76, har strike ka IV us din ke asli NSE bhavcopy ke open (9:15) "
    "aur close (~3:15) se, beech me seedhi line (report 10 jaisa). Entry (9:20) aur exit (3:15) ka price asli ke paas hai, par din ke andar ka SL / kill switch timing approximate hai.",
    "Stock options NIFTY se kam liquid hain. Strike tabhi count hota hai jab us din uske CE aur PE dono me kaafi trade ho (nayi file: 100 trade, 8 Jul 2024 se pehle ki file me trade count nahi: 20 contracts). "
    "Jis din sabse paas wali expiry ka kaafi liquid smile nahi bana, us din trade nahi hua ('Contract nahi mila'); agli expiry me nahi jaate. Har stock ke ginti 'Data' card me.",
    "Stock options monthly expiry ke hain, isliye entry ke time expiry 0 se 30+ din door ho sakti hai. NIFTY (weekly, 0-5 din) se seedha compare mat karo: premium aur theta ka chaal alag hai.",
    "Stock ka price Fyers ka back-adjusted (split / bonus ke baad ka) hai, option strike asli price pe. Isliye har din ka asli price put-call parity se nikala (clean ratio 2x / 3x / 10x pe snap) aur 1-min price us factor se multiply kiya. "
    "Jaanch: bonus / split ke din (RELIANCE Oct 2024, HDFCBANK Aug 2025, TATASTEEL Jul 2022, BSE May 2025) factor sahi aaya.",
    "Lot size: nayi file (Jul 2024 se) me asli lot hai; purani file me nahi, to turnover se nikala (report 10 jaisa). Lot size time ke saath badalta hai (SEBI revision), wahi data se liya.",
    "Stock option me earnings / corporate action ka gap bada hota hai; 'sabse bura din' din ke loss limit (Rs 45,000) se kaafi bada ho sakta hai - stop 1-min close pe check hota hai, gap me fill door ho sakta hai. Dividend ex-date ka alag dhyaan nahi (forward me basis se jhalak).",
    "Margin 10% x contract value maana (asli SPAN alag; stock options me aksar zyada ~ 15-20%). Isliye lots zyada ho sakte hain asli se. Trading expert se confirm karna baaki hai.",
    "Ye rules report 10 (NIFTY) ke result dekh ke chune gaye the; stocks pe naya (unseen) setup hai. Kuch tune nahi kiya, koi number badla nahi.",
    "Survivorship: ye 10 stocks aaj ke bade F&O stocks hain (Ayush ne chune); 2021 me bhi F&O me the (BSE Ltd ke options sirf Nov 2024 se).",
]
FORMULAS = [f if f[0] != "Lots" else ["Lots", "max(1, floor(capital x 1/3 / (10% x lot size x stock price)))"] for f in RR.FORMULAS]
FORMULAS = [["Asli price factor", "put-call parity price (K + CE - PE) / Fyers adjusted close; clean ratio (2, 3, 10 ...) pe snap, 3% ke andar"]] + [
    ["Straddle premium", "CE price + PE price (same strike); ATM strike = round(price / step) x step  (step = us din ke listed strikes ka sabse common gap)"]] + [f for f in FORMULAS if f[0] != "Straddle premium"]


def finish_fail(msg: str):
    send(f"{TITLE} run me error {TAG_TECH}:\n{msg[-1500:]}")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--no-telegram", action="store_true")
    ap.add_argument("--status-file")
    ap.add_argument("--symbols")
    ap.add_argument("--workers", type=int, default=4)
    ap.add_argument("--name", default=NAME)
    ap.add_argument("--start")
    ap.add_argument("--end")
    ap.add_argument("--load-from")
    ap.add_argument("--title")
    ap.add_argument("--live-rules", action="store_true", help="Rules tab = Live Rule No 1 sections (Stock selection / Timing / Entry / Exit / Risk / Execution)")
    args = ap.parse_args()
    global START, END, SPLIT, TITLE
    if args.start:
        START, END, SPLIT = args.start, args.end or END, args.start          # short window: everything counts as verify
    LF = args.load_from or LOAD_FROM
    if args.title:
        TITLE = args.title
    if args.start:                                               # short-window texts
        per = f"{START} se {END} (sirf pichhla ~1 mahina, chhota sample: result pe zyada bharosa mat karna)"
        for lst in (SETTINGS, RULES):
            for i, x in enumerate(lst):
                if isinstance(x, list):
                    if x[0] == "Period":
                        x[1] = per
                else:
                    lst[i] = x.replace("Period 1 Nov 2021 se 3 Nov 2026 (BSE Ltd ke options ka data Nov 2024 se hai, isliye uska test chhota)", "Period " + per)
    status = (lambda s: Path(args.status_file).write_text(s + "\n", encoding="utf-8")) if args.status_file else (lambda s: None)
    say = (lambda m: None) if args.no_telegram else send
    syms = args.symbols.split(",") if args.symbols else SYMS
    t0 = time.time()
    try:
        out = result_dir(args.name)
        say(f"Run shuru: {TITLE}, {START} se {END}. {len(syms)} stocks, har ek 3 setup (main + sirf straddle + slippage x2). Folder {out.name}.")
        status(f"Simulation chal rahi hai ({len(syms)} stocks, {args.workers} parallel) | {out.name}")
        with mp.Pool(args.workers) as pool:
            res = {}
            for r in pool.imap_unordered(work, [(x, LF, START, END) for x in syms]):
                res[r["sym"]] = r
                status(f"{len(res)}/{len(syms)} stocks ho gaye ({r['sym']} {r['secs']:.0f}s)")
                print(f"done {r['sym']} {r['secs']:.0f}s", flush=True)
        cap = BASE.capital
        runs, labels, chips, subs, invs, series, prem = {}, {}, {}, {}, {}, {}, {}
        for s in syms:
            r = res[s]
            runs[s] = r["logs"]["main"]
            labels[s] = f"{s}: straddle + risk layer"
            p = S.with_params(BASE, index=s)
            chips[s], subs[s], invs[s] = chips_of(s, p), f"{s} option ATM straddle SELL 9:20 pe, 3:15 pe square-off | intraday option price model (asli 1-min stock price + Black-76)", inv_of(p)
            series.update(r["series"])
            prem.update(r["prem"])
        base_key = syms[0]
        checks = [[f"Har stock ka main test (straddle + risk layer), alag Rs 30 lakh"]]
        checks += [check_row(f"{s}", runs[s], cap, SPLIT) for s in syms]

        def combo(key):
            parts = [res[s]["logs"][key][["date", "net", "traded"]] for s in syms]
            return pd.concat(parts).groupby("date", as_index=False).agg(net=("net", "sum"), traded=("traded", "max"))
        allc = combo("main")
        checks += [["Sab stocks ek saath (har ek ka alag Rs 30 lakh, P&L ka jod; 'din' = stock-din)"], check_row(f"Sab {len(syms)} stocks ka jod (capital Rs {cap * len(syms) / 1e5:,.0f} lakh)", allc, cap * len(syms), SPLIT)]
        for k, (lb, _) in EXTRA.items():
            checks += [[f"Extra check: {lb}"]] + [check_row(f"{s}: {lb}", res[s]["logs"][k], cap, SPLIT) for s in syms] + [check_row(f"Sab {len(syms)} ka jod: {lb}", combo(k), cap * len(syms), SPLIT)]
        data_info = [["Stock 1-minute", "Fyers 1-min stock price (NSE, split / bonus ke liye back-adjusted; asli price factor se wapas), Oct 2021 se"],
                     ["Option data", "NSE stock option bhavcopy (purana format 2021 - 7 Jul 2024, UDiFF uske baad): har strike ka din ka Open / Close / trades, expired bhi"]]
        for s in syms:
            m = runs[s]
            lots = sorted(set(m[m.traded == 1].lot.dropna().astype(int)))
            sk = m[(m.traded == 0) & (m.exit == "Contract nahi mila")]
            data_info.append([s, f"option data {res[s]['first_chain']} se; {int(m.traded.sum())} trade din, {len(sk)} din 'Contract nahi mila', {len(m)} trading din; lot size: {', '.join(map(str, lots))}; "
                                 f"price factor (split / bonus) {res[s]['factor_days']} din ({', '.join(f'{x:g}x' for x in res[s]['factors']) or 'koi nahi'})"])
        write_html(out / "report.html", runs, labels, chips, subs, invs, base_key, cap, BASE.deploy, RULES, NOTES, checks, FORMULAS, data_info, series, prem, [START, END],
                   "NSE stock option bhavcopy (daily) + Fyers 1-min stock price; option ka din ke andar ka price model", SETTINGS, SPLIT, "0.5% option", title=TITLE,
                   check_hint="Har stock ka alag backtest (report 10 ke rules, kuch badla nahi). Extra checks: sirf straddle (risk layer band) aur slippage double. Net = poora period; Fit / Verify = do hisse.",
                   sub="NSE stock options &middot; report 10 ke rules (10 stocks)")
        for s in syms:
            runs[s].drop(columns=["events"]).to_csv(out / f"days_{s}.csv", index=False)
        lines = []
        for s in syms:
            st = S.stats(runs[s], cap)
            fit, ver = runs[s][runs[s].date < SPLIT].net.sum(), runs[s][runs[s].date >= SPLIT].net.sum()
            lines.append(f"{s}: {st['days']} din, win {st['win_pct'] or 0:.0f}%, net Rs {st['net']:,.0f} ({st['ret_pct']:.1f}%), PF {st['profit_factor'] or 0:.2f}, max DD Rs {st['max_dd']:,.0f}, "
                         f"sabse bura din Rs {st['worst']:,.0f} | fit Rs {fit:,.0f}, verify Rs {ver:,.0f}")
        sa = S.stats(allc, cap * len(syms))
        lines.append(f"SAB: net Rs {sa['net']:,.0f} ({sa['ret_pct']:.1f}%), PF {sa['profit_factor'] or 0:.2f}, max DD Rs {sa['max_dd']:,.0f}")
        (out / "summary.txt").write_text("\n".join(lines), encoding="utf-8")
        print("\n".join(lines), flush=True)
        print(f"done {time.time() - t0:.0f}s -> {out}", flush=True)
        return 0
    except Exception:
        err = traceback.format_exc()
        print(err, flush=True)
        if not args.no_telegram:
            finish_fail(err)
        return 1


if __name__ == "__main__":
    sys.exit(main())
