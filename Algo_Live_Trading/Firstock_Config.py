# Firstock_Config.py - reads Firstock_Credentials.txt and builds the creds dict
# that Firstock_Utils.firstock_login(api, creds) expects.

import os

_DIR = os.path.dirname(os.path.abspath(__file__))
FS_CREDENTIALS_FILE = os.path.join(_DIR, "Firstock_Credentials.txt")


def _read_credentials(path=FS_CREDENTIALS_FILE):
    """Parse 'KEY = value' lines (# = comment). Keys are upper-cased, spaces -> '_'."""
    out = {}
    with open(path, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            k, v = line.split("=", 1)
            out["_".join(k.upper().split())] = v.strip()
    return out


_c = _read_credentials()

FS_STATIC_IP = _c.get("STATIC_IP")

# Telegram alerts: TELEGRAM_BOT_TOKEN = ... and TELEGRAM_CHAT_IDS = id1,id2
FS_TELEGRAM_BOT_TOKEN = _c.get("TELEGRAM_BOT_TOKEN", "")
FS_TELEGRAM_CHAT_IDS = [x.strip() for x in _c.get("TELEGRAM_CHAT_IDS", "").split(",") if x.strip()]

FS_CREDS = {
    "name":        _c["USERID"],
    "user_id":     _c["USERID"],
    "password":    _c["PASSWORD"],
    "totp_key":    _c["TOTP_KEY"],
    "api_key":     _c["API_KEY"],
    "vendor_code": _c["API_SECRET"],  # Firstock vendorCode
    "file":        os.path.join(_DIR, f"{_c['USERID']}_jkey.txt"),  # session jKey saved here
    "enabled":     True,
}
