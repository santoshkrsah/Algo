"""Vol 5L + 7% target: run at 0%, 0.3% and 0.5% slippage, write results/<name>/report.html and send it.

    python tools/run_vol5l.py --start 2021-10-01 --end 2026-10-28 --name vol5l_2021_2026
    python tools/run_vol5l.py --name vol5l_2021_2026 --cached --no-telegram    # reuse the prepared data
"""
import argparse
import pickle
import sys
import time
import traceback
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from backtest import vol5l  # noqa: E402
from backtest.config import RESULTS  # noqa: E402
from backtest.notify import send, send_document  # noqa: E402
from backtest.vmu_report import totals  # noqa: E402
from backtest.vol5l_report import write_html  # noqa: E402

TAG_TRADING, TAG_TECH = "@ayushanand440", "@albert_max"
SLIPS = {"0%": 0.0, "0.3%": 0.003, "0.5%": 0.005}
RULES = [
    "Samay: 1 Oct 2021 se 28 Oct 2026 tak (jitna data hamare paas hai). NSE equity (cash), sirf intraday, sirf BUY (short nahi).",
    "Kaun se stocks: hamare data ke saare stocks (lagbhag 1,386, market cap ke hisaab se chune hue).",
    "Signal candle: din ki pehli 15 minute candle (9:15 se 9:30).",
    "Volume: is pehli candle me kam se kam 5 lakh (5,00,000) shares ka volume hona chahiye.",
    "Entry: pehli candle green ho (close, open se upar) to 9:30 pe usi candle ke close price pe BUY.",
    "Din me max 2 trade. Agar zyada stocks milte hain to jinka volume (shares) sabse zyada hai, wo 2 stock lete hain.",
    "Paisa: capital Rs 70,000. Har trade me Rs 35,000 margin x 5 leverage = Rs 1,75,000 ka maal. "
    "Qty = 1,75,000 / buy price (point hata ke). Jo profit hua wo dobara nahi lagaya (no compounding).",
    "Target: buy price se 7% upar pahunchte hi bech do.",
    "Stop loss: buy price se 3% neeche.",
    "Trailing SL: jaise hi stock buy price se 2% upar jata hai, SL buy price pe aa jata hai (ab loss nahi hoga). "
    "Uske baad SL = ab tak ka sabse uncha price - 2%, par buy price se neeche kabhi nahi. SL sirf upar jata hai, wapas neeche nahi aata.",
    "Exit: target lage, SL lage, ya 3:15 baje (15:15 candle ke open pe) square-off - jo pehle ho.",
    "Kharcha: har trade pe trade value ka 0.10%. Slippage 0%, 0.3% aur 0.5% - teeno alag alag chala ke dekha.",
]
NOTES = [
    "Trade 9:30 se 1 minute candles pe chalaya. SL kitna upar jayega, ye candle band hone ke baad decide hota hai aur agli candle se lagu hota hai.",
    "Agar ek hi 1 minute candle me SL aur 7% target dono touch ho jaye, to maana ki SL pehle laga (sabse bura case).",
    "Agar candle SL ke neeche hi khulti hai (gap down), to exit us candle ke open pe. Agar target ke upar khulti hai, to exit us open pe.",
    "SL aur target 15:14 wali candle tak check hote hain; 15:15 wali candle pe square-off hota hai.",
    "Kuch din stock ki 15:15 candle nahi thi (trading ruk gayi). Tab exit aakhri candle ke close pe (last candle close).",
    "Slippage dono taraf lagti hai (buy aur sell dono pe). 0.10% kharcha buy value pe lagaya (VMU test jaisa).",
    "Stock list aaj ke market cap se bani hai, isliye jo stock band (delist) ho gaye ya chhote ho gaye wo purane saalon me nahi hain "
    "(survivorship bias). Price split/bonus ke liye adjust nahi hain.",
    "Agar 9:30 pe stock upper circuit pe ho to asli me buy nahi ho paata; backtest maan leta hai ki buy hamesha ho gaya.",
]
FORMULAS = [
    ["Pehli candle ka volume", "Volume = 9:15 se 9:30 wali 15 min candle ke shares. Shart: Volume >= 5,00,000"],
    ["Green candle", "Close > Open (pehli 15 min candle)"],
    ["Buy price (E)", "E = pehli 15 min candle ka Close"],
    ["Stock chunna (din me 2)", "Volume ke hisaab se bade se chhote me lagao, upar ke 2 lo"],
    ["Quantity", "Qty = 1,75,000 / E  (neeche round, jaise 1,234.8 -> 1,234)"],
    ["Target price", "Target = E x 1.07"],
    ["Shuru ka SL", "SL = E x 0.97"],
    ["Sabse uncha price (H)", "H = entry ke baad band hui 1 min candles ka sabse bada High"],
    ["Trailing SL", "Agar H >= E x 1.02 to SL = max(E, H x 0.98), warna SL = E x 0.97"],
    ["SL kabhi neeche nahi", "Naya SL = max(Purana SL, upar wala SL)"],
    ["SL laga?", "Candle ka Low <= SL  ->  Exit = min(candle ka Open, SL)"],
    ["Target laga?", "Candle ka High >= Target  ->  Exit = max(candle ka Open, Target)"],
    ["3:15 square-off", "Exit = 15:15 wali 1 min candle ka Open"],
    ["Slippage ke baad price", "Buy = E x (1 + s),  Sell = Exit x (1 - s)   (s = 0, 0.003 ya 0.005)"],
    ["Gross P&L", "Gross = Qty x (Sell - Buy)"],
    ["Kharcha", "Kharcha = 0.10% x Qty x E = 0.001 x Qty x E"],
    ["Net P&L", "Net = Gross - Kharcha"],
    ["Win rate", "Win % = (Net > 0 wale trade / kul trade) x 100"],
    ["Return %", "Return = kul Net / 70,000 x 100"],
    ["Average profit / loss", "Avg profit = jeete trades ka Net jod / jeete trade;  Avg loss = hare trades ka Net jod / hare trade"],
    ["Profit factor", "PF = jeete trades ka Net jod / hare trades ka loss jod (bina minus). 1 se upar = profit"],
    ["Payoff ratio", "Payoff = Avg profit / Avg loss (bina minus)"],
    ["Kul profit (equity)", "Kul = ab tak ke saare trades ka Net jod (date ke hisaab se)"],
    ["Max drawdown", "Peak = max(0, ab tak ka sabse uncha Kul);  Drawdown = Kul - Peak;  Max DD = sabse bada minus Drawdown"],
    ["Max drawdown %", "DD % = (Kul - Peak) / (70,000 + Peak) x 100, inme sabse bada minus"],
    ["Net / Max DD", "= kul Net / Max DD (bina minus)"],
    ["Turnover (sirf extra check)", "Turnover = Volume x Close"],
]


def checks(cands: list) -> list[list]:
    """Same data, one change at a time (0% slippage)."""
    def row(name, **kw):
        s = totals(vol5l.simulate(cands, 0.0, **kw))
        return [name, s["trades"], round(s["win_pct"], 1), round(s["net"]), s["profit_factor"], round(s["max_dd"])]

    return [
        row("Jaisa rules me hai (din me 2 trade)"),
        row("Din me sirf 1 trade", max_trades=1),
        row("Din me 5 trade (har ek Rs 1.75 lakh)", max_trades=5),
        row("Shares ki jagah sabse zyada turnover (Rs = volume x price) wale 2 stock", by_turnover=True),
        row("Sirf Rs 100 ya usse mehenge stock", min_price=vol5l.CHECK_PRICE),
        row("Trailing SL high se 2% ki jagah 3% neeche", gap=0.03),
        row("Sirf breakeven (+2% pe SL buy price pe, uske baad trailing nahi)", gap=None),
        row("Breakeven / trailing nahi (sirf 3% SL + 7% target)", be=None),
        row("Target 7% ki jagah 5%", target=0.05),
        row("Target nahi (sirf trailing SL ya 3:15 exit)", target=None),
    ]


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--start", default="2021-10-01")
    ap.add_argument("--end", default="2026-10-28")
    ap.add_argument("--name", default="vol5l_2021_2026")
    ap.add_argument("--cached", action="store_true")
    ap.add_argument("--no-telegram", action="store_true")
    a = ap.parse_args()
    tell = (lambda m: None) if a.no_telegram else send
    out = RESULTS / a.name
    out.mkdir(parents=True, exist_ok=True)
    t0 = last = time.time()
    tell(f"VOL 5L + 7% TARGET BACKTEST STARTED ({a.start} to {a.end}). 15m first candle green & vol >= 5 lakh -> BUY, "
         "SL 3% -> breakeven at +2% -> trail 2%, target 7%. Slippage 0 / 0.3 / 0.5%. Progress every 10 min.")

    def on_day(done, total, n):
        nonlocal last
        if time.time() - last >= 600:
            last = time.time()
            tell(f"Vol 5L progress: day {done}/{total} read, {n} candidates kept, {(time.time() - t0) / 60:.0f} min elapsed")

    try:
        pkl = out / "candidates.pkl"
        if a.cached and pkl.exists():
            cands, qualified = pickle.load(open(pkl, "rb"))
        else:
            cands, qualified = vol5l.prepare(a.start, a.end, on_day)
            pickle.dump((cands, qualified), open(pkl, "wb"))
        tell(f"Vol 5L: data read in {(time.time() - t0) / 60:.1f} min. {sum(qualified.values()):,} stock-days qualified "
             f"on {sum(n > 0 for n in qualified.values())} of {len(qualified)} days. Simulating trades now.")
        runs = {k: vol5l.simulate(cands, s) for k, s in SLIPS.items()}
        for k, t in runs.items():
            t.to_csv(out / f"trades_slip_{k.rstrip('%')}pct.csv", index=False)
        write_html(out / "report.html", runs, RULES, NOTES, checks(cands), FORMULAS, f"{a.start} se {a.end}")
    except Exception:
        tell(f"{TAG_TECH} Vol 5L backtest FAILED:\n{traceback.format_exc()[-1500:]}")
        raise
    lines = []
    for k, t in runs.items():
        s = totals(t)
        tgt = t.exit_reason.str.startswith("target").mean() * 100
        lines.append(f"Slippage {k}: {s['trades']} trades | win {s['win_pct']:.1f}% | net Rs {s['net']:,.0f} ({s['ret_pct']:.0f}% on 70k) | "
                     f"PF {s['profit_factor'] or 0:.2f} | max DD Rs {s['max_dd']:,.0f} | target hit {tgt:.1f}% | profit months {s['plus_months']}")
    msg = f"{TAG_TRADING} VOL 5L + 7% TARGET BACKTEST DONE ({(time.time() - t0) / 60:.1f} min)\n" + "\n".join(lines)
    tell(msg)
    print(msg)
    if not a.no_telegram:
        send_document(out / "report.html", "Vol 5L + 7% target report (browser me kholo). Rules tab me saare rules, neeche Formulas.")
    print("done ->", out)
    return 0


if __name__ == "__main__":
    sys.exit(main())
