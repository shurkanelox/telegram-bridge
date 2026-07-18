# Мост Telegram (много людей) -> MAX (один чат) -> ответы обратно

Сценарий:
- Любой человек пишет твоему TG-боту в личку.
- Все такие сообщения стекаются в один MAX-чат — каждое подписано,
  от кого оно (имя, @username, ID).
- Чтобы ответить конкретному человеку — сделай **Reply** (ответ) прямо
  на его сообщение в MAX-чате. Бот сам поймёт, кому это переслать.
- Кто есть кто — хранится в файле `bridge.db` (SQLite) рядом со скриптом
  и переживает перезапуск бота.

Работает через long polling — вебхук и публичный домен не нужны.

## 1. Создай ботов

### Telegram
1. [@BotFather](https://t.me/BotFather) → `/newbot` → получишь `TELEGRAM_BOT_TOKEN`.
2. Больше ничего не нужно — бот работает в личках, там privacy mode не мешает.

### MAX
1. Открой диалог с **MasterBot** в приложении MAX → получишь `MAX_BOT_TOKEN`.
2. Добавь бота в чат, где ты будешь видеть все сообщения и отвечать —
   и **выдай ему права администратора** этого чата.

## 2. Установка

```bash
python3 -m venv venv
source venv/bin/activate        # Windows: venv\Scripts\activate
pip install -r requirements.txt
cp .env.example .env
```

Впиши в `.env` `TELEGRAM_BOT_TOKEN` и `MAX_BOT_TOKEN`. `MAX_CHAT_ID` пока оставь пустым.

## 3. Узнай ID MAX-чата

```bash
python bridge.py
```

Напиши что-нибудь в MAX-чат, где добавлен бот. В консоли появится:

```
Пришло сообщение из MAX-чата с ID: 987654321
```

Останови (`Ctrl+C`), впиши ID в `.env` → `MAX_CHAT_ID`, запусти снова.

## 4. Как это использовать

- Попроси людей написать твоему TG-боту `/start` и любое сообщение — оно
  придёт тебе в MAX-чат с подписью, от кого.
- Чтобы ответить — **зажми/выбери Reply** на конкретное сообщение в MAX и
  напиши текст (или прикрепи фото/файл). Ответ уйдёт именно этому человеку
  в личку в Telegram.
- Если отправить сообщение в MAX-чат **без Reply** — бот вежливо напомнит,
  что нужно ответить конкретному сообщению.

## 5. Деплой на бесплатный хостинг

Бот работает через polling — подходит любой хостинг, который не «усыпляет»
процесс. Условия бесплатных тарифов часто меняются, стоит свериться перед
деплоем:

- **Google Compute Engine** (`e2-micro`, always-free тариф в некоторых
  регионах США) — самый предсказуемый вариант, обычная VM.
- Railway / Render (background worker) / Fly.io — быстрее разворачивать,
  но бесплатные лимиты урезаны и периодически меняются.

### Google Compute Engine + systemd

```bash
sudo apt update && sudo apt install -y python3-venv
cd tg-max-bridge
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
nano .env   # впиши токены и MAX_CHAT_ID
```

`/etc/systemd/system/tg-max-bridge.service`:

```ini
[Unit]
Description=Telegram-MAX bridge bot
After=network.target

[Service]
Type=simple
WorkingDirectory=/home/<твой_пользователь>/tg-max-bridge
ExecStart=/home/<твой_пользователь>/tg-max-bridge/venv/bin/python bridge.py
Restart=always
RestartSec=5

[Install]
WantedBy=multi-user.target
```

```bash
sudo systemctl daemon-reload
sudo systemctl enable --now tg-max-bridge
sudo journalctl -u tg-max-bridge -f
```

**Важно:** файл `bridge.db` (в нём хранится соответствие "кто есть кто")
должен оставаться на диске между перезапусками — не удаляй его и не сбрасывай
рабочую директорию при деплое.

## Возможные проблемы

- **"Не нашёл, кому переслать"** — либо ответили не на то сообщение, либо
  запись успела потеряться (например, `bridge.db` был удалён). Попроси
  человека написать ещё раз.
- **MAX не даёт вытащить ID отправленного сообщения** (в логе предупреждение
  про `_extract_mid`) — библиотека `maxapi` молодая, структура ответа может
  отличаться в твоей версии. Временно замени `sent = await max_bot.send_message(...)`
  на `print(sent)` / `print(vars(sent))`, посмотри реальные поля и поправь
  функцию `_extract_mid` в `bridge.py` под них.
- **MAX-бот не видит сообщения** — проверь права администратора чата у бота.
- Стикеры, голосовые, геолокация пока не пересылаются — добавляются по
  аналогии с фото/документами.
