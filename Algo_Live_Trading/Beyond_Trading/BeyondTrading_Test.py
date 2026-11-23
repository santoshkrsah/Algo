#!/usr/bin/env python3
"""
BeyondTrading_Test.py - READ-ONLY check of the Beyond Trading (Nirmal Bang) API. No order is placed.

Does: login (browser authCode -> session), profile, funds (limits), positions, order book.
The session is saved for the day (BeyondTrading_session_<date>.json), so BeyondTrading_Test_Order.py needs no second login.

    py BeyondTrading_Test.py                       asks you to log in in the browser and paste the redirect address
    py BeyondTrading_Test.py --url "https://...?authCode=XXXX&userId=U402B112..."   login from an address you already have
    py BeyondTrading_Test.py --no-telegram         print only

Needs BeyondTrading_Config.py and BeyondTrading_Credentials.txt in the same folder (nothing to install).
"""
import argparse
import sys

import BeyondTrading_Config as B
from BeyondTrading_Config import say


def rows(r):
    """The list inside an answer: {"result": [...]} / {"result": {...}} / [...]"""
    if isinstance(r, dict):
        r = r.get("result", r.get("data"))
    if isinstance(r, dict):
        return [r]
    return r if isinstance(r, list) else []


def main():
    ap = argparse.ArgumentParser(description="Read-only check of the Beyond Trading API")
    ap.add_argument("--url", help="the redirect address with authCode=... (or just the authCode)")
    ap.add_argument("--no-telegram", action="store_true")
    a = ap.parse_args()
    B.SEND_TELEGRAM = not a.no_telegram
    if not B.C.get("API_KEY") or not B.C.get("API_SECRET") or not B.C.get("USERID"):
        sys.exit("BeyondTrading_Credentials.txt: USERID / API_KEY / API_SECRET are needed.")

    lines = ["🧪 BEYOND TRADING TEST (read-only)"]
    ip, why = B.public_ip()
    expected = B.C.get("STATIC_IP", "")
    lines.append(f"IP: this machine {ip or 'unknown (' + str(why) + ')'}, registered {expected or 'not set'}"
                 + ("  -> MATCH" if ip and ip == expected else "  -> different (the docs name no IP rule; confirm in the developer portal)"))
    try:
        sess = B.need_session(args_url=a.url)
    except RuntimeError as e:
        lines.append(f"Login: FAILED - {e}")
        say("\n".join(lines))
        B.telegram("\n".join(lines))
        return 1
    lines.append(f"Login: OK (clientId {sess.get('clientId')})")
    api = B.API(sess)

    try:
        r = api.call("GET", "/profile/")
        p = (rows(r) or [{}])[0]
        lines.append(f"Profile: {'OK' if api.ok(r) else 'FAILED ' + str(r)[:150]} | name {p.get('clientName')} | status {p.get('accountStatus')}"
                     f" | totp {p.get('isTotpEnabled')} | exchanges {p.get('exchanges')} | products {p.get('products')}")
        r = api.call("GET", "/limits/")
        f = (rows(r) or [{}])[0]
        lines.append(f"Funds: {'OK' if api.ok(r) else 'FAILED ' + str(r)[:150]} | trading limit {f.get('tradingLimit')}"
                     f" | opening cash {f.get('openingCashLimit')} | utilized margin {f.get('utilizedMargin')}")
        r = api.call("GET", "/positions")
        ps = rows(r)
        open_ = [x for x in ps if str(x.get("netQuantity", "0")) not in ("0", "0.0", "")]
        lines.append(f"Positions: {'OK' if api.ok(r) else 'FAILED ' + str(r)[:150]} | {len(ps)} rows, {len(open_)} open"
                     + "".join(f"\n   {x.get('tradingSymbol')} {x.get('product')} net {x.get('netQuantity')} @ {x.get('netAveragePrice')}" for x in open_[:10]))
        r = api.call("GET", "/orders/book")
        os_ = rows(r)
        lines.append(f"Order book: {'OK' if api.ok(r) else 'FAILED ' + str(r)[:150]} | {len(os_)} orders today"
                     + "".join(f"\n   {x.get('brokerOrderId')} {x.get('transactionType')} {x.get('tradingSymbol')} {x.get('quantity')} "
                               f"@ {x.get('price')} -> {x.get('orderStatus')}" for x in os_[:10]))
    except B.SessionExpired as e:
        B.clear_session()
        lines.append(f"Session refused ({e}). Login again (run this script again).")
        say("\n".join(lines))
        B.telegram("\n".join(lines))
        return 1
    say("\n" + "\n".join(lines))
    B.telegram("\n".join(lines))
    return 0


if __name__ == "__main__":
    sys.exit(main())
