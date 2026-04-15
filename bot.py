#!/usr/bin/env python3
# -*- coding: utf-8 -*-
import os
import re
import json
import logging
import threading
import calendar
import csv
import io
import time
import requests
from datetime import datetime, timedelta
from http.server import HTTPServer, BaseHTTPRequestHandler

from dotenv import load_dotenv
import telebot
from telebot import types
import gspread
from oauth2client.service_account import ServiceAccountCredentials

# ==================== ВЕБ-СЕРВЕР ДЛЯ RENDER ====================
class HealthCheckHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.end_headers()
        self.wfile.write(b"OK")
    def log_message(self, format, *args):
        pass

def run_web_server():
    port = int(os.environ.get("PORT", 10000))
    server = HTTPServer(("0.0.0.0", port), HealthCheckHandler)
    server.serve_forever()

# ==================== НАСТРОЙКИ ====================
load_dotenv()
BOT_TOKEN = os.getenv("BOT_TOKEN")
SHEET_ID = os.getenv("SHEET_ID")
GOOGLE_CREDS_JSON = os.getenv("GOOGLE_CREDENTIALS")

if not GOOGLE_CREDS_JSON:
    raise ValueError("❌ GOOGLE_CREDENTIALS не задан в переменных окружения")

# Загрузка админов
ADMIN_ID = os.getenv("ADMIN_ID")
ADMIN_IDS_STR = os.getenv("ADMIN_IDS", "")
ADMIN_IDS = []

if ADMIN_ID:
    try:
        ADMIN_IDS.append(int(ADMIN_ID))
    except:
        pass

if ADMIN_IDS_STR:
    for x in ADMIN_IDS_STR.split(","):
        try:
            ADMIN_IDS.append(int(x.strip()))
        except:
            pass

ADMIN_IDS = list(set(ADMIN_IDS))

logging.basicConfig(format="%(asctime)s - %(levelname)s - %(message)s", level=logging.INFO)
logger = logging.getLogger(__name__)

# Google Sheets
scope = ["https://spreadsheets.google.com/feeds", "https://www.googleapis.com/auth/drive"]
creds_dict = json.loads(GOOGLE_CREDS_JSON)
creds = ServiceAccountCredentials.from_json_keyfile_dict(creds_dict, scope)
client = gspread.authorize(creds)
sheet = client.open_by_key(SHEET_ID).sheet1

bot = telebot.TeleBot(BOT_TOKEN)

user_state = {}
user_data = {}

# ==================== ПРОВЕРКА АДМИНА ====================
def is_admin(message_or_call):
    if not ADMIN_IDS:
        return True
    if hasattr(message_or_call, 'chat'):
        user_id = message_or_call.chat.id
    elif hasattr(message_or_call, 'message'):
        user_id = message_or_call.message.chat.id
    else:
        return False
    return user_id in ADMIN_IDS

# ==================== ГЛАВНОЕ МЕНЮ ====================
def main_menu():
    keyboard = types.ReplyKeyboardMarkup(resize_keyboard=True)
    keyboard.row("➕ Новый заказ", "🔍 Найти")
    keyboard.row("📅 Сегодня", "📅 Завтра")
    keyboard.row("📋 Все активные", "📊 Экспорт")
    return keyboard

# ==================== ОЧИСТКА ТЕЛЕФОНА ====================
def clean_phone(phone):
    if not phone:
        return None
    digits = re.sub(r'\D', '', phone)
    if digits.startswith('80') and len(digits) >= 11:
        digits = '375' + digits[2:]
    elif digits.startswith('8') and len(digits) == 11:
        digits = '7' + digits[1:]
    if digits.startswith('375') and len(digits) == 12:
        display = f"+{digits[:3]} {digits[3:5]} {digits[5:8]}-{digits[8:10]}-{digits[10:12]}"
    elif digits.startswith('7') and len(digits) == 11:
        display = f"+{digits[0]} {digits[1:4]} {digits[4:7]}-{digits[7:9]}-{digits[9:11]}"
    else:
        display = digits
    return {'raw': digits, 'display': display, 'tel': f"+{digits}"}

def format_phone_for_markdown(phone):
    if not phone or phone == "—":
        return "—"
    info = clean_phone(phone)
    if info:
        return f"[{info['display']}](tel:{info['tel']})"
    return phone

# ==================== КАЛЕНДАРЬ ====================
def get_calendar_keyboard(year=None, month=None):
    now = datetime.now()
    if year is None:
        year = now.year
    if month is None:
        month = now.month
    month_names = ['Январь', 'Февраль', 'Март', 'Апрель', 'Май', 'Июнь',
                   'Июль', 'Август', 'Сентябрь', 'Октябрь', 'Ноябрь', 'Декабрь']
    kb = types.InlineKeyboardMarkup(row_width=7)
    kb.add(types.InlineKeyboardButton(f"{month_names[month-1]} {year}", callback_data="ignore"))
    days_row = [types.InlineKeyboardButton(d, callback_data="ignore") for d in ['Пн', 'Вт', 'Ср', 'Чт', 'Пт', 'Сб', 'Вс']]
    kb.add(*days_row)
    cal = calendar.monthcalendar(year, month)
    for week in cal:
        row = []
        for day in week:
            if day == 0:
                row.append(types.InlineKeyboardButton(" ", callback_data="ignore"))
            else:
                date_str = f"{year}-{month:02d}-{day:02d}"
                row.append(types.InlineKeyboardButton(str(day), callback_data=f"calpick_{date_str}"))
        kb.add(*row)
    prev_month = month - 1 if month > 1 else 12
    prev_year = year if month > 1 else year - 1
    next_month = month + 1 if month < 12 else 1
    next_year = year if month < 12 else year + 1
    nav_row = [
        types.InlineKeyboardButton("<<", callback_data=f"cal_{prev_year}_{prev_month}"),
        types.InlineKeyboardButton(">>", callback_data=f"cal_{next_year}_{next_month}")
    ]
    kb.add(*nav_row)
    today = now.strftime("%Y-%m-%d")
    tomorrow = (now + timedelta(days=1)).strftime("%Y-%m-%d")
    quick_row = [
        types.InlineKeyboardButton("📅 Сегодня", callback_data=f"calpick_{today}"),
        types.InlineKeyboardButton("📅 Завтра", callback_data=f"calpick_{tomorrow}")
    ]
    kb.add(*quick_row)
    return kb

# ==================== РАБОТА С ТАБЛИЦЕЙ ====================
def get_next_id():
    rows = sheet.get_all_values()
    if len(rows) <= 1:
        return 1
    ids = []
    for r in rows[1:]:
        if r and r[0] and r[0].isdigit():
            ids.append(int(r[0]))
    return max(ids) + 1 if ids else 1

def add_order(client, phone, items, date, price="", order_type="Самовывоз", address=""):
    order_id = get_next_id()
    created = datetime.now().strftime("%d.%m.%Y %H:%M")
    sheet.append_row([str(order_id), created, client, phone, items, date, price, "Активен", order_type, address])
    return order_id

def get_order_by_id(order_id):
    rows = sheet.get_all_values()
    for r in rows[1:]:
        if len(r) > 0 and r[0] == str(order_id):
            while len(r) < 10:
                r.append("")
            return r
    return None

def get_active_orders():
    rows = sheet.get_all_values()
    if len(rows) <= 1:
        return []
    result = []
    for r in rows[1:]:
        if len(r) >= 8 and r[7] == "Активен":
            while len(r) < 10:
                r.append("")
            result.append(r)
    return result

def get_all_orders():
    rows = sheet.get_all_values()
    if len(rows) <= 1:
        return []
    return rows[1:]

def update_order_field(order_id, field_col, value):
    try:
        cell = sheet.find(str(order_id), in_column=1)
        if cell:
            sheet.update_cell(cell.row, field_col, str(value))
            return True
    except:
        pass
    return False

def update_status(order_id, status):
    return update_order_field(order_id, 8, status)

def update_price(order_id, price):
    return update_order_field(order_id, 7, str(price))

def update_items(order_id, items):
    return update_order_field(order_id, 5, items)

def update_client(order_id, name):
    return update_order_field(order_id, 3, name)

def update_phone(order_id, phone):
    return update_order_field(order_id, 4, phone)

def update_date(order_id, date):
    return update_order_field(order_id, 6, date)

def update_order_type(order_id, order_type):
    return update_order_field(order_id, 9, order_type)

def update_address(order_id, address):
    return update_order_field(order_id, 10, address)

def find_orders(query):
    orders = get_active_orders()
    q = query.lower().strip()
    result = []
    for o in orders:
        if len(o) > 3:
            name = o[2].lower() if o[2] else ""
            phone = o[3].lower() if o[3] else ""
            order_id = str(o[0])
            if q in name or q in phone or q == order_id:
                result.append(o)
    return result

def get_orders_by_date(date_str):
    orders = get_active_orders()
    result = []
    for o in orders:
        if len(o) > 5 and o[5] and date_str.lower() in o[5].lower():
            result.append(o)
    return result

# ==================== ЭКСПОРТ В CSV ====================
def export_orders_to_csv(orders):
    output = io.StringIO()
    writer = csv.writer(output, delimiter=';')
    writer.writerow(['ID', 'Создан', 'Клиент', 'Телефон', 'Позиции', 'Дата', 'Сумма', 'Статус', 'Тип', 'Адрес'])
    for order in orders:
        row = order[:10]
        while len(row) < 10:
            row.append("")
        writer.writerow(row)
    output.seek(0)
    return output.getvalue().encode('utf-8-sig')

# ==================== КНОПКИ ====================
def order_action_buttons(order_id):
    kb = types.InlineKeyboardMarkup(row_width=2)
    kb.add(
        types.InlineKeyboardButton("✅ Выдать", callback_data=f"done_{order_id}"),
        types.InlineKeyboardButton("✏️ Ред.", callback_data=f"edit_{order_id}")
    )
    kb.add(
        types.InlineKeyboardButton("📋 Сообщение клиенту", callback_data=f"msg_{order_id}")
    )
    return kb

def edit_menu_buttons(order_id):
    kb = types.InlineKeyboardMarkup(row_width=2)
    kb.add(
        types.InlineKeyboardButton("👤 Имя", callback_data=f"editname_{order_id}"),
        types.InlineKeyboardButton("📞 Тел", callback_data=f"editphone_{order_id}")
    )
    kb.add(
        types.InlineKeyboardButton("📦 Позиции", callback_data=f"edititems_{order_id}"),
        types.InlineKeyboardButton("📅 Дата", callback_data=f"editdate_{order_id}")
    )
    kb.add(
        types.InlineKeyboardButton("💰 Сумма", callback_data=f"editprice_{order_id}"),
        types.InlineKeyboardButton("🚗 Тип", callback_data=f"edittype_{order_id}")
    )
    kb.add(
        types.InlineKeyboardButton("📍 Адрес", callback_data=f"editaddress_{order_id}")
    )
    kb.add(
        types.InlineKeyboardButton("✅ Готово", callback_data=f"backto_{order_id}"),
        types.InlineKeyboardButton("❌ Отмена", callback_data=f"canceledit_{order_id}")
    )
    return kb

def format_order_message(order):
    order_id = order[0] if len(order) > 0 else "—"
    client = order[2] if len(order) > 2 else "—"
    phone = order[3] if len(order) > 3 else "—"
    items = order[4] if len(order) > 4 else "—"
    date = order[5] if len(order) > 5 else "—"
    price = order[6] if len(order) > 6 and order[6] else "—"
    order_type = order[8] if len(order) > 8 else "Самовывоз"
    address = order[9] if len(order) > 9 else ""
    
    type_emoji = "🚶" if order_type == "Самовывоз" else "🚚"
    
    msg = f"📋 Заказ #{order_id}\n"
    msg += f"👤 {client}\n"
    msg += f"📞 {format_phone_for_markdown(phone)}\n"
    msg += f"📦 {items}\n"
    msg += f"📅 {date}\n"
    msg += f"💰 {price}₽\n"
    msg += f"{type_emoji} {order_type}"
    if address:
        msg += f"\n📍 {address}"
    return msg

# ==================== ОБРАБОТЧИКИ ====================
@bot.message_handler(commands=['start'])
def start(message):
    if not is_admin(message):
        bot.reply_to(message, "⛔ Доступ запрещён.")
        return
    chat_id = message.chat.id
    user_state[chat_id] = None
    user_data[chat_id] = {}
    bot.send_message(
        chat_id,
        "🔥 CRM LARICH FOOD\n\n"
        "Нажмите «➕ Новый заказ» для создания заказа.\n\n"
        "Поиск: /find\n"
        "Заказы на сегодня: /today\n"
        "Заказы на завтра: /tomorrow",
        reply_markup=main_menu()
    )

@bot.message_handler(commands=['find'])
def cmd_find(message):
    if not is_admin(message):
        return
    chat_id = message.chat.id
    user_state[chat_id] = "WAIT_SEARCH"
    bot.send_message(chat_id, "🔍 Введите имя или телефон для поиска:")

@bot.message_handler(commands=['today'])
def cmd_today(message):
    if not is_admin(message):
        return
    chat_id = message.chat.id
    orders = get_orders_by_date("сегодня")
    if not orders:
        bot.send_message(chat_id, "📭 Нет заказов на сегодня.")
    else:
        bot.send_message(chat_id, f"📅 Заказов на сегодня: {len(orders)}")
        for o in orders:
            msg = format_order_message(o)
            bot.send_message(chat_id, msg, reply_markup=order_action_buttons(o[0]), parse_mode='Markdown')

@bot.message_handler(commands=['tomorrow'])
def cmd_tomorrow(message):
    if not is_admin(message):
        return
    chat_id = message.chat.id
    orders = get_orders_by_date("завтра")
    if not orders:
        bot.send_message(chat_id, "📭 Нет заказов на завтра.")
    else:
        bot.send_message(chat_id, f"📅 Заказов на завтра: {len(orders)}")
        for o in orders:
            msg = format_order_message(o)
            bot.send_message(chat_id, msg, reply_markup=order_action_buttons(o[0]), parse_mode='Markdown')

@bot.message_handler(func=lambda m: True)
def handle_message(message):
    if not is_admin(message):
        bot.reply_to(message, "⛔ Доступ запрещён.")
        return
    chat_id = message.chat.id
    text = message.text
    
    if chat_id not in user_state:
        user_state[chat_id] = None
        user_data[chat_id] = {}
    
    state = user_state.get(chat_id)
    
    # Кнопки меню — сбрасываем состояние
    if text in ["➕ Новый заказ", "🔍 Найти", "📅 Сегодня", "📅 Завтра", "📋 Все активные", "📊 Экспорт"]:
        user_state[chat_id] = None
    
    if text == "➕ Новый заказ":
        user_state[chat_id] = "WAIT_NAME"
        bot.send_message(chat_id, "👤 Введите имя клиента:")
        return
    
    elif text == "🔍 Найти":
        user_state[chat_id] = "WAIT_SEARCH"
        bot.send_message(chat_id, "Введите имя или телефон:")
        return
    
    elif text == "📅 Сегодня":
        orders = get_orders_by_date("сегодня")
        if not orders:
            bot.send_message(chat_id, "📭 Нет заказов на сегодня.", reply_markup=main_menu())
        else:
            bot.send_message(chat_id, f"📅 Заказов на сегодня: {len(orders)}", reply_markup=main_menu())
            for o in orders:
                msg = format_order_message(o)
                bot.send_message(chat_id, msg, reply_markup=order_action_buttons(o[0]), parse_mode='Markdown')
        return
    
    elif text == "📅 Завтра":
        orders = get_orders_by_date("завтра")
        if not orders:
            bot.send_message(chat_id, "📭 Нет заказов на завтра.", reply_markup=main_menu())
        else:
            bot.send_message(chat_id, f"📅 Заказов на завтра: {len(orders)}", reply_markup=main_menu())
            for o in orders:
                msg = format_order_message(o)
                bot.send_message(chat_id, msg, reply_markup=order_action_buttons(o[0]), parse_mode='Markdown')
        return
    
    elif text == "📋 Все активные":
        orders = get_active_orders()
        if not orders:
            bot.send_message(chat_id, "📭 Нет активных заказов.", reply_markup=main_menu())
        else:
            bot.send_message(chat_id, f"📋 Всего активных: {len(orders)}", reply_markup=main_menu())
            for o in orders[:10]:
                msg = format_order_message(o)
                bot.send_message(chat_id, msg, reply_markup=order_action_buttons(o[0]), parse_mode='Markdown')
            if len(orders) > 10:
                bot.send_message(chat_id, f"... и ещё {len(orders)-10} заказов.")
        return
    
    elif text == "📊 Экспорт":
        kb = types.InlineKeyboardMarkup(row_width=2)
        kb.add(
            types.InlineKeyboardButton("Сегодня", callback_data="export_сегодня"),
            types.InlineKeyboardButton("Завтра", callback_data="export_завтра")
        )
        kb.add(
            types.InlineKeyboardButton("Все активные", callback_data="export_все"),
            types.InlineKeyboardButton("Все заказы", callback_data="export_всевсе")
        )
        bot.send_message(chat_id, "📊 Выберите период для экспорта:", reply_markup=kb)
        return
    
    # Обработка состояний
    if state == "WAIT_NAME":
        user_data[chat_id]["new_name"] = text
        user_state[chat_id] = "WAIT_PHONE"
        bot.send_message(chat_id, "📞 Введите номер телефона (или '-' если нет):")
    
    elif state == "WAIT_PHONE":
        phone = text if text != "-" else ""
        user_data[chat_id]["new_phone"] = phone
        user_state[chat_id] = "WAIT_ITEMS"
        bot.send_message(chat_id, "📦 Введите позиции заказа:")
    
    elif state == "WAIT_ITEMS":
        user_data[chat_id]["new_items"] = text
        user_state[chat_id] = "WAIT_TYPE"
        kb = types.InlineKeyboardMarkup(row_width=2)
        kb.add(
            types.InlineKeyboardButton("🚶 Самовывоз", callback_data="newtype_Самовывоз"),
            types.InlineKeyboardButton("🚚 Доставка", callback_data="newtype_Доставка")
        )
        bot.send_message(chat_id, "🚗 Выберите тип заказа:", reply_markup=kb)
    
    elif state == "WAIT_ADDRESS":
        address = text
        user_data[chat_id]["new_address"] = address
        user_state[chat_id] = "WAIT_PRICE"
        bot.send_message(chat_id, "💰 Введите стоимость (или '-' если неизвестно):")
    
    elif state == "WAIT_PRICE":
        price = text if text != "-" else ""
        user_data[chat_id]["new_price"] = price
        user_state[chat_id] = "WAIT_DATE"
        kb = get_calendar_keyboard()
        bot.send_message(chat_id, "📅 Выберите дату выдачи:", reply_markup=kb)
    
    elif state == "WAIT_SEARCH":
        orders = find_orders(text)
        if orders:
            bot.send_message(chat_id, f"🔍 Найдено: {len(orders)}", reply_markup=main_menu())
            for o in orders[:10]:
                msg = format_order_message(o)
                bot.send_message(chat_id, msg, reply_markup=order_action_buttons(o[0]), parse_mode='Markdown')
        else:
            bot.send_message(chat_id, "❌ Ничего не найдено.", reply_markup=main_menu())
        user_state[chat_id] = None
    
    elif state and state.startswith("EDIT_"):
        parts = state.split("_")
        if len(parts) >= 3:
            field = parts[1]
            order_id = parts[2]
            
            if field == "NAME":
                if update_client(order_id, text):
                    bot.send_message(chat_id, f"✅ Имя изменено на {text}")
                else:
                    bot.send_message(chat_id, "❌ Ошибка")
            elif field == "PHONE":
                phone_info = clean_phone(text)
                if phone_info and update_phone(order_id, phone_info['raw']):
                    bot.send_message(chat_id, f"✅ Телефон изменён на {phone_info['display']}")
                else:
                    bot.send_message(chat_id, "❌ Ошибка")
            elif field == "ITEMS":
                if update_items(order_id, text):
                    bot.send_message(chat_id, "✅ Позиции изменены")
                else:
                    bot.send_message(chat_id, "❌ Ошибка")
            elif field == "DATE":
                if update_date(order_id, text):
                    bot.send_message(chat_id, f"✅ Дата изменена на {text}")
                else:
                    bot.send_message(chat_id, "❌ Ошибка")
            elif field == "PRICE":
                price_match = re.search(r'\d+', text)
                if price_match and update_price(order_id, price_match.group()):
                    bot.send_message(chat_id, f"✅ Сумма изменена на {price_match.group()}₽")
                else:
                    bot.send_message(chat_id, "❌ Ошибка")
            elif field == "ADDRESS":
                if update_address(order_id, text):
                    bot.send_message(chat_id, f"✅ Адрес изменён на {text}")
                else:
                    bot.send_message(chat_id, "❌ Ошибка")
            
            order = get_order_by_id(order_id)
            if order:
                msg = format_order_message(order)
                bot.send_message(chat_id, msg, reply_markup=order_action_buttons(order_id), parse_mode='Markdown')
        
        user_state[chat_id] = None
    
    elif state == "WAIT_TEMP_ADDRESS":
        address = text
        name = user_data[chat_id].get("temp_name", "")
        phone = user_data[chat_id].get("temp_phone", "")
        items = user_data[chat_id].get("temp_items", "")
        date = user_data[chat_id].get("temp_date", "сегодня")
        price = user_data[chat_id].get("temp_price", "")
        order_type = user_data[chat_id].get("temp_type", "Доставка")
        
        order_id = add_order(name, phone, items, date, price, order_type, address)
        
        msg = f"✅ Заказ #{order_id} создан!\n\n"
        msg += f"👤 {name}\n"
        msg += f"📞 {format_phone_for_markdown(phone)}\n"
        msg += f"📦 {items}\n"
        msg += f"📅 {date}\n"
        if price:
            msg += f"💰 {price}₽\n"
        msg += f"🚚 Доставка\n"
        msg += f"📍 {address}"
        
        bot.send_message(chat_id, msg, reply_markup=order_action_buttons(order_id), parse_mode='Markdown')
        bot.send_message(chat_id, "Готово!", reply_markup=main_menu())
        
        for key in ["temp_name", "temp_phone", "temp_items", "temp_date", "temp_price", "temp_type"]:
            user_data[chat_id].pop(key, None)
        user_state[chat_id] = None

# ==================== INLINE-КНОПКИ ====================
@bot.callback_query_handler(func=lambda call: True)
def handle_callback(call):
    if not is_admin(call):
        bot.answer_callback_query(call.id, "⛔ Доступ запрещён.", show_alert=True)
        return
    chat_id = call.message.chat.id
    data = call.data
    
    # Выдать заказ
    if data.startswith("done_"):
        order_id = data.split("_")[1]
        if update_status(order_id, "Выдан"):
            bot.answer_callback_query(call.id, "✅ Отмечено как выданное")
            bot.edit_message_text(f"✅ Заказ #{order_id} выдан.", chat_id, call.message.message_id)
        else:
            bot.answer_callback_query(call.id, "❌ Ошибка")
    
    # Сообщение клиенту
    elif data.startswith("msg_"):
        order_id = data.split("_")[1]
        order = get_order_by_id(order_id)
        if order:
            client = order[2] if len(order) > 2 else "Клиент"
            items = order[4] if len(order) > 4 else ""
            date = order[5] if len(order) > 5 else ""
            price = order[6] if len(order) > 6 and order[6] else ""
            order_type = order[8] if len(order) > 8 else "Самовывоз"
            address = order[9] if len(order) > 9 else ""
            
            msg = f"✅ Ваш заказ #{order_id} в Larich Food принят!\n\n"
            msg += f"Состав: {items}\n"
            msg += f"Дата: {date}\n"
            if price:
                msg += f"Сумма: {price}₽\n"
            msg += f"Тип: {'🚶 Самовывоз' if order_type == 'Самовывоз' else '🚚 Доставка'}\n"
            if address:
                msg += f"Адрес: {address}\n"
            msg += "\nСпасибо за заказ! Мы свяжемся с вами перед выдачей."
            
            bot.answer_callback_query(call.id, "✅ Текст готов, скопируйте ниже")
            bot.send_message(chat_id, f"<pre>{msg}</pre>", parse_mode='HTML')
        else:
            bot.answer_callback_query(call.id, "❌ Заказ не найден")
    
    # Меню редактирования
    elif data.startswith("edit_"):
        order_id = data.split("_")[1]
        order = get_order_by_id(order_id)
        if order:
            msg = format_order_message(order) + "\n\n✏️ Что изменить?"
            bot.edit_message_text(msg, chat_id, call.message.message_id, reply_markup=edit_menu_buttons(order_id), parse_mode='Markdown')
        else:
            bot.answer_callback_query(call.id, "Заказ не найден")
    
    # Назад к заказу
    elif data.startswith("backto_"):
        order_id = data.split("_")[1]
        order = get_order_by_id(order_id)
        if order:
            msg = format_order_message(order)
            bot.edit_message_text(msg, chat_id, call.message.message_id, reply_markup=order_action_buttons(order_id), parse_mode='Markdown')
        else:
            bot.answer_callback_query(call.id, "Заказ не найден")
        user_state[chat_id] = None
    
    # Отмена редактирования
    elif data.startswith("canceledit_"):
        order_id = data.split("_")[1]
        order = get_order_by_id(order_id)
        if order:
            msg = format_order_message(order)
            bot.edit_message_text(msg, chat_id, call.message.message_id, reply_markup=order_action_buttons(order_id), parse_mode='Markdown')
        else:
            bot.edit_message_text("❌ Заказ не найден.", chat_id, call.message.message_id)
        user_state[chat_id] = None
        bot.answer_callback_query(call.id, "Редактирование отменено")
    
    # Редактирование полей
    elif data.startswith("editname_"):
        order_id = data.split("_")[1]
        user_state[chat_id] = f"EDIT_NAME_{order_id}"
        bot.edit_message_text("👤 Введите новое имя:", chat_id, call.message.message_id)
    
    elif data.startswith("editphone_"):
        order_id = data.split("_")[1]
        user_state[chat_id] = f"EDIT_PHONE_{order_id}"
        bot.edit_message_text("📞 Введите новый телефон:", chat_id, call.message.message_id)
    
    elif data.startswith("edititems_"):
        order_id = data.split("_")[1]
        user_state[chat_id] = f"EDIT_ITEMS_{order_id}"
        bot.edit_message_text("📦 Введите новые позиции:", chat_id, call.message.message_id)
    
    elif data.startswith("editdate_"):
        order_id = data.split("_")[1]
        user_state[chat_id] = f"EDIT_DATE_{order_id}"
        kb = get_calendar_keyboard()
        bot.edit_message_text("📅 Выберите новую дату:", chat_id, call.message.message_id, reply_markup=kb)
    
    elif data.startswith("editprice_"):
        order_id = data.split("_")[1]
        user_state[chat_id] = f"EDIT_PRICE_{order_id}"
        bot.edit_message_text("💰 Введите новую сумму:", chat_id, call.message.message_id)
    
    elif data.startswith("edittype_"):
        order_id = data.split("_")[1]
        kb = types.InlineKeyboardMarkup(row_width=2)
        kb.add(
            types.InlineKeyboardButton("🚶 Самовывоз", callback_data=f"settype_{order_id}_Самовывоз"),
            types.InlineKeyboardButton("🚚 Доставка", callback_data=f"settype_{order_id}_Доставка")
        )
        bot.edit_message_text("🚗 Выберите тип заказа:", chat_id, call.message.message_id, reply_markup=kb)
    
    elif data.startswith("editaddress_"):
        order_id = data.split("_")[1]
        user_state[chat_id] = f"EDIT_ADDRESS_{order_id}"
        bot.edit_message_text("📍 Введите новый адрес:", chat_id, call.message.message_id)
    
    elif data.startswith("settype_"):
        parts = data.split("_")
        order_id = parts[1]
        order_type = parts[2]
        if update_order_type(order_id, order_type):
            bot.answer_callback_query(call.id, f"✅ Тип изменён на {order_type}")
            order = get_order_by_id(order_id)
            if order:
                msg = format_order_message(order)
                bot.edit_message_text(msg, chat_id, call.message.message_id, reply_markup=order_action_buttons(order_id), parse_mode='Markdown')
        else:
            bot.answer_callback_query(call.id, "❌ Ошибка")
    
    # Выбор типа при создании (новый заказ)
    elif data.startswith("newtype_"):
        order_type = data.split("_")[1]
        user_data[chat_id]["new_type"] = order_type
        
        if order_type == "Доставка":
            user_state[chat_id] = "WAIT_ADDRESS"
            bot.edit_message_text("📍 Введите адрес доставки:", chat_id, call.message.message_id)
        else:
            user_state[chat_id] = "WAIT_PRICE"
            bot.edit_message_text("💰 Введите стоимость (или '-' если неизвестно):", chat_id, call.message.message_id)
    
    # Выбор типа при создании из календаря
    elif data.startswith("temp_"):
        order_type = data.split("_")[1]
        if order_type == "Доставка":
            user_state[chat_id] = "WAIT_TEMP_ADDRESS"
            user_data[chat_id]["temp_type"] = order_type
            bot.edit_message_text("📍 Введите адрес доставки:", chat_id, call.message.message_id)
        else:
            name = user_data[chat_id].get("temp_name", "")
            phone = user_data[chat_id].get("temp_phone", "")
            items = user_data[chat_id].get("temp_items", "")
            date = user_data[chat_id].get("temp_date", "сегодня")
            price = user_data[chat_id].get("temp_price", "")
            
            order_id = add_order(name, phone, items, date, price, order_type)
            
            msg = f"✅ Заказ #{order_id} создан!\n\n"
            msg += f"👤 {name}\n"
            msg += f"📞 {format_phone_for_markdown(phone)}\n"
            msg += f"📦 {items}\n"
            msg += f"📅 {date}\n"
            if price:
                msg += f"💰 {price}₽\n"
            msg += f"🚶 Самовывоз"
            
            bot.edit_message_text(msg, chat_id, call.message.message_id, parse_mode='Markdown')
            bot.send_message(chat_id, "Готово!", reply_markup=main_menu())
            
            for key in ["temp_name", "temp_phone", "temp_items", "temp_date", "temp_price", "temp_type"]:
                user_data[chat_id].pop(key, None)
            user_state[chat_id] = None
    
    # Экспорт
    elif data.startswith("export_"):
        period = data.split("_")[1]
        if period == "сегодня":
            orders = get_orders_by_date("сегодня")
            filename = "заказы_сегодня.csv"
        elif period == "завтра":
            orders = get_orders_by_date("завтра")
            filename = "заказы_завтра.csv"
        elif period == "все":
            orders = get_active_orders()
            filename = "все_активные_заказы.csv"
        elif period == "всевсе":
            orders = get_all_orders()
            filename = "все_заказы.csv"
        else:
            orders = get_active_orders()
            filename = "заказы.csv"
        
        if orders:
            csv_data = export_orders_to_csv(orders)
            bot.send_document(chat_id, (filename, csv_data), caption=f"📊 Экспорт: {len(orders)} заказов")
            bot.answer_callback_query(call.id, "✅ Файл готов!")
        else:
            bot.answer_callback_query(call.id, "📭 Нет заказов за этот период.")
    
    # Календарь
    elif data.startswith("cal_"):
        parts = data.split("_")
        if len(parts) == 3:
            year = int(parts[1])
            month = int(parts[2])
            kb = get_calendar_keyboard(year, month)
            bot.edit_message_reply_markup(chat_id, call.message.message_id, reply_markup=kb)
        else:
            bot.answer_callback_query(call.id)
    
    elif data.startswith("calpick_"):
        date_str = data.split("_")[1]
        try:
            dt = datetime.strptime(date_str, "%Y-%m-%d")
            months_ru = ['января', 'февраля', 'марта', 'апреля', 'мая', 'июня',
                         'июля', 'августа', 'сентября', 'октября', 'ноября', 'декабря']
            display_date = f"{dt.day} {months_ru[dt.month-1]}"
        except:
            display_date = date_str
        
        state = user_state.get(chat_id)
        
        if state == "WAIT_DATE":
            name = user_data[chat_id].get("new_name", "")
            phone = user_data[chat_id].get("new_phone", "")
            phone_clean = clean_phone(phone)['raw'] if phone else ""
            items = user_data[chat_id].get("new_items", "")
            price = user_data[chat_id].get("new_price", "")
            order_type = user_data[chat_id].get("new_type", "Самовывоз")
            address = user_data[chat_id].get("new_address", "")
            
            order_id = add_order(name, phone_clean, items, display_date, price, order_type, address)
            
            msg = f"✅ Заказ #{order_id} создан!\n\n"
            msg += f"👤 {name}\n"
            msg += f"📞 {format_phone_for_markdown(phone_clean)}\n"
            msg += f"📦 {items}\n"
            msg += f"📅 {display_date}\n"
            if price:
                msg += f"💰 {price}₽\n"
            msg += f"{'🚶' if order_type == 'Самовывоз' else '🚚'} {order_type}"
            if address:
                msg += f"\n📍 {address}"
            
            bot.edit_message_text(msg, chat_id, call.message.message_id, parse_mode='Markdown')
            bot.send_message(chat_id, "Готово!", reply_markup=main_menu())
            
            for key in ["new_name", "new_phone", "new_items", "new_price", "new_type", "new_address"]:
                user_data[chat_id].pop(key, None)
            user_state[chat_id] = None
        
        elif state and state.startswith("EDIT_DATE_"):
            order_id = state.split("_")[2]
            if update_date(order_id, display_date):
                bot.answer_callback_query(call.id, f"✅ Дата изменена на {display_date}")
                order = get_order_by_id(order_id)
                if order:
                    msg = format_order_message(order)
                    bot.edit_message_text(msg, chat_id, call.message.message_id, reply_markup=order_action_buttons(order_id), parse_mode='Markdown')
            else:
                bot.answer_callback_query(call.id, "❌ Ошибка")
            user_state[chat_id] = None
        
        else:
            bot.answer_callback_query(call.id, f"Выбрана дата: {display_date}")
    
    elif data == "ignore":
        bot.answer_callback_query(call.id)

# ==================== ЗАПУСК ====================
def main():
    try:
        requests.get(f"https://api.telegram.org/bot{BOT_TOKEN}/deleteWebhook?drop_pending_updates=true", timeout=5)
        logger.info("✅ Старые вебхуки и pending updates очищены")
    except Exception as e:
        logger.warning(f"⚠️ Не удалось очистить вебхуки: {e}")
    
    threading.Thread(target=run_web_server, daemon=True).start()
    logger.info("🌐 Веб-сервер запущен")
    logger.info("🤖 Бот запущен...")
    
    while True:
        try:
            bot.polling(none_stop=True)
        except Exception as e:
            logger.error(f"❌ Ошибка polling: {e}")
            logger.info("🔄 Перезапуск через 5 секунд...")
            time.sleep(5)

if __name__ == "__main__":
    main()
