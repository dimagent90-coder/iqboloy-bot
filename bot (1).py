# -*- coding: utf-8 -*-
"""
Anonymous Q&A Telegram bot.

Flow:
  - User must be a member of the required channel before asking.
  - User submits a question anonymously.
  - Bot forwards the question to the admin (Iqboloy) as a notification.
  - Admin replies; the answer is relayed back to the asker as coming from the bot.
  - All Q&A appear in a public feed; users can like questions.
  - Admin can list all still-unanswered questions with /pending.
"""

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
BOT_TOKEN        = os.environ["BOT_TOKEN"]              # from @BotFather
REQUIRED_CHANNEL = os.environ.get("REQUIRED_CHANNEL", "@xorazmloyihalar")
ADMIN_ID         = int(os.environ["ADMIN_ID"])         # Iqboloy's numeric Telegram ID
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
        created_at TEXT NOT NULL
    );
    CREATE TABLE IF NOT EXISTS likes (
        question_id INTEGER NOT NULL,
        user_id     INTEGER NOT NULL,
        PRIMARY KEY (question_id, user_id)
    );
    """)
    conn.commit()
    conn.close()

def like_count(qid):
    conn = db()
    n = conn.execute("SELECT COUNT(*) c FROM likes WHERE question_id=?", (qid,)).fetchone()["c"]
    conn.close()
    return n

# ----------------------------- HELPERS -----------------------------
async def is_member(context, user_id):
    """True if user is a member/admin/owner of the required channel."""
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
    await update.message.reply_text(
        "Assalomu alaykum! 👋\n\n"
        "Savolingizni shu yerga yozing — u *anonim* tarzda yuboriladi.\n"
        "Boshqalarning savollarini ko‘rish: /feed",
        parse_mode="Markdown",
    )

async def feed(update: Update, context: ContextTypes.DEFAULT_TYPE):
    conn = db()
    rows = conn.execute(
        "SELECT * FROM questions ORDER BY id DESC LIMIT 15"
    ).fetchall()
    conn.close()
    if not rows:
        await update.message.reply_text("Hozircha savollar yo‘q.")
        return
    for r in rows:
        body, kb = question_card(r["id"], r["text"], r["answer"] if r["answered"] else None)
        await update.message.reply_text(body, parse_mode="Markdown", reply_markup=kb)

async def pending(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Admin-only: list all still-unanswered questions."""
    uid = update.effective_user.id
    if uid != ADMIN_ID:
        await update.message.reply_text("Bu buyruq faqat administrator uchun.")
        return
    conn = db()
    rows = conn.execute(
        "SELECT * FROM questions WHERE answered=0 ORDER BY id ASC"
    ).fetchall()
    conn.close()
    if not rows:
        await update.message.reply_text("🎉 Javob berilmagan savollar yo‘q. Hammasi javoblangan!")
        return
    await update.message.reply_text(
        f"📋 Javob kutayotgan savollar: {len(rows)} ta\n"
        "Javob berish uchun kerakli savolga *reply* qiling.",
        parse_mode="Markdown",
    )
    # Send each unanswered question as its own message so the admin can reply to it.
    for r in rows:
        await update.message.reply_text(
            f"⏳ Savol #{r['id']}\n\n{r['text']}\n\n↩️ Javob berish uchun shu xabarga reply qiling.",
        )

# ----------------------------- MESSAGES -----------------------------
async def on_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id

    # If the ADMIN is replying to a forwarded question, treat it as an answer.
    if uid == ADMIN_ID and update.message.reply_to_message:
        replied = update.message.reply_to_message.text or ""
        # Forwarded questions are tagged with "#<id>" — parse it.
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
            # Relay to asker as coming from the bot (Iqboloy stays hidden)
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

    # Otherwise a normal user is asking a question — require membership.
    if not await is_member(context, uid):
        await update.message.reply_text(
            "Savol berish uchun avval kanalga qo‘shiling.",
            reply_markup=join_prompt(),
        )
        return

    text = update.message.text.strip()
    if len(text) < 3:
        await update.message.reply_text("Savolingiz juda qisqa. Iltimos to‘liqroq yozing.")
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
    # Notify the admin (Iqboloy). The "#<id>" tag lets us match her reply.
    await context.bot.send_message(
        ADMIN_ID,
        f"🆕 Yangi savol #{qid}\n\n{text}\n\n"
        "↩️ Javob berish uchun shu xabarga *reply* qiling.",
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

# ----------------------------- MAIN -----------------------------
def main():
    init_db()
    app = Application.builder().token(BOT_TOKEN).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("feed", feed))
    app.add_handler(CommandHandler("pending", pending))
    app.add_handler(CallbackQueryHandler(on_callback))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, on_text))
    log.info("Bot started.")
    app.run_polling(allowed_updates=Update.ALL_TYPES)

if __name__ == "__main__":
    main()
