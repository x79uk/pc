#!/usr/bin/env python3
"""
🔥 79 ADVANCED SCRIPT HOSTING ENGINE (V3 - MINI APP EDITION)
- Clean UI (Message Editing + No Chat Clutter)
- Full Universal Back & Main Menu Navigation
- Role-Based Interface (Terminal Restricted to Admin)
- Admin Spy Mode (Instant DM Notifications on User Activity)
- Auto-Repair & OpenAI v1.x Debugging
"""

import asyncio
import atexit
import json
import logging
import os
import re
import shlex
import signal
import subprocess
import sys
import zipfile
from datetime import datetime, timedelta
from pathlib import Path
from threading import Thread
from typing import List, Optional, Tuple, Dict, Any

import psutil
from dotenv import load_dotenv
from flask import Flask, jsonify
from telegram import (
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    Update,
)
from telegram.ext import (
    Application,
    CallbackQueryHandler,
    CommandHandler,
    ConversationHandler,
    MessageHandler,
    ContextTypes,
    filters,
)

# ---------- LOGGING SETUP ----------
logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO,
)
logger = logging.getLogger("79Engine")

# ---------- CONFIG & ENVIRONMENT ----------
load_dotenv()

BOT_TOKEN = os.getenv("BOT_TOKEN", "8923078994:AAEYk_-hdpVh2NYN4_yXX5lERPhVt8Ccs1I").strip()
ADMIN_IDS_STR = os.getenv("ADMIN_IDS", "7546911540").strip()
ADMIN_IDS = [int(x.strip()) for x in ADMIN_IDS_STR.split(",") if x.strip().isdigit()] or [7546911540]

CHANNEL_USERNAME = os.getenv("CHANNEL_USERNAME", "@seventyx79").strip()
PORT = int(os.getenv("PORT") or os.getenv("RAILWAY_PUBLIC_PORT") or "8080")
WORKSPACE_BASE = Path(os.getenv("WORKSPACE_BASE", "./workspaces"))
MAX_UPLOAD_SIZE = int(os.getenv("MAX_UPLOAD_SIZE", "10MB").replace("MB", "")) * 1024 * 1024
MAX_ARCHIVE_SIZE = int(os.getenv("MAX_ARCHIVE_SIZE", "50MB").replace("MB", "")) * 1024 * 1024
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY", "").strip()
SCRIPT_TIMEOUT = int(os.getenv("SCRIPT_TIMEOUT", "300"))

WORKSPACE_BASE.mkdir(parents=True, exist_ok=True)

# Conversation States
UPLOAD_WAIT, TERMINAL_SESSION = range(2)

# ---------- PATH REGISTRY (Solves Telegram 64-Byte Callback Data Limit) ----------
class PathRegistry:
    def __init__(self):
        self._registry: Dict[str, str] = {}
        self._counter = 0

    def register(self, path: Path) -> str:
        resolved = str(path.resolve())
        for k, v in self._registry.items():
            if v == resolved:
                return k
        key = f"p79_{self._counter}"
        self._registry[key] = resolved
        self._counter += 1
        return key

    def get(self, key: str) -> Optional[Path]:
        res = self._registry.get(key)
        return Path(res) if res else None

path_registry = PathRegistry()

# ---------- PERSISTENCE & DATA MANAGER ----------
DATA_FILE = "bot_data.json"

def load_data() -> dict:
    if Path(DATA_FILE).exists():
        try:
            with open(DATA_FILE, "r") as f:
                return json.load(f)
        except Exception as e:
            logger.error(f"Failed to load data file: {e}")
    return {"users": {}, "processes": [], "telemetry": {}}

def save_data(data: dict):
    try:
        with open(DATA_FILE, "w") as f:
            json.dump(data, f, indent=2, default=str)
    except Exception as e:
        logger.error(f"Failed to save data file: {e}")

class UserManager:
    def __init__(self):
        self.data = load_data()
        self.users = self.data.get("users", {})
        self.processes = self.data.get("processes", [])
        self.telemetry = self.data.get("telemetry", {})

    def save(self):
        self.data["users"] = self.users
        self.data["processes"] = self.processes
        self.data["telemetry"] = self.telemetry
        save_data(self.data)

    def get_user(self, user_id: int) -> Optional[dict]:
        return self.users.get(str(user_id))

    def add_user(self, user_id: int, name: str, username: str):
        status = "approved" if user_id in ADMIN_IDS else "pending"
        self.users[str(user_id)] = {
            "status": status,
            "name": name,
            "username": username,
            "request_time": datetime.now().isoformat(),
            "workspace": str(WORKSPACE_BASE / str(user_id)),
        }
        self.telemetry.setdefault(str(user_id), {"runs": 0, "success": 0, "fail": 0, "bad": 0})
        self.save()

    def approve_user(self, user_id: int) -> bool:
        if u := self.users.get(str(user_id)):
            u["status"] = "approved"
            self.save()
            return True
        return False

    def ban_user(self, user_id: int) -> bool:
        if u := self.users.get(str(user_id)):
            u["status"] = "banned"
            self.save()
            return True
        return False

    def unban_user(self, user_id: int) -> bool:
        if u := self.users.get(str(user_id)):
            u["status"] = "approved"
            self.save()
            return True
        return False

    def is_approved(self, user_id: int) -> bool:
        if user_id in ADMIN_IDS:
            return True
        u = self.get_user(user_id)
        return bool(u and u.get("status") == "approved")

    def is_pending(self, user_id: int) -> bool:
        if user_id in ADMIN_IDS:
            return False
        u = self.get_user(user_id)
        return bool(u and u.get("status") == "pending")

    def is_banned(self, user_id: int) -> bool:
        u = self.get_user(user_id)
        return bool(u and u.get("status") == "banned")

    def get_workspace(self, user_id: int) -> Path:
        u = self.get_user(user_id)
        return Path(u["workspace"]) if u else WORKSPACE_BASE / str(user_id)

    def get_pending_requests(self) -> List[dict]:
        return [{"user_id": k, **v} for k, v in self.users.items() if v.get("status") == "pending"]

    def get_approved_users(self) -> List[dict]:
        return [{"user_id": k, **v} for k, v in self.users.items() if v.get("status") == "approved"]

    def get_banned_users(self) -> List[dict]:
        return [{"user_id": k, **v} for k, v in self.users.items() if v.get("status") == "banned"]

    def add_process(self, user_id: int, filename: str, pid: int, log_path: str):
        self.processes.append({
            "user_id": user_id,
            "filename": filename,
            "pid": pid,
            "start_time": datetime.now().isoformat(),
            "status": "running",
            "log_path": log_path,
        })
        self.save()

    def get_user_processes(self, user_id: int) -> List[dict]:
        return [p for p in self.processes if p.get("user_id") == user_id]

    def get_all_processes(self) -> List[dict]:
        return self.processes

    def stop_process(self, pid: int) -> bool:
        for p in self.processes:
            if p.get("pid") == pid:
                try:
                    os.kill(pid, signal.SIGTERM)
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

    def inc_run(self, user_id: int):
        uid = str(user_id)
        self.telemetry.setdefault(uid, {"runs": 0, "success": 0, "fail": 0, "bad": 0})
        self.telemetry[uid]["runs"] += 1
        self.save()

    def inc_success(self, user_id: int):
        uid = str(user_id)
        self.telemetry.setdefault(uid, {"runs": 0, "success": 0, "fail": 0, "bad": 0})
        self.telemetry[uid]["success"] += 1
        self.save()

    def inc_fail(self, user_id: int):
        uid = str(user_id)
        self.telemetry.setdefault(uid, {"runs": 0, "success": 0, "fail": 0, "bad": 0})
        self.telemetry[uid]["fail"] += 1
        self.save()

    def inc_bad(self, user_id: int):
        uid = str(user_id)
        self.telemetry.setdefault(uid, {"runs": 0, "success": 0, "fail": 0, "bad": 0})
        self.telemetry[uid]["bad"] += 1
        self.save()

    def get_user_telemetry(self, user_id: int) -> dict:
        return self.telemetry.get(str(user_id), {"runs": 0, "success": 0, "fail": 0, "bad": 0})

user_manager = UserManager()
atexit.register(user_manager.cleanup_all)

# ---------- ADMIN SPY NOTIFICATION ENGINE ----------
async def spy_notify_admins(context: ContextTypes.DEFAULT_TYPE, title: str, user_info: str, details: str):
    """Sends immediate tracking alert DM to Admins whenever users perform actions."""
    text = (
        f"🕵️‍♂️ *79 SPY TRACKER ALERT*\n"
        f"📌 *Event:* `{title}`\n"
        f"👤 *User:* {user_info}\n"
        f"📝 *Details:* {details}\n"
        f"⏱ *Time:* `{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}`"
    )
    for admin_id in ADMIN_IDS:
        try:
            await context.bot.send_message(chat_id=admin_id, text=text, parse_mode="Markdown")
        except Exception as e:
            logger.warning(f"Spy Alert failed for admin {admin_id}: {e}")

# ---------- HELPER UTILITIES ----------
def sanitize_filename(filename: str) -> str:
    return os.path.basename(filename).replace("/", "").replace("\\", "")

def ensure_workspace(user_id: int) -> Path:
    ws = user_manager.get_workspace(user_id)
    ws.mkdir(parents=True, exist_ok=True)
    return ws

def is_safe_path(user_id: int, path: Path) -> bool:
    ws = ensure_workspace(user_id)
    try:
        path.resolve().relative_to(ws.resolve())
        return True
    except ValueError:
        return False

def extract_zip(zip_path: Path, dest_dir: Path) -> Tuple[bool, str]:
    try:
        with zipfile.ZipFile(zip_path, "r") as z:
            total = sum(i.file_size for i in z.infolist())
            if total > MAX_ARCHIVE_SIZE:
                return False, f"Archive size exceeds limit ({MAX_ARCHIVE_SIZE // 1024 // 1024}MB)"
            for m in z.infolist():
                if m.filename.startswith("/") or ".." in m.filename:
                    return False, "Unsafe file path detected in ZIP"
                target = dest_dir / m.filename
                try:
                    target.resolve().relative_to(dest_dir.resolve())
                except ValueError:
                    return False, "Path traversal attack blocked"
            z.extractall(dest_dir)
        return True, "Extraction successful"
    except Exception as e:
        return False, str(e)

def detect_entry_point(dest_dir: Path) -> Tuple[Optional[str], Optional[str]]:
    for name in ("main.py", "bot.py", "index.js", "app.py"):
        p = dest_dir / name
        if p.exists():
            return ("py" if name.endswith(".py") else "js", str(p))
    pys = list(dest_dir.glob("*.py"))
    if pys:
        return ("py", str(pys[0]))
    jss = list(dest_dir.glob("*.js"))
    if jss:
        return ("js", str(jss[0]))
    return None, None

async def install_dependencies(dest_dir: Path) -> Tuple[bool, str]:
    req = dest_dir / "requirements.txt"
    pkg = dest_dir / "package.json"
    if req.exists():
        proc = await asyncio.create_subprocess_exec(
            sys.executable, "-m", "pip", "install", "-r", str(req),
            cwd=str(dest_dir),
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT,
        )
        out, _ = await proc.communicate()
        text = out.decode(errors="ignore")
        return proc.returncode == 0, text[:400]
    if pkg.exists():
        try:
            proc = await asyncio.create_subprocess_exec(
                "npm", "install", "--production",
                cwd=str(dest_dir),
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.STDOUT,
            )
            out, _ = await proc.communicate()
            return proc.returncode == 0, out.decode(errors="ignore")[:400]
        except Exception as e:
            return False, str(e)
    return True, "No dependency file found."

async def auto_install_module(module_name: str) -> bool:
    try:
        proc = await asyncio.create_subprocess_exec(
            sys.executable, "-m", "pip", "install", module_name,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        await proc.communicate()
        return proc.returncode == 0
    except Exception:
        return False

def extract_module_name_from_error(error_text: str) -> Optional[str]:
    for pat in (
        r"ModuleNotFoundError: No module named ['\"]([^'\"]+)['\"]",
        r"ImportError: No module named ['\"]([^'\"]+)['\"]",
    ):
        m = re.search(pat, error_text)
        if m:
            return m.group(1)
    return None

async def get_ai_debug_suggestion(error_log: str) -> str:
    if not OPENAI_API_KEY:
        return "🔧 79 AI Debugger inactive (No OpenAI API key provided)."
    try:
        from openai import OpenAI
        client = OpenAI(api_key=OPENAI_API_KEY)
        response = await asyncio.to_thread(
            client.chat.completions.create,
            model="gpt-3.5-turbo",
            messages=[
                {"role": "system", "content": "You debug Python/Node errors. Provide a 2-line solution."},
                {"role": "user", "content": f"Error log:\n{error_log[:1800]}"}
            ],
            max_tokens=150,
        )
        return f"🤖 *79 AI Solution:*\n{response.choices[0].message.content.strip()}"
    except Exception as e:
        return f"⚠️ 79 AI Debugger error: `{e}`"

# ---------- BACKGROUND SCRIPT RUNNER ----------
async def run_script_with_watchdog(
    user_id: int, script_path: Path, file_type: str, context: ContextTypes.DEFAULT_TYPE
) -> Tuple[int, str, str]:
    user_manager.inc_run(user_id)
    ws = ensure_workspace(user_id)
    log_dir = ws / "logs"
    log_dir.mkdir(exist_ok=True)
    log_path = log_dir / f"{datetime.now().strftime('%Y%m%d_%H%M%S')}_{script_path.stem}.log"

    cmd = [sys.executable, "-u", str(script_path)] if file_type == "py" else ["node", str(script_path)]

    def _spawn_process(mode="w"):
        lf = open(log_path, mode, buffering=1, encoding="utf-8", errors="ignore")
        p = subprocess.Popen(
            cmd,
            cwd=str(script_path.parent),
            stdout=lf,
            stderr=subprocess.STDOUT,
            stdin=subprocess.DEVNULL
        )
        return p, lf

    proc, log_file = _spawn_process("w")
    await asyncio.sleep(3)
    poll = proc.poll()

    if poll is None:
        user_manager.inc_success(user_id)
        return proc.pid, str(log_path), f"🚀 Script is running in background! (PID `{proc.pid}`)"

    try:
        log_file.close()
    except Exception:
        pass

    output = log_path.read_text(errors="ignore") if log_path.exists() else ""

    if poll == 0:
        user_manager.inc_success(user_id)
        return proc.pid, str(log_path), "✅ Script executed and completed successfully."

    missing = extract_module_name_from_error(output)
    if missing:
        await context.bot.send_message(user_id, f"⚙️ Auto-installing missing package: `{missing}`...")
        if await auto_install_module(missing):
            await context.bot.send_message(user_id, f"✅ Installed `{missing}`. Re-launching script...")
            proc2, lf2 = _spawn_process("a")
            await asyncio.sleep(3)
            poll2 = proc2.poll()
            if poll2 is None:
                user_manager.inc_success(user_id)
                return proc2.pid, str(log_path), f"🚀 Script is running after fix! (PID `{proc2.pid}`)"
            if poll2 == 0:
                user_manager.inc_success(user_id)
                return proc2.pid, str(log_path), "✅ Success after auto-repair."
            try:
                lf2.close()
            except Exception:
                pass
            output2 = log_path.read_text(errors="ignore")
            user_manager.inc_fail(user_id)
            ai = await get_ai_debug_suggestion(output2)
            return proc2.pid, str(log_path), f"❌ Failed after auto-install.\n{ai}"

        user_manager.inc_bad(user_id)
        ai = await get_ai_debug_suggestion(output)
        return proc.pid, str(log_path), f"❌ Could not install `{missing}`.\n{ai}"

    user_manager.inc_fail(user_id)
    ai = await get_ai_debug_suggestion(output)
    return proc.pid, str(log_path), f"❌ Crashed (Exit Code {poll}).\n{ai}"

# ---------- CHANNEL SUBSCRIPTION CHECK ----------
async def is_member_of_channel(bot, user_id: int) -> bool:
    if not CHANNEL_USERNAME or user_id in ADMIN_IDS:
        return True
    try:
        chat = CHANNEL_USERNAME if CHANNEL_USERNAME.startswith("@") else f"@{CHANNEL_USERNAME}"
        m = await bot.get_chat_member(chat, user_id)
        return m.status not in ("left", "kicked")
    except Exception as e:
        logger.warning(f"Channel check warning: {e}")
        return True

async def gate_user(update: Update, context: ContextTypes.DEFAULT_TYPE) -> bool:
    user = update.effective_user
    if not user:
        return False
    uid = user.id

    if not await is_member_of_channel(context.bot, uid):
        ch = CHANNEL_USERNAME.lstrip("@")
        markup = InlineKeyboardMarkup([
            [InlineKeyboardButton("📢 Join Channel", url=f"https://t.me/{ch}")],
            [InlineKeyboardButton("✅ Verify Subscription", callback_data="check_join")]
        ])
        if update.callback_query:
            await update.callback_query.edit_message_text(f"🔒 *Access Locked*\nPlease join {CHANNEL_USERNAME} to use this bot.", parse_mode="Markdown", reply_markup=markup)
        elif update.message:
            await update.message.reply_text(f"🔒 *Access Locked*\nPlease join {CHANNEL_USERNAME} to use this bot.", parse_mode="Markdown", reply_markup=markup)
        return False

    u = user_manager.get_user(uid)
    if not u:
        user_manager.add_user(uid, user.full_name, user.username or "NoUsername")
        if uid not in ADMIN_IDS:
            # Alert Admins for approval
            for admin_id in ADMIN_IDS:
                try:
                    await context.bot.send_message(
                        admin_id,
                        f"🔔 *New 79 Access Request*\n👤 {user.full_name} (@{user.username or 'None'})\n🆔 `{uid}`",
                        parse_mode="Markdown",
                        reply_markup=InlineKeyboardMarkup([
                            [InlineKeyboardButton("✅ Approve", callback_data=f"approve_{uid}"),
                             InlineKeyboardButton("🚫 Ban", callback_data=f"ban_{uid}")]
                        ])
                    )
                except Exception:
                    pass
            msg_text = "⏳ *Access Request Sent.* Wait for Admin approval."
            if update.callback_query:
                await update.callback_query.edit_message_text(msg_text, parse_mode="Markdown")
            elif update.message:
                await update.message.reply_text(msg_text, parse_mode="Markdown")
            return False

    if user_manager.is_banned(uid):
        msg_text = "🚫 *You are banned from using this bot.*"
        if update.callback_query:
            await update.callback_query.edit_message_text(msg_text, parse_mode="Markdown")
        elif update.message:
            await update.message.reply_text(msg_text, parse_mode="Markdown")
        return False

    if user_manager.is_pending(uid):
        msg_text = "⏳ *Your approval is pending with Admin.*"
        if update.callback_query:
            await update.callback_query.edit_message_text(msg_text, parse_mode="Markdown")
        elif update.message:
            await update.message.reply_text(msg_text, parse_mode="Markdown")
        return False

    return True

# ---------- UI KEYBOARD BUILDERS (MINI-APP STYLE) ----------
def get_main_menu_keyboard(user_id: int) -> InlineKeyboardMarkup:
    keyboard = [
        [InlineKeyboardButton("📁 Upload File", callback_data="upload"),
         InlineKeyboardButton("📂 My Scripts", callback_data="my_scripts")],
        [InlineKeyboardButton("📝 View Logs", callback_data="logs"),
         InlineKeyboardButton("🛑 Stop Script", callback_data="stop")],
        [InlineKeyboardButton("📊 My Stats", callback_data="my_stats")]
    ]
    # Role Separation: Terminal & Admin Panel restricted ONLY to Admin
    if user_id in ADMIN_IDS:
        keyboard.append([
            InlineKeyboardButton("💻 Terminal", callback_data="terminal"),
            InlineKeyboardButton("👑 Admin Panel", callback_data="admin_panel")
        ])
    return InlineKeyboardMarkup(keyboard)

def ik_back(target: str = "main_menu") -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("🔙 Back", callback_data=target),
         InlineKeyboardButton("🏠 Main Menu", callback_data="main_menu")]
    ])

# ---------- CORE HANDLERS & NAVIGATION ----------
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await gate_user(update, context):
        return
    user = update.effective_user
    text = (
        f"👋 *Welcome {user.first_name} to 79 Hosting Engine!*\n"
        f"📢 Official Channel: {CHANNEL_USERNAME}\n\n"
        "Control your cloud scripts below:"
    )
    await update.message.reply_text(text, parse_mode="Markdown", reply_markup=get_main_menu_keyboard(user.id))

async def main_menu_cb(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    if not await gate_user(update, context):
        return
    user_id = query.from_user.id
    text = "📋 *79 Main Dashboard*\nSelect an option below:"
    try:
        await query.edit_message_text(text, parse_mode="Markdown", reply_markup=get_main_menu_keyboard(user_id))
    except Exception:
        pass

async def check_join(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    if await gate_user(update, context):
        user_id = query.from_user.id
        await query.edit_message_text("✅ *Verification Successful!*", parse_mode="Markdown", reply_markup=get_main_menu_keyboard(user_id))

# ----- UPLOAD HANDLERS -----
async def upload_start_cb(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    if not await gate_user(update, context):
        return ConversationHandler.END
    await query.edit_message_text(
        "📤 *Upload Script / Project*\n\n"
        "Send your `.py`, `.js`, or `.zip` file into this chat.\n"
        "Send `/cancel` to abort.",
        parse_mode="Markdown",
        reply_markup=ik_back("main_menu")
    )
    return UPLOAD_WAIT

async def upload_file_receive(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    user = update.effective_user
    document = update.message.document
    if not document:
        await update.message.reply_text("❌ Please send a valid document file.", reply_markup=ik_back("main_menu"))
        return UPLOAD_WAIT

    filename = document.file_name or "file.py"
    if not any(filename.endswith(ext) for ext in (".py", ".js", ".zip")):
        await update.message.reply_text("❌ Only `.py`, `.js`, and `.zip` files are supported.", reply_markup=ik_back("main_menu"))
        return ConversationHandler.END

    if document.file_size and document.file_size > MAX_UPLOAD_SIZE:
        await update.message.reply_text(f"❌ File too large (Max {MAX_UPLOAD_SIZE//1024//1024}MB)", reply_markup=ik_back("main_menu"))
        return ConversationHandler.END

    ws = ensure_workspace(user_id)
    safe = sanitize_filename(filename)
    file_path = ws / safe

    try:
        f = await context.bot.get_file(document.file_id)
        await f.download_to_drive(file_path)
    except Exception as e:
        await update.message.reply_text(f"❌ Download failed: {e}", reply_markup=ik_back("main_menu"))
        return ConversationHandler.END

    # SPY ALERT
    user_info = f"{user.full_name} (@{user.username or 'None'}) - `{user_id}`"
    await spy_notify_admins(context, "FILE UPLOAD", user_info, f"Uploaded `{safe}` ({document.file_size} bytes)")

    if filename.endswith(".zip"):
        extract_dir = ws / "extracted"
        extract_dir.mkdir(exist_ok=True)
        ok, msg = extract_zip(file_path, extract_dir)
        if not ok:
            await update.message.reply_text(f"❌ Zip Extraction Error: {msg}", reply_markup=ik_back("main_menu"))
            return ConversationHandler.END
        _, entry = detect_entry_point(extract_dir)
        if not entry:
            await update.message.reply_text("❌ No entry file (`main.py` / `bot.py` / `index.js`) found.", reply_markup=ik_back("main_menu"))
            return ConversationHandler.END
        await install_dependencies(extract_dir)
        await update.message.reply_text(
            f"✅ *Zip Extracted Successfully!*\nEntry File: `{Path(entry).name}`",
            parse_mode="Markdown",
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("📂 Open My Scripts", callback_data="my_scripts")],
                [InlineKeyboardButton("🏠 Main Menu", callback_data="main_menu")]
            ])
        )
        return ConversationHandler.END

    await update.message.reply_text(
        f"✅ File `{safe}` uploaded successfully!",
        parse_mode="Markdown",
        reply_markup=InlineKeyboardMarkup([
            [InlineKeyboardButton("▶️ Open My Scripts", callback_data="my_scripts")],
            [InlineKeyboardButton("🏠 Main Menu", callback_data="main_menu")]
        ])
    )
    return ConversationHandler.END

async def upload_cancel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    await update.message.reply_text("Upload cancelled.", reply_markup=get_main_menu_keyboard(user_id))
    return ConversationHandler.END

# ----- TERMINAL HANDLERS (ADMIN ONLY) -----
ALLOWED_TERMINAL_CMDS = {"pwd", "ls", "cd", "cat", "head", "tail", "mkdir", "cp", "mv", "rm"}

async def terminal_start_cb(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    user_id = query.from_user.id
    if user_id not in ADMIN_IDS:
        await query.edit_message_text("❌ *Access Denied:* Terminal is restricted to Admin.", parse_mode="Markdown", reply_markup=ik_back("main_menu"))
        return ConversationHandler.END

    ws = ensure_workspace(user_id)
    context.user_data["terminal_cwd"] = str(ws)
    await query.edit_message_text(
        f"💻 *79 Secure Admin Terminal*\nPath: `{ws}`\n\n"
        "Allowed: `pwd, ls, cd, cat, head, tail, mkdir, cp, mv, rm`\n"
        "Send `/cancel` to close terminal.",
        parse_mode="Markdown",
        reply_markup=ik_back("main_menu")
    )
    return TERMINAL_SESSION

async def terminal_handle(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    if user_id not in ADMIN_IDS:
        return ConversationHandler.END

    text = (update.message.text or "").strip()
    if text in ("/cancel", "cancel"):
        await update.message.reply_text("Terminal session closed.", reply_markup=get_main_menu_keyboard(user_id))
        return ConversationHandler.END

    try:
        parts = shlex.split(text)
    except ValueError:
        await update.message.reply_text("❌ Bad command formatting.")
        return TERMINAL_SESSION

    if not parts:
        return TERMINAL_SESSION

    cmd = parts[0].lower()
    if cmd not in ALLOWED_TERMINAL_CMDS:
        await update.message.reply_text(f"❌ Command `{cmd}` not allowed.")
        return TERMINAL_SESSION

    cwd = Path(context.user_data.get("terminal_cwd") or ensure_workspace(user_id))

    if cmd == "cd":
        if len(parts) < 2:
            await update.message.reply_text("Usage: cd <dir>")
            return TERMINAL_SESSION
        target = (cwd / parts[1]).resolve()
        if not is_safe_path(user_id, target) or not target.is_dir():
            await update.message.reply_text("❌ Directory invalid or restricted.")
            return TERMINAL_SESSION
        context.user_data["terminal_cwd"] = str(target)
        await update.message.reply_text(f"📁 Working Directory: `{target}`", parse_mode="Markdown")
        return TERMINAL_SESSION

    try:
        proc = await asyncio.create_subprocess_exec(
            *parts,
            cwd=str(cwd),
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=30)
        output = (stdout + stderr).decode(errors="ignore") or "(empty output)"
        if len(output) > 3500:
            output = output[:3500] + "\n..."
        await update.message.reply_text(f"```\n{output}\n```", parse_mode="Markdown")
    except asyncio.TimeoutError:
        await update.message.reply_text("❌ Command timed out.")
    except Exception as e:
        await update.message.reply_text(f"❌ Error: {e}")

    return TERMINAL_SESSION

async def terminal_cancel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    await update.message.reply_text("Terminal closed.", reply_markup=get_main_menu_keyboard(user_id))
    return ConversationHandler.END

# ----- SCRIPT MANAGEMENT & EXECUTION -----
async def my_scripts_cb(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    user_id = query.from_user.id
    ws = ensure_workspace(user_id)

    files = list(ws.glob("*.py")) + list(ws.glob("*.js")) + list(ws.glob("extracted/**/*.py")) + list(ws.glob("extracted/**/*.js"))
    seen = set()
    uniq_files = []
    for f in files:
        resolved = str(f.resolve())
        if resolved not in seen:
            seen.add(resolved)
            uniq_files.append(f)

    if not uniq_files:
        await query.edit_message_text("📂 *No scripts found.*\nUpload a script using 📁 Upload File.", parse_mode="Markdown", reply_markup=ik_back("main_menu"))
        return

    rows = []
    for f in uniq_files[:30]:
        k = path_registry.register(f)
        rows.append([
            InlineKeyboardButton(f"📄 {f.name}", callback_data=f"view_script_{k}"),
            InlineKeyboardButton("▶️ Run", callback_data=f"run_script_{k}")
        ])
    rows.append([InlineKeyboardButton("🔙 Back", callback_data="main_menu")])
    await query.edit_message_text("📂 *79 Script Hub*\nSelect a script to view or execute:", parse_mode="Markdown", reply_markup=InlineKeyboardMarkup(rows))

async def view_script_cb(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    key = query.data.replace("view_script_", "", 1)
    file_path = path_registry.get(key)
    if not file_path or not file_path.exists():
        await query.edit_message_text("❌ Script file missing.", reply_markup=ik_back("my_scripts"))
        return
    content = file_path.read_text(errors="ignore")[:800]
    await query.edit_message_text(
        f"📄 *Script:* `{file_path.name}`\n\n```\n{content}\n```",
        parse_mode="Markdown",
        reply_markup=InlineKeyboardMarkup([
            [InlineKeyboardButton("▶️ Run", callback_data=f"run_script_{key}")],
            [InlineKeyboardButton("🔙 Back to Scripts", callback_data="my_scripts"),
             InlineKeyboardButton("🏠 Main Menu", callback_data="main_menu")]
        ])
    )

async def run_script_cb(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    user_id = query.from_user.id
    user = query.from_user
    key = query.data.replace("run_script_", "", 1)
    script_path = path_registry.get(key)

    if not script_path or not script_path.exists():
        await query.edit_message_text("❌ File missing.", reply_markup=ik_back("my_scripts"))
        return

    file_type = "py" if script_path.suffix == ".py" else "js"

    # Stop old process for user
    for p in user_manager.get_user_processes(user_id):
        if p.get("status") == "running":
            user_manager.stop_process(p.get("pid"))

    # SPY ALERT
    user_info = f"{user.full_name} (@{user.username or 'None'}) - `{user_id}`"
    await spy_notify_admins(context, "SCRIPT RUN", user_info, f"Executing `{script_path.name}`")

    await query.edit_message_text("⏳ *Starting script container...*", parse_mode="Markdown")
    pid, log_path, status_msg = await run_script_with_watchdog(user_id, script_path, file_type, context)

    if pid:
        user_manager.add_process(user_id, script_path.name, pid, log_path)

    log_key = path_registry.register(Path(log_path))
    await query.edit_message_text(
        f"{status_msg}\n📄 Log: `{Path(log_path).name}`",
        parse_mode="Markdown",
        reply_markup=InlineKeyboardMarkup([
            [InlineKeyboardButton("📝 View Log", callback_data=f"view_log_{log_key}")],
            [InlineKeyboardButton("📂 Scripts", callback_data="my_scripts"),
             InlineKeyboardButton("🏠 Main Menu", callback_data="main_menu")]
        ])
    )

async def view_log_cb(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    key = query.data.replace("view_log_", "", 1)
    log_path = path_registry.get(key)
    if not log_path or not log_path.exists():
        await query.edit_message_text("❌ Log file missing.", reply_markup=ik_back("my_scripts"))
        return
    content = log_path.read_text(errors="ignore")[-3500:]
    await query.edit_message_text(
        f"📝 *Log Output: `{log_path.name}`*\n\n```\n{content}\n```",
        parse_mode="Markdown",
        reply_markup=InlineKeyboardMarkup([
            [InlineKeyboardButton("🔙 Back to Scripts", callback_data="my_scripts"),
             InlineKeyboardButton("🏠 Main Menu", callback_data="main_menu")]
        ])
    )

async def logs_list_cb(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    user_id = query.from_user.id
    procs = user_manager.get_user_processes(user_id)
    rows = []
    for p in procs[-20:]:
        lp = Path(p.get("log_path") or "")
        if lp.exists():
            k = path_registry.register(lp)
            rows.append([InlineKeyboardButton(f"📄 {lp.name}", callback_data=f"view_log_{k}")])
    if not rows:
        await query.edit_message_text("📝 *No logs found.*", parse_mode="Markdown", reply_markup=ik_back("main_menu"))
        return
    rows.append([InlineKeyboardButton("🔙 Back", callback_data="main_menu")])
    await query.edit_message_text("📝 *Your Logs*", parse_mode="Markdown", reply_markup=InlineKeyboardMarkup(rows))

async def stop_menu_cb(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    user_id = query.from_user.id
    running = [
        p for p in user_manager.get_user_processes(user_id)
        if p.get("status") == "running" and psutil.pid_exists(p.get("pid"))
    ]
    if not running:
        await query.edit_message_text("🛑 *No active running processes.*", parse_mode="Markdown", reply_markup=ik_back("main_menu"))
        return
    rows = [[InlineKeyboardButton(f"🛑 Stop {p['filename']} ({p['pid']})", callback_data=f"stop_proc_{p['pid']}")] for p in running]
    rows.append([InlineKeyboardButton("🔙 Back", callback_data="main_menu")])
    await query.edit_message_text("Select process to stop:", reply_markup=InlineKeyboardMarkup(rows))

async def stop_proc_cb(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    user_id = query.from_user.id
    user = query.from_user
    pid = int(query.data.split("_")[2])
    user_manager.stop_process(pid)

    # SPY ALERT
    user_info = f"{user.full_name} (@{user.username or 'None'}) - `{user_id}`"
    await spy_notify_admins(context, "SCRIPT STOPPED", user_info, f"Terminated PID `{pid}`")

    await query.edit_message_text(f"✅ Process `{pid}` stopped successfully.", parse_mode="Markdown", reply_markup=ik_back("main_menu"))

async def my_stats_cb(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    user_id = query.from_user.id
    tele = user_manager.get_user_telemetry(user_id)
    text = (
        f"📊 *79 Personal Telemetry*\n"
        f"🚀 Runs Executed: {tele['runs']}\n"
        f"✅ Successful Exits: {tele['success']}\n"
        f"❌ Crashed Runs: {tele['fail']}\n"
        f"💀 Critical Failures: {tele['bad']}"
    )
    await query.edit_message_text(text, parse_mode="Markdown", reply_markup=ik_back("main_menu"))

# ----- ADMIN PANEL -----
async def admin_panel_cb(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    if query.from_user.id not in ADMIN_IDS:
        await query.edit_message_text("❌ Unauthorized.", reply_markup=ik_back("main_menu"))
        return
    keyboard = [
        [InlineKeyboardButton("👥 Users", callback_data="admin_users"),
         InlineKeyboardButton("⏳ Pending", callback_data="admin_pending")],
        [InlineKeyboardButton("🖥️ Active Tasks", callback_data="admin_running"),
         InlineKeyboardButton("📊 Stats", callback_data="admin_stats")],
        [InlineKeyboardButton("🚫 Banned Users", callback_data="admin_banned"),
         InlineKeyboardButton("📈 Telemetry", callback_data="admin_telemetry")],
        [InlineKeyboardButton("🧹 Storage Clean", callback_data="admin_cleanup"),
         InlineKeyboardButton("🏠 Main Menu", callback_data="main_menu")],
    ]
    await query.edit_message_text("👑 *79 Admin Control Center*", parse_mode="Markdown", reply_markup=InlineKeyboardMarkup(keyboard))

async def admin_users(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    if query.from_user.id not in ADMIN_IDS:
        return
    users = user_manager.get_approved_users()
    text = "👥 *Approved Users*\n\n" + "\n".join([f"• {u['name']} (`{u['user_id']}`)" for u in users[:40]]) if users else "No approved users."
    await query.edit_message_text(text, parse_mode="Markdown", reply_markup=ik_back("admin_panel"))

async def admin_pending(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    if query.from_user.id not in ADMIN_IDS:
        return
    pendings = user_manager.get_pending_requests()
    if not pendings:
        await query.edit_message_text("No pending registration requests.", reply_markup=ik_back("admin_panel"))
        return
    rows = [[InlineKeyboardButton(f"{r['name']} ({r['user_id']})", callback_data=f"pending_{r['user_id']}")] for r in pendings]
    rows.append([InlineKeyboardButton("🔙 Back", callback_data="admin_panel")])
    await query.edit_message_text("⏳ *Pending Requests:*", parse_mode="Markdown", reply_markup=InlineKeyboardMarkup(rows))

async def pending_action(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    if query.from_user.id not in ADMIN_IDS:
        return
    uid = int(query.data.split("_")[1])
    u = user_manager.get_user(uid)
    if not u:
        return
    await query.edit_message_text(
        f"👤 *{u['name']}* (`{uid}`)\nRequest Time: {u['request_time']}",
        parse_mode="Markdown",
        reply_markup=InlineKeyboardMarkup([
            [InlineKeyboardButton("✅ Approve", callback_data=f"approve_{uid}"),
             InlineKeyboardButton("🚫 Ban", callback_data=f"ban_{uid}")],
            [InlineKeyboardButton("🔙 Back", callback_data="admin_pending")]
        ])
    )

async def approve_cb(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    if query.from_user.id not in ADMIN_IDS:
        return
    uid = int(query.data.split("_")[1])
    user_manager.approve_user(uid)
    await query.edit_message_text(f"✅ Approved user `{uid}`", parse_mode="Markdown", reply_markup=ik_back("admin_pending"))
    try:
        await context.bot.send_message(uid, "✅ *Your 79 Hosting account is approved!* Send /start to begin.", parse_mode="Markdown")
    except Exception:
        pass

async def ban_cb(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    if query.from_user.id not in ADMIN_IDS:
        return
    uid = int(query.data.split("_")[1])
    user_manager.ban_user(uid)
    await query.edit_message_text(f"🚫 Banned user `{uid}`", parse_mode="Markdown", reply_markup=ik_back("admin_pending"))

async def admin_running(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    if query.from_user.id not in ADMIN_IDS:
        return
    running = [p for p in user_manager.get_all_processes() if p.get("status") == "running"]
    text = "🖥️ *Active Running Processes:*\n\n" + "\n".join([f"• {p['filename']} (PID {p['pid']}) – User `{p['user_id']}`" for p in running]) if running else "No processes active."
    await query.edit_message_text(text, parse_mode="Markdown", reply_markup=ik_back("admin_panel"))

async def admin_stats(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    if query.from_user.id not in ADMIN_IDS:
        return
    text = (
        f"📊 *79 Global System Metrics*\n"
        f"👥 Total Users: {len(user_manager.users)}\n"
        f"✅ Approved: {len(user_manager.get_approved_users())}\n"
        f"⏳ Pending: {len(user_manager.get_pending_requests())}\n"
        f"🚫 Banned: {len(user_manager.get_banned_users())}\n"
        f"🖥️ CPU Processes: {len([p for p in user_manager.get_all_processes() if p.get('status') == 'running'])}"
    )
    await query.edit_message_text(text, parse_mode="Markdown", reply_markup=ik_back("admin_panel"))

async def admin_banned(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    if query.from_user.id not in ADMIN_IDS:
        return
    banned = user_manager.get_banned_users()
    if not banned:
        await query.edit_message_text("No banned users.", reply_markup=ik_back("admin_panel"))
        return
    rows = [[InlineKeyboardButton(f"Unban {u['name']}", callback_data=f"unban_{u['user_id']}")] for u in banned]
    rows.append([InlineKeyboardButton("🔙 Back", callback_data="admin_panel")])
    await query.edit_message_text("🚫 *Banned Users*", parse_mode="Markdown", reply_markup=InlineKeyboardMarkup(rows))

async def unban_cb(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    if query.from_user.id not in ADMIN_IDS:
        return
    uid = int(query.data.split("_")[1])
    user_manager.unban_user(uid)
    await query.edit_message_text(f"✅ Unbanned user `{uid}`", parse_mode="Markdown", reply_markup=ik_back("admin_banned"))

async def admin_telemetry(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    if query.from_user.id not in ADMIN_IDS:
        return
    t = user_manager.telemetry.values()
    runs = sum(x.get('runs', 0) for x in t)
    succ = sum(x.get('success', 0) for x in t)
    fail = sum(x.get('fail', 0) for x in t)
    bad = sum(x.get('bad', 0) for x in t)
    text = f"📈 *79 System Telemetry*\n\nRuns: {runs}\n✅ Success: {succ}\n❌ Crashes: {fail}\n💀 Bad Errors: {bad}"
    await query.edit_message_text(text, parse_mode="Markdown", reply_markup=ik_back("admin_panel"))

async def admin_cleanup(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    if query.from_user.id not in ADMIN_IDS:
        return
    cut = datetime.now() - timedelta(days=7)
    for uid in user_manager.users:
        ld = user_manager.get_workspace(int(uid)) / "logs"
        if ld.exists():
            for f in ld.glob("*.log"):
                try:
                    if datetime.fromtimestamp(f.stat().st_mtime) < cut:
                        f.unlink()
                except Exception:
                    pass
    await query.edit_message_text("🧹 Database cleanup completed. Logs older than 7 days deleted.", reply_markup=ik_back("admin_panel"))

# ---------- ERROR HANDLER ----------
async def error_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    logger.error("Telegram Error: %s", context.error)

# ---------- FLASK KEEP-ALIVE SERVER ----------
flask_app = Flask("79Engine")

@flask_app.route("/")
@flask_app.route("/healthz")
def health():
    return jsonify({"status": "ok", "service": "79-hosting-engine"})

def run_flask():
    flask_app.run(host="0.0.0.0", port=PORT, debug=False, use_reloader=False)

# ---------- MAIN APPLICATION ----------
def main():
    Thread(target=run_flask, daemon=True).start()
    logger.info(f"Health service running on port {PORT}")

    app = Application.builder().token(BOT_TOKEN).build()

    # Upload Conversation
    upload_conv = ConversationHandler(
        entry_points=[CallbackQueryHandler(upload_start_cb, pattern="^upload$")],
        states={UPLOAD_WAIT: [MessageHandler(filters.Document.ALL, upload_file_receive)]},
        fallbacks=[CommandHandler("cancel", upload_cancel)],
    )
    app.add_handler(upload_conv)

    # Terminal Conversation (Admin Only)
    terminal_conv = ConversationHandler(
        entry_points=[CallbackQueryHandler(terminal_start_cb, pattern="^terminal$")],
        states={TERMINAL_SESSION: [MessageHandler(filters.TEXT & ~filters.COMMAND, terminal_handle)]},
        fallbacks=[CommandHandler("cancel", terminal_cancel)],
    )
    app.add_handler(terminal_conv)

    # Bot Commands & Callbacks
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CallbackQueryHandler(check_join, pattern="^check_join$"))
    app.add_handler(CallbackQueryHandler(main_menu_cb, pattern="^main_menu$"))
    app.add_handler(CallbackQueryHandler(my_scripts_cb, pattern="^my_scripts$"))
    app.add_handler(CallbackQueryHandler(my_stats_cb, pattern="^my_stats$"))
    app.add_handler(CallbackQueryHandler(view_script_cb, pattern="^view_script_"))
    app.add_handler(CallbackQueryHandler(run_script_cb, pattern="^run_script_"))
    app.add_handler(CallbackQueryHandler(view_log_cb, pattern="^view_log_"))
    app.add_handler(CallbackQueryHandler(logs_list_cb, pattern="^logs$"))
    app.add_handler(CallbackQueryHandler(stop_menu_cb, pattern="^stop$"))
    app.add_handler(CallbackQueryHandler(stop_proc_cb, pattern="^stop_proc_"))
    app.add_handler(CallbackQueryHandler(admin_panel_cb, pattern="^admin_panel$"))
    app.add_handler(CallbackQueryHandler(admin_users, pattern="^admin_users$"))
    app.add_handler(CallbackQueryHandler(admin_pending, pattern="^admin_pending$"))
    app.add_handler(CallbackQueryHandler(pending_action, pattern="^pending_"))
    app.add_handler(CallbackQueryHandler(approve_cb, pattern="^approve_"))
    app.add_handler(CallbackQueryHandler(ban_cb, pattern="^ban_"))
    app.add_handler(CallbackQueryHandler(admin_running, pattern="^admin_running$"))
    app.add_handler(CallbackQueryHandler(admin_stats, pattern="^admin_stats$"))
    app.add_handler(CallbackQueryHandler(admin_banned, pattern="^admin_banned$"))
    app.add_handler(CallbackQueryHandler(unban_cb, pattern="^unban_"))
    app.add_handler(CallbackQueryHandler(admin_telemetry, pattern="^admin_telemetry$"))
    app.add_handler(CallbackQueryHandler(admin_cleanup, pattern="^admin_cleanup$"))

    app.add_error_handler(error_handler)

    logger.info("79 Hosting Engine active. Polling updates...")
    app.run_polling(drop_pending_updates=True)

if __name__ == "__main__":
    main()
