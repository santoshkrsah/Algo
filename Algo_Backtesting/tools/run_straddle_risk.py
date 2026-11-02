"""Report 16 ki extra-checks row "Straddle + risk layer" ab apna main test: NIFTY weekly 9:20 ATM straddle SELL, 3:15 square-off, sirf
risk layer (leg SL 75%, din ka loss limit 1.5%, trailing 50%). Strike adjustment, averaging, tactical buy, delta scalping, drawdown
scaling aur pause band. Same data, engine, kharcha aur slippage jaisa tools/run_straddle.py; extra checks is setup ke aas-paas
ek-ek cheez badalte hain.

    python tools/run_straddle_risk.py
    python tools/run_straddle_risk.py --no-telegram
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

import run_straddle as R  # noqa: E402
from backtest import straddle as S  # noqa: E402

P0, CORE, BASE = R.P0, R.CORE, R.RISK_ONLY


def W(**kw) -> S.Params:
    return S.with_params(BASE, **kw)


VIEW = {"main": ("Straddle + risk layer", BASE), "core": ("Sirf straddle (risk layer bhi band)", CORE), "r16": ("Report 16 ka main test (saare layer)", P0)}
BANK_VIEW = {"bank": ("BANKNIFTY monthly: straddle + risk layer", S.with_params(BASE, index="BANKNIFTY")),
             "bank_core": ("BANKNIFTY monthly: sirf straddle", S.with_params(CORE, index="BANKNIFTY"))}
CHECKS = [
    ["Risk layer ka ek-ek hissa band (baaki sab main test jaisa)"],
    ("Main test: straddle + risk layer", BASE),
    ("Leg SL band", W(leg_sl=None)),
    ("Din ka loss limit band", W(day_sl=None)),
    ("Trailing band", W(trail=None)),
    ("Poori risk layer band (sirf straddle bechna)", CORE),
    ["Risk layer ke numbers"],
    ("Leg SL 50%", W(leg_sl=0.5)),
    ("Leg SL 100%", W(leg_sl=1.0)),
    ("Din ka loss limit 1%", W(day_sl=0.01)),
    ("Din ka loss limit 2%", W(day_sl=0.02)),
    ("Trailing 30% pe chalu", W(trail=0.3)),
    ("Trailing 70% pe chalu", W(trail=0.7)),
    ("Stop ka confirm nahi (1 minute me touch pe)", W(confirm=1)),
    ["Report 16 ki baaki layer ek-ek karke wapas chalu"],
    ("Strike adjustment chalu (ek leg 200 pt)", W(adj_pts=P0.adj_pts)),
    ("Tactical buy chalu", W(tactical=True)),
    ("Delta scalping chalu", W(scalp=True)),
    ("Drawdown scaling chalu", W(dd_steps=P0.dd_steps)),
    ("Pause chalu (gap / VIX / Budget)", W(pause=True)),
    ("Averaging chalu", W(avg=True)),
    ("Report 16 ka main test (saare layer chalu)", P0),
    ["Entry aur expiry"],
    ("Entry 9:15 pe", W(entry_t=0)),
    ("Agli weekly expiry", W(expiry="next")),
    ("Expiry tak hold (har din 3:15 pe square-off nahi)", W(hold="expiry")),
    ["Slippage"],
    ("Slippage 0", W(slip_opt=0.0, slip_fut=0.0)),
    ("Slippage zyada: option 1%, futures 0.05%", W(slip_opt=0.01, slip_fut=0.0005)),
]
BANK_CHECKS = [["Doosra index (BANKNIFTY, sirf monthly expiry)"], ("BANKNIFTY: straddle + risk layer", "bank"), ("BANKNIFTY: sirf straddle", "bank_core")]

SETTINGS = R.SETTINGS + [["Setup kahan se", "report 16 ki extra checks se chuna (result dekh ke)", 1]]
RULES = [
    "Index: NIFTY (weekly expiry) main test; BANKNIFTY (ab sirf monthly expiry) alag check. Period 2 Dec 2024 se 30 Oct 2026. Fit = Dec 2024 - Dec 2025, verify = Jan - Oct 2026.",
    "Paisa: capital Rs 30 lakh, usme se sirf 1/3 (Rs 10 lakh) margin me, baaki 2/3 buffer. Lots = Rs 10 lakh / (10% x lot size x index), neeche round (kam se kam 1). "
    "Profit dobara invest nahi hota (compounding nahi).",
    "Entry: har trading din 9:20 pe ATM straddle SELL (CE aur PE, same strike, sabse paas wali expiry). Koi din chhoda nahi jaata (pause band).",
    "Exit: 3:15 pe jo bhi khula hai sab band (intraday, raat ko kuch nahi).",
    "Leg SL: kisi leg ka premium entry se 75% upar ho jaye (2 lagatar 1-min close) to sirf wo leg band. Doosra leg 3:15 tak ya agle stop tak chalta hai. Band leg dobara nahi becha jaata.",
    "Din ka loss limit (kill switch): din ka P&L (band leg + khule leg) capital ke 1.5% (Rs 45,000) ke loss tak ya usse neeche 2 lagatar 1-min close rahe to sab band.",
    "Trailing: profit becha hua premium (Rs) ka 50% ho jaye to trailing chalu. Uske baad profit apne sabse upar wale level ka aadha reh jaye (2 lagatar 1-min close) to sab band.",
    "Band layers: strike adjustment, averaging, tactical buy, delta scalping (futures hedge), drawdown pe size kam karna, pause (gap / VIX / Budget). "
    "Inko ek-ek karke chalu karne ka asar 'Extra checks' me hai.",
    R.RULES[-1],
]
NOTES = [
    "Ye setup report 16 ki 'Extra checks' table se liya hai (30 setup me sabse achha). Result dekh ke chuna gaya, isliye fit aur verify dono ab dekha hua data hai "
    "aur asli me result isse kam aa sakta hai. Sachcha test naye data (Nov 2026 ke baad) pe hi hoga.",
    "Rulebook me risk ke exact numbers nahi the (leg SL, loss limit, trailing). Jahan 3 value di thi wahan beech wali li; aas-paas ki values 'Extra checks' me hain.",
    *(R.NOTES[i] for i in (1, 3, 4, 5, 6)),
    "Pause band hai, isliye Budget wale din (1 Feb 2025, 1 Feb 2026) aur bade gap wale din bhi trade hua.",
]
FORMULAS = [f for f in R.FORMULAS if f[0] in ("Straddle premium", "Option price (Black-76)", "Forward F", "IV per strike", "Leg SL", "Kill switch", "Trailing",
                                             "Fill price", "Kharcha", "Net P&L", "Return %", "Win rate", "Profit factor")]
FORMULAS.insert(1, ["Lots", "floor(capital x 1/3 / (10% x lot size x index)); kam se kam 1"])
FORMULAS.insert(-5, ["Gross P&L", "sum(side x (exit fill - entry fill) x lots x lot size) dono leg ka   (side: bechne -1)"])
FORMULAS += [["Average / din", "net / trade wale din"], ["Max drawdown", "roz ke kul P&L ki curve ka sabse bada gir (peak se neeche)"]]


def chips_of(p: S.Params) -> list:
    return [c[:2] + [1] if c[0] in ("Leg SL", "Din ka loss limit", "Trailing") else c for c in R.chips_of(p)]


CFG = dict(name="straddle_risk_layer", title="Straddle + Risk Layer", desc="NIFTY weekly, 9:20 ATM straddle SELL + sirf risk layer (leg SL 75%, din ka loss limit 1.5%, trailing 50%)",
           base=BASE, view=VIEW, bank_view=BANK_VIEW, checks=CHECKS, bank_checks=BANK_CHECKS, settings=SETTINGS, rules=RULES, notes=NOTES, formulas=FORMULAS, chips_of=chips_of,
           check_hint="Main test (straddle + risk layer) se ek baar me sirf ek cheez badli: risk layer ka ek hissa band, uska number badla, ya report 16 ki koi layer wapas chalu. "
                      "Net = poora period; Fit / Verify = do hisse.")

if __name__ == "__main__":
    sys.exit(R.main(CFG))
