"""Calendar spread (stock futures, near vs next month) backtest on the NSE F&O bhavcopy: gap thresholds 0.75 / 1.5 / 2 / 3%
x slippage 0 / 0.02 / 0.05% per leg; writes results/NN_<name>/report.html and sends it.

    python tools/run_calspread.py
    python tools/run_calspread.py --no-telegram --base "1.5%|0.02%"

Needs:  (Algo_Historical_Data) python -m fyers_data.download_calendar bhav --start 2024-11-01 --end 2026-10-30
"""
import argparse
import sys
import time
import traceback
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from backtest import calspread as cs  # noqa: E402
from backtest.calspread_report import totals, write_html  # noqa: E402
from backtest.config import result_dir  # noqa: E402
from backtest.notify import send, send_document  # noqa: E402

TAG_TRADING, TAG_TECH = "@ayushanand440", "@albert_max"
DATA_START, DATA_END, TRADE_START = "2024-11-01", "2026-10-30", "2025-05-01"
THS = {"0.75%": 0.75, "1.5%": 1.5, "2%": 2.0, "3%": 3.0}
SLIPS = {"0%": 0.0, "0.02%": 0.0002, "0.05%": 0.0005}
RULES = [
    "Samay: NSE F&O bhavcopy (har din ka closing price) 1 Nov 2024 se 30 Oct 2026. Pehle 6 mahine sirf har stock ka 'normal gap' aur "
    "pichhla record banane ke liye; trade 1 May 2025 se.",
    "Stocks: saare NSE F&O stocks (stock futures). Sirf liquid din gine: us din near aur far dono contract me kam se kam 100 trade.",
    "Gap = Far (agle mahine) future ka close - Near (paas wale mahine) future ka close. Gap % = Gap / Near price x 100.",
    "Normal gap: har stock ka pichhle 120 trading din ke Gap % ka median (beech wala number).",
    "Doori = aaj ka Gap % - Normal gap %. Doori +T ya zyada = gap bahut chauda; -T ya kam = gap bahut patla ya ulta. "
    "T = 0.75%, 1.5%, 2%, 3% - chaaro alag chala ke dekhe (video me 2-3% bola tha).",
    "Pichhla record: pichhle 6 expiry me se kam se kam 4 me expiry ke din gap normal ke paas (0.25% ke andar) wapas aaya ho "
    "(jin expiry me beech me dividend / corporate action tha, wo nahi gine).",
    "Dividend / bonus / split: aaj se far expiry tak stock ki koi ex-date ho to entry nahi. Roll ke samay naye pair me ex-date ho to roll nahi (band).",
    "Pehle din entry nahi: jis din doori pehli baar T paar kare us din sirf dekho. Entry agle din jab doori ab bhi T ke paar ho aur aur na badhe "
    "(aaj ki doori kal se zyada nahi).",
    "Direction: gap chauda -> Far SELL + Near BUY (gap patla hone pe fayda). Gap patla/ulta -> Far BUY + Near SELL. Dono leg ek saath, same lot.",
    "Lot: pehli entry 2 lot (2 pair). Gap 0.5% aur door jaye to 1 lot aur (sirf ek baar). Saare stocks mila ke max 5 lot khule (capital Rs 2 lakh). "
    "Margin lagbhag Rs 17,000 per pair.",
    "Exit 1: gap aadha wapas aa jaye (doori, entry wali doori ki aadhi reh jaye) to aadhe lot band.",
    "Exit 2: gap normal pe aa jaye (doori 0 ya ulti taraf) to baaki saare lot band.",
    "Roll: near expiry se 2 trading din pehle tak gap normal nahi hua to ek baar agle pair pe roll (purana pair band; naya near = purana far, "
    "naya far = teesra mahina). Doosri expiry se 2 din pehle tak bhi nahi bhara to sab band.",
    "Risk stop: doori entry wali doori ki 1.5 guna ho jaye, ya trade ka loss Rs 4,000 (capital ka 2%) ho jaye - jo pehle ho - sab band.",
    "Near expiry se 3 trading din pehle naya trade nahi.",
    "Fill: us din ka NSE closing price (aakhri 30 minute ka average) +/- slippage. Slippage 0%, 0.02%, 0.05% (har leg, har fill) - teeno alag.",
    "Kharcha: Fyers/NSE futures charges - Rs 20 har order, STT 0.02% sell pe, exchange 0.00173%, SEBI 0.0001%, stamp 0.002% buy pe, GST 18%.",
]
NOTES = [
    "Video me exact entry / exit / stop numbers nahi the. Rulebook ke [A] wale rules (upar) liye, aur jahan wo bhi clear nahi tha wahan ye maana:",
    "'Gap 2-3% se upar' ko 'normal gap se 2-3% door' maana, kyunki normal gap khud ~0.5-0.7% hota hai (1 mahine ka interest). Seedha Gap % wala "
    "version extra check me hai.",
    "Dividend / bonus / split ki ex-date NSE ke corporate actions se li. Ye asli ex-date hain; asli me dividend ki khabar 2-6 hafte pehle aati hai "
    "(kabhi entry ke baad), isliye ye filter thoda 'aage ka pata' maan ke chalta hai. Quarterly result wala filter nahi lagaya.",
    "Dividend filter ke bina 'gap patla' wale zyada tar signal dividend ki wajah se hote hain (jaise INFY May 2025, HEROMOTOCO Jul 2025) - "
    "ye extra check me dikhaya hai.",
    "Jis din near aur far ka lot size alag tha (NSE lot size badalta hai), us din entry / roll nahi, kyunki dono taraf same quantity nahi ho sakti.",
    "Normal gap entry ke din wala hi poore trade me rakha (beech me nahi badla). Roll ke baad bhi wahi.",
    "Fill daily closing price pe maana. Asli me din ke beech entry / exit aur bid-ask se price alag hoga; far month me spread zyada hota hai.",
    "Expiry day margin wali dikkat: hum expiry se 2 din pehle hi exit / roll karte hain, isliye expiry ke din position nahi rehti.",
    "Margin Rs 17,000 per pair maana (video: 15-18k). Asli margin stock aur broker ke hisaab se alag hoga.",
    "Ek stock pe ek samay me ek hi position. Jis din exit hua, us din usi stock pe nayi entry nahi.",
    "Ek din me kai stock me signal aaye to jiski doori sabse zyada, wo pehle (max 5 lot tak).",
    "Exit 1 me 'aadhe lot': 2 lot pe 1, 3 lot pe 1 (neeche round). 1 lot ho to exit 1 nahi, seedha exit 2.",
    "30 Oct 2026 (data ka aakhri din) ko khule trade usi din ke close pe band maane ('data khatam').",
]
FORMULAS = [
    ["Near, Far, Teesra", "Har stock, har din: expiry ke hisaab se pehla contract = Near, doosra = Far, teesra = roll ke liye"],
    ["Gap (Rs)", "Gap = Far close - Near close"],
    ["Gap %", "Gap % = Gap / Near close x 100"],
    ["Liquid din", "Near ke trades >= 100  AUR  Far ke trades >= 100 (us din)"],
    ["Normal gap %", "= pichhle 120 trading din (liquid din) ke Gap % ka median;  aaj ka din shamil nahi"],
    ["Doori", "Doori = Gap % - Normal gap %   (percentage point)"],
    ["Signal (chauda)", "Doori >= +T  ->  Far SELL + Near BUY"],
    ["Signal (patla)", "Doori <= -T  ->  Far BUY + Near SELL"],
    ["Wait (1 din)", "Kal bhi same signal tha  AUR  |aaj ki doori| <= |kal ki doori|"],
    ["Pichhla record", "Pichhli 6 saaf expiry: |expiry din ka Gap % - Normal| <= 0.25  ->  'wapas aaya';  kam se kam 4 chahiye"],
    ["Dividend filter", "Entry nahi agar:  aaj <= ex-date <= far expiry;   Roll nahi agar:  roll din <= ex-date <= teesre mahine ki expiry"],
    ["a (trade ki taraf doori)", "a = side x Doori   (side = +1 chauda wale trade me, -1 patle wale me);  entry pe a0"],
    ["Scale-in", "a >= a0 + 0.5  ->  1 lot aur (ek hi baar, total 5 lot se zyada nahi)"],
    ["Exit 1", "a <= a0 / 2  ->  aadhe lot band (neeche round)"],
    ["Exit 2", "a <= 0  ->  saare lot band"],
    ["Risk stop", "a >= 1.5 x a0   YA   trade ka Net (band + khula) <= -4,000"],
    ["Roll din", "Near expiry ke trading din se 2 trading din pehle"],
    ["Fill price", "Buy = Close x (1 + s),  Sell = Close x (1 - s);   s = slippage (0, 0.0002, 0.0005)"],
    ["P&L (chauda trade)", "Qty x [(Far entry - Far exit) + (Near exit - Near entry)]   (Qty = lots x lot size)"],
    ["P&L (patla trade)", "Qty x [(Far exit - Far entry) + (Near entry - Near exit)]"],
    ["Brokerage", "Rs 20 x orders (har entry / exit / roll me har leg = 1 order)"],
    ["Baaki charges", "STT = 0.02% x sell value;  Exchange = 0.00173% x (buy + sell value);  SEBI = 0.0001% x (buy + sell);  "
                      "Stamp = 0.002% x buy value;  GST = 18% x (Brokerage + Exchange + SEBI)"],
    ["Net P&L", "Net = Gross - Kharcha (poore trade ke saare leg, roll mila ke)"],
    ["Equity (mark to market)", "Roz: band trades ka Net jod + khule trades ka (us din ke close pe) P&L - kharcha"],
    ["Max drawdown", "Peak = max(0, ab tak ka sabse uncha Equity);  Max DD = (Equity - Peak) ka sabse bada minus"],
    ["Max drawdown %", "(Equity - Peak) / (2,00,000 + Peak) x 100, inme sabse bada minus"],
    ["Win rate", "Win % = (Net > 0 wale trade / kul trade) x 100"],
    ["Profit factor", "PF = jeete trades ka Net jod / hare trades ka loss jod (bina minus). 1 se upar = profit"],
    ["Return %", "Return = kul Net / 2,00,000 x 100"],
    ["Margin pe return", "= kul Net / (sabse zyada khule lot x 17,000) x 100"],
    ["Roll ke baad bhi loss", "= roll hue trades jinka Net <= 0"],
]


def check_rows(t, px, base: cs.Params) -> list[list]:
    def row(name, **kw):
        p = cs.with_params(base, **kw)
        tr, eq = cs.simulate(cs.prepare(t, p), px, p, start=TRADE_START)
        s = totals(tr, eq, p.capital, p.margin_per_pair)
        return [name, s["trades"], s["win_pct"], round(s["net"]), s["profit_factor"], round(s["max_dd"]), s["rolled"]]

    return [
        row("Jaisa rules me hai"),
        row("Sirf gap chauda wale trade (Far SELL + Near BUY, video jaisa)", sides="wide"),
        row("Sirf gap patla wale trade (Far BUY + Near SELL)", sides="narrow"),
        row("Sirf gap chauda + pichhla record filter nahi", sides="wide", consist_need=None),
        row("Pichhla record wala filter nahi", consist_need=None),
        row("Dividend / corporate action filter nahi", ca_filter=False),
        row("Pehle din hi entry (wait nahi)", wait=0),
        row("2 din wait", wait=2),
        row("Scale-in nahi (sirf 2 lot)", lots2=0),
        row("Aadha exit nahi (sab exit 2 pe)", partial=False),
        row("Roll nahi (pehli expiry se 2 din pehle band)", roll=False),
        row("Risk stop nahi", stop_mult=None, stop_loss=None),
        row("Seedha Gap % >= T (normal se doori nahi)", entry_on="raw"),
        row("Liquidity 100 ki jagah 20 trade", min_trades=20),
        row("Normal = pichhle 60 din ka median (120 ki jagah)", lookback=60),
    ]


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--name", default="calspread_2024_2026")
    ap.add_argument("--base", default="0.75%|0.02%", help="threshold|slippage used for the extra checks and opened first")
    ap.add_argument("--no-telegram", action="store_true")
    a = ap.parse_args()
    tell = (lambda m: None) if a.no_telegram else send
    out = result_dir(a.name)
    t0 = time.time()
    tell("CALENDAR SPREAD BACKTEST STARTED: saare F&O stocks, near vs next month future, NSE bhavcopy daily close, trade May 2025 - Oct 2026. "
         "Gap normal se 0.75 / 1.5 / 2 / 3% door pe entry, 2+1 lot, aadha exit, 1 roll, risk stop. Slippage 0 / 0.02 / 0.05% har leg.")
    try:
        t, px = cs.load(DATA_START, DATA_END)
        runs, freq = {}, []
        for label, th in THS.items():
            p0 = cs.Params(threshold=th)
            tp = cs.prepare(t, p0)
            live = tp[(tp.date >= TRADE_START) & tp.dev.notna()]
            far = live[live.dev.abs() >= th]
            freq.append([label, len(far), int(far.symbol.nunique()), int(live.signal.sum())])
            for sl_label, s in SLIPS.items():
                p = cs.with_params(p0, slip=s)
                tr, eq = cs.simulate(tp, px, p, start=TRADE_START)
                runs[(label, sl_label)] = (tr, eq)
                tr.to_csv(out / f"trades_T{label.rstrip('%')}_slip_{sl_label.rstrip('%')}pct.csv", index=False)
            tell(f"Calendar spread: threshold {label} done - {freq[-1][1]:,} stock-din gap itna door, {freq[-1][3]:,} pe entry ki shart, "
                 f"{len(runs[(label, '0.02%')][0])} trade (0.02% slippage).")
        bt, bs = a.base.split("|")
        base = cs.Params(threshold=THS[bt], slip=SLIPS[bs])
        checks = check_rows(t, px, base)
        days = sorted(t.date.unique())
        data_info = [
            ["Data", f"NSE F&O bhavcopy, {len(days)} trading din ({days[0]} se {days[-1]}), {t.symbol.nunique()} stocks (stock futures)"],
            ["Trade ka samay", f"{TRADE_START} se {DATA_END} (pehle 6 mahine normal gap aur pichhle 6 expiry ka record banane ke liye)"],
            ["Liquid stock-din", f"{int(((t.near_trades >= 100) & (t.far_trades >= 100)).sum()):,} / {len(t):,} "
                                 f"({((t.near_trades >= 100) & (t.far_trades >= 100)).mean() * 100:.0f}%) me dono contract me 100+ trade"],
            ["Normal gap (sab stocks ka median)", f"{t.gap_pct[(t.near_trades >= 100) & (t.far_trades >= 100)].median():.2f}%"],
            ["Price", "ClsPric = NSE closing price (aakhri 30 min ka weighted average). Expired contracts bhi shamil."],
            ["Corporate actions", f"NSE se {len(pd.read_csv(cs.CORP_CSV)):,} actions (dividend, bonus, split ...), Nov 2024 - Nov 2026; "
                                  f"{t.ca_far.mean() * 100:.0f}% stock-din me far expiry tak koi ex-date thi"],
        ]
        write_html(out / "report.html", runs, base.capital, base.margin_per_pair, RULES, NOTES, checks, FORMULAS, freq, data_info,
                   f"Trade {TRADE_START} se {DATA_END}", a.base)
    except Exception:
        tell(f"{TAG_TECH} Calendar spread backtest FAILED:\n{traceback.format_exc()[-1500:]}")
        raise
    lines = []
    for (label, sl_label), (tr, eq) in runs.items():
        s = totals(tr, eq, 200_000, 17_000)
        lines.append(f"T {label} slip {sl_label}: {s['trades']} trades | win {s['win_pct'] or 0:.1f}% | net Rs {s['net']:,.0f} | "
                     f"PF {s['profit_factor'] or 0:.2f} | max DD Rs {s['max_dd']:,.0f} | roll {s['rolled']} (loss {s['roll_failed']})")
    msg = (f"{TAG_TRADING} CALENDAR SPREAD BACKTEST DONE ({(time.time() - t0) / 60:.1f} min). Capital Rs 2 L, max 5 lot.\n" + "\n".join(lines))
    tell(msg)
    print(msg)
    if not a.no_telegram:
        send_document(out / "report.html", "Calendar spread report (browser me kholo). Rules tab me rules, neeche Formulas.")
    print("done ->", out)
    return 0


if __name__ == "__main__":
    sys.exit(main())
