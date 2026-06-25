"""
Телеграм-бот, который под каждым постом канала оставляет в комментариях
ссылку на правила чата.

Как это работает:
  * У канала должна быть привязана группа обсуждения (Linked Group).
  * Каждый новый пост канала Telegram автоматически пересылает в эту группу —
    так создаётся ветка комментариев. Такое сообщение помечено флагом
    `is_automatic_forward`.
  * Бот (он должен быть админом группы обсуждения) ловит это сообщение и
    отвечает на него. Ответ становится первым комментарием под постом.
"""

import logging
import os

from dotenv import load_dotenv
from telegram import Update
from telegram.constants import ParseMode
from telegram.ext import Application, ContextTypes, MessageHandler, filters

load_dotenv()

logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO,
)
logger = logging.getLogger("rules-bot")

BOT_TOKEN = os.environ.get("BOT_TOKEN", "").strip()
RULES_URL = os.environ.get("RULES_URL", "").strip()
COMMENT_TEXT = os.environ.get(
    "COMMENT_TEXT",
    '📜 Перед общением ознакомьтесь с <a href="{rules_url}">правилами чата</a>.',
)

# Необязательное ограничение на один канал.
_channel_id_raw = os.environ.get("CHANNEL_ID", "").strip()
CHANNEL_ID = int(_channel_id_raw) if _channel_id_raw else None


async def post_rules_comment(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Отвечает ссылкой на правила под каждым новым постом канала."""
    message = update.effective_message
    if message is None:
        return

    # Реагируем только на автоматические пересылки поста из канала в группу
    # обсуждения — именно они открывают ветку комментариев.
    if not message.is_automatic_forward:
        return

    # При необходимости фильтруем по конкретному каналу-источнику.
    origin_chat = message.sender_chat
    if CHANNEL_ID is not None and (origin_chat is None or origin_chat.id != CHANNEL_ID):
        return

    text = COMMENT_TEXT.format(rules_url=RULES_URL)

    try:
        await message.reply_text(
            text,
            parse_mode=ParseMode.HTML,
            disable_web_page_preview=True,
        )
        logger.info(
            "Оставлен комментарий с правилами под постом %s в чате %s",
            message.message_id,
            message.chat_id,
        )
    except Exception:  # noqa: BLE001 — логируем любую ошибку отправки и продолжаем
        logger.exception("Не удалось отправить комментарий с правилами")


def main() -> None:
    if not BOT_TOKEN:
        raise SystemExit("Не задан BOT_TOKEN. Скопируйте .env.example в .env и заполните.")
    if not RULES_URL:
        logger.warning("RULES_URL пуст — комментарий будет со ссылкой-заглушкой.")

    application = Application.builder().token(BOT_TOKEN).build()

    # Ловим автопересылки постов в привязанной группе обсуждения.
    application.add_handler(
        MessageHandler(filters.IS_AUTOMATIC_FORWARD, post_rules_comment)
    )

    logger.info("Бот запущен. Ожидаю новые посты канала…")
    application.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()
