# ═══════════════════════════════════════════════════════════════
#  Firstock_Utils.py  —  Core utility layer for Firstock broker
#  Mirrors avinash_utils.py (FlatTrade) in structure & interface
#  so strategy files need minimal changes to switch brokers.
# ═══════════════════════════════════════════════════════════════

import requests
import requests.adapters
import hashlib
import pyotp
import logging
import os
import time
import threading
import pandas as pd
import numpy as np

from datetime import datetime as dt, timedelta, timezone
from Firstock_Config import *


# ── CONSTANTS ──────────────────────────────────────────────────
FS_BASE_URL = "https://api.firstock.in/V1"
FS_CSV_FILE = "firstock-scrip.csv"

# Symbol file download URLs (public, no auth needed)
FS_SYMBOL_URLS = {
    "NFO":     "https://api.firstock.in/V1/symbols/NFO",
    "BFO":     "https://api.firstock.in/V1/symbols/BFO",
    "NSE":     "https://api.firstock.in/V1/symbols/NSE",
    "BSE":     "https://api.firstock.in/V1/symbols/BSE",
    "Indices": "https://api.firstock.in/V1/symbols/Indices",
}


# ════════════════════════════════════════════════════════════════
#  SECTION 1 — LOGGING SETUP
# ════════════════════════════════════════════════════════════════

os.makedirs("tokens_logs", exist_ok=True)

def _setup_logger(name):
    logger = logging.getLogger(name)
    logger.setLevel(logging.INFO)
    if not logger.handlers:
        fh = logging.FileHandler(f"tokens_logs/{name}.log", encoding="utf-8")
        fh.setFormatter(logging.Formatter("%(asctime)s - %(levelname)s - %(message)s"))
        logger.addHandler(fh)
    logger.propagate = False
    return logger

fs_login_logger  = _setup_logger(f"{dt.today().date()}_FS_Login_logger")
fs_utils_logger  = _setup_logger(f"{dt.today().date()}_FS_Utils_logger")


def _log(msg, logger=fs_utils_logger, telegram=False, client=None):
    """Print + log + optional telegram. client=None -> tagged [SYSTEM] on Telegram."""
    try:
        print(msg)
    except UnicodeEncodeError:
        print(msg.encode("ascii", "ignore").decode("ascii"))
    try:
        logger.info(msg)
    except Exception:
        pass
    if telegram:
        _send_telegram(msg, client=client)


# ════════════════════════════════════════════════════════════════
#  SECTION 2 — TELEGRAM
#  Named functions per strategy — same pattern as avinash_utils.py
#  Strategy files import the specific function they need.
#  e.g. Main_TTFT_V6 imports telegram_bot_sendtext_ttft from avinash_utils
#       Firstock_Login_Test imports telegram_bot_sendtext_fs_login from here
# ════════════════════════════════════════════════════════════════

def telegram_bot_sendtext_fs_login(bot_message):
    """FS_IP_BINDING_bot — Firstock login/session/IP-binding alerts."""
    try:
        if not FS_TELEGRAM_BOT_TOKEN or not FS_TELEGRAM_CHAT_IDS:
            print("[IP-BIND TG] TELEGRAM_BOT_TOKEN / TELEGRAM_CHAT_IDS missing in Firstock_Credentials.txt - alert NOT sent")
            fs_utils_logger.error(f"[IP-BIND TG] Telegram not configured | message was: {bot_message[:200]}")
            return
        url = f"https://api.telegram.org/bot{FS_TELEGRAM_BOT_TOKEN}/sendMessage"
        for bot_chatID in FS_TELEGRAM_CHAT_IDS:  # personal chat, group chat
            r = requests.post(url, json={"chat_id": bot_chatID, "text": bot_message}, timeout=10)
            if r.status_code != 200:
                # requests.post() does NOT raise on a non-2xx HTTP response (e.g.
                # Telegram's 429 Too Many Requests) — only network-level failures
                # land in the except block below. Without this explicit check, a
                # rate-limited/rejected send looks IDENTICAL to a successful one.
                # Deliberately NOT retrying (a retry would double the worst-case
                # blocking time on this synchronous call during a real outage).
                fs_utils_logger.error(
                    f"[IP-BIND TG] Send to {bot_chatID} failed HTTP {r.status_code}: {r.text[:200]} | message was: {bot_message[:200]}"
                )
    except Exception as e:
        # This bot is shared by ALL 5 Firstock strategies' login/IP-binding
        # alerts — a silent failure here means nobody gets told a strategy
        # even attempted to log in. Log it so it's at least traceable.
        try:
            fs_utils_logger.error(f"[IP-BIND TG] Failed to send: {str(e).replace(FS_TELEGRAM_BOT_TOKEN, '***')} | message was: {bot_message[:200]}")
        except Exception:
            pass


# ── Per-strategy tagging for the shared IP-binding bot ────────────
# Mirrors api_helper_flatrade.py's BoundRequests.set_strategy_name()/_stag().
# Each of the 5 Firstock strategies runs as its own separate OS process, so
# this module-level variable is NOT shared across strategies — each process
# gets its own independent copy of this module and its own value. Safe.
_fs_strategy_name = ""
_fs_startup_logged = set()  # accounts that have already sent startup TG this process

def set_strategy_name(name):
    """
    Call once, right after importing Firstock_Utils, so every Telegram
    message this module sends (login, IP-bind, order alerts, CSV download
    status, etc.) is tagged with which strategy sent it — e.g. "[TTFT_FS] ".
    Without this, all 5 strategies' login messages on the shared IP-binding
    bot look identical and you can't tell which strategy logged in.
    """
    global _fs_strategy_name
    try:
        _fs_strategy_name = str(name)
        print(f"[IP-BIND] Strategy name set to: {_fs_strategy_name}")
        # TG removed — strategy announces itself via its own 🔐 IP BINDING OK message
    except Exception as e:
        print(f"[IP-BIND] WARNING: set_strategy_name failed: {e}")
        try:
            fs_utils_logger.error(f"[IP-BIND] set_strategy_name('{name}') failed: {e}")
        except Exception:
            pass


def _fs_stag():
    """Return strategy tag prefix for Telegram messages, e.g. '[TTFT_FS] '."""
    return f"[{_fs_strategy_name}] " if _fs_strategy_name else ""


def _send_telegram_raw(message):
    """Send to the IP-binding bot WITHOUT any tag (used to send the
    'Strategy registered' announcement itself, and by _send_telegram below
    after it has already added the tag — avoids double-tagging)."""
    telegram_bot_sendtext_fs_login(message)


def _send_telegram(message, client=None):
    """
    Internal helper — uses the fs_login bot. Every message is tagged with
    the strategy name (if set_strategy_name() was called) AND a client tag:
    [client.upper()] if a client was given, else [SYSTEM] for messages that
    aren't tied to one specific account (CSV downloads, etc.).
    Mandatory format: "[STRATEGY][CLIENT] message text".
    Silent-but-logged on failure (see telegram_bot_sendtext_fs_login).
    """
    client_tag = f"[{str(client).upper()}]" if client else "[SYSTEM]"
    # Some call sites (e.g. firstock_login()'s per-account log lines) already
    # bake a literal "[name] " prefix into their message text — written
    # before this client= auto-tagging existed. Passing client= on top of
    # that produced a visible double tag on Telegram, e.g.
    # "[MI_V3_A5] [SADDY] [SADDY] Saved jKey FAILED historical check...".
    # Strip a leading tag that already matches the client tag so this can't
    # double up, regardless of which call site it comes from.
    msg_body = message.lstrip()
    if client and msg_body.upper().startswith(client_tag):
        msg_body = msg_body[len(client_tag):].lstrip()
    else:
        msg_body = message
    tagged = f"{_fs_stag()}{client_tag} {msg_body}"
    telegram_bot_sendtext_fs_login(tagged)


# ════════════════════════════════════════════════════════════════
#  SECTION 3 — IP BINDING  (mirrors api_helper_flatrade.py)
#  Binds each account's outbound HTTP socket to its private IP
#  so AWS routes it through the matching Elastic IP (public IP).
# ════════════════════════════════════════════════════════════════

class SourceAddressAdapter(requests.adapters.HTTPAdapter):
    """HTTPAdapter that binds the TCP socket to a specific local IP."""
    def __init__(self, source_ip, **kwargs):
        self.source_ip = source_ip
        super().__init__(**kwargs)

    def init_poolmanager(self, connections, maxsize, block=False, **kw):
        super().init_poolmanager(
            connections, maxsize, block=block,
            source_address=(self.source_ip, 0),
            **kw
        )


def _create_bound_session(source_ip=None):
    """
    Create a requests.Session.
    If source_ip is given → bind outbound socket to that private IP.
    AWS then routes it via the Elastic IP mapped to that private IP.
    """
    session = requests.Session()
    session.headers.update({"Content-Type": "application/json"})
    if source_ip:
        adapter = SourceAddressAdapter(source_ip)
        session.mount("https://", adapter)
        session.mount("http://", adapter)
        _log(f"[IP-BIND] Session bound to private IP: {source_ip}")
    return session


# ════════════════════════════════════════════════════════════════
#  SECTION 4 — FirStockAPI CLASS
#  One instance per account. Mirrors NorenApiPy in role.
#  Holds the bound session + jKey + userId.
# ════════════════════════════════════════════════════════════════

class FirStockAPI:
    """
    Lightweight REST wrapper around Firstock V1 API.
    Each account gets its own instance with IP-bound session.
    Interface mirrors FlatTrade's NorenApiPy so strategy code
    can call the same helper functions.
    """

    def __init__(self, source_ip=None, name=""):
        self.name      = name
        self.source_ip = source_ip
        self.session   = _create_bound_session(source_ip)
        self.jkey      = None
        self.userId    = None
        self.last_status_code = None   # set by _post() — lets callers detect 429 etc.
        self.token_file = None         # 2026-10-24: set by firstock_login(); lets _post() adopt a newer shared key

    # ── Internal POST helper ────────────────────────────────────
    @staticmethod
    def _is_invalid_jkey(status_code, data):
        """True when the broker rejected the session key (HTTP 401 or INVALID_JKEY in the body)."""
        if status_code == 401:
            return True
        try:
            return isinstance(data, dict) and data.get("status") != "success" and "INVALID_JKEY" in str(data).upper()
        except Exception:
            return False

    def _adopt_shared_token(self, payload):
        """2026-10-24 (PYSR 23-Oct incident). All strategies share ONE token file. When any of them does a fresh login
        the others keep the OLD key in memory and are rejected until they restart. Here: if the file now holds a
        DIFFERENT key than the one this request used, adopt it and return a copy of the payload carrying it; else None.
        Never logs in, never writes the file, never loops (the caller retries exactly once)."""
        try:
            tf = getattr(self, "token_file", None)
            if not tf or not isinstance(payload, dict) or "jKey" not in payload or not os.path.exists(tf):
                return None
            with open(tf, "r") as _f:
                new_key = _f.read().strip()
            if not new_key or new_key == payload.get("jKey"):
                return None
            _log(f"[{self.name}] INVALID_JKEY — adopting the newer shared token from {os.path.basename(tf)} "
                 f"(old ...{str(payload.get('jKey'))[-6:]} -> new ...{new_key[-6:]})")
            self.jkey = new_key
            new_payload = dict(payload)
            new_payload["jKey"] = new_key
            return new_payload
        except Exception as _e:
            _log(f"[{self.name}] adopt-shared-token failed (ignored): {_e}")
            return None

    def _post(self, endpoint, payload, timeout=15, _adopted=False):
        url = f"{FS_BASE_URL}{endpoint}"
        resp = None
        try:
            resp = self.session.post(url, json=payload, timeout=timeout)
            self.last_status_code = resp.status_code
            _data = resp.json()
            if (not _adopted and endpoint != "/login" and self._is_invalid_jkey(resp.status_code, _data)):
                _np = self._adopt_shared_token(payload)
                if _np is not None:
                    return self._post(endpoint, _np, timeout=timeout, _adopted=True)
            return _data
        except Exception as e:
            if resp is not None:
                self.last_status_code = resp.status_code
                _log(f"[{self.name}] HTTP error on {endpoint}: {e} | "
                     f"status_code={resp.status_code} | raw_body={resp.text!r}")
            else:
                self.last_status_code = None
                _log(f"[{self.name}] HTTP error on {endpoint}: {e} | no response received (connection/timeout failure)")
            return None

    # ── Set session from saved token file ──────────────────────
    def set_session(self, userId, jkey):
        self.userId = userId
        self.jkey   = jkey

    # ── Quick connectivity check (get Nifty LTP) ───────────────
    def ping(self):
        """
        Returns LTP float if session alive, else None.
        TradingSymbol for Nifty 50 = "NIFTY" (token 26000, NSE).
        If market closed (LTP=0), falls back to previous day close
        via 1d timePriceSeries so we always get a meaningful price.
        """
        try:
            resp = self._post("/getQuote/ltp", {
                "userId":        self.userId,
                "jKey":          self.jkey,
                "exchange":      "NSE",
                "tradingSymbol": "NIFTY"   # correct — NOT "Nifty 50" (that is CompanyName)
            })
            if resp and resp.get("status") == "success":
                data = resp["data"]
                row  = data[0] if isinstance(data, list) else data
                ltp  = float(row["lastTradedPrice"])
                if ltp > 0:
                    return ltp
                # Market closed — get previous day close
                return self._get_prev_close("NSE", "NIFTY")
            _log(f"[ping] Non-success response -> resp={resp!r}")
        except Exception as e:
            _log(f"[ping] Exception: {e}")
        return None  # None = session dead / API unreachable

    def _get_prev_close(self, exchange, trading_symbol):
        """Fetch last available daily close using 1d timePriceSeries."""
        try:
            from datetime import datetime as _dt, timedelta as _td
            start_str = (_dt.today() - _td(days=5)).strftime("%H:%M:%S %d-%m-%Y")
            end_str   = _dt.now().strftime("%H:%M:%S %d-%m-%Y")
            resp = self._post("/timePriceSeries", {
                "userId":        self.userId,
                "jKey":          self.jkey,
                "exchange":      exchange,
                "tradingSymbol": trading_symbol,
                "startTime":     start_str,
                "endTime":       end_str,
                "interval":      "1d"
            })
            if resp and resp.get("status") == "success":
                candles = resp.get("data", [])
                if candles:
                    prev_close = float(candles[-1]["close"])
                    _log(f"[ping] Market closed — prev close: {prev_close}")
                    return prev_close
        except Exception as e:
            _log(f"[_get_prev_close] Error: {e}")
        return 0.0

    # ── Get LTP ────────────────────────────────────────────────
    def get_quotes(self, exchange, tradingSymbol):
        """
        Returns {'lp': float, 'stale': bool} ('lp' as in the FlatTrade pattern).
        Firstock getQuote/ltp returns data as a DICT (not list as docs say).
        Handles both dict and list defensively.
        If LTP=0, 'lp' is the previous day close and stale=True (0.0 if even
        that is unavailable). get_ltp() decides whether a stale value is usable.
        """
        resp = self._post("/getQuote/ltp", {
            "userId":        self.userId,
            "jKey":          self.jkey,
            "exchange":      exchange,
            "tradingSymbol": tradingSymbol
        })
        if resp and resp.get("status") == "success":
            data = resp["data"]
            # API returns dict for single quote (actual behaviour)
            # but docs say list — handle both
            if isinstance(data, list):
                row = data[0]
            elif isinstance(data, dict):
                row = data
            else:
                return None
            ltp = float(row["lastTradedPrice"])
            if ltp > 0:
                return {"lp": ltp, "stale": False}
            # LTP=0 → not a live price → previous day close, flagged as stale
            prev = self._get_prev_close(exchange, tradingSymbol)
            return {"lp": prev, "stale": True}
        _log(f"[get_quotes] Non-success response | symbol={tradingSymbol} exchange={exchange} -> resp={resp!r}")
        return None

    # ── Historical OHLCV ───────────────────────────────────────
    def get_time_price_series(self, exchange, tradingSymbol, starttime_ts, interval):
        """
        starttime_ts: Unix timestamp (float/int)
        interval: e.g. "1mi", "5mi", "15mi", "1d"
        Returns list of dicts with keys: time, open, high, low, close, volume, oi
        """
        start_dt  = dt.fromtimestamp(starttime_ts)
        end_dt    = dt.now()
        start_str = start_dt.strftime("%H:%M:%S %d-%m-%Y")
        end_str   = end_dt.strftime("%H:%M:%S %d-%m-%Y")

        resp = self._post("/timePriceSeries", {
            "userId":        self.userId,
            "jKey":          self.jkey,
            "exchange":      exchange,
            "tradingSymbol": tradingSymbol,
            "startTime":     start_str,
            "endTime":       end_str,
            "interval":      interval
        })
        if resp and resp.get("status") == "success":
            return resp.get("data", [])
        # Genuine failure (no response, or status != "success") — return
        # None so callers can tell this apart from a SUCCESSFUL call that
        # legitimately returned zero rows (e.g. asking for a "1d" candle
        # that hasn't finalized yet, or an index with no bar for the
        # requested window). Returning [] for both used to make
        # firstock_login()'s `if hist_check:` treat "call succeeded, zero
        # rows" as "call failed", forcing a completely unnecessary fresh
        # login (and a false-alarm Telegram alert) — confirmed live on
        # 2026-07-27: 11 of 12 "FAILED historical check" events that day
        # had HTTP 200, only 1 was a real 401 INVALID_JKEY.
        _log(f"[get_time_price_series] Non-success response | symbol={tradingSymbol} interval={interval} -> resp={resp!r}")
        return None

    # ── Place Order ────────────────────────────────────────────
    def place_order(self, buy_or_sell, product_type, exchange, tradingsymbol,
                    quantity, price_type, price, trigger_price=0,
                    retention="DAY", remarks="order", mkt_protection="1",
                    discloseqty=0):
        payload = {
            "userId":          self.userId,
            "jKey":            self.jkey,
            "exchange":        exchange,
            "tradingSymbol":   tradingsymbol,
            "transactionType": buy_or_sell,    # "B" or "S"
            "product":         product_type,   # "C", "M", "I"
            "priceType":       price_type,     # "MKT", "LMT", "SL-LMT"
            "price":           str(price),
            "triggerPrice":    str(trigger_price),
            "quantity":        str(quantity),
            "retention":       retention,
            "remarks":         remarks,
            "mkt_protection":  str(mkt_protection),
        }
        resp = self._post("/placeOrder", payload)
        _log(f"[place_order] {buy_or_sell} {quantity} {tradingsymbol} @ {price_type} -> resp={resp!r}")
        return resp

    # ── Modify Order ───────────────────────────────────────────
    def modify_order(self, orderno, exchange, tradingsymbol,
                     newquantity, newprice_type, newprice,
                     newtrigger_price=0, product="M", retention="DAY",
                     mkt_protection="1"):
        payload = {
            "userId":        self.userId,
            "jKey":          self.jkey,
            "orderNumber":   orderno,
            "exchange":      exchange,
            "tradingSymbol": tradingsymbol,
            "quantity":      str(newquantity),
            "priceType":     newprice_type,
            "price":         str(newprice),
            "triggerPrice":  str(newtrigger_price),
            "product":       product,
            "retention":     retention,
            "mkt_protection": str(mkt_protection),
        }
        return self._post("/modifyOrder", payload)

    # ── Cancel Order ───────────────────────────────────────────
    def cancel_order(self, orderno):
        return self._post("/cancelOrder", {
            "userId":      self.userId,
            "jKey":        self.jkey,
            "orderNumber": orderno
        })

    # ── Single Order History ───────────────────────────────────
    def single_order_history(self, orderno):
        resp = self._post("/singleOrderHistory", {
            "userId":      self.userId,
            "jKey":        self.jkey,
            "orderNumber": orderno
        })
        if resp and resp.get("status") == "success":
            return resp.get("data", [])
        return []

    # ── Order Book ─────────────────────────────────────────────
    def get_order_book(self):
        """Returns list of orders on success (possibly empty — no orders
        today is a normal state), or None if the call itself failed."""
        resp = self._post("/orderBook", {"userId": self.userId, "jKey": self.jkey})
        if resp and resp.get("status") == "success":
            return resp.get("data", [])
        _log(f"[get_order_book] Non-success response -> resp={resp!r}")
        return None

    # ── Position Book ──────────────────────────────────────────
    def get_positions(self):
        """Returns list of positions on success (possibly empty — genuinely
        flat is a normal state), or None if the call itself failed. Callers
        MUST distinguish None (couldn't verify) from [] (verified flat) —
        collapsing both into the same value here previously meant a failed
        session/auth check would silently be reported as 'confirmed flat'."""
        resp = self._post("/positionBook", {"userId": self.userId, "jKey": self.jkey})
        if resp and resp.get("status") == "success":
            return resp.get("data", [])
        _log(f"[get_positions] Non-success response -> resp={resp!r}")
        return None

    # ── Limits ─────────────────────────────────────────────────
    def get_limits(self):
        return self._post("/limit", {"userId": self.userId, "jKey": self.jkey})


# ════════════════════════════════════════════════════════════════
#  SECTION 5 — LOGIN
# ════════════════════════════════════════════════════════════════

def _sha256(text):
    return hashlib.sha256(text.encode()).hexdigest()

def _generate_totp(totp_key):
    return pyotp.TOTP(totp_key).now()


class _SafeFileLock:
    """
    Cross-process file lock that wraps the Firstock REST login (Step 2).
    Guarantees only one process does a fresh REST login at a time.
    Other processes wait (up to 60 s), then re-check the token file —
    if the winner already wrote a fresh jKey they skip REST login entirely.
    Degrades gracefully to a no-op if filelock is not installed or times out,
    so login still works — just without the cascade protection.

    Install once on the server: pip install filelock
    """
    def __init__(self, lock_path, name, timeout=60):
        self._name = name
        self._lock = None
        try:
            from filelock import FileLock
            self._lock = FileLock(lock_path, timeout=timeout)
        except ImportError:
            _log(f"[{name}] filelock not installed — cross-process lock disabled "
                 f"(pip install filelock to enable).", fs_login_logger)

    def __enter__(self):
        if self._lock is not None:
            try:
                self._lock.acquire()
                _log(f"[{self._name}] Login lock acquired.", fs_login_logger)
            except Exception as _le:
                _log(f"[{self._name}] Login lock timeout/error ({_le}) — proceeding without lock.",
                     fs_login_logger)
                self._lock = None
        return self

    def __exit__(self, *args):
        if self._lock is not None:
            try:
                self._lock.release()
            except Exception:
                pass


# ────────────────────────────────────────────────────────────────────────────
#  CASCADE-PREVENTION RETRY (added 2026-07-30)
#
#  Root cause the retry solves: firstock_login()'s file-cached jKey verify
#  historically used a SINGLE-SHOT ping + SINGLE-SHOT historical check. Either
#  call failing once — transient rate-limit blip on /getQuote/ltp being the
#  common case — declared the token EXPIRED and triggered a fresh REST login.
#  That fresh login writes a new jKey to disk, invalidating every OTHER
#  running process's in-memory jKey. Those processes then run the SAME
#  fragile check, ALSO trip a false EXPIRED, generate ANOTHER new token, and
#  cascade.
#
#  Live evidence (07-29 shared FS_Login_logger): 59 login attempts, 43 (73%)
#  went to a fresh POST /login. Downstream mitigations already in RIFLY_X2's
#  validate_saddy_session() and Position Checker's load_session() reduced
#  false triggers there (25% and 19% save rates respectively) but couldn't
#  stop the cascade at its source — this function.
#
#  This helper wraps both the ping AND historical checks in one retry loop:
#  3 attempts, 5 s gap. Either attempt succeeding on BOTH calls is treated
#  as VALID. If all 3 ping attempts fail, a /positionBook fallback is tried
#  before concluding the token is dead — because /getQuote/ltp is far more
#  flaky than /positionBook (observed Aug-14: ping down at 09:16 but REST
#  login returned the SAME jKey, proving the session was alive). Roughly
#  ~10 s max added latency on a truly dead token; near-zero on a healthy one.
#
#  Contract: never raises.
#  Success : (True,  ltp,  hist_check)
#  Failure : (False, None, None, reason_str, events_list, fallback_positions)
#    events_list       — list of human-readable attempt lines for ONE TG msg
#    fallback_positions — result of api.get_positions(): list=alive, None=dead
# ────────────────────────────────────────────────────────────────────────────
_VERIFY_RETRIES = 3      # total attempts (1 initial + 2 retries)
_VERIFY_WAIT_S  = 5      # gap between attempts, in seconds


def _verify_saved_jkey_with_retry(api: "FirStockAPI", name: str):
    """
    Verify the currently-set jKey via ping + historical, with retry.
    After all ping attempts fail, falls back to /positionBook.

    Returns:
        (True, ltp, hist_check)
            — ping+hist both passed on some attempt
        (False, None, None, reason_str, events_list, fallback_positions)
            — all ping attempts failed; fallback_positions is
              api.get_positions() result (list=alive, None=dead/error).
              events_list is 2 compact lines for ONE combined TG message
              (ping summary + fallback result).
    """
    last_reason = "unknown"

    for attempt in range(1, _VERIFY_RETRIES + 1):
        try:
            ltp = api.ping()
            if ltp is None:
                last_reason = "returned None"
                _log(f"[{name}] Ping #{attempt}/{_VERIFY_RETRIES}: FAIL (returned None)",
                     fs_login_logger)
            else:
                _log(f"[{name}] Ping #{attempt}/{_VERIFY_RETRIES}: OK  LTP={ltp}",
                     fs_login_logger)
                _hist_start = (dt.today() - timedelta(days=2)).timestamp()
                hist_check  = api.get_time_price_series("NSE", "NIFTY", _hist_start, "1d")
                if hist_check is None:
                    last_reason = f"hist returned None (HTTP {api.last_status_code})"
                    _log(f"[{name}] Hist #{attempt}/{_VERIFY_RETRIES}: FAIL "
                         f"(HTTP {api.last_status_code})", fs_login_logger)
                else:
                    _log(f"[{name}] Hist #{attempt}/{_VERIFY_RETRIES}: OK  "
                         f"({len(hist_check)} rows) — VALID", fs_login_logger)
                    # BOTH checks passed on this attempt — trust the token.
                    return True, ltp, hist_check
        except Exception as e:
            last_reason = f"exception: {e}"
            _log(f"[{name}] Attempt #{attempt}/{_VERIFY_RETRIES}: EXCEPTION — {e}",
                 fs_login_logger)

        if attempt < _VERIFY_RETRIES:
            _log(f"[{name}] Retrying in {_VERIFY_WAIT_S}s...", fs_login_logger)
            time.sleep(_VERIFY_WAIT_S)

    # Compact ping summary for TG (one line)
    _ping_line = f"  All {_VERIFY_RETRIES} Pings FAILED  ({last_reason})"

    # ── All ping+hist attempts failed — try /positionBook as fallback ────────
    _log(f"[{name}] All {_VERIFY_RETRIES} ping attempts failed — "
         f"trying /positionBook fallback...", fs_login_logger)
    try:
        fb_positions = api.get_positions()
        if fb_positions is not None:
            _fb_line = (f"  Fallback  : /positionBook OK  "
                        f"({len(fb_positions)} position(s)) — session ALIVE")
            _log(f"[{name}] /positionBook fallback OK "
                 f"({len(fb_positions)} positions) — session alive.", fs_login_logger)
        else:
            _fb_line = "  Fallback  : /positionBook FAIL (None) — session DEAD"
            _log(f"[{name}] /positionBook fallback FAILED — session confirmed dead.",
                 fs_login_logger)
    except Exception as e:
        fb_positions = None
        _fb_line = f"  Fallback  : /positionBook EXCEPTION — {e}"
        _log(f"[{name}] /positionBook fallback EXCEPTION: {e}", fs_login_logger)

    return False, None, None, last_reason, [_ping_line, _fb_line], fb_positions


_ip_checked = False

def _check_static_ip(name):
    """Once per process: compare this machine's public IP with FS_STATIC_IP (Firstock_Config).
    Warn on Telegram if different (Firstock rejects orders/login from an unbound IP).
    Never blocks login and never raises."""
    global _ip_checked
    if _ip_checked:
        return
    _ip_checked = True
    try:
        expected = globals().get("FS_STATIC_IP")
        if not expected:
            return
        current = requests.get("https://api.ipify.org", timeout=8).text.strip()
        if current == expected:
            _log(f"[{name}] Public IP {current} matches static IP.", fs_login_logger)
        else:
            _log(f"[{name}] ⚠️ PUBLIC IP MISMATCH — this machine: {current} | Firstock static IP: {expected}. "
                 f"Login/orders will likely be rejected (check router / VPN / hotspot / ISP).",
                 fs_login_logger, telegram=True, client=name)
    except Exception as e:
        _log(f"[{name}] Static IP check skipped: {e}", fs_login_logger)


def firstock_login(api: FirStockAPI, creds: dict, source_ip=None, force_fresh=False):
    """
    Login to Firstock, save jKey to token file, return api instance.
    Mirrors flatrade_login() in avinash_utils.py.

    force_fresh (2026-10-24, default False = behaviour unchanged for every existing caller): skip the
    "saved key still alive?" shortcut (which treats ping-OK + positionBook-OK as healthy even when the history endpoint
    keeps answering INVALID_JKEY) and go straight to the locked fresh-login path. If another process already wrote a
    DIFFERENT, working key to the file, that key is adopted instead of logging in again.

    creds keys: user_id, password, totp_key, api_key, vendor_code,
                file (token filename), name (display name), enabled
    """
    filename = creds["file"]
    name     = creds.get("name", creds["user_id"])
    api.token_file = filename            # lets api._post() adopt a newer shared key on INVALID_JKEY
    _stale_key = api.jkey                # the key the caller found unusable (used by force_fresh)

    if not creds.get("enabled", True):
        _log(f"[{name}] Account disabled in config — skipping login.", telegram=True, client=name)
        return None

    _check_static_ip(name)

    _log(f"\n{'='*55}", fs_login_logger)
    _log(f"[{name}] Starting Firstock login...", fs_login_logger)
    _log(f"[{name}] User ID      : {creds['user_id']}", fs_login_logger)
    _log(f"[{name}] Token file   : {filename}", fs_login_logger)
    if source_ip:
        _log(f"[{name}] Source IP    : {source_ip} (IP-bound)", fs_login_logger)

    # Accumulates (label, [event_lines]) from verify steps; used to build ONE
    # combined TG after REST login. Empty on a clean fresh-start (no token file).
    _login_events = []

    # ── Step 1: Try existing token from file ───────────────────
    _log(f"\n[{name}] STEP 1 — Checking existing token file...", fs_login_logger)
    if force_fresh:
        _log(f"[{name}] force_fresh=True — skipping the saved-key shortcut", fs_login_logger)
    if (not force_fresh) and os.path.exists(filename):
        try:
            with open(filename, "r") as f:
                saved_jkey = f.read().strip()
            if saved_jkey:
                _log(f"[{name}] Token file found. Testing saved jKey...", fs_login_logger)
                api.set_session(creds["user_id"], saved_jkey)
                # ─ Retry-wrapped verify (3 attempts × 5s + /positionBook fallback)
                #   see _verify_saved_jkey_with_retry() docstring. Transient
                #   blips on /getQuote/ltp no longer trip EXPIRED; fallback
                #   via /positionBook catches the flaky-ping case entirely.
                _log(f"[{name}] Verifying saved jKey "
                     f"(ping+hist ×{_VERIFY_RETRIES}, then /positionBook fallback)...",
                     fs_login_logger)
                _verify_result = _verify_saved_jkey_with_retry(api, name)
                if _verify_result[0]:
                    _ltp        = _verify_result[1]
                    _hist_check = _verify_result[2]
                    _log(f"[{name}] Saved jKey is VALID "
                         f"(LTP={_ltp}, historical={len(_hist_check)} row(s)).", fs_login_logger)
                    if name not in _fs_startup_logged:
                        _fs_startup_logged.add(name)
                        _log(f"[{name}] Login done (from file): {filename}",
                             telegram=True, client=name)
                    else:
                        _log(f"[{name}] Login done (from file): {filename}", fs_login_logger)
                    return api
                else:
                    _fail_reason  = _verify_result[3]
                    _v_events     = _verify_result[4]
                    _fb_positions = _verify_result[5]
                    if _fb_positions is not None:
                        # Ping flaky but session alive — log only, no TG noise
                        _log(f"[{name}] Ping flaky, /positionBook rescued session "
                             f"— skipping REST login.", fs_login_logger)
                        return api
                    else:
                        # Session dead — store events; TG fires after REST login
                        _login_events.append(("Step 1 verify", _v_events))
                        _log(f"[{name}] Saved jKey EXPIRED after {_VERIFY_RETRIES} verify attempts "
                             f"(last: {_fail_reason}) — will do fresh login.", fs_login_logger)
        except Exception as e:
            _log(f"[{name}] Error reading token file: {e} — will do fresh login.", fs_login_logger)
    else:
        _log(f"[{name}] No token file found — fresh login required.", fs_login_logger)

    # ── Step 2: Fresh login via REST API ──────────────────────
    # Cross-process file lock — only one process does REST login at a time.
    # After acquiring the lock, re-check the token file: another strategy
    # may have already refreshed it while we were waiting (cascade prevention).
    _log(f"\n[{name}] STEP 2 — Acquiring login lock before fresh login...", fs_login_logger)
    lock_path = filename + ".lock"

    with _SafeFileLock(lock_path, name, timeout=60):
        # ── Re-check after acquiring lock (fast-path for losers) ──
        # This is the SECOND cascade-prevention point: another process may
        # have refreshed the token file while we were waiting for the lock.
        # Same retry wrapper as STEP 1 — a single transient blip here would
        # otherwise force us into an unnecessary REST login, generating
        # ANOTHER new token and invalidating whichever process just wrote
        # the one we're checking. This is the exact double-hit failure the
        # cascade fix targets.
        _log(f"[{name}] Re-checking token file after lock acquire...", fs_login_logger)
        if force_fresh:
            try:
                if os.path.exists(filename):
                    with open(filename, "r") as _ff:
                        _fk = _ff.read().strip()
                    if _fk and _fk != _stale_key:
                        api.set_session(creds["user_id"], _fk)
                        _okq = api.ping() is not None
                        _okh = api.get_time_price_series("NSE", "NIFTY", time.time() - 5 * 86400, "1d") is not None
                        if _okq and _okh:
                            _log(f"[{name}] force_fresh: another process already refreshed the key (works for quotes AND "
                                 f"history) — adopted, no REST login.", fs_login_logger)
                            return api
                        _log(f"[{name}] force_fresh: file key differs but is not fully usable (quote={_okq}, history={_okh}) "
                             f"— doing a fresh REST login.", fs_login_logger)
                    api.set_session(creds["user_id"], _stale_key)
            except Exception as _fe:
                _log(f"[{name}] force_fresh re-check error: {_fe} — doing a fresh REST login.", fs_login_logger)
        if (not force_fresh) and os.path.exists(filename):
            try:
                with open(filename, "r") as f:
                    _recheck_jkey = f.read().strip()
                if _recheck_jkey:
                    api.set_session(creds["user_id"], _recheck_jkey)
                    _verify_result = _verify_saved_jkey_with_retry(api, name)
                    if _verify_result[0]:
                        _log(f"[{name}] Token refreshed by another process — skipping REST login.",
                             fs_login_logger)
                        _log(f"[{name}] Login done (from file): {filename}", telegram=True, client=name)
                        return api
                    else:
                        _fail_reason  = _verify_result[3]
                        _v_events     = _verify_result[4]
                        _fb_positions = _verify_result[5]
                        if _fb_positions is not None:
                            # Ping flaky but session alive — log only, no TG noise
                            _log(f"[{name}] Post-lock: ping flaky, /positionBook rescued session "
                                 f"— skipping REST login.", fs_login_logger)
                            return api
                        else:
                            # Session dead — store events; TG fires after REST login
                            _login_events.append(("Step 2 re-check", _v_events))
                            _log(f"[{name}] Post-lock re-check DEAD ({_fail_reason}) "
                                 f"— proceeding with fresh REST login.", fs_login_logger)
            except Exception as _re_err:
                _log(f"[{name}] Re-check error: {_re_err} — proceeding with fresh login.", fs_login_logger)

        for attempt in range(1, 21):  # 20 retries — same as FlatTrade
            try:
                totp = _generate_totp(creds["totp_key"])
                _log(f"[{name}] Attempt {attempt}/20 | TOTP generated.", fs_login_logger)

                payload = {
                    "userId":     creds["user_id"],
                    "password":   _sha256(creds["password"]),
                    "TOTP":       totp,
                    "vendorCode": creds["vendor_code"],
                    "apiKey":     creds["api_key"],
                }

                _log(f"[{name}] Calling POST /V1/login ...", fs_login_logger)
                resp = api._post("/login", payload)

                # Never log the raw response: on success it carries the session token.
                _log(f"[{name}] API response status: {resp.get('status') if resp else None}", fs_login_logger)

                if resp is None:
                    _log(f"[{name}] No response from API. Retry in 5s...", fs_login_logger)
                    time.sleep(5)
                    continue

                if resp.get("status") == "success":
                    jkey     = resp["data"]["susertoken"]
                    act_id   = resp["data"].get("actid", creds["user_id"])
                    username = resp["data"].get("userName", name)

                    _log(f"[{name}] LOGIN SUCCESS!", fs_login_logger)
                    _log(f"[{name}] Account ID   : {act_id}", fs_login_logger)
                    _log(f"[{name}] User Name    : {username}", fs_login_logger)
                    _log(f"[{name}] jKey (token) : ...{jkey[-6:]}", fs_login_logger)

                    # ── Compare new jKey to old (flaky-ping detection) ─────
                    _old_jkey = ""
                    try:
                        if os.path.exists(filename):
                            with open(filename, "r") as _of:
                                _old_jkey = _of.read().strip()
                    except Exception:
                        pass
                    _same_key = bool(_old_jkey and jkey == _old_jkey)

                    # ── Atomic write to token file ─────────────────────────
                    _log(f"[{name}] Saving jKey to {filename} ...", fs_login_logger)
                    tmp_path = filename + ".tmp"
                    with open(tmp_path, "w") as f:
                        f.write(jkey)
                    os.replace(tmp_path, filename)
                    _log(f"[{name}] Token file saved successfully.", fs_login_logger)

                    # ── Set session ────────────────────────────────────────
                    api.set_session(creds["user_id"], jkey)

                    # ── Verify with ping ───────────────────────────────────
                    _log(f"[{name}] Verifying session with Nifty LTP ping...", fs_login_logger)
                    ltp = api.ping()
                    if ltp:
                        _log(f"[{name}] Session VERIFIED — Nifty LTP = {ltp}", fs_login_logger)
                    else:
                        _log(f"[{name}] WARNING: Ping failed after login. Session may be unstable.",
                             fs_login_logger)

                    # ── Combined TG or normal Login complete ──────────────
                    if _login_events:
                        # Session was dead: send ONE combined message covering
                        # all verify steps + REST login result
                        _tg_lines = ["🔴→✅ Session expired — REST login completed", ""]
                        for _lbl, _evs in _login_events:
                            _tg_lines.append(f"{_lbl}:")
                            _tg_lines.extend(_evs)
                            _tg_lines.append("")
                        _tg_lines.append(f"Action : REST login → SUCCESS")
                        _tg_lines.append(
                            f"         New jKey written to {os.path.basename(filename)}")
                        if _same_key:
                            _tg_lines.append(
                                f"⚠️  Note : Same jKey returned — session may have been "
                                f"alive (all endpoints were flaky)")
                        _send_telegram("\n".join(_tg_lines), client=name)
                        _log(f"[{name}] Login complete (combined TG sent).", fs_login_logger)
                    else:
                        # Normal fresh start — no prior verify steps
                        if _same_key:
                            _tg = (f"⚠️ REST login returned SAME jKey\n"
                                   f"  Old (last 8): ...{_old_jkey[-8:]}\n"
                                   f"  New (last 8): ...{jkey[-8:]}\n"
                                   f"  /getQuote/ltp may have been flaky")
                            _send_telegram(_tg, client=name)
                            _log(f"[{name}] REST login returned same jKey — ping was flaky.",
                                 fs_login_logger)
                        else:
                            _log(f"[{name}] Login complete.", telegram=True, client=name)
                    return api

                else:
                    err = resp.get("error", {})
                    _log(f"[{name}] Login FAILED — status: {resp.get('status')} | "
                         f"code: {resp.get('code')} | name: {resp.get('name')} | "
                         f"field: {err.get('field')} | msg: {err.get('message')}",
                         fs_login_logger)

            except Exception as e:
                _log(f"[{name}] Exception on attempt {attempt}: {e}", fs_login_logger)

            time.sleep(5)

        # ── All 20 retries exhausted ───────────────────────────────
        sos = (f"🚨🚨 SOS — FIRSTOCK LOGIN FAILED 🚨🚨\n"
               f"Account   : {name}\n"
               f"Token file: {filename}\n"
               f"All 20 retries exhausted.\n"
               f"Restart manually!")
        _log(sos, fs_login_logger, telegram=True, client=name)
        raise RuntimeError(sos)


# ════════════════════════════════════════════════════════════════
#  SECTION 6 — MASTER CSV DOWNLOAD
#  Mirrors retrieve_csv() in avinash_utils.py
#  Downloads all symbol files from Firstock and merges into
#  firstock-scrip.csv
#  Firstock CSV columns (Title Case):
#    Exchange, Token, LotSize, Symbol, TradingSymbol,
#    CompanyName, Expiry, Instrument, OptionType,
#    StrikePrice, TickSize, FreezeQty
# ════════════════════════════════════════════════════════════════

def retrieve_firstock_csv():
    """
    Download Firstock symbol master files for all segments,
    merge into firstock-scrip.csv, add today's date column.
    Skips download if today's file already exists.
    """
    _log("\n" + "="*55)
    _log("MASTER CSV DOWNLOAD — Firstock Symbol Files")
    _log("="*55)

    # ── Check if today's file already exists ──────────────────
    if os.path.exists(FS_CSV_FILE):
        try:
            df_check = pd.read_csv(FS_CSV_FILE, nrows=1, low_memory=False)
            if "Date" in df_check.columns:
                csv_date   = str(df_check["Date"].iloc[0])[:10]
                today_date = dt.today().date().strftime("%Y-%m-%d")
                if csv_date == today_date:
                    _log(f"[CSV] Today's file already exists ({FS_CSV_FILE}). Skipping download.", telegram=True)
                    return pd.read_csv(FS_CSV_FILE, low_memory=False)
        except Exception as e:
            _log(f"[CSV] Could not read existing file: {e} — will re-download.")

    all_dfs = []
    _segment_results = []   # (segment, rows_or_None) — for one combined TG summary

    for segment, url in FS_SYMBOL_URLS.items():
        _log(f"\n[CSV] Downloading {segment} symbols from:\n      {url}")

        for attempt in range(1, 4):  # 3 retries per segment
            try:
                resp = requests.get(url, timeout=30)
                _log(f"[CSV] {segment} — HTTP {resp.status_code} | Size: {len(resp.content)} bytes")

                if resp.status_code == 200:
                    from io import StringIO
                    df = pd.read_csv(StringIO(resp.text))
                    df["Segment"] = segment        # track which file each row came from
                    all_dfs.append(df)
                    _log(f"[CSV] {segment} — {len(df):,} rows downloaded. Columns: {list(df.columns)}")
                    _segment_results.append((segment, len(df)))
                    break
                else:
                    _log(f"[CSV] {segment} — HTTP {resp.status_code}. Retry {attempt}/3 in 10s...")
                    time.sleep(10)

            except Exception as e:
                _log(f"[CSV] {segment} — Error: {e}. Retry {attempt}/3 in 10s...")
                time.sleep(10)
        else:
            _log(f"[CSV] WARNING: Could not download {segment} after 3 attempts.")
            _segment_results.append((segment, None))

    _summary_lines = [
        f"{seg}: {rows:,} rows OK" if rows is not None else f"{seg}: FAILED"
        for seg, rows in _segment_results
    ]
    _log("[FIRSTOCK CSV] Download summary:\n" + "\n".join(_summary_lines))

    if not all_dfs:
        _log("[CSV] CRITICAL: No symbol data downloaded at all!", telegram=True)
        return None

    # ── Merge all segments ────────────────────────────────────
    _log("\n[CSV] Merging all segments...")
    result = pd.concat(all_dfs, ignore_index=True)
    result["Date"] = dt.today().date().strftime("%Y-%m-%d")

    # ── Standardise column names to lowercase for code compat ─
    # (FlatTrade used lowercase; Firstock uses Title Case)
    # We keep Title Case in file but add lowercase aliases
    col_map = {
        "Exchange":      "exchange",
        "Token":         "token",
        "LotSize":       "lotsize",
        "Symbol":        "symbol",
        "TradingSymbol": "tradingsymbol",
        "CompanyName":   "companyname",
        "Expiry":        "expiry",
        "Instrument":    "instrument",
        "OptionType":    "optiontype",
        "StrikePrice":   "strike",
        "TickSize":      "ticksize",
        "FreezeQty":     "freezeqty",
    }
    # Add lowercase duplicate columns so helper functions work
    for orig, lower in col_map.items():
        if orig in result.columns and lower not in result.columns:
            result[lower] = result[orig]

    # ── Save ──────────────────────────────────────────────────
    result.to_csv(FS_CSV_FILE, index=False)
    _log(f"\n[CSV] Master file saved: {FS_CSV_FILE}")
    _log(f"[CSV] Total rows: {len(result):,}")
    _log(f"[CSV] Columns   : {list(result.columns)}")

    summary = (f"[FIRSTOCK CSV] Master file ready!\n"
               f"Total rows : {len(result):,}\n"
               f"Segments   : {', '.join(FS_SYMBOL_URLS.keys())}\n"
               + "\n".join(_summary_lines))
    _send_telegram(summary)

    return result


# ════════════════════════════════════════════════════════════════
#  SECTION 7 — MARKET DATA HELPERS
#  Mirror avinash_utils.py functions so strategy files work
#  with minimal changes.
# ════════════════════════════════════════════════════════════════

_NON_LIVE_RETRIES = 5     # tries for a real LTP before accepting a stale / zero quote
_fs_stale_warned  = set() # symbols already alerted about a non-live price (one TG per symbol per process)
_IST = timezone(timedelta(hours=5, minutes=30))


def _market_hours_now():
    """True on a weekday between 09:15 and 15:30 IST (exchange holidays are not known here)."""
    now = dt.now(_IST)
    return now.weekday() < 5 and (9 * 60 + 15) <= now.hour * 60 + now.minute < (15 * 60 + 30)


def get_ltp(api: FirStockAPI, trading_symbol, exchange, max_retries=None, allow_stale=True):
    """
    Get Last Traded Price.
    trading_symbol: e.g. "Nifty 50", "IDEA-EQ", "NIFTY08MAY25C24350"
    Returns float LTP.
    Mirrors get_ltp() from avinash_utils.py.

    Non-live quotes (LTP=0, so previous close or 0.0 from get_quotes):
      - market closed and previous close available: returned at once.
      - otherwise retried up to _NON_LIVE_RETRIES times. If still not live:
        allow_stale=False -> None (never price an order from it);
        allow_stale=True  -> the stale value / 0.0, with one Telegram warning.
      These do not count towards the 90-failure kill-switch below.

    Failure behaviour (two-tier):
      - None return (Non-success): silent for first 4, WARNING from 5th,
        repeat WARNING every 60s, INFO on recovery.
      - Exception: logged individually at INFO every time.
      - sleep(0.5) on None return; sleep(1) on exception.
    """
    fail_count     = 0
    _warn_last_ts  = 0.0
    non_live       = 0

    while True:
        try:
            result = api.get_quotes(exchange, trading_symbol)
            if result:
                lp = float(result["lp"])
                if lp > 0 and (not result.get("stale") or not _market_hours_now()):
                    if fail_count >= 5:
                        _log(f"[get_ltp] Recovered after {fail_count} consecutive failures | symbol={trading_symbol}")
                    return lp
                non_live += 1
                if non_live >= min(_NON_LIVE_RETRIES, max_retries or _NON_LIVE_RETRIES):
                    if not allow_stale:
                        _log(f"[get_ltp] No live price for {trading_symbol} after {non_live} tries (lp={lp})")
                        return None
                    if trading_symbol not in _fs_stale_warned:
                        _fs_stale_warned.add(trading_symbol)
                        _log(f"⚠️ [get_ltp] No live price for {trading_symbol} — returning non-live value {lp} "
                             f"(previous close, or 0.0 if none)", telegram=True, client=api.name)
                    return lp
                time.sleep(0.5)
                continue
            # Non-success response — count failure, brief sleep, retry
            fail_count += 1
            sleep_secs  = 0.5
        except Exception as e:
            fail_count += 1
            _log(f"[get_ltp] Error: {e} | symbol={trading_symbol} | fail #{fail_count}")
            sleep_secs  = 1

        # Two-tier WARNING — silent first 4, WARNING at 5th, repeat every 60s
        _now = time.time()
        if fail_count == 5:
            fs_utils_logger.warning(
                f"[get_ltp] API failing — {fail_count} consecutive failures | "
                f"symbol={trading_symbol} exchange={exchange}"
            )
            _warn_last_ts = _now
        elif fail_count > 5 and (_now - _warn_last_ts) >= 60:
            fs_utils_logger.warning(
                f"[get_ltp] Still failing — {fail_count} consecutive | symbol={trading_symbol}"
            )
            _warn_last_ts = _now

        if max_retries is not None and fail_count >= max_retries:
            return None
        if fail_count >= 90:
            _kill_strategy(f"get_ltp: {trading_symbol} — 90 consecutive failures", client=api.name)
        time.sleep(sleep_secs)


def get_historical_data(api: FirStockAPI, exchange, trading_symbol, interval, no_of_days=20):
    """
    Fetch OHLCV candles — single attempt, returns None on any failure.
    Retry logic and TG alerts are owned by the caller (get_data in strategy).
    interval: "1mi", "5mi", "15mi", "30mi", "60mi", "1d"
    Note: "45mi" is NOT supported by Firstock — use resample from 15mi instead.
    Returns DataFrame with columns: time, open, high, low, close, volume, oi
    """
    start_ts = (dt.today() - timedelta(days=no_of_days)).timestamp()
    try:
        raw = api.get_time_price_series(exchange, trading_symbol, start_ts, interval)
        _log(f"[get_historical_data] RAW response | symbol={trading_symbol} interval={interval} start_ts={start_ts} -> type={type(raw)} value={raw!r}")
        if not raw:
            return None

        df = pd.DataFrame(raw)
        # Firstock returns: time, epochTime, open, high, low, close, volume, oi
        # Timestamp format is "HH:MM:SS DD-MM-YYYY" (day-first). Without an
        # explicit format=, pandas infers the format from the first few rows
        # and can guess MM-DD-YYYY on ambiguous early dates (day <= 12), then
        # throws on a later day > 12 that doesn't fit that guess — this is
        # the exact bug that silently broke every historical-data fetch on
        # 2026-07-16 (worked fine on other days purely by luck of which
        # dates happened to be in the lookback window). Explicit format
        # removes the guesswork entirely.
        df["time"] = pd.to_datetime(df["time"], format="%H:%M:%S %d-%m-%Y")
        df = df.sort_values("time", ascending=True).reset_index(drop=True)

        for col in ["open", "high", "low", "close"]:
            df[col] = df[col].astype(float)

        return df

    except Exception as e:
        _log(f"[get_historical_data] Error: {e}")
        return None


# ════════════════════════════════════════════════════════════════
#  SECTION 8 — SYMBOL LOOKUP HELPERS
#  Mirrors avinash_utils.py — reads from firstock-scrip.csv
#  Firstock CSV uses: symbol, optiontype, strike (float),
#  expiry (dd-MMM-YYYY), tradingsymbol, token, exchange
# ════════════════════════════════════════════════════════════════

def get_ins_token(trading_symbol, exchange):
    """Get numeric token for a tradingSymbol+exchange pair."""
    try:
        df = pd.read_csv(FS_CSV_FILE, low_memory=False)
        mask = (df["tradingsymbol"] == trading_symbol) & (df["exchange"] == exchange)
        return str(int(df[mask].iloc[0]["token"]))
    except Exception as e:
        _log(f"[get_ins_token] Error: {e} | symbol={trading_symbol}")
        return None


def get_strike_symbol(symbol, strike, expiry):
    """
    Find CE and PE tradingSymbols for a given underlying + strike + expiry.
    expiry: "YYYY-MM-DD" string (same format as FlatTrade)
    Returns: (ce_tradingsymbol, pe_tradingsymbol)
    """
    df = pd.read_csv(FS_CSV_FILE, encoding="utf-8")
    df["expiry_dt"] = pd.to_datetime(df["expiry"], format="%d-%b-%Y", errors="coerce")

    target_date = pd.to_datetime(expiry).date()

    mask = (
        (df["symbol"] == symbol) &
        (df["strike"].astype(float) == float(strike)) &
        (df["expiry_dt"].dt.date == target_date)
    )
    filtered = df[mask]

    ce_row = filtered[filtered["optiontype"] == "CE"]
    pe_row = filtered[filtered["optiontype"] == "PE"]

    ce_sym = ce_row["tradingsymbol"].iloc[0] if not ce_row.empty else None
    pe_sym = pe_row["tradingsymbol"].iloc[0] if not pe_row.empty else None

    return ce_sym, pe_sym


def get_strike_value(trading_symbol):
    """Extract strike price from a tradingSymbol row in CSV."""
    df = pd.read_csv(FS_CSV_FILE, encoding="utf-8")
    row = df[df["tradingsymbol"] == trading_symbol]
    return int(float(row["strike"].iloc[0]))


def get_fut_token(symbol):
    """
    Get nearest futures tradingSymbol and token for an index.
    Returns: (tradingsymbol, token)
    """
    df = pd.read_csv(FS_CSV_FILE, low_memory=False)
    mask = (df["symbol"] == symbol) & (df["instrument"] == "FUTIDX")
    filtered = df[mask].copy()
    filtered["expiry_dt"] = pd.to_datetime(filtered["expiry"], format="%d-%b-%Y", errors="coerce")
    filtered = filtered.sort_values("expiry_dt")
    row = filtered.iloc[0]
    return row["tradingsymbol"], str(int(row["token"]))


def get_spot_token(symbol, exchange):
    """Get token for spot index symbol."""
    df = pd.read_csv(FS_CSV_FILE, low_memory=False)
    mask = (df["tradingsymbol"] == symbol) & (df["exchange"] == exchange)
    return str(int(df[mask].iloc[0]["token"]))


def get_sorted_expiry_dates(symbol):
    """
    Get sorted DataFrame of expiry dates for an underlying.
    Returns DataFrame with column 'ExpiryDates' (datetime).
    Saves to {symbol}_Expiries.csv.
    Mirrors get_sorted_expiry_dates() from avinash_utils.py.
    """
    df = pd.read_csv(FS_CSV_FILE, low_memory=False)

    if symbol == "NIFTY":
        mask = (
            df["tradingsymbol"].str.contains("NIFTY", case=False, na=False) &
            ~df["tradingsymbol"].str.contains("MIDCPNIFTY|FINNIFTY|BANKNIFTY", case=False, na=False)
        )
    elif symbol == "SENSEX":
        mask = (
            df["tradingsymbol"].str.contains("SENSEX", case=False, na=False) &
            ~df["tradingsymbol"].str.contains("SENSEX50", case=False, na=False)
        )
    else:
        mask = df["tradingsymbol"].str.contains(symbol, case=False, na=False)

    sub = df[mask].copy()
    sub["expiry_dt"] = pd.to_datetime(sub["expiry"], format="%d-%b-%Y", errors="coerce")
    unique_dates = sorted(sub["expiry_dt"].dt.date.dropna().unique())
    result = pd.DataFrame({"ExpiryDates": unique_dates})
    result["ExpiryDates"] = pd.to_datetime(result["ExpiryDates"])
    result.to_csv(f"{symbol}_Expiries.csv", index=True)
    return result


def get_monthly_expiry(csv_file):
    """
    Last weekly expiry of the current month (monthly expiry).
    Returns "YYYY-MM-DD" string.
    """
    df = pd.read_csv(csv_file)
    df["ExpiryDates"] = pd.to_datetime(df["ExpiryDates"])
    idx = (df["ExpiryDates"].dt.to_period("M") != df["ExpiryDates"].shift(-1).dt.to_period("M")).idxmax()
    return df.loc[idx, "ExpiryDates"].strftime("%Y-%m-%d")


def get_monthly_second_expiry(csv_file):
    """Second month's last expiry date. Returns "YYYY-MM-DD"."""
    df = pd.read_csv(csv_file)
    df["ExpiryDates"] = pd.to_datetime(df["ExpiryDates"])
    periods  = df["ExpiryDates"].dt.to_period("M")
    changes  = (periods != periods.shift(-1)).to_numpy().nonzero()[0]
    if len(changes) > 1:
        return df.loc[changes[1], "ExpiryDates"].strftime("%Y-%m-%d")
    return None


# ════════════════════════════════════════════════════════════════
#  SECTION 9 — ORDER PLACEMENT
#  Mirrors placeOrder() from avinash_utils.py.
#  MKT orders work natively in Firstock with mkt_protection.
#  Returns (orderID, status, avgprice) — same 3-tuple as FlatTrade.
# ════════════════════════════════════════════════════════════════

def _nfo_tick(price):
    """Round to nearest NFO tick (₹0.05), min ₹0.05."""
    return max(round(round(price / 0.05) * 0.05, 2), 0.05)


_fs_seen_orders = set()   # order numbers placeOrder() already handled in this process


def _same_number(a, b):
    try:
        return abs(float(a) - float(b)) < 1e-6
    except (TypeError, ValueError):
        return False


def _filled_qty(row):
    """Filled quantity from an order-history row, or None if the broker gave no such field."""
    for key in ("fillShares", "filledShares", "filledQuantity", "filledQty", "fillshares", "flqty"):
        value = row.get(key)
        if value not in (None, ""):
            try:
                return int(float(value))
            except (TypeError, ValueError):
                pass
    return None


def _locate_unacked_order(api, buy_sell_code, symbol, qty, price):
    """
    A /placeOrder call that got no usable response may still have reached the
    broker. Look for it in the order book (3 tries, 2 s apart).
    Returns (order_number, state), state being:
      FOUND     - exactly one new order matches symbol + side + qty + price
      NOT_FOUND - order book read fine and has no such order
      UNKNOWN   - order book unreadable, unexpected fields, or an ambiguous match
    """
    required = ("orderNumber", "tradingSymbol", "transactionType", "quantity")
    state = "UNKNOWN"
    for _ in range(3):
        time.sleep(2)
        book = api.get_order_book()
        if book is None:
            state = "UNKNOWN"
            continue
        if any(key not in row for row in book for key in required):
            return None, "UNKNOWN"
        same = [o for o in book
                if o["tradingSymbol"] == symbol
                and str(o["transactionType"]).upper()[:1] == buy_sell_code
                and _same_number(o["quantity"], qty)
                and str(o["orderNumber"]) not in _fs_seen_orders]
        if len(same) == 1 and _same_number(same[0].get("price"), price):
            return str(same[0]["orderNumber"]), "FOUND"
        if same:
            return None, "UNKNOWN"
        state = "NOT_FOUND"
    return None, state


def _resolve_no_response(api, buy_sell_code, symbol, qty, price):
    """Returns (resp, state). resp is a success-shaped dict when the order was found, else None."""
    _log(f"[placeOrder] {symbol} | no response from /placeOrder — checking the order book")
    try:
        ordid, state = _locate_unacked_order(api, buy_sell_code, symbol, qty, price)
    except Exception as e:
        _log(f"[placeOrder] {symbol} | order book check failed: {e}")
        ordid, state = None, "UNKNOWN"
    if state == "FOUND":
        _log(f"⚠️ [placeOrder] {symbol} | no response, but order {ordid} IS at the broker — continuing with it",
             telegram=True, client=api.name)
        return {"status": "success", "data": {"orderNumber": ordid}}, state
    if state == "UNKNOWN":
        _log(f"🚨🚨 SOS — ORDER STATE UNKNOWN 🚨🚨\n"
             f"{symbol} | side {buy_sell_code} | qty {qty} | price {price}\n"
             f"/placeOrder gave no response and the order book could not confirm it.\n"
             f"CHECK THE BROKER TERMINAL NOW.", telegram=True, client=api.name)
    return None, state


def placeOrder(api: FirStockAPI, buy_sell, exchange, symbol,
               order_type, product_type, qty,
               price=0, trigger_price=0, mkt_protection="1"):
    """
    Unified order placement — mirrors placeOrder() from avinash_utils.py.
    Returns (orderID, status, avgprice).

    buy_sell     : "BUY" or "SELL"
    order_type   : "MKT", "LMT", "SL", "SLM"
    product_type : "NRML" → "M" | "MIS" → "I" | "CNC" → "C"

    SEBI CIRCULAR SEBI/HO/MIRSD/MIRSD-PoD/P/CIR/2025/0000013 — COMPLIANCE
    MKT orders are banned via API from April 2026 (same rule FlatTrade
    already complies with in avinash_utils.py). "MKT" calls are converted
    internally to LMT + DAY with live-price-based pricing + retry logic.
    Firstock's API does not expose top-of-book bid/ask (no sp1/bp1 like
    FlatTrade's Noren API), so pricing uses LTP + buffer directly —
    the same fallback FlatTrade itself uses when its own quote fetch fails.
    Non-MKT orders (LMT, SL, SLM) are placed as-is, unchanged.
    """

    # ── Type conversions (match FlatTrade call convention) ─────
    buySell = "B" if buy_sell == "BUY" else "S"

    prod_map = {"NRML": "M", "MIS": "I", "CNC": "C", "M": "M", "I": "I", "C": "C"}
    product = prod_map.get(product_type, "M")

    # ── MKT → LMT+DAY conversion (SEBI compliance) ──────────────
    if order_type == "MKT":
        MAX_RETRIES = 4
        BUFFER      = 0.04   # 4% buffer — same as avinash_utils.py
        WAIT_PLACE  = 1
        WAIT_MODIFY = 1
        LOW_PREMIUM_THRESHOLD = 20.0   # rupees
        # A fixed 4% buffer is a tiny absolute rupee amount on cheap,
        # deep-OTM legs (e.g. 4% of Rs.1.75 = Rs.0.07) — often not enough to
        # cross a wide/thin bid-ask spread on an illiquid contract, causing
        # repeated unfilled retries (observed live: ITMBFLY 2026-07-20,
        # NIFTY21JUL26P23350 @ ~Rs.1.75, all 4 retries at ~4% exhausted).
        # For sub-Rs.20 premiums, widen the buffer on each retry: 4%, 8%,
        # 16%, 32% (retry attempts 1-4) instead of holding flat at 4%.
        # Legs priced >= Rs.20 keep the original flat 4% buffer unchanged.

        try:
            try:
                # max_retries widened 3 -> 8 (2026-07-30) — get_ltp() sleeps
                # 0.5-1s between attempts, so this bounds total wait to
                # roughly 4-6s (was ~1-1.5s) before giving up — enough extra
                # runway to ride out a brief rate-limit/data blip at the
                # exact instant of order placement, without freezing a
                # time-sensitive entry indefinitely (get_ltp() itself still
                # has its own 90-consecutive-failure kill-switch as the
                # absolute ceiling regardless of this value).
                lp = get_ltp(api, symbol, exchange, max_retries=8, allow_stale=False)
                if not lp or lp <= 0:
                    raise ValueError("LTP is zero/unavailable")
                lmt_price = _nfo_tick(lp * (1 + BUFFER) if buy_sell == "BUY" else lp * (1 - BUFFER))
                _log(f"[placeOrder-LMT] {symbol} | {buy_sell} | lp={lp} | lmt_price={lmt_price} (buf={BUFFER*100:.0f}%)")
            except Exception as eq:
                _log(f"[placeOrder-LMT] {symbol} | LTP fetch failed: {eq} — cannot price order", telegram=True, client=api.name)
                return "", f"LTP_FETCH_FAILED: {eq}", 0

            resp = api.place_order(
                buy_or_sell=buySell, product_type=product,
                exchange=exchange, tradingsymbol=symbol,
                quantity=qty, price_type="LMT", price=lmt_price,
                trigger_price=0, retention="DAY", remarks="strategy_order",
                mkt_protection=mkt_protection,
            )
            _log(f"[placeOrder-LMT] {symbol} | place_order response: {resp}")

            if resp is None:
                resp, _ack_state = _resolve_no_response(api, buySell, symbol, qty, lmt_price)
                if _ack_state == "UNKNOWN":
                    return "", "UNKNOWN_ORDER_STATE", 0

            if not resp or resp.get("status") != "success":
                err = (resp or {}).get("error", {})
                err_msg = f"{(resp or {}).get('name')} — {err.get('message')}"
                _log(f"[placeOrder-LMT] {symbol} | place_order FAILED: {err_msg}", telegram=True, client=api.name)
                return "", f"PLACE_FAILED: {err_msg}", 0

            data = resp.get("data", {})
            ordid = (data[0].get("orderNumber", "") if isinstance(data, list) else data.get("orderNumber", ""))
            if not ordid:
                _log(f"[placeOrder-LMT] {symbol} | no orderNumber in response: {resp}", telegram=True, client=api.name)
                return "", f"PLACE_FAILED_NO_ORDID: {resp}", 0

            _fs_seen_orders.add(str(ordid))
            _log(f"[placeOrder-LMT] {symbol} | placed ordid={ordid} | waiting {WAIT_PLACE}s...")
            time.sleep(WAIT_PLACE)

            reject_reason = ""
            try:
                history  = api.single_order_history(ordid)[0]
                status   = history.get("status", "UNKNOWN").upper()
                avgprice = float(history.get("averagePrice", 0) or history.get("price", 0) or 0)
                # rejectReason is the actual broker-side cause (e.g. "Margin
                # Shortfall") — previously discarded entirely, leaving TG
                # alerts and logs with only a generic "ORDER_REJECTED"/
                # "ALL_4_RETRIES_EXHAUSTED" and no way to tell WHY.
                reject_reason = (history.get("rejectReason") or "").strip()
            except Exception as hs:
                _log(f"[placeOrder-LMT] {symbol} | status check failed ({hs}) — treating as OPEN")
                status, avgprice = "OPEN", 0

            _log(f"[placeOrder-LMT] {symbol} | initial status={status} | avgprice={avgprice}"
                 f"{f' | rejectReason={reject_reason}' if reject_reason else ''}")

            if status == "COMPLETE":
                if avgprice == 0:
                    avgprice = get_ltp(api, symbol, exchange, max_retries=3) or 0
                return ordid, status, float(avgprice)

            if status in ("REJECTED", "CANCELLED", "CANCELED"):
                _log(f"[placeOrder-LMT] {symbol} | order {status} on placement — no retries"
                     f"{f' | reason: {reject_reason}' if reject_reason else ''}",
                     telegram=True, client=api.name)
                return "", f"ORDER_{status}" + (f" ({reject_reason})" if reject_reason else ""), 0

            # ── Retry loop — modify with fresh LTP-based price ──
            is_low_premium = lp < LOW_PREMIUM_THRESHOLD
            for attempt in range(1, MAX_RETRIES + 1):
                try:
                    lp2 = get_ltp(api, symbol, exchange, max_retries=3, allow_stale=False)
                    if not lp2 or lp2 <= 0:
                        raise ValueError("LTP zero on retry")
                    # Sub-Rs.20 premium: widen buffer per attempt (4/8/16/32%).
                    # Rs.20+ premium: keep the original flat 4% (unchanged).
                    retry_buffer = BUFFER * (2 ** (attempt - 1)) if is_low_premium else BUFFER
                    new_price = _nfo_tick(lp2 * (1 + retry_buffer) if buy_sell == "BUY" else lp2 * (1 - retry_buffer))

                    _log(f"[placeOrder-LMT-RETRY-{attempt}] {symbol} | {buy_sell} | "
                         f"new_price={new_price} (buf={retry_buffer*100:.0f}%{' LOW_PREMIUM' if is_low_premium else ''}) "
                         f"| modifying ordid={ordid}...")

                    mod_ret = api.modify_order(
                        orderno=ordid, exchange=exchange, tradingsymbol=symbol,
                        newquantity=qty, newprice_type="LMT", newprice=new_price,
                        product=product,
                    )
                    if mod_ret is None:
                        _log(f"[placeOrder-LMT-RETRY-{attempt}] {symbol} | modify_order returned None — order may be filled already")

                    time.sleep(WAIT_MODIFY)

                    try:
                        history2 = api.single_order_history(ordid)[0]
                        status   = history2.get("status", "UNKNOWN").upper()
                        avgprice = float(history2.get("averagePrice", 0) or history2.get("price", 0) or 0)
                        reject_reason = (history2.get("rejectReason") or "").strip() or reject_reason
                    except Exception as hs2:
                        _log(f"[placeOrder-LMT-RETRY-{attempt}] {symbol} | status check failed ({hs2}) — continuing")
                        status, avgprice = "OPEN", 0

                    _log(f"[placeOrder-LMT-RETRY-{attempt}] {symbol} | status={status} | avgprice={avgprice}"
                         f"{f' | rejectReason={reject_reason}' if reject_reason else ''}")

                    if status == "COMPLETE":
                        if avgprice == 0:
                            avgprice = get_ltp(api, symbol, exchange, max_retries=3) or 0
                        _log(f"[placeOrder-LMT-RETRY-{attempt}] {symbol} | FILLED ✓ avgprice={avgprice} "
                             f"| FILLED_ON_RETRY_ATTEMPT={attempt}/{MAX_RETRIES} buf={retry_buffer*100:.0f}%"
                             f"{' (widened for low-premium leg)' if is_low_premium else ''}")
                        return ordid, status, float(avgprice)

                    if status in ("REJECTED", "CANCELLED", "CANCELED"):
                        _log(f"[placeOrder-LMT-RETRY-{attempt}] {symbol} | order {status} — stopping retries"
                             f"{f' | reason: {reject_reason}' if reject_reason else ''}",
                             telegram=True, client=api.name)
                        return "", f"ORDER_{status}_retry{attempt}" + (f" ({reject_reason})" if reject_reason else ""), 0

                except Exception as re:
                    _log(f"[placeOrder-LMT-RETRY-{attempt}] {symbol} | retry exception: {re} — continuing")

            # ── All retries exhausted — cancel and give up ──────
            _log(f"[placeOrder-LMT] {symbol} | {buy_sell} | all {MAX_RETRIES} retries exhausted — cancelling"
                 f"{f' | last known reason: {reject_reason}' if reject_reason else ''}",
                 telegram=True, client=api.name)
            try:
                cancel_resp = api.cancel_order(orderno=ordid)
                _log(f"[placeOrder-LMT] {symbol} | cancel_order response for {ordid}: {cancel_resp}")
            except Exception as ce:
                _log(f"[placeOrder-LMT] {symbol} | cancel_order failed: {ce}")

            # The order can fill between the last status check and the cancel,
            # and a cancel can leave a partial fill behind: confirm the end state.
            final = None
            for _ in range(3):
                time.sleep(WAIT_MODIFY)
                try:
                    rows = api.single_order_history(ordid)
                except Exception as hs3:
                    _log(f"[placeOrder-LMT] {symbol} | post-cancel status check failed: {hs3}")
                    rows = []
                if rows:
                    final = rows[0]
                    if str(final.get("status", "")).upper() in ("COMPLETE", "REJECTED", "CANCELLED", "CANCELED"):
                        break

            final_status = str(final.get("status", "UNKNOWN")).upper() if final else "UNKNOWN"
            final_avg    = float(final.get("averagePrice", 0) or 0) if final else 0.0
            final_filled = _filled_qty(final) if final else None
            _log(f"[placeOrder-LMT] {symbol} | after cancel: status={final_status} avg={final_avg} filled={final_filled}")

            if final_status == "COMPLETE":
                if final_avg == 0:
                    final_avg = get_ltp(api, symbol, exchange, max_retries=3) or 0
                _log(f"⚠️ [placeOrder-LMT] {symbol} | order {ordid} FILLED before the cancel took effect (avg {final_avg})",
                     telegram=True, client=api.name)
                return ordid, "COMPLETE", float(final_avg)

            if final_status in ("REJECTED", "CANCELLED", "CANCELED"):
                if (final_filled or 0) > 0 or (final_filled is None and final_avg > 0):
                    shown = final_filled if final_filled is not None else "?"
                    _log(f"🚨🚨 SOS — PARTIAL FILL 🚨🚨\n{symbol} | {buy_sell} | order {ordid}\n"
                         f"Filled {shown} of {qty} @ avg {final_avg}, rest cancelled.\n"
                         f"POSITION IS OPEN — CHECK THE BROKER TERMINAL.", telegram=True, client=api.name)
                    return ordid, f"PARTIALLY_FILLED ({shown}/{qty})", float(final_avg)
                return "", f"ALL_{MAX_RETRIES}_RETRIES_EXHAUSTED" + (f" ({reject_reason})" if reject_reason else ""), 0

            _log(f"🚨🚨 SOS — ORDER STATE UNKNOWN AFTER CANCEL 🚨🚨\n{symbol} | {buy_sell} | order {ordid} | qty {qty}\n"
                 f"Last status: {final_status}. The order may still be live or filled.\n"
                 f"CHECK THE BROKER TERMINAL NOW.", telegram=True, client=api.name)
            return "", "UNKNOWN_AFTER_CANCEL", 0

        except Exception as e:
            _log(f"[placeOrder-LMT] {symbol} | {buy_sell} | outer exception: {e}", telegram=True, client=api.name)
            return "", f"EXCEPTION: {e}", 0

    # ── Non-MKT orders (LMT, SL, SLM) — unchanged, placed as-is ─
    price_type = {"LMT": "LMT", "SL": "SL-LMT", "SLM": "SL-MKT"}.get(order_type, order_type)

    try:
        _log(f"[placeOrder] {buy_sell} {symbol} | {order_type} | qty={qty} | "
             f"price={price} | product={product}")

        resp = api.place_order(
            buy_or_sell=buySell,
            product_type=product,
            exchange=exchange,
            tradingsymbol=symbol,
            quantity=qty,
            price_type=price_type,
            price=price,
            trigger_price=trigger_price,
            retention="DAY",
            remarks="strategy_order",
            mkt_protection=mkt_protection,
        )

        _log(f"[placeOrder] API response: {resp}")

        if resp is None:
            resp, _ack_state = _resolve_no_response(api, buySell, symbol, qty, price)
            if _ack_state == "UNKNOWN":
                return "", "UNKNOWN_ORDER_STATE", 0

        if resp is None:
            _log(f"[placeOrder] No response from API — {symbol}", telegram=True, client=api.name)
            return "", "NO_RESPONSE", 0

        if resp.get("status") == "success":
            data = resp.get("data", {})

            # Sliced orders → data is a list
            if isinstance(data, list):
                order_id = data[0].get("orderNumber", "")
            else:
                order_id = data.get("orderNumber", "")
            _fs_seen_orders.add(str(order_id))

            # Fetch fill price from order history
            time.sleep(1)
            avg_price = 0
            try:
                history = api.single_order_history(order_id)
                if history:
                    status   = history[0].get("status", "UNKNOWN").upper()
                    avg_price = float(history[0].get("averagePrice", 0) or 0)
                    if avg_price == 0:
                        avg_price = float(history[0].get("price", 0) or 0)
                else:
                    status = "UNKNOWN"
            except Exception as e:
                _log(f"[placeOrder] Could not fetch history for {order_id}: {e}")
                status = "PLACED"

            # If fill price still 0 → fetch LTP as fallback
            if avg_price == 0:
                try:
                    avg_price = get_ltp(api, symbol, exchange, max_retries=3) or 0
                except Exception:
                    avg_price = 0

            _log(f"[placeOrder] DONE — orderId={order_id} | status={status} | avg={avg_price}")
            return order_id, status, float(avg_price)

        else:
            err = resp.get("error", {})
            err_msg = f"{resp.get('name')} — {err.get('message')}"
            _log(f"[placeOrder] FAILED — {err_msg}", telegram=True, client=api.name)
            return "", err_msg, 0

    except Exception as e:
        _log(f"[placeOrder] Exception: {e} | {symbol}", telegram=True, client=api.name)
        return "", f"EXCEPTION: {e}", 0


# ════════════════════════════════════════════════════════════════
#  SECTION 10 — INDICATORS
#  Identical to avinash_utils.py — pure pandas/numpy, broker-agnostic
# ════════════════════════════════════════════════════════════════

def EMA(df, base, target, period, alpha=False):
    con = pd.concat([df[:period][base].rolling(window=period).mean(), df[period:][base]])
    if alpha:
        df[target] = con.ewm(alpha=1 / period, adjust=False).mean()
    else:
        df[target] = con.ewm(span=period, adjust=False).mean()
    df[target] = df[target].fillna(0)   # df[col].fillna(inplace=True) does nothing on pandas 3
    return df


def ATR(df, period, ohlc=["open", "high", "low", "close"]):
    atr = "ATR_" + str(period)
    if "TR" not in df.columns:
        df["h-l"]  = df[ohlc[1]] - df[ohlc[2]]
        df["h-yc"] = abs(df[ohlc[1]] - df[ohlc[3]].shift())
        df["l-yc"] = abs(df[ohlc[2]] - df[ohlc[3]].shift())
        df["TR"]   = df[["h-l", "h-yc", "l-yc"]].max(axis=1)
        df.drop(["h-l", "h-yc", "l-yc"], axis=1, inplace=True)
    EMA(df, "TR", atr, period, alpha=True)
    return df


def SuperTrend(df, period, multiplier, ohlc=["open", "high", "low", "close"]):
    ATR(df, period, ohlc=ohlc)
    atr = "ATR_" + str(period)
    st  = "sup"
    stx = "STX"

    df["basic_ub"] = (df[ohlc[1]] + df[ohlc[2]]) / 2 + multiplier * df[atr]
    df["basic_lb"] = (df[ohlc[1]] + df[ohlc[2]]) / 2 - multiplier * df[atr]

    # Plain arrays: df[col].iat[i] = x writes to a temporary copy on pandas 3
    # and leaves df unchanged, which made every SuperTrend value 0.
    n        = len(df)
    basic_ub = df["basic_ub"].to_numpy(dtype=float)
    basic_lb = df["basic_lb"].to_numpy(dtype=float)
    close    = df[ohlc[3]].to_numpy(dtype=float)
    final_ub = np.zeros(n)
    final_lb = np.zeros(n)
    sup      = np.zeros(n)

    for i in range(period, n):
        final_ub[i] = (
            basic_ub[i]
            if basic_ub[i] < final_ub[i - 1] or close[i - 1] > final_ub[i - 1]
            else final_ub[i - 1]
        )
        final_lb[i] = (
            basic_lb[i]
            if basic_lb[i] > final_lb[i - 1] or close[i - 1] < final_lb[i - 1]
            else final_lb[i - 1]
        )

    for i in range(period, n):
        if sup[i - 1] == final_ub[i - 1]:
            sup[i] = final_lb[i] if close[i] > final_ub[i] else final_ub[i]
        else:
            sup[i] = final_ub[i] if close[i] < final_lb[i] else final_lb[i]

    df["final_ub"] = final_ub
    df["final_lb"] = final_lb
    df[st] = sup

    df[stx] = np.where(df[st] > 0.00, np.where(df[ohlc[3]] < df[st], "down", "up"), None)
    df.drop(["basic_ub", "basic_lb", "final_ub", "final_lb", "TR", f"ATR_{period}", "STX"],
            axis=1, inplace=True)
    df.fillna(0, inplace=True)
    return df


# ════════════════════════════════════════════════════════════════
#  SECTION 11 — MISC HELPERS
# ════════════════════════════════════════════════════════════════

def _kill_strategy(reason, client=None):
    """Hard process exit — same as FlatTrade. Kills all daemon threads."""
    msg = (f"🚨🚨 SOS — STRATEGY TERMINATING 🚨🚨\n"
           f"Reason: {reason}\n"
           f"All recovery attempts exhausted.\n"
           f"Restart manually!")
    try:
        _log(msg, telegram=True, client=client)
    except Exception:
        pass
    os._exit(1)


def get_positions(api: FirStockAPI):
    """
    Return positions as a DataFrame on success (empty DataFrame = genuinely
    flat, a normal state), or None if the call itself failed (session/auth
    problem, network error, etc). Callers MUST check `df is None` for
    "couldn't verify" separately from `len(df) == 0` for "verified flat" —
    the underlying api.get_positions() now returns None on failure instead
    of [] for exactly this reason (see its docstring). Silently treating a
    failed check as "confirmed flat" is what this fix removes.
    """
    try:
        data = api.get_positions()
        if data is None:
            return None
        return pd.DataFrame(data)
    except Exception as e:
        _log(f"[get_positions] Error: {e}")
        return None
