"""Positional option selling (9:15-11:15 range breakout, sell ITM option + OTM hedge, exit next day 9:35):
NIFTY (Oct 2021 - Oct 2026) and BANKNIFTY / SENSEX (Jan - Oct 2026) at 0%, 0.5%, 1% slippage; report + Telegram.

    python tools/run_stbt.py
    python tools/run_stbt.py --no-telegram
"""
import argparse
import sys
import time
import traceback
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from backtest import stbt  # noqa: E402
from backtest.config import result_dir  # noqa: E402
from backtest.notify import send, send_document  # noqa: E402
from backtest.stbt_report import totals, write_html  # noqa: E402

TAG_TRADING, TAG_TECH = "@ayushanand440", "@albert_max"
INSTS = ["NIFTY", "BANKNIFTY", "SENSEX"]
SLIPS = {"0%": 0.0, "0.5%": 0.005, "1%": 0.01}
CAPITAL = 200_000
RULES = [
    "Index: NIFTY 50 (1 Oct 2021 se 30 Oct 2026, spot ka 1 min data). BANKNIFTY aur SENSEX (Jan se Oct 2026, futures ka 1 min data).",
    "Range: 9:15 se 11:15 tak index ka sabse uncha (High) aur sabse neecha (Low) price.",
    "Breakout: 11:15 se 15:15 ke beech koi 1 min candle range High ke upar CLOSE ho to ITM PUT sell. Range Low ke neeche CLOSE ho to "
    "ITM CALL sell. Din me sirf pehla breakout.",
    "Strike: 1 strike ITM. PUT ke liye index ke upar wala pehla strike, CALL ke liye index ke neeche wala pehla strike "
    "(NIFTY 50 ke step me, BANKNIFTY/SENSEX 100 ke step me).",
    "Expiry: NIFTY aur SENSEX me sabse paas wala weekly, BANKNIFTY me monthly. Entry wale din hi expiry ho to agla expiry.",
    "Hedge: same type ka OTM option BUY, index se lagbhag 2% door (PUT sell pe 2% neeche wala PUT, CALL sell pe 2% upar wala CALL), same qty.",
    "Stop loss: index entry se 0.5% ulta chala jaye (PUT sell pe 0.5% neeche, CALL sell pe 0.5% upar). Usi din aur agle din 9:35 tak check.",
    "Re-entry: SL lagne ke baad usi din 15:15 se pehle index wapas entry price pe aa jaye to ek baar dobara same trade (naya strike, naya SL). "
    "Sirf ek re-entry.",
    "Exit: SL na lage to agle trading din 9:35 baje (STBT / BTST).",
    "Paisa: capital Rs 2,00,000 (sirf 20% wala positional hissa). Har trade me Rs 2,000 ka risk: qty = 2,000 / (SL lagne pe 1 unit ka loss).",
    "Risk limit: mahine me net loss Rs 6,000 (capital ka 3%) ho jaye to us mahine aur trade nahi. Din me max 2 trade (pehla + re-entry), "
    "yaani din ka max loss lagbhag Rs 4,000 (2%).",
    "Kharcha: Fyers/NSE option charges (Rs 20 har order, STT 0.1% sell premium pe, exchange, GST, stamp, SEBI). "
    "Slippage 0%, 0.5% aur 1% premium ka, har fill pe - teeno alag chala ke dekha.",
]
NOTES = [
    "Option ka premium MODEL se hai: Fyers expired option ka data nahi deta, aur hamare paas asli 1 min option prices sirf ~22 Sep - 28 Oct 2026 "
    "(Nov expiry, 11 strike) ke hain. Premium Black-76 model se nikala: IV = k x pichhle 20 din ki realized volatility (8% se 80% ke beech). "
    "k har index ke liye Oct 2026 ke asli prices se chuna (Model check tab).",
    "Ek trade me entry se exit tak IV same rakha. Asli me IV badalta hai (khaas kar bade move pe), ye model me nahi hai.",
    "Model me har strike ka IV same hai (skew nahi). Asli me door wala OTM PUT mehenga hota hai, isliye PUT hedge asli me thoda mehenga padega.",
    "Qty fractional hai taaki har trade ka risk Rs 2,000 rahe. Asli me lot size lagta hai: NIFTY me 1 lot ka risk hi lagbhag Rs 4,000-6,000 "
    "hota hai, yaani asli me Rs 2,000 risk possible nahi; P&L lot ke hisaab se 2-3 guna hoga.",
    "'9:50 AM entry' wali line range 9:15-11:15 se match nahi karti. Main test 11:15 ke baad breakout pe hai; 9:15-9:50 range wala alag extra check me hai.",
    "'Futures se hedge' ka matlab clear nahi tha: futures se hedge karne pe trade ka direction hi khatam ho jata, isliye OTM option hedge liya.",
    "SL '0.5% underlying ya 50% premium' me se 0.5% underlying liya; 50% premium wala extra check me hai.",
    "BANKNIFTY aur SENSEX ke liye spot data nahi hai, isliye front-month futures ka price use kiya (range, SL aur model ka forward).",
    "SL ka fill: candle SL ke paar khule (gap) to us candle ke open pe. Agle din 9:15 ke gap se loss Rs 2,000 se zyada ho sakta hai.",
    "Charges 2026 ke rate se poore samay ke liye lagaye (Oct 2024 se pehle STT kam tha).",
    "Expiry wala din chhutti ho to pichhla trading din. NIFTY weekly expiry 1 Sep 2025 se Thursday se Tuesday hua, wahi maana.",
    "Muhurat trading jaise chhote session wale din (9:15 candle nahi) na entry, na exit.",
    "'79% intraday 0 DTE' aur '1% option buying' wale hisse is test me nahi hain, sirf 20% positional option selling.",
    "Speaker ne kaha backtest return ko 20-25% kam maano; slippage 0.5% aur 1% wale run isi ke liye hain.",
]
FORMULAS = [
    ["Range High (H), Low (L)", "H = 9:15-11:14 ki 1 min candles ka sabse bada High;  L = sabse chhota Low"],
    ["Breakout", "11:15-15:14 ki candle: Close > H  ->  SELL PE;   Close < L  ->  SELL CE   (pehla breakout hi)"],
    ["Index entry (E)", "E = breakout candle ka Close"],
    ["Sold strike (K)", "PUT: K = (floor(E / step) + 1) x step;   CALL: K = (ceil(E / step) - 1) x step"],
    ["Hedge strike", "PUT: round(E x 0.98 / step) x step;   CALL: round(E x 1.02 / step) x step"],
    ["SL level", "PUT sell: SL = E x 0.995;   CALL sell: SL = E x 1.005"],
    ["SL laga?", "PUT sell: candle Low <= SL;  CALL sell: candle High >= SL  ->  Index exit = SL (gap ho to candle ka Open)"],
    ["Re-entry", "SL ke baad usi din (15:15 se pehle) PUT sell: Close >= E;  CALL sell: Close <= E  ->  ek baar dobara entry"],
    ["Normal exit", "Index exit = agle trading din 9:35 wali candle ka Open"],
    ["Realized vol (RV)", "RV = pichhle 20 din ke daily Close ke log return ka standard deviation x root(252)"],
    ["IV", "IV = k x RV   (8% se kam nahi, 80% se zyada nahi)"],
    ["Time (T)", "T = expiry din 15:30 tak bacha samay / 365 din (saal me)"],
    ["Forward (F)", "NIFTY: F = Spot x e^((r - q) x T), r = 6.5%, q = 1.2%;   BANKNIFTY / SENSEX: F = futures price"],
    ["d1, d2", "d1 = [ln(F / K) + IV^2 x T / 2] / (IV x root(T));   d2 = d1 - IV x root(T)"],
    ["CALL premium", "e^(-r x T) x [F x N(d1) - K x N(d2)]   (N = normal distribution)"],
    ["PUT premium", "e^(-r x T) x [K x N(-d2) - F x N(-d1)]"],
    ["1 unit ka loss SL pe", "[Sold premium (SL pe) - Sold premium (entry)] - [Hedge premium (SL pe) - Hedge premium (entry)]  (entry ke samay pe)"],
    ["Qty", "Qty = 2,000 / (1 unit ka loss SL pe)"],
    ["Slippage (s)", "Sold option: sell = P x (1 - s), buy back = P x (1 + s);   Hedge: buy = P x (1 + s), sell = P x (1 - s)"],
    ["Gross P&L", "Gross = Qty x [(Sold sell - Sold buy back) + (Hedge sell - Hedge buy)]"],
    ["Brokerage", "Rs 20 x orders (hedge ke saath 4 order)"],
    ["Baaki charges", "STT = 0.1% x sell premium value;  Exchange = 0.03503% x (buy + sell value);  SEBI = 0.0001% x (buy + sell);  "
                      "Stamp = 0.003% x buy value;  GST = 18% x (Brokerage + Exchange + SEBI)"],
    ["Net P&L", "Net = Gross - Kharcha"],
    ["Month stop", "Mahine ka Net jod <= -6,000  ->  us mahine naya trade nahi"],
    ["Win rate", "Win % = (Net > 0 wale trade / kul trade) x 100"],
    ["Profit factor", "PF = jeete trades ka Net jod / hare trades ka loss jod (bina minus). 1 se upar = profit"],
    ["Return %", "Return = kul Net / 2,00,000 x 100"],
    ["Max drawdown", "Kul = ab tak ke trades ka Net jod;  Peak = max(0, ab tak ka sabse uncha Kul);  Max DD = (Kul - Peak) ka sabse bada minus"],
    ["Max drawdown %", "(Kul - Peak) / (2,00,000 + Peak) x 100, inme sabse bada minus"],
    ["k chunna (Model check)", "k = 0.60 se 2.50 tak try kiya;  jis k pe |model price - asli price| / asli price ka median sabse kam, wahi k"],
]


def checks(under, k: float) -> list[list]:
    """NIFTY, 0.5% slippage, one change at a time."""
    base = stbt.Params(iv_mult=k, slip=0.005, capital=CAPITAL)

    def row(name, **kw):
        s = totals(stbt.simulate("NIFTY", under, stbt.with_params(base, **kw)), CAPITAL)
        return [name, s["trades"], round(s["win_pct"], 1), round(s["net"]), s["profit_factor"], round(s["max_dd"])]

    return [
        row("Jaisa rules me hai"),
        row("Hedge nahi (sirf ITM option sell)", hedge_pct=None),
        row("Re-entry nahi", reentry=False),
        row("Mahine ka -3% stop nahi", month_stop=None),
        row("SL = premium 50% badhe (0.5% index ki jagah)", sl_prem=0.5),
        row("Range 9:15-9:50, breakout 9:50 ke baad", range_end=590),
        row("2 strike ITM (1 ki jagah)", itm_steps=2),
        row("Overnight nahi: usi din 15:15 exit", exit_next=False),
        row(f"IV 20% kam (k = {k * 0.8:.2f})", iv_mult=k * 0.8),
        row(f"IV 20% zyada (k = {k * 1.2:.2f})", iv_mult=k * 1.2),
    ]


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--name", default="stbt_options")
    ap.add_argument("--no-telegram", action="store_true")
    a = ap.parse_args()
    tell = (lambda m: None) if a.no_telegram else send
    out = result_dir(a.name)
    t0 = time.time()
    tell("OPTION SELLING STBT BACKTEST STARTED: NIFTY (Oct 2021-Oct 2026), BANKNIFTY + SENSEX (Jan-Oct 2026). "
         "Range 9:15-11:15 breakout, ITM sell + 2% OTM hedge, SL 0.5% index, 1 re-entry, exit agle din 9:35. Slippage 0 / 0.5 / 1%.")
    try:
        runs, calib, periods, ks = {}, [], {}, {}
        for name in INSTS:
            under = stbt.load_under(name)
            k, info = stbt.calibrate(name, under)
            ks[name] = k
            calib.append([name, info["n"], k, info["med_abs_err"], info["itm_med_abs_err"], info["med_err"], info["rv_avg"]])
            for label, s in SLIPS.items():
                t = stbt.simulate(name, under, stbt.Params(iv_mult=k, slip=s, capital=CAPITAL))
                runs[(name, label)] = t
                t.to_csv(out / f"trades_{name}_slip_{label.rstrip('%')}pct.csv", index=False)
            t = runs[(name, "0.5%")]
            periods[name] = f"{t.date.min()} se {t.date.max()}"
            if name == "NIFTY":
                nifty_under = under
            tell(f"Option STBT: {name} done (k = {k:.2f}, model galti median {info['med_abs_err']}%, ITM {info['itm_med_abs_err']}%).")
        chk = checks(nifty_under, ks["NIFTY"])
        write_html(out / "report.html", runs, CAPITAL, RULES, NOTES, chk, FORMULAS, calib, periods)
    except Exception:
        tell(f"{TAG_TECH} Option STBT backtest FAILED:\n{traceback.format_exc()[-1500:]}")
        raise
    lines = []
    for (name, label), t in runs.items():
        s = totals(t, CAPITAL)
        lines.append(f"{name} slip {label}: {s['trades']} trades | win {s['win_pct']:.1f}% | net Rs {s['net']:,.0f} ({s['ret_pct']:.0f}% on 2L) | "
                     f"PF {s['profit_factor'] or 0:.2f} | max DD Rs {s['max_dd']:,.0f}")
    msg = (f"{TAG_TRADING} OPTION SELLING STBT BACKTEST DONE ({(time.time() - t0) / 60:.1f} min). Option premium MODEL se hai (Model check tab dekho).\n"
           + "\n".join(lines))
    tell(msg)
    print(msg)
    if not a.no_telegram:
        send_document(out / "report.html", "Option selling STBT report (browser me kholo). Rules tab me rules, neeche Formulas.")
    print("done ->", out)
    return 0


if __name__ == "__main__":
    sys.exit(main())
