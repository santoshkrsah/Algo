"""Send a custom Telegram message to every chat configured in .env.

    python tools/send_telegram.py "your message"   # send it straight away
    python tools/send_telegram.py                  # asks you to type the message, then sends it
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from fyers_data.notify import send  # noqa: E402


def main() -> int:
    text = " ".join(sys.argv[1:]).strip() or input("Message to send: ").strip()
    if not text:
        print("Nothing to send.")
        return 1
    if send(text):
        print("sent")
        return 0
    print("NOT sent")
    return 1


if __name__ == "__main__":
    sys.exit(main())

# ayushanand440 , @albert_max