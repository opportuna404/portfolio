import asyncio
import logging
import os
import ssl
from datetime import datetime, timedelta
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
    LabeledPrice,
    PreCheckoutQuery
)
from aiogram.utils.keyboard import InlineKeyboardBuilder
from aiogram.client.session.aiohttp import AiohttpSession

DB_PATH = os.path.join(os.path.dirname(__file__), "vip_club.db")
ADMIN_ID = int(os.getenv("ADMIN_ID", "123456789"))
VIP_CHANNEL_ID = -1001234567890
CRYPTO_BOT_TOKEN = os.getenv("CRYPTO_BOT_TOKEN", "")

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

TARIFFS = {
    "1m": {"title": "🌟 1 месяц доступа", "days": 30, "price_usdt": 15.0, "price_stars": 750},
    "3m": {"title": "🚀 3 месяца доступа (Скидка 20%)", "days": 90, "price_usdt": 36.0, "price_stars": 1800},
    "lifetime": {"title": "👑 Пожизненный VIP доступ", "days": 3650, "price_usdt": 99.0, "price_stars": 4900}
}

async def init_db():
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("""
            CREATE TABLE IF NOT EXISTS users (
                user_id INTEGER PRIMARY KEY,
                username TEXT,
                full_name TEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)
        await db.execute("""
            CREATE TABLE IF NOT EXISTS subscriptions (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL,
                tariff_key TEXT NOT NULL,
                tariff_title TEXT NOT NULL,
                expires_at TIMESTAMP NOT NULL,
                status TEXT DEFAULT 'active', -- 'active', 'expired'
                invite_link TEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (user_id) REFERENCES users(user_id)
            )
        """)
        await db.execute("""
            CREATE TABLE IF NOT EXISTS invoices (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                invoice_id TEXT UNIQUE,
                user_id INTEGER NOT NULL,
                tariff_key TEXT NOT NULL,
                amount REAL NOT NULL,
                currency TEXT NOT NULL,
                pay_url TEXT,
                status TEXT DEFAULT 'pending' -- 'pending', 'paid'
            )
        """)
        await db.commit()

async def create_cryptopay_invoice(amount_usd: float, description: str) -> dict | None:
    """Создание счета в Crypto Pay API (@CryptoBot)."""
    if not CRYPTO_BOT_TOKEN:

        return {
            "invoice_id": f"demo_{os.urandom(4).hex()}",
            "pay_url": "http://t.me/CryptoBot?start=pay_demo_invoice"
        }
        
    url = "https://pay.crypt.bot/api/createInvoice"
    headers = {"Crypto-Pay-API-Token": CRYPTO_BOT_TOKEN}
    payload = {
        "asset": "USDT",
        "amount": str(amount_usd),
        "description": description,
        "expires_in": 3600
    }
    try:
        async with aiohttp.ClientSession() as session:
            async with session.post(url, headers=headers, json=payload, timeout=aiohttp.ClientTimeout(total=5)) as resp:
                data = await resp.json()
                if data.get("ok"):
                    res = data["result"]
                    return {
                        "invoice_id": str(res["invoice_id"]),
                        "pay_url": res["pay_url"]
                    }
    except Exception as e:
        logging.error(f"Ошибка создания CryptoPay инвойса: {e}")
    return None

async def check_cryptopay_invoice(invoice_id: str) -> bool:
    """Проверка статуса оплаты в Crypto Pay API."""
    if invoice_id.startswith("demo_"):
        return True
    if not CRYPTO_BOT_TOKEN:
        return True
        
    url = f"https://pay.crypt.bot/api/getInvoices?invoice_ids={invoice_id}"
    headers = {"Crypto-Pay-API-Token": CRYPTO_BOT_TOKEN}
    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(url, headers=headers, timeout=aiohttp.ClientTimeout(total=5)) as resp:
                data = await resp.json()
                if data.get("ok") and data["result"]["items"]:
                    item = data["result"]["items"][0]
                    return item.get("status") == "paid"
    except Exception as e:
        logging.error(f"Ошибка проверки инвойса {invoice_id}: {e}")
    return False

def get_main_menu_kb() -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.button(text="👑 Выбрать тариф в VIP-клуб", callback_data="tariffs_menu")
    builder.button(text="👤 Моя подписка", callback_data="my_sub")
    builder.button(text="ℹ️ О закрытом клубе", callback_data="about_club")
    builder.adjust(1)
    return builder.as_markup()

def get_tariffs_kb() -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    for key, item in TARIFFS.items():
        builder.button(
            text=f"{item['title']} — {item['price_usdt']} USDT / {item['price_stars']} ⭐️",
            callback_data=f"tariff_{key}"
        )
    builder.button(text="🔙 Главное меню", callback_data="main_menu")
    builder.adjust(1)
    return builder.as_markup()

def get_payment_method_kb(tariff_key: str) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.button(text="💎 Оплатить USDT (CryptoBot)", callback_data=f"pay_crypto_{tariff_key}")
    builder.button(text="⭐️ Оплатить Telegram Stars", callback_data=f"pay_stars_{tariff_key}")
    builder.button(text="🧪 Тестовая оплата (Демо)", callback_data=f"mock_pay_{tariff_key}")
    builder.button(text="🔙 Назад к тарифам", callback_data="tariffs_menu")
    builder.adjust(1)
    return builder.as_markup()

dp = Dispatcher()

@dp.message(CommandStart())
async def cmd_start(message: Message):
    user_id = message.from_user.id
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("""
            INSERT INTO users (user_id, username, full_name)
            VALUES (?, ?, ?)
            ON CONFLICT(user_id) DO UPDATE SET
                username = excluded.username,
                full_name = excluded.full_name
        """, (user_id, message.from_user.username, message.from_user.full_name))
        await db.commit()

    text = (
        f"👋 Привет, <b>{message.from_user.full_name}</b>!\n\n"
        "Добро пожаловать в закрытое сообщество <b>«Crypto Alpha Insiders»</b> 💎\n\n"
        "В нашем клубе вы получаете:\n"
        "• Ежедневную аналитику рынка и торговые сетапы\n"
        "• Закрытый чат инвесторов и разработчиков\n"
        "• Доступ к приватным скриптам и ранним дропам\n\n"
        "Оформите доступ в пару кликов с помощью крипты или Telegram Stars:"
    )
    await message.answer(text, reply_markup=get_main_menu_kb(), parse_mode="HTML")

@dp.callback_query(F.data == "main_menu")
async def cb_main_menu(callback: CallbackQuery):
    await callback.message.edit_text(
        "Главное меню VIP-клуба:",
        reply_markup=get_main_menu_kb()
    )

@dp.callback_query(F.data == "about_club")
async def cb_about_club(callback: CallbackQuery):
    text = (
        "👑 <b>О клубе «Crypto Alpha Insiders»:</b>\n\n"
        "Мы закрытое международное сообщество трейдеров, разработчиков и фаундеров.\n\n"
        "📊 <b>Винрейт сигналов:</b> 82% по итогам последних 12 месяцев\n"
        "🤝 <b>Нетворкинг:</b> более 450 активных резидентов\n"
        "🔒 <b>Безопасность:</b> полностью автоматический вход по одноразовым защищенным инвайтам."
    )
    builder = InlineKeyboardBuilder()
    builder.button(text="🚀 Вступить в клуб", callback_data="tariffs_menu")
    builder.button(text="🔙 Меню", callback_data="main_menu")
    builder.adjust(1)
    await callback.message.edit_text(text, reply_markup=builder.as_markup(), parse_mode="HTML")

@dp.callback_query(F.data == "tariffs_menu")
async def cb_tariffs_menu(callback: CallbackQuery):
    await callback.message.edit_text(
        "💎 <b>Выберите подходящий тариф подписки:</b>",
        reply_markup=get_tariffs_kb(),
        parse_mode="HTML"
    )

@dp.callback_query(F.data.startswith("tariff_"))
async def cb_tariff_selected(callback: CallbackQuery):
    tariff_key = callback.data.replace("tariff_", "")
    tariff = TARIFFS.get(tariff_key)
    if not tariff:
        return
        
    text = (
        f"Тариф: <b>{tariff['title']}</b>\n\n"
        f"⏳ Срок действия: <b>{tariff['days']} дней</b>\n"
        f"💵 Стоимость: <b>{tariff['price_usdt']} USDT</b> или <b>{tariff['price_stars']} ⭐️</b>\n\n"
        "Выберите удобный способ оплаты:"
    )
    await callback.message.edit_text(text, reply_markup=get_payment_method_kb(tariff_key), parse_mode="HTML")

@dp.callback_query(F.data.startswith("pay_crypto_"))
async def cb_pay_crypto(callback: CallbackQuery):
    tariff_key = callback.data.replace("pay_crypto_", "")
    tariff = TARIFFS[tariff_key]
    user_id = callback.from_user.id
    
    invoice = await create_cryptopay_invoice(
        amount_usd=tariff["price_usdt"],
        description=f"Подписка VIP Club: {tariff['title']}"
    )
    if not invoice:
        await callback.answer("Ошибка создания счета. Попробуйте другой способ.", show_alert=True)
        return
        
    inv_id = invoice["invoice_id"]
    pay_url = invoice["pay_url"]
    
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("""
            INSERT INTO invoices (invoice_id, user_id, tariff_key, amount, currency, pay_url)
            VALUES (?, ?, ?, ?, 'USDT', ?)
        """, (inv_id, user_id, tariff_key, tariff["price_usdt"], pay_url))
        await db.commit()
        
    builder = InlineKeyboardBuilder()
    builder.button(text="💳 Оплатить в @CryptoBot", url=pay_url)
    builder.button(text="🔄 Проверить оплату", callback_data=f"check_pay_{inv_id}")
    builder.button(text="🔙 Отмена", callback_data="tariffs_menu")
    builder.adjust(1)
    
    await callback.message.edit_text(
        f"🧾 <b>Счет на оплату сформирован!</b>\n\n"
        f"Тариф: <b>{tariff['title']}</b>\n"
        f"Сумма к оплате: <b>{tariff['price_usdt']} USDT</b>\n\n"
        "1. Нажмите «Оплатить в @CryptoBot».\n"
        "2. После подтверждения оплаты нажмите «Проверить оплату».",
        reply_markup=builder.as_markup(),
        parse_mode="HTML"
    )

@dp.callback_query(F.data.startswith("check_pay_"))
async def cb_check_pay(callback: CallbackQuery, bot: Bot):
    inv_id = callback.data.replace("check_pay_", "")
    
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute("SELECT user_id, tariff_key, status FROM invoices WHERE invoice_id = ?", (inv_id,)) as cur:
            row = await cur.fetchone()
            
    if not row:
        await callback.answer("Счет не найден.", show_alert=True)
        return
        
    user_id, tariff_key, status = row[0], row[1], row[2]
    

    is_paid = await check_cryptopay_invoice(inv_id)
    if is_paid:
        await grant_subscription(bot, callback.message, user_id, tariff_key)
    else:
        await callback.answer("⏳ Платеж еще не поступил. Пожалуйста, завершите оплату.", show_alert=True)

async def grant_subscription(bot: Bot, msg_or_cb_msg, user_id: int, tariff_key: str):
    tariff = TARIFFS[tariff_key]
    days = tariff["days"]
    expire_date = datetime.now() + timedelta(days=days)
    

    invite_url = "https://t.me/+join_vip_secret_demo"
    try:

        ts = int(expire_date.timestamp())
        invite = await bot.create_chat_invite_link(
            chat_id=VIP_CHANNEL_ID,
            member_limit=1,
            expire_date=ts
        )
        invite_url = invite.invite_link
    except Exception as e:
        logging.warning(f"Не удалось сгенерировать ссылку Bot API (демо канал): {e}")
        invite_url = f"https://t.me/telegram?start=vip_token_{os.urandom(3).hex()}"

    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("""
            INSERT INTO subscriptions (user_id, tariff_key, tariff_title, expires_at, status, invite_link)
            VALUES (?, ?, ?, ?, 'active', ?)
        """, (user_id, tariff_key, tariff["title"], expire_date.strftime("%Y-%m-%d %H:%M:%S"), invite_url))
        await db.commit()

    text = (
        "🎉 <b>ПОЗДРАВЛЯЕМ! ОПЛАТА УСПЕШНО ПРОШЛА!</b>\n\n"
        f"👑 Ваш тариф: <b>{tariff['title']}</b>\n"
        f"📅 Доступ активен до: <b>{expire_date.strftime('%d.%m.%Y')}</b>\n\n"
        "🔗 <b>Ваша персональная одноразовая ссылка для входа:</b>\n"
        f"👉 <a href='{invite_url}'>Вступить в закрытый VIP-клуб</a>\n\n"
        "⚠️ <i>Ссылка сгорает после одного использования, не передавайте ее третьим лицам!</i>"
    )
    
    builder = InlineKeyboardBuilder()
    builder.button(text="🚀 Вступить в канал", url=invite_url)
    builder.button(text="👤 Моя подписка", callback_data="my_sub")
    builder.adjust(1)
    
    await msg_or_cb_msg.answer(text, reply_markup=builder.as_markup(), parse_mode="HTML")

@dp.callback_query(F.data.startswith("mock_pay_"))
async def cb_mock_pay(callback: CallbackQuery, bot: Bot):
    tariff_key = callback.data.replace("mock_pay_", "")
    await callback.answer("Тестовая оплата прошла успешно!")
    await grant_subscription(bot, callback.message, callback.from_user.id, tariff_key)

@dp.callback_query(F.data == "my_sub")
async def cb_my_sub(callback: CallbackQuery):
    user_id = callback.from_user.id
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute("""
            SELECT tariff_title, expires_at, status, invite_link 
            FROM subscriptions 
            WHERE user_id = ? AND status = 'active'
            ORDER BY id DESC LIMIT 1
        """, (user_id,)) as cur:
            sub = await cur.fetchone()
            
    builder = InlineKeyboardBuilder()
    if not sub:
        builder.button(text="💎 Оформить подписку", callback_data="tariffs_menu")
        builder.button(text="🔙 Меню", callback_data="main_menu")
        builder.adjust(1)
        await callback.message.edit_text(
            "У вас нет активной подписки на VIP-клуб.",
            reply_markup=builder.as_markup()
        )
        return
        
    title, exp_str, status, link = sub
    text = (
        "👤 <b>Ваш статус резидента:</b>\n\n"
        f"👑 Тариф: <b>{title}</b>\n"
        f"📅 Активен до: <b>{exp_str}</b>\n"
        f"🟢 Статус: <b>Активен</b>\n\n"
        f"🔗 Ссылка на канал: {link}"
    )
    builder.button(text="🔄 Продлить подписку", callback_data="tariffs_menu")
    builder.button(text="🔙 Меню", callback_data="main_menu")
    builder.adjust(1)
    await callback.message.edit_text(text, reply_markup=builder.as_markup(), parse_mode="HTML")

async def subscription_checker_worker(bot: Bot):
    """Проверка истечения подписок каждые 60 секунд."""
    while True:
        try:
            await asyncio.sleep(60)
            now_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            async with aiosqlite.connect(DB_PATH) as db:

                async with db.execute("""
                    SELECT id, user_id, tariff_title FROM subscriptions 
                    WHERE status = 'active' AND expires_at < ?
                """, (now_str,)) as cur:
                    expired = await cur.fetchall()
                    
                for sub_id, uid, title in expired:

                    try:
                        await bot.ban_chat_member(chat_id=VIP_CHANNEL_ID, user_id=uid)
                        await bot.unban_chat_member(chat_id=VIP_CHANNEL_ID, user_id=uid)
                        await bot.send_message(
                            uid,
                            f"⚠️ Ваша подписка <b>{title}</b> истекла. Доступ к VIP-каналу приостановлен.\n"
                            "Чтобы продолжить участие, оформите продление в меню /start.",
                            parse_mode="HTML"
                        )
                    except Exception:
                        pass
                    await db.execute("UPDATE subscriptions SET status = 'expired' WHERE id = ?", (sub_id,))
                await db.commit()
        except Exception as e:
            logging.error(f"Ошибка в subscription_checker: {e}")
            await asyncio.sleep(10)

async def main():
    logging.basicConfig(level=logging.INFO)
    await init_db()
    BOT_TOKEN = os.getenv("BOT_TOKEN", "YOUR_BOT_TOKEN_HERE")
    session = CustomSession()
    bot = Bot(token=BOT_TOKEN, session=session)
    asyncio.create_task(subscription_checker_worker(bot))
    while True:
        try:
            await dp.start_polling(bot, polling_timeout=15)
            break
        except Exception as e:
            logging.error(f"Polling connection reset: {e}. Reconnecting in 3s...")
            await asyncio.sleep(3)

if __name__ == "__main__":
    asyncio.run(main())
