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

# Username бота (для справки и проверки при запуске), напр. "my_rules_bot".
BOT_USERNAME = os.environ.get("BOT_USERNAME", "").strip().lstrip("@")

# Необязательное ограничение на один канал — по числовому id и/или @username.
_channel_id_raw = os.environ.get("CHANNEL_ID", "").strip()
CHANNEL_ID = int(_channel_id_raw) if _channel_id_raw else None
CHANNEL_USERNAME = os.environ.get("CHANNEL_USERNAME", "").strip().lstrip("@").lower()


def _is_target_channel(origin_chat) -> bool:
    """True, если пост пришёл из нужного канала (или фильтр не задан)."""
    if CHANNEL_ID is None and not CHANNEL_USERNAME:
        return True
    if origin_chat is None:
        return False
    if CHANNEL_ID is not None and origin_chat.id == CHANNEL_ID:
        return True
    if CHANNEL_USERNAME and (origin_chat.username or "").lower() == CHANNEL_USERNAME:
        return True
    return False


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
    if not _is_target_channel(message.sender_chat):
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

    # Сверяем фактический username бота с заданным в настройках.
    async def _check_identity(app: Application) -> None:
        me = await app.bot.get_me()
        logger.info("Бот @%s (id=%s) авторизован.", me.username, me.id)
        if BOT_USERNAME and me.username.lower() != BOT_USERNAME.lower():
            logger.warning(
                "BOT_USERNAME=%s не совпадает с реальным @%s — проверьте токен.",
                BOT_USERNAME,
                me.username,
            )

    application.post_init = _check_identity

    if CHANNEL_USERNAME or CHANNEL_ID is not None:
        logger.info(
            "Фильтр канала: id=%s username=%s",
            CHANNEL_ID,
            f"@{CHANNEL_USERNAME}" if CHANNEL_USERNAME else None,
        )

    # Ловим автопересылки постов в привязанной группе обсуждения.
    application.add_handler(
        MessageHandler(filters.IS_AUTOMATIC_FORWARD, post_rules_comment)
    )

    logger.info("Бот запущен. Ожидаю новые посты канала…")
    application.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()
