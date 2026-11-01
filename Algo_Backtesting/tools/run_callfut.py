"""Intraday CALL + FUTURE hedge (2 lots delta-0.50 CALL BUY + 1 lot FUTURE SELL, same expiry, 9:15 -> same day) on the last six
months of NSE stock F&O; report + Telegram.

    python tools/run_callfut.py
    python tools/run_callfut.py --no-telegram
"""
import argparse
import sys
import time
import traceback
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

import numpy as np  # noqa: E402

from backtest import callfut as cf  # noqa: E402
from backtest.callfut_report import grid_rows, intraday_series, write_html  # noqa: E402
from backtest.config import result_dir  # noqa: E402
from backtest.notify import send, send_document  # noqa: E402

TAG_TRADING, TAG_TECH = "@ayushanand440", "@albert_max"
NAME = "call_fut_intraday"
LOAD_FROM, START, END = "2026-04-24", "2026-05-01", "2026-10-28"
BASE = "close"
P0 = cf.Params()
SETTINGS = [["Universe", "NSE F&O stocks (~210), current-month expiry", 1], ["Position", "2 lot CALL BUY + 1 lot FUTURE SELL (same stock, same expiry)", 1],
            ["Strike", "delta 0.50 ke sabse paas (0.40-0.60 ke andar)", 1], ["Entry", "9:15 open (asli NSE open), usi din exit"],
            ["Roz", "top 5 stocks (kal sabse zyada trade wala strike), paisa capital ke andar"], ["Slippage", "option 0.5%, future 0.05% har fill", 1],
            ["Kharcha", "Rs 20/order, STT option 0.15% + future 0.05% sell, exchange, stamp, GST"], ["Capital", "Rs 10,00,000 (fixed, compounding nahi)"],
            ["Margin", "future ka 20% contract value"], ["Expiry din", "trade nahi"]]
RULES = [
    "Samay: 1 May 2026 se 28 Oct 2026 (last 6 mahine), NSE stock F&O. Har trade INTRADAY - subah lo, usi din band.",
    "Position: 2 lot CALL BUY (delta ~0.50 wala strike) + usi stock ka 1 lot FUTURE SELL. Dono current month expiry ke. "
    "CALL ka delta 2 x 0.50 = 1 aur FUTURE ka -1, yaani shuruaat me position ka delta lagbhag 0 (stock upar ya neeche, seedha asar kam).",
    "Delta 0.50 wala strike: kal ke close pe har CALL strike ka IV (Black-76) nikala, phir aaj ke 9:15 future price pe delta. "
    "Jo strike 0.50 ke sabse paas (0.40-0.60 ke andar) wahi liya. Strike me kal kam se kam 50 trade, future me 100 trade.",
    "Entry: 9:15 pe - option aur future dono ka aaj ka asli NSE open price.",
    "Exit (4 tarike alag test kiye): (1) din ka close (main test, asli NSE close), (2) target 10% / SL 10%, (3) target 20% / SL 15% - "
    "position ka P&L premium ka itna % ho jaye to us minute band, warna close pe, (4) 12 baje exit.",
    "Roz max 5 stocks: jinke chune hue CALL strike me kal sabse zyada trade hue. Sabka paisa (premium + future margin) Rs 10 lakh ke andar.",
    "Expiry wale din trade nahi (stock F&O physical settle hota hai).",
    "Kharcha: Rs 20 har order (4 order), STT option sell premium pe 0.15% aur future sell pe 0.05% (Budget 2026 rates), exchange, SEBI, stamp, GST 18%. "
    "Slippage: option 0.5%, future 0.05%, har buy aur sell pe.",
]
NOTES = [
    "Rule me sirf position bataya tha (entry signal, time, exit nahi), isliye har din 9:15 entry aur din ke close pe exit main test hai. Baaki exit 'Saransh' ki table me.",
    "2 CALL - 1 FUTURE asal me 1 CALL + 1 PUT (straddle) jaisa hai: stock kisi bhi taraf bada move kare to profit, stock ruka rahe to premium (theta) ghatta hai. "
    "Extra checks me asli straddle (1 lot CE + 1 lot PE) bhi test kiya.",
    "Expired options ka 1-minute data Fyers nahi deta. Isliye main test (9:15 open se close) sirf asli NSE bhavcopy price pe hai. Target/SL aur 12 baje wale exit ke liye "
    "din ke andar CALL ka price model kiya: asli 1-min future + Black-76, IV subah ke IV se shaam ke IV tak seedhi line me.",
    "NSE ka 'close' price aakhri 30 minute (3:00-3:30) ka average hota hai, yaani lagbhag 3:15 ka price.",
    "Option ka 'open' us din ka pehla trade hai. Kam trade wale strike me ye 9:15 ke baad ka ho sakta hai; isliye kal 50+ trade wala strike hi liya.",
    "Future margin 20% maana (hedge ka margin benefit nahi liya), isliye asli me paisa thoda kam lagega. Return % capital Rs 10 lakh pe hai.",
    "Budget 2026 ke STT rates (option 0.15%, future 0.05%) liye; purane rates (0.1% / 0.02%) wala result extra checks me hai.",
]
FORMULAS = [
    ["IV (kal)", "Black-76: CALL price = e^(-rT) x [F x N(d1) - K x N(d2)], d1 = (ln(F/K) + IV^2 x T/2) / (IV x sqrt(T)), d2 = d1 - IV x sqrt(T); r = 6%, F = future close"],
    ["Delta", "e^(-rT) x N(d1), aaj ke 9:15 future price aur kal ke IV se; strike = |delta - 0.50| sabse kam"],
    ["T (time)", "expiry 3:30 tak bache minute / (365 x 1440)"],
    ["Net delta", "2 x delta(CALL) - 1 (FUTURE)"],
    ["Entry", "CALL buy = open x (1 + 0.5%); FUTURE sell = open x (1 - 0.05%)"],
    ["Exit", "CALL sell = exit price x (1 - 0.5%); FUTURE buy = exit price x (1 + 0.05%)"],
    ["Gross P&L", "2 x lot x (CALL sell - CALL buy) - 1 x lot x (FUTURE buy - FUTURE sell)"],
    ["Target / SL", "Gross P&L >= target% x premium paid (2 x lot x CALL buy) -> target; <= -SL% x premium -> SL; har minute 9:16-3:14 check"],
    ["Model CALL price (din ke andar)", "Black-76 with asli 1-min future, IV = IV(9:15) + (IV(close) - IV(9:15)) x minute / 360"],
    ["Kharcha", "Rs 20 x 4 + STT (0.15% option sell + 0.05% future sell) + exchange (0.03503% option, 0.00173% future) + SEBI 0.0001% + stamp (0.003% option buy, 0.002% future buy) + GST 18% (brokerage + exchange + SEBI)"],
    ["Net P&L", "Gross P&L - kharcha"],
    ["Paisa laga (ek position)", "2 x lot x CALL premium + 20% x lot x future price"],
    ["Return %", "net / Rs 10,00,000 x 100 (compounding nahi)"],
    ["Win rate", "net > 0 wale trade / kul trade x 100"],
    ["Profit factor", "jeete trades ka kul profit / hare trades ka kul loss"],
    ["Max drawdown", "roz ke kul P&L ki curve ka sabse bada gir (peak se neeche)"],
]


def run(args, status) -> int:
    t0 = time.time()
    out = result_dir(NAME)
    say = (lambda m: None) if args.no_telegram else send
    say(f"Run shuru: CALL + FUTURE hedge intraday, {START} se {END}. Folder {out.name}.")
    status(f"Data load (bhavcopy + 1-min futures) | {out.name}")
    d = cf.load(LOAD_FROM, END)
    mins = cf.minute_futures(sorted(d["fut"].symbol.unique()), START, END)
    status("Har stock-din ka strike, entry, exit nikaal raha hoon")
    c = cf.candidates(d, P0, START, END, mins)
    c.to_pickle(out / "candidates.pkl")
    t = cf.pick(c, P0)
    print(f"candidates {len(c)}, picked {len(t)}, {time.time() - t0:.0f}s", flush=True)

    grid = grid_rows("Roz top 5", t, P0.capital) + grid_rows("Roz top 10", cf.pick(c, cf.with_params(P0, top=10)), P0.capital) + grid_rows("Saare stocks", c, P0.capital)

    status("Extra checks chal rahe hain")

    def chk(label, tt, key="close"):
        s = cf.stats(tt[key + "_net"], tt.date, P0.capital)
        return [label, s["trades"], s["win_pct"], s["net"], s["profit_factor"], s["max_dd"], s["avg"]]

    def re_exit(label, **kw):
        q = cf.with_params(P0, **kw)
        return chk(label, cf.exits(t.copy(), q, None))

    def re_cand(label, top=5, **kw):
        q = cf.with_params(P0, top=top, **kw)
        cc = cf.candidates(d, q, START, END, None)
        return chk(label, cf.pick(cc, q))

    checks = [chk("Kuch nahi badla (main test)", t),
              re_exit("Slippage 0 (option aur future dono)", slip_opt=0.0, slip_fut=0.0),
              None,                                                    # filled below: no slippage and no costs at all
              re_exit("Slippage zyada: option 1%, future 0.1%", slip_opt=0.01, slip_fut=0.001),
              re_exit("Purane STT rates (option 0.1%, future 0.02%)", stt_opt=0.001, stt_fut=0.0002),
              chk("Expiry ke aakhri hafte me trade nahi (7 din se zyada bache)", t[t.dte > 7]),
              chk("Roz top 10 stocks", cf.pick(c, cf.with_params(P0, top=10))),
              chk("Saare stocks (har din har stock)", c),
              chk("Exit 12 baje (model)", t, "noon"),
              re_cand("Strike delta 0.40", delta=0.40),
              re_cand("Strike delta 0.60", delta=0.60),
              re_cand("Asli straddle: 1 lot CE + 1 lot PE (same strike), future nahi", call_lots=1, fut_lots=0, put_lots=1),
              re_cand("Next month expiry ke contract", series="next"),
              re_cand("Sirf liquid strike (kal 500+ trade)", min_trades=500)]
    raw = t.copy()
    q = cf.with_params(P0, slip_opt=0.0, slip_fut=0.0)
    raw = cf.exits(raw, q, None)
    checks[2] = ["Slippage 0 aur kharcha 0 (sirf strategy ka dum)", len(raw), float((raw.close_gross > 0).mean() * 100), float(raw.close_gross.sum()),
                 float(raw.close_gross[raw.close_gross > 0].sum() / -raw.close_gross[raw.close_gross <= 0].sum()), None, float(raw.close_gross.mean())]
    print(f"checks done {time.time() - t0:.0f}s", flush=True)

    m = c[c.model_ok]
    hi_err = float(((m.m_high / m.c_high - 1).abs() * 100).median())
    lo_err = float(((m.m_low / m.c_low - 1).abs() * 100).median())
    data_info = [["Daily", f"NSE F&O bhavcopy (futures + options, expired bhi), {LOAD_FROM} se {END}"],
                 ["1-minute", "Fyers current-month future (continuous, expiry ke baad roll), 1 May - 28 Oct 2026"],
                 ["Expiries", ", ".join(e for e in d["expiries"] if START <= e <= "2026-11-30")],
                 ["Din", f"{c.date.nunique()} trading din (expiry din chhod ke), {c.symbol.nunique()} stocks"],
                 ["Stock-din (sab)", f"{len(c):,} (delta 0.40-0.60 strike mila, kal liquid, aaj trade hua)"],
                 ["Model ki jaanch", f"{len(m):,} stock-din pe model ka din ka CALL high asli high se median {hi_err:.1f}% door, low {lo_err:.1f}% door"],
                 ["1-min data nahi mila", f"{int((~c.model_ok).sum())} stock-din (wahan target/SL wale exit close pe)"]]
    series = intraday_series(t, mins, P0)
    t.to_csv(out / "trades_top5.csv", index=False)
    status("Report (HTML) likh raha hoon")
    write_html(out / "report.html", t, grid, P0.capital, RULES, NOTES, [x for x in checks if x], FORMULAS, data_info, BASE, series,
               [START, END], "NSE bhavcopy (daily) + Fyers 1-min futures", P0, SETTINGS, {})
    s = cf.stats(t.close_net, t.date, P0.capital)
    lines = [f"{g[0]} | {g[2]}: {g[3]} trade, win {g[4]:.0f}%, net Rs {g[5]:,.0f}, PF {g[6] or 0:.2f}" for g in grid]
    summary = "\n".join(lines + ["", "Checks:"] + [f"{x[0]}: {x[1]} trade, net Rs {x[3]:,.0f}, avg Rs {x[6] or 0:,.0f}" for x in checks if x])
    (out / "summary.txt").write_text(summary, encoding="utf-8")
    print(summary, flush=True)
    br_g, br_c = float(t.close_gross.sum()), float(t.close_costs.sum())
    msg = (f"CALL + FUTURE hedge INTRADAY ka result (1 May - 28 Oct 2026) {TAG_TRADING}\n"
           f"2 lot CALL (delta 0.50) BUY + 1 lot FUT SELL, 9:15 entry, close exit, roz top 5, capital Rs 10L fixed.\n"
           f"Trade {s['trades']}, win {s['win_pct']:.0f}%, net Rs {s['net']:,.0f} ({s['ret_pct']:.1f}%), PF {s['profit_factor'] or 0:.2f}, max DD Rs {s['max_dd']:,.0f}.\n"
           f"Gross (slippage ke baad) Rs {br_g:,.0f}, kharcha Rs {br_c:,.0f}. Bina slippage-kharcha: Rs {checks[2][3]:,.0f}.\n"
           + "\n".join(lines[:4]) + "\nReport HTML neeche.")
    if not args.no_telegram:
        send(msg)
        send_document(out / "report.html", f"CALL + FUTURE hedge intraday report ({out.name})")
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
            send(f"CALL + FUTURE hedge run me error {TAG_TECH}:\n{err[-1500:]}")
        return 1


if __name__ == "__main__":
    sys.exit(main())
