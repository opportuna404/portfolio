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
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton, CallbackQuery, Message
from aiogram.utils.keyboard import InlineKeyboardBuilder
from aiogram.client.session.aiohttp import AiohttpSession

DB_PATH = os.path.join(os.path.dirname(__file__), "shop.db")
ADMIN_ID = int(os.getenv("ADMIN_ID", "123456789"))

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
                balance INTEGER DEFAULT 0,
                joined_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)
        await db.execute("""
            CREATE TABLE IF NOT EXISTS categories (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT NOT NULL
            )
        """)
        await db.execute("""
            CREATE TABLE IF NOT EXISTS products (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                category_id INTEGER,
                title TEXT NOT NULL,
                description TEXT,
                price INTEGER NOT NULL,
                content TEXT NOT NULL, -- ключ/файл/данные для выдачи
                is_sold INTEGER DEFAULT 0,
                FOREIGN KEY (category_id) REFERENCES categories (id)
            )
        """)
        await db.execute("""
            CREATE TABLE IF NOT EXISTS orders (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER,
                product_id INTEGER,
                product_title TEXT,
                price INTEGER,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)
        

        async with db.execute("SELECT COUNT(*) FROM categories") as cursor:
            count = (await cursor.fetchone())[0]
            if count == 0:
                await db.execute("INSERT INTO categories (name) VALUES ('🔑 VPN Подписки')")
                await db.execute("INSERT INTO categories (name) VALUES ('🎮 Игровые ключи')")
                await db.execute("INSERT INTO categories (name) VALUES ('🤖 AI Доступы')")
                

                await db.execute("""
                    INSERT INTO products (category_id, title, description, price, content) 
                    VALUES (1, 'Outline VPN (1 мес)', 'Быстрый доступ, безлимитный трафик, 100 Мбит/с', 150, 'ss://Y2hhY2hhMjAtaWV0Zi1wb2x5MTMwNTpNeUtleTEyMw@194.87.1.1:443/?outline=1')
                """)
                await db.execute("""
                    INSERT INTO products (category_id, title, description, price, content) 
                    VALUES (1, 'VLESS Shadowsocks (3 мес)', 'Протокол против блокировок, локация Нидерланды', 400, 'vless://uuid-test-key@nl.server.com:443?type=tcp')
                """)
                await db.execute("""
                    INSERT INTO products (category_id, title, description, price, content) 
                    VALUES (3, 'ChatGPT Plus Shared', 'Общий аккаунт с активной подпиской GPT-4o', 350, 'Логин: user_gpt@mail.com\nПароль: GPT_Secure_2026')
                """)
        await db.commit()

def get_main_menu(is_admin=False):
    builder = InlineKeyboardBuilder()
    builder.button(text="🛍 Каталог товаров", callback_data="catalog")
    builder.button(text="👤 Мой профиль", callback_data="profile")
    builder.button(text="ℹ️ О магазине / Поддержка", callback_data="about")
    if is_admin:
        builder.button(text="⚙️ Админ-панель", callback_data="admin_panel")
    builder.adjust(1)
    return builder.as_markup()

async def get_categories_kb():
    builder = InlineKeyboardBuilder()
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute("SELECT id, name FROM categories") as cursor:
            rows = await cursor.fetchall()
            for cat_id, cat_name in rows:
                builder.button(text=cat_name, callback_data=f"cat_{cat_id}")
    builder.button(text="🔙 В главное меню", callback_data="main_menu")
    builder.adjust(1)
    return builder.as_markup()

async def get_products_kb(category_id: int):
    builder = InlineKeyboardBuilder()
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute("SELECT id, title, price FROM products WHERE category_id = ? AND is_sold = 0", (category_id,)) as cursor:
            rows = await cursor.fetchall()
            for prod_id, title, price in rows:
                builder.button(text=f"{title} — {price} ₽", callback_data=f"prod_{prod_id}")
    builder.button(text="🔙 К категориям", callback_data="catalog")
    builder.adjust(1)
    return builder.as_markup()

dp = Dispatcher()

@dp.message(CommandStart())
async def cmd_start(message: Message):
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("INSERT OR IGNORE INTO users (user_id, username) VALUES (?, ?)", 
                         (message.from_user.id, message.from_user.username))
        await db.commit()
    
    is_admin = message.from_user.id == ADMIN_ID
    await message.answer(
        f"👋 Добро пожаловать, {message.from_user.first_name}!\n\n"
        "🛍 Это автоматизированный магазин цифровых товаров.\n"
        "Выдача ключей и доступов происходит мгновенно после оплаты 24/7.",
        reply_markup=get_main_menu(is_admin)
    )

@dp.callback_query(F.data == "main_menu")
async def cb_main_menu(callback: CallbackQuery):
    is_admin = callback.from_user.id == ADMIN_ID
    await callback.message.edit_text(
        "Главное меню магазина:",
        reply_markup=get_main_menu(is_admin)
    )

@dp.callback_query(F.data == "catalog")
async def cb_catalog(callback: CallbackQuery):
    kb = await get_categories_kb()
    await callback.message.edit_text("📂 Выберите категорию товаров:", reply_markup=kb)

@dp.callback_query(F.data.startswith("cat_"))
async def cb_category(callback: CallbackQuery):
    cat_id = int(callback.data.split("_")[1])
    kb = await get_products_kb(cat_id)
    await callback.message.edit_text("🛒 Доступные товары в категории:", reply_markup=kb)

@dp.callback_query(F.data.startswith("prod_"))
async def cb_product(callback: CallbackQuery):
    prod_id = int(callback.data.split("_")[1])
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute("SELECT title, description, price, category_id FROM products WHERE id = ?", (prod_id,)) as cursor:
            row = await cursor.fetchone()
            if not row:
                await callback.answer("Товар распродан!", show_alert=True)
                return
            title, desc, price, cat_id = row

    builder = InlineKeyboardBuilder()
    builder.button(text=f"💳 Купить за {price} ₽", callback_data=f"buy_{prod_id}")
    builder.button(text="🔙 Назад", callback_data=f"cat_{cat_id}")
    builder.adjust(1)

    text = (
        f"📦 <b>{title}</b>\n\n"
        f"📝 <b>Описание:</b>\n{desc}\n\n"
        f"💰 <b>Цена:</b> {price} ₽\n"
        f"⚡️ <i>Выдача товара сразу после нажатия кнопки покупки.</i>"
    )
    await callback.message.edit_text(text, parse_mode="HTML", reply_markup=builder.as_markup())

@dp.callback_query(F.data.startswith("buy_"))
async def cb_buy(callback: CallbackQuery):
    prod_id = int(callback.data.split("_")[1])
    user_id = callback.from_user.id

    async with aiosqlite.connect(DB_PATH) as db:

        async with db.execute("SELECT title, price, content, is_sold FROM products WHERE id = ?", (prod_id,)) as cursor:
            product = await cursor.fetchone()
            if not product or product[3] == 1:
                await callback.answer("❌ Товар уже куплен другим пользователем!", show_alert=True)
                return
            title, price, content, _ = product

        async with db.execute("SELECT balance FROM users WHERE user_id = ?", (user_id,)) as cursor:
            user = await cursor.fetchone()
            balance = user[0] if user else 0

        if balance < price:
            builder = InlineKeyboardBuilder()
            builder.button(text=f"➕ Пополнить баланс на {price - balance} ₽ (Тест)", callback_data=f"add_bal_{price}")
            builder.button(text="🔙 В меню", callback_data="main_menu")
            builder.adjust(1)
            await callback.message.edit_text(
                f"⚠️ Недостаточно средств на балансе.\n\n"
                f"Стоимость товара: {price} ₽\n"
                f"Ваш баланс: {balance} ₽",
                reply_markup=builder.as_markup()
            )
            return

        await db.execute("UPDATE users SET balance = balance - ? WHERE user_id = ?", (price, user_id))
        await db.execute("UPDATE products SET is_sold = 1 WHERE id = ?", (prod_id,))
        await db.execute("INSERT INTO orders (user_id, product_id, product_title, price) VALUES (?, ?, ?, ?)",
                         (user_id, prod_id, title, price))
        await db.commit()

    success_text = (
        f"✅ <b>Успешная покупка!</b>\n\n"
        f"📦 Товар: <b>{title}</b>\n"
        f"💵 Списано: <b>{price} ₽</b>\n\n"
        f"🔑 <b>Ваши данные для доступа:</b>\n"
        f"<code>{content}</code>\n\n"
        f"<i>Данные также всегда доступны в вашем профиле.</i>"
    )
    builder = InlineKeyboardBuilder()
    builder.button(text="🔙 Главное меню", callback_data="main_menu")
    await callback.message.edit_text(success_text, parse_mode="HTML", reply_markup=builder.as_markup())

@dp.callback_query(F.data.startswith("add_bal_"))
async def cb_add_balance(callback: CallbackQuery):
    amount = int(callback.data.split("_")[2])
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("UPDATE users SET balance = balance + ? WHERE user_id = ?", (amount, callback.from_user.id))
        await db.commit()
    await callback.answer(f"✅ Баланс успешно пополнен на {amount} ₽!", show_alert=True)
    await cb_catalog(callback)

@dp.callback_query(F.data == "profile")
async def cb_profile(callback: CallbackQuery):
    user_id = callback.from_user.id
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute("SELECT balance, joined_at FROM users WHERE user_id = ?", (user_id,)) as cursor:
            user = await cursor.fetchone()
            balance, joined = user if user else (0, "Сегодня")

        async with db.execute("SELECT COUNT(*), SUM(price) FROM orders WHERE user_id = ?", (user_id,)) as cursor:
            orders_info = await cursor.fetchone()
            orders_count = orders_info[0] or 0
            spent = orders_info[1] or 0

    builder = InlineKeyboardBuilder()
    builder.button(text="💳 Пополнить баланс", callback_data="add_bal_500")
    builder.button(text="📦 Мои покупки", callback_data="my_orders")
    builder.button(text="🔙 Назад", callback_data="main_menu")
    builder.adjust(1)

    profile_text = (
        f"👤 <b>Личный кабинет</b>\n\n"
        f"🆔 Ваш ID: <code>{user_id}</code>\n"
        f"💰 Баланс: <b>{balance} ₽</b>\n"
        f"🛍 Всего покупок: <b>{orders_count} шт.</b> (на сумму {spent} ₽)\n"
        f"📅 С нами с: {joined[:10]}"
    )
    await callback.message.edit_text(profile_text, parse_mode="HTML", reply_markup=builder.as_markup())

@dp.callback_query(F.data == "my_orders")
async def cb_my_orders(callback: CallbackQuery):
    user_id = callback.from_user.id
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute("""
            SELECT o.product_title, o.price, o.created_at, p.content 
            FROM orders o 
            LEFT JOIN products p ON o.product_id = p.id 
            WHERE o.user_id = ? ORDER BY o.id DESC LIMIT 5
        """, (user_id,)) as cursor:
            rows = await cursor.fetchall()

    if not rows:
        await callback.answer("У вас пока нет покупок.", show_alert=True)
        return

    text = "📋 <b>Последние покупки:</b>\n\n"
    for title, price, date, content in rows:
        text += f"🔹 <b>{title}</b> ({price} ₽) — {date[:10]}\nКлюч: <code>{content}</code>\n\n"

    builder = InlineKeyboardBuilder()
    builder.button(text="🔙 В профиль", callback_data="profile")
    await callback.message.edit_text(text, parse_mode="HTML", reply_markup=builder.as_markup())

@dp.callback_query(F.data == "about")
async def cb_about(callback: CallbackQuery):
    builder = InlineKeyboardBuilder()
    builder.button(text="💬 Написать в поддержку", url="https://t.me/telegram")
    builder.button(text="🔙 Назад", callback_data="main_menu")
    builder.adjust(1)
    await callback.message.edit_text(
        "ℹ️ <b>О магазине:</b>\n\n"
        "• Мгновенная автоматическая выдача товаров.\n"
        "• Гарантия на все ключи и аккаунты.\n"
        "• Техподдержка 24/7.",
        parse_mode="HTML",
        reply_markup=builder.as_markup()
    )

@dp.callback_query(F.data == "admin_panel")
async def cb_admin_panel(callback: CallbackQuery):
    if callback.from_user.id != ADMIN_ID:
        await callback.answer("Доступ запрещен", show_alert=True)
        return
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute("SELECT COUNT(*) FROM users") as c:
            users_count = (await c.fetchone())[0]
        async with db.execute("SELECT COUNT(*), COALESCE(SUM(price), 0) FROM orders") as c:
            orders_row = await c.fetchone()
            orders_count, total_revenue = orders_row[0], orders_row[1]
        async with db.execute("SELECT COUNT(*) FROM products WHERE is_sold = 0") as c:
            active_products = (await c.fetchone())[0]

    builder = InlineKeyboardBuilder()
    builder.button(text="🔙 Главное меню", callback_data="main_menu")
    text = (
        "⚙️ <b>Панель администратора</b>\n\n"
        f"👥 Пользователей в базе: <b>{users_count}</b>\n"
        f"📦 Товаров в наличии: <b>{active_products}</b>\n"
        f"🛍 Всего продаж: <b>{orders_count}</b>\n"
        f"💰 Общая выручка: <b>{total_revenue} ₽</b>"
    )
    await callback.message.edit_text(text, parse_mode="HTML", reply_markup=builder.as_markup())

async def main():
    logging.basicConfig(level=logging.INFO)
    await init_db()
    token = os.getenv("BOT_TOKEN", "YOUR_BOT_TOKEN_HERE")
    session = CustomSession()
    bot = Bot(token=token, session=session)
    while True:
        try:
            await dp.start_polling(bot, polling_timeout=15)
            break
        except Exception as e:
            logging.error(f"Polling connection reset: {e}. Reconnecting in 3s...")
            await asyncio.sleep(3)

if __name__ == "__main__":
    asyncio.run(main())
