import asyncio
import logging
import os
import ssl
import calendar
from datetime import datetime, date, timedelta
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
    ReplyKeyboardMarkup,
    KeyboardButton,
    CallbackQuery,
    Message,
)
from aiogram.utils.keyboard import InlineKeyboardBuilder, ReplyKeyboardBuilder
from aiogram.client.session.aiohttp import AiohttpSession

DB_PATH = os.path.join(os.path.dirname(__file__), "booking.db")
ADMIN_ID = int(os.getenv("ADMIN_ID", "123456789"))

ALL_SLOTS = ["10:00", "11:30", "13:00", "14:30", "16:00", "17:30", "19:00"]

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

class BookingStates(StatesGroup):
    choosing_service = State()
    choosing_master = State()
    choosing_date = State()
    choosing_time = State()
    waiting_for_name = State()
    waiting_for_phone = State()

async def init_db():
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("""
            CREATE TABLE IF NOT EXISTS services (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT NOT NULL,
                duration INTEGER NOT NULL, -- в минутах
                price INTEGER NOT NULL,
                description TEXT
            )
        """)
        await db.execute("""
            CREATE TABLE IF NOT EXISTS masters (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT NOT NULL,
                role TEXT NOT NULL,
                experience TEXT
            )
        """)
        await db.execute("""
            CREATE TABLE IF NOT EXISTS bookings (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                booking_code TEXT UNIQUE,
                user_id INTEGER NOT NULL,
                client_name TEXT NOT NULL,
                client_phone TEXT NOT NULL,
                service_id INTEGER NOT NULL,
                master_id INTEGER NOT NULL,
                booking_date TEXT NOT NULL, -- YYYY-MM-DD
                booking_time TEXT NOT NULL, -- HH:MM
                status TEXT DEFAULT 'confirmed', -- confirmed, cancelled
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (service_id) REFERENCES services(id),
                FOREIGN KEY (master_id) REFERENCES masters(id)
            )
        """)
        

        async with db.execute("SELECT COUNT(*) FROM services") as cursor:
            count = (await cursor.fetchone())[0]
            if count == 0:
                await db.execute("""
                    INSERT INTO services (name, duration, price, description) VALUES 
                    ('✂️ Мужская стрижка Fade', 45, 1600, 'Мытье головы, моделирование формы, укладка премиум-средствами'),
                    ('🧔 Оформление и стрижка бороды', 30, 1100, 'Распаривание горячим полотенцем, подравнивание контуров опасной бритвой'),
                    ('🔥 Комплекс "Премиум" (Стрижка + Борода)', 75, 2400, 'Полный уход за волосами и бородой, спа-уход для лица'),
                    ('🧖‍♂️ Черная маска / Спа для лица', 20, 800, 'Глубокое очищение пор, охлаждающий компресс')
                """)
                await db.execute("""
                    INSERT INTO masters (name, role, experience) VALUES 
                    ('Артём «Blade»', 'Топ-барбер', 'Опыт 6 лет, чемпион барбер-баттла'),
                    ('Даниил Соколов', 'Старший барбер', 'Опыт 4 года, эксперт классических стрижек'),
                    ('Максим Кравц', 'Барбер', 'Опыт 2 года, мастер современных текстурных стрижек')
                """)
        await db.commit()

RU_MONTHS = [
    "", "Январь", "Февраль", "Март", "Апрель", "Май", "Июнь",
    "Июль", "Август", "Сентябрь", "Октябрь", "Ноябрь", "Декабрь"
]

def build_inline_calendar(year: int, month: int) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    

    builder.row(
        InlineKeyboardButton(text="◀️", callback_data=f"cal_nav_{year}_{month-1}"),
        InlineKeyboardButton(text=f"{RU_MONTHS[month]} {year}", callback_data="ignore"),
        InlineKeyboardButton(text="▶️", callback_data=f"cal_nav_{year}_{month+1}")
    )
    

    days_of_week = ["Пн", "Вт", "Ср", "Чт", "Пт", "Сб", "Вс"]
    builder.row(*[InlineKeyboardButton(text=day, callback_data="ignore") for day in days_of_week])
    

    cal = calendar.monthcalendar(year, month)
    today = date.today()
    
    for week in cal:
        row_buttons = []
        for day in week:
            if day == 0:
                row_buttons.append(InlineKeyboardButton(text=" ", callback_data="ignore"))
            else:
                day_date = date(year, month, day)

                if day_date < today:
                    row_buttons.append(InlineKeyboardButton(text=f"▫️{day}", callback_data="ignore"))
                else:
                    date_str = day_date.strftime("%Y-%m-%d")
                    row_buttons.append(InlineKeyboardButton(text=f"{day}", callback_data=f"cal_day_{date_str}"))
        builder.row(*row_buttons)
        
    builder.row(InlineKeyboardButton(text="🔙 Отмена", callback_data="main_menu"))
    return builder.as_markup()

def get_main_menu_kb(is_admin: bool = False) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.button(text="📅 Записаться на стрижку/услугу", callback_data="book_now")
    builder.button(text="📋 Мои активные записи", callback_data="my_bookings")
    builder.button(text="💈 О барбершопе и мастерах", callback_data="about_salon")
    if is_admin:
        builder.button(text="⚙️ Расписание на сегодня (Админ)", callback_data="admin_schedule")
    builder.adjust(1)
    return builder.as_markup()

async def get_services_kb() -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute("SELECT id, name, price FROM services") as cursor:
            services = await cursor.fetchall()
            for s_id, name, price in services:
                builder.button(text=f"{name} — {price} ₽", callback_data=f"srv_{s_id}")
    builder.button(text="🔙 В главное меню", callback_data="main_menu")
    builder.adjust(1)
    return builder.as_markup()

async def get_masters_kb() -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.button(text="🎲 Любой свободный мастер", callback_data="mst_0")
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute("SELECT id, name, role FROM masters") as cursor:
            masters = await cursor.fetchall()
            for m_id, name, role in masters:
                builder.button(text=f"👤 {name} ({role})", callback_data=f"mst_{m_id}")
    builder.button(text="🔙 Назад", callback_data="book_now")
    builder.adjust(1)
    return builder.as_markup()

async def get_available_time_slots_kb(selected_date: str, master_id: int) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    

    async with aiosqlite.connect(DB_PATH) as db:
        if master_id != 0:
            query = "SELECT booking_time FROM bookings WHERE booking_date = ? AND master_id = ? AND status = 'confirmed'"
            params = (selected_date, master_id)
        else:
            query = "SELECT booking_time FROM bookings WHERE booking_date = ? AND status = 'confirmed'"
            params = (selected_date,)
            
        async with db.execute(query, params) as cursor:
            booked_slots = {row[0] for row in await cursor.fetchall()}
            
    for slot in ALL_SLOTS:
        if slot in booked_slots and master_id != 0:
            builder.button(text=f"🔒 {slot}", callback_data="slot_taken")
        else:
            builder.button(text=f"🕒 {slot}", callback_data=f"time_{slot}")
            
    builder.button(text="🔙 Выбрать другую дату", callback_data="reselect_date")
    builder.adjust(2)
    return builder.as_markup()

dp = Dispatcher()

@dp.message(CommandStart())
async def cmd_start(message: Message):
    is_admin = (message.from_user.id == ADMIN_ID)
    await message.answer(
        f"👋 Здравствуйте, <b>{message.from_user.full_name}</b>!\n\n"
        "Добро пожаловать в сервис онлайн-записи барбершопа <b>«Gentlemen's Club»</b> 💈\n\n"
        "Здесь вы можете выбрать удобное время, мастера и записаться в 2 клика без звонков.",
        reply_markup=get_main_menu_kb(is_admin),
        parse_mode="HTML"
    )

@dp.callback_query(F.data == "main_menu")
async def cb_main_menu(callback: CallbackQuery, state: FSMContext):
    await state.clear()
    is_admin = (callback.from_user.id == ADMIN_ID)
    await callback.message.edit_text(
        "💈 Главное меню бронирования:",
        reply_markup=get_main_menu_kb(is_admin)
    )

@dp.callback_query(F.data == "book_now")
async def cb_start_booking(callback: CallbackQuery, state: FSMContext):
    await callback.answer()
    await state.set_state(BookingStates.choosing_service)
    await callback.message.edit_text(
        "✂️ <b>Шаг 1: Выберите желаемую услугу:</b>",
        reply_markup=await get_services_kb(),
        parse_mode="HTML"
    )

@dp.callback_query(BookingStates.choosing_service, F.data.startswith("srv_"))
async def cb_service_chosen(callback: CallbackQuery, state: FSMContext):
    service_id = int(callback.data.replace("srv_", ""))
    await state.update_data(service_id=service_id)
    await state.set_state(BookingStates.choosing_master)
    
    await callback.message.edit_text(
        "💈 <b>Шаг 2: Выберите барбера:</b>\n\n"
        "Вы можете доверить выбор системе или записаться к любимому мастеру:",
        reply_markup=await get_masters_kb(),
        parse_mode="HTML"
    )

@dp.callback_query(BookingStates.choosing_master, F.data.startswith("mst_"))
async def cb_master_chosen(callback: CallbackQuery, state: FSMContext):
    master_id = int(callback.data.replace("mst_", ""))
    await state.update_data(master_id=master_id)
    await state.set_state(BookingStates.choosing_date)
    
    today = date.today()
    calendar_kb = build_inline_calendar(today.year, today.month)
    await callback.message.edit_text(
        "📅 <b>Шаг 3: Выберите дату визита в календаре:</b>",
        reply_markup=calendar_kb,
        parse_mode="HTML"
    )

@dp.callback_query(F.data.startswith("cal_nav_"))
async def cb_calendar_nav(callback: CallbackQuery):
    _, _, y_str, m_str = callback.data.split("_")
    year = int(y_str)
    month = int(m_str)
    if month < 1:
        month = 12
        year -= 1
    elif month > 12:
        month = 1
        year += 1
    await callback.message.edit_reply_markup(reply_markup=build_inline_calendar(year, month))

@dp.callback_query(F.data == "ignore")
async def cb_calendar_ignore(callback: CallbackQuery):
    await callback.answer()

@dp.callback_query(F.data == "reselect_date")
async def cb_reselect_date(callback: CallbackQuery, state: FSMContext):
    await state.set_state(BookingStates.choosing_date)
    today = date.today()
    await callback.message.edit_text(
        "📅 Выберите новую дату визита:",
        reply_markup=build_inline_calendar(today.year, today.month)
    )

@dp.callback_query(BookingStates.choosing_date, F.data.startswith("cal_day_"))
async def cb_date_chosen(callback: CallbackQuery, state: FSMContext):
    selected_date_str = callback.data.replace("cal_day_", "")
    await state.update_data(booking_date=selected_date_str)
    
    data = await state.get_data()
    master_id = data.get("master_id", 0)
    
    await state.set_state(BookingStates.choosing_time)
    await callback.message.edit_text(
        f"🕒 <b>Шаг 4: Выберите удобное время на {selected_date_str}:</b>\n\n"
        "🔒 — время уже занято\n"
        "🕒 — свободно для бронирования",
        reply_markup=await get_available_time_slots_kb(selected_date_str, master_id),
        parse_mode="HTML"
    )

@dp.callback_query(F.data == "slot_taken")
async def cb_slot_taken(callback: CallbackQuery):
    await callback.answer("❌ Это время уже занято другим клиентом! Пожалуйста, выберите другое.", show_alert=True)

@dp.callback_query(BookingStates.choosing_time, F.data.startswith("time_"))
async def cb_time_chosen(callback: CallbackQuery, state: FSMContext):
    selected_time = callback.data.replace("time_", "")
    await state.update_data(booking_time=selected_time)
    
    await state.set_state(BookingStates.waiting_for_name)
    await callback.message.edit_text(
        "👤 <b>Шаг 5: Ваше имя</b>\n\nКак мастеру к вам обращаться при встрече?",
        parse_mode="HTML"
    )

@dp.message(BookingStates.waiting_for_name)
async def cb_receive_name(message: Message, state: FSMContext):
    await state.update_data(client_name=message.text.strip())
    await state.set_state(BookingStates.waiting_for_phone)
    
    kb = ReplyKeyboardMarkup(
        keyboard=[[KeyboardButton(text="📱 Поделиться номером", request_contact=True)]],
        resize_keyboard=True,
        one_time_keyboard=True
    )
    await message.answer(
        "📞 <b>Шаг 6: Номер телефона для подтверждения</b>\n\n"
        "Нажмите кнопку ниже или введите номер в формате +7 (999) 000-00-00:",
        reply_markup=kb,
        parse_mode="HTML"
    )

@dp.message(BookingStates.waiting_for_phone)
async def cb_receive_phone(message: Message, state: FSMContext, bot: Bot):
    if message.contact:
        phone = message.contact.phone_number
    else:
        phone = message.text.strip()
        
    data = await state.get_data()
    service_id = data["service_id"]
    master_id = data["master_id"]
    booking_date = data["booking_date"]
    booking_time = data["booking_time"]
    client_name = data["client_name"]
    user_id = message.from_user.id
    

    async with aiosqlite.connect(DB_PATH) as db:
        if master_id == 0:
            async with db.execute("SELECT id FROM masters LIMIT 1") as cur:
                master_id = (await cur.fetchone())[0]

        async with db.execute("SELECT name, price FROM services WHERE id = ?", (service_id,)) as cur:
            srv_row = await cur.fetchone()
            service_name, price = srv_row[0], srv_row[1]

        async with db.execute("SELECT name FROM masters WHERE id = ?", (master_id,)) as cur:
            master_name = (await cur.fetchone())[0]

        booking_code = f"GC-{datetime.now().strftime('%d%m')}-{os.urandom(2).hex().upper()}"
        
        await db.execute("""
            INSERT INTO bookings (booking_code, user_id, client_name, client_phone, service_id, master_id, booking_date, booking_time)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """, (booking_code, user_id, client_name, phone, service_id, master_id, booking_date, booking_time))
        await db.commit()

    await state.clear()
    
    ticket_text = (
        "🎉 <b>ВЫ УСПЕШНО ЗАПИСАНЫ!</b>\n\n"
        f"🎫 <b>Талон:</b> <code>{booking_code}</code>\n"
        f"✂️ <b>Услуга:</b> {service_name}\n"
        f"👤 <b>Мастер:</b> {master_name}\n"
        f"📅 <b>Дата:</b> {booking_date}\n"
        f"🕒 <b>Время:</b> {booking_time}\n"
        f"💰 <b>Стоимость:</b> {price} ₽\n"
        f"📍 <b>Адрес:</b> ул. Ленина, д. 42 (Барбершоп Gentlemen's Club)\n\n"
        "Мы пришлем вам напоминание за 2 часа до визита. До встречи!"
    )
    
    builder = InlineKeyboardBuilder()
    builder.button(text="📋 Мои записи", callback_data="my_bookings")
    builder.button(text="🏠 В главное меню", callback_data="main_menu")
    builder.adjust(1)
    
    await message.answer(ticket_text, reply_markup=types.ReplyKeyboardRemove(), parse_mode="HTML")
    await message.answer("Управление вашей записью:", reply_markup=builder.as_markup())
    

    try:
        admin_notice = (
            "🔔 <b>НОВАЯ ЗАПИСЬ НА УСЛУГУ!</b>\n\n"
            f"🎫 Код: <code>{booking_code}</code>\n"
            f"👤 Клиент: {client_name} ({phone})\n"
            f"✂️ Услуга: {service_name}\n"
            f"💈 Мастер: {master_name}\n"
            f"📅 Время: {booking_date} в {booking_time}"
        )
        await bot.send_message(ADMIN_ID, admin_notice, parse_mode="HTML")
    except Exception:
        pass

@dp.callback_query(F.data == "my_bookings")
async def cb_my_bookings(callback: CallbackQuery):
    user_id = callback.from_user.id
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute("""
            SELECT b.booking_code, s.name, m.name, b.booking_date, b.booking_time, b.id
            FROM bookings b
            JOIN services s ON b.service_id = s.id
            JOIN masters m ON b.master_id = m.id
            WHERE b.user_id = ? AND b.status = 'confirmed'
            ORDER BY b.booking_date ASC
        """, (user_id,)) as cursor:
            bookings = await cursor.fetchall()

    if not bookings:
        builder = InlineKeyboardBuilder()
        builder.button(text="📅 Записаться сейчас", callback_data="book_now")
        builder.button(text="🔙 Меню", callback_data="main_menu")
        builder.adjust(1)
        await callback.message.edit_text(
            "У вас нет активных записей.",
            reply_markup=builder.as_markup()
        )
        return

    text = "📋 <b>Ваши активные записи:</b>\n\n"
    builder = InlineKeyboardBuilder()
    for code, s_name, m_name, b_date, b_time, b_id in bookings:
        text += (
            f"🎫 <b>Талон:</b> <code>{code}</code>\n"
            f"✂️ {s_name}\n"
            f"👤 Мастер: {m_name}\n"
            f"🕒 {b_date} в {b_time}\n"
            f"———————————————\n"
        )
        builder.button(text=f"❌ Отменить {b_date} {b_time}", callback_data=f"cancel_{b_id}")

    builder.button(text="🔙 В главное меню", callback_data="main_menu")
    builder.adjust(1)
    await callback.message.edit_text(text, reply_markup=builder.as_markup(), parse_mode="HTML")

@dp.callback_query(F.data.startswith("cancel_"))
async def cb_cancel_booking(callback: CallbackQuery):
    booking_id = int(callback.data.replace("cancel_", ""))
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("UPDATE bookings SET status = 'cancelled' WHERE id = ?", (booking_id,))
        await db.commit()

    await callback.answer("Запись успешно отменена!", show_alert=True)
    await cb_my_bookings(callback)

@dp.callback_query(F.data == "about_salon")
async def cb_about_salon(callback: CallbackQuery):
    text = (
        "💈 <b>Барбершоп «Gentlemen's Club»</b>\n\n"
        "Мы создаем безупречный мужской стиль с 2018 года.\n\n"
        "📍 <b>Адрес:</b> ул. Ленина, д. 42 (м. Центр)\n"
        "🕒 <b>Режим работы:</b> Ежедневно с 10:00 до 21:00\n"
        "☕️ Вкусный зерновой кофе, крафтовые напитки и PlayStation для гостей!\n"
        "📞 Телефон: +7 (800) 555-35-35"
    )
    builder = InlineKeyboardBuilder()
    builder.button(text="📅 Записаться", callback_data="book_now")
    builder.button(text="🔙 Меню", callback_data="main_menu")
    builder.adjust(1)
    await callback.message.edit_text(text, reply_markup=builder.as_markup(), parse_mode="HTML")

@dp.callback_query(F.data == "admin_schedule")
async def cb_admin_schedule(callback: CallbackQuery):
    if callback.from_user.id != ADMIN_ID:
        await callback.answer("Доступ только для администратора!", show_alert=True)
        return

    today_str = date.today().strftime("%Y-%m-%d")
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute("""
            SELECT b.booking_time, b.client_name, b.client_phone, s.name, m.name
            FROM bookings b
            JOIN services s ON b.service_id = s.id
            JOIN masters m ON b.master_id = m.id
            WHERE b.booking_date = ? AND b.status = 'confirmed'
            ORDER BY b.booking_time ASC
        """, (today_str,)) as cursor:
            rows = await cursor.fetchall()

    if not rows:
        text = f"📅 <b>Расписание на сегодня ({today_str}):</b>\n\nЗаписей пока нет."
    else:
        text = f"📅 <b>Расписание на сегодня ({today_str}):</b>\n\n"
        for time_s, name, phone, srv, m_name in rows:
            text += f"🕒 <b>{time_s}</b> — {name} ({phone})\n   ✂️ {srv} (Мастер: {m_name})\n\n"

    builder = InlineKeyboardBuilder()
    builder.button(text="🔙 Меню", callback_data="main_menu")
    await callback.message.edit_text(text, reply_markup=builder.as_markup(), parse_mode="HTML")

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
