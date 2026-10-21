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

DB_PATH = os.path.join(os.path.dirname(__file__), "support.db")
ADMIN_ID = int(os.getenv("ADMIN_ID", "123456789"))
OPENROUTER_API_KEY = os.getenv("OPENROUTER_API_KEY", "")

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

class SupportStates(StatesGroup):
    chatting_with_operator = State()

async def init_db():
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("""
            CREATE TABLE IF NOT EXISTS tickets (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL,
                user_name TEXT,
                status TEXT DEFAULT 'open', -- 'open', 'closed'
                rating INTEGER,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                closed_at TIMESTAMP
            )
        """)
        await db.execute("""
            CREATE TABLE IF NOT EXISTS ticket_messages (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                ticket_id INTEGER NOT NULL,
                sender_role TEXT NOT NULL, -- 'client', 'operator', 'ai'
                message_text TEXT NOT NULL,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (ticket_id) REFERENCES tickets(id)
            )
        """)
        await db.execute("""
            CREATE TABLE IF NOT EXISTS faq (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                category TEXT NOT NULL,
                question TEXT NOT NULL,
                answer TEXT NOT NULL,
                keywords TEXT
            )
        """)
        

        async with db.execute("SELECT COUNT(*) FROM faq") as cursor:
            count = (await cursor.fetchone())[0]
            if count == 0:
                await db.execute("""
                    INSERT INTO faq (category, question, answer, keywords) VALUES
                    ('Доставка', 'Какие сроки и стоимость доставки?', 
                     '🚚 Доставка по РФ осуществляется СДЭК и Почтой России. Сроки: 2-5 рабочих дней. При заказе от 3000 ₽ доставка бесплатная!', 
                     'доставка,сдэк,почта,курьер,привезти,доставить,сроки'),
                    ('Оплата', 'Как оплатить заказ?', 
                     '💳 Мы принимаем банковские карты (МИР, Visa, Mastercard), СБП, а также безналичный расчет для юридических лиц.', 
                     'оплата,оплатить,карта,сбп,перевод,деньги,безнал'),
                    ('Возврат', 'Как оформить возврат или обмен товара?', 
                     '🔄 Вы можете вернуть товар надлежащего качества в течение 14 дней с момента получения. Для оформления свяжитесь с оператором в этом боте.', 
                     'возврат,вернуть,обмен,брак,деньги назад,гарантия'),
                    ('График', 'Какой у вас режим работы?', 
                     '🕒 Служба поддержки работает ежедневно с 09:00 до 22:00 по МСК. AI-ассистент доступен круглосуточно 24/7.', 
                     'график,время,режим,часы,когда работаете')
                """)
        await db.commit()

async def ask_llm(user_question: str) -> str | None:
    """Запрос к нейросети через OpenRouter (если задан ключ)."""
    if not OPENROUTER_API_KEY:
        return None
        
    url = "https://openrouter.ai/api/v1/chat/completions"
    headers = {
        "Authorization": f"Bearer {OPENROUTER_API_KEY}",
        "Content-Type": "application/json"
    }
    payload = {
        "model": "google/gemini-2.0-flash-lite-001",
        "messages": [
            {
                "role": "system", 
                "content": "Ты дружелюбный и компетентный консультант службы поддержки интернет-магазина. Отвечай кратко, вежливо и по делу на русском языке."
            },
            {"role": "user", "content": user_question}
        ],
        "max_tokens": 300
    }
    try:
        async with aiohttp.ClientSession() as session:
            async with session.post(url, headers=headers, json=payload, timeout=aiohttp.ClientTimeout(total=8)) as resp:
                if resp.status == 200:
                    data = await resp.json()
                    return data["choices"][0]["message"]["content"].strip()
    except Exception as e:
        logging.warning(f"Ошибка запроса к LLM: {e}")
    return None

async def search_faq(query: str) -> str | None:
    """Поиск подходящего ответа по ключевым словам базы знаний."""
    query_lower = query.lower()
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute("SELECT question, answer, keywords FROM faq") as cursor:
            rows = await cursor.fetchall()
            for q, ans, kws in rows:
                if any(kw.strip() in query_lower for kw in (kws or "").split(",")):
                    return f"💡 <b>Ответ по теме «{q}»:</b>\n\n{ans}"
    return None

def get_main_menu_kb() -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.button(text="❓ Частые вопросы (FAQ)", callback_data="faq_menu")
    builder.button(text="👨‍💼 Связаться с оператором", callback_data="connect_operator")
    builder.button(text="ℹ️ О компании", callback_data="about_company")
    builder.adjust(1)
    return builder.as_markup()

async def get_faq_categories_kb() -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute("SELECT id, question FROM faq") as cursor:
            for faq_id, question in await cursor.fetchall():
                builder.button(text=question, callback_data=f"faq_item_{faq_id}")
    builder.button(text="🔙 Назад в меню", callback_data="main_menu")
    builder.adjust(1)
    return builder.as_markup()

def get_operator_chat_kb() -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.button(text="⏹ Завершить диалог с оператором", callback_data="client_close_ticket")
    builder.adjust(1)
    return builder.as_markup()

def get_rating_kb(ticket_id: int) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.row(
        InlineKeyboardButton(text="⭐ 1", callback_data=f"rate_{ticket_id}_1"),
        InlineKeyboardButton(text="⭐ 2", callback_data=f"rate_{ticket_id}_2"),
        InlineKeyboardButton(text="⭐ 3", callback_data=f"rate_{ticket_id}_3"),
        InlineKeyboardButton(text="⭐ 4", callback_data=f"rate_{ticket_id}_4"),
        InlineKeyboardButton(text="⭐ 5", callback_data=f"rate_{ticket_id}_5"),
    )
    return builder.as_markup()

dp = Dispatcher()

@dp.message(CommandStart())
async def cmd_start(message: Message, state: FSMContext):
    await state.clear()
    text = (
        f"👋 Здравствуйте, <b>{message.from_user.full_name}</b>!\n\n"
        "Вас приветствует служба заботы о клиентах <b>«SmartSupport 24/7»</b> 🤖\n\n"
        "Я могу мгновенно ответить на любой ваш вопрос о доставке, оплате или возврате.\n"
        "Если потребуется помощь человека — я переключу вас на живого оператора!"
    )
    await message.answer(text, reply_markup=get_main_menu_kb(), parse_mode="HTML")

@dp.callback_query(F.data == "main_menu")
async def cb_main_menu(callback: CallbackQuery, state: FSMContext):
    await state.clear()
    await callback.message.edit_text(
        "Главное меню поддержки:",
        reply_markup=get_main_menu_kb()
    )

@dp.callback_query(F.data == "faq_menu")
async def cb_faq_menu(callback: CallbackQuery):
    await callback.message.edit_text(
        "📚 <b>Выберите интересующий вас вопрос:</b>",
        reply_markup=await get_faq_categories_kb(),
        parse_mode="HTML"
    )

@dp.callback_query(F.data.startswith("faq_item_"))
async def cb_faq_item(callback: CallbackQuery):
    faq_id = int(callback.data.replace("faq_item_", ""))
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute("SELECT question, answer FROM faq WHERE id = ?", (faq_id,)) as cur:
            row = await cur.fetchone()
            
    if not row:
        await callback.answer("Вопрос не найден.")
        return
        
    text = f"❓ <b>{row[0]}</b>\n\n{row[1]}"
    builder = InlineKeyboardBuilder()
    builder.button(text="🔙 К списку вопросов", callback_data="faq_menu")
    builder.button(text="👨‍💼 Задать вопрос человеку", callback_data="connect_operator")
    builder.adjust(1)
    await callback.message.edit_text(text, reply_markup=builder.as_markup(), parse_mode="HTML")

@dp.callback_query(F.data == "about_company")
async def cb_about_company(callback: CallbackQuery):
    text = (
        "🏢 <b>Компания «OmniTrade Solutions»</b>\n\n"
        "Лидер в сфере поставок электроники и аксессуаров с 2020 года.\n"
        "📍 Главный офис: Москва, Пресненская наб., 12 (Москва-Сити)\n"
        "🌐 Сайт: omnitrade.example.com\n"
        "✉️ Email: support@omnitrade.example.com"
    )
    builder = InlineKeyboardBuilder()
    builder.button(text="🔙 Меню", callback_data="main_menu")
    await callback.message.edit_text(text, reply_markup=builder.as_markup(), parse_mode="HTML")

@dp.callback_query(F.data == "connect_operator")
async def cb_connect_operator(callback: CallbackQuery, state: FSMContext, bot: Bot):
    user_id = callback.from_user.id
    user_name = callback.from_user.full_name
    username = callback.from_user.username or "без username"
    

    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute("SELECT id FROM tickets WHERE user_id = ? AND status = 'open'", (user_id,)) as cur:
            row = await cur.fetchone()
            
        if row:
            ticket_id = row[0]
        else:
            async with db.execute(
                "INSERT INTO tickets (user_id, user_name) VALUES (?, ?)", 
                (user_id, user_name)
            ) as cur:
                ticket_id = cur.lastrowid
            await db.commit()
            
    await state.set_state(SupportStates.chatting_with_operator)
    await state.update_data(ticket_id=ticket_id)
    
    text = (
        f"👨‍💼 <b>Тикет в поддержку #{ticket_id} открыт!</b>\n\n"
        "Вы подключены к чату с оператором.\n"
        "Напишите ваш вопрос или опишите проблему прямо сюда. Персонал ответит вам в ближайшие минуты!\n\n"
        "<i>Все сообщения пересылаются дежурному специалисту.</i>"
    )
    await callback.message.edit_text(text, reply_markup=get_operator_chat_kb(), parse_mode="HTML")
    

    try:
        op_kb = InlineKeyboardBuilder()
        op_kb.button(text="🔒 Закрыть тикет", callback_data=f"op_close_{ticket_id}")
        await bot.send_message(
            ADMIN_ID,
            f"🚨 <b>НОВЫЙ ЗАПРОС В ПОДДЕРЖКУ!</b>\n\n"
            f"🎫 Тикет: <b>#{ticket_id}</b>\n"
            f"👤 Клиент: {user_name} (@{username})\n"
            f"🆔 ID: <code>{user_id}</code>\n\n"
            f"<i>Чтобы ответить, сделайте Reply (Ответить) на сообщение клиента.</i>",
            reply_markup=op_kb.as_markup(),
            parse_mode="HTML"
        )
    except Exception as e:
        logging.error(f"Не удалось отправить уведомление оператору: {e}")

@dp.message(SupportStates.chatting_with_operator)
async def process_user_ticket_message(message: Message, state: FSMContext, bot: Bot):
    data = await state.get_data()
    ticket_id = data.get("ticket_id")
    user_id = message.from_user.id
    

    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            "INSERT INTO ticket_messages (ticket_id, sender_role, message_text) VALUES (?, 'client', ?)",
            (ticket_id, message.text or "[Медиа/Файл]")
        )
        await db.commit()
        
    try:

        forward_text = f"📨 <b>[Тикет #{ticket_id}] от {message.from_user.full_name}:</b>\n\n{message.text}"
        await bot.send_message(ADMIN_ID, forward_text, parse_mode="HTML")
    except Exception as e:
        logging.error(f"Ошибка пересылки оператору: {e}")

@dp.message(F.chat.id == ADMIN_ID, F.reply_to_message)
async def process_operator_reply(message: Message, bot: Bot):
    reply_text = message.reply_to_message.text or ""
    

    import re
    match = re.search(r'\[Тикет #(\d+)\]', reply_text)
    if not match:
        return
        
    ticket_id = int(match.group(1))
    operator_answer = message.text
    
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute("SELECT user_id, status FROM tickets WHERE id = ?", (ticket_id,)) as cur:
            row = await cur.fetchone()
            
        if not row or row[1] != 'open':
            await message.reply(f"❌ Тикет #{ticket_id} уже закрыт или не найден.")
            return
            
        user_id = row[0]
        await db.execute(
            "INSERT INTO ticket_messages (ticket_id, sender_role, message_text) VALUES (?, 'operator', ?)",
            (ticket_id, operator_answer)
        )
        await db.commit()
        
    try:
        await bot.send_message(
            user_id,
            f"👨‍💼 <b>Специалист службы поддержки:</b>\n\n{operator_answer}",
            parse_mode="HTML"
        )
        await message.react([types.ReactionTypeEmoji(emoji="👍")])
    except Exception as e:
        await message.reply(f"❌ Ошибка отправки сообщения клиенту: {e}")

@dp.callback_query(F.data == "client_close_ticket")
async def cb_client_close_ticket(callback: CallbackQuery, state: FSMContext):
    data = await state.get_data()
    ticket_id = data.get("ticket_id")
    await state.clear()
    
    if ticket_id:
        async with aiosqlite.connect(DB_PATH) as db:
            await db.execute("UPDATE tickets SET status = 'closed', closed_at = CURRENT_TIMESTAMP WHERE id = ?", (ticket_id,))
            await db.commit()
            
    await callback.message.edit_text(
        "✅ Диалог с оператором завершен.\n\n"
        "Пожалуйста, оцените качество обслуживания от 1 до 5:",
        reply_markup=get_rating_kb(ticket_id or 0)
    )

@dp.callback_query(F.data.startswith("op_close_"))
async def cb_op_close_ticket(callback: CallbackQuery, bot: Bot):
    ticket_id = int(callback.data.replace("op_close_", ""))
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute("SELECT user_id FROM tickets WHERE id = ?", (ticket_id,)) as cur:
            row = await cur.fetchone()
        await db.execute("UPDATE tickets SET status = 'closed', closed_at = CURRENT_TIMESTAMP WHERE id = ?", (ticket_id,))
        await db.commit()
        
    await callback.answer(f"Тикет #{ticket_id} закрыт!")
    await callback.message.edit_text(f"🔒 <b>Тикет #{ticket_id} успешно закрыт оператором.</b>", parse_mode="HTML")
    
    if row:
        user_id = row[0]
        try:
            await bot.send_message(
                user_id,
                "✅ Оператор закрыл диалог.\n\nПожалуйста, оцените качество консультации:",
                reply_markup=get_rating_kb(ticket_id)
            )
        except Exception:
            pass

@dp.callback_query(F.data.startswith("rate_"))
async def cb_rate_ticket(callback: CallbackQuery):
    _, t_str, r_str = callback.data.split("_")
    ticket_id = int(t_str)
    rating = int(r_str)
    
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("UPDATE tickets SET rating = ? WHERE id = ?", (rating, ticket_id))
        await db.commit()
        
    builder = InlineKeyboardBuilder()
    builder.button(text="🏠 Главное меню", callback_data="main_menu")
    await callback.message.edit_text(
        f"🙏 <b>Спасибо за вашу оценку ({'⭐' * rating})!</b>\n\nМы постоянно улучшаем сервис для вас.",
        reply_markup=builder.as_markup(),
        parse_mode="HTML"
    )

@dp.message(F.text)
async def process_smart_faq_or_ai(message: Message, state: FSMContext):

    query = message.text.strip()
    

    faq_ans = await search_faq(query)
    if faq_ans:
        builder = InlineKeyboardBuilder()
        builder.button(text="👨‍💼 Связаться с оператором", callback_data="connect_operator")
        builder.button(text="🔙 Меню", callback_data="main_menu")
        builder.adjust(1)
        await message.answer(faq_ans, reply_markup=builder.as_markup(), parse_mode="HTML")
        return
        

    status = await message.answer("🤖 Секунду, ищу ответ в базе знаний...")
    llm_ans = await ask_llm(query)
    await status.delete()
    
    if llm_ans:
        builder = InlineKeyboardBuilder()
        builder.button(text="👨‍💼 Позвать живого оператора", callback_data="connect_operator")
        builder.button(text="🔙 Меню", callback_data="main_menu")
        builder.adjust(1)
        await message.answer(f"🤖 <b>AI-ассистент:</b>\n\n{llm_ans}", reply_markup=builder.as_markup(), parse_mode="HTML")
        return
        

    builder = InlineKeyboardBuilder()
    builder.button(text="👨‍💼 Переключить на оператора", callback_data="connect_operator")
    builder.button(text="📚 Каталог FAQ", callback_data="faq_menu")
    builder.adjust(1)
    await message.answer(
        "🤔 Я не нашел точного ответа на ваш вопрос в автоматической базе знаний.\n\n"
        "Желаете передать диалог дежурному менеджеру поддержки?",
        reply_markup=builder.as_markup()
    )

@dp.message(Command("stats"))
async def cmd_stats(message: Message):
    if message.from_user.id != ADMIN_ID:
        return
        
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute("SELECT COUNT(*) FROM tickets") as cur:
            total_tickets = (await cur.fetchone())[0]
        async with db.execute("SELECT COUNT(*) FROM tickets WHERE status = 'open'") as cur:
            open_tickets = (await cur.fetchone())[0]
        async with db.execute("SELECT AVG(rating) FROM tickets WHERE rating IS NOT NULL") as cur:
            avg_rating = (await cur.fetchone())[0] or 0.0

    text = (
        "📊 <b>Аналитика службы поддержки:</b>\n\n"
        f"🎫 Всего тикетов: <b>{total_tickets}</b>\n"
        f"⏳ Открытых сейчас: <b>{open_tickets}</b>\n"
        f"⭐ Средняя оценка CSAT: <b>{avg_rating:.1f} / 5.0</b>"
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
