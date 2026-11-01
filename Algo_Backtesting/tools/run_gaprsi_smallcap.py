"""Gap + RSI(10) + 21-EMA on SMALL CAP companies (market cap above Rs 1,000 Cr ON THAT DAY), slippage 0.5% - long side only.
Same engine and daily data as tools/run_gaprsi.py (needs results/13_gap_rsi_ema/daily.pkl); writes its own numbered result folder.

    python tools/run_gaprsi_smallcap.py
    python tools/run_gaprsi_smallcap.py --no-telegram        # report + summary.txt only, nothing is sent
"""
import argparse
import pickle
import sys
import time
import traceback
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import run_gaprsi as B  # noqa: E402  (rules / notes / formulas of the F&O test, adapted below)
from backtest import gaprsi as G  # noqa: E402
from backtest import gaprsi_data as D  # noqa: E402
from backtest.config import result_dir  # noqa: E402
from backtest.gaprsi_report import series_for, write_html  # noqa: E402
from backtest.notify import send, send_document  # noqa: E402

TAG_TRADING, TAG_TECH = B.TAG_TRADING, B.TAG_TECH
NAME = "gap_rsi_ema_smallcap"
BASE = "long|2|ema"
SLIP = 0.005
MIN_MCAP = 1000.0
META = {"title": "Gap + RSI + 21-EMA (Small cap)", "sub": "NSE small cap, market cap Rs 1,000 Cr+ · daily backtest & trade audit",
        "universe": "NSE small cap (market cap us din Rs 1,000 Cr+)", "slip_pct": 0.5, "sides": ["long"],
        "settings": [["Universe", "Small cap, market cap us din Rs 1,000 Cr+", 1], ["Slippage", "0.5% har fill (buy aur sell dono)", 1],
                     ["Kharcha", "Delivery: STT 0.1% buy + sell, exchange, stamp, GST, DP"], ["Capital", "Rs 10,00,000 (fixed, compounding nahi)"],
                     ["Risk per trade", "1% = Rs 10,000"], ["Limit", "max 10 stock, 20% per stock, leverage nahi"], ["Entry", "gap wale din ka close"],
                     ["Circuit", "pura lock din (open = high = low = close) pe entry nahi"]]}

STOCKS_RULE = ("Stocks: sirf SMALL CAP companies jinka market cap us din Rs 1,000 Cr se upar ho. Market cap 'us din' ka = AMFI market cap (30 Jun 2026) x us din ka close / "
               "(Jan-Jun 2026 ka average close). Small cap = us din ki market cap ranking me 251va number ya neeche (AMFI definition: top 100 large, agle 150 mid, baaki small). "
               "Cash market me kharidna-bechna.")
COST_RULE = ("Kharcha: delivery - brokerage 0, STT 0.1% (buy + sell), exchange, SEBI, stamp 0.015% (buy), GST, DP charge Rs 15.34 + GST. "
             "Slippage 0.5% har fill pe (buy aur sell dono), yaani ek trade me kul lagbhag 1%.")


def adapt_rules() -> list[str]:
    out, hit = [], set()
    for r in B.RULES:
        if r.startswith("Stocks:"):
            out.append(STOCKS_RULE); hit.add("stocks")
        elif r.startswith("Gap-up = "):
            out.append("Gap-up = aaj ka open pichhle din ke High se upar, aur gap % (open vs pichhla close) kam se kam T. T = 1%, 2%, 3% - teeno alag chala ke dekhe."); hit.add("gap")
        elif r.startswith("LONG entry"):
            out.append(r + " Jis din price pura lock ho (open = high = low = close) us din entry nahi - upper circuit me kharid nahi sakte."); hit.add("long")
        elif r.startswith("SHORT entry"):
            out.append("SHORT side is test me nahi: small cap stocks me F&O futures nahi hote aur cash market me raat bhar short nahi hota."); hit.add("short")
        elif r.startswith("Stop-loss"):
            out.append("Stop-loss (close ke hisaab se): din ka close 21 EMA se neeche, ya gap fill (close pichhle din ke close se neeche) - jo pehle ho."); hit.add("stop")
        elif r.startswith("Kharcha"):
            out.append(COST_RULE); hit.add("cost")
        else:
            out.append(r)
    assert hit == {"stocks", "gap", "long", "short", "stop", "cost"}, hit
    return out


NOTES = [
    "Summary me exact numbers nahi the, isliye rulebook ke [A] parameter liye; har 'maana hua' number Extra checks me ek-ek karke badal ke dikhaya hai. "
    "Profit kitne bade winners pe tika hai, ye Saransh -> Saare numbers me 'Top 5% trade ka hissa' me dikhta hai.",
    "Market cap 'us din' ka andaaza hai (AMFI mcap x price ka ratio). Naye shares (QIP / ESOP) ya buyback se asli past mcap alag ho sakta hai. Aaj ki AMFI 'Small' list ka result "
    "Extra checks me hai - usme wo companies bhi aa jati hain jo tab chhoti thi aur baad me badi hui (look-ahead).",
    "Monthly confirm 10-mahine EMA se (20-mahine ke liye data kam). RSI = 10-din RSI ka 3-din average, 60 se 90 ke beech.",
    "Stop 'close' pe lagta hai, exit us din ka close. Small cap me circuit (5 / 10 / 20%) hota hai: pura lock din chhoda, par aadha lock ya kam volume ke din asli me fill alag ho sakta hai, "
    "aur lower circuit me stop pe nikalna mushkil ho sakta hai. Slippage 0.5% isi liye (pehle test me 0.1% tha; 0% / 0.1% / 1% extra checks me).",
    "Data saaf kiya: Fyers ka data zyada tar pehle se split / bonus adjusted tha, 13 events khud adjust kiye; 12 galat price print hataye; 19 demerger jaise jump ke baad 60 din signal nahi; 25% se bada gap trade nahi.",
    "SHORT side is test me nahi (small cap me possible nahi). Ek stock me ek hi position; same din kai signal ho to bade gap wale pehle; cash / limit se size aadhe se kam mile to trade chhoda.",
    "Bajaj Finance ka triangle example aur promoter buying jaise fundamental inputs test nahi kiye.",
]


def adapt_formulas() -> list[list]:
    out, seen = [], set()
    for name, f in B.FORMULAS:
        if name in ("SHORT signal", "SHORT stop hit", "Kharcha (SHORT, futures)"):
            continue
        if name == "Gap-up / gap-down":
            name, f = "Gap-up", "Gap-up: open > pichhla High"
        elif name == "Entry (E)":
            f = "E = gap wale din ka close;   fill: Buy = E x (1 + s),  Sell = Exit x (1 - s),   s = 0.005"
        elif name == "Stop level (S)":
            f = "S = max(F, EMA21 entry pe)"
        elif name == "Target (R)":
            f = "Target = E + R x Risk;  High >= Target to exit = Open (agar open paar ho) warna Target"
        elif name == "Gross P&L":
            f = "Gross = Qty x (Exit - Entry)"
        out.append([name, f])
        seen.add(name)
        if name == "Gap %":
            out.append(["Market cap (us din)", "Mcap = AMFI mcap x Close / (Jan-Jun 2026 ka average Close)"])
            out.append(["Small cap", "us din Mcap ki ranking (1 = sabse bada) 251 ya neeche  AUR  Mcap >= Rs 1,000 Cr"])
            out.append(["Lock din", "High = Low (din me ek hi price)  ->  entry nahi"])
    assert {"Gap-up", "Entry (E)", "Stop level (S)", "Target (R)", "Gross P&L"} <= seen
    return out


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--no-telegram", action="store_true")
    ap.add_argument("--name", default=NAME)
    a = ap.parse_args()
    tell = (lambda m: None) if a.no_telegram else send
    out = result_dir(a.name)
    t0 = time.time()
    tell("GAP + RSI + 21-EMA (SMALL CAP) BACKTEST STARTED: small cap, market cap us din Rs 1,000 Cr+, slippage 0.5%, long side, gap 1 / 2 / 3%, 8 exit tarike. Progress har 10 min.")
    try:
        _, adj = pickle.load(open(result_dir("gap_rsi_ema") / "daily.pkl", "rb"))
        ind = D.add_mcap(D.indicators(adj))
        nifty = D.nifty_daily()
        arrs = G._arrays(ind, 10)
        fno = frozenset(D.fno_symbols())
        u = D.universe_table()
        static_small = frozenset(u[u.category == "Small"].sym)
        base = G.Params(min_mcap=MIN_MCAP, cap_class="small", skip_locked=True, slip=SLIP)
        runs = {}
        for gap in G.GAPS:
            p = G.with_params(base, gap=gap)
            sg = G.signals(ind, p, "long")
            for ex in G.EXIT:
                runs[("long", gap, ex)] = B.run_combo(ind, nifty, p, "long", ex, sg, arrs)
            tell(f"Small cap: gap {gap:g}% done - {len(sg)} signal ({sg.symbol.nunique()} stocks), {len(runs[('long', gap, 'ema')])} trade (21-EMA exit). {(time.time() - t0) / 60:.1f} min")
        for (side, gap, ex), t in runs.items():
            t.to_csv(out / f"trades_{side}_{gap:g}_{ex}.csv", index=False)

        def chk(name, ex="ema", **kw):
            ar = G._arrays(ind, kw["monthly_n"]) if kw.get("monthly_n") not in (None, 0, 10) else arrs
            p = G.with_params(base, gap=2.0, **kw)
            t = B.run_combo(ind, nifty, p, "long", ex, arrs=ar)
            s = G.stats(t, p.capital)
            return [name, s["trades"], s["win_pct"], round(s["net"]), s["profit_factor"], s["avg_r"], round(s["max_dd"])]

        checks = [
            chk("Jaisa rules me hai (small cap, mcap 1,000 Cr+, slippage 0.5%)"),
            chk("Slippage 0.1% (pehle test jaisa)", slip=0.001),
            chk("Slippage 0%", slip=0.0),
            chk("Slippage 1%", slip=0.01),
            chk("Aaj ki AMFI 'Small' list (market cap us din ka nahi)", min_mcap=0.0, cap_class=None, universe=static_small),
            chk("Market cap Rs 2,000 Cr+ (us din)", min_mcap=2000.0),
            chk("Market cap Rs 5,000 Cr+ (us din)", min_mcap=5000.0),
            chk("Mid + small cap dono (Rs 1,000 Cr+)", cap_class="midsmall"),
            chk("F&O stocks (pehle wala universe), 0.5% slippage", min_mcap=0.0, cap_class=None, universe=fno),
            chk("Liquidity: 20-din turnover Rs 1 Cr+", min_turn=1e7),
            chk("Liquidity: 20-din turnover Rs 5 Cr+", min_turn=5e7),
            chk("Lock din ki shart nahi", skip_locked=False),
            chk("Resistance (20-din high) wali shart nahi", resist=False),
            chk("Monthly confirm nahi", monthly_n=0),
            chk("RSI 70 se upar (60 ki jagah)", rsi_lo=70),
            chk("Gap-fill = pichhle din ka High", fill="hl"),
            chk("Entry agle din ke open pe", entry="next_open"),
            chk("Ek saath max 5 stock", max_pos=5),
            chk("Risk 0.5% per trade", risk=0.005),
            chk("Risk 2% per trade", risk=0.02),
            chk("Target 1:2 exit (21-EMA ki jagah)", ex="rr2"),
        ]
        tell(f"Small cap: {len(runs)} combination aur {len(checks)} extra check ho gaye. Report ban rahi hai. {(time.time() - t0) / 60:.1f} min")
        p2 = G.with_params(base, gap=2.0)
        sig = G.signals(ind, p2, "long")
        sig_nolock = G.signals(ind, G.with_params(p2, skip_locked=False), "long")
        ind["year"] = ind.date.str[:4]
        pit = ind[(ind.mcap_pit >= MIN_MCAP) & (ind.rank_pit > 250)]
        per_year = pit.groupby("year").apply(lambda g: g.groupby("date").symbol.nunique().mean())
        first = min(t.entry_date.min() for t in runs.values() if len(t))
        info = [
            ["Data", f"Daily candle (15-min candles se bana), {ind.symbol.nunique():,} stocks, {ind.date.min()} se {ind.date.max()}"],
            ["Universe (us din)", "Roz ke average small cap (mcap Rs 1,000 Cr+ us din): " + ", ".join(f"{y} ~{int(v)}" for y, v in per_year.items()) + " stocks"],
            ["AMFI list", f"30 Jun 2026: {len(static_small):,} small cap (sab Rs 1,002 Cr+; hamare data ka cut-off ~Rs 1,000 Cr hai, isliye aaj ki list me 1,000 Cr ki shart se koi nahi hatta)"],
            ["Signal (gap 2%)", f"{len(sig)} gap-up din, {sig.symbol.nunique()} stocks; pura lock din ki wajah se {len(sig_nolock) - len(sig)} signal hataye"],
            ["Pehla trade", f"{first} (monthly 10-EMA ke liye 10 mahine ka data chahiye)"],
            ["Saaf kiya", "12 galat price print hataye, 13 split/bonus adjust kiye, 19 bade jump (demerger) pe signal band / forced exit"],
            ["Nifty", "Nifty 50 index 1-min data se daily close; 200-din average ke upar / neeche = bull / bear"],
        ]
        series = series_for(ind, runs)
        write_html(out / "report.html", runs, base.capital, adapt_rules(), NOTES, checks, adapt_formulas(), info, BASE, series, [ind.date.min(), ind.date.max()],
                   "Daily data: equity 15-min candles se bana, 1 Oct 2021 se 28 Oct 2026. Pehla trade " + first + ".", META)
    except Exception:
        tell(f"{TAG_TECH} Small cap Gap RSI EMA backtest FAILED:\n{traceback.format_exc()[-1500:]}")
        raise
    lines = []
    for (side, gap, ex), t in runs.items():
        if ex in ("ema", "rr2", "rr3", "monthly"):
            s = G.stats(t, base.capital)
            lines.append(f"gap {gap:g}% {ex}: {s['trades']} tr | win {s['win_pct'] or 0:.0f}% | net Rs {s['net']:,.0f} | PF {s['profit_factor'] or 0:.2f} | avg R {s['avg_r'] or 0:+.2f} | max DD Rs {s['max_dd']:,.0f}")
    msg = (f"{TAG_TRADING} GAP + RSI + 21-EMA (SMALL CAP) BACKTEST DONE ({(time.time() - t0) / 60:.1f} min). Small cap mcap Rs 1,000 Cr+ (us din), slippage 0.5%, "
           "capital Rs 10 L, 1% risk, long only.\n" + "\n".join(lines))
    (out / "summary.txt").write_text(msg, encoding="utf-8")
    tell(msg)
    print(msg)
    if not a.no_telegram:
        send_document(out / "report.html", "Gap + RSI + 21-EMA SMALL CAP report (browser me kholo). Home page pe 'Test ki settings' strip, Rules tab me rules aur Formulas.")
    print("done ->", out, f"{(time.time() - t0) / 60:.1f} min")
    return 0


if __name__ == "__main__":
    sys.exit(main())
