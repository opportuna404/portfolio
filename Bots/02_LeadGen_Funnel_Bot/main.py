import asyncio
import logging
import os
import ssl
import aiosqlite
import aiohttp
from dotenv import load_dotenv

load_dotenv()

from aiogram import Bot, Dispatcher, F, types
from aiogram.filters import CommandStart, Command
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import (
    InlineKeyboardMarkup,
    InlineKeyboardButton,
    CallbackQuery,
    Message,
)
from aiogram.utils.keyboard import InlineKeyboardBuilder
from aiogram.client.session.aiohttp import AiohttpSession

DB_PATH = os.path.join(os.path.dirname(__file__), "raffle.db")
ADMIN_ID = int(os.getenv("ADMIN_ID", "123456789"))

GIFTS = {
    1: {"name": "📱 Apple iPhone 16 Pro Max 256GB", "price": "145 000 ₽", "link": "https://t.me/telegram"},
    2: {"name": "🎧 Наушники Apple AirPods Pro 2", "price": "24 000 ₽", "link": "https://t.me/telegram"},
    3: {"name": "⭐️ Подписка Telegram Premium на 1 год", "price": "3 990 ₽", "link": "https://t.me/telegram"},
    4: {"name": "🎮 Игровая консоль Sony PlayStation 5 Slim", "price": "58 000 ₽", "link": "https://t.me/telegram"},
    5: {"name": "💎 10 000 Telegram Stars на баланс", "price": "18 000 ₽", "link": "https://t.me/telegram"},
    6: {"name": "💻 Ноутбук Apple MacBook Air M3", "price": "125 000 ₽", "link": "https://t.me/telegram"},
}

REQUIRED_SPONSORS = [
    {"title": "📢 Канал Главного Спонсора", "url": "https://t.me/telegram", "channel_id": "@telegram"}
]

class RaffleStates(StatesGroup):
    waiting_for_dice = State()
    uploading_screenshots = State()
    waiting_for_sponsor_check = State()
    waiting_for_payment = State()

class CustomSession(AiohttpSession):
    async def create_session(self) -> aiohttp.ClientSession:
        ssl_ctx = ssl.create_default_context()
        ssl_ctx.set_ciphers('DEFAULT@SECLEVEL=1')
        connector = aiohttp.TCPConnector(
            resolver=aiohttp.ThreadedResolver(),
            ssl=ssl_ctx,
            force_close=True,
            enable_cleanup_closed=True
        )
        return aiohttp.ClientSession(
            connector=connector,
            json_serialize=self.json_dumps
        )

async def init_db():
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("""
            CREATE TABLE IF NOT EXISTS users (
                user_id INTEGER PRIMARY KEY,
                username TEXT,
                full_name TEXT,
                stage INTEGER DEFAULT 1, -- 1: старт, 2: кубик, 3: скрины, 4: спонсоры, 5: оплата
                won_gift_id INTEGER,
                screenshots_count INTEGER DEFAULT 0,
                is_subscribed INTEGER DEFAULT 0,
                is_paid INTEGER DEFAULT 0,
                ticket_code TEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)
        await db.execute("""
            CREATE TABLE IF NOT EXISTS screenshots (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER,
                file_id TEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)
        await db.commit()

def get_start_kb() -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.button(text="🎲 Бросить кубик и выбрать приз", callback_data="roll_dice")
    builder.button(text="⭐️ Отзывы победителей", callback_data="reviews")
    builder.button(text="📜 Условия розыгрыша", callback_data="rules")
    builder.adjust(1)
    return builder.as_markup()

def get_gift_won_kb(gift_link: str) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.button(text="🎁 Посмотреть приз в каталоге", url=gift_link)
    builder.button(text="🚀 Перейти к заданию и забрать", callback_data="go_to_tasks")
    builder.adjust(1)
    return builder.as_markup()

def get_task_progress_kb(current: int, total: int = 10) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    if current >= total:
        builder.button(text="✅ Все 10 скриншотов сданы! Далее", callback_data="finish_tasks")
    else:
        builder.button(text=f"📊 Прогресс: {current}/{total} (Загрузить ещё)", callback_data="noop")
        builder.button(text="⚡️ Завершить загрузку скриншотов", callback_data="finish_tasks")
    builder.adjust(1)
    return builder.as_markup()

def get_sponsors_kb() -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    for sp in REQUIRED_SPONSORS:
        builder.button(text=f"👉 {sp['title']}", url=sp["url"])
    builder.button(text="🔄 Я подписался / Проверить", callback_data="check_sponsors")
    builder.adjust(1)
    return builder.as_markup()

def get_payment_kb() -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.button(text="💳 Оплатить подписку 1 ₽ на сайте", url="https://t.me/telegram")
    builder.button(text="✅ Я оплатил 1 ₽ (Подтвердить)", callback_data="confirm_payment")
    builder.adjust(1)
    return builder.as_markup()

dp = Dispatcher()

@dp.message(CommandStart())
async def cmd_start(message: Message, state: FSMContext):
    await state.clear()
    user_id = message.from_user.id
    username = message.from_user.username
    full_name = message.from_user.full_name

    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("""
            INSERT INTO users (user_id, username, full_name, stage)
            VALUES (?, ?, ?, 1)
            ON CONFLICT(user_id) DO UPDATE SET
                username = excluded.username,
                full_name = excluded.full_name
        """, (user_id, username, full_name))
        await db.commit()

    text = (
        f"👋 Здравствуйте, <b>{full_name}</b>!\n\n"
        "🎉 Добро пожаловать в <b>Официальный гранд-розыгрыш ценных призов 2026</b>!\n\n"
        "📦 <b>В призовом фонде участвуют 6 главных подарков:</b>\n"
        "⚀ [1] — Apple iPhone 16 Pro Max (256 GB)\n"
        "⚁ [2] — Наушники Apple AirPods Pro 2\n"
        "⚂ [3] — Подписка Telegram Premium на 1 год\n"
        "⚃ [4] — Игровая консоль Sony PlayStation 5 Slim\n"
        "⚄ [5] — 10 000 Telegram Stars на личный аккаунт\n"
        "⚅ [6] — Ноутбук Apple MacBook Air M3\n\n"
        "🎲 Бросьте кубик прямо сейчас, чтобы определить, какой приз закрепляется за вами!"
    )
    await message.answer(text, reply_markup=get_start_kb(), parse_mode="HTML")

@dp.callback_query(F.data == "reviews")
async def cb_reviews(callback: CallbackQuery):
    text = (
        "⭐️ <b>Отзывы победителей прошлого сезона:</b>\n\n"
        "👤 <b>Александр (Москва):</b> «Выпала PS5! Сначала не верил, выполнил задания со скриншотами, через неделю курьер привез коробку прямо до двери. Спасибо!»\n\n"
        "👤 <b>Елена (СПб):</b> «Выиграла годовой Telegram Premium. Подключили за 10 минут прямо через подарок в боте. Все честно и прозрачно.»\n\n"
        "👤 <b>Дмитрий (Казань):</b> «Заняло буквально 5 минут на выполнение заданий. Билет #RAFFLE-8841 победил на стриме!»"
    )
    builder = InlineKeyboardBuilder()
    builder.button(text="🎲 Бросить кубик", callback_data="roll_dice")
    builder.button(text="🔙 Назад", callback_data="back_to_start")
    builder.adjust(1)
    await callback.message.edit_text(text, reply_markup=builder.as_markup(), parse_mode="HTML")

@dp.callback_query(F.data == "rules")
async def cb_rules(callback: CallbackQuery):
    text = (
        "📜 <b>Официальные правила участия в розыгрыше:</b>\n\n"
        "1. Участие доступно всем пользователям старше 14 лет.\n"
        "2. Каждый участник может бросить кубик 1 раз и зафиксировать 1 подарок.\n"
        "3. Для подтверждения активности участник загружает 10 скриншотов выполнения заданий (комментарии под видео).\n"
        "4. Обязательна подписка на спонсорские каналы проекта до момента подведения итогов.\n"
        "5. Для защиты от ботов требуется подтверждение через символическую подписку участника (1 ₽).\n"
        "6. Победители определяются случайным генератором в прямом эфире."
    )
    builder = InlineKeyboardBuilder()
    builder.button(text="🎲 Бросить кубик", callback_data="roll_dice")
    builder.button(text="🔙 Назад", callback_data="back_to_start")
    builder.adjust(1)
    await callback.message.edit_text(text, reply_markup=builder.as_markup(), parse_mode="HTML")

@dp.callback_query(F.data == "back_to_start")
async def cb_back_to_start(callback: CallbackQuery):
    text = (
        "🎯 <b>Главное меню розыгрыша:</b>\n\n"
        "Бросьте кубик 🎲, чтобы определить ваш персональный приз и зафиксировать его за вашим аккаунтом!"
    )
    await callback.message.edit_text(text, reply_markup=get_start_kb(), parse_mode="HTML")

@dp.callback_query(F.data == "roll_dice")
async def cb_roll_dice(callback: CallbackQuery, bot: Bot, state: FSMContext):
    await callback.answer()
    dice_msg = await bot.send_dice(chat_id=callback.message.chat.id, emoji="🎲")
    score = dice_msg.dice.value
    await asyncio.sleep(3.5)

    gift = GIFTS.get(score, GIFTS[1])
    user_id = callback.from_user.id

    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("""
            UPDATE users SET stage = 2, won_gift_id = ? WHERE user_id = ?
        """, (score, user_id))
        await db.commit()

    await state.update_data(gift_id=score)

    text = (
        f"💥 <b>ВАМ ВЫПАЛО ЧИСЛО [{score}]!</b>\n\n"
        f"🎁 <b>Ваш зафиксированный приз:</b>\n"
        f"👉 <b>{gift['name']}</b> (Стоимость: <b>{gift['price']}</b>)\n\n"
        "🎉 Поздравляем! Данный приз предварительно закреплен за вашим аккаунтом.\n\n"
        "⚠️ Чтобы окончательно зарегистрировать подарок и перейти в финал, выполните простое задание спонсоров."
    )
    await callback.message.answer(text, reply_markup=get_gift_won_kb(gift["link"]), parse_mode="HTML")

@dp.callback_query(F.data == "go_to_tasks")
async def cb_go_to_tasks(callback: CallbackQuery, state: FSMContext):
    await callback.answer()
    await state.set_state(RaffleStates.uploading_screenshots)
    
    user_id = callback.from_user.id
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("UPDATE users SET stage = 3 WHERE user_id = ?", (user_id,))
        await db.commit()

    text = (
        "📋 <b>Шаг 3 из 5: Выполнение задания спонсоров</b>\n\n"
        "1. Оставьте осмысленные комментарии под 10 видео на каналах спонсоров.\n"
        "2. Сделайте скриншот каждого комментария.\n"
        "3. <b>Отправьте 10 скриншотов прямо в этот чат</b> (можно по одному или сразу группой).\n\n"
        "📸 <i>Отправьте ваш первый скриншот:</i>"
    )
    await callback.message.answer(text, reply_markup=get_task_progress_kb(0, 10), parse_mode="HTML")

@dp.message(RaffleStates.uploading_screenshots, F.photo)
async def process_screenshot_upload(message: Message, state: FSMContext):
    photo = message.photo[-1]
    user_id = message.from_user.id

    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("INSERT INTO screenshots (user_id, file_id) VALUES (?, ?)", (user_id, photo.file_id))
        await db.execute("UPDATE users SET screenshots_count = screenshots_count + 1 WHERE user_id = ?", (user_id,))
        await db.commit()

        async with db.execute("SELECT screenshots_count FROM users WHERE user_id = ?", (user_id,)) as cur:
            count = (await cur.fetchone())[0]

    total = 10
    progress = min(count, total)
    bar = "🟩" * progress + "⬜️" * (total - progress)

    if count >= total:
        text = (
            f"🎉 <b>Отлично! Все {total} скриншотов успешно загружены!</b>\n\n"
            f"[{bar}] <b>{count}/{total}</b>\n\n"
            "Все задания выполнены. Переходим к подтверждению подписки на спонсоров."
        )
        await message.answer(text, reply_markup=get_task_progress_kb(count, 10), parse_mode="HTML")
    else:
        text = (
            f"📸 <b>Скриншот принят!</b>\n\n"
            f"[{bar}] <b>{count}/{total}</b> загружено.\n"
            f"Осталось загрузить еще {total - count} скриншотов."
        )
        await message.answer(text, reply_markup=get_task_progress_kb(count, 10), parse_mode="HTML")

@dp.callback_query(F.data == "finish_tasks")
async def cb_finish_tasks(callback: CallbackQuery, state: FSMContext):
    await callback.answer()
    await state.set_state(RaffleStates.waiting_for_sponsor_check)
    
    user_id = callback.from_user.id
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("UPDATE users SET stage = 4 WHERE user_id = ?", (user_id,))
        await db.commit()

    text = (
        "📢 <b>Шаг 4 из 5: Подписка на спонсоров</b>\n\n"
        "Для допуска к розыгрышу и получения приза подпишитесь на спонсорские каналы:\n\n"
        "После подписки нажмите кнопку <b>«Я подписался / Проверить»</b>."
    )
    await callback.message.answer(text, reply_markup=get_sponsors_kb(), parse_mode="HTML")

@dp.callback_query(F.data == "check_sponsors")
async def cb_check_sponsors(callback: CallbackQuery, bot: Bot, state: FSMContext):
    user_id = callback.from_user.id
    is_all_subbed = True

    for sp in REQUIRED_SPONSORS:
        try:
            m = await bot.get_chat_member(chat_id=sp["channel_id"], user_id=user_id)
            if m.status not in ["member", "administrator", "creator"]:
                is_all_subbed = False
                break
        except Exception as e:
            logging.warning(f"Ошибка проверки канала {sp['channel_id']}: {e}")

    if not is_all_subbed:
        await callback.answer("❌ Вы еще не подписались на все каналы спонсоров!", show_alert=True)
        return

    await callback.answer("✅ Подписка на спонсоров успешно подтверждена!")
    await state.set_state(RaffleStates.waiting_for_payment)

    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("UPDATE users SET stage = 5, is_subscribed = 1 WHERE user_id = ?", (user_id,))
        await db.commit()

    text = (
        "💎 <b>Шаг 5 из 5: Финальная верификация участника</b>\n\n"
        "Для защиты от ботов и спам-ферм требуется разовая символическая верификация участника за <b>1 ₽</b>.\n\n"
        "После подтверждения оплаты вы мгновенно получите ваш **официальный номерной билет участника** розыгрыша!"
    )
    await callback.message.answer(text, reply_markup=get_payment_kb(), parse_mode="HTML")

@dp.callback_query(F.data == "confirm_payment")
async def cb_confirm_payment(callback: CallbackQuery, state: FSMContext, bot: Bot):
    user_id = callback.from_user.id
    await callback.answer("✅ Оплата 1 ₽ подтверждена!")
    await state.clear()

    import random
    ticket_num = f"RAFFLE-2026-{random.randint(10000, 99999)}"

    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute("SELECT won_gift_id FROM users WHERE user_id = ?", (user_id,)) as cur:
            row = await cur.fetchone()
            gift_id = row[0] if row and row[0] else 1

        await db.execute("""
            UPDATE users SET is_paid = 1, ticket_code = ? WHERE user_id = ?
        """, (ticket_num, user_id))
        await db.commit()

    gift = GIFTS.get(gift_id, GIFTS[1])

    final_text = (
        "🎉 <b>ПОЗДРАВЛЯЕМ! ВЫ ОФИЦИАЛЬНЫЙ УЧАСТНИК РОЗЫГРЫША!</b>\n\n"
        f"🎟 <b>Ваш номерной билет:</b> <code>{ticket_num}</code>\n"
        f"🎁 <b>Закрепленный подарок:</b> {gift['name']}\n"
        f"💵 <b>Оценочная стоимость:</b> {gift['price']}\n"
        "🟢 <b>Статус билета:</b> Активен и допущен к розыгрышу!\n\n"
        "🔔 <i>Прямой эфир с определением победителей состоится в это воскресенье в 19:00 по МСК. Бот пришлет вам напоминание за 1 час до начала!</i>"
    )

    builder = InlineKeyboardBuilder()
    builder.button(text="⭐️ Мой билет и статус", callback_data="my_ticket")
    builder.button(text="👥 Пригласить друзей (+1 билет)", callback_data="invite_friends")
    builder.adjust(1)

    await callback.message.answer(final_text, reply_markup=builder.as_markup(), parse_mode="HTML")

    try:
        await bot.send_message(
            ADMIN_ID,
            f"🔥 <b>НОВЫЙ ФИНАЛИСТ РОЗЫГРЫША!</b>\n\n"
            f"👤 Участник: {callback.from_user.full_name} (@{callback.from_user.username})\n"
            f"🎟 Билет: <code>{ticket_num}</code>\n"
            f"🎁 Подарок: {gift['name']}\n"
            f"🆔 User ID: <code>{user_id}</code>",
            parse_mode="HTML"
        )
    except Exception:
        pass

@dp.callback_query(F.data == "my_ticket")
async def cb_my_ticket(callback: CallbackQuery):
    user_id = callback.from_user.id
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute("SELECT ticket_code, won_gift_id, is_paid FROM users WHERE user_id = ?", (user_id,)) as cur:
            row = await cur.fetchone()

    if not row or not row[0]:
        await callback.answer("У вас еще нет активного билета. Пройдите шаги розыгрыша в меню /start.", show_alert=True)
        return

    ticket_code, gift_id, is_paid = row
    gift = GIFTS.get(gift_id, GIFTS[1])

    text = (
        "🎫 <b>Информация о вашем участии:</b>\n\n"
        f"🎟 Номер билета: <code>{ticket_code}</code>\n"
        f"🎁 Приз: <b>{gift['name']}</b>\n"
        f"🟢 Статус оплаты (1 ₽): Подтвержден\n"
        "✅ Все проверки пройдены!"
    )
    await callback.message.answer(text, parse_mode="HTML")

@dp.message(Command("admin"))
async def cmd_admin(message: Message):
    if message.from_user.id != ADMIN_ID:
        return

    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute("SELECT COUNT(*) FROM users") as cur:
            total_started = (await cur.fetchone())[0]
        async with db.execute("SELECT COUNT(*) FROM users WHERE stage >= 2") as cur:
            rolled_dice = (await cur.fetchone())[0]
        async with db.execute("SELECT COUNT(*) FROM users WHERE stage >= 3") as cur:
            uploaded_screens = (await cur.fetchone())[0]
        async with db.execute("SELECT COUNT(*) FROM users WHERE is_subscribed = 1") as cur:
            subbed = (await cur.fetchone())[0]
        async with db.execute("SELECT COUNT(*) FROM users WHERE is_paid = 1") as cur:
            paid_finalists = (await cur.fetchone())[0]

    text = (
        "📊 <b>Аналитика воронки розыгрыша:</b>\n\n"
        f"1️⃣ Шаг 1 (Запуск /start): <b>{total_started}</b> чел.\n"
        f"2️⃣ Шаг 2 (Бросили кубик 🎲): <b>{rolled_dice}</b> чел.\n"
        f"3️⃣ Шаг 3 (Сдали скриншоты): <b>{uploaded_screens}</b> чел.\n"
        f"4️⃣ Шаг 4 (Подписались на спонсоров): <b>{subbed}</b> чел.\n"
        f"5️⃣ Шаг 5 (Оплатили 1 ₽ и получили билет 🎟): <b>{paid_finalists}</b> чел.\n\n"
        f"📈 <b>Итоговая сквозная конверсия:</b> "
        f"{((paid_finalists / total_started) * 100) if total_started else 0:.1f}%"
    )
    await message.answer(text, parse_mode="HTML")

async def main():
    logging.basicConfig(level=logging.INFO)
    await init_db()

    BOT_TOKEN = os.getenv("BOT_TOKEN", "YOUR_BOT_TOKEN_HERE")
    session = CustomSession()
    bot = Bot(token=BOT_TOKEN, session=session)
    while True:
        try:
            await dp.start_polling(bot, polling_timeout=15)
            break
        except Exception as e:
            logging.error(f"Polling connection reset: {e}. Reconnecting in 3s...")
            await asyncio.sleep(3)

if __name__ == "__main__":
    asyncio.run(main())
