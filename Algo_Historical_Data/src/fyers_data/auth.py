"""Fyers v3 headless login (TOTP) using plain REST calls. Caches the token for the day."""
import base64
import hashlib
import json
import os
import time
from datetime import date
from pathlib import Path
from urllib.parse import parse_qs, urlparse

import pyotp
import requests
from dotenv import load_dotenv

from fyers_data.paths import CACHE, ENV_FILE

TOKEN_FILE = CACHE / "fyers_token.json"

URL_SEND_OTP = "https://api-t2.fyers.in/vagator/v2/send_login_otp_v2"
URL_VERIFY_OTP = "https://api-t2.fyers.in/vagator/v2/verify_otp"
URL_VERIFY_PIN = "https://api-t2.fyers.in/vagator/v2/verify_pin_v2"
URL_TOKEN = "https://api-t1.fyers.in/api/v3/token"
URL_VALIDATE = "https://api-t1.fyers.in/api/v3/validate-authcode"


def _b64(s: str) -> str:
    return base64.b64encode(s.encode()).decode()


def _post(url, **kw):
    r = requests.post(url, timeout=30, **kw)
    try:
        data = r.json()
    except ValueError:
        raise RuntimeError(f"{url} -> HTTP {r.status_code}: {r.text[:200]}")
    if r.status_code not in (200, 308):
        raise RuntimeError(f"{url} -> HTTP {r.status_code}: {data}")
    return data


def login() -> str:
    load_dotenv(ENV_FILE)
    app_id = os.environ["FYERS_APP_ID"]
    secret = os.environ["FYERS_SECRET_ID"]
    redirect = os.environ["FYERS_REDIRECT_URI"]
    user = os.environ["FYERS_USERNAME"]
    pin = os.environ["FYERS_PIN"]
    totp_key = os.environ["FYERS_TOTP_KEY"]

    d = _post(URL_SEND_OTP, json={"fy_id": _b64(user), "app_id": "2"})
    request_key = d["request_key"]

    # avoid submitting a TOTP code that is about to roll over
    if pyotp.TOTP(totp_key).interval - (time.time() % 30) < 3:
        time.sleep(4)
    d = _post(URL_VERIFY_OTP, json={"request_key": request_key,
                                    "otp": pyotp.TOTP(totp_key).now()})
    request_key = d["request_key"]

    d = _post(URL_VERIFY_PIN, json={"request_key": request_key,
                                    "identity_type": "pin",
                                    "identifier": _b64(pin)})
    bearer = d["data"]["access_token"]

    base_id = app_id.split("-")[0]
    d = _post(URL_TOKEN,
              headers={"Authorization": f"Bearer {bearer}", "content-type": "application/json"},
              json={"fyers_id": user, "app_id": base_id, "redirect_uri": redirect,
                    "appType": app_id.split("-")[1], "code_challenge": "", "state": "auth",
                    "scope": "", "nonce": "", "response_type": "code", "create_cookie": True})
    auth_code = parse_qs(urlparse(d["Url"]).query)["auth_code"][0]

    app_hash = hashlib.sha256(f"{app_id}:{secret}".encode()).hexdigest()
    d = _post(URL_VALIDATE, json={"grant_type": "authorization_code",
                                  "appIdHash": app_hash, "code": auth_code})
    token = d["access_token"]
    TOKEN_FILE.parent.mkdir(parents=True, exist_ok=True)
    TOKEN_FILE.write_text(json.dumps({"date": str(date.today()), "token": token}))
    return token


def get_token() -> str:
    if TOKEN_FILE.exists():
        t = json.loads(TOKEN_FILE.read_text())
        if t.get("date") == str(date.today()):
            return t["token"]
    return login()


def auth_header() -> dict:
    load_dotenv(ENV_FILE)
    return {"Authorization": f"{os.environ['FYERS_APP_ID']}:{get_token()}"}


if __name__ == "__main__":
    get_token()
    print("login ok, token cached")
