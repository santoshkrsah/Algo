"""Block commits that would leak secrets or data.

Checks every file git would commit (tracked + untracked-but-not-ignored) for:
  * any value from your local .env (API secret, PIN, TOTP key, bot token, chat ids, ...)
  * anything shaped like a Telegram bot token
  * unexpectedly large files (data must stay under data/, which is git-ignored)
Prints file names and the KEY NAME that matched, never the secret itself. Exit code 1 = problem found.

    python tools/check_secrets.py                  # run the check
    python tools/check_secrets.py --install-hook   # run it automatically before every git commit
"""
import re
import stat
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
MIN_SECRET_LEN = 6
MAX_FILE_MB = 5
PUBLIC_KEYS = {"FYERS_REDIRECT_URI"}                      # a public URL, fine to appear in .env.example
TELEGRAM_TOKEN = re.compile(r"\b\d{8,10}:[A-Za-z0-9_-]{35}\b")

HOOK = """#!/bin/sh
ROOT="$(git rev-parse --show-toplevel)"
PY="$ROOT/.venv/Scripts/python.exe"
[ -x "$PY" ] || PY="$ROOT/.venv/bin/python"
[ -x "$PY" ] || PY=python
exec "$PY" "$ROOT/tools/check_secrets.py"
"""


def env_secrets() -> dict[str, str]:
    env = ROOT / ".env"
    out = {}
    if env.exists():
        for line in env.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, val = line.split("=", 1)
            val = val.strip().strip("'\"")
            if key.strip() not in PUBLIC_KEYS and len(val) >= MIN_SECRET_LEN:
                out[key.strip()] = val
    return out


def committable_files() -> list[Path]:
    r = subprocess.run(["git", "ls-files", "--cached", "--others", "--exclude-standard", "-z"],
                       cwd=ROOT, capture_output=True, text=True, check=True)
    return [ROOT / p for p in r.stdout.split("\0") if p and (ROOT / p).is_file()]


def check() -> int:
    secrets = env_secrets()
    if not secrets:
        print("check_secrets: no .env found, so only pattern checks are possible")
    problems, files = [], committable_files()
    for f in files:
        rel = f.relative_to(ROOT).as_posix()
        if f.stat().st_size > MAX_FILE_MB * 1024 * 1024:
            problems.append(f"{rel}: larger than {MAX_FILE_MB} MB (data belongs in data/, which is ignored)")
            continue
        text = f.read_bytes().decode("utf-8", errors="ignore")
        for key, val in secrets.items():
            if val in text:
                problems.append(f"{rel}: contains the value of {key} from .env")
        if TELEGRAM_TOKEN.search(text):
            problems.append(f"{rel}: contains something shaped like a Telegram bot token")
    if (ROOT / ".env").exists() and any(f.name == ".env" for f in files):
        problems.append(".env would be committed - it must stay in .gitignore")
    if problems:
        print("check_secrets: FOUND PROBLEMS - do not commit:")
        for p in problems:
            print("  -", p)
        return 1
    print(f"check_secrets: OK ({len(files)} files checked against {len(secrets)} secrets from .env)")
    return 0


def install_hook():
    hook = ROOT / ".git" / "hooks" / "pre-commit"
    hook.write_text(HOOK, encoding="utf-8", newline="\n")
    hook.chmod(hook.stat().st_mode | stat.S_IEXEC)
    print(f"installed {hook.relative_to(ROOT).as_posix()} (runs this check before every commit)")


if __name__ == "__main__":
    if "--install-hook" in sys.argv:
        install_hook()
    else:
        sys.exit(check())
