"""Report 17 ki extra-checks row "Slippage 0" ab apna main test: NIFTY weekly 9:20 ATM straddle SELL, 3:15 square-off, risk layer
(leg SL 75%, din ka loss limit 1.5%, trailing 50%), slippage 0 (kharcha chalu). Same data, engine aur kharcha jaisa tools/run_straddle_risk.py;
extra checks wahi hain par sab slippage 0 pe, aur "Slippage" wale hisse me slippage ki seedhi (0 se 1%).

    python tools/run_straddle_slip0.py
    python tools/run_straddle_slip0.py --no-telegram
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

import run_straddle as R  # noqa: E402
import run_straddle_risk as RK  # noqa: E402
from backtest import straddle as S  # noqa: E402


def Z(p: S.Params) -> S.Params:
    return S.with_params(p, slip_opt=0.0, slip_fut=0.0)


BASE = Z(RK.BASE)


def W(**kw) -> S.Params:
    return S.with_params(BASE, **kw)


VIEW = {"main": ("Straddle + risk layer, slippage 0", BASE), "core": ("Sirf straddle, slippage 0", Z(R.CORE)),
        "r17": ("Report 17 ka main test (slippage 0.5%)", RK.BASE)}
BANK_VIEW = {"bank": ("BANKNIFTY monthly: risk layer, slippage 0", S.with_params(BASE, index="BANKNIFTY")),
             "bank_core": ("BANKNIFTY monthly: sirf straddle, slippage 0", S.with_params(Z(R.CORE), index="BANKNIFTY"))}
# report 17 ke saare checks slippage 0 pe; uska "Slippage" hissa yahan slippage ki seedhi se badla
_cut = next(i for i, c in enumerate(RK.CHECKS) if c == ["Slippage"])
CHECKS = [c if len(c) == 1 else (c[0] + (" (slippage 0)" if c[1] is RK.BASE else ""), Z(c[1])) for c in RK.CHECKS[:_cut]] + [
    ["Slippage ki seedhi (baaki sab main test jaisa)"],
    ("Slippage 0 (main test)", BASE),
    ("Slippage option 0.1%, futures 0.01%", W(slip_opt=0.001, slip_fut=0.0001)),
    ("Slippage option 0.25%, futures 0.01%", W(slip_opt=0.0025, slip_fut=0.0001)),
    ("Slippage option 0.5%, futures 0.02% (report 17)", RK.BASE),
    ("Slippage option 1%, futures 0.05%", W(slip_opt=0.01, slip_fut=0.0005)),
]
BANK_CHECKS = RK.BANK_CHECKS

SETTINGS = R.SETTINGS + [["Setup kahan se", "report 17 ki extra checks row 'Slippage 0' (result dekh ke chuna)", 1]]
_SLIP_OLD = "Slippage option 0.5%, futures 0.02%, har fill pe."
assert _SLIP_OLD in R.RULES[-1]
RULES = RK.RULES[:-1] + [
    R.RULES[-1].replace(_SLIP_OLD, "Slippage 0: har fill model ke price pe hi maana (bid-ask spread / market impact ka koi nuksan nahi). Ye best case hai."),
    "Fark sirf slippage ka: report 17 me option 0.5% aur futures 0.02% tha, yahan 0. Baaki rules, kharcha, data aur engine same.",
]
NOTES = [
    "Ye setup report 17 ki 'Extra checks' table ki row 'Slippage 0' hai. Report 16 -> 17 -> ye: setup result dekh ke chuna gaya, isliye fit aur verify dono ab dekha hua data hai. "
    "Sachcha test naye data (Nov 2026 ke baad) pe hi hoga.",
    "Slippage 0 asli me nahi milta: har order pe bid-ask spread aur market impact lagta hai, aur SL / tez move ke time fill aur door hota hai. "
    "Isliye ye result 'upar ki seema' (best case) hai. Asli result is report aur report 17 (0.5%) ke beech kahin aayega; slippage ki seedhi 'Extra checks' me hai.",
    "Kharcha (brokerage, STT, exchange, stamp, GST) poora chalu hai, sirf slippage 0 kiya.",
    *RK.NOTES[1:],
]
FORMULAS = [["Fill price", "buy = price x (1 + slippage), sell = price x (1 - slippage); is test me slippage = 0, to fill = model price"] if f[0] == "Fill price" else f
            for f in RK.FORMULAS]


def chips_of(p: S.Params) -> list:
    return [["Slippage", "0, fill = model price", 1] if c[0] == "Slippage" and not (p.slip_opt or p.slip_fut) else c for c in RK.chips_of(p)]


CFG = dict(name="straddle_risk_slip0", title="Straddle + Risk Layer (Slippage 0)",
           desc="NIFTY weekly, 9:20 ATM straddle SELL + risk layer (leg SL 75%, din ka loss limit 1.5%, trailing 50%), slippage 0",
           base=BASE, view=VIEW, bank_view=BANK_VIEW, checks=CHECKS, bank_checks=BANK_CHECKS, settings=SETTINGS, rules=RULES, notes=NOTES, formulas=FORMULAS, chips_of=chips_of,
           slip_txt="0", check_hint="Main test (risk layer, slippage 0) se ek baar me sirf ek cheez badli. 'Slippage ki seedhi' ko chhod ke saare check bhi slippage 0 pe hain. "
                                     "Net = poora period; Fit / Verify = do hisse.")

if __name__ == "__main__":
    sys.exit(R.main(CFG))
