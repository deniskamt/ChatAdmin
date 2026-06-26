"""
Интерактивная часть бота: меню в личке, ставки, заявки, админ-панель.

Главное меню (/start):
  🔨 Лоты            — список активных лотов (по 10 на страницу) → карточка
                       лота с описанием, ставками и быстрыми ставками в один тап.
  🏆 Мои ставки      — лоты, где пользователь участвует, и лидирует ли он.
  📝 Запросить пост  — заявка на свой лот уходит админам в личку.
  ℹ️ Как это работает — короткая справка.
  📜 Правила         — ссылка на правила.
  🛠 Админ-панель    — только для ADMIN_IDS: добавить/снять лоты, статистика.
"""

import logging
import os
from datetime import datetime, timedelta, timezone

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
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
QUICK_STEPS = (100, 500, 1000)  # быстрые прибавки к ставке
SEP = "➖➖➖➖➖➖➖➖➖➖➖"

MIN_DAYS = 3       # минимальный срок аукциона
MAX_DAYS = 30      # максимальный срок (≈ месяц)
DEFAULT_DAYS = 7   # срок по умолчанию

ADMIN_IDS = {
    int(x) for x in os.environ.get("ADMIN_IDS", "").replace(" ", "").split(",") if x
}

# Ссылка на правила (проставляется из bot.py при регистрации).
RULES_URL = ""


# ---------- Утилиты ----------

def is_admin(user_id: int) -> bool:
    return user_id in ADMIN_IDS


def _display_name(user) -> str:
    if user.username:
        return "@" + user.username
    return user.full_name or str(user.id)


def fmt_money(n) -> str:
    try:
        return f"{int(n):,}".replace(",", " ")
    except (TypeError, ValueError):
        return str(n)


def _min_next_bid(lot) -> int:
    """Минимально допустимая следующая ставка по лоту."""
    top = db.get_max_bid(lot["id"])
    if top is not None:
        return top + 1
    return lot["start_price"] or 1


# ---------- Время ----------

def _now() -> datetime:
    return datetime.now(timezone.utc)


def _end_in_days(days: int) -> str:
    return (_now() + timedelta(days=days)).isoformat()


def _parse_dt(value):
    if not value:
        return None
    try:
        dt = datetime.fromisoformat(str(value))
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt


def _remaining_text(ends_at) -> str:
    dt = _parse_dt(ends_at)
    if dt is None:
        return "бессрочно"
    delta = dt - _now()
    secs = int(delta.total_seconds())
    if secs <= 0:
        return "истёк"
    days, rem = divmod(secs, 86400)
    hours, rem = divmod(rem, 3600)
    minutes = rem // 60
    if days:
        return f"{days} дн. {hours} ч."
    if hours:
        return f"{hours} ч. {minutes} мин."
    return f"{minutes} мин."


def _clamp_days(days: int) -> int:
    return max(MIN_DAYS, min(MAX_DAYS, days))


# ---------- Клавиатуры ----------

def main_menu_keyboard(user_id: int) -> InlineKeyboardMarkup:
    rows = [
        [InlineKeyboardButton("🔨 Лоты", callback_data="lots:0")],
        [
            InlineKeyboardButton("🏆 Мои ставки", callback_data="mybids"),
            InlineKeyboardButton("📝 Запросить пост", callback_data="request"),
        ],
        [InlineKeyboardButton("ℹ️ Как это работает", callback_data="help")],
    ]
    if RULES_URL:
        rows.append([InlineKeyboardButton("📜 Правила", url=RULES_URL)])
    if is_admin(user_id):
        rows.append([InlineKeyboardButton("🛠 Админ-панель", callback_data="admin")])
    return InlineKeyboardMarkup(rows)


def lots_page_keyboard(page: int) -> InlineKeyboardMarkup:
    total = db.count_active_lots()
    pages = max(1, (total + PAGE_SIZE - 1) // PAGE_SIZE)
    page = max(0, min(page, pages - 1))
    offset = page * PAGE_SIZE
    lots = db.get_active_lots(PAGE_SIZE, offset)

    rows = []
    for lot in lots:
        top = db.get_max_bid(lot["id"])
        price = top if top is not None else lot["start_price"]
        rows.append([InlineKeyboardButton(
            f"#{lot['id']} · {lot['title']} — {fmt_money(price)}",
            callback_data=f"lot:{lot['id']}",
        )])

    nav = []
    if page > 0:
        nav.append(InlineKeyboardButton("◀️", callback_data=f"lots:{page - 1}"))
    nav.append(InlineKeyboardButton(f"{page + 1}/{pages}", callback_data="noop"))
    if offset + PAGE_SIZE < total:
        nav.append(InlineKeyboardButton("▶️", callback_data=f"lots:{page + 1}"))
    rows.append(nav)

    rows.append([
        InlineKeyboardButton("🔄 Обновить", callback_data=f"lots:{page}"),
        InlineKeyboardButton("🏠 Меню", callback_data="home"),
    ])
    return InlineKeyboardMarkup(rows)


def lot_card_keyboard(lot) -> InlineKeyboardMarkup:
    lot_id = lot["id"]
    base = db.get_max_bid(lot_id)
    base = base if base is not None else (lot["start_price"] or 0)
    quick = [
        InlineKeyboardButton(f"+{fmt_money(step)}", callback_data=f"qbid:{lot_id}:{step}")
        for step in QUICK_STEPS
    ]
    return InlineKeyboardMarkup([
        quick,
        [InlineKeyboardButton("✏️ Своя сумма", callback_data=f"bid:{lot_id}")],
        [
            InlineKeyboardButton("🔄 Обновить", callback_data=f"lot:{lot_id}"),
            InlineKeyboardButton("◀️ К списку", callback_data="lots:0"),
        ],
        [InlineKeyboardButton("🏠 Меню", callback_data="home")],
    ])


def back_to_menu_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([[InlineKeyboardButton("🏠 Меню", callback_data="home")]])


def anon_choice_kb(user) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [InlineKeyboardButton(f"👤 С именем ({_display_name(user)})",
                              callback_data="bidsave:pub")],
        [InlineKeyboardButton("🕵️ Анонимно", callback_data="bidsave:anon")],
        [InlineKeyboardButton("✖️ Отмена", callback_data="home")],
    ])


# ---------- Рендер карточек ----------

def render_lot_card(lot_id: int):
    lot = db.get_lot(lot_id)
    if lot is None or not lot["active"]:
        return None, None

    bids = db.get_bids(lot_id)
    lines = [f"🏷 <b>Лот #{lot['id']}: {lot['title']}</b>", SEP]
    if lot["description"]:
        lines.append(lot["description"])
        lines.append("")
    if lot["start_price"]:
        lines.append(f"🔢 Старт: <b>{fmt_money(lot['start_price'])}</b>")
    if lot["ends_at"]:
        lines.append(f"⏳ До конца: <b>{_remaining_text(lot['ends_at'])}</b>")

    if bids:
        leader = bids[0]
        who = "Аноним" if leader["anonymous"] else (leader["username"] or "Участник")
        lines.append(f"💰 Текущая ставка: <b>{fmt_money(leader['amount'])}</b>")
        lines.append(f"👑 Лидер: {who}")
        lines.append(f"📊 Всего ставок: {len(bids)}")
        lines.append(SEP)
        lines.append("<b>Ставки:</b>")
        for i, b in enumerate(bids[:10], 1):
            name = "Аноним" if b["anonymous"] else (b["username"] or "Участник")
            medal = {1: "🥇", 2: "🥈", 3: "🥉"}.get(i, f"{i}.")
            lines.append(f"{medal} {name} — {fmt_money(b['amount'])}")
        if len(bids) > 10:
            lines.append(f"…и ещё {len(bids) - 10}")
    else:
        lines.append("📭 Ставок пока нет — будьте первым!")

    lines.append(SEP)
    lines.append(f"➡️ Минимальная ставка: <b>{fmt_money(_min_next_bid(lot))}</b>")
    return "\n".join(lines), lot_card_keyboard(lot)


# ---------- /start и команды ----------

async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    context.user_data.pop("await", None)
    user = update.effective_user
    await update.message.reply_text(
        _greeting(user), parse_mode=ParseMode.HTML,
        reply_markup=main_menu_keyboard(user.id),
    )


def _greeting(user) -> str:
    return (
        f"👋 Привет, <b>{user.first_name}</b>!\n"
        "Добро пожаловать в бота аукциона.\n\n"
        "Выбирайте лоты, делайте ставки и следите за своими позициями. "
        "Выберите действие 👇"
    )


async def cmd_menu(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await cmd_start(update, context)


async def cmd_addlot(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not is_admin(update.effective_user.id):
        await update.message.reply_text("Команда доступна только администраторам.")
        return
    context.user_data["await"] = {"action": "addlot_title"}
    await update.message.reply_text("➕ <b>Новый лот.</b> Пришлите название:",
                                    parse_mode=ParseMode.HTML)


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
        f"Лот #{lot_id} снят." if ok else f"Лот #{lot_id} не найден."
    )


# ---------- Экраны (через callback) ----------

async def _show_home(query) -> None:
    await query.edit_message_text(
        "🏠 <b>Главное меню</b>\nВыберите действие:",
        parse_mode=ParseMode.HTML,
        reply_markup=main_menu_keyboard(query.from_user.id),
    )


async def _show_lots(query, page: int) -> None:
    if db.count_active_lots() == 0:
        await query.edit_message_text(
            "📭 Активных лотов пока нет.\nЗаглядывайте позже!",
            reply_markup=back_to_menu_kb(),
        )
        return
    await query.edit_message_text(
        "🔨 <b>Активные лоты</b>\nВыберите лот, чтобы посмотреть детали и ставки:",
        parse_mode=ParseMode.HTML,
        reply_markup=lots_page_keyboard(page),
    )


async def _show_lot(query, lot_id: int) -> None:
    text, keyboard = render_lot_card(lot_id)
    if text is None:
        await query.edit_message_text("Лот недоступен.", reply_markup=lots_page_keyboard(0))
        return
    await query.edit_message_text(text, parse_mode=ParseMode.HTML, reply_markup=keyboard)


async def _show_mybids(query) -> None:
    rows = db.get_user_bid_lots(query.from_user.id)
    if not rows:
        await query.edit_message_text(
            "🏆 <b>Мои ставки</b>\n\nВы ещё не делали ставок. Загляните в «🔨 Лоты»!",
            parse_mode=ParseMode.HTML, reply_markup=back_to_menu_kb(),
        )
        return
    lines = ["🏆 <b>Мои ставки</b>", SEP]
    buttons = []
    for r in rows:
        top = db.get_max_bid(r["lot_id"])
        leading = top is not None and r["my_max"] >= top
        status = "👑 лидируете" if leading else "🔻 перебили"
        closed = "" if r["active"] else " (лот закрыт)"
        lines.append(
            f"#{r['lot_id']} {r['title']} — ваша {fmt_money(r['my_max'])} · {status}{closed}"
        )
        if r["active"]:
            buttons.append([InlineKeyboardButton(
                f"#{r['lot_id']} {r['title']}", callback_data=f"lot:{r['lot_id']}")])
    buttons.append([InlineKeyboardButton("🏠 Меню", callback_data="home")])
    await query.edit_message_text(
        "\n".join(lines), parse_mode=ParseMode.HTML,
        reply_markup=InlineKeyboardMarkup(buttons),
    )


async def _show_help(query) -> None:
    text = (
        "ℹ️ <b>Как это работает</b>\n" + SEP + "\n"
        "1️⃣ Откройте «🔨 Лоты» и выберите интересующий товар.\n"
        "2️⃣ В карточке видны описание, текущая ставка и лидер.\n"
        "3️⃣ Нажмите быструю ставку (<b>+100/+500/+1000</b>) или «✏️ Своя сумма».\n"
        "4️⃣ Выберите, показать ставку <b>с именем</b> или <b>анонимно</b>.\n"
        "5️⃣ Следите за позициями в «🏆 Мои ставки».\n\n"
        "📝 Хотите выставить свой товар? Жмите «Запросить пост» — заявка уйдёт админу."
    )
    await query.edit_message_text(text, parse_mode=ParseMode.HTML,
                                  reply_markup=back_to_menu_kb())


async def _show_admin(query) -> None:
    if not is_admin(query.from_user.id):
        await query.answer("Только для администраторов.", show_alert=True)
        return
    s = db.stats()
    text = (
        "🛠 <b>Админ-панель</b>\n" + SEP + "\n"
        f"🟢 Активных лотов: <b>{s['active_lots']}</b>\n"
        f"📦 Всего лотов: <b>{s['total_lots']}</b>\n"
        f"💸 Всего ставок: <b>{s['bids']}</b>"
    )
    kb = InlineKeyboardMarkup([
        [InlineKeyboardButton("➕ Добавить лот", callback_data="adm_add")],
        [InlineKeyboardButton("📋 Управление лотами", callback_data="adm_lots")],
        [InlineKeyboardButton("🏠 Меню", callback_data="home")],
    ])
    await query.edit_message_text(text, parse_mode=ParseMode.HTML, reply_markup=kb)


async def _show_admin_lots(query) -> None:
    if not is_admin(query.from_user.id):
        return
    lots = db.list_all_lots()
    if not lots:
        await query.edit_message_text(
            "Лотов пока нет.",
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("➕ Добавить лот", callback_data="adm_add")],
                [InlineKeyboardButton("◀️ Назад", callback_data="admin")],
            ]),
        )
        return
    rows = []
    for lot in lots[:20]:
        if lot["active"]:
            rows.append([
                InlineKeyboardButton(f"✏️ #{lot['id']} {lot['title']}",
                                     callback_data=f"adm_edit:{lot['id']}"),
                InlineKeyboardButton("🗑", callback_data=f"adm_del:{lot['id']}"),
            ])
        else:
            rows.append([
                InlineKeyboardButton(f"⚪️ #{lot['id']} {lot['title']}",
                                     callback_data=f"adm_edit:{lot['id']}"),
                InlineKeyboardButton("♻️", callback_data=f"adm_restore:{lot['id']}"),
            ])
    rows.append([InlineKeyboardButton("◀️ Назад", callback_data="admin")])
    await query.edit_message_text(
        "📋 <b>Управление лотами</b>\n"
        "✏️ — редактировать · 🗑 — снять · ♻️ — вернуть.",
        parse_mode=ParseMode.HTML, reply_markup=InlineKeyboardMarkup(rows),
    )


async def _show_lot_edit(query, lot_id: int) -> None:
    if not is_admin(query.from_user.id):
        return
    lot = db.get_lot(lot_id)
    if lot is None:
        await query.edit_message_text("Лот не найден.", reply_markup=back_to_menu_kb())
        return
    status = "🟢 активен" if lot["active"] else "⚪️ снят"
    srok = _remaining_text(lot["ends_at"]) if lot["ends_at"] else "бессрочно"
    text = (
        f"✏️ <b>Редактирование лота #{lot['id']}</b>\n" + SEP + "\n"
        f"🏷 Название: {lot['title']}\n"
        f"📝 Описание: {lot['description'] or '—'}\n"
        f"🔢 Старт: {fmt_money(lot['start_price'])}\n"
        f"⏳ Срок: {srok}\n"
        f"Статус: {status}"
    )
    toggle = (
        InlineKeyboardButton("🗑 Снять", callback_data=f"adm_del:{lot_id}")
        if lot["active"]
        else InlineKeyboardButton("♻️ Вернуть", callback_data=f"adm_restore:{lot_id}")
    )
    kb = InlineKeyboardMarkup([
        [InlineKeyboardButton("🏷 Название", callback_data=f"ledit:{lot_id}:title"),
         InlineKeyboardButton("📝 Описание", callback_data=f"ledit:{lot_id}:description")],
        [InlineKeyboardButton("🔢 Старт", callback_data=f"ledit:{lot_id}:start_price"),
         InlineKeyboardButton("⏳ Срок (дни)", callback_data=f"ledit:{lot_id}:ends_at")],
        [toggle],
        [InlineKeyboardButton("◀️ К списку", callback_data="adm_lots")],
    ])
    await query.edit_message_text(text, parse_mode=ParseMode.HTML, reply_markup=kb)


# ---------- Автозакрытие по сроку ----------

async def _close_expired(bot) -> None:
    try:
        closed = db.close_expired_lots(_now().isoformat())
    except Exception as exc:  # noqa: BLE001
        logger.warning("Ошибка автозакрытия лотов: %s", exc)
        return
    for lot in closed:
        await _announce_winner(bot, lot)


async def _announce_winner(bot, lot) -> None:
    bids = db.get_bids(lot["id"])
    if bids:
        w = bids[0]
        who = "Аноним" if w["anonymous"] else (w["username"] or "Участник")
        text = (
            f"🏁 <b>Лот #{lot['id']} «{lot['title']}» закрыт!</b>\n"
            f"🏆 Победитель: {who} — <b>{fmt_money(w['amount'])}</b>"
        )
    else:
        text = f"🏁 <b>Лот #{lot['id']} «{lot['title']}» закрыт</b> без ставок."
    for admin_id in ADMIN_IDS:
        try:
            await bot.send_message(admin_id, text, parse_mode=ParseMode.HTML)
        except Exception as exc:  # noqa: BLE001
            logger.warning("Не объявить победителя админу %s: %s", admin_id, exc)


async def _expiry_job(context: ContextTypes.DEFAULT_TYPE) -> None:
    await _close_expired(context.bot)


# ---------- Роутер callback-кнопок ----------

async def on_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    data = query.data or ""
    await query.answer()

    # Перед показом — снимаем лоты с истёкшим сроком.
    await _close_expired(context.bot)

    if data == "noop":
        return
    if data == "home":
        context.user_data.pop("await", None)
        await _show_home(query)
        return
    if data == "help":
        await _show_help(query)
        return
    if data == "mybids":
        await _show_mybids(query)
        return
    if data == "request":
        context.user_data["await"] = {"action": "request"}
        await query.edit_message_text(
            "📝 <b>Заявка на пост</b>\n\nОпишите товар одним сообщением "
            "(что продаёте, состояние, желаемая цена). Заявка уйдёт администратору.",
            parse_mode=ParseMode.HTML, reply_markup=back_to_menu_kb(),
        )
        return
    if data == "admin":
        await _show_admin(query)
        return
    if data == "adm_add":
        if not is_admin(query.from_user.id):
            return
        context.user_data["await"] = {"action": "addlot_title"}
        await query.edit_message_text("➕ <b>Новый лот.</b> Пришлите название:",
                                      parse_mode=ParseMode.HTML)
        return
    if data == "adm_lots":
        await _show_admin_lots(query)
        return
    if data.startswith("adm_edit:"):
        await _show_lot_edit(query, int(data.split(":")[1]))
        return
    if data.startswith("adm_del:"):
        if not is_admin(query.from_user.id):
            return
        db.deactivate_lot(int(data.split(":")[1]))
        await _show_admin_lots(query)
        return
    if data.startswith("adm_restore:"):
        if not is_admin(query.from_user.id):
            return
        lot_id = int(data.split(":")[1])
        lot = db.get_lot(lot_id)
        # Если срок истёк (или его нет) — продлеваем на срок по умолчанию.
        dt = _parse_dt(lot["ends_at"]) if lot else None
        new_end = None if (dt and dt > _now()) else _end_in_days(DEFAULT_DAYS)
        db.reactivate_lot(lot_id, ends_at=new_end)
        await _show_admin_lots(query)
        return
    if data.startswith("ledit:"):
        if not is_admin(query.from_user.id):
            return
        _, lot_id_s, field = data.split(":")
        context.user_data["await"] = {
            "action": "edit_value", "lot_id": int(lot_id_s), "field": field,
        }
        prompts = {
            "title": "Введите новое название:",
            "description": "Введите новое описание:",
            "start_price": "Введите новую стартовую цену (число):",
            "ends_at": f"На сколько дней продлить? ({MIN_DAYS}–{MAX_DAYS}):",
        }
        await query.message.reply_text(prompts.get(field, "Введите значение:"))
        return
    if data.startswith("lots:"):
        await _show_lots(query, int(data.split(":")[1]))
        return
    if data.startswith("lot:"):
        await _show_lot(query, int(data.split(":")[1]))
        return
    if data.startswith("qbid:"):
        await _quick_bid(query, context, data)
        return
    if data.startswith("bid:"):
        lot_id = int(data.split(":")[1])
        lot = db.get_lot(lot_id)
        if lot is None or not lot["active"]:
            await query.edit_message_text("Лот недоступен.")
            return
        context.user_data["await"] = {"action": "bid", "lot_id": lot_id}
        await query.message.reply_text(
            f"✏️ Введите вашу ставку для лота #{lot_id} «{lot['title']}».\n"
            f"Минимум: <b>{fmt_money(_min_next_bid(lot))}</b>",
            parse_mode=ParseMode.HTML,
        )
        return
    if data.startswith("bidsave:"):
        await _save_bid(query, context, data.split(":")[1] == "anon")
        return


async def _quick_bid(query, context, data: str) -> None:
    _, lot_id_s, step_s = data.split(":")
    lot_id, step = int(lot_id_s), int(step_s)
    lot = db.get_lot(lot_id)
    if lot is None or not lot["active"]:
        await query.edit_message_text("Лот недоступен.")
        return
    top = db.get_max_bid(lot_id)
    base = top if top is not None else (lot["start_price"] or 0)
    amount = base + step
    context.user_data["await"] = {"action": "bid_confirm", "lot_id": lot_id, "amount": amount}
    await query.message.reply_text(
        f"Ваша ставка: <b>{fmt_money(amount)}</b> на лот #{lot_id}.\n"
        "Как показать её в списке?",
        parse_mode=ParseMode.HTML, reply_markup=anon_choice_kb(query.from_user),
    )


async def _save_bid(query, context, anonymous: bool) -> None:
    state = context.user_data.get("await")
    if not state or state.get("action") != "bid_confirm":
        await query.answer("Сессия истекла — начните ставку заново.", show_alert=True)
        return
    lot_id, amount = state["lot_id"], state["amount"]
    lot = db.get_lot(lot_id)
    if lot is None or not lot["active"]:
        context.user_data.pop("await", None)
        await query.edit_message_text("Лот уже недоступен.")
        return
    top = db.get_max_bid(lot_id)
    if top is not None and amount <= top:
        context.user_data.pop("await", None)
        await query.edit_message_text(
            f"⚠️ Кто-то уже поставил больше ({fmt_money(top)}). Сделайте ставку заново.",
            reply_markup=lot_card_keyboard(lot),
        )
        return
    db.add_bid(lot_id, query.from_user.id, _display_name(query.from_user), amount, anonymous)
    context.user_data.pop("await", None)
    card_text, keyboard = render_lot_card(lot_id)
    how = "анонимно" if anonymous else "с именем"
    await query.edit_message_text(
        f"✅ <b>Ставка {fmt_money(amount)} принята ({how})!</b>\n\n{card_text}",
        parse_mode=ParseMode.HTML, reply_markup=keyboard,
    )


# ---------- Текстовый ввод ----------

async def on_text(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    state = context.user_data.get("await")
    if not state:
        await update.message.reply_text(
            "Откройте меню командой /start 🙂", reply_markup=None
        )
        return

    action = state.get("action")
    text = (update.message.text or "").strip()
    user = update.effective_user

    if action == "bid":
        amount = _parse_amount(text)
        if amount is None:
            await update.message.reply_text("Введите сумму числом, например: 1500")
            return
        lot = db.get_lot(state["lot_id"])
        if lot is None or not lot["active"]:
            context.user_data.pop("await", None)
            await update.message.reply_text("Лот уже недоступен.")
            return
        min_next = _min_next_bid(lot)
        if amount < min_next:
            await update.message.reply_text(
                f"Ставка должна быть не меньше {fmt_money(min_next)}. Попробуйте снова:"
            )
            return
        context.user_data["await"] = {
            "action": "bid_confirm", "lot_id": lot["id"], "amount": amount,
        }
        await update.message.reply_text(
            f"Ваша ставка: <b>{fmt_money(amount)}</b> на лот #{lot['id']}.\n"
            "Как показать её в списке?",
            parse_mode=ParseMode.HTML, reply_markup=anon_choice_kb(user),
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
        context.user_data["await"] = {
            "action": "addlot_price", "title": state["title"], "desc": text,
        }
        await update.message.reply_text(
            "Укажите <b>стартовую цену</b> числом (или 0, если без неё):",
            parse_mode=ParseMode.HTML,
        )
        return

    if action == "addlot_price":
        price = _parse_amount(text) or 0
        context.user_data["await"] = {
            "action": "addlot_days", "title": state["title"],
            "desc": state["desc"], "price": price,
        }
        await update.message.reply_text(
            f"На сколько <b>дней</b> запустить аукцион? "
            f"(от {MIN_DAYS} до {MAX_DAYS}, по умолчанию {DEFAULT_DAYS}):",
            parse_mode=ParseMode.HTML,
        )
        return

    if action == "addlot_days":
        days = _parse_amount(text)
        days = _clamp_days(days) if days else DEFAULT_DAYS
        lot_id = db.add_lot(
            state["title"], state["desc"], state["price"],
            ends_at=_end_in_days(days), created_by=user.id,
        )
        context.user_data.pop("await", None)
        await update.message.reply_text(
            f"✅ Лот #{lot_id} «{state['title']}» добавлен.\n"
            f"Старт: {fmt_money(state['price'])} · срок: {days} дн.",
            reply_markup=main_menu_keyboard(user.id),
        )
        return

    if action == "edit_value":
        await _apply_lot_edit(update, context, state, text)
        return


async def _apply_lot_edit(update, context, state, text: str) -> None:
    lot_id, field = state["lot_id"], state["field"]
    if field == "start_price":
        value = _parse_amount(text)
        if value is None:
            await update.message.reply_text("Введите число, например: 1000")
            return
    elif field == "ends_at":
        days = _parse_amount(text)
        if days is None:
            await update.message.reply_text("Введите число дней, например: 7")
            return
        value = _end_in_days(_clamp_days(days))
    else:  # title / description
        value = text

    db.update_lot_field(lot_id, field, value)
    context.user_data.pop("await", None)
    names = {"title": "Название", "description": "Описание",
             "start_price": "Стартовая цена", "ends_at": "Срок"}
    await update.message.reply_text(
        f"✅ {names.get(field, field)} лота #{lot_id} обновлено.",
        reply_markup=InlineKeyboardMarkup([
            [InlineKeyboardButton("✏️ К лоту", callback_data=f"adm_edit:{lot_id}")],
            [InlineKeyboardButton("🏠 Меню", callback_data="home")],
        ]),
    )


async def _send_request_to_admins(update, context, text: str) -> None:
    user = update.effective_user
    if not ADMIN_IDS:
        await update.message.reply_text(
            "⚠️ Администратор не настроен (ADMIN_IDS). Заявка не отправлена.",
            reply_markup=back_to_menu_kb(),
        )
        return
    msg = (
        "📝 <b>Новая заявка на пост</b>\n"
        f"От: {_display_name(user)} (id <code>{user.id}</code>)\n" + SEP + "\n"
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
        await update.message.reply_text(
            "✅ Заявка отправлена администратору. Спасибо!",
            reply_markup=back_to_menu_kb(),
        )
    else:
        await update.message.reply_text(
            "⚠️ Не удалось доставить заявку (админ ещё не запускал бота).",
            reply_markup=back_to_menu_kb(),
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
    application.add_handler(CommandHandler("menu", cmd_menu))
    application.add_handler(CommandHandler("addlot", cmd_addlot))
    application.add_handler(CommandHandler("dellot", cmd_dellot))
    application.add_handler(CallbackQueryHandler(on_callback))
    application.add_handler(
        MessageHandler(
            filters.ChatType.PRIVATE & filters.TEXT & ~filters.COMMAND, on_text
        )
    )

    # Периодически закрываем лоты с истёкшим сроком и объявляем победителей.
    if application.job_queue is not None:
        application.job_queue.run_repeating(_expiry_job, interval=300, first=15)
        logger.info("Автозакрытие лотов по сроку включено (каждые 5 мин).")
    else:
        logger.warning(
            "JobQueue недоступен — лоты будут закрываться лениво, при заходе в меню. "
            "Для фонового закрытия установите python-telegram-bot[job-queue]."
        )

    logger.info(
        "Аукцион-меню подключено. Админы: %s",
        ", ".join(map(str, ADMIN_IDS)) or "не заданы (ADMIN_IDS пуст)",
    )
