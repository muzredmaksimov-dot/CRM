# -*- coding: utf-8 -*-
import os
import re
import json
import logging
from datetime import datetime

from dotenv import load_dotenv
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup, ReplyKeyboardMarkup, KeyboardButton
from telegram.ext import Application, CommandHandler, MessageHandler, filters, CallbackContext, CallbackQueryHandler, ConversationHandler
import gspread

# ==================== НАСТРОЙКИ ====================
load_dotenv()
BOT_TOKEN = os.getenv("BOT_TOKEN")
SHEET_ID = os.getenv("SHEET_ID")
GOOGLE_CREDS_JSON = os.getenv("GOOGLE_CREDENTIALS")

logging.basicConfig(format="%(asctime)s - %(levelname)s - %(message)s", level=logging.INFO)

# Подключение к Google Sheets
def get_sheet():
    creds_dict = json.loads(GOOGLE_CREDS_JSON)
    gc = gspread.service_account_from_dict(creds_dict)
    return gc.open_by_key(SHEET_ID).sheet1

# ==================== ГЛАВНОЕ МЕНЮ ====================
def main_menu():
    keyboard = [
        [KeyboardButton("➕ Новый заказ"), KeyboardButton("🔍 Найти")],
        [KeyboardButton("📅 Сегодня"), KeyboardButton("📅 Завтра")],
        [KeyboardButton("📋 Все активные")]
    ]
    return ReplyKeyboardMarkup(keyboard, resize_keyboard=True)

# ==================== РАБОТА С ТАБЛИЦЕЙ ====================
def get_next_id():
    sheet = get_sheet()
    rows = sheet.get_all_values()
    if len(rows) <= 1:
        return 1
    ids = [int(r[0]) for r in rows[1:] if r[0].isdigit()]
    return max(ids) + 1 if ids else 1

def add_order(client, phone, items, date, price=""):
    sheet = get_sheet()
    order_id = get_next_id()
    created = datetime.now().strftime("%d.%m.%Y %H:%M")
    sheet.append_row([str(order_id), created, client, phone, items, date, price, "Активен"])
    return order_id

def get_active_orders():
    sheet = get_sheet()
    rows = sheet.get_all_values()
    if len(rows) <= 1:
        return []
    return [r for r in rows[1:] if len(r) >= 8 and r[7] == "Активен"]

def update_status(order_id, status):
    sheet = get_sheet()
    cell = sheet.find(str(order_id))
    if cell:
        sheet.update_cell(cell.row, 8, status)
        return True
    return False

def update_price(order_id, price):
    sheet = get_sheet()
    cell = sheet.find(str(order_id))
    if cell:
        sheet.update_cell(cell.row, 7, str(price))
        return True
    return False

def find_orders(query):
    orders = get_active_orders()
    q = query.lower()
    result = []
    for o in orders:
        if q in o[2].lower() or q in o[3].lower() or q == o[0]:
            result.append(o)
    return result

def get_orders_by_date(date_str):
    orders = get_active_orders()
    result = []
    for o in orders:
        if date_str.lower() in o[5].lower():
            result.append(o)
    return result

# ==================== ПАРСИНГ ТЕКСТА ====================
def parse_text(text):
    result = {"name": None, "phone": None, "items": [], "date": None}
    
    # Телефон
    phone = re.search(r'(\+?7\d{10}|\+?\d{10,12})', text.replace(" ", "").replace("-", ""))
    if phone:
        result["phone"] = phone.group(1)
    
    # Имя
    name = re.search(r'([А-Яа-я]{2,})\s*(?:тел|7|8|\+|,)', text, re.IGNORECASE)
    if name:
        result["name"] = name.group(1).capitalize()
    else:
        first = re.search(r'^([А-Яа-я]{2,})', text, re.IGNORECASE)
        if first:
            result["name"] = first.group(1).capitalize()
    
    # Позиции
    items = re.findall(r'([А-Яа-я]{3,})\s+(\d+\.?\d*)\s*(?:кг|kg)?', text, re.IGNORECASE)
    for item, w in items:
        result["items"].append(f"{item.capitalize()} {w}кг")
    
    # Дата
    date = re.search(r'(понедельник|вторник|сред[ау]|четверг|пятниц[ау]|суббот[ау]|воскресенье|\d{1,2}[\.-]\d{1,2})', text, re.IGNORECASE)
    result["date"] = date.group(1).lower() if date else "сегодня"
    
    return result

# ==================== ОБРАБОТЧИКИ ====================
async def start(update: Update, context: CallbackContext):
    await update.message.reply_text(
        "🔥 CRM КОПТИЛЬНЯ\n\nОтправьте текст заказа или используйте кнопки.",
        reply_markup=main_menu()
    )

async def handle_message(update: Update, context: CallbackContext):
    text = update.message.text
    
    # Главное меню
    if text == "➕ Новый заказ":
        await update.message.reply_text("Введите имя клиента:")
        context.user_data["state"] = "WAIT_NAME"
        return
    
    elif text == "🔍 Найти":
        await update.message.reply_text("Введите имя или телефон:")
        context.user_data["state"] = "WAIT_SEARCH"
        return
    
    elif text == "📅 Сегодня":
        orders = get_orders_by_date("сегодня")
        await show_orders(update, orders, "сегодня")
    
    elif text == "📅 Завтра":
        orders = get_orders_by_date("завтра")
        await show_orders(update, orders, "завтра")
    
    elif text == "📋 Все активные":
        orders = get_active_orders()
        await show_orders(update, orders, "все")
    
    # Состояния
    elif context.user_data.get("state") == "WAIT_NAME":
        context.user_data["new_name"] = text
        await update.message.reply_text("Введите телефон:")
        context.user_data["state"] = "WAIT_PHONE"
    
    elif context.user_data.get("state") == "WAIT_PHONE":
        context.user_data["new_phone"] = text
        await update.message.reply_text("Введите позиции (например: Грудинка 1.5 кг, Рёбра 1 кг):")
        context.user_data["state"] = "WAIT_ITEMS"
    
    elif context.user_data.get("state") == "WAIT_ITEMS":
        context.user_data["new_items"] = text
        await update.message.reply_text(
            "Когда отдать?",
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("Сегодня", callback_data="date_сегодня"),
                 InlineKeyboardButton("Завтра", callback_data="date_завтра")]
            ])
        )
        context.user_data["state"] = "WAIT_DATE"
    
    elif context.user_data.get("state") == "WAIT_SEARCH":
        orders = find_orders(text)
        if orders:
            reply = f"🔍 Найдено:\n\n"
            for o in orders[:5]:
                reply += f"#{o[0]} | {o[2]} | {o[3]}\n📦 {o[4]}\n💰 {o[6] if o[6] else '—'}₽\n\n"
            await update.message.reply_text(reply, reply_markup=main_menu())
        else:
            await update.message.reply_text("Ничего не найдено.", reply_markup=main_menu())
        context.user_data["state"] = None
    
    # Автораспознавание текста заказа
    if re.search(r'\d{10}|\d+\.?\d*\s*кг', text, re.IGNORECASE):
        p = parse_text(text)
        if p["name"] or p["phone"]:
            items_text = ", ".join(p["items"]) if p["items"] else "Не указано"
            order_id = add_order(p["name"] or "—", p["phone"] or "—", items_text, p["date"])
            
            reply = f"✅ Заказ #{order_id}\n👤 {p['name']}\n📞 {p['phone']}\n📅 {p['date']}\n📦 {items_text}"
            keyboard = InlineKeyboardMarkup([
                [InlineKeyboardButton("✅ Выдать", callback_data=f"done_{order_id}"),
                 InlineKeyboardButton("💰 Сумма", callback_data=f"price_{order_id}")]
            ])
            await update.message.reply_text(reply, reply_markup=keyboard)
    
    else:
        await update.message.reply_text("Используйте кнопки меню.", reply_markup=main_menu())

async def show_orders(update, orders, period):
    if not orders:
        await update.message.reply_text(f"📭 Нет заказов на {period}.", reply_markup=main_menu())
        return
    reply = f"📋 {period}:\n\n"
    for o in orders[:10]:
        reply += f"#{o[0]} | {o[2]} | {o[3]}\n📦 {o[4]}\n💰 {o[6] if o[6] else '—'}₽\n\n"
    await update.message.reply_text(reply, reply_markup=main_menu())

async def button_callback(update: Update, context: CallbackContext):
    query = update.callback_query
    await query.answer()
    data = query.data
    
    if data.startswith("done_"):
        order_id = data.split("_")[1]
        if update_status(order_id, "Выдан"):
            await query.edit_message_text(f"✅ Заказ #{order_id} выдан.")
        else:
            await query.edit_message_text("❌ Ошибка.")
    
    elif data.startswith("price_"):
        order_id = data.split("_")[1]
        context.user_data["price_for"] = order_id
        await query.edit_message_text(f"💰 Введите сумму для заказа #{order_id}:")
    
    elif data.startswith("date_"):
        date_val = data.split("_")[1]
        name = context.user_data.get("new_name", "")
        phone = context.user_data.get("new_phone", "")
        items = context.user_data.get("new_items", "")
        order_id = add_order(name, phone, items, date_val)
        await query.edit_message_text(f"✅ Заказ #{order_id} создан!")
        context.user_data["state"] = None

async def handle_price(update: Update, context: CallbackContext):
    if "price_for" in context.user_data:
        order_id = context.user_data["price_for"]
        price = re.search(r'\d+', update.message.text)
        if price:
            update_price(order_id, price.group())
            await update.message.reply_text(f"✅ Сумма {price.group()}₽ сохранена.", reply_markup=main_menu())
        del context.user_data["price_for"]

# ==================== ЗАПУСК ====================
def main():
    app = Application.builder().token(BOT_TOKEN).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CallbackQueryHandler(button_callback))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
    app.add_handler(MessageHandler(filters.Regex(r'^\d+$'), handle_price))
    
    print("Бот запущен...")
    app.run_polling()

if __name__ == "__main__":
    main()
