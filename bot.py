#!/usr/bin/env python3
"""
🔥 ULTIMATE SCRIPT HOSTING BOT – OP, NON-STUCK, AUTO-REPAIR
Supports multiple admins (ADMIN_IDS comma-separated).
"""

import asyncio
import atexit
import json
import os
import re
import shlex
import shutil
import signal
import subprocess
import sys
import tempfile
import time
import traceback
import zipfile
from datetime import datetime, timedelta
from pathlib import Path
from threading import Thread
from typing import Dict, List, Optional, Tuple, Any

import psutil
from dotenv import load_dotenv
from flask import Flask, jsonify
from telegram import (
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    Update,
    constants,
)
from telegram.ext import (
    Application,
    CallbackContext,
    CallbackQueryHandler,
    CommandHandler,
    ConversationHandler,
    MessageHandler,
    filters,
    ContextTypes,
)

# ---------- ENV ----------
load_dotenv()

BOT_TOKEN = os.getenv("BOT_TOKEN", "8872210815:AAGB1oqKN-z5QaTgOnMMZj8L6_VVRKZLgoQ").strip()
ADMIN_IDS_STR = os.getenv("ADMIN_IDS", "7618187004,8846085944")  
ADMIN_IDS = [int(x.strip()) for x in ADMIN_IDS_STR.split(",") if x.strip().isdigit()]
if not ADMIN_IDS:
    old_admin = os.getenv("ADMIN_ID")
    if old_admin and old_admin.isdigit():
        ADMIN_IDS = [int(old_admin)]
    else:
        raise ValueError("ADMIN_IDS or ADMIN_ID must be set")

# FIX: Remove any hidden quotes from channel username
CHANNEL_USERNAME = os.getenv("CHANNEL_USERNAME", "@seventyx79").strip().replace('"', '').replace("'", "")
PORT = int(os.getenv("PORT", "8080"))
WORKSPACE_BASE = Path(os.getenv("WORKSPACE_BASE", "./workspaces"))
MAX_UPLOAD_SIZE = int(os.getenv("MAX_UPLOAD_SIZE", "10MB").replace("MB", "")) * 1024 * 1024
MAX_ARCHIVE_SIZE = int(os.getenv("MAX_ARCHIVE_SIZE", "50MB").replace("MB", "")) * 1024 * 1024
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY", "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").strip()
SCRIPT_TIMEOUT = int(os.getenv("SCRIPT_TIMEOUT", "300"))

if not BOT_TOKEN:
    raise ValueError("BOT_TOKEN must be set")

WORKSPACE_BASE.mkdir(parents=True, exist_ok=True)

# ---------- PERSISTENCE ----------
DATA_FILE = "bot_data.json"

def load_data():
    if Path(DATA_FILE).exists():
        with open(DATA_FILE, "r") as f:
            return json.load(f)
    return {"users": {}, "processes": [], "telemetry": {}}

def save_data(data):
    with open(DATA_FILE, "w") as f:
        json.dump(data, f, indent=2, default=str)

# ---------- USER & TELEMETRY MANAGER ----------
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

    def get_user(self, user_id: int) -> dict:
        return self.users.get(str(user_id), None)

    def add_user(self, user_id: int, name: str, username: str):
        self.users[str(user_id)] = {
            "status": "pending",
            "name": name,
            "username": username,
            "request_time": datetime.now().isoformat(),
            "workspace": str(WORKSPACE_BASE / str(user_id)),
        }
        self.telemetry[str(user_id)] = {"runs": 0, "success": 0, "fail": 0, "bad": 0}
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
        u = self.get_user(user_id)
        return u and u.get("status") == "approved"

    def is_pending(self, user_id: int) -> bool:
        u = self.get_user(user_id)
        return u and u.get("status") == "pending"

    def is_banned(self, user_id: int) -> bool:
        u = self.get_user(user_id)
        return u and u.get("status") == "banned"

    def get_workspace(self, user_id: int) -> Path:
        u = self.get_user(user_id)
        if u:
            return Path(u["workspace"])
        return WORKSPACE_BASE / str(user_id)

    def get_pending_requests(self) -> List[dict]:
        return [{"user_id": k, **v} for k, v in self.users.items() if v["status"] == "pending"]

    def get_approved_users(self) -> List[dict]:
        return [{"user_id": k, **v} for k, v in self.users.items() if v["status"] == "approved"]

    def get_banned_users(self) -> List[dict]:
        return [{"user_id": k, **v} for k, v in self.users.items() if v["status"] == "banned"]

    def add_process(self, user_id: int, filename: str, pid: int, log_path: str):
        proc = {
            "user_id": user_id,
            "filename": filename,
            "pid": pid,
            "start_time": datetime.now().isoformat(),
            "status": "running",
            "log_path": log_path,
        }
        self.processes.append(proc)
        self.save()
        return proc

    def get_user_processes(self, user_id: int) -> List[dict]:
        return [p for p in self.processes if p["user_id"] == user_id]

    def get_all_processes(self) -> List[dict]:
        return self.processes

    def stop_process(self, pid: int) -> bool:
        for p in self.processes:
            if p["pid"] == pid:
                try:
                    os.kill(pid, signal.SIGTERM)
                except:
                    pass
                p["status"] = "stopped"
                self.save()
                return True
        return False

    def remove_terminated(self):
        to_remove = []
        for i, p in enumerate(self.processes):
            if not psutil.pid_exists(p["pid"]):
                to_remove.append(i)
        for i in reversed(to_remove):
            self.processes.pop(i)
        if to_remove:
            self.save()

    def cleanup_all(self):
        for p in self.processes:
            try:
                os.kill(p["pid"], signal.SIGTERM)
            except:
                pass
        self.processes.clear()
        self.save()

    def inc_run(self, user_id: int):
        uid = str(user_id)
        if uid not in self.telemetry:
            self.telemetry[uid] = {"runs": 0, "success": 0, "fail": 0, "bad": 0}
        self.telemetry[uid]["runs"] += 1
        self.save()

    def inc_success(self, user_id: int):
        uid = str(user_id)
        if uid not in self.telemetry:
            self.telemetry[uid] = {"runs": 0, "success": 0, "fail": 0, "bad": 0}
        self.telemetry[uid]["success"] += 1
        self.save()

    def inc_fail(self, user_id: int):
        uid = str(user_id)
        if uid not in self.telemetry:
            self.telemetry[uid] = {"runs": 0, "success": 0, "fail": 0, "bad": 0}
        self.telemetry[uid]["fail"] += 1
        self.save()

    def inc_bad(self, user_id: int):
        uid = str(user_id)
        if uid not in self.telemetry:
            self.telemetry[uid] = {"runs": 0, "success": 0, "fail": 0, "bad": 0}
        self.telemetry[uid]["bad"] += 1
        self.save()

    def get_user_telemetry(self, user_id: int) -> dict:
        return self.telemetry.get(str(user_id), {"runs": 0, "success": 0, "fail": 0, "bad": 0})

user_manager = UserManager()
atexit.register(user_manager.cleanup_all)

# ---------- HELPER FUNCTIONS ----------
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

def extract_zip(user_id: int, zip_path: Path, dest_dir: Path) -> Tuple[bool, str]:
    try:
        with zipfile.ZipFile(zip_path, 'r') as zip_ref:
            total_size = sum(info.file_size for info in zip_ref.infolist())
            if total_size > MAX_ARCHIVE_SIZE:
                return False, f"Archive too large (>{MAX_ARCHIVE_SIZE//1024//1024}MB)"
            for member in zip_ref.infolist():
                if member.filename.startswith("/") or ".." in member.filename:
                    return False, "Invalid file path in archive"
                target = dest_dir / member.filename
                try:
                    target.resolve().relative_to(dest_dir.resolve())
                except ValueError:
                    return False, "Path traversal attempt detected"
            zip_ref.extractall(dest_dir)
        return True, "Extraction successful"
    except Exception as e:
        return False, f"Extraction error: {str(e)}"

def detect_entry_point(dest_dir: Path) -> Tuple[Optional[str], Optional[str]]:
    for py in dest_dir.glob("main.py"):
        return ("py", str(py))
    for js in dest_dir.glob("index.js"):
        return ("js", str(js))
    py_files = list(dest_dir.glob("*.py"))
    if py_files:
        return ("py", str(py_files[0]))
    js_files = list(dest_dir.glob("*.js"))
    if js_files:
        return ("js", str(js_files[0]))
    return (None, None)

async def install_dependencies(user_id: int, dest_dir: Path) -> Tuple[bool, str]:
    req_file = dest_dir / "requirements.txt"
    package_file = dest_dir / "package.json"
    output_lines = []
    if req_file.exists():
        cmd = ["pip", "install", "--user", "-r", str(req_file)]
        proc = await asyncio.create_subprocess_exec(
            *cmd, cwd=str(dest_dir), stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.STDOUT,
        )
        stdout, _ = await proc.communicate()
        output_lines.append(f"pip install output:\n{stdout.decode()[:500]}...")
        if proc.returncode != 0: return False, f"Dependency installation failed"
    elif package_file.exists():
        cmd = ["npm", "install", "--production"]
        proc = await asyncio.create_subprocess_exec(
            *cmd, cwd=str(dest_dir), stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.STDOUT,
        )
        stdout, _ = await proc.communicate()
        output_lines.append(f"npm install output:\n{stdout.decode()[:500]}...")
        if proc.returncode != 0: return False, f"npm install failed"
    else:
        output_lines.append("No dependency file found; skipping.")
    return True, "\n".join(output_lines)

async def auto_install_module(module_name: str) -> bool:
    try:
        proc = await asyncio.create_subprocess_exec(
            sys.executable, "-m", "pip", "install", "--user", module_name,
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
        )
        await proc.communicate()
        return proc.returncode == 0
    except:
        return False

def extract_module_name_from_error(error_text: str) -> Optional[str]:
    match = re.search(r"ModuleNotFoundError: No module named ['\"](.+?)['\"]", error_text)
    if match: return match.group(1)
    match = re.search(r"ImportError: No module named ['\"](.+?)['\"]", error_text)
    if match: return match.group(1)
    return None

async def get_ai_debug_suggestion(error_log: str) -> str:
    if not OPENAI_API_KEY: return "🔧 AI Debugger inactive."
    try:
        import openai
        openai.api_key = OPENAI_API_KEY
        response = await asyncio.to_thread(
            openai.ChatCompletion.create,
            model="gpt-3.5-turbo",
            messages=[
                {"role": "system", "content": "You are a helpful Python/Node.js debugging assistant. Given the error log, suggest a fix."},
                {"role": "user", "content": f"Error log:\n{error_log[:2000]}"}
            ],
            max_tokens=200,
        )
        return f"🤖 *AI Suggestion:*\n{response.choices[0].message.content.strip()}"
    except Exception as e:
        return f"⚠️ AI Debugger error: {str(e)}"

async def run_script_with_watchdog(user_id: int, script_path: Path, file_type: str, context: ContextTypes.DEFAULT_TYPE) -> Tuple[int, str, str]:
    user_manager.inc_run(user_id)
    ws = ensure_workspace(user_id)
    log_dir = ws / "logs"
    log_dir.mkdir(exist_ok=True)
    log_path = log_dir / f"{datetime.now().strftime('%Y%m%d_%H%M%S')}_{script_path.stem}.log"

    cmd = [sys.executable, str(script_path)] if file_type == "py" else ["node", str(script_path)]
    proc = await asyncio.create_subprocess_exec(
        *cmd, cwd=str(script_path.parent), stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.STDOUT,
    )
    try:
        stdout_data, _ = await asyncio.wait_for(proc.communicate(), timeout=SCRIPT_TIMEOUT)
        output, returncode = stdout_data.decode(), proc.returncode
    except asyncio.TimeoutError:
        proc.terminate()
        await proc.wait()
        output, returncode = f"⏰ Script exceeded {SCRIPT_TIMEOUT}s timeout.\n", -1

    if returncode != 0:
        missing = extract_module_name_from_error(output)
        if missing:
            await context.bot.send_message(user_id, f"⚙️ Missing `{missing}`. Installing...")
            if await auto_install_module(missing):
                await context.bot.send_message(user_id, f"✅ Installed `{missing}`. Restarting...")
                proc2 = await asyncio.create_subprocess_exec(
                    *cmd, cwd=str(script_path.parent), stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.STDOUT,
                )
                try:
                    stdout2, _ = await asyncio.wait_for(proc2.communicate(), timeout=SCRIPT_TIMEOUT)
                    output2, returncode2 = stdout2.decode(), proc2.returncode
                except asyncio.TimeoutError:
                    proc2.terminate()
                    await proc2.wait()
                    output2, returncode2 = f"⏰ Timeout.\n", -1
                with open(log_path, "w") as f: f.write(f"{output}\n\n[Restart]\n{output2}")
                if returncode2 == 0:
                    user_manager.inc_success(user_id)
                    return (proc2.pid, str(log_path), "✅ Success after auto-install.")
                user_manager.inc_fail(user_id)
                return (proc2.pid, str(log_path), f"❌ Failing after install.\n{await get_ai_debug_suggestion(output2)}")
            user_manager.inc_bad(user_id)
            with open(log_path, "w") as f: f.write(output)
            return (proc.pid, str(log_path), f"❌ Failed to install `{missing}`.\n{await get_ai_debug_suggestion(output)}")
        user_manager.inc_fail(user_id)
        with open(log_path, "w") as f: f.write(output)
        return (proc.pid, str(log_path), f"❌ Script crashed.\n{await get_ai_debug_suggestion(output)}")
    
    user_manager.inc_success(user_id)
    with open(log_path, "w") as f: f.write(output)
    return (proc.pid, str(log_path), "✅ Script executed successfully.")

# ---------- TELEGRAM BOT HANDLERS ----------
(UPLOAD_WAIT, TERMINAL_SESSION) = range(2)

def get_main_keyboard(user_id: int) -> InlineKeyboardMarkup:
    keyboard = [
        [InlineKeyboardButton("💻 Terminal", callback_data="terminal"), InlineKeyboardButton("📁 Upload", callback_data="upload")],
        [InlineKeyboardButton("📂 My Scripts", callback_data="my_scripts"), InlineKeyboardButton("📝 View Logs", callback_data="logs")],
        [InlineKeyboardButton("🛑 Stop Script", callback_data="stop")],
    ]
    if user_id in ADMIN_IDS: keyboard.append([InlineKeyboardButton("👑 Admin Panel", callback_data="admin_panel")])
    return InlineKeyboardMarkup(keyboard)

# ----- START / JOIN -----
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    user_id = user.id
    username = user.username or "No username"
    fullname = user.full_name

    if CHANNEL_USERNAME:
        try:
            member = await context.bot.get_chat_member(CHANNEL_USERNAME, user_id)
            if member.status in (constants.ChatMemberStatus.LEFT, constants.ChatMemberStatus.BANNED):
                await update.message.reply_text(
                    f"🔒 *Join {CHANNEL_USERNAME} to use this bot*",
                    parse_mode="Markdown",
                    reply_markup=InlineKeyboardMarkup([
                        [InlineKeyboardButton("📢 Channel", url=f"https://t.me/{CHANNEL_USERNAME.lstrip('@')}")],
                        [InlineKeyboardButton("✅ I Joined — Check", callback_data="check_join")]
                    ])
                )
                return
        except Exception as e:
            # FIX: Yahan ab EXACT error print hoga taake pata chale kya masla hai
            error_msg = str(e)
            await update.message.reply_text(f"⚠️ *Channel Verification Failed!*\n\n**Error:** `{error_msg}`\n\nPlease check if bot is admin in {CHANNEL_USERNAME}", parse_mode="Markdown")
            return

    u = user_manager.get_user(user_id)
    if not u:
        user_manager.add_user(user_id, fullname, username)
        admin_msg = f"🔔 *New Request*\n👤 {fullname}\n🆔 `{user_id}`\n📅 {datetime.now().strftime('%Y-%m-%d %H:%M')}"
        for admin_id in ADMIN_IDS:
            try:
                await context.bot.send_message(admin_id, admin_msg, parse_mode="Markdown", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("✅ Approve", callback_data=f"approve_{user_id}"), InlineKeyboardButton("🚫 Ban", callback_data=f"ban_{user_id}")]]))
            except: pass
        await update.message.reply_text("✅ *Request sent to admin.*", parse_mode="Markdown")
        return

    if user_manager.is_banned(user_id):
        await update.message.reply_text("🚫 *You are banned.*", parse_mode="Markdown")
        return
    if user_manager.is_pending(user_id):
        await update.message.reply_text("⏳ *Your request is pending.*", parse_mode="Markdown")
        return

    await update.message.reply_text(f"👋 *Welcome {fullname}!*\nSelect an option:", parse_mode="Markdown", reply_markup=get_main_keyboard(user_id))

async def check_join(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    user_id = query.from_user.id
    if CHANNEL_USERNAME:
        try:
            member = await context.bot.get_chat_member(CHANNEL_USERNAME, user_id)
            if member.status in (constants.ChatMemberStatus.LEFT, constants.ChatMemberStatus.BANNED):
                await query.edit_message_text(
                    "❌ Still not joined.",
                    reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("📢 Channel", url=f"https://t.me/{CHANNEL_USERNAME.lstrip('@')}")], [InlineKeyboardButton("✅ I Joined — Check", callback_data="check_join")]])
                )
                return
        except Exception as e:
            await query.edit_message_text(f"⚠️ **Error:** `{str(e)}`", parse_mode="Markdown")
            return
    user = query.from_user
    u = user_manager.get_user(user_id)
    if not u:
        user_manager.add_user(user_id, user.full_name, user.username or "No username")
        for admin_id in ADMIN_IDS:
            try:
                await context.bot.send_message(admin_id, f"🔔 *New Request*\n👤 {user.full_name}\n🆔 `{user_id}`", parse_mode="Markdown", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("✅ Approve", callback_data=f"approve_{user_id}"), InlineKeyboardButton("🚫 Ban", callback_data=f"ban_{user_id}")]]))
            except: pass
        await query.edit_message_text("✅ Request sent.")
        return
    if user_manager.is_banned(user_id): return await query.edit_message_text("🚫 Banned.")
    if user_manager.is_pending(user_id): return await query.edit_message_text("⏳ Pending.")
    await query.edit_message_text(f"👋 *Welcome {user.full_name}!*", parse_mode="Markdown", reply_markup=get_main_keyboard(user_id))

# ----- MAIN MENU -----
async def main_menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    if not user_manager.is_approved(query.from_user.id): return await query.edit_message_text("❌ Not approved.")
    await query.edit_message_text("📋 *Main Menu*", parse_mode="Markdown", reply_markup=get_main_keyboard(query.from_user.id))

# ----- UPLOAD -----
async def upload_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    if not user_manager.is_approved(query.from_user.id): return await query.edit_message_text("❌ Not approved.")
    await query.edit_message_text("📤 *Send a file* (`.py`, `.js`, or `.zip`)", parse_mode="Markdown")
    return UPLOAD_WAIT

async def upload_receive(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id, document = update.effective_user.id, update.message.document
    if not document: return UPLOAD_WAIT
    filename = document.file_name
    if not any(filename.endswith(ext) for ext in [".py", ".js", ".zip"]):
        await update.message.reply_text("❌ Only `.py`, `.js`, `.zip` allowed.")
        return ConversationHandler.END
    if document.file_size > MAX_UPLOAD_SIZE:
        await update.message.reply_text("❌ File too large.")
        return ConversationHandler.END
    
    ws = ensure_workspace(user_id)
    file_path = ws / sanitize_filename(filename)
    try:
        file = await context.bot.get_file(document.file_id)
        await file.download_to_drive(file_path)
    except Exception as e:
        await update.message.reply_text(f"❌ Error: {e}")
        return ConversationHandler.END

    if filename.endswith(".zip"):
        ext_dir = ws / "extracted"
        ext_dir.mkdir(exist_ok=True)
        success, msg = extract_zip(user_id, file_path, ext_dir)
        if not success:
            await update.message.reply_text(f"❌ {msg}")
            return ConversationHandler.END
        file_type, entry = detect_entry_point(ext_dir)
        if not entry:
            await update.message.reply_text("❌ No main.py/index.js found.")
            return ConversationHandler.END
        dep_ok, dep_msg = await install_dependencies(user_id, ext_dir)
        await update.message.reply_text(f"✅ Extracted. Entry: `{Path(entry).name}`", parse_mode="Markdown")
    else:
        await update.message.reply_text(f"✅ `{file_path.name}` uploaded.", parse_mode="Markdown")
    return ConversationHandler.END

async def upload_cancel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("Upload cancelled.")
    return ConversationHandler.END

# ----- TERMINAL -----
ALLOWED_COMMANDS = {"pwd", "ls", "cd", "cat", "head", "tail", "mkdir", "cp", "mv", "rm"}

async def terminal_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    if not user_manager.is_approved(query.from_user.id): return await query.edit_message_text("❌ Not approved.")
    ws = ensure_workspace(query.from_user.id)
    context.user_data["terminal_cwd"] = str(ws)
    await query.edit_message_text(f"💻 *Terminal*\n`{ws}`\nSend `/cancel` to exit.", parse_mode="Markdown")
    return TERMINAL_SESSION

async def terminal_handle(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text.strip()
    if text.lower() == "/cancel":
        await update.message.reply_text("Terminal ended.")
        return ConversationHandler.END
    parts = shlex.split(text)
    cmd = parts[0].lower()
    if cmd not in ALLOWED_COMMANDS:
        await update.message.reply_text(f"❌ Command not allowed.")
        return TERMINAL_SESSION
    
    cwd = Path(context.user_data.get("terminal_cwd", str(ensure_workspace(update.effective_user.id))))
    if cmd == "cd" and len(parts) > 1:
        target = (cwd / parts[1]).resolve()
        if is_safe_path(update.effective_user.id, target) and target.is_dir():
            context.user_data["terminal_cwd"] = str(target)
            await update.message.reply_text(f"📁 `{target}`", parse_mode="Markdown")
        else:
            await update.message.reply_text("❌ Invalid path.")
        return TERMINAL_SESSION

    try:
        proc = await asyncio.create_subprocess_exec(*parts, cwd=str(cwd), stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
        stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=10)
        out = (stdout.decode() + stderr.decode())[:4000] or "(no output)"
        await update.message.reply_text(f"```\n{out}\n```", parse_mode="Markdown")
    except Exception as e:
        await update.message.reply_text(f"❌ Error: {e}")
    return TERMINAL_SESSION

async def terminal_cancel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("Cancelled.")
    return ConversationHandler.END

# ----- SCRIPTS & RUN -----
async def my_scripts(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    files = list(ensure_workspace(query.from_user.id).glob("**/*.py")) + list(ensure_workspace(query.from_user.id).glob("**/*.js"))
    if not files: return await query.edit_message_text("📂 *No scripts.*", parse_mode="Markdown")
    kb = [[InlineKeyboardButton(f"📄 {f.name}", callback_data=f"view_script_{f}"), InlineKeyboardButton("▶️ Run", callback_data=f"run_script_{f}")] for f in files]
    kb.append([InlineKeyboardButton("🔙 Back", callback_data="main_menu")])
    await query.edit_message_text("📂 *Your Scripts*", parse_mode="Markdown", reply_markup=InlineKeyboardMarkup(kb))

async def view_script(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    file_path = query.data.replace("view_script_", "", 1)
    try:
        with open(file_path, "r") as f:
            await query.edit_message_text(f"📄 `{Path(file_path).name}`\n```\n{f.read(500)}\n```", parse_mode="Markdown", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("▶️ Run", callback_data=f"run_script_{file_path}")], [InlineKeyboardButton("🔙 Back", callback_data="my_scripts")]]))
    except Exception as e:
        await query.edit_message_text(f"❌ Error: {e}")

async def run_script_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    user_id = query.from_user.id
    script_path = Path(query.data.replace("run_script_", "", 1))
    if not script_path.exists(): return await query.edit_message_text("❌ File not found.")
    
    for p in [p for p in user_manager.get_user_processes(user_id) if p["status"] == "running"]:
        user_manager.stop_process(p["pid"])
    
    await query.edit_message_text("⏳ *Starting...*", parse_mode="Markdown")
    pid, log_path, msg = await run_script_with_watchdog(user_id, script_path, "py" if script_path.suffix == ".py" else "js", context)
    if pid: user_manager.add_process(user_id, script_path.name, pid, log_path)
    await query.edit_message_text(f"{msg}\n📄 `{Path(log_path).name}`", parse_mode="Markdown", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 Back", callback_data="my_scripts")]]))

async def view_logs(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    procs = user_manager.get_user_processes(query.from_user.id)
    kb = [[InlineKeyboardButton(f"📄 {Path(p['log_path']).name}", callback_data=f"view_log_{p['log_path']}")] for p in procs if Path(p.get("log_path", "")).exists()]
    kb.append([InlineKeyboardButton("🔙 Back", callback_data="main_menu")])
    await query.edit_message_text("📝 *Logs*", parse_mode="Markdown", reply_markup=InlineKeyboardMarkup(kb))

async def stop_script(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    running = [p for p in user_manager.get_user_processes(query.from_user.id) if p["status"] == "running"]
    if not running: return await query.edit_message_text("🛑 *No running processes.*", parse_mode="Markdown", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 Back", callback_data="main_menu")]]))
    kb = [[InlineKeyboardButton(f"Stop {p['filename']}", callback_data=f"stop_proc_{p['pid']}")] for p in running]
    kb.append([InlineKeyboardButton("🔙 Back", callback_data="main_menu")])
    await query.edit_message_text("Select to stop:", reply_markup=InlineKeyboardMarkup(kb))

async def stop_proc_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    if user_manager.stop_process(int(query.data.split("_")[2])):
        await query.edit_message_text("✅ Stopped.")
    else:
        await query.edit_message_text("❌ Failed.")

# ----- ADMIN PANEL -----
async def admin_panel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    if query.from_user.id not in ADMIN_IDS: return await query.edit_message_text("❌ Not admin.")
    kb = [
        [InlineKeyboardButton("⏳ Pending", callback_data="admin_pending"), InlineKeyboardButton("👥 Users", callback_data="admin_users")],
        [InlineKeyboardButton("🔙 Back", callback_data="main_menu")]
    ]
    await query.edit_message_text("👑 *Admin*", parse_mode="Markdown", reply_markup=InlineKeyboardMarkup(kb))

async def admin_pending(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    kb = [[InlineKeyboardButton(f"{r['name']}", callback_data=f"pending_{r['user_id']}")] for r in user_manager.get_pending_requests()]
    kb.append([InlineKeyboardButton("🔙 Back", callback_data="admin_panel")])
    await query.edit_message_text("⏳ *Pending*", parse_mode="Markdown", reply_markup=InlineKeyboardMarkup(kb))

async def pending_action(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    uid = int(query.data.split("_")[1])
    kb = [[InlineKeyboardButton("✅ Approve", callback_data=f"approve_{uid}"), InlineKeyboardButton("🚫 Ban", callback_data=f"ban_{uid}")], [InlineKeyboardButton("🔙 Back", callback_data="admin_pending")]]
    await query.edit_message_text(f"Action for `{uid}`:", parse_mode="Markdown", reply_markup=InlineKeyboardMarkup(kb))

async def approve_user_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    uid = int(query.data.split("_")[1])
    user_manager.approve_user(uid)
    await query.edit_message_text(f"✅ Approved `{uid}`")
    try: await context.bot.send_message(uid, "✅ *Approved!* Use /start.", parse_mode="Markdown")
    except: pass

async def ban_user_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    uid = int(query.data.split("_")[1])
    user_manager.ban_user(uid)
    await query.edit_message_text(f"🚫 Banned `{uid}`")

# ----- FLASK & MAIN -----
flask_app = Flask(__name__)
@flask_app.route('/')
@flask_app.route('/api/healthz')
def health(): return jsonify({"status": "ok"})
def run_flask(): flask_app.run(host='0.0.0.0', port=PORT, debug=False, use_reloader=False)

async def post_init(application: Application):
    async def scheduled_cleanup():
        while True:
            await asyncio.sleep(86400)
            for uid in user_manager.users:
                log_dir = user_manager.get_workspace(int(uid)) / "logs"
                if log_dir.exists():
                    for f in log_dir.glob("*.log"):
                        if datetime.fromtimestamp(f.stat().st_mtime) < datetime.now() - timedelta(days=7):
                            try: f.unlink()
                            except: pass
    asyncio.create_task(scheduled_cleanup())

def main():
    Thread(target=run_flask, daemon=True).start()
    app = Application.builder().token(BOT_TOKEN).post_init(post_init).build()
    
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CallbackQueryHandler(check_join, pattern="^check_join$"))
    app.add_handler(CallbackQueryHandler(main_menu, pattern="^main_menu$"))
    app.add_handler(CallbackQueryHandler(upload_start, pattern="^upload$"))
    app.add_handler(CallbackQueryHandler(terminal_start, pattern="^terminal$"))
    app.add_handler(CallbackQueryHandler(my_scripts, pattern="^my_scripts$"))
    app.add_handler(CallbackQueryHandler(view_script, pattern="^view_script_"))
    app.add_handler(CallbackQueryHandler(run_script_callback, pattern="^run_script_"))
    app.add_handler(CallbackQueryHandler(view_logs, pattern="^logs$"))
    app.add_handler(CallbackQueryHandler(stop_script, pattern="^stop$"))
    app.add_handler(CallbackQueryHandler(stop_proc_callback, pattern="^stop_proc_"))
    app.add_handler(CallbackQueryHandler(admin_panel, pattern="^admin_panel$"))
    app.add_handler(CallbackQueryHandler(admin_pending, pattern="^admin_pending$"))
    app.add_handler(CallbackQueryHandler(pending_action, pattern="^pending_"))
    app.add_handler(CallbackQueryHandler(approve_user_callback, pattern="^approve_"))
    app.add_handler(CallbackQueryHandler(ban_user_callback, pattern="^ban_"))

    app.add_handler(ConversationHandler(
        entry_points=[CallbackQueryHandler(upload_start, pattern="^upload$")],
        states={UPLOAD_WAIT: [MessageHandler(filters.Document.ALL, upload_receive)]},
        fallbacks=[CommandHandler("cancel", upload_cancel)],
    ))
    app.add_handler(ConversationHandler(
        entry_points=[CallbackQueryHandler(terminal_start, pattern="^terminal$")],
        states={TERMINAL_SESSION: [MessageHandler(filters.TEXT & ~filters.COMMAND, terminal_handle)]},
        fallbacks=[CommandHandler("cancel", terminal_cancel)],
    ))

    app.run_polling()

if __name__ == "__main__":
    main()
