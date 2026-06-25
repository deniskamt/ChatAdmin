"""
Одноразовый скрипт: публикует правила чата в Telegraph и печатает ссылку.

Запуск:
    python create_telegraph.py

Полученный URL вставьте в переменную RULES_URL (в .env или в настройках Railway).
Telegraph не требует токена бота — это отдельный публичный сервис Telegram.
"""

import json
import urllib.parse
import urllib.request

API = "https://api.telegra.ph/"


def call(method: str, params: dict) -> dict:
    data = urllib.parse.urlencode(params).encode()
    req = urllib.request.Request(API + method, data=data)
    with urllib.request.urlopen(req, timeout=30) as resp:
        result = json.load(resp)
    if not result.get("ok"):
        raise SystemExit(f"Telegraph API error: {result}")
    return result["result"]


def _p(*children) -> dict:
    return {"tag": "p", "children": list(children)}


def _b(text: str) -> dict:
    return {"tag": "b", "children": [text]}


# Содержимое страницы в формате Telegraph (Node).
CONTENT = [
    _p(
        "Это чат для обсуждения лотов аукциона. ",
        _b("Сам аукцион здесь не проводится"),
        " — ставки, оплата и сделки происходят не в комментариях. "
        "Здесь можно делиться мнением о вещах, задавать вопросы и общаться.",
    ),
    _p(_b("1. Будьте вежливы. "),
       "Уважайте других участников. Запрещены оскорбления, переход на личности, "
       "травля и разжигание конфликтов."),
    _p(_b("2. По теме. "),
       "Пишите по поводу лота: впечатления, вопросы, оценка состояния, "
       "адекватность цены. Оффтоп и флуд не приветствуются."),
    _p(_b("3. Без спама и рекламы. "),
       "Запрещены реклама, ссылки на сторонние магазины, сбор подписчиков "
       "и рассылки без согласования с админами."),
    _p(_b("4. Не вводите в заблуждение. "),
       "Не выдавайте слухи за факты и не давайте заведомо ложную информацию о вещах."),
    _p(_b("5. Аукцион идёт не здесь. "),
       "Комментарии — это обсуждение, а не ставки. Условия участия, оплаты "
       "и доставки уточняйте у организаторов."),
    _p(_b("6. Запрещён контент 18+, мошенничество и нарушения закона. "),
       "Попрошайничество, фишинг и обман участников ведут к бану."),
    _p(_b("7. Решения админов окончательны. "),
       "Модерация может удалять сообщения и ограничивать доступ нарушителям "
       "без предупреждения."),
    {"tag": "hr"},
    _p("Оставляя комментарий, вы соглашаетесь с этими правилами. "
       "Спасибо, что делаете обсуждение приятным для всех 🤝"),
]


def main() -> None:
    url, token = publish_rules()
    print("\n✅ Страница правил создана!")
    print("RULES_URL =", url)
    print("\nВставьте этот URL в переменную RULES_URL (в .env или на Railway).")
    print("Токен для будущего редактирования (сохраните, если нужно):", token)


def publish_rules(title: str = "Правила чата", author: str = "Аукцион") -> tuple[str, str]:
    """Создаёт страницу правил в Telegraph. Возвращает (url, access_token)."""
    account = call("createAccount", {"short_name": "ChatAdmin", "author_name": author})
    token = account["access_token"]
    page = call("createPage", {
        "access_token": token,
        "title": title,
        "author_name": author,
        "content": json.dumps(CONTENT, ensure_ascii=False),
        "return_content": "false",
    })
    return page["url"], token


if __name__ == "__main__":
    main()
