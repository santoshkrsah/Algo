"""Layered short-straddle algo (Vivek Bajaj / Chirag interview rulebook) on NIFTY weekly and BANKNIFTY monthly options, Dec 2024 - Oct 2026:
core straddle, strike adjustment, averaging, tactical buy, delta scalping, risk layer, drawdown scaling, regime pause; every layer switched
off one at a time; report + Telegram.

    python tools/run_straddle.py
    python tools/run_straddle.py --no-telegram

Needs:  (Algo_Historical_Data) python -m fyers_data.download_calendar bhavidx --start 2024-11-01 --end 2026-10-30
        python -m fyers_data.download_reference index NSE:NIFTYBANK-INDEX BANKNIFTY --start 2024-07-01   (and NSE:INDIAVIX-INDEX INDIAVIX)
"""
import argparse
import sys
import time
import traceback
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

import numpy as np  # noqa: E402
import pandas as pd  # noqa: E402

from backtest import straddle as S  # noqa: E402
from backtest.config import FNO, result_dir  # noqa: E402
from backtest.notify import send, send_document  # noqa: E402
from backtest.straddle_report import check_row, index_series, premium_series, write_html  # noqa: E402

TAG_TRADING, TAG_TECH = "@ayushanand440", "@albert_max"
NAME = "layered_straddle"
LOAD_FROM, START, END, SPLIT = "2024-11-25", "2024-12-02", "2026-10-30", "2026-01-01"
P0 = S.Params()
OFF = dict(adj_pts=None, tactical=False, scalp=False, leg_sl=None, day_sl=None, trail=None, dd_steps=(), pause=False)
CORE = S.with_params(P0, **OFF)
RISK_ONLY = S.with_params(CORE, leg_sl=P0.leg_sl, day_sl=P0.day_sl, trail=P0.trail)

# the setups that get a full page in the report (day table + trade viewer)
VIEW = {"main": ("Rulebook (saare layer)", P0), "core": ("Sirf straddle bechna", CORE), "hold": ("Expiry tak hold", S.with_params(P0, hold="expiry")),
        "avg": ("Averaging ON", S.with_params(P0, avg=True)), "next": ("Agli weekly expiry", S.with_params(P0, expiry="next"))}
BANK_VIEW = {"bank": ("BANKNIFTY monthly (saare layer)", S.with_params(P0, index="BANKNIFTY")),
             "bank_core": ("BANKNIFTY sirf straddle", S.with_params(CORE, index="BANKNIFTY"))}
# extra checks: one thing changed at a time (a one-element row is a heading)
CHECKS = [
    ["Layer ek-ek karke (baaki sab main test jaisa)"],
    ("Main test (saare layer chalu)", P0),
    ("Sirf straddle bechna (koi layer nahi)", CORE),
    ("Straddle + risk layer (leg SL, din ka loss limit, trailing)", RISK_ONLY),
    ("Strike adjustment band", S.with_params(P0, adj_pts=None)),
    ("Tactical buy band", S.with_params(P0, tactical=False)),
    ("Delta scalping band", S.with_params(P0, scalp=False)),
    ("Risk layer band (leg SL, din ka loss limit, trailing)", S.with_params(P0, leg_sl=None, day_sl=None, trail=None)),
    ("Drawdown scaling band", S.with_params(P0, dd_steps=())),
    ("Pause (gap / VIX / Budget) band", S.with_params(P0, pause=False)),
    ("Averaging ON", S.with_params(P0, avg=True)),
    ["Entry aur expiry"],
    ("Entry 9:15 pe", S.with_params(P0, entry_t=0)),
    ("Agli weekly expiry", S.with_params(P0, expiry="next")),
    ("Expiry tak hold (har din 3:15 pe square-off nahi)", S.with_params(P0, hold="expiry")),
    ["Strike adjustment (leg SL ke saath aur bina SL ke)"],
    ("Adjustment: poora straddle 170 pt upar", S.with_params(P0, adj_mode="combined", adj_pts=170.0)),
    ("Adjustment: poora straddle 220 pt upar", S.with_params(P0, adj_mode="combined", adj_pts=220.0)),
    ("Leg SL band, sirf adjustment (ek leg 200 pt)", S.with_params(P0, leg_sl=None)),
    ("Leg SL band, adjustment ek leg 170 pt", S.with_params(P0, leg_sl=None, adj_pts=170.0)),
    ("Leg SL band, adjustment ek leg 220 pt", S.with_params(P0, leg_sl=None, adj_pts=220.0)),
    ["Risk layer ke numbers"],
    ("Leg SL 50%", S.with_params(P0, leg_sl=0.5)),
    ("Leg SL 100%", S.with_params(P0, leg_sl=1.0)),
    ("Din ka loss limit 1%", S.with_params(P0, day_sl=0.01)),
    ("Din ka loss limit 2%", S.with_params(P0, day_sl=0.02)),
    ("Trailing 70% pe", S.with_params(P0, trail=0.7)),
    ("Stop ka confirm nahi (1 minute me touch pe)", S.with_params(P0, confirm=1)),
    ["Delta scalping ke numbers"],
    ("Net delta band +-0.3 / lot", S.with_params(P0, band=0.3)),
    ("Net delta band +-1.0 / lot", S.with_params(P0, band=1.0)),
    ["Slippage"],
    ("Slippage 0", S.with_params(P0, slip_opt=0.0, slip_fut=0.0)),
    ("Slippage zyada: option 1%, futures 0.05%", S.with_params(P0, slip_opt=0.01, slip_fut=0.0005)),
]
BANK_CHECKS = [["Doosra index (BANKNIFTY, sirf monthly expiry)"], ("BANKNIFTY: saare layer", BANK_VIEW["bank"][1]), ("BANKNIFTY: sirf straddle bechna", BANK_VIEW["bank_core"][1])]

SETTINGS = [["Capital", "Rs 30,00,000 (fixed, compounding nahi)", 1], ["Margin", "1/3 capital (Rs 10 lakh), ek straddle ka margin = 10% x lot x index"], ["Period", "2 Dec 2024 se 30 Oct 2026 (fit: 2024 - 2025, verify: 2026)"],
            ["Kharcha", "Rs 20/order, STT (option sell 0.15%, futures 0.05%), exchange, stamp, GST"], ["Option price", "model: asli 1-min index + Black-76 (strike-wise IV)"]]
RULES = [
    "Index: NIFTY (weekly expiry) main test; BANKNIFTY (ab sirf monthly expiry) alag. Period 2 Dec 2024 se 30 Oct 2026 - SEBI ke nayi expiry rules ke baad ka daur. "
    "Fit = Dec 2024 - Dec 2025, verify = Jan - Oct 2026.",
    "Paisa: capital Rs 30 lakh, usme se sirf 1/3 (Rs 10 lakh) margin me. Baaki 2/3 buffer. Lots = Rs 10 lakh / (10% x lot size x index), neeche round. Profit dobara invest nahi hota.",
    "Core: 9:20 pe ATM straddle SELL (CE aur PE, same strike, sabse paas wali expiry). Din ke 3:15 pe sab band (intraday). Kamai theta (time decay) se.",
    "Strike adjustment: ek leg ka premium entry se 200 point upar jaye (2 lagatar 1-min close) to wo leg band karo aur naye strike pe bech do jiska premium doosre leg ke premium ke barabar ho. Din me max 2. "
    "Weekly Nifty me leg SL (75%, lagbhag 110 pt) pehle lag jaata hai, isliye ye layer kam chalti hai; alag se 'SL band' wale checks me test hui.",
    "Averaging: main test me OFF. ON karne par: poore straddle ka premium entry se 20% upar ho to aadhe lots aur bech do (din me ek baar), jab tak din ka loss limit ke aadhe se kam ho.",
    "Tactical buy: leg SL lagne ke baad, index opening range (9:15 - 9:30) ko usi taraf tode (CE leg SL = upar, PE leg SL = neeche) to ATM option BUY. Core lots ka 25%, SL 30% premium, 2:30 ke baad nahi.",
    "Delta scalping: saare option ka net delta (per core lot) nikalo. +-0.5 se bahar jaye aur 3-minute MACD (12, 26, 9) histogram hedge ki taraf palte to futures se hedge. Delta wapas band me aaye to hedge band.",
    "Risk: leg SL = premium entry se 75% upar. Stop tab maana jab 2 lagatar 1-min close level ke paar ho (ek spike pe nahi). Din ka loss limit = capital ka 1.5% (Rs 45,000), lagte hi sab band (kill switch). "
    "Trailing: profit becha hua premium ka 50% ho jaye to uske baad profit apne peak se aadha ho jaye tab sab band.",
    "Drawdown scaling: peak equity se 5% neeche = size aadha, 10% = aur aadha, 15% = phir aadha. Equity wapas aadhe level (2.5% / 5% / 7.5%) tak aane par size dobara badhta hai.",
    "Pause: gap 1% se zyada, ya VIX entry tak pichhle close se 10% upar, ya Budget wala din (1 Feb 2025, 1 Feb 2026) - us din trade nahi.",
    "Kharcha: Rs 20 har order, STT Budget 2026 (1 Apr 2026) se option sell pe 0.15% aur futures sell pe 0.05% (pehle 0.1% / 0.02%), exchange, SEBI, stamp, GST 18%. Slippage option 0.5%, futures 0.02%, har fill pe.",
]
NOTES = [
    "Rulebook me zyadatar numbers nahi the (entry time, adjustment ke points, stop, delta band, drawdown ke aage ke step ...). Jahan 3 value di thi wahan beech wali li; baaki number Ayush ko Telegram pe bataye. "
    "Har number ke aas-paas ki values 'Extra checks' me hain.",
    "Expired index options ka 1-minute data kahin nahi milta. Isliye option ka price model kiya: asli 1-min index + Black-76; har strike ka IV us din ke asli NSE bhavcopy ke open (9:15) aur close (~3:15) se, beech me seedhi line. "
    "Entry (9:20) aur exit (3:15) ka price isliye asli price ke paas hai (jaanch 'Data' card me), par din ke andar ka SL / adjustment ka timing approximate hai.",
    "Rulebook ka '500% annualized' high-volatility daur ka tha. Yahan 23 mahine ka poora daur hai (calm aur tez dono); mahine ke hisaab se table dekho.",
    "Short straddle ka tail risk bada hai: ek gap ya news din saal bhar ka premium kha sakta hai. 'Sabse bura din' har table me hai. Bank Nifty me aur bhi zyada.",
    "SL, adjustment aur kill switch 1-min close pe check hote hain (confirm: 2 close). Tez gap me fill level se door ho sakta hai - ye jaanbujh ke asli jaisa rakha hai.",
    "Margin 10% x contract value maana (asli SPAN alag hota hai, hedge ka benefit bhi milta hai). Isliye 'return on deployed margin' ek andaza hai. Lots poore number me.",
    "'Expiry tak hold' me position raat ko rakhi jaati hai; raat ka move us din ke P&L me jodte hain. Kill switch aur drawdown din ke hisaab se chalte hain.",
    "Rulebook me jo exact baatein nahi thi (averaging ka rule, news / manipulation ki definition) uske liye simple, testable rule banaye (VIX jump, gap, Budget din). Asli news filter alag cheez hai.",
    "Pause wale din trade nahi hua, isliye 'din' me ginti poore trading din se kam ho sakti hai.",
]
FORMULAS = [
    ["Straddle premium", "CE price + PE price (same strike); ATM strike = round(index / step) x step  (step: NIFTY 50, BANKNIFTY 100)"],
    ["Lots", "floor(capital x 1/3 / (10% x lot size x index)) x 0.5^(drawdown stage); kam se kam 1"],
    ["Option price (Black-76)", "CALL = e^(-rT) x [F x N(d1) - K x N(d2)], PUT = e^(-rT) x [K x N(-d2) - F x N(-d1)], d1 = (ln(F/K) + IV^2 x T / 2) / (IV x sqrt(T)), d2 = d1 - IV x sqrt(T), r = 6%"],
    ["Forward F", "index + basis; basis = median(K + (CE - PE) x e^(rT) - index) paas ke 5 strike se, 9:15 aur 3:15 ke liye alag, beech me seedhi line"],
    ["IV per strike", "bhavcopy ka OTM option price (open aur close) se Black-76 ulta; IV(t) = IV(open) + (IV(close) - IV(open)) x min(t, 360) / 360"],
    ["Delta", "CALL: e^(-rT) x N(d1); PUT: -e^(-rT) x N(-d1)"],
    ["Net delta / lot", "sum(side x delta x qty) / lot size / core lots   (side: bechne -1, kharidne +1)"],
    ["Leg SL", "leg ka price >= entry price x 1.75 (2 lagatar 1-min close)"],
    ["Strike adjustment", "leg price >= entry + 200 pt: leg buy back; naya strike = jiska price doosre leg ke price ke sabse paas (+-40 strike me)"],
    ["Kill switch", "din ka P&L <= -1.5% x capital (2 lagatar 1-min close)"],
    ["Trailing", "cycle profit >= 50% x becha hua premium (Rs) ke baad: profit <= 50% x peak profit"],
    ["Tactical buy", "leg SL ke baad index > opening range high (CE SL) / < low (PE SL): ATM option buy, lots = max(1, round(25% x core lots)), SL = 30% premium"],
    ["Hedge", "|net delta / lot| > 0.5 aur 3-min MACD histogram palta: futures (side = -delta ki disha), lots = round(|net delta|); band me aate hi band"],
    ["MACD histogram", "EMA(12) - EMA(26) ka signal EMA(9) se farq, 3-minute index close pe"],
    ["Fill price", "buy = price x (1 + slippage), sell = price x (1 - slippage)"],
    ["Gross P&L", "sum(side x (exit fill - entry fill) x lots x lot size) saare leg aur layer ka"],
    ["Kharcha", "Rs 20 x orders + STT (sell side) + exchange (option 0.03503%, futures 0.00173%) + SEBI 0.0001% + stamp (buy side) + GST 18% x (brokerage + exchange + SEBI)"],
    ["Net P&L", "Gross P&L - kharcha"],
    ["Return %", "net / Rs 30,00,000 x 100 (compounding nahi); return on deployed margin = net / Rs 10,00,000"],
    ["Win rate", "net > 0 wale trade din / kul trade din x 100"],
    ["Profit factor", "jeete dino ka kul profit / hare dino ka kul loss"],
    ["Max drawdown", "roz ke kul P&L ki curve ka sabse bada gir (peak se neeche); size scaling: equity peak se girne ka %"],
]


def chips_of(p: S.Params) -> list:
    nif = p.index == "NIFTY"
    return [["Index", f"{p.index} ({'weekly' if nif else 'monthly'} expiry)", 1], ["Expiry", "sabse paas wali" if p.expiry == "cur" else "agli wali"], ["Entry", f"{_hm(p.entry_t)} pe ATM straddle SELL", 1],
            ["Exit", "3:15 pe square-off" if p.hold == "day" else "expiry din 3:15 (raat ko hold)"],
            ["Strike adjust", "band" if p.adj_pts is None else f"{p.adj_pts:g} pt ({'ek leg' if p.adj_mode == 'leg' else 'poora straddle'}), din me max {p.adj_max}"],
            ["Averaging", "ON" if p.avg else "OFF"], ["Tactical buy", "band" if not p.tactical else f"SL ke baad opening range tode, {p.tac_frac * 100:g}% lots"],
            ["Delta scalping", "band" if not p.scalp else f"+-{p.band:g} / lot, {p.macd_tf}-min MACD"],
            ["Leg SL", "band" if p.leg_sl is None else f"{p.leg_sl * 100:g}%"], ["Din ka loss limit", "band" if p.day_sl is None else f"{p.day_sl * 100:g}% capital"],
            ["Trailing", "band" if p.trail is None else f"{p.trail * 100:g}% ke baad"], ["Stop confirm", f"{p.confirm} lagatar 1-min close"],
            ["Drawdown scaling", "band" if not p.dd_steps else " / ".join(f"{x * 100:g}%" for x in p.dd_steps) + " pe size aadha"],
            ["Pause", "band" if not p.pause else f"gap > {p.gap_max * 100:g}%, VIX > +{p.vix_jump * 100:g}%, Budget din"],
            ["Slippage", f"option {p.slip_opt * 100:g}%, futures {p.slip_fut * 100:g}%", 1]]


def inv_of(p: S.Params) -> list:
    return [["Shuruaati capital", f"Rs {p.capital:,.0f} (fixed, compounding nahi)"], ["Margin me lagaya", f"{p.deploy * 100:.0f}% = Rs {p.capital * p.deploy:,.0f} (baaki buffer)"],
            ["Ek straddle ka margin", f"{p.margin_pct * 100:g}% x lot size x index (SPAN ka andaza)"], ["Lots", "margin budget / ek straddle ka margin, neeche round; drawdown pe aadha"],
            ["Din ka loss limit", "band" if p.day_sl is None else f"{p.day_sl * 100:g}% = Rs {p.day_sl * p.capital:,.0f} (kill switch)"],
            ["Leg SL", "band" if p.leg_sl is None else f"premium {p.leg_sl * 100:g}% upar"],
            ["Drawdown scaling", "band" if not p.dd_steps else " / ".join(f"{x * 100:g}%" for x in p.dd_steps) + " pe size aadha"],
            ["Slippage", f"option {p.slip_opt * 100:g}%, futures {p.slip_fut * 100:g}% har fill"]]


def _hm(t: int) -> str:
    m = 555 + int(t)
    return f"{m // 60}:{m % 60:02d}"


def sub_of(label: str, p: S.Params) -> str:
    return (f"{label} | {p.index} {'weekly' if p.index == 'NIFTY' else 'monthly'} ATM straddle SELL {_hm(p.entry_t)} pe, " + ("3:15 pe square-off" if p.hold == "day" else "expiry tak hold")
            + " | intraday option price model (asli 1-min index + Black-76)")


def run_market(index: str, variants: dict, say, status, view_keys: set) -> tuple[dict, dict, dict, dict]:
    """Every variant {key: Params} through the same days. Returns ({key: log}, series, prem, market)."""
    t0 = time.time()
    mk = S.Market(index, LOAD_FROM, END)
    sims = {k: S.Sim(p) for k, p in variants.items()}
    prem, traded = {}, set()
    days = mk.days(START, END)
    for i, day in enumerate(days):
        d = mk.day(day)
        for k, s in sims.items():
            s.step(d)
            if k in view_keys:
                r = s.log[-1]
                if r["traded"]:
                    traded.add(day)
                    key = f"{index}|{day}|{r['expiry']}|{r['strike']:g}"
                    if key not in prem and r["expiry"] in d.smiles:
                        prem[key] = premium_series(d, r["expiry"], float(r["strike"]))
        if i % 60 == 0:
            status(f"{index}: {i}/{len(days)} din, {len(sims)} setup chal rahe hain")
            print(f"{index} {i}/{len(days)} {time.time() - t0:.0f}s", flush=True)
    series = {f"{index}|{d}": v for d, v in index_series(mk.bars, sorted(traded)).items()}
    return {k: S.frame(s) for k, s in sims.items()}, series, prem, mk


def validate(mk) -> list:
    """Model vs the real 1-minute prices of the NIFTY weekly contracts Fyers still lists (3 expiries, 14-28 Oct 2026, 6-20 days to expiry)."""
    import re
    price_err, rows = [], []
    for f1 in sorted((FNO / "1m" / "options").glob("NIFTY26N[0-9][0-9]*.csv")):
        m = re.match(r"NIFTY26N(\d\d)(\d{5})(CE|PE)$", f1.stem)
        exp, K, cp = "2026-11-" + m.group(1), float(m.group(2)), m.group(3)
        r = pd.read_csv(f1)
        dt = pd.to_datetime(r.datetime)
        r["k"] = (dt.dt.hour * 60 + dt.dt.minute - 555 + 1).values
        for day, g in r.groupby(r.datetime.str[:10]):
            if day not in mk.all_days:
                continue
            d = mk.day(day)
            if exp not in d.smiles or abs(K - d.S[5]) > 150:
                continue
            a = np.full(376, np.nan)
            a[g.k.values.clip(0, 375)] = g.close.values
            a = pd.Series(a).ffill().values
            mod = d.contract(exp, K, cp)[0]
            ok = np.arange(376)
            sel = (ok >= 1) & (ok <= 360) & ~np.isnan(a)
            if sel.sum() >= 200:
                price_err.append(float(np.median(np.abs(mod[sel] / a[sel] - 1)) * 100))
            rows.append((day, exp, K, cp, a[5], a[360], mod[5], mod[360]))
    x = pd.DataFrame(rows, columns=["day", "exp", "K", "cp", "r5", "r360", "m5", "m360"])
    st = x.groupby(["day", "exp", "K"])[["r5", "r360", "m5", "m360"]].sum().dropna()
    st = st[(x.groupby(["day", "exp", "K"]).cp.count() == 2).reindex(st.index).fillna(False)]
    e5, e360 = (st.m5 / st.r5 - 1) * 100, (st.m360 / st.r360 - 1) * 100
    pr, pm = st.r5 - st.r360, st.m5 - st.m360
    return [["Model ki jaanch (asli 1-min NIFTY option prices ke saamne)",
             f"{len(price_err)} contract-din (ATM +-150 pt, {x.day.nunique()} din, expiry 6-20 din door): har minute ka median error {np.median(price_err):.1f}%. "
             f"Straddle ka 9:20 price asli se {e5.mean():+.1f}% door (+ = model thoda zyada), 3:15 pe {e360.mean():+.1f}%. "
             f"9:20 se 3:15 tak straddle bechne ka P&L (har unit): asli {pr.mean():+.1f} pt, model {pm.mean():+.1f} pt ({len(st)} straddle)."],
            ["Jaanch ki seema", "Fyers sirf abhi chalu contracts ka 1-min data deta hai, isliye sirf door ki expiry check ho payi. 0-5 din wali expiry (jahan strategy chalti hai) ka asli 1-min data nahi hai - wahan model ka gamma / IV ka chaal unverified hai."]]


def run(args, status) -> int:
    t0 = time.time()
    out = result_dir(NAME)
    say = (lambda m: None) if args.no_telegram else send
    say(f"Run shuru: Layered Straddle Selling, {START} se {END} (NIFTY weekly + BANKNIFTY monthly). Folder {out.name}.")
    status(f"Data load + NIFTY simulation | {out.name}")
    nv = {k: v[1] for k, v in VIEW.items()}
    nchecks = {f"c{i}": c[1] for i, c in enumerate(CHECKS) if len(c) == 2}
    allp = {**nv, **nchecks}
    logs, series, prem, mk = run_market("NIFTY", allp, say, status, set(VIEW))
    status("BANKNIFTY simulation")
    bv = {k: v[1] for k, v in BANK_VIEW.items()}
    blogs, bseries, bprem, bmk = run_market("BANKNIFTY", bv, say, status, set(BANK_VIEW))
    print(f"simulations done {time.time() - t0:.0f}s", flush=True)
    series.update(bseries)
    prem.update(bprem)

    # consistency: layers add up to gross, equity ends at the sum of net
    for k, f in {**logs, **blogs}.items():
        gap = float((f[["pnl_core", "pnl_avg", "pnl_tac", "pnl_hedge"]].sum(axis=1) - f.gross).abs().max())
        assert gap < 1.0, (k, gap)

    status("Extra checks table + report")
    runs = {k: logs[k] for k in VIEW} | {k: blogs[k] for k in BANK_VIEW}
    labels = {k: v[0] for k, v in {**VIEW, **BANK_VIEW}.items()}
    params = {k: v[1] for k, v in {**VIEW, **BANK_VIEW}.items()}
    checks, ci = [], 0
    for c in CHECKS:
        if len(c) == 1:
            checks.append(c)
        else:
            checks.append(check_row(c[0], logs[f"c{ci}"], P0.capital, SPLIT))
        ci += 1
    for c in BANK_CHECKS:
        checks.append(c if len(c) == 1 else check_row(c[0], blogs["bank" if "saare" in c[0] else "bank_core"], P0.capital, SPLIT))
    m = logs["main"]
    chk = validate(mk)
    lots = sorted(set(m[m.traded == 1].lot.dropna().astype(int)))
    data_info = [["Index 1-minute", "NIFTY 50 (Fyers 1-min) + India VIX (1-min), 1 Jul 2024 se 30 Oct 2026; BANKNIFTY 1-min bhi"],
                 ["Option data", "NSE index option bhavcopy (NIFTY, BANKNIFTY ke 3 paas wali expiry, expired bhi): har strike ka din ka Open / Close / trades"],
                 ["Din", f"{int(m.traded.sum())} trade din + {int((m.traded == 0).sum())} pause din = {len(m)} trading din ({m.date.min()} se {m.date.max()})"],
                 ["Lot size (data se)", "NIFTY: " + ", ".join(str(x) for x in lots)], *chk, ["Budget din", "1 Feb 2025, 1 Feb 2026 (pause)"]]
    pause_days = m[m.pause != ""]
    data_info.append(["Pause ke karan", f"{len(pause_days)} din: " + ", ".join(sorted(set(x.split(" ")[0] if x.startswith(("gap", "VIX")) else x for x in pause_days.pause)))])
    chips = {k: chips_of(p) for k, p in params.items()}
    subs = {k: sub_of(labels[k], p) for k, p in params.items()}
    invs = {k: inv_of(p) for k, p in params.items()}
    write_html(out / "report.html", runs, labels, chips, subs, invs, "main", P0.capital, P0.deploy, RULES, NOTES, checks, FORMULAS, data_info, series, prem, [START, END],
               "NSE index option bhavcopy (daily) + Fyers 1-min index / VIX; option ka din ke andar ka price model", SETTINGS, SPLIT, "0.5% option, 0.02% futures")
    for k, f in runs.items():
        f.drop(columns=["events"]).to_csv(out / f"days_{k}.csv", index=False)

    # ---- summary
    def line(label, f):
        s = S.stats(f, P0.capital)
        fit, ver = f[f.date < SPLIT].net.sum(), f[f.date >= SPLIT].net.sum()
        return f"{label}: {s['days']} din, win {s['win_pct']:.0f}%, net Rs {s['net']:,.0f} ({s['ret_pct']:.1f}%), PF {s['profit_factor'] or 0:.2f}, max DD Rs {s['max_dd']:,.0f}, sabse bura din Rs {s['worst']:,.0f} | fit Rs {fit:,.0f}, verify Rs {ver:,.0f}"
    lines = [line(labels[k], f) for k, f in runs.items()]
    lay = {n: float(m[f"pnl_{n}"].sum()) for n in ("core", "avg", "tac", "hedge")}
    summary = "\n".join(lines + ["", "Layer (main): " + ", ".join(f"{n} Rs {v:,.0f}" for n, v in lay.items()) + f", slippage Rs {m.slip.sum():,.0f}, kharcha Rs {m.costs.sum():,.0f}", "", "Checks:"]
                        + [f"{c[0]}: {c[1]} din, net Rs {c[3]:,.0f}, PF {c[4] or 0:.2f}, worst Rs {c[6]:,.0f}, fit Rs {c[8]:,.0f}, verify Rs {c[9]:,.0f}" for c in checks if len(c) > 1])
    (out / "summary.txt").write_text(summary, encoding="utf-8")
    print(summary, flush=True)
    s = S.stats(m, P0.capital)
    core = S.stats(logs["core"], P0.capital)
    bank = S.stats(blogs["bank"], P0.capital)
    msg = (f"Layered Straddle Selling ka result ({START} - {END}) {TAG_TRADING}\n"
           f"NIFTY weekly, 9:20 ATM straddle SELL + sab layer, capital Rs 30L (1/3 margin), compounding nahi.\n"
           f"{s['days']} din, win {s['win_pct']:.0f}%, net Rs {s['net']:,.0f} ({s['ret_pct']:.1f}%), PF {s['profit_factor'] or 0:.2f}, max DD Rs {s['max_dd']:,.0f}, sabse bura din Rs {s['worst']:,.0f}.\n"
           f"Fit (2024-25) Rs {m[m.date < SPLIT].net.sum():,.0f}, verify (2026) Rs {m[m.date >= SPLIT].net.sum():,.0f}.\n"
           f"Layer: core Rs {lay['core']:,.0f}, tactical Rs {lay['tac']:,.0f}, hedge Rs {lay['hedge']:,.0f}; slippage Rs {m.slip.sum():,.0f}, kharcha Rs {m.costs.sum():,.0f}.\n"
           f"Sirf straddle (koi layer nahi): net Rs {core['net']:,.0f}, max DD Rs {core['max_dd']:,.0f}, sabse bura din Rs {core['worst']:,.0f}.\n"
           f"BANKNIFTY monthly (saare layer): net Rs {bank['net']:,.0f}, sabse bura din Rs {bank['worst']:,.0f}.")
    if not args.no_telegram:
        send(msg)
        send_document(out / "report.html", f"Layered Straddle Selling report ({out.name})")
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
            send(f"Layered Straddle run me error {TAG_TECH}:\n{err[-1500:]}")
        return 1


if __name__ == "__main__":
    sys.exit(main())
