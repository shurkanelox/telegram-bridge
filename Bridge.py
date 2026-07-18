"""
Мост Telegram <-> MAX для сценария "поддержка/приёмная":

- Любой человек может написать TG-боту в личку.
- Все такие сообщения стекаются в один MAX-чат, с указанием, от кого они.
- Чтобы ответить конкретному человеку, в MAX-чате нужно сделать Reply
  (ответ) именно на его сообщение — бот поймёт, кому это переслать.
- Соответствие "сообщение в MAX -> кто из Telegram" хранится в SQLite
  (bridge.db) и переживает перезапуск бота.

Работает через long polling с обеих сторон — вебхук и публичный домен
не нужны.
"""

import asyncio
import logging
import os
import tempfile
from pathlib import Path

import aiohttp
from dotenv import load_dotenv

from telegram import Update
from telegram.ext import Application, CommandHandler, ContextTypes, MessageHandler, filters

from maxapi import Bot as MaxBot, Dispatcher as MaxDispatcher
from maxapi.types import MessageCreated, InputMedia
from maxapi.enums.attachment import AttachmentType

import db

load_dotenv()

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
log = logging.getLogger("bridge")

# ---------------------------------------------------------------------------
# Конфигурация
# ---------------------------------------------------------------------------

TG_TOKEN = os.environ["TELEGRAM_BOT_TOKEN"]
MAX_TOKEN = os.environ["MAX_BOT_TOKEN"]

_max_chat_raw = os.environ.get("MAX_CHAT_ID", "").strip()
MAX_CHAT_ID = int(_max_chat_raw) if _max_chat_raw else None

# Отправлять ли автору в Telegram короткое подтверждение "сообщение доставлено"
SEND_ACK_TO_TG_USER = True

# Приветственное сообщение при первом /start
WELCOME_TEXT = (
    "Привет! 👋\n\n"
    "Напиши сюда что угодно — текст, фото или файл — и я передам это дальше. "
    "Как только будет ответ, я перешлю его тебе прямо сюда."
)

TMP_DIR = Path(tempfile.gettempdir()) / "tg_max_bridge"
TMP_DIR.mkdir(parents=True, exist_ok=True)

max_bot = MaxBot(MAX_TOKEN)
max_dp = MaxDispatcher()

tg_app: Application | None = None
MAX_BOT_ID: int | None = None


# ---------------------------------------------------------------------------
# Вспомогательное: вытащить ID отправленного MAX-сообщения из ответа API.
# Структура ответа у молодой библиотеки maxapi может отличаться между
# версиями — поэтому пробуем несколько путей.
# ---------------------------------------------------------------------------

def _extract_mid(sent) -> str | None:
    if sent is None:
        return None
    msg = getattr(sent, "message", sent)
    body = getattr(msg, "body", None)
    mid = getattr(body, "mid", None) or getattr(msg, "mid", None)
    return str(mid) if mid is not None else None


# ---------------------------------------------------------------------------
# /start — приветствие
# ---------------------------------------------------------------------------

async def tg_start_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if update.effective_chat.type != "private":
        return
    await update.effective_message.reply_text(WELCOME_TEXT)


# ---------------------------------------------------------------------------
# Telegram -> MAX  (любой человек пишет боту в личку)
# ---------------------------------------------------------------------------

async def tg_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    message = update.effective_message
    user = update.effective_user
    if message is None or user is None:
        return

    if update.effective_chat.type != "private":
        return  # групповые чаты игнорируем — работаем только с личками

    if MAX_CHAT_ID is None:
        log.warning("MAX_CHAT_ID не задан в .env — не знаю, куда пересылать. Впиши ID MAX-чата и перезапусти бота.")
        return

    display_name = user.full_name
    header = f"👤 {display_name}
    caption = message.caption or message.text or ""
    text = f"{header}\n{caption}" if caption else header

    try:
        attachments = None
        local_path = None

        if message.photo:
            tg_file = await message.photo[-1].get_file()
            local_path = TMP_DIR / f"{tg_file.file_unique_id}.jpg"
            await tg_file.download_to_drive(str(local_path))
            attachments = [InputMedia(path=str(local_path))]

        elif message.document:
            tg_file = await message.document.get_file()
            filename = message.document.file_name or tg_file.file_unique_id
            local_path = TMP_DIR / filename
            await tg_file.download_to_drive(str(local_path))
            attachments = [InputMedia(path=str(local_path))]

        elif message.video:
            tg_file = await message.video.get_file()
            local_path = TMP_DIR / f"{tg_file.file_unique_id}.mp4"
            await tg_file.download_to_drive(str(local_path))
            attachments = [InputMedia(path=str(local_path))]

        sent = await max_bot.send_message(
            chat_id=MAX_CHAT_ID,
            text=text,
            attachments=attachments,
        )

        mid = _extract_mid(sent)
        if mid:
            await db.save_mapping(mid, user.id, display_name)
        else:
            log.warning("Не удалось определить ID отправленного MAX-сообщения — ответ на это сообщение не будет доставлен. Проверь структуру объекта sent (см. README).")

        if local_path and local_path.exists():
            local_path.unlink(missing_ok=True)

        if SEND_ACK_TO_TG_USER:
            await message.reply_text("✅ Сообщение передано.")

    except Exception:
        log.exception("Ошибка при пересылке сообщения из Telegram в MAX")


# ---------------------------------------------------------------------------
# MAX -> Telegram  (ответ конкретному человеку через Reply)
# ---------------------------------------------------------------------------

@max_dp.message_created()
async def max_handler(event: MessageCreated) -> None:
    chat_id, sender_id = event.get_ids()

    if MAX_BOT_ID is not None and sender_id == MAX_BOT_ID:
        return  # свои же сообщения игнорируем (защита от петли)

    if MAX_CHAT_ID is None:
        log.info("Пришло сообщение из MAX-чата с ID: %s (укажи его в .env как MAX_CHAT_ID)", chat_id)
        return

    if chat_id != MAX_CHAT_ID:
        return

    link = event.message.link
    if link is None or getattr(link, "type", None) != "reply":
        await event.message.reply(
            "Чтобы ответить человеку — сделай Reply (ответ) на его сообщение в этом чате."
        )
        return

    replied_mid = str(link.message.mid)
    target = await db.get_tg_user(replied_mid)

    if target is None:
        await event.message.reply(
            "Не нашёл, кому это переслать (слишком старое сообщение или бот был перезапущен без сохранённых данных)."
        )
        return

    tg_user_id, tg_name = target
    body = event.message.body
    text = body.text if body else ""
    attachments = body.attachments if body else []

    try:
        if not attachments:
            if text:
                await tg_app.bot.send_message(chat_id=tg_user_id, text=text)
            return

        caption = text
        for att in attachments:
            payload = att.payload
            url = getattr(payload, "url", None)
            if not url:
                continue

            async with aiohttp.ClientSession() as session:
                async with session.get(url) as resp:
                    data = await resp.read()

            if att.type == AttachmentType.IMAGE:
                await tg_app.bot.send_photo(chat_id=tg_user_id, photo=data, caption=caption or None)
            else:
                filename = url.split("/")[-1].split("?")[0] or "file"
                await tg_app.bot.send_document(
                    chat_id=tg_user_id, document=data, filename=filename, caption=caption or None
                )
            caption = ""  # подпись — только на первое вложение

    except Exception:
        log.exception("Ошибка при пересылке ответа из MAX пользователю %s (%s)", tg_user_id, tg_name)
        await event.message.reply(f"⚠️ Не получилось доставить ответ пользователю {tg_name}.")


# ---------------------------------------------------------------------------
# Запуск
# ---------------------------------------------------------------------------

async def run_telegram() -> None:
    global tg_app
    tg_app = Application.builder().token(TG_TOKEN).build()
    tg_app.add_handler(CommandHandler("start", tg_start_handler))
    tg_app.add_handler(MessageHandler(filters.ChatType.PRIVATE & ~filters.COMMAND, tg_handler))

    async with tg_app:
        await tg_app.start()
        await tg_app.updater.start_polling(drop_pending_updates=True)
        log.info("Telegram-бот запущен и слушает личные сообщения...")
        await asyncio.Event().wait()


async def run_max() -> None:
    global MAX_BOT_ID
    me = await max_bot.get_me()
    MAX_BOT_ID = me.user_id
    log.info("MAX-бот запущен (ID=%s) и слушает сообщения...", MAX_BOT_ID)
    await max_dp.start_polling(max_bot)


async def main() -> None:
    if MAX_CHAT_ID is None:
        log.warning(
            "MAX_CHAT_ID не задан в .env. Напиши что-нибудь в MAX-чат, "
            "где добавлен бот, ID появится в логах ниже — впиши его в .env и перезапусти."
        )

    await asyncio.gather(run_telegram(), run_max())


if __name__ == "__main__":
    asyncio.run(main())