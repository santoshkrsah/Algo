#!/usr/bin/env python3
"""
Firstock_Test_Order.py - ONE small test order from this machine, cancelled right away.

Checks the whole order path from this VM: static IP, login, /placeOrder, order status, /cancelOrder.
    BUY 1 share (default IDEA-EQ on NSE), LIMIT price 5% below the last price, CNC (delivery),
    then it is cancelled at once. A limit 5% under the market does not fill in those 1-2 seconds.
A rejection is also a result: the reason shows where it stopped (IP, market closed, margin ...).
When the market is closed (evenings, weekends) Firstock will most likely refuse it. The full check is a run in
market hours (Mon-Fri 09:15-15:30 IST): then the order should show OPEN at the exchange, then CANCELLED.

    py Firstock_Test_Order.py                        (Linux: python3 Firstock_Test_Order.py)
    py Firstock_Test_Order.py --symbol YESBANK-EQ    another cheap NSE share (NSE shares end with -EQ)
    py Firstock_Test_Order.py --no-telegram          print only, send nothing to Telegram

Needs Firstock_Status_AWS.py and Firstock_Credentials.txt in the same folder (login, Telegram and settings).
It shows the order first and places it only after you type YES. It never runs unattended and never re-sends an order.
"""
import argparse
import math
import os
import re
import socket
import sys
import time
from datetime import datetime, timedelta

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import Firstock_Status_AWS as fs  # noqa: E402  (same folder)

BELOW = 0.05                                   # limit price this far under the last price
FINAL = ("COMPLETE", "REJECTED", "CANCELLED", "CANCELED")
IP_WORDS = re.compile(r"\bip\b|static|whitelist|white list", re.I)


def tick_floor(price):
    """Round DOWN to a valid NSE price step: 0.05 under Rs 250 (fits the 0.01 and 0.05 ticks), else whole rupees."""
    step = 0.05 if price < 250 else 1.0
    return max(round(math.floor(price / step + 1e-9) * step, 2), 0.05)


def last_price(api, symbol):
    """(price, live) - the live LTP, else the newest daily close (no trade today); (None, False) if not found."""
    resp = api.call("/getQuote/ltp", exchange="NSE", tradingSymbol=symbol)
    if resp is None:
        return None, False
    try:
        ltp = float(api._rows(resp)[0]["lastTradedPrice"])
    except Exception:
        ltp = 0.0
    if ltp > 0:
        return ltp, True
    now = datetime.now(fs.IST)
    resp = api.call("/timePriceSeries", exchange="NSE", tradingSymbol=symbol, interval="1d",
                    startTime=(now - timedelta(days=10)).strftime("%H:%M:%S %d-%m-%Y"),
                    endTime=now.strftime("%H:%M:%S %d-%m-%Y"))
    best = None
    for row in (api._rows(resp) if resp else []):
        try:
            when = datetime.strptime(str(row["time"]), "%H:%M:%S %d-%m-%Y")
            close = float(row["close"])
        except Exception:
            continue
        if close > 0 and (best is None or when > best[0]):
            best = (when, close)
    return (best[1], False) if best else (None, False)


def order_state(api, ordid, tries):
    """(STATUS, reject reason, row) from /singleOrderHistory, read once a second until a final status or `tries` reads."""
    status, reason, row = "UNKNOWN", "", {}
    for _ in range(tries):
        time.sleep(1)
        resp = api.call("/singleOrderHistory", orderNumber=ordid)
        rows = api._rows(resp) if resp else []
        if rows and isinstance(rows[0], dict):
            row = rows[0]
            status = str(row.get("status") or "UNKNOWN").upper()
            reason = str(row.get("rejectReason") or "").strip()
            if status in FINAL:
                break
    return status, reason, row


def _num(value):
    try:
        return round(float(value), 2)
    except (TypeError, ValueError):
        return None


def find_unacked(api, symbol, price):
    """/placeOrder gave no reply: is the order in the order list anyway? Its number, or None."""
    for _ in range(3):
        time.sleep(2)
        book = api.orders()
        if book is None:
            continue
        same = [o for o in book if isinstance(o, dict) and o.get("tradingSymbol") == symbol
                and str(o.get("transactionType", "")).upper()[:1] == "B"
                and _num(o.get("quantity")) == 1 and _num(o.get("price")) == round(price, 2)]
        if len(same) == 1 and same[0].get("orderNumber"):
            return str(same[0]["orderNumber"])
    return None


def verdict(reason, market_is_open):
    if IP_WORDS.search(reason or ""):
        return "Result: looks like an IP problem - register this machine's public IP with Firstock"
    if not market_is_open:
        return ("Result: Firstock answered the order from this machine (login + order API work; the reason does not "
                "mention the IP). Market is closed: run again in market hours (Mon-Fri 09:15-15:30 IST) for the full check.")
    return "Result: the order API answered from this machine; the reason above is why it was not accepted (e.g. margin)."


def finish(lines, name, code):
    text = "\n".join(lines)
    fs.say("\n" + text)
    fs.telegram(f"[TEST_ORDER][{str(name).upper()}] {text}")
    return code


def main():
    ap = argparse.ArgumentParser(description="Place ONE small test order (NSE, 1 share, limit 5% below) and cancel it at once.")
    ap.add_argument("--symbol", default="IDEA-EQ", help="NSE trading symbol, default IDEA-EQ")
    ap.add_argument("--no-telegram", action="store_true", help="print only, send nothing to Telegram")
    a = ap.parse_args()
    fs.SEND_TELEGRAM = not a.no_telegram
    fs.setup_log()

    path = os.path.join(fs.HERE, fs.CRED_FILE)
    if not os.path.exists(path):
        sys.exit(f"{fs.CRED_FILE} not found next to this file - run Firstock_Status_AWS.py once first (it sets it up).")
    fs.C.update(fs.read_creds(path))
    missing = [k for k, _, _, required in fs.FIELDS if required and not fs.C.get(k)]
    if missing:
        sys.exit(f"Missing in {fs.CRED_FILE}: {', '.join(missing)} - run: py Firstock_Status_AWS.py --setup")
    if not sys.stdin or not sys.stdin.isatty():
        sys.exit("This places a REAL order, so it only runs by hand in a terminal (it asks for YES first).")

    symbol, name, market_is_open = a.symbol.strip().upper(), fs.C["USERID"], fs.market_open()
    lines = [f"🧪 TEST ORDER — {datetime.now(fs.IST):%d-%b-%Y %H:%M:%S} IST",
             f"Machine: {socket.gethostname()}",
             f"Market: {'OPEN' if market_is_open else 'CLOSED'}"]
    (ip, why), expected = fs.public_ip(), fs.C.get("STATIC_IP", "")
    if ip is None:
        lines.append(f"Static IP: could not check ({why})")
    elif expected and ip != expected:
        lines.append(f"Static IP: MISMATCH — this machine {ip}, Firstock static IP {expected}")
    else:
        lines.append(f"Static IP: {'match' if expected else 'not set'} ({ip})")

    api = fs.Firstock(name)
    try:
        how = fs.login(api, os.path.join(fs.HERE, f"{name}_jkey.txt"))
    except Exception as e:
        lines += [f"Login: FAILED ({e})", "Result: no order placed"]
        return finish(lines, name, 1)
    lines.append(f"Login: OK ({how})")

    last, live = last_price(api, symbol)
    if not last:
        lines += [f"Price of {symbol}: not found (wrong symbol? NSE shares end with -EQ, e.g. IDEA-EQ)",
                  "Result: no order placed"]
        return finish(lines, name, 1)
    if market_is_open and not live:
        lines += [f"{symbol}: no live price in market hours", "Result: no order placed"]
        return finish(lines, name, 1)
    price = tick_floor(last * (1 - BELOW))
    order = (f"BUY 1 {symbol} (NSE) LIMIT {price:.2f} "
             f"({BELOW:.0%} under the {'last price' if live else 'previous close'} {last:.2f}), CNC")

    print("\n" + "\n".join(lines))
    print(f"\nThis is a REAL order on account {name}:\n  {order}\n  It is cancelled right after it is placed.")
    if not market_is_open:
        print("  Market is CLOSED now: Firstock will most likely refuse it - that is fine for this test.")
    if input("Type YES to place it: ").strip() != "YES":
        print("Not placed (you did not type YES).")
        return 0
    lines.append(f"Order: {order}")

    # Sent exactly ONCE, never retried: a retry after a lost reply could make a second order.
    resp = api.post("/placeOrder", {
        "userId": api.user_id, "jKey": api.jkey, "exchange": "NSE", "tradingSymbol": symbol,
        "transactionType": "B", "product": "C", "priceType": "LMT", "price": f"{price:.2f}", "triggerPrice": "0",
        "quantity": "1", "retention": "DAY", "remarks": "vm_test_order", "mkt_protection": "1",
    })
    fs.say(f"[placeOrder] reply: {str(resp)[:500]}")
    if resp is None:
        ordid = find_unacked(api, symbol, price)
        if not ordid:
            lines += ["Placed: NO REPLY from Firstock, and the order list does not show it",
                      "🚨 Result: UNKNOWN — open the Firstock app and check the order list"]
            return finish(lines, name, 1)
        lines.append(f"Placed: no reply, but order {ordid} is in the order list")
    elif resp.get("status") != "success":
        err = resp.get("error") or {}
        msg = err.get("message") if isinstance(err, dict) else err
        reason = f"{resp.get('name') or resp.get('code') or resp.get('status')}: {msg}"
        lines += [f"Placed: REFUSED by Firstock — {reason}", verdict(reason, market_is_open)]
        return finish(lines, name, 0)
    else:
        data = resp.get("data") or {}
        if isinstance(data, list):
            data = data[0] if data else {}
        ordid = str(data.get("orderNumber") or "") if isinstance(data, dict) else ""
        if not ordid:
            lines += ["Placed: Firstock said success but gave no order number",
                      "🚨 Result: UNKNOWN — open the Firstock app and check the order list"]
            return finish(lines, name, 1)
        lines.append(f"Placed: OK — order number {ordid}")

    status, reason, row = order_state(api, ordid, tries=2)
    lines.append(f"Status: {status}" + (f" — {reason}" if reason else ""))
    if status == "REJECTED":
        lines.append(verdict(reason, market_is_open))
        return finish(lines, name, 0)
    if status in ("CANCELLED", "CANCELED"):
        lines.append("Result: the order is already cancelled — nothing is open")
        return finish(lines, name, 0)
    if status == "COMPLETE":
        lines.append(f"Result: FILLED — 1 share bought at {row.get('averagePrice') or price}. The order path works; "
                     f"the share is now in the account.")
        return finish(lines, name, 0)

    cresp = api.post("/cancelOrder", {"userId": api.user_id, "jKey": api.jkey, "orderNumber": ordid})
    fs.say(f"[cancelOrder] reply: {str(cresp)[:300]}")
    status, reason, row = order_state(api, ordid, tries=5)
    if status in ("CANCELLED", "CANCELED"):
        lines.append("Cancel: OK — order cancelled")
        lines.append("Result: ALL OK — the order went from this machine to the exchange and was cancelled"
                     if market_is_open else
                     "Result: OK — Firstock took the order and it was cancelled. Run again in market hours "
                     "(Mon-Fri 09:15-15:30 IST) to see it reach the exchange.")
        return finish(lines, name, 0)
    if status == "COMPLETE":
        lines.append(f"Result: FILLED before the cancel — 1 share bought at {row.get('averagePrice') or price}.")
        return finish(lines, name, 0)
    if status == "REJECTED":
        lines += [f"Status after cancel: REJECTED" + (f" — {reason}" if reason else ""), verdict(reason, market_is_open)]
        return finish(lines, name, 0)
    lines += [f"Cancel: NOT confirmed (status {status})",
              "🚨 Result: the test order may still be OPEN — cancel it in the Firstock app now"]
    return finish(lines, name, 1)


if __name__ == "__main__":
    sys.exit(main())
