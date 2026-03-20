import os
import httpx
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup, WebAppInfo
from telegram.ext import Application, CommandHandler, CallbackQueryHandler, MessageHandler, filters, ContextTypes
import json

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
    keyboard = [[InlineKeyboardButton(text="Открыть Киоск", web_app=WebAppInfo(url=WEBAPP_URL))]]
    await update.message.reply_text(
        "Привет! Я Киоск — твоя персональная лента Telegram-каналов.\n\nНажми кнопку чтобы открыть ленту:",
        reply_markup=InlineKeyboardMarkup(keyboard)
    )

async def save_user_chat_id(update: Update, context: ContextTypes.DEFAULT_TYPE):
    username = update.effective_user.username
    chat_id = update.effective_chat.id

    async with httpx.AsyncClient() as client:
        if username:
            await client.post(
                f"{SUPABASE_URL}/rest/v1/users",
                headers={**HEADERS, "Prefer": "resolution=merge-duplicates"},
                json={"username": username, "chat_id": chat_id}
            )

        if update.message.web_app_data:
            data = json.loads(update.message.web_app_data.data)
            handle = data.get("handle", "")
            title = data.get("title", "")
            description = data.get("description", "")
            categories = data.get("categories", "")
            contact = data.get("contact", "")
            subscribers = data.get("subscribers", 0)

            await client.post(
                f"{SUPABASE_URL}/rest/v1/applications",
                headers=HEADERS,
                json={
                    "handle": handle, "title": title, "description": description,
                    "categories": categories, "contact": contact,
                    "subscribers": subscribers, "chat_id": chat_id, "status": "pending"
                }
            )

            keyboard = InlineKeyboardMarkup([[
                InlineKeyboardButton("Одобрить", callback_data=f"approve:{handle}"),
                InlineKeyboardButton("Отклонить", callback_data=f"reject:{handle}")
            ]])
            await context.bot.send_message(
                chat_id=ADMIN_ID,
                text=f"Новая заявка!\n\nКанал: @{handle}\nНазвание: {title}\nКатегории: {categories}\nКонтакт: {contact}\nПодписчиков: {subscribers}",
                reply_markup=keyboard
            )
            await update.message.reply_text("Заявка отправлена! Рассмотрим в течение 24 часов.")
        else:
            await update.message.reply_text("Привет! Открой Киоск чтобы подать заявку: @Kiosk_lenta_Bot")

async def process_approve(context, handle, message=None, query=None):
    async with httpx.AsyncClient() as client:
        await client.patch(
            f"{SUPABASE_URL}/rest/v1/applications?handle=eq.{handle}&status=eq.pending",
            headers=HEADERS, json={"status": "approved"}
        )
        res2 = await client.get(
            f"{SUPABASE_URL}/rest/v1/applications?handle=eq.{handle}&select=contact,title,chat_id",
            headers=HEADERS
        )
        data = res2.json()

    text = f"Канал @{handle} одобрен и добавлен в Киоск!"
    if query:
        await query.edit_message_text(query.message.text + "\n\n" + text)
    elif message:
        await message.reply_text(text)

    if data:
        chat_id = data[0].get('chat_id')
        contact = data[0].get('contact', '').replace('@', '')

        # Try to get chat_id from users table if not in application
        if not chat_id and contact:
            async with httpx.AsyncClient() as client2:
                res3 = await client2.get(
                    f"{SUPABASE_URL}/rest/v1/users?username=eq.{contact}&select=chat_id",
                    headers=HEADERS
                )
                users = res3.json()
                if users:
                    chat_id = users[0].get('chat_id')

        if chat_id:
            try:
                await context.bot.send_message(
                    chat_id=chat_id,
                    text=f"Ваш канал @{handle} одобрен и добавлен в Киоск! Открыть: @Kiosk_lenta_Bot"
                )
            except Exception:
                if message:
                    await message.reply_text(f"Не удалось уведомить владельца. Контакт: {data[0].get('contact','—')}")
        else:
            if message:
                await message.reply_text(f"Владелец не найден. Свяжитесь вручную: {data[0].get('contact','—')}")

async def process_reject(context, handle, message=None, query=None):
    async with httpx.AsyncClient() as client:
        await client.patch(
            f"{SUPABASE_URL}/rest/v1/applications?handle=eq.{handle}&status=eq.pending",
            headers=HEADERS, json={"status": "rejected"}
        )
        res2 = await client.get(
            f"{SUPABASE_URL}/rest/v1/applications?handle=eq.{handle}&select=contact,chat_id",
            headers=HEADERS
        )
        data = res2.json()

    text = f"Заявка канала @{handle} отклонена."
    if query:
        await query.edit_message_text(query.message.text + "\n\n" + text)
    elif message:
        await message.reply_text(text)

    if data:
        chat_id = data[0].get('chat_id')
        contact = data[0].get('contact', '').replace('@', '')

        if not chat_id and contact:
            async with httpx.AsyncClient() as client2:
                res3 = await client2.get(
                    f"{SUPABASE_URL}/rest/v1/users?username=eq.{contact}&select=chat_id",
                    headers=HEADERS
                )
                users = res3.json()
                if users:
                    chat_id = users[0].get('chat_id')

        if chat_id:
            try:
                await context.bot.send_message(
                    chat_id=chat_id,
                    text=f"К сожалению, заявка канала @{handle} была отклонена. Если есть вопросы — напишите нам."
                )
            except Exception:
                pass

async def approve(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != ADMIN_ID:
        return
    if not context.args:
        await update.message.reply_text("Использование: /approve username")
        return
    handle = context.args[0].replace('@', '')
    await process_approve(context, handle, message=update.message)

async def reject(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != ADMIN_ID:
        return
    if not context.args:
        await update.message.reply_text("Использование: /reject username")
        return
    handle = context.args[0].replace('@', '')
    await process_reject(context, handle, message=update.message)

async def button_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    if update.effective_user.id != ADMIN_ID:
        await query.answer("Нет доступа")
        return
    await query.answer()
    data = query.data
    if data.startswith("approve:"):
        handle = data.split(":")[1]
        await process_approve(context, handle, query=query)
    elif data.startswith("reject:"):
        handle = data.split(":")[1]
        await process_reject(context, handle, query=query)

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
    await update.message.reply_text(f"Канал @{handle} удален из Киоска.")

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
        await update.message.reply_text("Новых заявок нет.")
        return
    await update.message.reply_text(f"Pending заявок: {len(data)}")
    for app in data[:10]:
        text = (
            f"@{app['handle']} — {app['title']}\n"
            f"{app['categories']}\n"
            f"Контакт: {app['contact']}\n"
            f"Подписчиков: {app.get('subscribers') or '—'}"
        )
        keyboard = InlineKeyboardMarkup([[
            InlineKeyboardButton("Одобрить", callback_data=f"approve:{app['handle']}"),
            InlineKeyboardButton("Отклонить", callback_data=f"reject:{app['handle']}")
        ]])
        await update.message.reply_text(text, reply_markup=keyboard)

async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "Киоск — персональная лента каналов\n\n"
        "/start — открыть приложение\n"
        "/applications — список заявок\n"
        "/approve username — одобрить канал\n"
        "/reject username — отклонить канал\n"
        "/remove username — удалить канал\n"
        "/help — помощь"
    )

async def post_init(application):
    from telegram import BotCommandScopeAllPrivateChats, BotCommandScopeChat

    # Команды для всех пользователей
    await application.bot.set_my_commands([
        ("start", "Открыть Киоск"),
        ("help", "Помощь"),
    ], scope=BotCommandScopeAllPrivateChats())

    # Команды для админа
    await application.bot.set_my_commands([
        ("start", "Открыть Киоск"),
        ("applications", "Список заявок"),
        ("approve", "Одобрить канал"),
        ("reject", "Отклонить канал"),
        ("remove", "Удалить канал"),
        ("help", "Помощь"),
    ], scope=BotCommandScopeChat(chat_id=ADMIN_ID))

def main():
    app = Application.builder().token(BOT_TOKEN).post_init(post_init).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("help", help_command))
    app.add_handler(CommandHandler("approve", approve))
    app.add_handler(CommandHandler("reject", reject))
    app.add_handler(CommandHandler("applications", applications_list))
    app.add_handler(CommandHandler("remove", remove))
    app.add_handler(CallbackQueryHandler(button_callback))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, save_user_chat_id))
    app.add_handler(MessageHandler(filters.StatusUpdate.WEB_APP_DATA, save_user_chat_id))
    print("Bot started!")
    app.run_polling()

if __name__ == "__main__":
    main()
