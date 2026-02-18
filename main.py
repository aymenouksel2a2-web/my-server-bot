import os
import threading
import asyncio
import random
import re
from flask import Flask
from telegram import Update, InputMediaPhoto
from telegram.error import BadRequest, RetryAfter # 👈 استيراد خطأ حظر تيليجرام
from telegram.ext import ApplicationBuilder, CommandHandler, ContextTypes
from playwright.async_api import async_playwright
import playwright_stealth as p_stealth
from pyvirtualdisplay import Display

# --- إعداد Flask ---
app = Flask(__name__)
@app.route('/')
def home(): return "Bot is running with Ultimate Optimization!"

def run_flask():
    port = int(os.environ.get("PORT", 10000))
    app.run(host='0.0.0.0', port=port)

active_sessions = {}

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id

    if not context.args:
        await update.message.reply_text("❌ أرسل الرابط بعد الأمر...")
        return

    raw_url = context.args[0]
    
    # منع تشغيل أكثر من جلسة لنفس المستخدم
    if chat_id in active_sessions and active_sessions[chat_id].get('is_running'):
        await update.message.reply_text("⚠️ لديك بث يعمل بالفعل، قم بإيقافه أولاً بـ /stop")
        return

    active_sessions[chat_id] = {'is_running': True, 'step': 'accept_terms', 'browser_instance': None, 'display': None}
    await update.message.reply_text("🎭 جاري تشغيل المتصفح المحسن والبدء في البث السريع...")

    # 🖥️ تشغيل الشاشة الوهمية وحفظها في الجلسة لإغلاقها لاحقاً
    disp = Display(visible=0, size=(1280, 800))
    disp.start()
    active_sessions[chat_id]['display'] = disp

    try:
        async with async_playwright() as p:
            # 🎯 التخفيف الأقصى لاستهلاك السيرفر
            browser = await p.chromium.launch(
                headless=False, 
                args=[
                    '--no-sandbox',
                    '--disable-setuid-sandbox',
                    '--disable-dev-shm-usage',
                    '--disable-gpu',
                    '--disable-software-rasterizer',
                    '--disable-blink-features=AutomationControlled',
                    '--start-maximized',
                    '--disable-infobars',
                    '--disable-extensions',           # تعطيل الإضافات لتخفيف الرام
                    '--disable-background-networking',# منع اتصالات الخلفية غير الضرورية
                    '--mute-audio'                    # كتم الصوت لتوفير المعالجة
                ]
            )
            active_sessions[chat_id]['browser_instance'] = browser
            
            browser_context = await browser.new_context(
                no_viewport=True,
                user_agent='Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36',
                locale='en-US',
                timezone_id='America/New_York'
            )
            
            page = await browser_context.new_page()
            
            try: await p_stealth.stealth_async(page)
            except: await page.add_init_script("Object.defineProperty(navigator, 'webdriver', {get: () => undefined})")

            await page.goto(raw_url, timeout=120000, wait_until="load")
            
            # 🚀 التقاط الصورة الأولى بصيغة JPEG الخفيفة جداً بدلاً من PNG
            screenshot_bytes = await page.screenshot(type='jpeg', quality=60)
            live_message = await context.bot.send_photo(
                chat_id=chat_id, 
                photo=screenshot_bytes, 
                caption="🔴 بث مباشر (نسخة محسنة وسريعة)\n⏳ جاري تنفيذ المهام..."
            )

            while active_sessions.get(chat_id, {}).get('is_running'):
                current_step = active_sessions[chat_id].get('step')

                try:
                    # 📌 المرحلة 1: قبول شروط جوجل
                    if current_step == 'accept_terms':
                        understand_btn = page.locator("text='I understand'").first
                        if await understand_btn.is_visible(timeout=500):
                            await asyncio.sleep(random.uniform(1.0, 2.0)) 
                            await understand_btn.click(force=True)
                            active_sessions[chat_id]['step'] = 'wait_for_console'
                        else:
                            for text in ["Ik begrijp het", "Accept all", "I agree", "Agree", "Confirm"]:
                                btn = page.get_by_text(text, exact=False).first
                                if await btn.is_visible(timeout=200):
                                    await btn.click(force=True)
                                    active_sessions[chat_id]['step'] = 'wait_for_console'
                                    break

                    # 📌 المرحلة 2: رصد لوحة التحكم
                    elif current_step == 'wait_for_console':
                        if "console.cloud.google.com" in page.url or await page.get_by_text("Cloud overview").is_visible(timeout=500):
                            page_text = await page.content()
                            match = re.search(r'qwiklabs-gcp-[a-zA-Z0-9\-]+', page_text)
                            project_id = match.group(0) if match else ""
                            shell_url = f"https://console.cloud.google.com/cloudshell?project={project_id}"
                            await page.goto(shell_url, timeout=120000)
                            active_sessions[chat_id]['step'] = 'start_cloud_shell'

                    # 📌 المرحلة 3: شروط Cloud Shell
                    elif current_step == 'start_cloud_shell':
                        start_btn = page.get_by_text("Start Cloud Shell", exact=False).first
                        if await start_btn.is_visible(timeout=500):
                            checkbox = page.get_by_role("checkbox").first
                            if await checkbox.is_visible(): await checkbox.check(force=True)
                            await asyncio.sleep(1)
                            await start_btn.click(force=True)
                            active_sessions[chat_id]['step'] = 'wait_for_authorize'

                    # 📌 المرحلة 4: Authorize
                    elif current_step == 'wait_for_authorize':
                        auth_btn = page.get_by_text("Authorize", exact=True).first
                        if await auth_btn.is_visible(timeout=500):
                            await auth_btn.click(force=True)
                            active_sessions[chat_id]['step'] = 'done'
                            await context.bot.send_message(chat_id=chat_id, text="🎉 تم تجهيز التيرمينال بنجاح!")
                except Exception:
                    pass

                # انتظار ثابت بين اللقطات لتخفيف العبء على تيليجرام
                await asyncio.sleep(3)
                if not active_sessions.get(chat_id, {}).get('is_running'): break
                
                try:
                    # 🚀 استخدام JPEG مضغوط بنسبة 50% لتسريع نقل البث وتقليل الباندويث
                    new_screenshot = await page.screenshot(type='jpeg', quality=50)
                    await context.bot.edit_message_media(
                        chat_id=chat_id, 
                        message_id=live_message.message_id, 
                        media=InputMediaPhoto(new_screenshot)
                    )
                except BadRequest as e:
                    if "Message is not modified" in str(e): continue
                except RetryAfter as e:
                    # 🛡️ حماية تيليجرام: إذا غضب تيليجرام، ننتظر المدة التي يطلبها بالضبط!
                    print(f"⚠️ Telegram Rate Limit! Waiting {e.retry_after} seconds...")
                    await asyncio.sleep(e.retry_after)
                except Exception: 
                    continue

            if browser: await browser.close()
            
    except Exception as e:
        error_msg = str(e)
        if "Target closed" not in error_msg:
            await update.message.reply_text(f"❌ خطأ تقني: {error_msg}")
            
    finally:
        # 🧹 تنظيف الذاكرة بشكل آمن جداً
        if chat_id in active_sessions:
            d = active_sessions[chat_id].get('display')
            if d: d.stop() # إغلاق الشاشة الوهمية لتحرير الرام
            del active_sessions[chat_id]

async def stop_stream(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    if chat_id in active_sessions:
        active_sessions[chat_id]['is_running'] = False
        browser = active_sessions[chat_id].get('browser_instance')
        if browser:
            try: await browser.close()
            except: pass
        await update.message.reply_text("⏹️ تم إنهاء البث بنجاح.")
    else:
        await update.message.reply_text("⚠️ لا يوجد بث نشط لإيقافه.")

if __name__ == '__main__':
    TOKEN = os.environ.get("TELEGRAM_TOKEN")
    if TOKEN:
        threading.Thread(target=run_flask, daemon=True).start()
        application = ApplicationBuilder().token(TOKEN).build()
        application.add_handler(CommandHandler("start", start))
        application.add_handler(CommandHandler("stop", stop_stream))
        print("🚀 Bot is starting with ULTIMATE Optimization...")
        application.run_polling()
