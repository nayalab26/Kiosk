import os
import re
import json
import httpx
import asyncio
import xml.etree.ElementTree as ET
from email.utils import parsedate_to_datetime
from datetime import datetime
from aiohttp import web
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup, WebAppInfo
from telegram.ext import Application, CommandHandler, CallbackQueryHandler, MessageHandler, filters, ContextTypes

BOT_TOKEN = os.environ.get("BOT_TOKEN")
WEBAPP_URL = os.environ.get("WEBAPP_URL", "https://nayalab26.github.io/Kiosk/")
ADMIN_ID = int(os.environ.get("ADMIN_ID", "0"))
SUPABASE_URL = os.environ.get("SUPABASE_URL")
SUPABASE_KEY = os.environ.get("SUPABASE_KEY")
PORT = int(os.environ.get("PORT", 8080))

HEADERS = {
    "apikey": SUPABASE_KEY,
    "Authorization": f"Bearer {SUPABASE_KEY}",
    "Content-Type": "application/json"
}

# ===== RSS PARSER =====
async def fetch_channel_posts(handle: str) -> list:
    url = f"https://tg.i-c-a.su/rss/{handle}"
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

                text = re.sub(r'<[^>]+>', '', description or title)
                text = text.strip()[:500]

                if not text:
                    continue

                published_at = None
                if pub_date:
                    try:
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
    keyboard = [[InlineKeyboardButton(text="\u041e\u0442\u043a\u0440\u044b\u0442\u044c \u041a\u0438\u043e\u0441\u043a", web_app=WebAppInfo(url=WEBAPP_URL))]]
    await update.message.reply_text(
        "\u041f\u0440\u0438\u0432\u0435\u0442! \u042f \u041a\u0438\u043e\u0441\u043a \u2014 \u0442\u0432\u043e\u044f \u043f\u0435\u0440\u0441\u043e\u043d\u0430\u043b\u044c\u043d\u0430\u044f \u043b\u0435\u043d\u0442\u0430 Telegram-\u043a\u0430\u043d\u0430\u043b\u043e\u0432.\n\n\u041d\u0430\u0436\u043c\u0438 \u043a\u043d\u043e\u043f\u043a\u0443 \u0447\u0442\u043e\u0431\u044b \u043e\u0442\u043a\u0440\u044b\u0442\u044c \u043b\u0435\u043d\u0442\u0443:",
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

            # Check for duplicate before saving
            check = await client.get(
                f"{SUPABASE_URL}/rest/v1/applications?handle=eq.{handle}&status=eq.pending&select=id",
                headers=HEADERS
            )
            if not check.json():
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
                InlineKeyboardButton("\u041e\u0434\u043e\u0431\u0440\u0438\u0442\u044c", callback_data=f"approve:{handle}"),
                InlineKeyboardButton("\u041e\u0442\u043a\u043b\u043e\u043d\u0438\u0442\u044c", callback_data=f"reject:{handle}")
            ]])
            await context.bot.send_message(
                chat_id=ADMIN_ID,
                text=f"\u041d\u043e\u0432\u0430\u044f \u0437\u0430\u044f\u0432\u043a\u0430!\n\n\u041a\u0430\u043d\u0430\u043b: @{handle}\n\u041d\u0430\u0437\u0432\u0430\u043d\u0438\u0435: {title}\n\u041a\u0430\u0442\u0435\u0433\u043e\u0440\u0438\u0438: {categories}\n\u041a\u043e\u043d\u0442\u0430\u043a\u0442: {contact}\n\u041f\u043e\u0434\u043f\u0438\u0441\u0447\u0438\u043a\u043e\u0432: {subscribers}",
                reply_markup=keyboard
            )
            await update.message.reply_text("\u0417\u0430\u044f\u0432\u043a\u0430 \u043e\u0442\u043f\u0440\u0430\u0432\u043b\u0435\u043d\u0430! \u0420\u0430\u0441\u0441\u043c\u043e\u0442\u0440\u0438\u043c \u0432 \u0442\u0435\u0447\u0435\u043d\u0438\u0435 24 \u0447\u0430\u0441\u043e\u0432.")
        else:
            await update.message.reply_text("\u041f\u0440\u0438\u0432\u0435\u0442! \u041e\u0442\u043a\u0440\u043e\u0439 \u041a\u0438\u043e\u0441\u043a \u0447\u0442\u043e\u0431\u044b \u043f\u043e\u0434\u0430\u0442\u044c \u0437\u0430\u044f\u0432\u043a\u0443: @Kiosk_lenta_Bot")

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

    text = f"\u041a\u0430\u043d\u0430\u043b @{handle} \u043e\u0434\u043e\u0431\u0440\u0435\u043d \u0438 \u0434\u043e\u0431\u0430\u0432\u043b\u0435\u043d \u0432 \u041a\u0438\u043e\u0441\u043a!"
    if query:
        await query.edit_message_text(query.message.text + "\n\n" + text)
    elif message:
        await message.reply_text(text)

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
                    text=f"\u0412\u0430\u0448 \u043a\u0430\u043d\u0430\u043b @{handle} \u043e\u0434\u043e\u0431\u0440\u0435\u043d \u0438 \u0434\u043e\u0431\u0430\u0432\u043b\u0435\u043d \u0432 \u041a\u0438\u043e\u0441\u043a! \u041e\u0442\u043a\u0440\u044b\u0442\u044c: @Kiosk_lenta_Bot"
                )
            except Exception:
                if message:
                    await message.reply_text(f"\u041d\u0435 \u0443\u0434\u0430\u043b\u043e\u0441\u044c \u0443\u0432\u0435\u0434\u043e\u043c\u0438\u0442\u044c \u0432\u043b\u0430\u0434\u0435\u043b\u044c\u0446\u0430. \u041a\u043e\u043d\u0442\u0430\u043a\u0442: {data[0].get('contact','\u2014')}")
        else:
            if message:
                await message.reply_text(f"\u0412\u043b\u0430\u0434\u0435\u043b\u0435\u0446 \u043d\u0435 \u043d\u0430\u0439\u0434\u0435\u043d. \u0421\u0432\u044f\u0436\u0438\u0442\u0435\u0441\u044c \u0432\u0440\u0443\u0447\u043d\u0443\u044e: {data[0].get('contact','\u2014')}")

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

    text = f"\u0417\u0430\u044f\u0432\u043a\u0430 \u043a\u0430\u043d\u0430\u043b\u0430 @{handle} \u043e\u0442\u043a\u043b\u043e\u043d\u0435\u043d\u0430."
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
                    text=f"\u041a \u0441\u043e\u0436\u0430\u043b\u0435\u043d\u0438\u044e, \u0437\u0430\u044f\u0432\u043a\u0430 \u043a\u0430\u043d\u0430\u043b\u0430 @{handle} \u0431\u044b\u043b\u0430 \u043e\u0442\u043a\u043b\u043e\u043d\u0435\u043d\u0430. \u0415\u0441\u043b\u0438 \u0435\u0441\u0442\u044c \u0432\u043e\u043f\u0440\u043e\u0441\u044b \u2014 \u043d\u0430\u043f\u0438\u0448\u0438\u0442\u0435 \u043d\u0430\u043c."
                )
            except Exception:
                pass

async def approve(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != ADMIN_ID:
        return
    if not context.args:
        await update.message.reply_text("\u0418\u0441\u043f\u043e\u043b\u044c\u0437\u043e\u0432\u0430\u043d\u0438\u0435: /approve username")
        return
    await process_approve(context, context.args[0].replace('@', ''), message=update.message)

async def reject(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != ADMIN_ID:
        return
    if not context.args:
        await update.message.reply_text("\u0418\u0441\u043f\u043e\u043b\u044c\u0437\u043e\u0432\u0430\u043d\u0438\u0435: /reject username")
        return
    await process_reject(context, context.args[0].replace('@', ''), message=update.message)

async def button_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    if update.effective_user.id != ADMIN_ID:
        await query.answer("\u041d\u0435\u0442 \u0434\u043e\u0441\u0442\u0443\u043f\u0430")
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
        await update.message.reply_text("\u0418\u0441\u043f\u043e\u043b\u044c\u0437\u043e\u0432\u0430\u043d\u0438\u0435: /remove username")
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
    await update.message.reply_text(f"\u041a\u0430\u043d\u0430\u043b @{handle} \u0438 \u0435\u0433\u043e \u043f\u043e\u0441\u0442\u044b \u0443\u0434\u0430\u043b\u0435\u043d\u044b \u0438\u0437 \u041a\u0438\u043e\u0441\u043a\u0430.")

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
        await update.message.reply_text("\u041d\u043e\u0432\u044b\u0445 \u0437\u0430\u044f\u0432\u043e\u043a \u043d\u0435\u0442.")
        return
    await update.message.reply_text(f"Pending \u0437\u0430\u044f\u0432\u043e\u043a: {len(data)}")
    for app in data[:10]:
        text = (
            f"@{app['handle']} \u2014 {app['title']}\n"
            f"{app['categories']}\n"
            f"\u041a\u043e\u043d\u0442\u0430\u043a\u0442: {app['contact']}\n"
            f"\u041f\u043e\u0434\u043f\u0438\u0441\u0447\u0438\u043a\u043e\u0432: {app.get('subscribers') or '\u2014'}"
        )
        keyboard = InlineKeyboardMarkup([[
            InlineKeyboardButton("\u041e\u0434\u043e\u0431\u0440\u0438\u0442\u044c", callback_data=f"approve:{app['handle']}"),
            InlineKeyboardButton("\u041e\u0442\u043a\u043b\u043e\u043d\u0438\u0442\u044c", callback_data=f"reject:{app['handle']}")
        ]])
        await update.message.reply_text(text, reply_markup=keyboard)

async def sync_now(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != ADMIN_ID:
        return
    await update.message.reply_text("\u0417\u0430\u043f\u0443\u0441\u043a\u0430\u044e \u0441\u0438\u043d\u0445\u0440\u043e\u043d\u0438\u0437\u0430\u0446\u0438\u044e \u043f\u043e\u0441\u0442\u043e\u0432...")
    await sync_posts()
    await update.message.reply_text("\u0413\u043e\u0442\u043e\u0432\u043e!")

async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "\u041a\u0438\u043e\u0441\u043a \u2014 \u043f\u0435\u0440\u0441\u043e\u043d\u0430\u043b\u044c\u043d\u0430\u044f \u043b\u0435\u043d\u0442\u0430 \u043a\u0430\u043d\u0430\u043b\u043e\u0432\n\n"
        "/start \u2014 \u043e\u0442\u043a\u0440\u044b\u0442\u044c \u043f\u0440\u0438\u043b\u043e\u0436\u0435\u043d\u0438\u0435\n"
        "/applications \u2014 \u0441\u043f\u0438\u0441\u043e\u043a \u0437\u0430\u044f\u0432\u043e\u043a\n"
        "/approve username \u2014 \u043e\u0434\u043e\u0431\u0440\u0438\u0442\u044c \u043a\u0430\u043d\u0430\u043b\n"
        "/reject username \u2014 \u043e\u0442\u043a\u043b\u043e\u043d\u0438\u0442\u044c \u043a\u0430\u043d\u0430\u043b\n"
        "/remove username \u2014 \u0443\u0434\u0430\u043b\u0438\u0442\u044c \u043a\u0430\u043d\u0430\u043b\n"
        "/sync \u2014 \u0441\u0438\u043d\u0445\u0440\u043e\u043d\u0438\u0437\u0438\u0440\u043e\u0432\u0430\u0442\u044c \u043f\u043e\u0441\u0442\u044b\n"
        "/help \u2014 \u043f\u043e\u043c\u043e\u0449\u044c"
    )

# ===== PROXY WEB SERVER =====
async def handle_getchat(request):
    handle = request.rel_url.query.get('handle', '').strip()
    if not handle or len(handle) < 3:
        return web.json_response({'ok': False, 'description': 'Invalid handle'}, status=400)
    async with httpx.AsyncClient(timeout=10) as client:
        res = await client.get(f'https://api.telegram.org/bot{BOT_TOKEN}/getChat?chat_id=@{handle}')
    return web.Response(
        text=res.text,
        content_type='application/json',
        headers={'Access-Control-Allow-Origin': '*'}
    )

async def handle_getmembercount(request):
    handle = request.rel_url.query.get('handle', '').strip()
    if not handle or len(handle) < 3:
        return web.json_response({'ok': False, 'description': 'Invalid handle'}, status=400)
    async with httpx.AsyncClient(timeout=10) as client:
        res = await client.get(f'https://api.telegram.org/bot{BOT_TOKEN}/getChatMemberCount?chat_id=@{handle}')
    return web.Response(
        text=res.text,
        content_type='application/json',
        headers={'Access-Control-Allow-Origin': '*'}
    )

async def run_web_server():
    app = web.Application()
    app.router.add_get('/api/getchat', handle_getchat)
    app.router.add_get('/api/getchatmembercount', handle_getmembercount)
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, '0.0.0.0', PORT)
    await site.start()
    print(f"Proxy server started on port {PORT}")

async def post_init(application):
    from telegram import BotCommandScopeAllPrivateChats, BotCommandScopeChat
    await application.bot.set_my_commands([
        ("start", "\u041e\u0442\u043a\u0440\u044b\u0442\u044c \u041a\u0438\u043e\u0441\u043a"),
        ("help", "\u041f\u043e\u043c\u043e\u0449\u044c"),
    ], scope=BotCommandScopeAllPrivateChats())
    await application.bot.set_my_commands([
        ("start", "\u041e\u0442\u043a\u0440\u044b\u0442\u044c \u041a\u0438\u043e\u0441\u043a"),
        ("applications", "\u0421\u043f\u0438\u0441\u043e\u043a \u0437\u0430\u044f\u0432\u043e\u043a"),
        ("approve", "\u041e\u0434\u043e\u0431\u0440\u0438\u0442\u044c \u043a\u0430\u043d\u0430\u043b"),
        ("reject", "\u041e\u0442\u043a\u043b\u043e\u043d\u0438\u0442\u044c \u043a\u0430\u043d\u0430\u043b"),
        ("remove", "\u0423\u0434\u0430\u043b\u0438\u0442\u044c \u043a\u0430\u043d\u0430\u043b"),
        ("sync", "\u0421\u0438\u043d\u0445\u0440\u043e\u043d\u0438\u0437\u0438\u0440\u043e\u0432\u0430\u0442\u044c \u043f\u043e\u0441\u0442\u044b"),
        ("help", "\u041f\u043e\u043c\u043e\u0449\u044c"),
    ], scope=BotCommandScopeChat(chat_id=ADMIN_ID))
    asyncio.create_task(run_web_server())
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
