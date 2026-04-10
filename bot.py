#!/usr/bin/env python3
# -*- coding: utf-8 -*-
import os
import re
import json
import logging
import threading
from datetime import datetime
from http.server import HTTPServer, BaseHTTPRequestHandler

from dotenv import load_dotenv
import telebot
from telebot import types
import gspread
from oauth2client.service_account import ServiceAccountCredentials

# ==================== ФИКТИВНЫЙ ВЕБ-СЕРВЕР (для Render Web Service) ====================
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

logging.basicConfig(format="%(asctime)s - %(levelname)s - %(message)s", level=logging.INFO)
logger = logging.getLogger(__name__)

# Google Sheets авторизация
scope = ["https://spreadsheets.google.com/feeds", "https://www.googleapis.com/auth/drive"]
creds_dict = json.loads(GOOGLE_CREDS_JSON)
creds = ServiceAccountCredentials.from_json_keyfile_dict(creds_dict, scope)
client = gspread.authorize(creds)
sheet = client.open_by_key(SHEET_ID).sheet1

# Бот
bot = telebot.TeleBot(BOT_TOKEN)

# Хранилище состояний пользователей
user_state = {}
user_data = {}

# ==================== ГЛАВНОЕ МЕНЮ (КНОПКИ) ====================
def main_menu():
    keyboard = types.ReplyKeyboardMarkup(resize_keyboard=True)
    keyboard.row("➕ Новый заказ", "🔍 Найти")
    keyboard.row("📅 Сегодня", "📅 Завтра")
    keyboard.row("📋 Все активные")
    return keyboard

# ==================== РАБОТА С ТАБЛИЦЕЙ ====================
def get_next_id():
    rows = sheet.get_all_values()
    if len(rows) <= 1:
        return 1
    ids = []
    for r in rows[1:]:
        if r and r[0].isdigit():
            ids.append(int(r[0]))
    return max(ids) + 1 if ids else 1

def add_order(client, phone, items, date, price=""):
    order_id = get_next_id()
    created = datetime.now().strftime("%d.%m.%Y %H:%M")
    sheet.append_row([str(order_id), created, client, phone, items, date, price, "Активен"])
    return order_id

def get_active_orders():
    rows = sheet.get_all_values()
    if len(rows) <= 1:
        return []
    return [r for r in rows[1:] if len(r) >= 8 and r[7] == "Активен"]

def update_status(order_id, status):
    try:
        cell = sheet.find(str(order_id))
        if cell:
            sheet.update_cell(cell.row, 8, status)
            return True
    except:
        pass
    return False

def update_price(order_id, price):
    try:
        cell = sheet.find(str(order_id))
        if cell:
            sheet.update_cell(cell.row, 7, str(price))
            return True
    except:
        pass
    return False

def find_orders(query):
    orders = get_active_orders()
    q = query.lower()
    result = []
    for o in orders:
        if len(o) > 3:
            name = o[2].lower() if o[2] else ""
            phone = o[3].lower() if o[3] else ""
            order_id = o[0]
            if q in name or q in phone or q == order_id:
                result.append(o)
    return result

def get_orders_by_date(date_str):
    orders = get_active_orders()
    result = []
    for o in orders:
        if len(o) > 5 and date_str.lower() in o[5].lower():
            result.append(o)
    return result

# ==================== ПАРСИНГ ТЕКСТА ====================
def parse_text(text):
    result = {"name": None, "phone": None, "items": [], "date": None}
    
    phone = re.search(r'(\+?7\d{10}|\+?\d{10,12})', text.replace(" ", "").replace("-", ""))
    if phone:
        result["phone"] = phone.group(1)
    
    name = re.search(r'([А-Яа-я]{2,})\s*(?:тел|7|8|\+|,)', text, re.IGNORECASE)
    if name:
        result["name"] = name.group(1).capitalize()
    else:
        first = re.search(r'^([А-Яа-я]{2,})', text, re.IGNORECASE)
        if first:
            result["name"] = first.group(1).capitalize()
    
    items = re.findall(r'([А-Яа-я]{3,})\s+(\d+\.?\d*)\s*(?:кг|kg)?', text, re.IGNORECASE)
    for item, w in items:
        result["items"].append(f"{item.capitalize()} {w}кг")
    
    date = re.search(r'(понедельник|вторник|сред[ау]|четверг|пятниц[ау]|суббот[ау]|воскресенье|\d{1,2}[\.-]\d{1,2})', text, re.IGNORECASE)
    result["date"] = date.group(1).lower() if date else "сегодня"
    
    return result

# ==================== ОБРАБОТЧИКИ КОМАНД ====================
@bot.message_handler(commands=['start'])
def start(message):
    chat_id = message.chat.id
    user_state[chat_id] = None
    user_data[chat_id] = {}
    bot.send_message(
        chat_id,
        "🔥 CRM КОПТИЛЬНЯ\n\nОтправьте текст заказа или используйте кнопки.",
        reply_markup=main_menu()
    )

@bot.message_handler(func=lambda m: True)
def handle_message(message):
    chat_id = message.chat.id
    text = message.text
    
    # Инициализация состояния
    if chat_id not in user_state:
        user_state[chat_id] = None
        user_data[chat_id] = {}
    
    state = user_state.get(chat_id)
    
    # Кнопки меню
    if text == "➕ Новый заказ":
        user_state[chat_id] = "WAIT_NAME"
        bot.send_message(chat_id, "Введите имя клиента:")
        return
    
    elif text == "🔍 Найти":
        user_state[chat_id] = "WAIT_SEARCH"
        bot.send_message(chat_id, "Введите имя или телефон:")
        return
    
    elif text == "📅 Сегодня":
        orders = get_orders_by_date("сегодня")
        show_orders(chat_id, orders, "сегодня")
        return
    
    elif text == "📅 Завтра":
        orders = get_orders_by_date("завтра")
        show_orders(chat_id, orders, "завтра")
        return
    
    elif text == "📋 Все активные":
        orders = get_active_orders()
        show_orders(chat_id, orders, "все")
        return
    
    # Обработка состояний
    elif state == "WAIT_NAME":
        user_data[chat_id]["new_name"] = text
        user_state[chat_id] = "WAIT_PHONE"
        bot.send_message(chat_id, "Введите телефон:")
    
    elif state == "WAIT_PHONE":
        user_data[chat_id]["new_phone"] = text
        user_state[chat_id] = "WAIT_ITEMS"
        bot.send_message(chat_id, "Введите позиции (например: Грудинка 1.5 кг, Рёбра 1 кг):")
    
    elif state == "WAIT_ITEMS":
        user_data[chat_id]["new_items"] = text
        user_state[chat_id] = "WAIT_DATE"
        
        kb = types.InlineKeyboardMarkup()
        kb.add(
            types.InlineKeyboardButton("Сегодня", callback_data="date_сегодня"),
            types.InlineKeyboardButton("Завтра", callback_data="date_завтра")
        )
        bot.send_message(chat_id, "Когда отдать?", reply_markup=kb)
    
    elif state == "WAIT_SEARCH":
        orders = find_orders(text)
        if orders:
            reply = "🔍 Найдено:\n\n"
            for o in orders[:5]:
                reply += f"#{o[0]} | {o[2]} | {o[3]}\n📦 {o[4]}\n💰 {o[6] if o[6] else '—'}₽\n\n"
            bot.send_message(chat_id, reply, reply_markup=main_menu())
        else:
            bot.send_message(chat_id, "Ничего не найдено.", reply_markup=main_menu())
        user_state[chat_id] = None
    
    elif state == "WAIT_PRICE":
        order_id = user_data[chat_id].get("price_for")
        price_match = re.search(r'\d+', text)
        if price_match and order_id:
            price = price_match.group()
            if update_price(order_id, price):
                bot.send_message(chat_id, f"✅ Сумма {price}₽ сохранена.", reply_markup=main_menu())
            else:
                bot.send_message(chat_id, "❌ Ошибка.", reply_markup=main_menu())
        user_state[chat_id] = None
        user_data[chat_id]["price_for"] = None
    
    # Автораспознавание текста заказа
    elif re.search(r'\d{10}|\d+\.?\d*\s*кг', text, re.IGNORECASE):
        p = parse_text(text)
        if p["name"] or p["phone"]:
            items_text = ", ".join(p["items"]) if p["items"] else "Не указано"
            order_id = add_order(p["name"] or "—", p["phone"] or "—", items_text, p["date"])
            
            reply = f"✅ Заказ #{order_id}\n👤 {p['name']}\n📞 {p['phone']}\n📅 {p['date']}\n📦 {items_text}"
            
            kb = types.InlineKeyboardMarkup()
            kb.add(
                types.InlineKeyboardButton("✅ Выдать", callback_data=f"done_{order_id}"),
                types.InlineKeyboardButton("💰 Сумма", callback_data=f"price_{order_id}")
            )
            bot.send_message(chat_id, reply, reply_markup=kb)
    
    else:
        bot.send_message(chat_id, "Используйте кнопки меню.", reply_markup=main_menu())

def show_orders(chat_id, orders, period):
    if not orders:
        bot.send_message(chat_id, f"📭 Нет заказов на {period}.", reply_markup=main_menu())
        return
    reply = f"📋 {period}:\n\n"
    for o in orders[:10]:
        reply += f"#{o[0]} | {o[2]} | {o[3]}\n📦 {o[4]}\n💰 {o[6] if o[6] else '—'}₽\n\n"
    bot.send_message(chat_id, reply, reply_markup=main_menu())

# ==================== ОБРАБОТКА INLINE-КНОПОК ====================
@bot.callback_query_handler(func=lambda call: True)
def handle_callback(call):
    chat_id = call.message.chat.id
    data = call.data
    
    if data.startswith("done_"):
        order_id = data.split("_")[1]
        if update_status(order_id, "Выдан"):
            bot.edit_message_text(f"✅ Заказ #{order_id} выдан.", chat_id, call.message.message_id)
        else:
            bot.edit_message_text("❌ Ошибка.", chat_id, call.message.message_id)
    
    elif data.startswith("price_"):
        order_id = data.split("_")[1]
        user_state[chat_id] = "WAIT_PRICE"
        user_data[chat_id]["price_for"] = order_id
        bot.edit_message_text(f"💰 Введите сумму для заказа #{order_id}:", chat_id, call.message.message_id)
    
    elif data.startswith("date_"):
        date_val = data.split("_")[1]
        name = user_data[chat_id].get("new_name", "")
        phone = user_data[chat_id].get("new_phone", "")
        items = user_data[chat_id].get("new_items", "")
        order_id = add_order(name, phone, items, date_val)
        bot.edit_message_text(f"✅ Заказ #{order_id} создан!", chat_id, call.message.message_id)
        user_state[chat_id] = None
        user_data[chat_id] = {}

# ==================== ЗАПУСК ====================
def main():
    # Запускаем веб-сервер в отдельном потоке
    threading.Thread(target=run_web_server, daemon=True).start()
    
    logger.info("Бот запущен...")
    
    # Запускаем бота (синхронно, без event loop)
    bot.polling(none_stop=True)

if __name__ == "__main__":
    main()
