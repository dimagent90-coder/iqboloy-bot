# -*- coding: utf-8 -*-
"""Anonymous Q&A Telegram bot."""

import os
import sqlite3
import logging
from datetime import datetime

from telegram import (
    Update,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
)
from telegram.constants import ChatMemberStatus
from telegram.ext import (
    Application,
    CommandHandler,
    MessageHandler,
    CallbackQueryHandler,
    ContextTypes,
    filters,
)

# ----------------------------- CONFIG -----------------------------
BOT_TOKEN        = os.environ["BOT_TOKEN"]
REQUIRED_CHANNEL = os.environ.get("REQUIRED_CHANNEL", "@xorazmloyihalar")
ADMIN_ID         = int(os.environ["ADMIN_ID"])
DB_PATH          = os.environ.get("DB_PATH", "qabot.db")

logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO,
)
log = logging.getLogger("qabot")

# ----------------------------- DATABASE -----------------------------
def db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn

def init_db():
    conn = db()
    conn.executescript("""
    CREATE TABLE IF NOT EXISTS questions (
        id         INTEGER PRIMARY KEY AUTOINCREMENT,
        asker_id   INTEGER NOT NULL,
        text       TEXT NOT NULL,
        answer     TEXT,
        answered   INTEGER DEFAULT 0,
        photo_id   TEXT,
        created_at TEXT NOT NULL
    );
    CREATE TABLE IF NOT EXISTS likes (
        question_id INTEGER NOT NULL,
        user_id     INTEGER NOT NULL,
        PRIMARY KEY (question_id, user_id)
    );
    CREATE TABLE IF NOT EXISTS blocked (
        user_id INTEGER PRIMARY KEY
    );
    """)
    cols = [r["name"] for r in conn.execute("PRAGMA table_info(questions)").fetchall()]
    if "photo_id" not in cols:
        conn.execute("ALTER TABLE questions ADD COLUMN photo_id TEXT")
    conn.commit()
    conn.close()

def like_count(qid):
    conn = db()
    n = conn.execute("SELECT COUNT(*) c FROM likes WHERE question_id=?", (qid,)).fetchone()["c"]
    conn.close()
    return n

def is_blocked(user_id):
    conn = db()
    row = conn.execute("SELECT 1 FROM blocked WHERE user_id=?", (user_id,)).fetchone()
    conn.close()
    return row is not None

def asker_of(qid):
    conn = db()
    row = conn.execute("SELECT asker_id FROM questions WHERE id=?", (qid,)).fetchone()
    conn.close()
    return row["asker_id"] if row else None

BAD_WORDS = set(
    w.strip().lower() for w in os.environ.get(
        "BAD_WORDS",
        "jinni,ahmoq,tentak,fuck,shit,bitch,suka,blyat,pidr,debil"
    ).split(",") if w.strip()
)

def has_profanity(text):
    t = text.lower()
    return any(w in t for w in BAD_WORDS)

# ----------------------------- HELPERS -----------------------------
async def is_member(context, user_id):
    try:
        m = await context.bot.get_chat_member(REQUIRED_CHANNEL, user_id)
        return m.status in (
            ChatMemberStatus.MEMBER,
            ChatMemberStatus.ADMINISTRATOR,
            ChatMemberStatus.OWNER,
        )
    except Exception as e:
        log.warning("membership check failed: %s", e)
        return False

def join_prompt():
    ch = REQUIRED_CHANNEL.lstrip("@")
    kb = InlineKeyboardMarkup([
        [InlineKeyboardButton("📢 Kanalga qo‘shilish", url=f"https://t.me/{ch}")],
        [InlineKeyboardButton("✅ Tekshirish / Check", callback_data="check_join")],
    ])
    return kb

def question_card(qid, text, answer=None):
    n = like_count(qid)
    body = f"❓ *Savol #{qid}*\n{text}"
    if answer:
        body += f"\n\n💬 *Javob:*\n{answer}"
    kb = InlineKeyboardMarkup([[
        InlineKeyboardButton(f"👍 {n}", callback_data=f"like:{qid}"),
    ]])
    return body, kb

# ----------------------------- COMMANDS -----------------------------
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    if not await is_member(context, uid):
        await update.message.reply_text(
            "Savol berish uchun avval kanalimizga qo‘shiling:\n"
            f"{REQUIRED_CHANNEL}\n\nQo‘shilgach «Tekshirish» tugmasini bosing.",
            reply_markup=join_prompt(),
        )
        return
    name = update.effective_user.first_name or update.effective_user.username or "do‘st"
    await update.message.reply_text(
        f"✈️Assalomu alaykum {name} 😁\n\n"
        "🙌Savolingizni shu yerga yozing (anonim tarzda yuboriladi✅)\n\n"
        "Boshqalarning savollarini ko‘rish: /feed",
    )

async def feed(update: Update, context: ContextTypes.DEFAULT_TYPE):
    conn = db()
    rows = conn.execute("SELECT * FROM questions ORDER BY id DESC LIMIT 15").fetchall()
    conn.close()
    if not rows:
        await update.message.reply_text("Hozircha savollar yo‘q.")
        return
    for r in rows:
        body, kb = question_card(r["id"], r["text"], r["answer"] if r["answered"] else None)
        await update.message.reply_text(body, parse_mode="Markdown", reply_markup=kb)

async def pending(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    if uid != ADMIN_ID:
        await update.message.reply_text("Bu buyruq faqat administrator uchun.")
        return
    conn = db()
    rows = conn.execute("SELECT * FROM questions WHERE answered=0 ORDER BY id ASC").fetchall()
    conn.close()
    if not rows:
        await update.message.reply_text("🎉 Javob berilmagan savollar yo‘q. Hammasi javoblangan!")
        return
    await update.message.reply_text(
        f"📋 Javob kutayotgan savollar: {len(rows)} ta\n"
        "Javob berish uchun kerakli savolga *reply* qiling.",
        parse_mode="Markdown",
    )
    for r in rows:
        await update.message.reply_text(
            f"⏳ Savol #{r['id']}\n\n{r['text']}\n\n↩️ Javob berish uchun shu xabarga reply qiling.",
        )

async def block(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != ADMIN_ID:
        return
    if not context.args or not context.args[0].isdigit():
        await update.message.reply_text("Foydalanish: /block <savol raqami>")
        return
    qid = int(context.args[0])
    aid = asker_of(qid)
    if not aid:
        await update.message.reply_text(f"Savol #{qid} topilmadi.")
        return
    conn = db()
    conn.execute("INSERT OR IGNORE INTO blocked (user_id) VALUES (?)", (aid,))
    conn.commit(); conn.close()
    await update.message.reply_text(f"⛔ Savol #{qid} muallifi bloklandi. Endi u savol yubora olmaydi.")

async def unblock(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != ADMIN_ID:
        return
    if not context.args or not context.args[0].isdigit():
        await update.message.reply_text("Foydalanish: /unblock <savol raqami>")
        return
    qid = int(context.args[0])
    aid = asker_of(qid)
    if not aid:
        await update.message.reply_text(f"Savol #{qid} topilmadi.")
        return
    conn = db()
    conn.execute("DELETE FROM blocked WHERE user_id=?", (aid,))
    conn.commit(); conn.close()
    await update.message.reply_text(f"✅ Savol #{qid} muallifi blokdan chiqarildi.")

async def delete(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != ADMIN_ID:
        return
    if not context.args or not context.args[0].isdigit():
        await update.message.reply_text("Foydalanish: /delete <savol raqami>")
        return
    qid = int(context.args[0])
    conn = db()
    row = conn.execute("SELECT 1 FROM questions WHERE id=?", (qid,)).fetchone()
    if not row:
        conn.close()
        await update.message.reply_text(f"Savol #{qid} topilmadi.")
        return
    conn.execute("DELETE FROM questions WHERE id=?", (qid,))
    conn.execute("DELETE FROM likes WHERE question_id=?", (qid,))
    conn.commit(); conn.close()
    await update.message.reply_text(f"🗑️ Savol #{qid} o‘chirildi.")

async def clearanswer(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != ADMIN_ID:
        return
    if not context.args or not context.args[0].isdigit():
        await update.message.reply_text("Foydalanish: /clearanswer <savol raqami>")
        return
    qid = int(context.args[0])
    conn = db()
    row = conn.execute("SELECT 1 FROM questions WHERE id=?", (qid,)).fetchone()
    if not row:
        conn.close()
        await update.message.reply_text(f"Savol #{qid} topilmadi.")
        return
    conn.execute("UPDATE questions SET answer=NULL, answered=0 WHERE id=?", (qid,))
    conn.commit(); conn.close()
    await update.message.reply_text(
        f"♻️ Savol #{qid} javobi o‘chirildi. Endi unga qayta javob berishingiz mumkin."
    )

# ----------------------------- MESSAGES -----------------------------
async def on_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id

    if uid == ADMIN_ID and update.message.reply_to_message:
        rm = update.message.reply_to_message
        replied = (rm.text or "") + " " + (rm.caption or "")
        qid = None
        for token in replied.replace("\n", " ").split():
            t = token.strip(".,:")
            if t.startswith("#") and t[1:].isdigit():
                qid = int(t[1:]); break
        if qid:
            answer = update.message.text
            conn = db()
            q = conn.execute("SELECT * FROM questions WHERE id=?", (qid,)).fetchone()
            if not q:
                conn.close()
                await update.message.reply_text(f"Savol #{qid} topilmadi.")
                return
            conn.execute("UPDATE questions SET answer=?, answered=1 WHERE id=?", (answer, qid))
            conn.commit(); conn.close()
            try:
                await context.bot.send_message(
                    q["asker_id"],
                    f"💬 *Savolingizga javob keldi:*\n\n❓ {q['text']}\n\n✅ {answer}",
                    parse_mode="Markdown",
                )
            except Exception as e:
                log.warning("could not notify asker: %s", e)
            await update.message.reply_text(f"✅ Javob #{qid} yuborildi.")
            return

    if not await is_member(context, uid):
        await update.message.reply_text(
            "Savol berish uchun avval kanalga qo‘shiling.",
            reply_markup=join_prompt(),
        )
        return

    if is_blocked(uid):
        await update.message.reply_text("⛔ Siz vaqtincha savol berish huquqidan mahrum qilingansiz.")
        return

    text = update.message.text.strip()
    if len(text) < 3:
        await update.message.reply_text("Savolingiz juda qisqa. Iltimos to‘liqroq yozing.")
        return
    if has_profanity(text):
        await update.message.reply_text("⚠️ Savolingizda nomaqbul so‘zlar bor. Iltimos, hurmat bilan qayta yozing.")
        return

    conn = db()
    cur = conn.execute(
        "INSERT INTO questions (asker_id, text, created_at) VALUES (?,?,?)",
        (uid, text, datetime.utcnow().isoformat()),
    )
    qid = cur.lastrowid
    conn.commit(); conn.close()

    await update.message.reply_text(
        f"✅ Savolingiz anonim tarzda yuborildi (Savol #{qid}).\n"
        "Javob tayyor bo‘lgach sizga xabar beramiz."
    )
    await context.bot.send_message(
        ADMIN_ID,
        f"🆕 Yangi savol #{qid}\n\n{text}\n\n↩️ Javob berish uchun shu xabarga *reply* qiling.",
        parse_mode="Markdown",
    )

# ----------------------------- CALLBACKS -----------------------------
async def on_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    uid = q.from_user.id
    data = q.data

    if data == "check_join":
        if await is_member(context, uid):
            await q.answer("✅ Tasdiqlandi! Endi savol yozishingiz mumkin.", show_alert=True)
            await q.edit_message_text("Rahmat! Endi savolingizni yozib yuboring. 🙂")
        else:
            await q.answer("Hali qo‘shilmagansiz. Kanalga qo‘shiling.", show_alert=True)
        return

    if data.startswith("like:"):
        qid = int(data.split(":")[1])
        conn = db()
        try:
            conn.execute("INSERT INTO likes (question_id, user_id) VALUES (?,?)", (qid, uid))
            conn.commit()
            liked = True
        except sqlite3.IntegrityError:
            conn.execute("DELETE FROM likes WHERE question_id=? AND user_id=?", (qid, uid))
            conn.commit()
            liked = False
        row = conn.execute("SELECT * FROM questions WHERE id=?", (qid,)).fetchone()
        conn.close()
        body, kb = question_card(qid, row["text"], row["answer"] if row["answered"] else None)
        try:
            await q.edit_message_text(body, parse_mode="Markdown", reply_markup=kb)
        except Exception:
            pass
        await q.answer("👍 Yoqdi!" if liked else "Bekor qilindi.")
        return

async def on_photo(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id

    if not await is_member(context, uid):
        await update.message.reply_text(
            "Savol berish uchun avval kanalga qo‘shiling.",
            reply_markup=join_prompt(),
        )
        return
    if is_blocked(uid):
        await update.message.reply_text("⛔ Siz vaqtincha savol berish huquqidan mahrum qilingansiz.")
        return

    file_id = update.message.photo[-1].file_id
    caption = (update.message.caption or "").strip()
    if caption and has_profanity(caption):
        await update.message.reply_text("⚠️ Izohingizda nomaqbul so‘zlar bor. Iltimos, hurmat bilan qayta yuboring.")
        return

    text = caption if caption else "(rasm)"
    conn = db()
    cur = conn.execute(
        "INSERT INTO questions (asker_id, text, photo_id, created_at) VALUES (?,?,?,?)",
        (uid, text, file_id, datetime.utcnow().isoformat()),
    )
    qid = cur.lastrowid
    conn.commit(); conn.close()

    await update.message.reply_text(
        f"✅ Rasmli savolingiz anonim tarzda yuborildi (Savol #{qid}).\n"
        "Javob tayyor bo‘lgach sizga xabar beramiz."
    )
    await context.bot.send_photo(
        ADMIN_ID,
        photo=file_id,
        caption=f"🆕 Yangi rasmli savol #{qid}\n\n{text}\n\n↩️ Javob berish uchun shu xabarga reply qiling.",
    )

# ----------------------------- MAIN -----------------------------
def main():
    init_db()
    app = Application.builder().token(BOT_TOKEN).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("feed", feed))
    app.add_handler(CommandHandler("pending", pending))
    app.add_handler(CommandHandler("block", block))
    app.add_handler(CommandHandler("unblock", unblock))
    app.add_handler(CommandHandler("delete", delete))
    app.add_handler(CommandHandler("clearanswer", clearanswer))
    app.add_handler(CallbackQueryHandler(on_callback))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, on_text))
    app.add_handler(MessageHandler(filters.PHOTO, on_photo))
    log.info("Bot started.")
    app.run_polling(allowed_updates=Update.ALL_TYPES)

if __name__ == "__main__":
    main()
