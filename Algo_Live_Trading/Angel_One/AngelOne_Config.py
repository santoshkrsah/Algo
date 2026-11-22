# AngelOne_Config.py - reads AngelOne_Credentials.txt ("KEY = value", # = comment)
import os

_DIR = os.path.dirname(os.path.abspath(__file__))
AO_CREDENTIALS_FILE = os.path.join(_DIR, "AngelOne_Credentials.txt")


def _read_credentials(path=AO_CREDENTIALS_FILE):
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
AO_CLIENT_CODE = _c.get("CLIENT_CODE", "")
AO_PIN = _c.get("PIN", "")
AO_TOTP_KEY = _c.get("TOTP_KEY", "").replace(" ", "")
AO_API_KEY = _c.get("API_KEY", "")
AO_STATIC_IP = _c.get("STATIC_IP", "")
AO_TELEGRAM_BOT_TOKEN = _c.get("TELEGRAM_BOT_TOKEN", "")
AO_TELEGRAM_CHAT_IDS = [x.strip() for x in (_c.get("TELEGRAM_CHAT_IDS") or _c.get("TELEGRAM_GROUP_CHAT_ID", "")).split(",") if x.strip()]
