"""Calendar arbitrage gap study (near FUT buy / next FUT sell): write results/<name>/report.html and send it.

    python tools/run_calarb.py                       # WIPRO IREDA IRFC RVNL ASTRAL, Sept/Oct daily + Oct/Nov 1m
    python tools/run_calarb.py --no-telegram

Needs ../Algo_Historical_Data data:  python -m fyers_data.download_calendar bhav ... / contract NSE:<SYM>26NOVFUT ...
"""
import argparse
import sys
import time
import traceback
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from backtest import calarb  # noqa: E402
from backtest.calarb_report import names, write_html  # noqa: E402
from backtest.config import FNO, RESULTS  # noqa: E402
from backtest.notify import send, send_document  # noqa: E402

TAG_TRADING, TAG_TECH = "@ayushanand440", "@albert_max"
SYMBOLS = ["WIPRO", "IREDA", "IRFC", "RVNL", "ASTRAL"]
DAILY = dict(near="26SEPFUT", far="26OCTFUT", start="2026-09-01", end="2026-09-29")      # Sept expiry 29 Sept
MINUTE = dict(start="2026-10-01", end="2026-10-27", far="26NOVFUT")                       # Oct expiry 27 Oct
CONT_FILE = "{}26NOVFUT.csv"      # Fyers continuous series (near contract until expiry) in fno/1m/futures/

RULES = [
    "Stocks: WIPRO, IREDA, IRFC, RVNL, ASTRAL (NSE stock futures). Message me 'irada' ko IREDA aur 'RBNL' ko RVNL (Rail Vikas Nigam) maana.",
    "Idea: paas wale mahine ka future BUY (Near), agle mahine ka future SELL (Far). Gap = Far price - Near price.",
    "Same rate = gap bilkul 0.00 (dono contract ek hi price pe).",
    "50 paise ya zyada ka gap dono taraf alag gina: (a) Far >= Near + 0.50 (agla mahina mehenga), (b) Near >= Far + 0.50 (paas wala mahina mehenga).",
    "Part 1 - Sept buy / Oct sell (jo poocha tha, asli contracts): 1 Sept se 29 Sept 2026 (Sept expiry) tak, 20 trading din (14 Sept chhutti). "
    "NSE bhavcopy se har din ka closing price. Ek din = ek ginti.",
    "Part 2 - Oct buy / Nov sell (wahi setup, 1 minute data): 1 Oct se 27 Oct 2026 (Oct expiry) tak, 17 trading din (2 Oct aur 20 Oct chhutti). "
    "Har 1 minute candle ka aakhri price (close).",
    "Part 2 me sirf wahi minute gine jisme DONO contracts me kam se kam 1 trade hua. Jis minute me kisi ek me trade nahi hua, wo minute chhod diya.",
    "Kitni baar: ek hi din me lagatar minute jinme same haal raha = 1 baar. Haal badla (ya din badla) to nayi baar.",
    "Kitne din: jis din kam se kam ek baar wo haal mila (Part 1 me closing price pe).",
    "Samay: 9:15 se 15:39 tak ki saari candles (2026 me F&O ke trades 15:39 tak ho rahe the, NSE bhavcopy me bhi ye shamil hain).",
    "Ye sirf ginti hai - koi trade simulate nahi kiya, isliye profit/loss, kharcha aur slippage nahi joda.",
]
NOTES = [
    "Fyers pe expired contracts ka data nahi milta (WIPRO26SEPFUT / WIPRO26OCTFUT = 'Invalid symbol'). Isliye Sept/Oct ka 1 minute data nahi mila. "
    "Sept/Oct sirf din-wise hai (NSE bhavcopy).",
    "Isliye 1 minute wala test ek mahina aage ke pair pe kiya: Oct buy / Nov sell, October me. Setup wahi hai (paas wala mahina vs agla mahina, expiry tak).",
    "Oct wala leg = Fyers ki continuous series. Ye expiry tak paas wale contract ki hoti hai. Check kiya: 27 Oct tak ye Nov se alag thi, 28 Oct se Nov ke "
    "barabar ho gayi. Nov wala leg = Fyers ka asli Nov contract.",
    "Part 1 me NSE closing price = aakhri 30 minute ka average price. Agar aakhri 30 minute me trade na ho to NSE theoretical price deta hai. "
    "Isliye LTP (aakhri trade) wala gap bhi saath me diya hai.",
    "1 minute close = us minute ka aakhri trade. Dono contracts ka aakhri trade minute ke alag second pe ho sakta hai. To 'same rate' ka matlab hai "
    "'ek hi minute me dono ka aakhri price same', bilkul ek hi second nahi.",
    "Bid / ask (order book) ka data nahi hai. Asli me buy ask pe aur sell bid pe hoga, to asli gap isse thoda kharab ho sakta hai.",
    "Gap 0.49 / 0.50 ke aas paas baar-baar aata jaata hai, isliye 'baar' ki ginti me bahut saare 1-2 minute wale mauke hain. "
    "'Har mauka' tab me dikhta hai ki har baar kitne minute chala.",
    "0.50 rupaye sab stocks ke liye same rakha (jaise poocha). ASTRAL (~Rs 1,350) ke liye 0.50 bahut chhota hai, IRFC (~Rs 75) ke liye bada. "
    "Ek lot pe kitne Rs, wo 'Har stock' tab me hai.",
    "Fyers khali minute ko pichhle price se bhar deta hai (volume 0). Aise minute hata diye, sirf asli trade wale minute rakhe.",
]
FORMULAS = [
    ["Gap (Part 1, close)", "Gap = Oct ka NSE close - Sept ka NSE close (same din)"],
    ["Gap (Part 1, LTP)", "Gap LTP = Oct ka aakhri trade price - Sept ka aakhri trade price (same din)"],
    ["Gap (Part 2, 1 minute)", "Gap = Nov ka 1 min close - Oct ka 1 min close (same minute)"],
    ["Minute gina jaye?", "Oct volume > 0 AUR Nov volume > 0 (us minute me dono me trade hua)"],
    ["Same rate", "Gap (2 decimal tak) = 0.00"],
    ["Far 0.50+ upar", "Gap >= +0.50"],
    ["Near 0.50+ upar", "Gap <= -0.50"],
    ["Chhota gap (gina nahi)", "-0.50 < Gap < +0.50 aur Gap 0.00 nahi"],
    ["Kitne minute", "= un minutes ki ginti jinme condition sahi thi"],
    ["Kitni baar", "Ek din me lagatar gine gaye minute jinme same condition = 1 baar"],
    ["Kitne din", "= alag-alag dates jinme condition kam se kam 1 baar mili"],
    ["% samay", "= condition wale minute (ya din) / kul gine gaye minute (ya din) x 100"],
    ["Kul samay (min)", "= Khatam minute - Shuru minute + 1"],
    ["Sabse bada gap (ek baar me)", "= us baar ke minutes me jiska |Gap| sabse bada"],
    ["Average gap", "= saare gine gaye gaps ka jod / ginti"],
    ["Median gap", "= saare gaps chhote se bade lagao, beech wala"],
    ["0.50 x lot (Rs)", "= 0.50 x lot size (lot size NSE bhavcopy se)"],
    ["Near contract (data check)", "= us din sabse paas wali expiry ka contract"],
    ["Data match %", "= jin dino Fyers ka value NSE ke barabar / kul din x 100"],
    ["Volume check", "= Fyers volume (shares) / (NSE contracts x lot size) x 100"],
    ["Trade wale formula", "Is study me trade nahi kiye, isliye entry, qty, target, SL, slippage, P&L, kharcha, win rate, profit factor, drawdown lagu nahi"],
]


def listing(st: pd.DataFrame, k: str, unit: str) -> str:
    x = st[st[k] > 0].sort_values(k, ascending=False)
    return ", ".join(f"{r.symbol} {r[k]:,}" for _, r in x.iterrows()) + f" {unit}" if len(x) else "kisi stock me nahi"


def verdict(st: pd.DataFrame, near: str, far: str, minute: bool, period: str) -> str:
    unit = "minute" if minute else "din"
    none = ", ".join(st[st.same == 0].symbol)
    same = (f"<b>Same rate: {listing(st, 'same', unit)}</b>" + (f" (baar: {', '.join(f'{r.symbol} {r.same_times}' for _, r in st[st.same > 0].iterrows())})"
                                                             if minute else "") + (f"; {none} me kabhi nahi" if none and st.same.sum() else ""))
    if not st.same.sum():
        same = "<b>Same rate: kisi bhi stock me ek din bhi nahi</b>"
    return (f"<b>{near} buy / {far} sell</b>, {period}: {same}. "
            f"{far}, {near} se 0.50+ upar: <b>{listing(st, 'up', unit)}</b>. "
            f"{near}, {far} se 0.50+ upar: <b>{listing(st, 'down', unit)}</b>.")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--name", default="calarb_sep_oct_2026")
    ap.add_argument("--no-telegram", action="store_true")
    a = ap.parse_args()
    tell = (lambda m: None) if a.no_telegram else send
    out = RESULTS / a.name
    out.mkdir(parents=True, exist_ok=True)
    t0 = last = time.time()
    tell(f"CALENDAR ARBITRAGE STUDY STARTED: {', '.join(SYMBOLS)}. Part 1 = Sept buy / Oct sell, 1-29 Sept, NSE closing price (din-wise). "
         "Part 2 = Oct buy / Nov sell, 1-27 Oct, 1 minute. Ginti: same rate (gap 0) aur 0.50+ gap dono taraf.")

    def progress(msg):
        nonlocal last
        if time.time() - last >= 600:
            last = time.time()
            tell(f"Calendar arbitrage progress: {msg}, {(time.time() - t0) / 60:.0f} min elapsed")

    try:
        b = calarb.load_bhav(SYMBOLS, "2026-08-26", MINUTE["end"])
        lots = b.groupby("symbol").lot.last().to_dict()
        d = calarb.daily(b, DAILY["near"], DAILY["far"], DAILY["start"], DAILY["end"])
        d_st = calarb.per_stock(d, None).set_index("symbol").reindex(SYMBOLS).dropna(subset=["obs"]).reset_index()
        missing = sorted(set(SYMBOLS) - set(d.symbol))
        if missing or d.groupby("symbol").size().nunique() != 1:
            tell(f"{TAG_TECH} Calendar arbitrage: bhavcopy me kuch din/stock missing: {missing or d.groupby('symbol').size().to_dict()}")

        mins, checks = [], []
        for i, s in enumerate(SYMBOLS, 1):
            mins.append(calarb.minute(s, CONT_FILE.format(s), f"{s}{MINUTE['far']}.csv", MINUTE["start"], MINUTE["end"]))
            for leg, path, con in [("Oct (continuous)", FNO / "1m" / "futures" / CONT_FILE.format(s), None),
                                   ("Nov", FNO / "1m" / "contracts" / f"{s}{MINUTE['far']}.csv", f"{s}{MINUTE['far']}")]:
                c = calarb.data_check(s, path, b, MINUTE["start"], MINUTE["end"], con)
                checks.append([s, leg, c["days"], c["high"], c["low"], c["last"], c["vol"]])
            progress(f"{i}/{len(SYMBOLS)} stocks done")
        m = pd.concat(mins, ignore_index=True)
        e = calarb.episodes(m)
        m_st = calarb.per_stock(m, e).set_index("symbol").reindex(SYMBOLS).dropna(subset=["obs"]).reset_index()
        days = calarb.per_day(m, e)
        if min(min(c[3], c[4]) for c in checks) < 90:
            tell(f"{TAG_TECH} Calendar arbitrage: Fyers 1m data NSE bhavcopy se poora match nahi kar raha (High/Low < 90% din). Report ke Rules tab me table dekho.")

        d.to_csv(out / "daily_sep_oct.csv", index=False)
        m.to_csv(out / "minutes_oct_nov.csv", index=False)
        e.to_csv(out / "times_oct_nov.csv", index=False)
        days.to_csv(out / "days_oct_nov.csv", index=False)
        d_st.to_csv(out / "stocks_sep_oct.csv", index=False)
        m_st.to_csv(out / "stocks_oct_nov.csv", index=False)

        p1, p2 = "1-29 Sept 2026 (20 din, NSE closing price)", "1-27 Oct 2026 (17 din, 1 minute)"
        n1, n2 = names("Sept", "Oct"), names("Oct", "Nov")
        views = {
            "sepoct": dict(label="Part 1: Sept buy / Oct sell (din-wise, asli contracts)", near="Sept", far="Oct", minute=False, period=p1,
                           rows=d, eps=None, stocks=d_st, days=None, lots=lots, verdict=verdict(d_st, "Sept", "Oct", False, p1),
                           every_title="Har stock ka har din", every_hint="Har stock, har din: Sept aur Oct ka closing price, LTP aur gap. "
                           f"'Kya mila' se filter karo ({n1['same']} / {n1['up']} / {n1['down']}).",
                           day_hint="Har din ka gap (Oct close - Sept close). Mota number = same rate ya 0.50+ gap."),
            "octnov": dict(label="Part 2: Oct buy / Nov sell (1 minute)", near="Oct", far="Nov", minute=True, period=p2,
                           rows=m, eps=e, stocks=m_st, days=days, lots=lots, verdict=verdict(m_st, "Oct", "Nov", True, p2),
                           every_title="Har baar (har mauka)", every_hint="Har ek baar jab same rate ya 0.50+ gap bana: kab shuru, kab khatam, "
                           f"kitne minute. 'Kya mila' se filter karo ({n2['same']} / {n2['up']} / {n2['down']}).",
                           day_hint="Har stock, har din: kitne minute dono me trade hua, unme kitne minute same rate / 0.50+ gap, aur din ka sabse kam / zyada gap."),
        }
        check = {"hint": "Fyers ke 1 minute data ko din me jod ke NSE bhavcopy se milaya (1-27 Oct). High/Low ~100% match = data sahi hai. "
                         "Last price thoda kam match hota hai kyunki aakhri minute me kai trade hote hain.",
                 "h": ["Stock", "Leg", "Din", "Din ka High match", "Din ka Low match", "Last price match", "Volume (Fyers / NSE)"], "rows": checks}
        write_html(out / "report.html", views, RULES, NOTES, check, FORMULAS,
                   "Stocks: " + ", ".join(SYMBOLS) + " | Part 1: Sept/Oct din-wise (NSE bhavcopy) | Part 2: Oct/Nov 1 minute (Fyers)")
    except Exception:
        tell(f"{TAG_TECH} Calendar arbitrage study FAILED:\n{traceback.format_exc()[-1500:]}")
        raise

    def line(st, minute):
        r = []
        for _, x in st.iterrows():
            if minute:
                r.append(f"{x.symbol}: same {x.same} min ({x.same_times} baar, {x.same_days} din) | Nov 0.50+ upar {x.up} min "
                         f"({x.up_times} baar, {x.up_days} din) | Oct 0.50+ upar {x.down} min ({x.down_times} baar, {x.down_days} din) | "
                         f"avg gap {x['avg']:+.2f}")
            else:
                r.append(f"{x.symbol}: same {x.same} din | Oct 0.50+ upar {x.up} din | Sept 0.50+ upar {x.down} din | avg gap {x['avg']:+.2f} "
                         f"({x['min']:+.2f} se {x['max']:+.2f})")
        return "\n".join(r)

    msg = (f"{TAG_TRADING} CALENDAR ARBITRAGE STUDY DONE ({(time.time() - t0) / 60:.1f} min)\n"
           f"PART 1 - Sept buy / Oct sell, 1-29 Sept, 20 din, NSE closing price (gap = Oct - Sept):\n{line(d_st, False)}\n\n"
           f"PART 2 - Oct buy / Nov sell, 1-27 Oct, 1 minute (sirf jab dono me trade hua; gap = Nov - Oct):\n{line(m_st, True)}\n\n"
           "Note: Sept/Oct ka 1-minute data Fyers pe nahi hai (expired), isliye minute-wise test Oct/Nov pe kiya.")
    tell(msg)
    print(msg)
    if not a.no_telegram:
        send_document(out / "report.html", "Calendar arbitrage report (browser me kholo). Rules tab me saare rules, neeche Formulas.")
    print("done ->", out)
    return 0


if __name__ == "__main__":
    sys.exit(main())
