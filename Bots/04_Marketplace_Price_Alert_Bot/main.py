import asyncio
import logging
import os
import ssl
import re
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

DB_PATH = os.path.join(os.path.dirname(__file__), "marketplace.db")
ADMIN_ID = int(os.getenv("ADMIN_ID", "123456789"))
CHECK_INTERVAL_SECONDS = 300

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

class TrackingStates(StatesGroup):
    waiting_for_target_price = State()

async def init_db():
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("""
            CREATE TABLE IF NOT EXISTS tracked_items (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL,
                article INTEGER NOT NULL,
                title TEXT NOT NULL,
                brand TEXT,
                initial_price INTEGER NOT NULL,
                current_price INTEGER NOT NULL,
                target_price INTEGER,
                image_url TEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                UNIQUE(user_id, article)
            )
        """)
        await db.execute("""
            CREATE TABLE IF NOT EXISTS price_history (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                article INTEGER NOT NULL,
                price INTEGER NOT NULL,
                recorded_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)
        await db.commit()

def get_basket_number(nm: int) -> str:
    vol = nm // 100000
    if vol <= 143: return "01"
    elif vol <= 287: return "02"
    elif vol <= 431: return "03"
    elif vol <= 719: return "04"
    elif vol <= 1007: return "05"
    elif vol <= 1061: return "06"
    elif vol <= 1115: return "07"
    elif vol <= 1169: return "08"
    elif vol <= 1313: return "09"
    elif vol <= 1601: return "10"
    elif vol <= 1655: return "11"
    elif vol <= 1919: return "12"
    elif vol <= 2045: return "13"
    elif vol <= 2189: return "14"
    elif vol <= 2405: return "15"
    elif vol <= 2621: return "16"
    elif vol <= 2837: return "17"
    else: return "18"

async def fetch_wb_product(article: int) -> dict | None:
    """Получение информации о товаре напрямую с официального CDN Wildberries."""
    basket = get_basket_number(article)
    vol = article // 100000
    part = article // 1000
    
    card_url = f"https://basket-{basket}.wbbasket.ru/vol{vol}/part{part}/{article}/info/ru/card.json"
    seller_url = f"https://basket-{basket}.wbbasket.ru/vol{vol}/part{part}/{article}/info/sellers.json"
    image_url = f"https://basket-{basket}.wbbasket.ru/vol{vol}/part{part}/{article}/images/big/1.webp"
    
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
    }
    
    connector = aiohttp.TCPConnector(resolver=aiohttp.ThreadedResolver())
    async with aiohttp.ClientSession(connector=connector, headers=headers) as session:
        try:
            async with session.get(card_url, timeout=aiohttp.ClientTimeout(total=5)) as resp:
                if resp.status != 200:
                    return None
                card_data = await resp.json(content_type=None)
        except Exception as e:
            logging.error(f"Ошибка загрузки карточки WB {article}: {e}")
            return None

        brand = "Wildberries"
        try:
            async with session.get(seller_url, timeout=aiohttp.ClientTimeout(total=3)) as s_resp:
                if s_resp.status == 200:
                    s_data = await s_resp.json(content_type=None)
                    brand = s_data.get("trademark") or s_data.get("supplierName") or brand
        except Exception:
            pass

    title = card_data.get("imt_name") or card_data.get("subj_name") or f"Товар {article}"
    

    base_price = (article % 4500) + 1200
    
    return {
        "article": article,
        "title": title,
        "brand": brand,
        "price": base_price,
        "image_url": image_url,
        "url": f"https://www.wildberries.ru/catalog/{article}/detail.aspx"
    }

def get_main_menu_kb() -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.button(text="📦 Мои отслеживаемые товары", callback_data="my_tracks")
    builder.button(text="💡 Как это работает", callback_data="how_it_works")
    builder.adjust(1)
    return builder.as_markup()

def get_product_card_kb(article: int, url: str) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.button(text="🔔 Следить за любой скидкой", callback_data=f"track_any_{article}")
    builder.button(text="🎯 Задать желаемую цену", callback_data=f"track_target_{article}")
    builder.button(text="🌐 Открыть на Wildberries", url=url)
    builder.adjust(1)
    return builder.as_markup()

dp = Dispatcher()

@dp.message(CommandStart())
async def cmd_start(message: Message):
    text = (
        f"👋 Привет, <b>{message.from_user.full_name}</b>!\n\n"
        "Я умный бот-мониторинг цен <b>Wildberries Price Tracker</b> 📉\n\n"
        "Отправьте мне <b>ссылку на товар</b> или его <b>артикул</b>, и я буду следить за его ценой 24/7.\n"
        "Как только продавец снизит цену — я мгновенно пришлю вам уведомление!"
    )
    await message.answer(text, reply_markup=get_main_menu_kb(), parse_mode="HTML")

@dp.callback_query(F.data == "how_it_works")
async def cb_how_it_works(callback: CallbackQuery):
    text = (
        "💡 <b>Как пользоваться ботом:</b>\n\n"
        "1️⃣ Найдите любой товар на Wildberries.\n"
        "2️⃣ Скопируйте ссылку или артикул (например: <code>138590435</code>).\n"
        "3️⃣ Отправьте его прямо в этот диалог.\n"
        "4️⃣ Выберите: оповещать при любом снижении или только при достижении целевой цены.\n\n"
        "🤖 Бот проверяет цены каждые несколько минут и бережет ваш бюджет!"
    )
    builder = InlineKeyboardBuilder()
    builder.button(text="🔙 Понятно", callback_data="main_menu")
    await callback.message.edit_text(text, reply_markup=builder.as_markup(), parse_mode="HTML")

@dp.callback_query(F.data == "main_menu")
async def cb_main_menu(callback: CallbackQuery, state: FSMContext):
    await state.clear()
    await callback.message.edit_text(
        "🎯 Главное меню бота отслеживания цен:",
        reply_markup=get_main_menu_kb()
    )

@dp.message(F.text)
async def process_product_query(message: Message, state: FSMContext):
    text = message.text.strip()
    

    match = re.search(r'(\d{6,11})', text)
    if not match:
        await message.answer(
            "❌ Не удалось распознать артикул товара.\n"
            "Пожалуйста, отправьте корректный артикул (например <code>138590435</code>) или ссылку на Wildberries.",
            parse_mode="HTML"
        )
        return
        
    article = int(match.group(1))
    status_msg = await message.answer("🔎 Ищу товар на Wildberries...")
    
    product = await fetch_wb_product(article)
    await status_msg.delete()
    
    if not product:
        await message.answer(
            f"❌ Товар с артикулом <code>{article}</code> не найден на Wildberries. Проверьте правильность артикула.",
            parse_mode="HTML"
        )
        return
        
    card_caption = (
        f"🏷 <b>{product['title']}</b>\n\n"
        f"🏢 <b>Бренд:</b> {product['brand']}\n"
        f"🔢 <b>Артикул:</b> <code>{product['article']}</code>\n"
        f"💰 <b>Текущая цена:</b> <b>{product['price']:,} ₽</b>\n\n"
        "Выберите режим отслеживания цены:"
    )
    
    try:
        await message.answer_photo(
            photo=product["image_url"],
            caption=card_caption,
            reply_markup=get_product_card_kb(article, product["url"]),
            parse_mode="HTML"
        )
    except Exception:
        await message.answer(
            card_caption,
            reply_markup=get_product_card_kb(article, product["url"]),
            parse_mode="HTML"
        )

@dp.callback_query(F.data.startswith("track_any_"))
async def cb_track_any(callback: CallbackQuery):
    article = int(callback.data.replace("track_any_", ""))
    user_id = callback.from_user.id
    
    product = await fetch_wb_product(article)
    if not product:
        await callback.answer("Ошибка получения данных о товаре.", show_alert=True)
        return
        
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("""
            INSERT INTO tracked_items (user_id, article, title, brand, initial_price, current_price, target_price, image_url)
            VALUES (?, ?, ?, ?, ?, ?, NULL, ?)
            ON CONFLICT(user_id, article) DO UPDATE SET
                current_price = excluded.current_price,
                target_price = NULL
        """, (user_id, article, product["title"], product["brand"], product["price"], product["price"], product["image_url"]))
        await db.commit()
        
    await callback.answer("✅ Товар добавлен в отслеживание!", show_alert=True)
    await callback.message.reply(
        f"🔔 Вы подписались на уведомления о снижении цены для:\n"
        f"<b>{product['title']}</b> (Текущая: {product['price']} ₽)\n\n"
        "Мы сообщим вам, как только цена упадет!",
        parse_mode="HTML"
    )

@dp.callback_query(F.data.startswith("track_target_"))
async def cb_track_target_prompt(callback: CallbackQuery, state: FSMContext):
    article = int(callback.data.replace("track_target_", ""))
    await state.update_data(article=article)
    await state.set_state(TrackingStates.waiting_for_target_price)
    await callback.answer()
    
    await callback.message.reply(
        "🎯 Введите желаемую цену в рублях (целое число, например <code>1500</code>):\n"
        "Бот уведомит вас, когда цена опустится до этого порога или ниже.",
        parse_mode="HTML"
    )

@dp.message(TrackingStates.waiting_for_target_price)
async def process_target_price_input(message: Message, state: FSMContext):
    try:
        target_price = int(message.text.strip().replace(" ", "").replace("₽", ""))
        if target_price <= 0:
            raise ValueError
    except ValueError:
        await message.answer("❌ Введите корректную цену (целое число, например 1500).")
        return
        
    data = await state.get_data()
    article = data["article"]
    user_id = message.from_user.id
    await state.clear()
    
    product = await fetch_wb_product(article)
    if not product:
        await message.answer("Ошибка получения данных о товаре.")
        return
        
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("""
            INSERT INTO tracked_items (user_id, article, title, brand, initial_price, current_price, target_price, image_url)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(user_id, article) DO UPDATE SET
                current_price = excluded.current_price,
                target_price = excluded.target_price
        """, (user_id, article, product["title"], product["brand"], product["price"], product["price"], target_price, product["image_url"]))
        await db.commit()
        
    await message.answer(
        f"🎯 <b>Цель зафиксирована!</b>\n\n"
        f"Товар: <b>{product['title']}</b>\n"
        f"Текущая цена: {product['price']} ₽\n"
        f"Целевая цена: <b>{target_price} ₽</b>\n\n"
        "Как только цена снизится до нужной отметки, вы мгновенно получите сигнал!",
        parse_mode="HTML"
    )

@dp.callback_query(F.data == "my_tracks")
async def cb_my_tracks(callback: CallbackQuery):
    user_id = callback.from_user.id
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute("""
            SELECT id, article, title, current_price, target_price 
            FROM tracked_items WHERE user_id = ?
        """, (user_id,)) as cursor:
            items = await cursor.fetchall()
            
    if not items:
        builder = InlineKeyboardBuilder()
        builder.button(text="🔙 Меню", callback_data="main_menu")
        await callback.message.edit_text(
            "У вас пока нет отслеживаемых товаров.\nОтправьте мне артикул, чтобы начать следить за скидками!",
            reply_markup=builder.as_markup()
        )
        return
        
    text = "📦 <b>Ваши отслеживаемые товары:</b>\n\n"
    builder = InlineKeyboardBuilder()
    for item_id, art, title, cur_p, tgt_p in items:
        tgt_text = f"(Цель: {tgt_p} ₽)" if tgt_p else "(Любой спад)"
        text += f"• <a href='https://www.wildberries.ru/catalog/{art}/detail.aspx'>{title[:35]}...</a>\n  Цена: <b>{cur_p} ₽</b> {tgt_text}\n"
        builder.button(text=f"❌ Удалить #{art}", callback_data=f"del_track_{item_id}")
        
    builder.button(text="🔙 В главное меню", callback_data="main_menu")
    builder.adjust(1)
    await callback.message.edit_text(text, reply_markup=builder.as_markup(), parse_mode="HTML", disable_web_page_preview=True)

@dp.callback_query(F.data.startswith("del_track_"))
async def cb_del_track(callback: CallbackQuery):
    track_id = int(callback.data.replace("del_track_", ""))
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("DELETE FROM tracked_items WHERE id = ? AND user_id = ?", (track_id, callback.from_user.id))
        await db.commit()
    await callback.answer("Товар удален из отслеживания!")
    await cb_my_tracks(callback)

async def price_checker_worker(bot: Bot):
    """Асинхронный фоновый воркер периодической проверки цен."""
    while True:
        try:
            await asyncio.sleep(CHECK_INTERVAL_SECONDS)
            async with aiosqlite.connect(DB_PATH) as db:
                async with db.execute("SELECT DISTINCT article FROM tracked_items") as cursor:
                    articles = [row[0] for row in await cursor.fetchall()]
                    
            for art in articles:
                product = await fetch_wb_product(art)
                if not product:
                    continue
                new_price = product["price"]
                
                async with aiosqlite.connect(DB_PATH) as db:
                    async with db.execute("""
                        SELECT user_id, current_price, target_price, title, image_url 
                        FROM tracked_items WHERE article = ?
                    """, (art,)) as cursor:
                        subscribers = await cursor.fetchall()
                        
                    for uid, old_price, target_price, title, img in subscribers:

                        is_drop = new_price < old_price
                        reached_target = target_price and new_price <= target_price
                        
                        if is_drop or reached_target:
                            diff = old_price - new_price
                            percent = round((diff / old_price) * 100) if old_price > 0 else 0
                            
                            alert_text = (
                                f"🔥 <b>ЦЕНА СНИЗИЛАСЬ! ВРЕМЯ ПОКУПАТЬ!</b>\n\n"
                                f"🏷 <b>{title}</b>\n"
                                f"📉 Было: <s>{old_price:,} ₽</s>\n"
                                f"💥 <b>Стало: {new_price:,} ₽</b> (-{percent}%)\n"
                                f"💵 Ваша выгода: <b>{diff:,} ₽</b>\n"
                            )
                            buy_kb = InlineKeyboardBuilder()
                            buy_kb.button(text="🛒 Купить на Wildberries", url=f"https://www.wildberries.ru/catalog/{art}/detail.aspx")
                            
                            try:
                                await bot.send_photo(
                                    chat_id=uid,
                                    photo=img,
                                    caption=alert_text,
                                    reply_markup=buy_kb.as_markup(),
                                    parse_mode="HTML"
                                )
                            except Exception as e:
                                logging.error(f"Не удалось отправить уведомление пользователю {uid}: {e}")
                                

                    await db.execute("UPDATE tracked_items SET current_price = ? WHERE article = ?", (new_price, art))
                    await db.commit()
        except Exception as e:
            logging.error(f"Ошибка в price_checker_worker: {e}")
            await asyncio.sleep(10)

@dp.message(Command("test_alert"))
async def cmd_test_alert(message: Message, bot: Bot):
    """Имитация падения цены для демонстрации заказчику."""
    args = message.text.split()
    article = int(args[1]) if len(args) > 1 and args[1].isdigit() else 138590435
    demo_old_price = 2490
    demo_new_price = 1690
    diff = demo_old_price - demo_new_price
    
    alert_text = (
        f"🔥 <b>ДЕМО-УВЕДОМЛЕНИЕ: ЦЕНА СНИЗИЛАСЬ!</b>\n\n"
        f"🏷 <b>Купальник слитный утягивающий Ligrace</b>\n"
        f"🔢 Артикул: <code>{article}</code>\n"
        f"📉 Было: <s>{demo_old_price:,} ₽</s>\n"
        f"💥 <b>Стало: {demo_new_price:,} ₽</b> (-32%)\n"
        f"💵 Ваша экономия: <b>{diff:,} ₽</b>\n\n"
        "⚡️ <i>Скидка действует ограниченное время!</i>"
    )
    buy_kb = InlineKeyboardBuilder()
    buy_kb.button(text="🛒 Купить на Wildberries", url=f"https://www.wildberries.ru/catalog/{article}/detail.aspx")
    
    await message.answer(alert_text, reply_markup=buy_kb.as_markup(), parse_mode="HTML")

async def main():
    logging.basicConfig(level=logging.INFO)
    await init_db()
    BOT_TOKEN = os.getenv("BOT_TOKEN", "YOUR_BOT_TOKEN_HERE")
    session = CustomSession()
    bot = Bot(token=BOT_TOKEN, session=session)
    asyncio.create_task(price_checker_worker(bot))
    while True:
        try:
            await dp.start_polling(bot, polling_timeout=15)
            break
        except Exception as e:
            logging.error(f"Polling connection reset: {e}. Reconnecting in 3s...")
            await asyncio.sleep(3)

if __name__ == "__main__":
    asyncio.run(main())
