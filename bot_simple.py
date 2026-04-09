# -*- coding: utf-8 -*-
import os
import re
import logging
from datetime import datetime

from dotenv import load_dotenv
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import Application, CommandHandler, MessageHandler, filters, CallbackContext, CallbackQueryHandler
import gspread
from oauth2client.service_account import ServiceAccountCredentials

# Загрузка переменных из .env
load_dotenv()
BOT_TOKEN = os.getenv("BOT_TOKEN")
SHEET_ID = os.getenv("SHEET_ID")

# Настройка логирования
logging.basicConfig(format="%(asctime)s - %(name)s - %(levelname)s - %(message)s", level=logging.INFO)
logger = logging.getLogger(__name__)

# Подключение к Google Sheets (пока через общий доступ по ссылке)
gc = gspread.service_account()  # Убедитесь, что у вас есть credentials.json
sheet = gc.open_by_key(SHEET_ID).sheet1

# Временное хранилище для состояний
user_state = {}

# ==================== ФУНКЦИИ ДЛЯ РАБОТЫ С ТАБЛИЦЕЙ ====================
def get_next_id():
    """Возвращает следующий ID для заказа"""
    try:
        all_rows = sheet.get_all_values()
        if len(all_rows) <= 1:
            return 1
        return len(all_rows)  # номер строки будет ID
    except:
        return 1

def add_order_to_sheet(client_name, phone, items_text, delivery_date, total_price=""):
    """Добавляет заказ в Google Таблицу"""
    try:
        order_id = get_next_id()
        created_at = datetime.now().strftime("%d.%m.%Y %H:%M")
        
        row = [
            str(order_id),
            created_at,
            client_name,
            phone,
            items_text,
            delivery_date,
            total_price,
            "Активен"
        ]
        sheet.append_row(row)
        return order_id
    except Exception as e:
        logger.error(f"Ошибка записи в таблицу: {e}")
        return None

def update_order_status(order_id, new_status):
    """Обновляет статус заказа в таблице"""
    try:
        cell = sheet.find(str(order_id))
        if cell:
            status_col = 8  # колонка H
            sheet.update_cell(cell.row, status_col, new_status)
            return True
    except Exception as e:
        logger.error(f"Ошибка обновления статуса: {e}")
    return False

# ==================== ПАРСИНГ ТЕКСТА ====================
def parse_order_text(text):
    """Разбирает текст заказа на имя, телефон, позиции и дату"""
    result = {"client_name": None, "phone": None, "items": [], "delivery_date": None}
    
    # Телефон
    phone_match = re.search(r'(\+?7\d{10}|\+?\d{10,12})', text.replace(" ", "").replace("-", ""))
    if phone_match:
        result["phone"] = phone_match.group(1)
    
    # Имя
    name_match = re.search(r'([А-Яа-я]{2,})\s*(?:тел|7|8|\+|,)', text, re.IGNORECASE)
    if name_match:
        result["client_name"] = name_match.group(1).capitalize()
    else:
        first_word = re.search(r'^([А-Яа-я]{2,})', text, re.IGNORECASE)
        if first_word:
            result["client_name"] = first_word.group(1).capitalize()
    
    # Позиции
    item_pattern = r'([А-Яа-я]{3,})\s+(\d+\.?\d*)\s*(?:кг|kg|гр|gr)?'
    items = re.findall(item_pattern, text, re.IGNORECASE)
    for item_name, weight in items:
        result["items"].append(f"{item_name.capitalize()} {weight}кг")
    
    # Дата
    date_match = re.search(r'(понедельник|вторник|сред[ау]|четверг|пятниц[ау]|суббот[ау]|воскресенье|\d{1,2}[\.-]\d{1,2})', text, re.IGNORECASE)
    if date_match:
        result["delivery_date"] = date_match.group(1).lower()
    else:
        result["delivery_date"] = "сегодня"
    
    return result

# ==================== ОБРАБОТЧИКИ КОМАНД ====================
async def start(update: Update, context: CallbackContext):
    await update.message.reply_text(
        "🔥 CRM КОПТИЛЬНЯ (ТЕСТ)\n\n"
        "Просто отправьте мне сообщение от клиента, и я создам заказ.\n"
        "Пример:\nАлексей, грудинка 1.5 кг, рёбра 1 кг, тел 89161234567, на пятницу"
    )

async def handle_message(update: Update, context: CallbackContext):
    text = update.message.text
    parsed = parse_order_text(text)
    
    if not parsed["client_name"] and not parsed["phone"]:
        await update.message.reply_text("❌ Не удалось распознать имя или телефон.")
        return
    
    # Формируем текст позиций
    items_text = ", ".join(parsed["items"]) if parsed["items"] else "Не указано"
    
    # Сохраняем в таблицу
    order_id = add_order_to_sheet(
        parsed["client_name"] or "Не указано",
        parsed["phone"] or "Не указан",
        items_text,
        parsed["delivery_date"]
    )
    
    if order_id:
        reply = f"✅ Заказ #{order_id} создан!\n\n"
        reply += f"👤 {parsed['client_name']}\n"
        reply += f"📞 {parsed['phone']}\n"
        reply += f"📅 {parsed['delivery_date']}\n"
        reply += f"📦 {items_text}\n"
        
        keyboard = [[InlineKeyboardButton("✅ Выдать", callback_data=f"done_{order_id}")]]
        reply_markup = InlineKeyboardMarkup(keyboard)
        
        await update.message.reply_text(reply, reply_markup=reply_markup)
    else:
        await update.message.reply_text("❌ Ошибка сохранения заказа.")

async def button_callback(update: Update, context: CallbackContext):
    query = update.callback_query
    await query.answer()
    
    data = query.data
    if data.startswith("done_"):
        order_id = int(data.split("_")[1])
        if update_order_status(order_id, "Выдан"):
            await query.edit_message_text(f"✅ Заказ #{order_id} отмечен как ВЫДАН.")
        else:
            await query.edit_message_text("❌ Ошибка обновления статуса.")

# ==================== ЗАПУСК ====================
def main():
    app = Application.builder().token(BOT_TOKEN).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
    app.add_handler(CallbackQueryHandler(button_callback))
    
    print("Бот запущен...")
    app.run_polling()

if __name__ == "__main__":
    main()
