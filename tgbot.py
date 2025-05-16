import json
import sqlite3
import time
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import Application, CommandHandler, MessageHandler, filters, CallbackQueryHandler, ContextTypes
from together import Together
import logging
import os

# Настройка логирования
logging.basicConfig(format='%(asctime)s - %(name)s - %(levelname)s - %(message)s', level=logging.INFO)
logger = logging.getLogger(__name__)

# Загрузка данных о фильмах
with open('kinopoisk-top250.json', 'r', encoding='utf-8') as f:
    movies = json.load(f)

# Инициализация Together AI
together_client = Together(api_key="814aaa3d65cd5cd6dedbab7f8685889419ad93285155ae2d7c1a94869343faa4")  # Укажите ваш API-ключ

# Инициализация базы данных SQLite
def init_db():
    conn = sqlite3.connect('dialog_history.db')
    c = conn.cursor()
    c.execute('''CREATE TABLE IF NOT EXISTS history
                 (user_id INTEGER, query TEXT, response TEXT, timestamp TEXT)''')
    conn.commit()
    conn.close()

init_db()

# Функция для поиска фильмов
def search_movies(query):
    results = []
    query = query.lower()
    for movie in movies:
        if (query in movie['movie'].lower() or 
            query in movie['overview'].lower() or 
            query in movie['director'].lower() or 
            query in movie['actors'].lower()):
            results.append(movie)
    return results

# Функция для генерации ответа с помощью Together AI
def generate_response(query, context):
    # Поиск релевантных фильмов
    relevant_movies = search_movies(query)
    context_str = json.dumps(relevant_movies, ensure_ascii=False)  # Ограничиваем до 5 фильмов для контекста!!!!!!!!

    # Формирование промпта
    prompt = f"""
    Ты бот, специализирующийся на кинематографии. Пользователь задал вопрос: "{query}".
    Используй следующие данные о фильмах для ответа:
    {context_str}
    Ответь на русском языке, кратко и информативно. Если запрос неясен, задай уточняющий вопрос.
    """
    
    try:
        response = together_client.chat.completions.create(
            model="meta-llama/Llama-3-8b-chat-hf",
            messages=[{"role": "user", "content": prompt}],
            max_tokens=200
        )
        return response.choices[0].message.content.strip()
    except Exception as e:
        logger.error(f"Ошибка Together AI: {e}")
        return "Извините, произошла ошибка. Попробуйте снова."

# Обработчик команды /start
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    keyboard = [
        [InlineKeyboardButton("Справка", callback_data='help')],
        [InlineKeyboardButton("История", callback_data='history')]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    await update.message.reply_text(
        "🎬 Привет! Я бот, который поможет с информацией о фильмах. Задай вопрос, например: 'Порекомендуй фильм 90-х'!",
        reply_markup=reply_markup
    )

# Обработчик команды /help
async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    help_text = """
    🎬 Я бот по кинематографии! Могу:
    - Рекомендовать фильмы (например, "Порекомендуй комедию").
    - Рассказать о фильме, режиссере или актерах (например, "Расскажи о 'Побеге из Шоушенка'").
    - Показать историю диалогов (/history).
    - Очистить историю (/clear_history).
    Задай вопрос, и я помогу!
    """
    await update.message.reply_text(help_text)

# Обработчик текстовых сообщений
async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    query = update.message.text
    start_time = time.time()

    # Генерация ответа
    response = generate_response(query, context)

    # Сохранение в историю
    conn = sqlite3.connect('dialog_history.db')
    c = conn.cursor()
    c.execute("INSERT INTO history (user_id, query, response, timestamp) VALUES (?, ?, ?, ?)",
              (user_id, query, response, time.strftime("%Y-%m-%d %H:%M:%S")))
    conn.commit()
    conn.close()

    # Отправка ответа
    await update.message.reply_text(response)

    # Оценка времени ответа
    logger.info(f"Время ответа: {time.time() - start_time:.2f} секунд")

# Обработчик команды /history
async def history(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    conn = sqlite3.connect('dialog_history.db')
    c = conn.cursor()
    c.execute("SELECT query, response, timestamp FROM history WHERE user_id = ? ORDER BY timestamp DESC LIMIT 10", (user_id,))
    rows = c.fetchall()
    conn.close()

    if not rows:
        await update.message.reply_text("История пуста.")
        return

    history_text = "📜 Последние 10 запросов:\n\n"
    for query, response, timestamp in rows:
        history_text += f"🕒 {timestamp}\n🗣 Запрос: {query}\n📢 Ответ: {response}\n\n"
    await update.message.reply_text(history_text)

# Обработчик команды /clear_history
async def clear_history(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    conn = sqlite3.connect('dialog_history.db')
    c = conn.cursor()
    c.execute("DELETE FROM history WHERE user_id = ?", (user_id,))
    conn.commit()
    conn.close()
    await update.message.reply_text("История очищена!")

# Обработчик кнопок
async def button(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    if query.data == 'help':
        await help_command(query, context)
    elif query.data == 'history':
        await history(query, context)

# Основная функция
def main():
    # Замените 'YOUR_BOT_TOKEN' на токен вашего бота
    application = Application.builder().token('7747105680:AAGmi7ImvNXxC_qy5KAwPfSm2VYOBxp8sJw').build()

    application.add_handler(CommandHandler("start", start))
    application.add_handler(CommandHandler("help", help_command))
    application.add_handler(CommandHandler("history", history))
    application.add_handler(CommandHandler("clear_history", clear_history))
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
    application.add_handler(CallbackQueryHandler(button))

    application.run_polling()

if __name__ == '__main__':
    main()