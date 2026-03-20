import os
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup, WebAppInfo
from telegram.ext import Application, CommandHandler, ContextTypes

BOT_TOKEN = os.environ.get("BOT_TOKEN", "8624327731:AAG5aS4V-X9rd8gAv9u-lpyxWqDezQH8myM")
WEBAPP_URL = os.environ.get("WEBAPP_URL", "https://nayalab26.github.io/Kiosk/")

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    keyboard = [[
        InlineKeyboardButton(
            text="🗞 Открыть Киоск",
            web_app=WebAppInfo(url=WEBAPP_URL)
        )
    ]]
    reply_markup = InlineKeyboardMarkup(keyboard)
    await update.message.reply_text(
        "👋 Привет! Я *Киоск* — твоя персональная лента Telegram-каналов.\n\n"
        "📰 Выбирай темы, читай только интересное, подписывайся на лучшие каналы.\n\n"
        "Нажми кнопку ниже чтобы открыть ленту 👇",
        parse_mode="Markdown",
        reply_markup=reply_markup
    )

async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "🗞 *Киоск* — персональная лента каналов\n\n"
        "Команды:\n"
        "/start — открыть приложение\n"
        "/help — помощь\n\n"
        "По вопросам: @your_support",
        parse_mode="Markdown"
    )

def main():
    app = Application.builder().token(BOT_TOKEN).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("help", help_command))
    print("✅ Бот запущен!")
    app.run_polling()

if __name__ == "__main__":
    main()
