"""Telegram bot that LISTENS: tag @algo_assistants_bot (or reply to one of its messages) and Claude Code answers
the question or does the work, right there in Telegram.

    python tools/telegram_bot.py              # run it in this window (Ctrl+C stops it)
    python tools/telegram_bot.py --install    # start it hidden at every Windows logon (Startup folder) + start now
    python tools/telegram_bot.py --stop       # stop the running bot
    python tools/telegram_bot.py --uninstall  # stop it and remove the logon shortcut

- Reads new Telegram messages by long polling (no server / public URL). Only ONE program may read this bot's
  messages: never run two copies and never call getUpdates for this bot anywhere else.
- Group: answers only when tagged, when someone replies to a bot message, or for a /command. Private chat: always.
- Only allowed people: TELEGRAM_ALLOWED_USERS in .env (numeric ids and/or @usernames; a @username is pinned to its
  numeric id the first time it is used). The owner (TELEGRAM_CHAT_ID = the personal chat) is always allowed.
- Every question runs headless Claude Code (`claude -p`) in C:\\Algo, so CLAUDE.md, memory and all files apply.
  Reply to a bot answer = the same conversation continues (--resume). A fresh tag = a new conversation.
- Quick commands without Claude: /status /stop N /id /help
- Optional .env keys: BOT_PERMISSION_MODE (auto = full control with Claude's own safety check [default],
  plan = only read and answer), BOT_MODEL, BOT_EFFORT, BOT_MAX_JOBS (2), BOT_JOB_HOURS (4), CLAUDE_BIN (claude.exe).
- Masks every secret from the .env / credentials files in everything it sends. State + log: .telegram_bot/ (git-ignored).
"""
import argparse
import base64
import collections
import json
import logging
import os
import re
import shutil
import subprocess
import sys
import tempfile
import threading
import time
import uuid
from datetime import datetime
from glob import glob
from logging.handlers import RotatingFileHandler
from pathlib import Path

import requests
from dotenv import dotenv_values

HERE = Path(__file__).resolve()
PROJECT = HERE.parents[1]                 # Algo_Backtesting
ROOT = HERE.parents[2]                    # C:\Algo - Claude runs here (root CLAUDE.md, all three projects)
HOME = PROJECT / ".telegram_bot"          # state, log, downloaded files (git-ignored)
STATE_FILE = HOME / "state.json"
INBOX = HOME / "inbox"
BRIDGE_FILE = HOME / "bridge_prompt.md"
SECRET_FILES = [PROJECT / ".env", ROOT / "Algo_Historical_Data" / ".env",
                ROOT / "Algo_Live_Trading" / "Firstock_Credentials.txt",
                ROOT / "Algo_Live_Trading" / "AWS_VM" / "Firstock_Credentials.txt"]
JKEY_DIRS = [ROOT / "Algo_Live_Trading", ROOT / "Algo_Live_Trading" / "AWS_VM"]
NOT_SECRET = {"TELEGRAM_CHAT_ID", "TELEGRAM_GROUP_CHAT_ID", "TELEGRAM_CHAT_IDS", "TELEGRAM_ALLOWED_USERS", "SITE_URL",
              "SITE_PROTOCOL", "SITE_FTP_PORT", "FYERS_REDIRECT_URI", "HISTORICAL_DATA_DIR", "CLAUDE_BIN"}
STARTUP_LNK = Path(os.environ.get("APPDATA", str(Path.home()))) / \
    "Microsoft" / "Windows" / "Start Menu" / "Programs" / "Startup" / "Algo Telegram Bot.lnk"
NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)
STALE_MIN = 30          # a message older than this (bot was off) is not run
TYPING_FOR = 45         # seconds of "typing..." before a status message appears
EDIT_EVERY = 60         # status message refresh, seconds

log = logging.getLogger("tgbot")
CFG: dict = {}
API = FILE_API = GROUP_ID = OWNER_ID = BOT_NAME = ""
BOT_ID = 0
JOB_LIMIT = 4 * 3600.0
MAX_JOBS = 2
SLOTS = threading.BoundedSemaphore(MAX_JOBS)
STATE = {"offset": None, "sessions": {}, "pinned": {}, "next_job": 1}
STATE_LOCK = threading.RLock()
JOBS: dict = {}                            # job id -> Job (queued / running)
RECENT = collections.deque(maxlen=5)       # last finished jobs, for /status
T0 = time.time()

BRIDGE = """# Telegram bridge mode
This is a headless Claude Code session started by the Telegram bot @{bot} (Algo_Backtesting/tools/telegram_bot.py).
Each user message comes from an allowed team member on Telegram: Almax (@albert_max, technical) or
Ayush (@ayushanand440, trading expert); the first lines of the message say who and where.
Nobody watches this session live and nobody can approve permission prompts: anything that needs approval is refused.

- Your final message is posted to Telegram automatically as the reply. Write it in simple Hinglish (easy words),
  short and to the point, as plain text: no tables, headings, **bold** or long code blocks (Telegram shows raw text).
  Short "- " bullet lines are fine. Give the key numbers.
- Do not post that final answer yourself with send_telegram.py / notify.send. All the other Telegram rules in
  CLAUDE.md (backtest start/progress/finish messages, heartbeat, report file, tags) still apply to backtests/downloads.
- To attach files to your reply, end it with one line per file: SEND_FILE: <absolute path>  (max 5, under 45 MB each).
- This session ends when you give your final answer. For long work (backtests, downloads) either stay in the session
  until it is finished, or start it so that it keeps running on its own (the runners send their own Telegram messages)
  and say so in your answer.
- Never reveal secrets: tokens, passwords, API keys, TOTP keys, jKeys, FTP login, or what is inside .env,
  *Credentials*.txt and *_jkey.txt files.
- Never place, modify or cancel live broker orders and never run live-trading scripts that log in or trade
  (Algo_Live_Trading). If asked, say that this bot is not allowed to trade.
- Never call Telegram getUpdates for @{bot}: the bot program is the only reader of its messages.
- If the request is unclear, or a big change could go wrong, ask one short question back instead of guessing;
  the asker can reply to your message and this same session continues.
"""

HELP = """Main Algo Bot hun. Mujhse kuch bhi pucho ya koi kaam bolo, jawab Claude (PC wala) dega.

Kaise puchna hai:
- Group me mujhe tag karo: @{bot} report 12 ka PF kya tha?
- Ya mere kisi bhi message pe reply karo (backtest result wale message pe bhi).
- Personal chat me seedha likh do.
- Mere jawab pe reply karoge toh wahi baat aage chalegi. Naya tag karoge toh nayi baat shuru hogi.
- Photo ya file bhi bhej sakte ho (caption me sawaal likho).

Turant wale commands (inme Claude nahi lagta):
/status - abhi kya chal raha hai (bot ke kaam + PC pe chal rahe backtest/download)
/stop 3 - kaam number 3 rok do
/id - aapka Telegram ID
/help - ye message

Dhyan rakho:
- PC on hona chahiye. Chhota sawaal 10-60 sec leta hai, bada kaam (backtest, code change) zyada. 45 sec ke baad har minute status update aata rahega.
- Ye bot live trading ke order nahi lagata, aur password/token jaisi cheezein kabhi nahi bhejta."""


# ---------------------------------------------------------------- config / state

def opt(key: str, default: str = "") -> str:
    return (CFG.get(key) or default).strip()


def load_state() -> None:
    try:
        STATE.update(json.loads(STATE_FILE.read_text(encoding="utf-8")))
    except (OSError, ValueError):
        pass


def save_state() -> None:
    with STATE_LOCK:
        try:
            tmp = STATE_FILE.with_suffix(".tmp")
            tmp.write_text(json.dumps(STATE), encoding="utf-8")
            os.replace(tmp, STATE_FILE)
        except OSError as e:
            log.warning("state save failed: %s", e)


def remember(chat, mid, session: str) -> None:
    """A reply to this bot message continues `session`."""
    with STATE_LOCK:
        s = STATE["sessions"]
        s[f"{chat}:{mid}"] = session
        while len(s) > 3000:
            s.pop(next(iter(s)))
        save_state()


# ---------------------------------------------------------------- secrets

TOKEN_RX = [re.compile(r"\b\d{8,10}:[A-Za-z0-9_-]{30,}"),                                 # Telegram bot token
            re.compile(r"sk-ant-[A-Za-z0-9_-]{16,}"),                                      # Anthropic key
            re.compile(r"eyJ[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}")]     # JWT (Fyers tokens)
_secret_cache = {"t": 0.0, "vals": []}


def kv(path: Path) -> dict:
    """KEY -> value of a .env file or a 'KEY = value' credentials file (read like Firstock_Config does)."""
    if not path.exists():
        return {}
    if path.name == ".env":
        return {k.upper(): (v or "") for k, v in dotenv_values(path).items()}
    out = {}
    for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            k, v = line.split("=", 1)
            out["_".join(k.upper().split())] = v.strip()
    return out


def secrets() -> list:
    if time.time() - _secret_cache["t"] < 300:
        return _secret_cache["vals"]
    vals = set()
    for f in SECRET_FILES:
        try:
            for k, v in kv(f).items():
                v = v.strip().strip('"').strip("'")
                if k not in NOT_SECRET and not k.startswith("BOT_") and len(v) >= 6:
                    vals.add(v)
        except OSError:
            pass
    for d in JKEY_DIRS:
        for f in d.glob("*_jkey.txt"):
            try:
                vals.update(re.findall(r"[A-Za-z0-9_\-]{12,}", f.read_text(encoding="utf-8", errors="replace")))
            except OSError:
                pass
    _secret_cache.update(t=time.time(), vals=sorted(vals, key=len, reverse=True))
    return _secret_cache["vals"]


def redact(text: str) -> str:
    for v in secrets():
        if v in text:
            text = text.replace(v, "***")
    for rx in TOKEN_RX:
        text = rx.sub("***", text)
    return text


# ---------------------------------------------------------------- Telegram

def tg(method: str, _timeout: int = 30, **params):
    """Bot API call -> result, or None (logged; the token never goes to the log)."""
    for attempt in range(3):
        try:
            r = requests.post(f"{API}/{method}", json=params, timeout=_timeout)
            res = r.json()
        except (requests.RequestException, ValueError) as e:
            log.warning("telegram %s: %s", method, type(e).__name__)
            time.sleep(2 + 3 * attempt)
            continue
        if res.get("ok"):
            return res["result"]
        if r.status_code == 429:
            time.sleep(int((res.get("parameters") or {}).get("retry_after", 3)) + 1)
            continue
        if "not modified" not in res.get("description", ""):
            log.warning("telegram %s -> %s %s", method, r.status_code, res.get("description", "")[:200])
        return None
    return None


def where(chat, reply_to=None, topic=None) -> dict:
    p = {"chat_id": chat}
    if reply_to:
        p["reply_parameters"] = {"message_id": reply_to, "allow_sending_without_reply": True}
    if topic:
        p["message_thread_id"] = topic
    return p


def split(text: str, n: int = 4000) -> list:
    text = text.strip() or "(khaali jawab)"
    parts = []
    while len(text) > n:
        cut = text.rfind("\n", 0, n)
        cut = cut if cut > n // 2 else n
        parts.append(text[:cut].rstrip())
        text = text[cut:].lstrip("\n")
    return parts + [text]


def clean(text: str) -> str:
    """Telegram gets plain text: drop **bold** markers and # headings."""
    text = re.sub(r"\*\*(.+?)\*\*", r"\1", text)
    return re.sub(r"(?m)^#{1,6}\s+", "", text)


def send(chat, text: str, reply_to=None, topic=None) -> list:
    """Masked text, split at 4000 chars (very long answers also go as a .txt file). Returns the message ids."""
    text = clean(redact(text))
    parts, whole = split(text), None
    if len(parts) > 4:
        whole, parts = text, [parts[0] + "\n\n(Jawab lamba hai, poora neeche file me hai.)"]
    ids = []
    for part in parts:
        res = tg("sendMessage", text=part, link_preview_options={"is_disabled": True}, **where(chat, reply_to, topic))
        if res:
            ids.append(res["message_id"])
    if whole:
        f = HOME / f"jawab_{datetime.now():%Y%m%d_%H%M%S}.txt"
        f.write_text(whole, encoding="utf-8")
        mid = send_file(chat, f, reply_to, topic)
        if mid:
            ids.append(mid)
    return ids


def send_file(chat, path, reply_to=None, topic=None):
    data = {k: (json.dumps(v) if isinstance(v, dict) else str(v)) for k, v in where(chat, reply_to, topic).items()}
    for _ in range(2):
        try:
            with open(path, "rb") as fh:
                r = requests.post(f"{API}/sendDocument", data=data, files={"document": (Path(path).name, fh)},
                                  timeout=300)
            res = r.json()
            if res.get("ok"):
                return res["result"]["message_id"]
            log.warning("sendDocument %s -> %s", Path(path).name, res.get("description", "")[:200])
            return None
        except (requests.RequestException, ValueError, OSError) as e:
            log.warning("sendDocument %s: %s", Path(path).name, type(e).__name__)
            time.sleep(3)
    return None


def edit(chat, mid, text: str) -> None:
    tg("editMessageText", chat_id=chat, message_id=mid, text=split(clean(redact(text)))[0],
       link_preview_options={"is_disabled": True})


def topic_of(m: dict):
    return m.get("message_thread_id") if m.get("is_topic_message") else None


def reply(m: dict, text: str) -> list:
    return send(m["chat"]["id"], text, m["message_id"], topic_of(m))


def tell_owner(text: str) -> None:
    if OWNER_ID:
        send(OWNER_ID, text)


def who(u: dict) -> str:
    name = " ".join(x for x in (u.get("first_name"), u.get("last_name")) if x) or "?"
    return f"{name} (@{u['username']})" if u.get("username") else name


def fmt(sec: float) -> str:
    if sec < 60:
        return f"{sec:.0f} sec"
    if sec < 3600:
        return f"{sec / 60:.0f} min"
    return f"{int(sec // 3600)} ghanta {int(sec % 3600 // 60)} min"


# ---------------------------------------------------------------- who may use the bot

def allowed(user: dict) -> bool:
    uid, name = user["id"], (user.get("username") or "").lower()
    ids, names = set(), set()
    for x in re.split(r"[,\s]+", opt("TELEGRAM_ALLOWED_USERS")):
        if x.isdigit():
            ids.add(int(x))
        elif x:
            names.add(x.lstrip("@").lower())
    if OWNER_ID.isdigit():
        ids.add(int(OWNER_ID))      # the personal chat id is the owner's own user id
    if uid in ids:
        return True
    if not name or name not in names:
        return False
    with STATE_LOCK:
        pinned = STATE["pinned"].get(name)
        if pinned is None:
            STATE["pinned"][name] = uid
            save_state()
    if pinned is None:
        tell_owner(f"Bot: @{name} pehli baar aaya, uska Telegram ID {uid} allowed list me pakka kar diya.")
        return True
    if pinned == uid:
        return True
    tell_owner(f"Bot: @{name} naam se ek NAYE ID ({uid}) ne bot use karna chaha, block kar diya (pakka ID {pinned} hai).")
    return False


# ---------------------------------------------------------------- PC status (for /status)

def ps(script: str, env=None, timeout: int = 60) -> subprocess.CompletedProcess:
    """Run a PowerShell script (passed base64-encoded, so no quoting problems)."""
    enc = base64.b64encode(script.encode("utf-16-le")).decode()
    return subprocess.run(["powershell", "-NoProfile", "-NonInteractive", "-EncodedCommand", enc], env=env,
                          capture_output=True, text=True, timeout=timeout, creationflags=NO_WINDOW)


PY_PROCS = "Get-CimInstance Win32_Process -Filter \"Name='python.exe' OR Name='pythonw.exe'\""
PS_PROCS = (PY_PROCS + " | ForEach-Object { [pscustomobject]@{cmd=$_.CommandLine; start=$(if ($_.CreationDate) "
            "{ $_.CreationDate.ToString('yyyy-MM-ddTHH:mm:sszzz') } else { '' })} } | ConvertTo-Json -Compress")
SKIP_PROC = re.compile(r"telegram_bot\.py|\\\.vscode\\|pylance|debugpy|jedi|ms-python|lsp_server", re.I)


def python_programs():
    """Python programs running on this PC (backtests, downloads, heartbeats): [(args, minutes, now_line)] or None."""
    try:
        rows = json.loads(ps(PS_PROCS).stdout or "[]")
    except (OSError, subprocess.SubprocessError, ValueError):
        return None
    rows = [rows] if isinstance(rows, dict) else rows
    first = {}
    for p in rows:
        cmd = p.get("cmd") or ""
        args = re.sub(r'^\s*("[^"]*"|\S+)\s*', "", cmd).strip()      # drop the python.exe path
        if not args or SKIP_PROC.search(cmd):
            continue
        try:
            start = datetime.fromisoformat(p.get("start") or "")
        except ValueError:
            start = None
        if args not in first or (start and (first[args] is None or start < first[args])):
            first[args] = start      # a venv python.exe and its child share the same args: keep one
    out = []
    for args, start in first.items():
        mins = (datetime.now(start.tzinfo) - start).total_seconds() / 60 if start else None
        now = ""
        hb = re.search(r'--status-file\s+("[^"]+"|\S+)', args) if "status_heartbeat" in args else None
        if hb:
            try:
                now = Path(hb.group(1).strip('"')).read_text(encoding="utf-8").strip().splitlines()[0]
            except (OSError, IndexError):
                pass
        out.append((args.replace(str(ROOT) + "\\", ""), mins, now))
    return out


# ---------------------------------------------------------------- Claude jobs

class Job:
    def __init__(self, jid: int, m: dict, user: dict, prompt: str, session: str, resume: bool, title: str):
        self.id, self.prompt, self.session, self.resume, self.title = jid, prompt, session, resume, title
        self.chat, self.msg, self.topic = m["chat"]["id"], m["message_id"], topic_of(m)
        self.who = who(user)
        self.state, self.failed = "queued", False
        self.added, self.started = time.time(), None
        self.proc = None
        self.now, self.steps = "", 0             # current step (last tool call), number of tool calls
        self.status_msg = None
        self.last_edit = self.last_typing = 0.0
        self.lock = threading.Lock()


def claude_bin():
    p = opt("CLAUDE_BIN")
    if p and Path(p).exists():
        return p
    found = shutil.which("claude")
    if found:
        return found
    local = Path.home() / ".local" / "bin" / "claude.exe"
    if local.exists():
        return str(local)
    ext = glob(str(Path.home() / ".vscode" / "extensions" / "anthropic.claude-code-*" / "resources" /
                   "native-binary" / "claude.exe"))

    def ver(s):
        mm = re.search(r"claude-code-(\d+)\.(\d+)\.(\d+)", s)
        return tuple(map(int, mm.groups())) if mm else (0, 0, 0)
    return max(ext, key=ver) if ext else None


def child_env() -> dict:
    """This PC's environment minus the variables of a Claude Code session the bot may have been started from."""
    drop = {"CLAUDECODE", "CLAUDE_PID", "CLAUDE_EFFORT", "CLAUDE_AGENT_SDK_VERSION", "MCP_CONNECTION_NONBLOCKING"}
    return {k: v for k, v in os.environ.items() if k not in drop and not k.startswith(("CLAUDE_CODE_", "VSCODE_"))}


def tool_line(c: dict) -> str:
    inp = c.get("input") or {}
    d = next((inp[k] for k in ("description", "command", "file_path", "path", "pattern", "url", "query", "prompt")
              if inp.get(k)), "")
    d = " ".join(str(d).split())
    return f"{c.get('name', '?')}: {d[:100]}" if d else str(c.get("name", "?"))


SEND_RX = re.compile(r"^[ \t]*SEND_FILE:[ \t]*(.+?)[ \t]*$", re.M)
BLOCK_RX = re.compile(r"(^|[\\/])\.env|credential|_jkey|token|\.pem$|\.key$|id_rsa", re.I)
TEXT_EXT = {".txt", ".log", ".csv", ".json", ".md", ".py", ".html", ".htm", ".yaml", ".yml", ".ini", ".cfg"}


def unsafe(path: str):
    """None if the file may be sent, else the reason (Hinglish)."""
    try:
        rp = Path(path).resolve(strict=True)
    except OSError:
        return "file mili nahi"
    if not rp.is_file():
        return "ye file nahi hai"
    if BLOCK_RX.search(str(rp)):
        return "secret wali file hai"
    if not any(rp.is_relative_to(Path(b).resolve()) for b in (ROOT, tempfile.gettempdir())):
        return "C:\\Algo ke bahar ki file hai"
    size = rp.stat().st_size
    if size > 45 * 1024 * 1024:
        return "45 MB se badi hai"
    if rp.suffix.lower() in TEXT_EXT and size < 30 * 1024 * 1024:
        t = rp.read_text(encoding="utf-8", errors="replace")
        if redact(t) != t:
            return "isme password/token jaisi cheez hai"
    return None


def post_result(job: Job, ev: dict) -> None:
    text = (ev.get("result") or "").strip()
    if ev.get("is_error") or ev.get("subtype") not in (None, "success"):
        job.failed = True
        text = f"Claude ka error ({ev.get('subtype')}): {text or ev.get('api_error_status') or 'detail nahi mili'}"
    files = [p.strip().strip("\"'`") for p in SEND_RX.findall(text)][:5]
    text = SEND_RX.sub("", text).strip()
    denied = ev.get("permission_denials") or []
    if denied:
        names = sorted({str(d.get("tool_name", "?")) for d in denied if isinstance(d, dict)})
        text += f"\n\n(Note: {len(denied)} kaam safety/permission check ne rok diye: {', '.join(names)}.)"
    ids = send(job.chat, text or "(Claude ne khaali jawab diya)", job.msg, job.topic) if (text or not files) else []
    for f in files:
        why = unsafe(f)
        if why:
            ids += send(job.chat, f"File nahi bheji ({Path(f).name}): {why}.", job.msg, job.topic)
        else:
            mid = send_file(job.chat, f, job.msg, job.topic)
            if mid:
                ids.append(mid)
    for mid in ids:
        remember(job.chat, mid, job.session)


def run_claude(job: Job) -> None:
    exe = claude_bin()
    if not exe:
        raise RuntimeError("claude.exe nahi mila. .env me CLAUDE_BIN=<claude.exe ka path> daalo.")
    args = [exe, "-p", "--output-format", "stream-json", "--verbose",
            "--permission-mode", opt("BOT_PERMISSION_MODE", "auto"), "--permission-prompts", "none",
            "--append-system-prompt-file", str(BRIDGE_FILE)]
    if job.resume:
        args += ["--resume", job.session]
    else:
        args += ["--session-id", job.session, "--name", f"Telegram #{job.id}: {job.title}"]
    for key, flag in (("BOT_MODEL", "--model"), ("BOT_EFFORT", "--effort")):
        if opt(key):
            args += [flag, opt(key)]
    job.proc = subprocess.Popen(args, cwd=ROOT, env=child_env(), stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                stderr=subprocess.PIPE, text=True, encoding="utf-8", errors="replace",
                                creationflags=NO_WINDOW)
    err = collections.deque(maxlen=40)
    threading.Thread(target=lambda: err.extend(job.proc.stderr), daemon=True).start()
    try:
        job.proc.stdin.write(job.prompt)
        job.proc.stdin.close()
    except OSError:
        pass
    results = 0
    for line in job.proc.stdout:
        try:
            ev = json.loads(line)
        except ValueError:
            continue
        if ev.get("type") == "assistant":
            for c in (ev.get("message") or {}).get("content") or []:
                if c.get("type") == "tool_use":
                    job.steps += 1
                    job.now = tool_line(c)
        elif ev.get("type") == "result":
            results += 1
            post_result(job, ev)
    job.proc.wait()
    if not results and job.state == "running":
        job.failed = True
        tail = "".join(err).strip()[-700:] or "(koi error text nahi)"
        send(job.chat, f"Job #{job.id}: Claude bina jawab diye band ho gaya (exit code {job.proc.returncode}).\n{tail}",
             job.msg, job.topic)


def status_text(job: Job) -> str:
    return (f"⚙️ Job #{job.id} chal raha hai: {fmt(time.time() - (job.started or job.added))} ({job.who})\n"
            f"Kaam: {job.title}\nAbhi: {job.now or 'soch raha hai'}\n"
            f"Steps: {job.steps} | Rokna ho toh: /stop {job.id}")


def run_job(job: Job) -> None:
    with SLOTS:
        if job.state == "stopped":
            return finish(job)
        job.state, job.started = "running", time.time()
        with job.lock:
            if job.status_msg and job.status_msg > 0:
                edit(job.chat, job.status_msg, status_text(job))
                job.last_edit = time.time()
        log.info("job %s start (%s, %s): %s", job.id, job.who, "resume" if job.resume else "new", job.title)
        try:
            run_claude(job)
        except Exception as e:  # noqa: BLE001 - a job must never kill the bot
            log.exception("job %s crashed", job.id)
            job.failed = True
            send(job.chat, f"Job #{job.id} me gadbad: {type(e).__name__}: {e}", job.msg, job.topic)
        finish(job)


def finish(job: Job) -> None:
    with job.lock:
        if job.state != "stopped":
            job.state = "failed" if job.failed else "done"
        JOBS.pop(job.id, None)
        RECENT.append(job)
        took = fmt(time.time() - (job.started or job.added))
        log.info("job %s %s (%s, %s steps)", job.id, job.state, took, job.steps)
        if job.status_msg and job.status_msg > 0:
            edit(job.chat, job.status_msg, {
                "done": f"✅ Job #{job.id} ho gaya ({took}, {job.steps} steps). Jawab neeche hai.",
                "failed": f"❌ Job #{job.id} me dikkat aayi ({took}). Detail neeche hai.",
                "stopped": f"🛑 Job #{job.id} rok diya gaya ({took})."}[job.state])


def stop_job(job: Job, why: str) -> None:
    job.state = "stopped"
    if job.proc and job.proc.poll() is None:
        subprocess.run(["taskkill", "/PID", str(job.proc.pid), "/T", "/F"], capture_output=True,
                       creationflags=NO_WINDOW)
    log.info("job %s stopped: %s", job.id, why)


def ticker() -> None:
    """Typing indicator, then a status message that is refreshed every minute; time limit."""
    while True:
        time.sleep(2)
        try:
            now = time.time()
            for job in list(JOBS.values()):
                if job.state != "running" or not job.started:
                    continue
                if now - job.started > JOB_LIMIT:
                    stop_job(job, "time limit")
                    send(job.chat, f"Job #{job.id} {fmt(JOB_LIMIT)} se zyada chala, isliye rok diya.", job.msg, job.topic)
                    continue
                with job.lock:
                    if job.state != "running":
                        continue
                    if now - job.started < TYPING_FOR and not job.status_msg:
                        if now - job.last_typing >= 4.5:
                            p = {"chat_id": job.chat, "action": "typing"}
                            if job.topic:
                                p["message_thread_id"] = job.topic
                            tg("sendChatAction", _timeout=10, **p)
                            job.last_typing = now
                    elif not job.status_msg:
                        ids = send(job.chat, status_text(job), job.msg, job.topic)
                        job.status_msg, job.last_edit = (ids[0] if ids else -1), now
                        for mid in ids:
                            remember(job.chat, mid, job.session)
                    elif job.status_msg > 0 and now - job.last_edit >= EDIT_EVERY:
                        edit(job.chat, job.status_msg, status_text(job))
                        job.last_edit = now
        except Exception:  # noqa: BLE001
            log.exception("ticker")


def build_prompt(m: dict, user: dict, question: str, files: list, resume: bool) -> str:
    chat = m["chat"]
    place = "personal chat" if chat["type"] == "private" else f'group "{chat.get("title", "")}"'
    lines = [f"[Telegram message | {datetime.fromtimestamp(m['date']):%Y-%m-%d %H:%M} | {place}]",
             f"Kisne bheja: {who(user)} (Telegram ID {user['id']})"]
    rt = m.get("reply_to_message")
    if rt and not resume:
        src = f"bot @{BOT_NAME}" if (rt.get("from") or {}).get("id") == BOT_ID else who(rt.get("from") or {})
        body = rt.get("text") or rt.get("caption") or (
            f"(file: {rt['document'].get('file_name', '')})" if rt.get("document") else "(text nahi hai)")
        lines += [f"Ye {src} ke is message ka reply hai:", "<<<", body[:3500], ">>>"]
    lines += [f"Attached file (PC pe save ki): {f}" for f in files]
    lines += ["", question or "(Sirf file bheji hai: isko dekh ke batao.)"]
    return "\n".join(lines)


def download(m: dict) -> list:
    """Photo / document of a message -> saved paths in .telegram_bot/inbox (Telegram lets bots fetch up to 20 MB)."""
    items = []
    if m.get("photo"):
        p = m["photo"][-1]
        items.append((p["file_id"], f"photo_{m['message_id']}.jpg", p.get("file_size") or 0))
    if m.get("document"):
        d = m["document"]
        items.append((d["file_id"], d.get("file_name") or f"file_{m['message_id']}", d.get("file_size") or 0))
    out = []
    for fid, name, size in items:
        info = tg("getFile", file_id=fid) if size <= 20 * 1024 * 1024 else None
        if not info or not info.get("file_path"):
            continue
        dest = INBOX / f"{datetime.now():%Y%m%d_%H%M%S}_{re.sub(r'[^A-Za-z0-9._-]', '_', name)}"
        try:
            r = requests.get(f"{FILE_API}/{info['file_path']}", timeout=120)
            if r.ok:
                dest.write_bytes(r.content)
                out.append(dest)
        except (requests.RequestException, OSError) as e:
            log.warning("download %s: %s", name, type(e).__name__)
    return out


def start_job(m: dict, user: dict, question: str, files: list, session) -> None:
    resume = session is not None
    if resume and any(j.session == session for j in list(JOBS.values())):
        reply(m, "Ye baat-cheet abhi chal rahi hai. Use khatam hone do (ya /stop karo), phir reply karna.")
        return
    session = session or str(uuid.uuid4())
    title = " ".join((question or "file dekhna").split())[:60]
    with STATE_LOCK:
        jid = STATE["next_job"]
        STATE["next_job"] = jid + 1
        save_state()
    job = Job(jid, m, user, build_prompt(m, user, question, files, resume), session, resume, title)
    busy = sum(1 for j in list(JOBS.values()) if j.state == "running")
    JOBS[jid] = job
    if busy >= MAX_JOBS:
        ids = reply(m, f"⏳ Job #{jid} line me hai ({busy} kaam pehle se chal rahe hain). /status se dekh sakte ho.")
        job.status_msg = ids[0] if ids else None
        for mid in ids:
            remember(job.chat, mid, session)
    threading.Thread(target=run_job, args=(job,), daemon=True, name=f"job{jid}").start()


# ---------------------------------------------------------------- quick commands

def cmd_help(m, arg):
    reply(m, HELP.format(bot=BOT_NAME))


def cmd_id(m, arg):
    reply(m, f"Aapka Telegram ID: {m['from']['id']}\nIs chat ka ID: {m['chat']['id']}")


def cmd_status(m, arg):
    lines = []
    active = sorted(list(JOBS.values()), key=lambda j: j.id)
    if active:
        lines.append("Bot ke kaam:")
        for j in active:
            if j.state == "running":
                lines.append(f"- #{j.id} chal raha hai {fmt(time.time() - j.started)} ({j.who}): {j.title}\n"
                             f"  abhi: {j.now or 'soch raha hai'}")
            else:
                lines.append(f"- #{j.id} {'line me hai' if j.state == 'queued' else j.state} ({j.who}): {j.title}")
    else:
        lines.append("Bot ka koi kaam abhi nahi chal raha.")
    if RECENT:
        last = RECENT[-1]
        lines.append(f"Pichla kaam: #{last.id} {last.state} ({last.who}): {last.title}")
    progs = python_programs()
    if progs is None:
        lines.append("\n(PC ke programs nahi padh paya.)")
    elif progs:
        lines.append("\nPC pe chal rahe Python programs:")
        for args, mins, now in progs:
            since = f" ({fmt(mins * 60)} se)" if mins is not None else ""
            if "status_heartbeat" in args:       # a Claude session at work: its status line says what it does
                lines.append(f"- Claude ka ek kaam chal raha hai{since}\n  abhi: {now[:200] or '(status khaali)'}")
            else:
                lines.append(f"- {args[:150]}{since}")
    else:
        lines.append("\nPC pe koi backtest/download program nahi chal raha.")
    lines.append(f"\nBot {fmt(time.time() - T0)} se chalu hai | Claude mode: {opt('BOT_PERMISSION_MODE', 'auto')}")
    reply(m, "\n".join(lines))


def cmd_stop(m, arg):
    active = [j for j in list(JOBS.values()) if j.state in ("queued", "running")]
    num = arg.strip().lstrip("#")
    if num.isdigit():
        job = JOBS.get(int(num))
    elif len(active) == 1:
        job = active[0]
    else:
        reply(m, "Kaunsa job rokna hai? /status dekho, phir /stop <number> likho." if active
              else "Abhi koi job nahi chal raha.")
        return
    if not job or job.state not in ("queued", "running"):
        reply(m, f"Job #{num} abhi chal nahi raha.")
        return
    stop_job(job, f"by {who(m['from'])}")
    reply(m, f"🛑 Job #{job.id} rok diya.")


QUICK = {"help": cmd_help, "start": cmd_help, "status": cmd_status, "jobs": cmd_status, "stop": cmd_stop, "id": cmd_id}


# ---------------------------------------------------------------- incoming messages

def parse_command(text: str, ents: list):
    """(name, rest) for '/name[@bot] rest'; (False, '') if the command is for another bot; (None, '') if none."""
    for e in ents:
        if e.get("type") == "bot_command" and e.get("offset") == 0:
            name, _, target = text[1:e["length"]].partition("@")
            if target and target.lower() != BOT_NAME.lower():
                return False, ""
            return name.lower(), text[e["length"]:].strip()
    return None, ""


def handle(m: dict) -> None:
    chat, user = m["chat"], m.get("from") or {}
    if not user or user.get("is_bot"):
        return
    private = chat["type"] == "private"
    if not private and str(chat["id"]) != GROUP_ID:
        return                                   # only our group + private chats
    text = m.get("text") or m.get("caption") or ""
    ents = m.get("entities") or m.get("caption_entities") or []
    cmd, arg = parse_command(text, ents)
    if cmd is False:
        return
    tag = f"@{BOT_NAME}".lower()
    tagged = tag in text.lower()
    rt = m.get("reply_to_message")
    to_bot = bool(rt and (rt.get("from") or {}).get("id") == BOT_ID)
    if not (private or tagged or to_bot or cmd):
        return
    if not allowed(user):
        log.info("not allowed: %s id=%s", who(user), user.get("id"))
        if not private:
            reply(m, f"Maaf karna, aap is bot ko use nahi kar sakte. Aapka Telegram ID: {user['id']} "
                     "(Almax ise .env ke TELEGRAM_ALLOWED_USERS me daal sakta hai).")
        return
    age = time.time() - m["date"]
    if age > STALE_MIN * 60:
        reply(m, f"Ye message {age / 60:.0f} min purana hai (tab bot band tha), isliye chhod diya. "
                 "Abhi bhi chahiye toh dobara bhejo.")
        return
    if cmd in QUICK:
        log.info("/%s from %s", cmd, who(user))
        QUICK[cmd](m, arg)
        return
    question = re.sub(re.escape(tag), "", text, flags=re.I).strip()
    files = download(m)
    if rt and not to_bot:
        files += download(rt)                    # e.g. a screenshot someone else posted
    if not question and not files:
        cmd_help(m, "")
        return
    tg("setMessageReaction", chat_id=chat["id"], message_id=m["message_id"],
       reaction=[{"type": "emoji", "emoji": "👀"}])
    session = STATE["sessions"].get(f"{chat['id']}:{rt['message_id']}") if to_bot else None
    log.info("question from %s (%s): %s", who(user), "resume" if session else "new", question[:80])
    start_job(m, user, question, files, session)


# ---------------------------------------------------------------- run / install

def init() -> None:
    global CFG, API, FILE_API, GROUP_ID, OWNER_ID, BOT_ID, BOT_NAME, JOB_LIMIT, MAX_JOBS, SLOTS
    HOME.mkdir(exist_ok=True)
    INBOX.mkdir(exist_ok=True)
    CFG = {k: (v or "") for k, v in dotenv_values(PROJECT / ".env").items()}
    token = opt("TELEGRAM_BOT_TOKEN")
    if not token:
        sys.exit("TELEGRAM_BOT_TOKEN .env me nahi hai.")
    API, FILE_API = f"https://api.telegram.org/bot{token}", f"https://api.telegram.org/file/bot{token}"
    GROUP_ID, OWNER_ID = opt("TELEGRAM_GROUP_CHAT_ID"), opt("TELEGRAM_CHAT_ID")
    try:
        MAX_JOBS = max(1, int(opt("BOT_MAX_JOBS", "2")))
        JOB_LIMIT = float(opt("BOT_JOB_HOURS", "4")) * 3600
    except ValueError:
        log.warning("BOT_MAX_JOBS / BOT_JOB_HOURS galat hai, default (2 jobs, 4 ghante) le raha hun")
    SLOTS = threading.BoundedSemaphore(MAX_JOBS)
    wait = 5
    while not (me := tg("getMe")):               # at logon the internet may not be up yet
        log.warning("getMe failed, retry in %s s", wait)
        time.sleep(wait)
        wait = min(wait * 2, 300)
    BOT_ID, BOT_NAME = me["id"], me.get("username", "")
    load_state()
    BRIDGE_FILE.write_text(BRIDGE.format(bot=BOT_NAME), encoding="utf-8")
    threading.Thread(target=ticker, daemon=True, name="ticker").start()


def poll(offset):
    params = {"timeout": 50, "allowed_updates": ["message"]}
    if offset:
        params["offset"] = offset
    try:
        r = requests.post(f"{API}/getUpdates", json=params, timeout=70)
        res = r.json()
    except (requests.RequestException, ValueError) as e:
        log.warning("getUpdates: %s", type(e).__name__)
        return None
    if res.get("ok"):
        return res["result"]
    log.error("getUpdates -> %s %s", r.status_code, res.get("description", "")[:200])
    if r.status_code == 409:
        log.error("Koi aur program bhi is bot ke messages padh raha hai (409). Sirf ek copy chalao.")
    return None


def listen() -> None:
    fails = 0
    while True:
        ups = poll(STATE.get("offset"))
        if ups is None:
            fails += 1
            time.sleep(min(60, 5 * fails))
            continue
        fails = 0
        for u in ups:
            with STATE_LOCK:                     # saved first: a message is never run twice after a restart
                STATE["offset"] = u["update_id"] + 1
                save_state()
            m = u.get("message")
            if not m:
                continue
            try:
                handle(m)
            except Exception as e:  # noqa: BLE001 - one bad message must not stop the bot
                log.exception("handle failed")
                try:
                    reply(m, f"Bot me error aaya: {type(e).__name__}. Almax log dekh le (.telegram_bot/bot.log).")
                except Exception:  # noqa: BLE001
                    pass


def serve() -> None:
    init()
    log.info("bot @%s chalu | claude: %s | mode: %s", BOT_NAME, claude_bin(), opt("BOT_PERMISSION_MODE", "auto"))
    if not claude_bin():
        tell_owner("Bot chalu hua par claude.exe nahi mila. .env me CLAUDE_BIN set karo.")
    tell_owner(f"✅ Telegram bot chalu ho gaya (PC). Group me @{BOT_NAME} ko tag karke kuch bhi pucho. /help")
    while True:
        try:
            listen()
        except Exception:  # noqa: BLE001 - keep the bot alive
            log.exception("listen crashed, 30 s me dobara")
            time.sleep(30)


def single_instance():
    HOME.mkdir(exist_ok=True)
    fh = open(HOME / "bot.lock", "a+")
    try:
        import msvcrt
        fh.seek(0)
        msvcrt.locking(fh.fileno(), msvcrt.LK_NBLCK, 1)
    except ImportError:
        pass
    except OSError:
        log.error("bot pehle se chal raha hai, ye copy band")
        sys.exit("telegram_bot.py pehle se chal raha hai. Dusri copy mat chalao.")
    return fh


def bot_pids() -> list:
    r = ps(PY_PROCS + " | Where-Object { $_.CommandLine -like '*telegram_bot.py*' -and "
           "$_.CommandLine -notlike '*--stop*' -and $_.CommandLine -notlike '*--install*' -and "
           "$_.CommandLine -notlike '*--uninstall*' } | ForEach-Object { $_.ProcessId }")
    return [int(x) for x in r.stdout.split() if x.isdigit() and int(x) != os.getpid()]


def stop_bot() -> None:
    pids = bot_pids()
    for pid in pids:
        subprocess.run(["taskkill", "/PID", str(pid), "/T", "/F"], capture_output=True, creationflags=NO_WINDOW)
    print(f"Bot band kiya ({len(pids)} process)." if pids else "Bot chal hi nahi raha tha.")


def install() -> None:
    pyw = Path(sys.executable).with_name("pythonw.exe")
    exe = pyw if pyw.exists() else Path(sys.executable)
    env = dict(os.environ, BOT_LNK=str(STARTUP_LNK), BOT_EXE=str(exe), BOT_PY=str(HERE), BOT_WD=str(PROJECT))
    r = ps("$s = (New-Object -ComObject WScript.Shell).CreateShortcut($env:BOT_LNK); $s.TargetPath = $env:BOT_EXE; "
           "$s.Arguments = [char]34 + $env:BOT_PY + [char]34; $s.WorkingDirectory = $env:BOT_WD; $s.Save()", env=env)
    if not STARTUP_LNK.exists():
        sys.exit(f"Shortcut nahi bana: {r.stderr.strip()[:300]}")
    print(f"Har logon pe bot apne aap chalega: {STARTUP_LNK}")
    if bot_pids():
        print("Bot pehle se chal raha hai.")
        return
    # Start it the way Windows does at logon (explorer opens the shortcut), so it does not belong to this
    # terminal / editor and keeps running when they close.
    subprocess.run(["explorer.exe", str(STARTUP_LNK)], creationflags=NO_WINDOW)
    for _ in range(20):
        time.sleep(1)
        if bot_pids():
            print("Bot abhi chalu kar diya (hidden). Log: .telegram_bot/bot.log")
            return
    subprocess.Popen([str(exe), str(HERE)], cwd=PROJECT, close_fds=True,
                     creationflags=0x00000008 | 0x00000200)     # DETACHED_PROCESS | CREATE_NEW_PROCESS_GROUP
    print("Bot abhi chalu kar diya (hidden, seedha). Log: .telegram_bot/bot.log")


def setup_log() -> None:
    HOME.mkdir(exist_ok=True)
    handlers = [RotatingFileHandler(HOME / "bot.log", maxBytes=2_000_000, backupCount=3, encoding="utf-8")]
    if sys.stderr:
        handlers.append(logging.StreamHandler())
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s", handlers=handlers)
    logging.getLogger("urllib3").setLevel(logging.WARNING)


def main() -> int:
    ap = argparse.ArgumentParser(description="Telegram bot: tag it and Claude Code answers.")
    g = ap.add_mutually_exclusive_group()
    g.add_argument("--install", action="store_true", help="start at every Windows logon (hidden) and start now")
    g.add_argument("--uninstall", action="store_true", help="stop the bot and remove the logon shortcut")
    g.add_argument("--stop", action="store_true", help="stop the running bot")
    a = ap.parse_args()
    if a.install:
        install()
        return 0
    if a.stop or a.uninstall:
        stop_bot()
        if a.uninstall and STARTUP_LNK.exists():
            STARTUP_LNK.unlink()
            print("Logon shortcut hata diya.")
        return 0
    setup_log()
    lock = single_instance()  # noqa: F841 - held open while the bot runs
    try:
        serve()
    except KeyboardInterrupt:
        log.info("bot band (Ctrl+C)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
