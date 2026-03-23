import os
import json
import httpx
import asyncio
import xml.etree.ElementTree as ET
from datetime import datetime
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup, WebAppInfo
from telegram.ext import Application, CommandHandler, CallbackQueryHandler, MessageHandler, filters, ContextTypes

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

# ===== RSS PARSER =====
async def fetch_channel_posts(handle: str) -> list:
    url = f"https://rsshub.app/telegram/channel/{handle}"
    try:
        async with httpx.AsyncClient(timeout=15) as client:
            res = await client.get(url)
            if res.status_code != 200:
                print(f"RSS error for {handle}: {res.status_code}")
                return []

            root = ET.fromstring(res.text)
            channel = root.find('channel')
            if not channel:
                return []

            channel_title = channel.findtext('title', handle)
            posts = []

            for item in channel.findall('item')[:5]:
                title = item.findtext('title', '')
                description = item.findtext('description', '')
                link = item.findtext('link', '')
                pub_date = item.findtext('pubDate', '')

                # Clean HTML from description
                import re
                text = re.sub(r'<[^>]+>', '', description or title)
                text = text.strip()[:500]

                if not text:
                    continue

                # Parse date
                published_at = None
                if pub_date:
                    try:
                        from email.utils import parsedate_to_datetime
                        published_at = parsedate_to_datetime(pub_date).isoformat()
                    except Exception:
                        pass

                posts.append({
                    "channel_handle": handle,
                    "channel_title": channel_title,
                    "text": text,
                    "post_url": link,
                    "published_at": published_at
                })

            return posts

    except Exception as e:
        print(f"Error fetching {handle}: {e}")
        return []

async def sync_posts():
    print("Syncing posts...")
    async with httpx.AsyncClient() as client:
        # Get approved channels
        res = await client.get(
            f"{SUPABASE_URL}/rest/v1/applications?status=eq.approved&select=handle,title",
            headers=HEADERS
        )
        channels = res.json()

    if not channels:
        print("No approved channels")
        return

    for channel in channels:
        handle = channel['handle']
        posts = await fetch_channel_posts(handle)

        if not posts:
            continue

        async with httpx.AsyncClient() as client:
            for post in posts:
                # Check if post already exists
                check = await client.get(
                    f"{SUPABASE_URL}/rest/v1/posts?channel_handle=eq.{handle}&post_url=eq.{post['post_url']}&select=id",
                    headers=HEADERS
                )
                if check.json():
                    continue

                await client.post(
                    f"{SUPABASE_URL}/rest/v1/posts",
                    headers=HEADERS,
                    json=post
                )

        print(f"Synced {len(posts)} posts from @{handle}")

async def periodic_sync(interval: int = 600):
    while True:
        try:
            await sync_posts()
        except Exception as e:
            print(f"Sync error: {e}")
        await asyncio.sleep(interval)

# ===== BOT HANDLERS =====
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

    # Start syncing posts immediately
    asyncio.create_task(fetch_and_save_posts(handle))

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
                    text=f"Ваш канал @{handle} одобрен и добавлен в Киоск! Открыть: @Kiosk_lenta_Bot"
                )
            except Exception:
                if message:
                    await message.reply_text(f"Не удалось уведомить владельца. Контакт: {data[0].get('contact','—')}")
        else:
            if message:
                await message.reply_text(f"Владелец не найден. Свяжитесь вручную: {data[0].get('contact','—')}")

async def fetch_and_save_posts(handle: str):
    posts = await fetch_channel_posts(handle)
    async with httpx.AsyncClient() as client:
        for post in posts:
            check = await client.get(
                f"{SUPABASE_URL}/rest/v1/posts?channel_handle=eq.{handle}&post_url=eq.{post['post_url']}&select=id",
                headers=HEADERS
            )
            if not check.json():
                await client.post(
                    f"{SUPABASE_URL}/rest/v1/posts",
                    headers=HEADERS,
                    json=post
                )
    print(f"Initial sync done for @{handle}")

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
    await process_approve(context, context.args[0].replace('@', ''), message=update.message)

async def reject(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != ADMIN_ID:
        return
    if not context.args:
        await update.message.reply_text("Использование: /reject username")
        return
    await process_reject(context, context.args[0].replace('@', ''), message=update.message)

async def button_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    if update.effective_user.id != ADMIN_ID:
        await query.answer("Нет доступа")
        return
    await query.answer()
    data = query.data
    if data.startswith("approve:"):
        await process_approve(context, data.split(":")[1], query=query)
    elif data.startswith("reject:"):
        await process_reject(context, data.split(":")[1], query=query)

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
        await client.delete(
            f"{SUPABASE_URL}/rest/v1/posts?channel_handle=eq.{handle}",
            headers=HEADERS
        )
    await update.message.reply_text(f"Канал @{handle} и его посты удалены из Киоска.")

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

async def sync_now(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != ADMIN_ID:
        return
    await update.message.reply_text("Запускаю синхронизацию постов...")
    await sync_posts()
    await update.message.reply_text("Готово!")

async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "Киоск — персональная лента каналов\n\n"
        "/start — открыть приложение\n"
        "/applications — список заявок\n"
        "/approve username — одобрить канал\n"
        "/reject username — отклонить канал\n"
        "/remove username — удалить канал\n"
        "/sync — синхронизировать посты\n"
        "/help — помощь"
    )

async def post_init(application):
    from telegram import BotCommandScopeAllPrivateChats, BotCommandScopeChat
    await application.bot.set_my_commands([
        ("start", "Открыть Киоск"),
        ("help", "Помощь"),
    ], scope=BotCommandScopeAllPrivateChats())
    await application.bot.set_my_commands([
        ("start", "Открыть Киоск"),
        ("applications", "Список заявок"),
        ("approve", "Одобрить канал"),
        ("reject", "Отклонить канал"),
        ("remove", "Удалить канал"),
        ("sync", "Синхронизировать посты"),
        ("help", "Помощь"),
    ], scope=BotCommandScopeChat(chat_id=ADMIN_ID))
    # Start periodic sync
    asyncio.create_task(periodic_sync(600))
    print("Bot started! Periodic sync every 10 minutes.")

def main():
    app = Application.builder().token(BOT_TOKEN).post_init(post_init).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("help", help_command))
    app.add_handler(CommandHandler("approve", approve))
    app.add_handler(CommandHandler("reject", reject))
    app.add_handler(CommandHandler("applications", applications_list))
    app.add_handler(CommandHandler("remove", remove))
    app.add_handler(CommandHandler("sync", sync_now))
    app.add_handler(CallbackQueryHandler(button_callback))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, save_user_chat_id))
    app.add_handler(MessageHandler(filters.StatusUpdate.WEB_APP_DATA, save_user_chat_id))
    app.run_polling()

if __name__ == "__main__":
    main()
