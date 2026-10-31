"""Send a Telegram message to every configured chat.

Reads TELEGRAM_BOT_TOKEN plus TELEGRAM_CHAT_ID (personal) and TELEGRAM_GROUP_CHAT_ID (group) from .env.
    python notify.py "text"      # send a test message
Never raises: a failed notification must not break a download.
"""
import os
import sys
from pathlib import Path

import requests
from dotenv import load_dotenv

from fyers_data.paths import ENV_FILE

CHAT_VARS = ["TELEGRAM_CHAT_ID", "TELEGRAM_GROUP_CHAT_ID"]


def send(text: str) -> bool:
    """True if the message reached at least one chat."""
    load_dotenv(ENV_FILE)
    token = os.environ.get("TELEGRAM_BOT_TOKEN")
    chats = [(v, os.environ[v]) for v in CHAT_VARS if os.environ.get(v)]
    if not token or not chats:
        print("notify: TELEGRAM_BOT_TOKEN and at least one chat id must be set", flush=True)
        return False
    ok = False
    for var, chat in chats:
        try:
            r = requests.post(f"https://api.telegram.org/bot{token}/sendMessage",
                              data={"chat_id": chat, "text": text[:4000]}, timeout=20)
            if r.status_code == 200:
                ok = True
            else:
                print(f"notify: {var} -> HTTP {r.status_code}: {r.text[:150]}", flush=True)
        except requests.RequestException as e:
            print(f"notify: {var} failed ({type(e).__name__})", flush=True)
    return ok


def send_document(path, caption: str = "") -> bool:
    """Send a file to every configured chat. True if it reached at least one."""
    load_dotenv(ENV_FILE)
    token = os.environ.get("TELEGRAM_BOT_TOKEN")
    chats = [(v, os.environ[v]) for v in CHAT_VARS if os.environ.get(v)]
    if not token or not chats:
        print("notify: TELEGRAM_BOT_TOKEN and at least one chat id must be set", flush=True)
        return False
    ok = False
    for var, chat in chats:
        try:
            with open(path, "rb") as fh:
                r = requests.post(f"https://api.telegram.org/bot{token}/sendDocument",
                                  data={"chat_id": chat, "caption": caption[:1000]},
                                  files={"document": (Path(path).name, fh)}, timeout=120)
            if r.status_code == 200:
                ok = True
            else:
                print(f"notify: {var} -> HTTP {r.status_code}: {r.text[:150]}", flush=True)
        except (requests.RequestException, OSError) as e:
            print(f"notify: {var} file send failed ({type(e).__name__})", flush=True)
    return ok


if __name__ == "__main__":
    print("sent" if send(" ".join(sys.argv[1:]) or "Test message from Algo_Backtesting") else "NOT sent")
