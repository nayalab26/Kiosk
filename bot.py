import os
import httpx
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup, WebAppInfo
from telegram.ext import Application, CommandHandler, ContextTypes

BOT_TOKEN = os.environ.get("BOT_TOKEN")
WEBAPP_URL = os.environ.get("WEBAPP_URL", "https://nayalab26.github.io/Kiosk/")
ADMIN_ID = int(os.environ.get("ADMIN_ID", "0"))
SUPABASE_URL = os.environ.get("SUPABASE_URL")
SUPABASE_KEY = os.environ.get("SUPABASE_KEY")

HEADERS = {
    "apikey": SUPABASE_KEY,
    "Authorization": f"Bearer {SUPABASE_KEY}",
    "Content-Type": "application/json"
}

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    keyboard = [[InlineKeyboardButton(text="🗞 Открыть Киоск", web_app=WebAppInfo(url=WEBAPP_URL))]]
    await update.message.reply_text(
        "👋 Привет! Я *Киоск* — твоя персональная лента Telegram-каналов.\n\n"
        "📰 Выбирай темы, читай только интересное, подписывайся на лучшие каналы.\n\n"
        "Нажми кнопку ниже чтобы открыть ленту 👇",
        parse_mode="Markdown",
        reply_markup=InlineKeyboardMarkup(keyboard)
    )

async def approve(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != ADMIN_ID:
        return
    if not context.args:
        await update.message.reply_text("Использование: /approve username")
        return
    handle = context.args[0].replace('@', '')
    async with httpx.AsyncClient() as client:
        await client.patch(
            f"{SUPABASE_URL}/rest/v1/applications?handle=eq.{handle}&status=eq.pending",
            headers=HEADERS, json={"status": "approved"}
        )
        res2 = await client.get(
            f"{SUPABASE_URL}/rest/v1/applications?handle=eq.{handle}&select=contact,title",
            headers=HEADERS
        )
        data = res2.json()
    await update.message.reply_text(f"✅ Канал @{handle} одобрен и добавлен в Киоск!")
    if data and data[0].get('contact'):
        contact = data[0]['contact'].replace('@', '')
        try:
            await context.bot.send_message(
                chat_id=f"@{contact}",
                text=f"🎉 Ваш канал @{handle} одобрен и добавлен в Киоск!\n\nОткрыть: @Kiosk_lenta_Bot"
            )
        except Exception:
            await update.message.reply_text(f"⚠️ Не удалось уведомить владельца. Контакт: {data[0]['contact']}")

async def reject(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != ADMIN_ID:
        return
    if not context.args:
        await update.message.reply_text("Использование: /reject username")
        return
    handle = context.args[0].replace('@', '')
    async with httpx.AsyncClient() as client:
        await client.patch(
            f"{SUPABASE_URL}/rest/v1/applications?handle=eq.{handle}&status=eq.pending",
            headers=HEADERS, json={"status": "rejected"}
        )
        res2 = await client.get(
            f"{SUPABASE_URL}/rest/v1/applications?handle=eq.{handle}&select=contact",
            headers=HEADERS
        )
        data = res2.json()
    await update.message.reply_text(f"❌ Заявка канала @{handle} отклонена.")
    if data and data[0].get('contact'):
        contact = data[0]['contact'].replace('@', '')
        try:
            await context.bot.send_message(
                chat_id=f"@{contact}",
                text=f"😔 К сожалению, заявка канала @{handle} была отклонена.\nЕсли есть вопросы — напишите нам."
            )
        except Exception:
            pass

async def applications_list(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != ADMIN_ID:
        return
    async with httpx.AsyncClient() as client:
        res = await client.get(
            f"{SUPABASE_URL}/rest/v1/applications?status=eq.pending&select=*&order=created_at.desc",
            headers=HEADERS
        )
        data = res.json()
    if not data:
        await update.message.reply_text("📭 Новых заявок нет.")
        return
    text = f"📬 Pending заявок: {len(data)}\n\n"
    for app in data[:10]:
        text += f"📢 @{app['handle']} — {app['title']}\n"
        text += f"📂 {app['categories']}\n"
        text += f"👤 {app['contact']}\n"
        text += f"✅ /approve {app['handle']}   ❌ /reject {app['handle']}\n\n"
    await update.message.reply_text(text)

async def remove(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != ADMIN_ID:
        return
    if not context.args:
        await update.message.reply_text("Использование: /remove username")
        return
    handle = context.args[0].replace('@', '')
    async with httpx.AsyncClient() as client:
        await client.delete(
            f"{SUPABASE_URL}/rest/v1/applications?handle=eq.{handle}",
            headers=HEADERS
        )
    await update.message.reply_text(f"🗑 Канал @{handle} удалён из Киоска.")

async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "🗞 *Киоск* — персональная лента каналов\n\n"
        "Команды:\n"
        "/start — открыть приложение\n"
        "/applications — список заявок\n"
        "/approve username — одобрить канал\n"
        "/reject username — отклонить канал\n"
        "/remove username — удалить канал\n"
        "/help — помощь",
        parse_mode="Markdown"
    )

async def post_init(application):
    await application.bot.set_my_commands([
        ("start", "🗞 Открыть Киоск"),
        ("applications", "📬 Список заявок"),
        ("approve", "✅ Одобрить канал"),
        ("reject", "❌ Отклонить канал"),
        ("remove", "🗑 Удалить канал"),
        ("help", "❓ Помощь"),
    ])

def main():
    app = Application.builder().token(BOT_TOKEN).post_init(post_init).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("help", help_command))
    app.add_handler(CommandHandler("approve", approve))
    app.add_handler(CommandHandler("reject", reject))
    app.add_handler(CommandHandler("applications", applications_list))
    app.add_handler(CommandHandler("remove", remove))
    print("✅ Бот запущен!")
    app.run_polling()

if __name__ == "__main__":
    main()
