"""Report 10 ('Straddle + Risk Layer (2021 - 2026 retest)') ka retest: uske 'Extra checks (ye rules ka hissa NAHI hai)' ke 4 test, har ek alag
poora backtest (din ki table + trade viewer), NIFTY (1 Nov 2021 - 3 Nov 2026) aur SENSEX (BSE; jab se data hai: 15 May 2023 - 3 Nov 2026):

    1. Trailing band                              2. Stop ka confirm nahi (1 minute me touch pe)
    3. Strike adjustment chalu (ek leg 200 pt)    4. Drawdown scaling chalu

Base = report 10 ka main test (9:20 ATM straddle SELL + leg SL 75%, din ka loss limit 1.5%, trailing 50%). Engine, kharcha, slippage aur NIFTY ka
data report 10 jaisa (tools/run_straddle23.py); SENSEX ka option data BSE bhavcopy se (Algo_Historical_Data: fyers_data.download_bse_bhav).

    python tools/run_straddle_retest.py
    python tools/run_straddle_retest.py --no-telegram
"""
import argparse
import sys
import traceback
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

import run_straddle as R  # noqa: E402
import run_straddle23 as R23  # noqa: E402,F401   (sets R.LOAD_FROM / START / END / SPLIT = 2021-10-25, 2021-11-01, 2026-11-03, 2024-11-01)
import run_straddle_risk as RR  # noqa: E402
from backtest import straddle as S  # noqa: E402
from backtest.config import result_dir  # noqa: E402
from backtest.notify import send, send_document  # noqa: E402

TAG_TRADING, TAG_TECH = "@ayushanand440", "@albert_max"
NAME = "straddle_risk_retest"
TITLE = "Straddle + Risk Layer (2021 - 2026 retest) - 4 test: Trailing band, Stop touch (1 min), Strike adjustment (200 pt), Drawdown scaling | NIFTY + SENSEX"
P0, BASE = RR.P0, RR.BASE
SX_FROM = "2023-05-15"

TESTS = [("trail", "Trailing band", dict(trail=None)),
         ("stop", "Stop ka confirm nahi (1 minute me touch pe)", dict(confirm=1)),
         ("adj", "Strike adjustment chalu (ek leg 200 pt)", dict(adj_pts=P0.adj_pts)),
         ("dd", "Drawdown scaling chalu", dict(dd_steps=P0.dd_steps))]
VIEW = {"main": ("NIFTY: straddle + risk layer (base)", BASE), **{f"n_{k}": (f"NIFTY: {lb}", S.with_params(BASE, **kw)) for k, lb, kw in TESTS}}
BANK_VIEW = {"sx_main": ("SENSEX: straddle + risk layer (base)", S.with_params(BASE, index="SENSEX")),
             **{f"sx_{k}": (f"SENSEX: {lb}", S.with_params(BASE, index="SENSEX", **kw)) for k, lb, kw in TESTS}}
# Extra checks table: rows point at the views above (a str = key of a run that already exists)
CHECKS = [["NIFTY (1 Nov 2021 - 3 Nov 2026): base aur 4 test, ek-ek karke"], ("Base: straddle + risk layer", "main"), *[(lb, f"n_{k}") for k, lb, _ in TESTS]]
BANK_CHECKS = [["SENSEX (15 May 2023 - 3 Nov 2026): base aur 4 test, ek-ek karke"], ("Base: straddle + risk layer", "sx_main"), *[(lb, f"sx_{k}") for k, lb, _ in TESTS]]


def chips_of(p: S.Params) -> list:
    """Chips of one view; a chip is highlighted when it differs from the base of the same index (= what this test changed)."""
    ref = {c[0]: c[1] for c in R.chips_of(S.with_params(BASE, index=p.index))}
    out = [c[:2] + [1] if (len(c) > 2 or c[1] != ref[c[0]]) else c[:2] for c in R.chips_of(p)]
    out[0] = ["Index", "SENSEX (BSE, nearest expiry)" if p.index == "SENSEX" else "NIFTY (NSE weekly expiry)", 1]
    return out


SETTINGS = [["Report", "report 10 (Straddle + Risk Layer 2021 - 2026 retest) ka retest", 1],
            ["Test", "4 alag test: Trailing band | Stop 1-min touch pe | Strike adjustment ek leg 200 pt | Drawdown scaling", 1],
            ["Index", "NIFTY (NSE weekly) + SENSEX (BSE, nearest expiry)", 1],
            ["Period NIFTY", "1 Nov 2021 se 3 Nov 2026 (fit = Nov 2024 se pehle, verify = Nov 2024 se)", 1],
            ["Period SENSEX", "15 May 2023 se 3 Nov 2026 (usse pehle Sensex ke option ka data hi nahi)", 1],
            ["Capital", "Rs 30,00,000 (fixed, compounding nahi)", 1],
            ["Margin", "1/3 capital (Rs 10 lakh), ek straddle ka margin = 10% x lot x index"],
            ["Base", "9:20 ATM straddle SELL, leg SL 75%, din ka loss limit 1.5%, trailing 50%, 3:15 square-off", 1],
            ["Kharcha", "Rs 20/order, STT 4 slab (0.05% se 0.15%), exchange: NSE 0.03503% / BSE (Sensex) 0.0325% premium pe, stamp, GST", 1],
            ["Slippage", "option 0.5%, futures 0.02% har fill", 1],
            ["Option price", "model: asli 1-min index + Black-76 (strike-wise IV)"],
            ["Sensex data", "BSE bhavcopy: purani file (15 May 2023 - 4 Jan 2024) me expiry-din ka close nahi hota, wo din trade nahi"]]

RULES = [
    "Ye report 10 ('Straddle + Risk Layer (2021 - 2026 retest)') ka retest hai: uske 'Extra checks (ye rules ka hissa NAHI hai)' ke 4 test, har ek alag poora backtest "
    "(din ki table + trade viewer), NIFTY aur SENSEX dono par. 4 test ek saath nahi, ek-ek karke base me lagaye gaye hain.",
    "Index: NIFTY (NSE weekly expiry) 1 Nov 2021 se 3 Nov 2026. SENSEX (BSE) 15 May 2023 se 3 Nov 2026: Sensex ke weekly option isi din shuru hue, usse pehle BSE ki file me sirf "
    "'SENSEX 50' ke option hain (alag index), isliye 2021 se Sensex ka test ho hi nahi sakta. Dono par rules bilkul same.",
    RR.RULES[1], RR.RULES[2], RR.RULES[3], RR.RULES[4], RR.RULES[5], RR.RULES[6],
    "Test 1 - Trailing band: base me se trailing profit lock hata diya. Leg SL aur din ka loss limit waise hi. Profit lock nahi, to din ka profit 3:15 tak chalta hai.",
    "Test 2 - Stop ka confirm nahi: leg SL, din ka loss limit (aur adjustment) ke liye '2 lagatar 1-min close' ki jagah 1 minute ke andar level touch hote hi stop (us minute ka sabse bura level maana "
    "jaata hai). Baaki sab base jaisa.",
    "Test 3 - Strike adjustment chalu (ek leg 200 pt): jab ek bechi hui leg ka premium entry se 200 point upar ho jaye (2 lagatar 1-min close) to wo leg buy back, aur usi type ki naya strike becha "
    "jaata hai jiska premium doosre leg ke paas ho (balance). Din me max 2 adjustment. Baaki sab base jaisa. NOTE: 200 point NIFTY ke hisaab se chuna gaya tha, Sensex pe bhi 200 hi liya (Sensex ka "
    "index ~3x bada hai, isliye wahan ye trigger relative zyada jaldi lagta hai).",
    "Test 4 - Drawdown scaling chalu: equity apne peak se 5% / 10% / 15% neeche jaye to har baar lots aadhe; recover hone par (limit ke aadhe tak wapas) size wapas badhta hai. Baaki sab base jaisa.",
    "Kharcha: Rs 20 har order, STT 4 baar badla (Finance Act 2023, Budget 2024, Budget 2026), exchange charge NSE 0.03503% aur BSE Sensex options 0.0325% (Rs 3,250 per crore, BSE circular Oct 2024) "
    "premium pe, SEBI, stamp, GST 18%. Slippage option 0.5%, futures 0.02% har fill pe.",
]
NOTES = [
    "Ye retest hai: 4 changes report 10 ke Extra checks me pehle se NIFTY pe ek-ek karke the (summary numbers). Ab inka poora backtest (din ki table + trade viewer) hai, aur Sensex pe bhi. "
    "Jaanch: NIFTY ke 5 numbers (base + 4 test: net, din, PF, max DD) report 10 ke Extra checks se bilkul match karte hain (Rs ke hisaab se), yaani purana setup waisa hi chala.",
    "SENSEX ka option data BSE ki bhavcopy se hai. BSE ki file me kisi contract ke apne expiry din 'Close Price' me option ka close nahi, index ka close likha hota hai (aur 'Settlement' bhi wahi). "
    "5 Jan 2024 se ki UDiFF file me 'last traded price' hai, wahi close maana. 15 May 2023 - 4 Jan 2024 ki purani file me last price nahi hota, isliye us daur me expiry (Friday) ke din ka closing smile nahi "
    "ban pata aur wo din trade nahi hua (data_info me din ginti). Engine un dino agli expiry nahi bechta (strict).",
    "Purani BSE file me lot size nahi aata: 10 maana (Sensex ka lot weekly options ke saath 10 tha). UDiFF file (5 Jan 2024 se) me asli lot aata hai - wahi liya.",
    "BSE ka option exchange charge Rs 3,250 per crore premium (0.0325%, Oct 2024 se) poore period ke liye flat maana; Oct 2024 se pehle BSE ka slab-wise alag tha (asli me thoda kam/zyada).",
    "Sensex ke 71 din (8%) trade nahi hue (36 purani file me, 35 UDiFF me) aur ye zyadatar expiry ke din hain - short straddle ke liye sabse risky / sabse tez theta wale din. Isliye Sensex ke numbers "
     "'expiry din ke bina' hain: seedha NIFTY (jisme expiry din shamil hain) se compare mat karo; asli me Sensex ka result isse alag aa sakta hai.",
    "SENSEX 15 May 2023 se pehle ke din: BSE me Sensex ke option ka data hi nahi mila (sirf SENSEX 50 ke), isliye test chhota (~3.5 saal) aur NIFTY (5 saal) se seedha compare nahi ho sakta.",
    "Sensex ke option NSE ke Nifty se kam liquid hain (khaas kar 2023 me) - 'trade count >= 100' wali liquidity shart wahi rakhi. Model ka price asli se zyada door ho sakta hai; Nifty ki tarah "
    "1-min asli option price se model ki jaanch Sensex ke liye nahi ho sakti (Fyers sirf chalu contracts ka 1-min deta hai).",
    "Test 3 me trigger 200 point NIFTY ke liye tha; Sensex ke liye alag (scale kiya hua) number nahi chuna - jaisa kaha gaya waisa. Trading expert se poochna baaki hai.",
    *(R.NOTES[i] for i in (1, 3, 4, 5, 6)),
    *R23.NOTES[-3:-1],
]
FORMULAS = R23.FORMULAS + [
    ["BSE exchange charge (Sensex option)", "0.0325% x (fill price x qty)  (Rs 3,250 per crore premium turnover); NSE option: 0.03503%"],
    ["Sensex expiry-din close", "BSE file: Close Price = index close. UDiFF (5 Jan 2024 se): close = last traded price; purani file: close nahi -> us din trade nahi"],
    ["Sensex lot (purani BSE file)", "10 (UDiFF file me asli lot size)"],
]

RESULT = {}


def info_extra(logs, blogs) -> list:
    """Data card lines about SENSEX + the numbers for the finish message."""
    cap = BASE.capital
    for k, f in {**logs, **blogs}.items():
        RESULT[k] = S.stats(f, cap)
    sm = blogs["sx_main"]
    old = sm[sm.date < "2024-01-05"]
    skipped = sm[(sm.traded == 0) & (sm.exit == "Contract nahi mila")]
    lots = sorted(set(sm[sm.traded == 1].lot.dropna().astype(int)))
    return [["SENSEX din", f"{int(sm.traded.sum())} trade din, {len(skipped)} din 'Contract nahi mila' (nearest expiry ka closing smile nahi bana; purani BSE file me {len(skipped[skipped.date < '2024-01-05'])} din, "
                           f"UDiFF me {len(skipped[skipped.date >= '2024-01-05'])} din), {len(sm)} trading din ({sm.date.min()} se {sm.date.max()}); purani file ke {len(old)} din me se {int(old.traded.sum())} trade"],
            ["SENSEX lot size", ", ".join(str(x) for x in lots)]]


CFG = dict(name=NAME, title=TITLE, desc="report 10 ka retest: 4 alag test, NIFTY + SENSEX", base=BASE, view=VIEW, bank_view=BANK_VIEW, checks=CHECKS, bank_checks=BANK_CHECKS,
           settings=SETTINGS, rules=RULES, notes=NOTES, formulas=FORMULAS, chips_of=chips_of, second="SENSEX", custom_finish=True, data_info_extra=info_extra,
           check_hint="Base (straddle + risk layer) se ek baar me sirf ek cheez badli: report 10 ke Extra checks ke 4 test. NIFTY aur SENSEX alag table me. Net = poora period; Fit / Verify = do hisse (Nov 2024 se pehle / baad).",
           sub="NIFTY + SENSEX options &middot; report 10 ka retest (4 test)",
           data_note="NSE (NIFTY) + BSE (SENSEX) index option bhavcopy (daily) + Fyers 1-min index / VIX; option ka din ke andar ka price model",
           data_info_head=[["Index 1-minute", "NIFTY 50 (Fyers 1-min, Oct 2021 se) + SENSEX (Fyers 1-min, 3 Apr 2023 se) + India VIX (1-min)"],
                           ["Option data", "NIFTY: NSE index option bhavcopy (purana format 2021 - 7 Jul 2024, UDiFF uske baad); SENSEX: BSE option bhavcopy (purani MS file 15 May 2023 - 4 Jan 2024, UDiFF 5 Jan 2024 se): "
                                           "har strike ka din ka Open / Close / trades, expired bhi"]],
           start_msg=f"Run shuru: {TITLE}. NIFTY 1 Nov 2021 - 3 Nov 2026, SENSEX 15 May 2023 - 3 Nov 2026 (Sensex option data isse pehle nahi hai). 10 setup (base + 4 test, har index).")


def finish(out: Path):
    cap = BASE.capital
    def line(k, label):
        s = RESULT[k]
        return f"{label}: {s['days']} din, win {s['win_pct']:.0f}%, net Rs {s['net']:,.0f}, PF {s['profit_factor'] or 0:.2f}, max DD Rs {s['max_dd']:,.0f}, sabse bura din Rs {s['worst']:,.0f}"
    msg = (f"Report 10 retest DONE ({out.name}) {TAG_TRADING}\n4 alag test (base = straddle + risk layer), Rs 30L capital, 1/3 margin, compounding nahi.\n\n"
           "NIFTY (1 Nov 2021 - 3 Nov 2026):\n" + "\n".join(line(k, v[0].replace("NIFTY: ", "")) for k, v in VIEW.items())
           + "\n\nSENSEX (15 May 2023 - 3 Nov 2026):\n" + "\n".join(line(k, v[0].replace("SENSEX: ", "")) for k, v in BANK_VIEW.items())
           + "\n\nNote: Sensex option data 15 May 2023 se pehle nahi; purani BSE file me expiry-din ka close nahi (wo din trade nahi). Details report ke Rules / Notes me.")
    send(msg)
    send_document(out / "report.html", f"{TITLE} ({out.name})")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--no-telegram", action="store_true")
    ap.add_argument("--status-file")
    args = ap.parse_args()
    status = (lambda s: Path(args.status_file).write_text(s + "\n", encoding="utf-8")) if args.status_file else (lambda s: None)
    try:
        rc = R.run(args, status, CFG)
        if rc == 0 and not args.no_telegram:
            finish(result_dir(NAME))
        return rc
    except Exception:
        err = traceback.format_exc()
        print(err, flush=True)
        if not args.no_telegram:
            send(f"Report 10 retest run me error {TAG_TECH}:\n{err[-1500:]}")
        return 1


if __name__ == "__main__":
    sys.exit(main())
