#!/usr/bin/env python3
"""
BeyondTrading_Test_Order.py - ONE small test order on Beyond Trading (Nirmal Bang), cancelled right away. Result goes to Telegram.

    BUY 1 share of IDEA-EQ (NSE), INTRADAY, LIMIT at the price YOU give (--price), then it is cancelled at once.
    Give a price clearly BELOW the market (about 5% under the last price) so it does not fill in those few seconds.
    The API has no price (LTP) call, so the price is yours: look at the last price of the share and take ~5% less.

    py BeyondTrading_Test_Order.py --price 11.50 --dry-run      show the order, send nothing
    py BeyondTrading_Test_Order.py --price 11.50                real order (asks YES), cancelled at once
    py BeyondTrading_Test_Order.py --price 11.50 --orderComplexity AMO     after-market order (market closed)
    py BeyondTrading_Test_Order.py --symbol TCS-EQ --instrument-id 11536 --price 3000

instrumentId = the exchange's own scrip token (NSE: IDEA-EQ 14366, IRFC-EQ 2029, TCS-EQ 11536). The script checks the order book
after placing: if the symbol that comes back is not the one you asked for, it cancels at once and tells Telegram.

Needs today's session (run BeyondTrading_Test.py first, it logs in) + BeyondTrading_Config.py + BeyondTrading_Credentials.txt.
It shows the order first and places it only after you type YES. It never re-sends an order.
"""
import argparse
import sys
import time
from datetime import datetime

import BeyondTrading_Config as B
from BeyondTrading_Config import say, IST

FINAL = ("COMPLETE", "REJECTED", "CANCELLED", "CANCELED")
TAG = "beyond_test"


def rows(r):
    if isinstance(r, dict):
        r = r.get("result", r.get("data"))
    if isinstance(r, dict):
        return [r]
    return r if isinstance(r, list) else []


def market_open():
    n = datetime.now(IST)
    return n.weekday() < 5 and (9, 15) <= (n.hour, n.minute) < (15, 30)


def book(api):
    r = api.call("GET", "/orders/book")
    return rows(r) if api.ok(r) else None


def find_row(api, oid):
    for o in book(api) or []:
        if str(o.get("brokerOrderId")) == oid:
            return o
    return None


def state(api, oid, tries):
    """(STATUS, reason, row) read once a second until a final status or `tries` reads."""
    status, reason, row = "UNKNOWN", "", {}
    for _ in range(tries):
        time.sleep(1)
        o = find_row(api, oid)
        if o:
            row = o
            status = str(o.get("orderStatus") or "UNKNOWN").upper()
            reason = str(o.get("rejectionReason") or "").strip()
            if status in FINAL:
                break
    return status, reason, row


def finish(lines, code):
    text = "\n".join(lines)
    say("\n" + text)
    B.telegram(f"[TEST_ORDER][BEYOND_TRADING] {text}")
    return code


def main():
    ap = argparse.ArgumentParser(description="ONE small test order on Beyond Trading, cancelled right away")
    ap.add_argument("--price", type=float, required=True, help="limit price (about 5%% under the last price)")
    ap.add_argument("--symbol", default="IDEA-EQ", help="trading symbol, used to check the order book (default IDEA-EQ)")
    ap.add_argument("--instrument-id", default="14366", help="exchange scrip token (default 14366 = NSE IDEA-EQ)")
    ap.add_argument("--exchange", default="NSE")
    ap.add_argument("--qty", type=int, default=1)
    ap.add_argument("--product", default="INTRADAY")
    ap.add_argument("--orderComplexity", default="REGULAR", choices=["REGULAR", "AMO"])
    ap.add_argument("--dry-run", action="store_true", help="show the order, send nothing")
    ap.add_argument("--no-telegram", action="store_true")
    a = ap.parse_args()
    B.SEND_TELEGRAM = not a.no_telegram
    symbol = a.symbol.strip().upper()
    market = market_open()
    order = {"exchange": a.exchange, "instrumentId": str(a.instrument_id), "transactionType": "BUY", "quantity": a.qty,
             "product": a.product, "orderComplexity": a.orderComplexity, "orderType": "LIMIT", "price": f"{a.price:.2f}",
             "validity": "DAY", "slLegPrice": 0, "targetLegPrice": 0, "slTriggerPrice": 0, "disclosedQuantity": 0,
             "marketProtectionPercent": 0, "deviceId": "123", "trailingSlAmount": 0, "apiOrderSource": 0, "algoId": 0,
             "orderTag": TAG, "source": "API"}
    text = (f"BUY {a.qty} {symbol} ({a.exchange}, instrumentId {a.instrument_id}) LIMIT {a.price:.2f}, {a.product}, "
            f"{a.orderComplexity}")
    if a.dry_run:
        say(f"DRY RUN - would send POST {B.BASE}/orders/placeorder with the body (a list of one order):\n  {[order]}\n  = {text}\nNothing sent.")
        return 0
    if not sys.stdin or not sys.stdin.isatty():
        sys.exit("This places a REAL order, so it only runs by hand in a terminal (it asks for YES first).")
    sess = B.load_session()
    if not sess:
        sys.exit("No session for today. Run  py BeyondTrading_Test.py  first (it logs in), then this.")
    api = B.API(sess)

    lines = [f"🧪 TEST ORDER (Beyond Trading) — {datetime.now(IST):%d-%b-%Y %H:%M:%S} IST",
             f"Market: {'OPEN' if market else 'CLOSED'}"]
    ip, why = B.public_ip()
    expected = B.C.get("STATIC_IP", "")
    lines.append(f"IP: this machine {ip or 'unknown'}, registered {expected or 'not set'}" + ("  (match)" if ip and ip == expected else "  (different)"))
    print("\n" + "\n".join(lines))
    print(f"\nThis is a REAL order on account {sess.get('clientId')}:\n  {text}\n  It is cancelled right after it is placed.")
    if not market and a.orderComplexity == "REGULAR":
        print("  Market is CLOSED now: a REGULAR order will most likely be refused (try --orderComplexity AMO) - that is fine for this test.")
    if input("Type YES to place it: ").strip() != "YES":
        print("Not placed (you did not type YES).")
        return 0
    lines.append(f"Order: {text}")

    try:
        known = {str(o.get("brokerOrderId")) for o in (book(api) or [])}
        # Sent exactly ONCE, never retried: a retry after a lost reply could make a second order.
        r = api.call("POST", "/orders/placeorder", [order])
        say(f"[placeorder] reply: {str(r)[:500]}")
        oid = ""
        if r is None:                                        # no reply: is it in the order book anyway?
            for _ in range(3):
                time.sleep(2)
                new = [o for o in (book(api) or []) if str(o.get("brokerOrderId")) not in known and o.get("orderTag") == TAG]
                if len(new) == 1:
                    oid = str(new[0]["brokerOrderId"])
                    break
            if not oid:
                lines += ["Placed: NO REPLY, and the order book does not show it",
                          "🚨 Result: UNKNOWN - open the Beyond Trading app and check the order list"]
                return finish(lines, 1)
            lines.append(f"Placed: no reply, but order {oid} is in the order book")
        elif not api.ok(r):
            reason = f"{r.get('message') or r.get('emsg') or r.get('raw') or r}" if isinstance(r, dict) else str(r)
            lines += [f"Placed: REFUSED - {reason}",
                      "Result: Beyond Trading answered the order (login + order API work); the reason above is why it was not accepted."
                      + ("" if market or a.orderComplexity == "AMO" else " Market is closed: try AMO or run in market hours.")]
            return finish(lines, 0)
        else:
            res = rows(r)
            oid = str((res[0] if res else {}).get("brokerOrderId") or "")
            if not oid:
                lines += ["Placed: success but no brokerOrderId", "🚨 Result: UNKNOWN - check the Beyond Trading app"]
                return finish(lines, 1)
            lines.append(f"Placed: OK - order {oid}")

        status, reason, row = state(api, oid, tries=3)
        seen = str(row.get("tradingSymbol") or row.get("formattedInstrumentName") or "")
        lines.append(f"Status: {status}" + (f" - {reason}" if reason else "") + (f" | symbol in book: {seen}" if seen else ""))
        wrong = seen and symbol.split("-")[0] not in seen.upper()
        if wrong:
            lines.append(f"⚠️ The order book shows {seen}, not {symbol}: instrumentId {a.instrument_id} is a different share. Cancelling.")
        if status == "REJECTED":
            lines.append("Result: the order was rejected (see the reason above); the order API itself works from this machine.")
            return finish(lines, 0)
        if status in ("CANCELLED", "CANCELED"):
            lines.append("Result: already cancelled - nothing is open")
            return finish(lines, 0)
        if status == "COMPLETE":
            lines.append(f"Result: FILLED - {a.qty} share bought at {row.get('averageTradedPrice') or a.price}. "
                         "The order path works; the share is now in the account (sell it again if you do not want it).")
            return finish(lines, 0)

        c = api.call("POST", "/orders/cancel", {"brokerOrderId": oid})
        say(f"[cancel] reply: {str(c)[:300]}")
        status, reason, row = state(api, oid, tries=5)
        if status in ("CANCELLED", "CANCELED"):
            lines.append("Cancel: OK - order cancelled")
            lines.append("Result: ALL OK - login, place order and cancel work from this machine."
                         if not wrong else "Result: cancelled, but the instrumentId was the wrong share - fix --instrument-id.")
            return finish(lines, 0)
        if status == "COMPLETE":
            lines.append(f"Result: FILLED before the cancel - {a.qty} share bought at {row.get('averageTradedPrice') or a.price}.")
            return finish(lines, 0)
        lines += [f"Cancel: NOT confirmed (status {status}){' - ' + reason if reason else ''}",
                  "🚨 Result: the test order may still be OPEN - cancel it in the Beyond Trading app now"]
        return finish(lines, 1)
    except B.SessionExpired as e:
        B.clear_session()
        lines += [f"Session refused ({e})", "Login again: run  py BeyondTrading_Test.py"]
        return finish(lines, 1)


if __name__ == "__main__":
    sys.exit(main())
