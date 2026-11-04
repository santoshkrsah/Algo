"""Report 17 (straddle_risk_layer) ab jitna data available hai usse poora chalaya: NIFTY weekly / BANKNIFTY (weekly ya
monthly, jo bhi tab chalu thi) ATM straddle SELL + sirf risk layer, Oct 2021 se latest din tak - jitna purane bhavcopy aur
Fyers 1-min index data abhi download ho chuka hai.

    python tools/run_straddle23.py
    python tools/run_straddle23.py --no-telegram

Needs (Algo_Historical_Data):
    python -m fyers_data.download_calendar bhavidxhist --start 2021-10-01 --end 2024-10-31   (NIFTY/BANKNIFTY option bhavcopy, purana NSE format)
    python -m fyers_data.download_calendar bhavidx --start 2024-11-01 --end <latest>          (naya UDiFF format)
    python -m fyers_data.download_reference nifty                                             (NIFTY50_1m.csv, 2021-10-01 se)
    python -m fyers_data.download_reference index NSE:NIFTYBANK-INDEX BANKNIFTY
    python -m fyers_data.download_reference index NSE:INDIAVIX-INDEX INDIAVIX

Purane format (8 Jul 2024 se pehle) me NSE ki file me lot size aur trade count nahi hote:
  - Lot size: turnover / (contracts x (strike + din ka average premium)) se nikala, NSE ke round lot pe snap kiya - same
    tarika jo breakout2.lot_sizes() stock options ke liye use karta hai. Format badalne ke din (5 -> 8 Jul 2024) pe
    check kiya: estimate aur NSE ka asli lot bilkul match karte hain.
  - Trade count (liquidity filter) ki jagah din ka volume (contracts traded) use hua, apna alag threshold (MIN_VOL) ke
    saath - thoda zyada rough hai trade-count wale filter se.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

import run_straddle as R  # noqa: E402
import run_straddle_risk as RR  # noqa: E402
from backtest import straddle as S  # noqa: E402

P0, CORE, BASE = RR.P0, RR.CORE, RR.BASE
VIEW, CHECKS = RR.VIEW, RR.CHECKS


def chips_of(p: S.Params) -> list:
    """Same as run_straddle_risk.chips_of, but the Index chip no longer says BANKNIFTY is 'monthly expiry' only
    (it was weekly too before Nov 2024, when the data here starts)."""
    out = RR.chips_of(p)
    if p.index != "NIFTY":
        out[0] = ["Index", f"{p.index} (weekly Nov 2024 tak, uske baad monthly)", 1]
    return out

# "monthly" hardcoded in report 17's BANKNIFTY labels is wrong for 2021 - Nov 2024 (BANKNIFTY had weekly expiry then too,
# discontinued only when SEBI capped each exchange to one weekly-expiry index)
BANK_VIEW = {"bank": ("BANKNIFTY: straddle + risk layer", S.with_params(BASE, index="BANKNIFTY")),
             "bank_core": ("BANKNIFTY: sirf straddle", S.with_params(CORE, index="BANKNIFTY"))}
BANK_CHECKS = [["Doosra index (BANKNIFTY; weekly expiry Nov 2024 tak, uske baad monthly)"], ("BANKNIFTY: straddle + risk layer", "bank"), ("BANKNIFTY: sirf straddle", "bank_core")]

NAME = "straddle_risk_2021_2026"
R.LOAD_FROM, R.START, R.END, R.SPLIT = "2021-10-25", "2021-11-01", "2026-11-03", "2024-11-01"

SETTINGS = R.SETTINGS[:2] + [["Period", "1 Nov 2021 se 3 Nov 2026 (fit = purane SEBI niyam ka daur, verify = Nov 2024 se, nayi expiry rules)"],
    ["Kharcha", "Rs 20/order, STT 4 slab (0.05% se 0.15%, saal ke hisaab se), exchange, stamp, GST"], R.SETTINGS[4]] + \
    [["Setup kahan se", "report 16/17 ki extra checks se chuna (result dekh ke); ab jitna purana data mila (2021 se) usme test", 1],
     ["Purana data (2021 - 7 Jul 2024)", "NSE ki purani file: lot size turnover se nikala, trade count ki jagah volume"]]

RULES = [
    "Index: NIFTY (weekly expiry) main test; BANKNIFTY alag check (weekly expiry Nov 2024 se pehle, uske baad sirf monthly - SEBI ne har exchange me "
    "ek hi weekly-expiry index rakhne ka rule banaya, NSE ne NIFTY rakha). Period 1 Nov 2021 se 3 Nov 2026 - jitna data abhi download ho chuka hai. "
    "Fit = 2021 - Oct 2024 (purane niyam, purana format), verify = Nov 2024 se (naya SEBI daur - yahi report 17 ka poora period tha).",
    RR.RULES[1], RR.RULES[2], RR.RULES[3], RR.RULES[4], RR.RULES[5], RR.RULES[6], RR.RULES[7],
    "Kharcha: STT 4 baar badla (Finance Act 2023, Budget 2024, Budget 2026) - exact % aur dates Formulas tab me. Baaki kharcha (brokerage, exchange, stamp, GST) saal bhar same maana.",
]
NOTES = RR.NOTES + [
    "1 Nov 2021 se 7 Jul 2024 tak NSE ki bhavcopy purane format me hai: usme lot size aur din ka trade count column hi nahi hota. Lot size turnover se nikala "
    "(formula Formulas tab me), aur liquidity filter (IV / basis ke liye kaunsa strike istemaal karna) trade count ki jagah volume se. Format badalne wale din "
    "(5 -> 8 Jul 2024) pe lot ka andaza NSE ke asli lot se match kiya, dono barabar aaye.",
    "2021-2024 ka daur structurally alag hai: alag lot size (NSE kai baar badla), kam STT, aur BANKNIFTY me weekly expiry bhi thi. Isliye 'fit' (purana daur) "
    "aur 'verify' (naya daur, Nov 2024 se) seedhe compare karne ki jagah alag-alag dikhaye gaye hain - verify wala hissa hi report 17 ka tha.",
    "Ye setup (risk layer ka exact number) report 16/17 ke result dekh ke chuna gaya tha, jo sirf Dec 2024 - Oct 2026 ka data tha. 2021-2024 ka result isliye "
    "asli naya (unseen) data hai is setup ke liye - agar yahan bhi number theek hai to ye acchi nishani hai.",
]
FORMULAS = RR.FORMULAS + [
    ["Purana lot size (7 Jul 2024 tak)", "median(turnover / (contracts x (strike + (open+close)/2))) din ke saare strike se, NSE ke round lot (1, 5, 25 ke multiple) pe snap"],
    ["Purana liquidity filter", "trade count ki jagah: din ka volume (contracts) >= 20 (naya format me trade count >= 100)"],
    ["STT (option / futures sell)", "<1 Apr 2023: 0.05% / 0.01%;  1 Apr 2023 - 30 Sep 2024: 0.0625% / 0.0125%;  1 Oct 2024 - 31 Mar 2026: 0.1% / 0.02%;  1 Apr 2026 se: 0.15% / 0.05%"],
]
CFG = dict(name=NAME, title="Straddle + Risk Layer (2021 - 2026)", desc="NIFTY weekly, 9:20 ATM straddle SELL + sirf risk layer, jitna data mila (2021 se)",
           base=BASE, view=VIEW, bank_view=BANK_VIEW, checks=CHECKS, bank_checks=BANK_CHECKS, settings=SETTINGS, rules=RULES, notes=NOTES, formulas=FORMULAS,
           chips_of=chips_of, check_hint=RR.CFG["check_hint"])

if __name__ == "__main__":
    sys.exit(R.main(CFG))
