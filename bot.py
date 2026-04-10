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

# ==================== ГЛАВНОЕ МЕНЮ ====================
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
        if r and r[0] and r[0].isdigit():
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

# ==================== УЛУЧШЕННЫЙ ПАРСЕР (ДЛЯ КРИВЫХ СООБЩЕНИЙ) ====================
def parse_order_text(text):
    """Парсер, устойчивый к ошибкам, опечаткам и кривому форматированию"""
    result = {"name": "", "phone": "", "items": [], "date": "сегодня"}
    
    # Приводим к нижнему регистру для поиска, но сохраняем оригинал
    text_lower = text.lower()
    text_clean = text.replace('\n', ' ').replace('\r', ' ')
    
    # ===== ТЕЛЕФОН =====
    # Ищем любые 10-12 цифр подряд (с пробелами, скобками, дефисами или без)
    phone_digits = re.sub(r'[^\d]', '', text_clean)
    phone_match = re.search(r'(\d{10,12})', phone_digits)
    if phone_match:
        result["phone"] = phone_match.group(1)
        # Убираем телефон из текста, чтобы не мешал парсить имя и позиции
        text_clean = re.sub(r'[\+\d\s\(\)\-]{10,}', '', text_clean)
    
    # ===== ИМЯ =====
    # Список слов, которые НЕ являются именем (продукты, служебные слова)
    not_names = {'ребра', 'рёбра', 'ребро', 'грудинка', 'грудника', 'грудинки',
                 'форель', 'форели', 'сало', 'сала', 'окорок', 'окорока',
                 'колбаса', 'колбасы', 'кг', 'гр', 'грамм', 'тел', 'телефон',
                 'заказ', 'хочу', 'возьми', 'положи', 'на', 'в', 'с', 'и', 'а'}
    
    # Ищем первое слово, которое похоже на имя (с большой буквы или просто первое слово)
    words = text_clean.split()
    for word in words:
        # Очищаем от запятых и точек
        word_clean = re.sub(r'[^\w]', '', word)
        if len(word_clean) >= 2 and word_clean.lower() not in not_names:
            # Если слово с большой буквы — скорее всего имя
            if word[0].isupper() or word_clean[0].isupper():
                result["name"] = word_clean.capitalize()
                break
    
    # Если имя не нашли — берём первое подходящее слово
    if not result["name"]:
        for word in words:
            word_clean = re.sub(r'[^\w]', '', word)
            if len(word_clean) >= 2 and word_clean.lower() not in not_names:
                result["name"] = word_clean.capitalize()
                break
    
    # Если совсем ничего — ставим "Клиент"
    if not result["name"]:
        result["name"] = "Клиент"
    
    # ===== ПОЗИЦИИ =====
    # Ищем конструкции типа: "слово число" или "слово число,слово" или "слово числокг"
    # Допускаем запятую как разделитель дробей (0,5)
    
    # Заменяем запятую на точку ТОЛЬКО в числах, чтобы 0,5 стало 0.5
    def fix_comma_in_numbers(s):
        return re.sub(r'(\d+),(\d+)', r'\1.\2', s)
    
    text_fixed = fix_comma_in_numbers(text_lower)
    
    # Паттерн: название продукта (2+ букв) + число с возможной точкой + опционально "кг" или "гр"
    item_pattern = re.compile(
        r'([а-яё]{2,})\s*[:\-\s]?\s*(\d+[\.\,]?\d*)\s*(?:кг|гр|г|kg|gr)?',
        re.IGNORECASE
    )
    
    # Также ищем обратный порядок: "500г форели"
    item_pattern_reverse = re.compile(
        r'(\d+[\.\,]?\d*)\s*(?:кг|гр|г|kg|gr)?\s+([а-яё]{2,})',
        re.IGNORECASE
    )
    
    found_items = []
    
    # Прямой порядок
    for match in item_pattern.finditer(text_fixed):
        name = match.group(1).strip()
        weight = match.group(2).strip().replace(',', '.')
        # Пропускаем служебные слова
        if name in ['тел', 'телефон', 'заказ', 'сегодня', 'завтра', 'пятница']:
            continue
        # Пропускаем если это часть телефона
        if name.isdigit():
            continue
        found_items.append(f"{name.capitalize()} {weight}кг")
    
    # Обратный порядок
    for match in item_pattern_reverse.finditer(text_fixed):
        weight = match.group(1).strip().replace(',', '.')
        name = match.group(2).strip()
        if name in ['тел', 'телефон', 'заказ', 'сегодня', 'завтра']:
            continue
        found_items.append(f"{name.capitalize()} {weight}кг")
    
    # Убираем дубликаты
    result["items"] = list(set(found_items))
    
    # Если ничего не нашли — сохраняем весь текст после имени как позицию
    if not result["items"] and len(words) > 1:
        # Всё кроме первого слова (имени) и телефона
        rest = ' '.join(words[1:])
        rest = re.sub(r'тел[:\s]*[\d\s\+\(\)\-]+', '', rest, flags=re.IGNORECASE)
        if rest.strip():
            result["items"] = [rest.strip()]
    
    # ===== ДАТА =====
    days = {
        'пн': 'понедельник', 'понедельник': 'понедельник',
        'вт': 'вторник', 'вторник': 'вторник',
        'ср': 'среда', 'среда': 'среда', 'среду': 'среда',
        'чт': 'четверг', 'четверг': 'четверг',
        'пт': 'пятница', 'пятница': 'пятница', 'пятницу': 'пятница',
        'сб': 'суббота', 'суббота': 'суббота', 'субботу': 'суббота',
        'вс': 'воскресенье', 'воскресенье': 'воскресенье',
        'сегодня': 'сегодня', 'завтра': 'завтра'
    }
    
    for key, val in days.items():
        if key in text_lower:
            result["date"] = val
            break
    
    # Ищем дату в формате дд.мм
    date_match = re.search(r'(\d{1,2}[\./-]\d{1,2})', text_clean)
    if date_match:
        result["date"] = date_match.group(1)
    
    return result

# ==================== ОБРАБОТЧИКИ ====================
@bot.message_handler(commands=['start'])
def start(message):
    chat_id = message.chat.id
    user_state[chat_id] = None
    user_data[chat_id] = {}
    bot.send_message(
        chat_id,
        "🔥 CRM КОПТИЛЬНЯ\n\n"
        "Просто отправьте мне сообщение от клиента, и я создам заказ.\n\n"
        "Пример:\n"
        "андрей ребра 0,5кг форель 200гр 80447706110\n\n"
        "Или используйте кнопки меню.",
        reply_markup=main_menu()
    )

@bot.message_handler(func=lambda m: True)
def handle_message(message):
    chat_id = message.chat.id
    text = message.text
    
    if chat_id not in user_state:
        user_state[chat_id] = None
        user_data[chat_id] = {}
    
    state = user_state.get(chat_id)
    
    # ===== КНОПКИ МЕНЮ =====
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
    
    # ===== ПОШАГОВЫЙ ВВОД =====
    elif state == "WAIT_NAME":
        user_data[chat_id]["new_name"] = text
        user_state[chat_id] = "WAIT_PHONE"
        bot.send_message(chat_id, "Введите телефон:")
    
    elif state == "WAIT_PHONE":
        user_data[chat_id]["new_phone"] = text
        user_state[chat_id] = "WAIT_ITEMS"
        bot.send_message(chat_id, "Введите позиции (например: ребра 0,5кг, форель 200гр):")
    
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
            for o in orders[:10]:
                reply += f"#{o[0]} | {o[2]} | {o[3]}\n📦 {o[4]}\n💰 {o[6] if len(o)>6 and o[6] else '—'}₽\n\n"
            bot.send_message(chat_id, reply, reply_markup=main_menu())
        else:
            bot.send_message(chat_id, "❌ Ничего не найдено.", reply_markup=main_menu())
        user_state[chat_id] = None
    
    elif state == "WAIT_PRICE":
        order_id = user_data[chat_id].get("price_for")
        price_match = re.search(r'\d+', text)
        if price_match and order_id:
            price = price_match.group()
            if update_price(order_id, price):
                bot.send_message(chat_id, f"✅ Сумма {price}₽ сохранена.", reply_markup=main_menu())
            else:
                bot.send_message(chat_id, "❌ Ошибка сохранения.", reply_markup=main_menu())
        user_state[chat_id] = None
        user_data[chat_id]["price_for"] = None
    
    # ===== АВТОРАСПОЗНАВАНИЕ ЗАКАЗА =====
    else:
        parsed = parse_order_text(text)
        
        if parsed["phone"] or parsed["items"]:
            # Формируем текст позиций
            if parsed["items"]:
                items_text = ", ".join(parsed["items"])
            else:
                items_text = "Не указано"
            
            order_id = add_order(
                parsed["name"],
                parsed["phone"],
                items_text,
                parsed["date"]
            )
            
            reply = f"✅ Заказ #{order_id} создан!\n\n"
            reply += f"👤 {parsed['name']}\n"
            reply += f"📞 {parsed['phone']}\n"
            reply += f"📅 {parsed['date']}\n"
            reply += f"📦 {items_text}"
            
            kb = types.InlineKeyboardMarkup()
            kb.row(
                types.InlineKeyboardButton("✅ Выдать", callback_data=f"done_{order_id}"),
                types.InlineKeyboardButton("💰 Сумма", callback_data=f"price_{order_id}")
            )
            bot.send_message(chat_id, reply, reply_markup=kb)
        else:
            bot.send_message(
                chat_id,
                "❌ Не удалось распознать заказ.\n\n"
                "Убедитесь, что в сообщении есть телефон или позиции с весом.\n\n"
                "Пример: андрей ребра 0,5кг форель 200гр 80447706110",
                reply_markup=main_menu()
            )

def show_orders(chat_id, orders, period):
    if not orders:
        bot.send_message(chat_id, f"📭 Нет заказов на {period}.", reply_markup=main_menu())
        return
    reply = f"📋 Заказы на {period}:\n\n"
    for o in orders[:15]:
        reply += f"#{o[0]} | {o[2]} | {o[3]}\n📦 {o[4]}\n💰 {o[6] if len(o)>6 and o[6] else '—'}₽\n\n"
    bot.send_message(chat_id, reply, reply_markup=main_menu())

# ==================== INLINE-КНОПКИ ====================
@bot.callback_query_handler(func=lambda call: True)
def handle_callback(call):
    chat_id = call.message.chat.id
    data = call.data
    
    if data.startswith("done_"):
        order_id = data.split("_")[1]
        if update_status(order_id, "Выдан"):
            bot.answer_callback_query(call.id, "✅ Отмечено как выданное")
            bot.edit_message_text(
                f"✅ Заказ #{order_id} выдан.",
                chat_id,
                call.message.message_id
            )
        else:
            bot.answer_callback_query(call.id, "❌ Ошибка")
    
    elif data.startswith("price_"):
        order_id = data.split("_")[1]
        user_state[chat_id] = "WAIT_PRICE"
        user_data[chat_id] = {"price_for": order_id}
        bot.edit_message_text(
            f"💰 Введите сумму для заказа #{order_id}:",
            chat_id,
            call.message.message_id
        )
    
    elif data.startswith("date_"):
        date_val = data.split("_")[1]
        name = user_data[chat_id].get("new_name", "")
        phone = user_data[chat_id].get("new_phone", "")
        items = user_data[chat_id].get("new_items", "")
        order_id = add_order(name, phone, items, date_val)
        bot.edit_message_text(
            f"✅ Заказ #{order_id} создан!",
            chat_id,
            call.message.message_id
        )
        user_state[chat_id] = None
        user_data[chat_id] = {}
        bot.send_message(chat_id, "Готово!", reply_markup=main_menu())

# ==================== ЗАПУСК ====================
def main():
    threading.Thread(target=run_web_server, daemon=True).start()
    logger.info("Бот запущен...")
    bot.polling(none_stop=True)

if __name__ == "__main__":
    main()
