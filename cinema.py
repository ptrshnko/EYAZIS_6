import pandas as pd
import sqlite3
import time
import logging
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import Application, CommandHandler, MessageHandler, filters, CallbackQueryHandler, ContextTypes
from together import Together
from fuzzywuzzy import fuzz
import os

# Настройка логирования
logging.basicConfig(format='%(asctime)s - %(name)s - %(levelname)s - %(message)s', level=logging.INFO)
logger = logging.getLogger(__name__)

# Загрузка данных из CSV
movies_df = pd.read_csv('kinopoisk-top250.csv')

# Инициализация Together AI
together_client = Together(api_key="814aaa3d65cd5cd6dedbab7f8685889419ad93285155ae2d7c1a94869343faa4")  # Замените на ваш API-ключ

# Инициализация базы данных SQLite
def init_db():
    conn = sqlite3.connect('dialog_history.db')
    c = conn.cursor()
    c.execute('''CREATE TABLE IF NOT EXISTS history
                 (user_id INTEGER, query TEXT, response TEXT, timestamp TEXT)''')
    conn.commit()
    conn.close()

init_db()

# Функция для извлечения релевантных данных (Retriever)
def retrieve_cinema_data(query, top_k=3):
    query = query.lower()
    results = []
    
    # Поиск по всем полям с использованием fuzzy matching
    for idx, row in movies_df.iterrows():
        score = max(
            fuzz.partial_ratio(query, row['movie'].lower()),
            fuzz.partial_ratio(query, row['overview'].lower()),
            fuzz.partial_ratio(query, row['director'].lower()),
            fuzz.partial_ratio(query, row['actors'].lower())
        )
        if score > 50:  # Порог для релевантности
            results.append((score, row))
    
    # Сортировка по релевантности и ограничение до top_k
    results = sorted(results, key=lambda x: x[0], reverse=True)[:top_k]
    return [row.to_dict() for score, row in results]

# Функция для генерации ответа (Generator)
def generate_response(query):
    # Извлечение релевантных данных
    relevant_data = retrieve_cinema_data(query)
    
    if not relevant_data:
        return "Извините, ничего не найдено. Попробуйте уточнить запрос, например, указать название фильма, имя режиссёра, актёра или жанр."
    
    # Формирование контекста для LLM
    context = "Найденная информация:\n"
    for item in relevant_data:
        context += f"- Фильм: {item['movie']} ({item['year']}, {item['country']}, рейтинг: {item['rating']})\n"
        context += f"  Описание: {item['overview']}\n"
        context += f"  Режиссёр: {item['director']}\n"
        context += f"  Актёры: {item['actors']}\n"
    
    # Формирование промпта для Together AI
    prompt = f"""
    Ты бот, специализирующийся на кинематографии. Пользователь спросил: "{query}".
    На основе следующей информации дай краткий и информативный ответ на русском языке:
    {context}
    Если запрос неясен, предложи уточнить.
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
        return "Произошла ошибка при формировании ответа. Попробуйте снова."

# Обработчик команды /start
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    keyboard = [
        [InlineKeyboardButton("Справка", callback_data='help')],
        [InlineKeyboardButton("История", callback_data='history')]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    await update.message.reply_text(
        "🎬 Привет! Я бот, который поможет с информацией о фильмах, режиссёрах, актёрах и жанрах. Задай вопрос, например: 'Какие фильмы снял Нолан?'",
        reply_markup=reply_markup
    )

# Обработчик команды /help
async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    help_text = """
    🎬 Я бот по кинематографии! Могу:
    - Рассказать о фильмах (например, "Расскажи о 'Начало'").
    - Дать информацию о режиссёрах или актёрах (например, "Фильмы с Томом Хэнксом").
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
    response = generate_response(query)

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