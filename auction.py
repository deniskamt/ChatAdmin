"""
Интерактивная часть бота: меню в личке, ставки, заявки на пост.

Меню (/start):
  🔨 Сделать ставку  — список активных лотов (по 10 на страницу) → карточка лота
                       с описанием и списком ставок → кнопка «Поставить».
  📝 Запросить пост  — пользователь описывает товар, заявка уходит админам в личку.
  📜 Правила         — ссылка на правила.

Админы (ADMIN_IDS) дополнительно могут добавлять/снимать лоты.
"""

import logging
import os

from telegram import (
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    Update,
)
from telegram.constants import ParseMode
from telegram.ext import (
    Application,
    CallbackQueryHandler,
    CommandHandler,
    ContextTypes,
    MessageHandler,
    filters,
)

import db

logger = logging.getLogger("rules-bot.auction")

PAGE_SIZE = 10

# id администраторов через запятую, напр. "12345,67890".
ADMIN_IDS = {
    int(x) for x in os.environ.get("ADMIN_IDS", "").replace(" ", "").split(",") if x
}

# Показывать ставки анонимно (1) или с @username (0).
ANON_BIDS = os.environ.get("ANON_BIDS", "0").strip().lower() not in ("0", "false", "no", "")

# Ссылка на правила (проставляется из bot.py при регистрации).
RULES_URL = ""


def is_admin(user_id: int) -> bool:
    return user_id in ADMIN_IDS


def _display_name(user) -> str:
    if user.username:
        return "@" + user.username
    return user.full_name or str(user.id)


# ---------- Клавиатуры ----------

def main_menu_keyboard(user_id: int) -> InlineKeyboardMarkup:
    rows = [
        [InlineKeyboardButton("🔨 Сделать ставку", callback_data="lots:0")],
        [InlineKeyboardButton("📝 Запросить пост", callback_data="menu:request")],
    ]
    if RULES_URL:
        rows.append([InlineKeyboardButton("📜 Правила", url=RULES_URL)])
    if is_admin(user_id):
        rows.append([InlineKeyboardButton("➕ Добавить лот", callback_data="menu:addlot")])
    return InlineKeyboardMarkup(rows)


def lots_page_keyboard(page: int) -> InlineKeyboardMarkup:
    total = db.count_active_lots()
    offset = page * PAGE_SIZE
    lots = db.get_active_lots(PAGE_SIZE, offset)

    rows = [
        [InlineKeyboardButton(f"#{lot['id']} · {lot['title']}", callback_data=f"lot:{lot['id']}")]
        for lot in lots
    ]

    nav = []
    if page > 0:
        nav.append(InlineKeyboardButton("◀️ Назад", callback_data=f"lots:{page - 1}"))
    if offset + PAGE_SIZE < total:
        nav.append(InlineKeyboardButton("Вперёд ▶️", callback_data=f"lots:{page + 1}"))
    if nav:
        rows.append(nav)

    rows.append([InlineKeyboardButton("🏠 В меню", callback_data="menu:home")])
    return InlineKeyboardMarkup(rows)


def lot_card_keyboard(lot_id: int, page: int = 0) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("💰 Поставить", callback_data=f"bid:{lot_id}")],
        [InlineKeyboardButton("◀️ К списку", callback_data=f"lots:{page}")],
    ])


def render_lot_card(lot_id: int) -> tuple[str | None, InlineKeyboardMarkup | None]:
    lot = db.get_lot(lot_id)
    if lot is None or not lot["active"]:
        return None, None

    bids = db.get_bids(lot_id)
    lines = [f"<b>Лот #{lot['id']}: {lot['title']}</b>"]
    if lot["description"]:
        lines.append(lot["description"])
    lines.append("")
    if bids:
        top = bids[0]["amount"]
        lines.append(f"💵 <b>Текущая ставка: {top}</b>")
        lines.append("Ставки:")
        for i, b in enumerate(bids, 1):
            who = "Аноним" if b["anonymous"] else (b["username"] or "Участник")
            lines.append(f"{i}. {who} — {b['amount']}")
    else:
        lines.append("Ставок пока нет — будь первым!")

    return "\n".join(lines), lot_card_keyboard(lot_id)


# ---------- Команды ----------

async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user = update.effective_user
    await update.message.reply_text(
        f"Привет, {user.first_name}! 👋\nЭто бот аукциона. Выбери действие:",
        reply_markup=main_menu_keyboard(user.id),
    )


async def cmd_addlot(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not is_admin(update.effective_user.id):
        await update.message.reply_text("Команда доступна только администраторам.")
        return
    context.user_data["await"] = {"action": "addlot_title"}
    await update.message.reply_text("Добавление лота. Пришлите <b>название</b> лота:",
                                    parse_mode=ParseMode.HTML)


async def cmd_lots_admin(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not is_admin(update.effective_user.id):
        return
    lots = db.list_all_lots()
    if not lots:
        await update.message.reply_text("Лотов пока нет. Добавить: /addlot")
        return
    lines = ["<b>Все лоты:</b>"]
    for lot in lots:
        mark = "🟢" if lot["active"] else "⚪️"
        lines.append(f"{mark} #{lot['id']} — {lot['title']}")
    lines.append("\nСнять лот: /dellot <id>")
    await update.message.reply_text("\n".join(lines), parse_mode=ParseMode.HTML)


async def cmd_dellot(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not is_admin(update.effective_user.id):
        return
    if not context.args:
        await update.message.reply_text("Использование: /dellot <id лота>")
        return
    try:
        lot_id = int(context.args[0])
    except ValueError:
        await update.message.reply_text("id лота должен быть числом.")
        return
    ok = db.deactivate_lot(lot_id)
    await update.message.reply_text(
        f"Лот #{lot_id} снят с аукциона." if ok else f"Лот #{lot_id} не найден."
    )


# ---------- Callback-кнопки ----------

async def on_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    await query.answer()
    data = query.data or ""
    user_id = query.from_user.id

    if data == "menu:home":
        await query.edit_message_text(
            "Выбери действие:", reply_markup=main_menu_keyboard(user_id)
        )
        return

    if data == "menu:request":
        context.user_data["await"] = {"action": "request"}
        await query.edit_message_text(
            "📝 Опишите товар, который хотите выставить (одним сообщением). "
            "Заявка уйдёт администратору."
        )
        return

    if data == "menu:addlot":
        if not is_admin(user_id):
            return
        context.user_data["await"] = {"action": "addlot_title"}
        await query.edit_message_text("Добавление лота. Пришлите <b>название</b> лота:",
                                      parse_mode=ParseMode.HTML)
        return

    if data.startswith("lots:"):
        page = int(data.split(":")[1])
        if db.count_active_lots() == 0:
            await query.edit_message_text(
                "Активных лотов пока нет.", reply_markup=main_menu_keyboard(user_id)
            )
            return
        await query.edit_message_text(
            "🔨 Активные лоты — выберите:", reply_markup=lots_page_keyboard(page)
        )
        return

    if data.startswith("lot:"):
        lot_id = int(data.split(":")[1])
        text, keyboard = render_lot_card(lot_id)
        if text is None:
            await query.edit_message_text(
                "Лот недоступен.", reply_markup=lots_page_keyboard(0)
            )
            return
        await query.edit_message_text(text, parse_mode=ParseMode.HTML, reply_markup=keyboard)
        return

    if data.startswith("bid:"):
        lot_id = int(data.split(":")[1])
        lot = db.get_lot(lot_id)
        if lot is None or not lot["active"]:
            await query.edit_message_text("Лот недоступен.")
            return
        context.user_data["await"] = {"action": "bid", "lot_id": lot_id}
        await query.message.reply_text(
            f"Введите вашу ставку для лота #{lot_id} «{lot['title']}» (число):"
        )
        return


# ---------- Текстовый ввод (ставка / заявка / добавление лота) ----------

async def on_text(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    state = context.user_data.get("await")
    if not state:
        await update.message.reply_text("Не понял. Откройте меню: /start")
        return

    action = state.get("action")
    text = (update.message.text or "").strip()
    user = update.effective_user

    if action == "bid":
        amount = _parse_amount(text)
        if amount is None:
            await update.message.reply_text("Введите сумму числом, например: 1500")
            return
        lot_id = state["lot_id"]
        lot = db.get_lot(lot_id)
        if lot is None or not lot["active"]:
            context.user_data.pop("await", None)
            await update.message.reply_text("Лот уже недоступен.")
            return
        top = db.get_max_bid(lot_id)
        if top is not None and amount <= top:
            await update.message.reply_text(
                f"Ставка должна быть больше текущей ({top}). Попробуйте снова:"
            )
            return
        db.add_bid(lot_id, user.id, _display_name(user), amount, ANON_BIDS)
        context.user_data.pop("await", None)
        card_text, keyboard = render_lot_card(lot_id)
        await update.message.reply_text(
            f"✅ Ставка {amount} принята!\n\n{card_text}",
            parse_mode=ParseMode.HTML,
            reply_markup=keyboard,
        )
        return

    if action == "request":
        context.user_data.pop("await", None)
        await _send_request_to_admins(update, context, text)
        return

    if action == "addlot_title":
        context.user_data["await"] = {"action": "addlot_desc", "title": text}
        await update.message.reply_text("Теперь пришлите <b>описание</b> лота:",
                                        parse_mode=ParseMode.HTML)
        return

    if action == "addlot_desc":
        title = state.get("title", "Лот")
        lot_id = db.add_lot(title, text)
        context.user_data.pop("await", None)
        await update.message.reply_text(
            f"✅ Лот #{lot_id} «{title}» добавлен и активен."
        )
        return


async def _send_request_to_admins(update, context, text: str) -> None:
    user = update.effective_user
    if not ADMIN_IDS:
        await update.message.reply_text(
            "⚠️ Администратор не настроен (ADMIN_IDS). Заявка не отправлена."
        )
        return
    msg = (
        "📝 <b>Новая заявка на пост</b>\n"
        f"От: {_display_name(user)} (id {user.id})\n\n"
        f"{text}"
    )
    delivered = 0
    for admin_id in ADMIN_IDS:
        try:
            await context.bot.send_message(admin_id, msg, parse_mode=ParseMode.HTML)
            delivered += 1
        except Exception as exc:  # noqa: BLE001
            logger.warning("Не доставить заявку админу %s: %s", admin_id, exc)
    if delivered:
        await update.message.reply_text("✅ Заявка отправлена администратору. Спасибо!")
    else:
        await update.message.reply_text(
            "⚠️ Не удалось доставить заявку. Возможно, администратор ещё не запускал бота."
        )


def _parse_amount(text: str):
    digits = text.replace(" ", "").replace(" ", "")
    if digits.isdigit():
        return int(digits)
    return None


# ---------- Регистрация ----------

def register(application: Application, rules_url: str = "") -> None:
    global RULES_URL
    RULES_URL = rules_url
    db.init_db()

    application.add_handler(CommandHandler("start", cmd_start))
    application.add_handler(CommandHandler("addlot", cmd_addlot))
    application.add_handler(CommandHandler("lots", cmd_lots_admin))
    application.add_handler(CommandHandler("dellot", cmd_dellot))
    application.add_handler(CallbackQueryHandler(on_callback))
    # Текстовый ввод только в личке, чтобы не мешать обработке постов в группе.
    application.add_handler(
        MessageHandler(
            filters.ChatType.PRIVATE & filters.TEXT & ~filters.COMMAND, on_text
        )
    )
    logger.info(
        "Аукцион-меню подключено. Админы: %s",
        ", ".join(map(str, ADMIN_IDS)) or "не заданы (ADMIN_IDS пуст)",
    )
