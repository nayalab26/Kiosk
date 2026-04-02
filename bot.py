import os
import re
import json
import httpx
import asyncio
import xml.etree.ElementTree as ET
from email.utils import parsedate_to_datetime
from datetime import datetime, timezone, timedelta
from aiohttp import web
from telegram import Bot, Update, InlineKeyboardButton, InlineKeyboardMarkup, WebAppInfo
from telegram.ext import Application, CommandHandler, CallbackQueryHandler, MessageHandler, filters, ContextTypes

BOT_TOKEN = os.environ.get("BOT_TOKEN")
WEBAPP_URL = os.environ.get("WEBAPP_URL", "https://nayalab26.github.io/Kiosk/")
ADMIN_ID = int(os.environ.get("ADMIN_ID", "0"))
SUPABASE_URL = os.environ.get("SUPABASE_URL")
SUPABASE_KEY = os.environ.get("SUPABASE_KEY")
SUPABASE_SERVICE_KEY = os.environ.get("SUPABASE_SERVICE_KEY", SUPABASE_KEY)
ADMIN_SECRET = os.environ.get("ADMIN_SECRET", "")
PORT = int(os.environ.get("PORT", 8080))

bot_instance = None

HEADERS = {
    "apikey": SUPABASE_KEY,
    "Authorization": f"Bearer {SUPABASE_KEY}",
    "Content-Type": "application/json"
}

SERVICE_HEADERS = {
    "apikey": SUPABASE_SERVICE_KEY,
    "Authorization": f"Bearer {SUPABASE_SERVICE_KEY}",
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
            channel = root.find("channel")
            if not channel:
                return []
            channel_title = channel.findtext("title", handle)
            posts = []
            for item in channel.findall("item")[:5]:
                title = item.findtext("title", "")
                description = item.findtext("description", "")
                link = item.findtext("link", "")
                pub_date = item.findtext("pubDate", "")
                text = re.sub(r"<[^>]+>", "", description or title)
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
        handle = channel["handle"]
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

# ===== DIGEST =====
async def build_digest_text() -> str:
    async with httpx.AsyncClient() as client:
        res = await client.get(
            f"{SUPABASE_URL}/rest/v1/posts?order=published_at.desc&limit=10"
            f"&select=channel_handle,channel_title,text,post_url,published_at",
            headers=HEADERS
        )
        posts = res.json() if res.status_code == 200 else []
    if not posts:
        return ""
    lines = ["<b>Дайджест Киоска</b> — свежие посты:\n"]
    for post in posts[:8]:
        title = post.get("channel_title") or post.get("channel_handle", "")
        text = (post.get("text") or "")[:150]
        url = post.get("post_url") or f"https://t.me/{post.get('channel_handle', '')}"
        lines.append(f"<b>{title}</b>\n{text}...\n<a href='{url}'>Читать</a>\n")
    return "\n".join(lines)

async def send_digest_to_user(bot, chat_id: int):
    text = await build_digest_text()
    if not text:
        await bot.send_message(chat_id=chat_id, text="Нет новых постов для дайджеста.")
        return
    await bot.send_message(chat_id=chat_id, text=text, parse_mode="HTML",
                           disable_web_page_preview=True)

async def send_digest_to_all(bot):
    text = await build_digest_text()
    if not text:
        return
    async with httpx.AsyncClient() as client:
        res = await client.get(
            f"{SUPABASE_URL}/rest/v1/users?select=chat_id&chat_id=not.is.null",
            headers=HEADERS
        )
        users = res.json() if res.status_code == 200 else []
    sent = 0
    for u in users:
        uid = u.get("chat_id")
        if not uid:
            continue
        try:
            await bot.send_message(chat_id=uid, text=text, parse_mode="HTML",
                                   disable_web_page_preview=True)
            sent += 1
            await asyncio.sleep(0.05)
        except Exception as e:
            print(f"Digest error for {uid}: {e}")
    print(f"Daily digest sent to {sent} users")

async def daily_digest_scheduler(bot):
    moscow = timezone(timedelta(hours=3))
    last_sent = None
    while True:
        try:
            now = datetime.now(moscow)
            today = now.date()
            if now.hour == 9 and now.minute < 10 and last_sent != today:
                await send_digest_to_all(bot)
                last_sent = today
        except Exception as e:
            print(f"Digest scheduler error: {e}")
        await asyncio.sleep(300)

# ===== BOT HANDLERS =====
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    # Handle referral link: /start ref_USERID
    if context.args and context.args[0].startswith("ref_"):
        try:
            referrer_id = int(context.args[0][4:])
            if referrer_id != user_id:
                async with httpx.AsyncClient() as client:
                    await client.post(
                        f"{SUPABASE_URL}/rest/v1/referrals",
                        headers={**HEADERS, "Prefer": "resolution=ignore-duplicates"},
                        json={"referrer_id": referrer_id, "referee_id": user_id}
                    )
                try:
                    name = update.effective_user.first_name or "Пользователь"
                    await context.bot.send_message(
                        referrer_id,
                        f"По твоей реферальной ссылке зарегистрировался {name}!"
                    )
                except Exception:
                    pass
        except (ValueError, IndexError):
            pass
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
                InlineKeyboardButton("Одобрить", callback_data=f"approve:{handle}"),
                InlineKeyboardButton("Отклонить", callback_data=f"reject:{handle}")
            ]])
            print(f"[DEBUG] Sending admin notification to ADMIN_ID={ADMIN_ID}")
            try:
                await context.bot.send_message(
                    chat_id=ADMIN_ID,
                    text=f"Новая заявка!\n\nКанал: @{handle}\nНазвание: {title}\nКатегории: {categories}\nКонтакт: {contact}\nПодписчиков: {subscribers}",
                    reply_markup=keyboard
                )
                print(f"[DEBUG] Admin notification sent OK")
            except Exception as e:
                print(f"[ERROR] Failed to notify admin: {e}")
            await update.message.reply_text("Заявка отправлена! Рассмотрим в течение 24 часов.")
        else:
            await update.message.reply_text("Привет! Открой Киоск чтобы подать заявку: @Kiosk_lenta_Bot")

async def process_approve(context, handle, message=None, query=None):
    async with httpx.AsyncClient() as client:
        res_all = await client.get(
            f"{SUPABASE_URL}/rest/v1/applications?handle=eq.{handle}&order=id.desc&select=id,contact,title,chat_id",
            headers=HEADERS
        )
        data = res_all.json()
        if not data:
            text = f"Заявка @{handle} не найдена."
            if query:
                await query.edit_message_text(query.message.text + "\n\n" + text)
            elif message:
                await message.reply_text(text)
            return
        latest_id = data[0]['id']
        await client.patch(
            f"{SUPABASE_URL}/rest/v1/applications?id=eq.{latest_id}",
            headers=HEADERS, json={"status": "approved"}
        )
        if len(data) > 1:
            await client.delete(
                f"{SUPABASE_URL}/rest/v1/applications?handle=eq.{handle}&id=neq.{latest_id}",
                headers=SERVICE_HEADERS
            )
    text = f"Канал @{handle} одобрен и добавлен в Киоск!"
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
                    text=f"Ваш канал @{handle} одобрен и добавлен в Киоск! Открыть: @Kiosk_lenta_Bot"
                )
            except Exception:
                if message:
                    contact_val = data[0].get('contact', '—')
                    await message.reply_text(f"Не удалось уведомить владельца. Контакт: {contact_val}")
        else:
            if message:
                contact_val = data[0].get('contact', '—')
                await message.reply_text(f"Владелец не найден. Свяжитесь вручную: {contact_val}")

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
        res_all = await client.get(
            f"{SUPABASE_URL}/rest/v1/applications?handle=eq.{handle}&order=id.desc&select=id,contact,chat_id",
            headers=HEADERS
        )
        data = res_all.json()
        if not data:
            text = f"Заявка @{handle} не найдена."
            if query:
                await query.edit_message_text(query.message.text + "\n\n" + text)
            elif message:
                await message.reply_text(text)
            return
        latest_id = data[0]['id']
        await client.patch(
            f"{SUPABASE_URL}/rest/v1/applications?id=eq.{latest_id}",
            headers=HEADERS, json={"status": "rejected"}
        )
        if len(data) > 1:
            await client.delete(
                f"{SUPABASE_URL}/rest/v1/applications?handle=eq.{handle}&id=neq.{latest_id}",
                headers=SERVICE_HEADERS
            )
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
        subs = app.get('subscribers') or '—'
        text = (
            f"@{app['handle']} — {app['title']}\n"
            f"{app['categories']}\n"
            f"Контакт: {app['contact']}\n"
            f"Подписчиков: {subs}"
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

async def digest_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("Готовлю дайджест...")
    await send_digest_to_user(context.bot, update.effective_chat.id)

async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "Киоск — персональная лента каналов\n\n"
        "/start — открыть приложение\n"
        "/digest — получить дайджест новых постов\n"
        "/help — помощь"
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

async def handle_notify(request):
    try:
        data = await request.json()
    except Exception:
        return web.json_response({'ok': False}, status=400)
    handle = data.get('handle', '')
    title = data.get('title', handle)
    categories = data.get('categories', '')
    contact = data.get('contact', '')
    subscribers = data.get('subscribers', 0)
    description = data.get('description', '')
    user_id = data.get('user_id')
    try:
        bot = Bot(token=BOT_TOKEN)
        async with httpx.AsyncClient() as client:
            check = await client.get(
                f"{SUPABASE_URL}/rest/v1/applications?handle=eq.{handle}&status=in.(pending,approved)&select=id,status",
                headers=HEADERS
            )
            existing = check.json()
        if existing:
            status = existing[0]['status']
            if status == 'approved':
                msg = f"Канал @{handle} уже добавлен в Киоск! Открыть: @Kiosk_lenta_Bot"
            else:
                msg = f"Заявка на канал @{handle} уже на рассмотрении. Мы сообщим вам о решении!"
            if user_id:
                await bot.send_message(chat_id=user_id, text=msg)
            return web.json_response({'ok': True, 'duplicate': True}, headers={'Access-Control-Allow-Origin': '*'})
        async with httpx.AsyncClient() as client:
            await client.post(
                f"{SUPABASE_URL}/rest/v1/applications",
                headers=HEADERS,
                json={
                    "handle": handle, "title": title, "description": description,
                    "categories": categories, "contact": contact,
                    "subscribers": subscribers, "chat_id": user_id, "status": "pending"
                }
            )
        keyboard = InlineKeyboardMarkup([[
            InlineKeyboardButton("Одобрить", callback_data=f"approve:{handle}"),
            InlineKeyboardButton("Отклонить", callback_data=f"reject:{handle}")
        ]])
        await bot.send_message(
            chat_id=ADMIN_ID,
            text=f"Новая заявка!\n\nКанал: @{handle}\nНазвание: {title}\nКатегории: {categories}\nКонтакт: {contact}\nПодписчиков: {subscribers}",
            reply_markup=keyboard
        )
        if user_id:
            await bot.send_message(chat_id=user_id, text="Заявка отправлена! Рассмотрим в течение 24 часов.")
    except Exception as e:
        print(f"[ERROR] handle_notify: {e}")
        return web.json_response({'ok': False, 'error': str(e)}, status=500,
                                 headers={'Access-Control-Allow-Origin': '*'})
    return web.json_response({'ok': True}, headers={'Access-Control-Allow-Origin': '*'})

ADMIN_CORS = {
    'Access-Control-Allow-Origin': '*',
    'Access-Control-Allow-Methods': 'GET, POST, OPTIONS',
    'Access-Control-Allow-Headers': 'Content-Type, Authorization'
}

def is_admin(request):
    return ADMIN_SECRET and request.headers.get('Authorization') == f'Bearer {ADMIN_SECRET}'

async def handle_admin_options(request):
    return web.Response(headers=ADMIN_CORS)

async def handle_admin_stats(request):
    if not is_admin(request):
        return web.json_response({'error': 'Unauthorized'}, status=401, headers=ADMIN_CORS)
    async with httpx.AsyncClient() as client:
        r_approved = await client.get(f"{SUPABASE_URL}/rest/v1/applications?status=eq.approved&select=id", headers=HEADERS)
        r_pending  = await client.get(f"{SUPABASE_URL}/rest/v1/applications?status=eq.pending&select=id", headers=HEADERS)
        r_posts    = await client.get(f"{SUPABASE_URL}/rest/v1/posts?select=id", headers=HEADERS)
        r_users    = await client.get(f"{SUPABASE_URL}/rest/v1/users?select=id", headers=HEADERS)
        r_clicks   = await client.get(f"{SUPABASE_URL}/rest/v1/channel_clicks?select=id", headers=HEADERS)
    return web.json_response({
        'approved': len(r_approved.json()),
        'pending':  len(r_pending.json()),
        'posts':    len(r_posts.json()),
        'users':    len(r_users.json()),
        'clicks':   len(r_clicks.json()),
    }, headers=ADMIN_CORS)

async def handle_admin_channels(request):
    if not is_admin(request):
        return web.json_response({'error': 'Unauthorized'}, status=401, headers=ADMIN_CORS)
    async with httpx.AsyncClient() as client:
        r_apps   = await client.get(f"{SUPABASE_URL}/rest/v1/applications?order=id.desc&select=id,handle,title,status,categories,contact,subscribers", headers=HEADERS)
        r_clicks = await client.get(f"{SUPABASE_URL}/rest/v1/channel_clicks?select=channel_handle", headers=HEADERS)
    apps = r_apps.json()
    clicks_raw = r_clicks.json()
    click_counts = {}
    for row in clicks_raw:
        h = row['channel_handle']
        click_counts[h] = click_counts.get(h, 0) + 1
    for app in apps:
        app['clicks'] = click_counts.get(app['handle'], 0)
    return web.json_response(apps, headers=ADMIN_CORS)

async def handle_admin_approve(request):
    if not is_admin(request):
        return web.json_response({'error': 'Unauthorized'}, status=401, headers=ADMIN_CORS)
    data = await request.json()
    handle = data.get('handle', '').replace('@', '')
    async with httpx.AsyncClient() as client:
        res = await client.get(f"{SUPABASE_URL}/rest/v1/applications?handle=eq.{handle}&order=id.desc&select=id,chat_id,contact", headers=HEADERS)
        rows = res.json()
        if not rows:
            return web.json_response({'ok': False, 'error': 'Not found'}, headers=ADMIN_CORS)
        latest_id = rows[0]['id']
        await client.patch(f"{SUPABASE_URL}/rest/v1/applications?id=eq.{latest_id}", headers=HEADERS, json={"status": "approved"})
        if len(rows) > 1:
            await client.delete(f"{SUPABASE_URL}/rest/v1/applications?handle=eq.{handle}&id=neq.{latest_id}", headers=SERVICE_HEADERS)
    asyncio.create_task(fetch_and_save_posts(handle))
    chat_id = rows[0].get('chat_id')
    if not chat_id:
        contact = rows[0].get('contact', '').replace('@', '')
        if contact:
            async with httpx.AsyncClient() as c:
                r = await c.get(f"{SUPABASE_URL}/rest/v1/users?username=eq.{contact}&select=chat_id", headers=HEADERS)
                u = r.json()
                if u: chat_id = u[0].get('chat_id')
    if chat_id and bot_instance:
        try:
            await bot_instance.send_message(chat_id=chat_id, text=f"Ваш канал @{handle} одобрен и добавлен в Киоск! Открыть: @Kiosk_lenta_Bot")
        except Exception:
            pass
    return web.json_response({'ok': True}, headers=ADMIN_CORS)

async def handle_admin_reject(request):
    if not is_admin(request):
        return web.json_response({'error': 'Unauthorized'}, status=401, headers=ADMIN_CORS)
    data = await request.json()
    handle = data.get('handle', '').replace('@', '')
    async with httpx.AsyncClient() as client:
        res = await client.get(f"{SUPABASE_URL}/rest/v1/applications?handle=eq.{handle}&order=id.desc&select=id,chat_id,contact", headers=HEADERS)
        rows = res.json()
        if not rows:
            return web.json_response({'ok': False, 'error': 'Not found'}, headers=ADMIN_CORS)
        latest_id = rows[0]['id']
        await client.patch(f"{SUPABASE_URL}/rest/v1/applications?id=eq.{latest_id}", headers=HEADERS, json={"status": "rejected"})
        if len(rows) > 1:
            await client.delete(f"{SUPABASE_URL}/rest/v1/applications?handle=eq.{handle}&id=neq.{latest_id}", headers=SERVICE_HEADERS)
    chat_id = rows[0].get('chat_id')
    if not chat_id:
        contact = rows[0].get('contact', '').replace('@', '')
        if contact:
            async with httpx.AsyncClient() as c:
                r = await c.get(f"{SUPABASE_URL}/rest/v1/users?username=eq.{contact}&select=chat_id", headers=HEADERS)
                u = r.json()
                if u: chat_id = u[0].get('chat_id')
    if chat_id and bot_instance:
        try:
            await bot_instance.send_message(chat_id=chat_id, text=f"Заявка канала @{handle} отклонена. Если есть вопросы — напишите нам.")
        except Exception:
            pass
    return web.json_response({'ok': True}, headers=ADMIN_CORS)

async def handle_admin_remove(request):
    if not is_admin(request):
        return web.json_response({'error': 'Unauthorized'}, status=401, headers=ADMIN_CORS)
    data = await request.json()
    handle = data.get('handle', '').replace('@', '')
    async with httpx.AsyncClient() as client:
        await client.delete(f"{SUPABASE_URL}/rest/v1/applications?handle=eq.{handle}", headers=SERVICE_HEADERS)
        await client.delete(f"{SUPABASE_URL}/rest/v1/posts?channel_handle=eq.{handle}", headers=SERVICE_HEADERS)
    return web.json_response({'ok': True}, headers=ADMIN_CORS)

async def handle_admin_analytics(request):
    if not is_admin(request):
        return web.json_response({'error': 'Unauthorized'}, status=401, headers=ADMIN_CORS)
    async with httpx.AsyncClient() as client:
        r_opens  = await client.get(f"{SUPABASE_URL}/rest/v1/app_opens?select=created_at&order=created_at.desc", headers=HEADERS)
        r_clicks = await client.get(f"{SUPABASE_URL}/rest/v1/post_clicks?select=channel_handle,created_at&order=created_at.desc", headers=HEADERS)
    opens  = r_opens.json()
    clicks = r_clicks.json()

    # Group opens by date
    opens_by_day = {}
    for row in opens:
        day = row['created_at'][:10]
        opens_by_day[day] = opens_by_day.get(day, 0) + 1

    # Group post clicks by channel
    clicks_by_channel = {}
    for row in clicks:
        h = row['channel_handle']
        clicks_by_channel[h] = clicks_by_channel.get(h, 0) + 1

    top_channels = sorted(clicks_by_channel.items(), key=lambda x: x[1], reverse=True)[:10]

    return web.json_response({
        'opens_by_day': opens_by_day,
        'total_opens': len(opens),
        'total_post_clicks': len(clicks),
        'top_channels': [{'handle': h, 'clicks': c} for h, c in top_channels],
    }, headers=ADMIN_CORS)

async def handle_admin_sync(request):
    if not is_admin(request):
        return web.json_response({'error': 'Unauthorized'}, status=401, headers=ADMIN_CORS)
    asyncio.create_task(sync_posts())
    return web.json_response({'ok': True}, headers=ADMIN_CORS)

async def run_web_server():
    app = web.Application()
    app.router.add_get('/api/getchat', handle_getchat)
    app.router.add_get('/api/getchatmembercount', handle_getmembercount)
    app.router.add_post('/api/notify', handle_notify)
    app.router.add_options('/api/notify', lambda r: web.Response(headers={
        'Access-Control-Allow-Origin': '*',
        'Access-Control-Allow-Methods': 'POST, OPTIONS',
        'Access-Control-Allow-Headers': 'Content-Type'
    }))
    app.router.add_get('/api/admin/stats',       handle_admin_stats)
    app.router.add_get('/api/admin/channels',    handle_admin_channels)
    app.router.add_post('/api/admin/approve',    handle_admin_approve)
    app.router.add_post('/api/admin/reject',     handle_admin_reject)
    app.router.add_post('/api/admin/remove',     handle_admin_remove)
    app.router.add_post('/api/admin/sync',       handle_admin_sync)
    app.router.add_get('/api/admin/analytics',   handle_admin_analytics)
    app.router.add_options('/api/admin/{tail:.*}', handle_admin_options)
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, '0.0.0.0', PORT)
    await site.start()
    print(f"Proxy server started on port {PORT}")

async def post_init(application):
    global bot_instance
    bot_instance = application.bot
    from telegram import BotCommandScopeAllPrivateChats, BotCommandScopeChat
    await application.bot.set_my_commands([
        ("start", "Открыть Киоск"),
        ("digest", "Дайджест новых постов"),
        ("help", "Помощь"),
    ], scope=BotCommandScopeAllPrivateChats())
    await application.bot.set_my_commands([
        ("start", "Открыть Киоск"),
        ("digest", "Дайджест новых постов"),
        ("applications", "Список заявок"),
        ("approve", "Одобрить канал"),
        ("reject", "Отклонить канал"),
        ("remove", "Удалить канал"),
        ("sync", "Синхронизировать посты"),
        ("help", "Помощь"),
    ], scope=BotCommandScopeChat(chat_id=ADMIN_ID))
    asyncio.create_task(run_web_server())
    asyncio.create_task(periodic_sync(600))
    asyncio.create_task(daily_digest_scheduler(application.bot))
    print("Bot started! Periodic sync every 10 minutes.")

def main():
    app = Application.builder().token(BOT_TOKEN).post_init(post_init).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("help", help_command))
    app.add_handler(CommandHandler("digest", digest_command))
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
