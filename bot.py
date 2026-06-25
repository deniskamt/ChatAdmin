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
import sys

from dotenv import load_dotenv
from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.constants import ParseMode
from telegram.error import Conflict
from telegram.ext import Application, ContextTypes, MessageHandler, filters

load_dotenv()

# Пишем логи в stdout (Railway красит stderr в красный, даже обычные INFO).
logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO,
    stream=sys.stdout,
)
# Приглушаем шумный лог опроса getUpdates.
logging.getLogger("httpx").setLevel(logging.WARNING)
logger = logging.getLogger("rules-bot")

BOT_TOKEN = os.environ.get("BOT_TOKEN", "").strip()

# Ссылка на правила в Telegraph (см. create_telegraph.py) — открывается по кнопке.
RULES_URL = os.environ.get("RULES_URL", "").strip()

# Текст сообщения над кнопкой и подпись самой кнопки.
COMMENT_TEXT = os.environ.get("COMMENT_TEXT", "👇 Правила нашего чата:")
BUTTON_TEXT = os.environ.get("BUTTON_TEXT", "📜 Правила чата")

# Username бота (для справки и проверки при запуске), напр. "my_rules_bot".
BOT_USERNAME = os.environ.get("BOT_USERNAME", "").strip().lstrip("@")

def _normalize_username(raw: str) -> str:
    """Приводит '@name', 'https://t.me/name', 't.me/name' к 'name' (в нижнем регистре)."""
    value = raw.strip()
    for prefix in ("https://", "http://", "t.me/", "telegram.me/"):
        if value.lower().startswith(prefix):
            value = value[len(prefix):]
    # на случай 'https://t.me/name' — после срезания протокола остаётся 't.me/name'
    for prefix in ("t.me/", "telegram.me/"):
        if value.lower().startswith(prefix):
            value = value[len(prefix):]
    return value.lstrip("@").strip("/").lower()


# Необязательное ограничение на один канал — по числовому id и/или @username.
_channel_id_raw = os.environ.get("CHANNEL_ID", "").strip()
CHANNEL_ID = int(_channel_id_raw) if _channel_id_raw else None
CHANNEL_USERNAME = _normalize_username(os.environ.get("CHANNEL_USERNAME", ""))


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

    origin = message.sender_chat
    logger.info(
        "Получен пост канала: id=%s username=%s title=%s (группа обсуждения %s)",
        getattr(origin, "id", None),
        getattr(origin, "username", None),
        getattr(origin, "title", None),
        message.chat_id,
    )

    # При необходимости фильтруем по конкретному каналу-источнику.
    if not _is_target_channel(origin):
        logger.info(
            "Пост отфильтрован: не совпал с CHANNEL_ID=%s / CHANNEL_USERNAME=%s",
            CHANNEL_ID,
            CHANNEL_USERNAME or None,
        )
        return

    keyboard = InlineKeyboardMarkup(
        [[InlineKeyboardButton(BUTTON_TEXT, url=RULES_URL)]]
    )

    try:
        await message.reply_text(
            COMMENT_TEXT,
            parse_mode=ParseMode.HTML,
            disable_web_page_preview=True,
            reply_markup=keyboard,
        )
        logger.info(
            "Оставлен комментарий с правилами под постом %s в чате %s",
            message.message_id,
            message.chat_id,
        )
    except Exception:  # noqa: BLE001 — логируем любую ошибку отправки и продолжаем
        logger.exception("Не удалось отправить комментарий с правилами")


async def on_error(update: object, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Гасит ожидаемые сетевые сбои без громоздкого трейсбека."""
    error = context.error
    if isinstance(error, Conflict):
        logger.warning(
            "Conflict: запущен ещё один экземпляр бота с этим токеном. "
            "Оставьте только один."
        )
        return
    logger.error("Ошибка при обработке обновления: %s", error)


def main() -> None:
    if not BOT_TOKEN:
        raise SystemExit("Не задан BOT_TOKEN. Скопируйте .env.example в .env и заполните.")
    if not RULES_URL:
        raise SystemExit(
            "Не задан RULES_URL. Создайте страницу правил: python create_telegraph.py "
            "и вставьте полученную ссылку в RULES_URL."
        )

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
    application.add_error_handler(on_error)

    logger.info("Бот запущен. Ожидаю новые посты канала…")
    application.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()
