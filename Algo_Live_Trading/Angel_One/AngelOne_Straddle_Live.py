#!/usr/bin/env python3
"""
AngelOne_Straddle_Live.py - LIVE trading of report "10 · Straddle + Risk Layer (2021 - 2026 retest)" on Angel One.

RULES (exactly report 10, Rules tab; every number below is the same as the backtest engine, backtest/straddle.py):
  Index   NIFTY, nearest weekly expiry (the expiry day itself is traded too), intraday only
  Entry   every trading day at 9:20: SELL ATM straddle (CE + PE, same strike = round(index/50)*50). No day is skipped.
  Exit    15:15: everything still open is bought back.
  Leg SL  a leg's option price >= entry price x 1.75 for 2 consecutive 1-min closes -> only that leg is bought back
          (the other leg keeps running to 15:15 or its own stop). A stopped leg is never sold again.
  Day loss limit (kill switch)   day P&L <= -Rs 45,000 (1.5% of Rs 30 lakh) for 2 consecutive 1-min closes -> close all.
  Trailing  once the day's profit >= 50% of the premium sold (Rs), then profit <= 50% of its peak for 2 consecutive
            1-min closes -> close all.
  Not used (switched off in report 10): strike adjustment, averaging, tactical buy, delta scalping, drawdown scaling, pause.
  Day P&L for the stops = sum over both legs of (entry fill - current price) x qty, bought-back legs at their exit fill.
  (charges are NOT part of the stops, same as the backtest.)

HOW THE SIZE IS DECIDED:  --lots N (you give it; there is no default). The backtest used Rs 30 lakh capital; here the
  lots are yours, the day loss limit stays Rs 45,000 (change with --day-loss).

WHAT IS DIFFERENT FROM THE BACKTEST (real market instead of a model):
  * Orders are marketable LIMIT orders (Angel One options), INTRADAY: sells start 1% under the last price, buy-backs 1% over;
    if not filled in 5 s the order is cancelled and re-sent 2%, 3%, 5% (buy-back up to 12%) away.  The backtest assumed 0.5% slippage.
  * Prices for the stop rules are the real 1-minute candle closes of the option (Angel getCandleData); the backtest used a model price.
  * Leg SL level = 1.75 x the last price seen just before the entry order (the backtest's "ref"), not the fill price.

SAFETY:
  * Nothing is sent without --live (without it the script only shows the plan).  After the plan you type YES (or use --yes).
  * An order is never re-sent blindly: if Angel One gives no reply, the order book is checked; if it is still unclear
    the script stops, tells Telegram and leaves the position for you to check in the app.
  * State is saved after every step (straddle_state_<date>.json).  If the script is restarted the same day it carries on
    with the open legs; it never enters a second time.  Started after 9:22 with no position -> no trade today.
  * If a buy-back cannot be filled even at 12% away, Telegram says MANUAL ACTION and the script keeps trying every minute.
  * Telegram: start, entry, every stop / exit, status every 15 minutes, errors, final result.

  py AngelOne_Straddle_Live.py --lots 1                       show the plan only (nothing is sent)
  py AngelOne_Straddle_Live.py --lots 1 --live                real orders (asks YES)
  py AngelOne_Straddle_Live.py --lots 1 --live --yes          real orders, no question (unattended / task scheduler)
  py AngelOne_Straddle_Live.py --lots 1 --replay 2026-11-20   replay a past day through the SAME rules, no orders at all
Run it on the AWS VM (registered static IP), start it before 9:15 and leave the window open until it says it is finished.
Needs AngelOne_Test_Order.py and AngelOne_Credentials.txt in the same folder (python standard library only).
"""
import argparse
import json
import math
import os
import sys
import time
from datetime import datetime, timedelta

import AngelOne_Test_Order as T
from AngelOne_Test_Order import IST, say

HERE = T.HERE
SEC = "/rest/secure/angelbroking/order/v1/"
HIST = "/rest/secure/angelbroking/historical/v1/getCandleData"
MASTER_HOST, MASTER_PATH = "margincalculator.angelbroking.com", "/OpenAPI_File/files/OpenAPIScripMaster.json"

# ---- report 10 rules --------------------------------------------------------------------------------------------
OPEN_MIN = 9 * 60 + 15                 # t = minutes after 9:15
ENTRY_T, EXIT_T = 5, 360               # 9:20 entry, 15:15 square-off
STEP = 50                              # NIFTY strike step
LEG_SL, TRAIL, CONFIRM = 0.75, 0.5, 2
DAY_LOSS_RS = 45000.0                  # 1.5% x Rs 30,00,000
# ---- order handling (live) --------------------------------------------------------------------------------------
SELL_BUF = (0.01, 0.02, 0.03, 0.05)
BUY_BUF = (0.01, 0.02, 0.03, 0.05, 0.08, 0.12)
WAIT_FILL_S = 5
ENTRY_LATE_MIN = 2                     # no entry later than 9:22
INDEX = {"exchange": "NSE", "tradingsymbol": "Nifty 50", "symboltoken": "99926000"}
TAG_RESULT, TAG_ERROR = "@ayushanand440", "@albert_max"


class Fatal(Exception):
    """Something unclear or unsafe: stop, tell Telegram, leave positions to a human."""


class Refused(Exception):
    """Angel One refused / rejected an order."""


# ================================================================ helpers
def now():
    return datetime.now(IST)


def day_open(day):                      # 9:15 of that day (aware datetime)
    return datetime(day.year, day.month, day.day, 9, 15, tzinfo=IST)


def tick_down(p, tick=0.05):
    return max(round(math.floor(p / tick + 1e-9) * tick, 2), tick)


def tick_up(p, tick=0.05):
    return round(math.ceil(p / tick - 1e-9) * tick, 2)


def notify(text, tag=""):
    say(text)
    T.telegram(f"[STRADDLE_LIVE] {text}" + (f"\n{tag}" if tag else ""))


def err(text):
    notify("🚨 " + text, TAG_ERROR)


def f2(x):
    return f"{x:,.2f}"


class API(T.Angel):
    """Angel One calls; logs in again once if the session token is refused, waits and repeats a read after a rate-limit reply."""
    AUTH = ("AG8001", "AG8002", "AB8050", "AB8051")

    def call(self, path, body=None, read=True):
        r = T.Angel.call(self, path, body)
        if r and not r.get("status"):
            msg = str(r.get("message", "")).lower()
            if r.get("errorcode") in self.AUTH or "invalid token" in msg:
                say("[api] session refused - logging in again")
                self.login()
                r = T.Angel.call(self, path, body)
            elif read and ("rate" in msg and "access" in msg or "exceeding" in msg):
                time.sleep(1.5)
                r = T.Angel.call(self, path, body)
        return r


def candles(api, exch, token, frm, to):
    """[(datetime, o, h, l, c)] 1-minute candles from..to (inclusive minutes), [] if none, None if no usable reply."""
    r = api.call(HIST, {"exchange": exch, "symboltoken": str(token), "interval": "ONE_MINUTE",
                        "fromdate": frm.strftime("%Y-%m-%d %H:%M"), "todate": to.strftime("%Y-%m-%d %H:%M")})
    if r is None or not r.get("status"):
        return None if r is None else []
    out = []
    for row in r.get("data") or []:
        try:
            out.append((datetime.fromisoformat(row[0]), float(row[1]), float(row[2]), float(row[3]), float(row[4])))
        except Exception:
            pass
    return out


# ================================================================ instrument master
def load_master(day):
    """List of instrument dicts. Cached per day in .cache/ ; the download is slow / flaky, so it retries."""
    cache = os.path.join(HERE, ".cache", f"scrip_master_{day:%Y-%m-%d}.json")
    if os.path.exists(cache):
        try:
            data = json.load(open(cache, encoding="utf-8"))
            if isinstance(data, list) and len(data) > 10000:
                return data
        except Exception:
            pass
    last = ""
    for attempt in range(1, 6):
        try:
            st, data = T.https("GET", MASTER_HOST, MASTER_PATH, timeout=300)
            if st == 200 and isinstance(data, list) and len(data) > 10000:
                os.makedirs(os.path.dirname(cache), exist_ok=True)
                json.dump(data, open(cache, "w", encoding="utf-8"))
                return data
            last = f"HTTP {st}, {type(data).__name__}"
        except Exception as e:
            last = str(e)[:100]
        say(f"[master] attempt {attempt} failed ({last}) - retrying")
        time.sleep(20)
    raise Fatal(f"instrument master could not be downloaded ({last})")


def nearest_expiry(master, day):
    exps = set()
    for r in master:
        if r.get("name") == "NIFTY" and r.get("exch_seg") == "NFO" and r.get("instrumenttype") == "OPTIDX":
            try:
                e = datetime.strptime(r["expiry"], "%d%b%Y").date()
            except Exception:
                continue
            if e >= day:
                exps.add(e)
    if not exps:
        raise Fatal("no NIFTY expiry found in the instrument master")
    return min(exps)


def find_contract(master, expiry, strike, cp):
    want = expiry.strftime("%d%b%Y").upper()
    for r in master:
        if (r.get("name") == "NIFTY" and r.get("exch_seg") == "NFO" and r.get("instrumenttype") == "OPTIDX"
                and r.get("expiry", "").upper() == want and str(r.get("symbol", "")).endswith(cp)
                and abs(float(r.get("strike") or 0) / 100 - strike) < 0.01):
            return {"symbol": r["symbol"], "token": str(r["token"]), "lot": int(r["lotsize"]),
                    "freeze": int(float(r.get("freeze_qty") or 0)), "tick": float(r.get("tick_size") or 5) / 100}
    raise Fatal(f"contract NIFTY {expiry} {strike:g} {cp} not found in the instrument master")


# ================================================================ brokers
class LiveBroker:
    """Real prices and real orders."""
    live = True

    def __init__(self, api, day):
        self.api, self.day, self.open = api, day, day_open(day)

    def spot(self, t):
        r = self.api.call(SEC + "getLtpData", INDEX)
        try:
            return float(r["data"]["ltp"])
        except Exception:
            raise Fatal(f"NIFTY index price not available ({str(r)[:150]})")

    def ltp(self, leg):
        r = self.api.call(SEC + "getLtpData", {"exchange": "NFO", "tradingsymbol": leg["symbol"], "symboltoken": leg["token"]})
        try:
            v = float(r["data"]["ltp"])
            return v if v > 0 else None
        except Exception:
            return None

    def price(self, leg, t):
        """Close of the 1-minute candle that ended at minute t (label 9:15 + t - 1). Waits up to ~20 s for it; else last price."""
        label = self.open + timedelta(minutes=t - 1)
        for _ in range(10):
            rows = candles(self.api, "NFO", leg["token"], label - timedelta(minutes=2), label)   # a little wider: single-minute queries sometimes come back empty
            for c in rows or []:
                if c[0].replace(tzinfo=None) == label.replace(tzinfo=None):
                    return c[4], "candle"
            time.sleep(2)
        v = self.ltp(leg)
        return (v, "ltp") if v else (None, "none")

    # ---- orders
    def _orders(self):
        r = self.api.call(SEC + "getOrderBook")
        return (r.get("data") or []) if r and r.get("status") else None

    def _row(self, oid):
        for o in self._orders() or []:
            if str(o.get("orderid")) == oid:
                return o
        return None

    @staticmethod
    def _num(x):
        try:
            return float(x)
        except (TypeError, ValueError):
            return 0.0

    def _place(self, leg, side, qty, price):
        known = {str(o.get("orderid")) for o in (self._orders() or [])}
        body = {"variety": "NORMAL", "tradingsymbol": leg["symbol"], "symboltoken": leg["token"], "transactiontype": side,
                "exchange": "NFO", "ordertype": "LIMIT", "producttype": "INTRADAY", "duration": "DAY",
                "price": f"{price:.2f}", "squareoff": "0", "stoploss": "0", "quantity": str(qty)}
        r = self.api.call(SEC + "placeOrder", body, read=False)      # sent once; never blindly repeated
        say(f"[placeOrder] {side} {qty} {leg['symbol']} @ {price:.2f} -> {str(r)[:200]}")
        if r is None:                                                # no reply: is it in the order book anyway?
            for _ in range(3):
                time.sleep(2)
                new = [o for o in (self._orders() or []) if str(o.get("orderid")) not in known
                       and o.get("tradingsymbol") == leg["symbol"] and str(o.get("transactiontype")).upper() == side
                       and str(o.get("quantity")) == str(qty)]
                if len(new) == 1:
                    return str(new[0]["orderid"])
            raise Fatal(f"placeOrder {side} {qty} {leg['symbol']}: NO REPLY and not in the order book - check the Angel One app")
        if not r.get("status"):
            raise Refused(f"{r.get('errorcode') or ''} {r.get('message') or r.get('raw') or ''}".strip())
        oid = str((r.get("data") or {}).get("orderid") or "")
        if not oid:
            raise Fatal(f"placeOrder {leg['symbol']}: success but no order id - check the Angel One app")
        return oid

    def _wait(self, oid, secs):
        """(filled qty, avg price, status, text) - polls once a second until complete / rejected / cancelled or `secs`."""
        last = (0, 0.0, "unknown", "")
        for _ in range(secs):
            time.sleep(1)
            o = self._row(oid)
            if o:
                last = (int(self._num(o.get("filledshares"))), self._num(o.get("averageprice")),
                        str(o.get("orderstatus") or o.get("status") or "").lower(), str(o.get("text") or ""))
                if last[2] in ("complete", "rejected", "cancelled", "canceled"):
                    break
        return last

    def _cancel(self, oid):
        self.api.call(SEC + "cancelOrder", {"variety": "NORMAL", "orderid": oid}, read=False)
        return self._wait(oid, 6)

    def trade(self, leg, side, qty, ref, why):
        """Marketable-limit ladder. Returns (filled qty, average fill price). Raises Refused / Fatal."""
        bufs = SELL_BUF if side == "SELL" else BUY_BUF
        remaining, filled, value = qty, 0, 0.0
        for i, b in enumerate(bufs):
            ltp = self.ltp(leg) or ref
            raw = ltp * (1 - b) if side == "SELL" else ltp * (1 + b)
            price = tick_down(raw, leg["tick"]) if side == "SELL" else tick_up(raw, leg["tick"])
            oid = self._place(leg, side, remaining, price)
            f, avg, status, text = self._wait(oid, WAIT_FILL_S)
            if status == "rejected":
                raise Refused(text or "rejected")
            if status != "complete":
                f, avg, status, text = self._cancel(oid)
                if status not in ("complete", "rejected", "cancelled", "canceled"):
                    raise Fatal(f"{side} {leg['symbol']}: order {oid} still '{status}' after cancel - check the Angel One app")
            if f:
                filled, value, remaining = filled + f, value + f * avg, remaining - f
            if remaining <= 0:
                break
            if i + 1 < len(bufs):
                say(f"[{why}] {side} {leg['symbol']}: {filled}/{qty} filled, trying a wider price ({i + 2}/{len(bufs)})")
        return filled, (value / filled if filled else None)


class ReplayBroker:
    """A past day: real 1-minute candles from Angel One, fills at the candle price +/- slippage. No orders."""
    live = False

    def __init__(self, api, day, slip):
        self.api, self.day, self.open, self.slip = api, day, day_open(day), slip
        self.cache = {}

    def _series(self, exch, token):
        k = (exch, token)
        if k not in self.cache:
            rows = None
            for _ in range(5):                                   # the history API sometimes answers empty: ask again
                rows = candles(self.api, exch, token, self.open, self.open + timedelta(minutes=380))
                if rows:
                    break
                time.sleep(2)
            if not rows:
                raise Fatal(f"no candles for {exch} {token} on {self.day} (contract expired or no data)")
            self.cache[k] = {c[0].replace(tzinfo=None): c[4] for c in rows}
        return self.cache[k]

    def spot(self, t):
        s = self._series(INDEX["exchange"], INDEX["symboltoken"])
        return s[(self.open + timedelta(minutes=t - 1)).replace(tzinfo=None)]

    def price(self, leg, t):
        s = self._series("NFO", leg["token"])
        v = s.get((self.open + timedelta(minutes=t - 1)).replace(tzinfo=None))
        return (v, "candle") if v else (None, "none")

    def ltp(self, leg):
        return None

    def trade(self, leg, side, qty, ref, why):
        return qty, ref * (1 - self.slip) if side == "SELL" else ref * (1 + self.slip)


# ================================================================ the strategy (rules of report 10)
class Strat:
    def __init__(self, lots, day_loss, state_path=None):
        self.lots, self.day_loss, self.path = lots, day_loss, state_path
        self.s = {"entry_started": False, "legs": [], "realised": 0.0, "armed": False, "peak": 0.0, "trail_cnt": 0,
                  "day_cnt": 0, "done": False, "reason": "", "max_target": 0.0, "orders": 0, "events": []}

    # ---- persistence
    def save(self):
        if self.path:
            self.s["saved"] = now().isoformat()
            json.dump(self.s, open(self.path, "w", encoding="utf-8"), indent=1)

    def load(self):
        if self.path and os.path.exists(self.path):
            self.s = json.load(open(self.path, encoding="utf-8"))
            return True
        return False

    def ev(self, t, text):
        self.s["events"].append(f"{hhmm(t)} {text}")
        self.save()

    # ---- numbers
    @property
    def legs(self):
        return self.s["legs"]

    def open_legs(self):
        return [l for l in self.legs if l["qty_open"] > 0]

    def pnl(self):
        """Day P&L: realised + open legs marked at their last price (entry fill - price) x qty."""
        return self.s["realised"] + sum((l["px0"] - l["last"]) * l["qty_open"] for l in self.open_legs())

    # ---- entry (t = 5, 9:20)
    def enter(self, b, master, expiry, t):
        s = self.s
        if s["entry_started"]:
            raise Fatal("entry was already started today - not entering again (check the Angel One app / order book)")
        s["entry_started"] = True
        self.save()
        spot = b.spot(t)
        K = round(spot / STEP) * STEP
        parts = {cp: find_contract(master, expiry, K, cp) for cp in ("CE", "PE")}
        lot = parts["CE"]["lot"]
        qty = self.lots * lot
        cap = min(p["freeze"] - 1 for p in parts.values()) if b.live and all(p["freeze"] for p in parts.values()) else 10 ** 9
        if qty > cap:
            raise Fatal(f"{self.lots} lots = {qty} qty is above the exchange freeze limit {cap} per order - use fewer lots")
        notify(f"Entry shuru: NIFTY {spot:.2f} -> strike {K:g}, expiry {expiry:%d-%b-%Y}, {self.lots} lot x {lot} = {qty} qty")
        done = []
        try:
            for cp in ("CE", "PE"):
                p = parts[cp]
                ref = (b.ltp(p) if b.live else None) or b.price(p, t)[0]
                if not ref:
                    raise Fatal(f"no price for {p['symbol']}")
                filled, avg = b.trade(p, "SELL", qty, ref, "entry")
                if not filled:
                    raise Refused(f"{p['symbol']} not filled")
                leg = {"cp": cp, "symbol": p["symbol"], "token": p["token"], "tick": p["tick"], "lot": lot, "qty": filled,
                       "qty_open": filled, "ref": ref, "px0": avg, "last": avg, "cnt": 0, "want_close": "", "exit_px": None}
                self.legs.append(leg)
                done.append(leg)
                s["orders"] += 1
                self.save()
                self.ev(t, f"SELL {p['symbol']} {filled} @ {avg:.2f} (ref {ref:.2f})")
        except (Refused, Fatal) as e:
            if isinstance(e, Fatal):
                raise
            err(f"Entry fail: {e}")
            for leg in done:                                           # one leg only = not the report's trade: buy it back
                self.close_leg(b, leg, t, "entry adhura - wapas kharid")
            s["done"], s["reason"] = True, f"entry failed: {e}"
            self.save()
            return
        s["max_target"] = sum(l["px0"] * l["qty"] for l in self.legs)
        self.save()
        lines = [f"✅ ENTRY {hhmm(t)}  NIFTY {spot:.2f}  strike {K:g}  {self.lots} lot ({qty} qty)"]
        for l in self.legs:
            lines.append(f"SELL {l['symbol']}  fill {l['px0']:.2f}  (leg SL {l['ref'] * (1 + LEG_SL):.2f})")
        lines.append(f"Premium becha: Rs {f2(s['max_target'])}  | trailing chalu hoga profit Rs {f2(TRAIL * s['max_target'])} par"
                     f"  | din ka loss limit Rs {f2(self.day_loss)}")
        notify("\n".join(lines))

    # ---- exits
    def close_leg(self, b, leg, t, why):
        """Buy back what is still open of this leg. If not fully filled the rest stays open and is retried every minute."""
        qty = leg["qty_open"]
        if qty <= 0:
            return
        ref = b.ltp(leg) if b.live else leg["last"]
        ref = ref or leg["last"]
        try:
            filled, avg = b.trade(leg, "BUY", qty, ref, why)
        except Refused as e:
            filled, avg = 0, None
            err(f"BUY-BACK REFUSED {leg['symbol']}: {e}  -> MANUAL ACTION may be needed, retrying every minute")
        if filled:
            self.s["realised"] += (leg["px0"] - avg) * filled
            leg["qty_open"] -= filled
            leg["exit_px"] = avg if leg["qty_open"] == 0 else leg["exit_px"]
            self.s["orders"] += 1
            self.ev(t, f"BUY {leg['symbol']} {filled} @ {avg:.2f} ({why})")
            notify(f"🔻 {why}: {leg['symbol']} {filled} wapas kharida @ {avg:.2f}  | leg P&L Rs {f2((leg['px0'] - avg) * filled)}")
        if leg["qty_open"] > 0:
            leg["want_close"] = why
            err(f"{leg['symbol']}: {leg['qty_open']} qty abhi BHI OPEN hai ({why}) - app me check karo, script har minute koshish karega")
        self.save()

    def close_all(self, b, t, why):
        self.s["reason"] = self.s["reason"] or why
        for leg in self.open_legs():
            leg["want_close"] = why
        for leg in list(self.open_legs()):
            self.close_leg(b, leg, t, why)

    # ---- one minute (t >= 6)
    def minute(self, b, t):
        s = self.s
        # a close that did not fill earlier is retried first
        for leg in list(self.open_legs()):
            if leg["want_close"]:
                self.close_leg(b, leg, t, leg["want_close"])
        if not self.open_legs():
            s["done"] = True
            self.save()
            return
        for leg in self.open_legs():
            p, src = b.price(leg, t)
            if p:
                leg["last"], leg["miss"] = p, 0
                if src != "candle":
                    say(f"[price] {leg['symbol']} {p:.2f} from {src}")
            else:
                leg["miss"] = leg.get("miss", 0) + 1
                if leg["miss"] in (3, 10):
                    err(f"{leg['symbol']}: {leg['miss']} minute se price nahi mila (last {leg['last']:.2f} use ho raha hai)")
        pnl = self.pnl()
        # 1) day loss limit
        s["day_cnt"] = s["day_cnt"] + 1 if pnl <= -self.day_loss else 0
        if s["day_cnt"] >= CONFIRM:
            self.ev(t, f"KILL SWITCH pnl {pnl:.0f}")
            self.close_all(b, t, "Din ka loss limit (kill switch)")
            return self._after(t)
        # 2) trailing profit lock
        if pnl >= TRAIL * s["max_target"]:
            s["armed"] = True
        if s["armed"]:
            s["peak"] = max(s["peak"], pnl)
            s["trail_cnt"] = s["trail_cnt"] + 1 if pnl < TRAIL * s["peak"] else 0
            if s["trail_cnt"] >= CONFIRM:
                self.ev(t, f"TRAILING pnl {pnl:.0f} peak {s['peak']:.0f}")
                self.close_all(b, t, "Trailing profit lock")
                return self._after(t)
        # 3) leg SL
        for leg in list(self.open_legs()):
            lvl = leg["ref"] * (1 + LEG_SL)
            leg["cnt"] = leg["cnt"] + 1 if leg["last"] >= lvl else 0
            if leg["cnt"] >= CONFIRM:
                self.ev(t, f"LEG SL {leg['symbol']} {leg['last']:.2f} >= {lvl:.2f}")
                self.close_leg(b, leg, t, f"Leg SL {leg['cp']}")
        # 15:15 square-off
        if t >= EXIT_T:
            self.close_all(b, t, "3:15 square-off")
        self._after(t)
        self.save()

    def _after(self, t):
        if not self.open_legs():
            self.s["done"] = True
        self.save()

    def status(self, t):
        legs = ", ".join(f"{l['cp']} {l['last']:.2f}{'' if l['qty_open'] else ' (band)'}" for l in self.legs)
        notify(f"⏱ {hhmm(t)}  P&L Rs {f2(self.pnl())}  | {legs}"
               f"{'  | trailing ON, peak Rs ' + f2(self.s['peak']) if self.s['armed'] else ''}")

    def summary(self, label):
        s = self.s
        gross = s["realised"] + sum((l["px0"] - l["last"]) * l["qty_open"] for l in self.open_legs())
        lines = [f"🏁 {label}: {s['reason'] or 'done'}", f"Gross P&L: Rs {f2(gross)}  (charges alag, ~Rs 20 x {s['orders']} orders + STT/exchange/GST)"]
        for l in self.legs:
            lines.append(f"{l['symbol']}: sell {l['px0']:.2f} -> {'buy ' + f2(l['exit_px']) if l['exit_px'] else 'OPEN ' + str(l['qty_open'])} "
                         f"({l['qty']} qty)")
        lines += ["Events:"] + s["events"]
        return "\n".join(lines)


# ================================================================ runners
def hhmm(t):
    return f"{(OPEN_MIN + t) // 60:02d}:{(OPEN_MIN + t) % 60:02d}"


def wait_for(day, t, lag):
    target = day_open(day) + timedelta(minutes=t, seconds=lag)
    while True:
        d = (target - now()).total_seconds()
        if d <= 0:
            return
        time.sleep(min(d, 5))


def t_now(day):
    return int((now() - day_open(day)).total_seconds() // 60)


def run_replay(api, a):
    day = datetime.strptime(a.replay, "%Y-%m-%d").date()
    master = load_master(now().date())
    expiry = nearest_expiry(master, day)
    b = ReplayBroker(api, day, a.slip)
    st = Strat(a.lots, a.day_loss, None)
    say(f"REPLAY {day}: expiry {expiry}, {a.lots} lot, slippage {a.slip:.2%}, day loss limit Rs {a.day_loss:,.0f}")
    T.SEND_TELEGRAM = False
    st.enter(b, master, expiry, ENTRY_T)
    for t in range(ENTRY_T + 1, EXIT_T + 1):
        if st.s["done"]:
            break
        st.minute(b, t)
        if t % 60 == 0 and not st.s["done"]:
            say(f"  {hhmm(t)} P&L {st.pnl():,.0f}")
    say("\n" + st.summary(f"REPLAY {day}"))
    return 0


def run_live(api, a):
    day = now().date()
    path = os.path.join(HERE, f"straddle_state_{day:%Y-%m-%d}.json")
    lock = os.path.join(HERE, f"straddle_{day:%Y-%m-%d}.lock")
    if os.path.exists(lock) and time.time() - os.path.getmtime(lock) < 150:
        sys.exit("Another copy of this script is running (lock file is fresh). Close it first.")
    st = Strat(a.lots, a.day_loss, path)
    resumed = st.load()
    if resumed and st.s["done"]:
        sys.exit(f"Today's trade is already finished ({st.s['reason']}). Nothing to do.")
    if now().weekday() >= 5:
        sys.exit("Aaj weekend hai - trading nahi.")
    master = load_master(day)
    expiry = nearest_expiry(master, day)
    b = LiveBroker(api, day)
    funds = (api.call("/rest/secure/angelbroking/user/v1/getRMS") or {}).get("data") or {}
    if resumed and st.s["legs"]:
        notify(f"♻️ Script dobara chala - aaj ke khule legs se aage badh raha hoon (lots {a.lots})")
    else:
        notify(f"▶️ Straddle live shuru ({now():%d-%b-%Y %H:%M}): NIFTY expiry {expiry:%d-%b-%Y}, {a.lots} lot, "
               f"din ka loss limit Rs {a.day_loss:,.0f}, available cash Rs {funds.get('availablecash')}. Entry 9:20 pe.")
    if not a.yes:
        if not sys.stdin or not sys.stdin.isatty():
            sys.exit("Needs a terminal to type YES (or use --yes).")
        if input("Ye ASLI orders bhejega (NIFTY straddle sell). Type YES to start: ").strip() != "YES":
            sys.exit("Cancelled (you did not type YES).")

    def beat():
        open(lock, "w").write(now().isoformat())
    beat()
    try:
        if not st.s["legs"]:
            if st.s["entry_started"]:
                raise Fatal("entry was started earlier today but no leg is saved - check the Angel One app, then delete the state file")
            wait_for(day, ENTRY_T, 0.5)
            if t_now(day) > ENTRY_T + ENTRY_LATE_MIN:
                st.s["done"], st.s["reason"] = True, "late start (after 9:22), no trade today"
                st.save()
                notify("⛔ Entry ka time nikal gaya (9:22 ke baad shuru hua) - aaj trade nahi.")
                return 0
            first = None
            for _ in range(4):                                       # any candle of the day so far (a trading day always has 9:15-9:19 by 9:20)
                first = candles(api, INDEX["exchange"], INDEX["symboltoken"], day_open(day), day_open(day) + timedelta(minutes=10))
                if first:
                    break
                time.sleep(5)
            if not first:
                st.s["done"], st.s["reason"] = True, "no NIFTY 9:15 candle today (holiday or data problem)"
                st.save()
                err("Aaj NIFTY ki 9:15 ki candle nahi mili - holiday hai ya data problem. Trade NAHI hua. Agar aaj market khula hai toh app me dekho.")
                return 0
            st.enter(b, master, expiry, ENTRY_T)
            beat()
        t = max(ENTRY_T + 1, t_now(day) + 1) if resumed else ENTRY_T + 1
        while t <= EXIT_T + 10 and not st.s["done"]:
            wait_for(day, t, 3.0)
            st.minute(b, min(t, EXIT_T) if t <= EXIT_T else EXIT_T)
            beat()
            if t % 15 == 0 and not st.s["done"]:
                st.status(t)
            t += 1
        if not st.s["done"]:
            raise Fatal("15:25 tak bhi saari position band nahi hui - app me check karo")
    except Fatal as e:
        err(f"RUK GAYA: {e}\n{st.summary('STOPPED')}")
        return 1
    except Exception as e:                                           # never die silently with a position open
        import traceback
        err(f"Script error: {type(e).__name__}: {e}\n{traceback.format_exc()[-800:]}\nOpen legs: "
            f"{[(l['symbol'], l['qty_open']) for l in st.open_legs()]} - app me check karo")
        return 1
    finally:
        try:
            os.remove(lock)
        except OSError:
            pass
    notify(st.summary("AAJ KA RESULT"), TAG_RESULT)
    return 0


def main():
    ap = argparse.ArgumentParser(description="Report 10 straddle + risk layer, live on Angel One")
    ap.add_argument("--lots", type=int, required=True, help="number of NIFTY lots to sell (no default)")
    ap.add_argument("--day-loss", type=float, default=DAY_LOSS_RS, help="day loss limit in Rs (report: 45000)")
    ap.add_argument("--live", action="store_true", help="send real orders")
    ap.add_argument("--yes", action="store_true", help="do not ask YES (unattended)")
    ap.add_argument("--replay", metavar="YYYY-MM-DD", help="replay a past day through the same rules, no orders")
    ap.add_argument("--slip", type=float, default=0.005, help="replay slippage (report: 0.5%%)")
    ap.add_argument("--no-telegram", action="store_true")
    a = ap.parse_args()
    if a.lots < 1:
        sys.exit("--lots must be at least 1")
    T.SEND_TELEGRAM = not a.no_telegram
    T.C.update(T.read_creds(os.path.join(HERE, T.CRED_FILE)))
    missing = [k for k in ("CLIENT_CODE", "PIN", "TOTP_KEY", "API_KEY") if not T.C.get(k)]
    if missing:
        sys.exit(f"Missing in {T.CRED_FILE}: {', '.join(missing)}")
    if not a.live and not a.replay:
        print(__doc__.split("HOW THE SIZE")[0])
        print(f"PLAN: NIFTY, {a.lots} lot, day loss limit Rs {a.day_loss:,.0f}.\nNothing is sent. Add --live for real orders, or --replay YYYY-MM-DD to test on a past day.")
        return 0
    ip, why = T.public_ip()
    expected = T.C.get("STATIC_IP", "")
    if a.live and ip and expected and ip != expected:
        sys.exit(f"STATIC IP MISMATCH: this machine {ip}, registered {expected}. Angel One will refuse orders. Run on the VM.")
    api = API(ip)
    try:
        api.login()
    except Exception as e:
        sys.exit(f"Login failed: {e}")
    try:
        return run_replay(api, a) if a.replay else run_live(api, a)
    except Fatal as e:
        err(f"Fatal: {e}") if a.live else say(f"Fatal: {e}")
        return 1


if __name__ == "__main__":
    sys.exit(main())
