import os
import threading
import asyncio
import random
import re
from flask import Flask
from telegram import Update, InputMediaPhoto
from telegram.error import BadRequest
from telegram.ext import ApplicationBuilder, CommandHandler, ContextTypes
from playwright.async_api import async_playwright
import playwright_stealth as p_stealth

# 🚀 استيراد مكتبة الشاشة الوهمية
from pyvirtualdisplay import Display

# --- إعداد Flask ---
app = Flask(__name__)
@app.route('/')
def home(): return "Bot is running with Virtual Display!"

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
    
    active_sessions[chat_id] = {'is_running': True, 'step': 'accept_terms', 'browser_instance': None}
    await update.message.reply_text("🎭 جاري تشغيل المتصفح بـ (شاشة وهمية) لتخطي حماية جوجل...")

    # 🖥️ تشغيل الشاشة الوهمية في الخلفية قبل فتح المتصفح
    disp = Display(visible=0, size=(1280, 800))
    disp.start()

    try:
        async with async_playwright() as p:
            # 🎯 السر هنا: المتصفح مرئي (False) لكنه يعمل داخل الشاشة الوهمية!
            browser = await p.chromium.launch(
                headless=False, 
                args=[
                    '--no-sandbox',
                    '--disable-setuid-sandbox',
                    '--disable-blink-features=AutomationControlled',
                    '--start-maximized',
                    '--disable-infobars'
                ]
            )
            active_sessions[chat_id]['browser_instance'] = browser
            
            browser_context = await browser.new_context(
                no_viewport=True, # استخدام حجم الشاشة الوهمية بالكامل
                user_agent='Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36',
                locale='en-US',
                timezone_id='America/New_York'
            )
            
            page = await browser_context.new_page()
            
            try: await p_stealth.stealth_async(page)
            except: await page.add_init_script("Object.defineProperty(navigator, 'webdriver', {get: () => undefined})")

            await page.goto(raw_url, timeout=120000, wait_until="load")
            
            screenshot_bytes = await page.screenshot()
            live_message = await context.bot.send_photo(
                chat_id=chat_id, 
                photo=screenshot_bytes, 
                caption="🔴 بث مباشر (متصفح مرئي وهمي)\n⏳ جاري تنفيذ المهام..."
            )

            while active_sessions.get(chat_id, {}).get('is_running'):
                current_step = active_sessions[chat_id].get('step')

                try:
                    # المرحلة 1: قبول شروط جوجل
                    if current_step == 'accept_terms':
                        for text in ["I understand", "Ik begrijp het", "Accept all", "I agree", "Agree", "Confirm"]:
                            btn = page.get_by_text(text, exact=False).first
                            if await btn.is_visible(timeout=500):
                                await asyncio.sleep(random.uniform(1, 2.5)) # تأخير بشري
                                await btn.click(force=True)
                                active_sessions[chat_id]['step'] = 'wait_for_console'
                                break

                    # المرحلة 2: رصد لوحة التحكم والانتقال للكلاود شيل
                    elif current_step == 'wait_for_console':
                        if "console.cloud.google.com" in page.url or await page.get_by_text("Cloud overview").is_visible(timeout=500):
                            page_text = await page.content()
                            match = re.search(r'qwiklabs-gcp-[a-zA-Z0-9\-]+', page_text)
                            project_id = match.group(0) if match else ""
                            shell_url = f"https://console.cloud.google.com/cloudshell?project={project_id}"
                            await page.goto(shell_url, timeout=120000)
                            active_sessions[chat_id]['step'] = 'start_cloud_shell'

                    # المرحلة 3: شروط Cloud Shell
                    elif current_step == 'start_cloud_shell':
                        start_btn = page.get_by_text("Start Cloud Shell", exact=False).first
                        if await start_btn.is_visible(timeout=500):
                            checkbox = page.get_by_role("checkbox").first
                            if await checkbox.is_visible(): await checkbox.check(force=True)
                            await asyncio.sleep(1)
                            await start_btn.click(force=True)
                            active_sessions[chat_id]['step'] = 'wait_for_authorize'

                    # المرحلة 4: Authorize
                    elif current_step == 'wait_for_authorize':
                        auth_btn = page.get_by_text("Authorize", exact=True).first
                        if await auth_btn.is_visible(timeout=500):
                            await auth_btn.click(force=True)
                            active_sessions[chat_id]['step'] = 'done'
                            await context.bot.send_message(chat_id=chat_id, text="🎉 تم تجهيز التيرمينال!")
                except: pass

                await asyncio.sleep(4)
                if not active_sessions.get(chat_id, {}).get('is_running'): break
                
                try:
                    new_screenshot = await page.screenshot()
                    await context.bot.edit_message_media(chat_id=chat_id, message_id=live_message.message_id, media=InputMediaPhoto(new_screenshot))
                except BadRequest as e:
                    if "Message is not modified" in str(e): continue
                except Exception: continue

            if browser: await browser.close()
            
    except Exception as e:
        if "Target closed" not in str(e): await update.message.reply_text(f"❌ خطأ: {str(e)}")
    finally:
        disp.stop() # إغلاق الشاشة الوهمية لتحرير الذاكرة
        if chat_id in active_sessions: del active_sessions[chat_id]

async def stop_stream(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    if chat_id in active_sessions:
        active_sessions[chat_id]['is_running'] = False
        browser = active_sessions[chat_id].get('browser_instance')
        if browser:
            try: await browser.close()
            except: pass
        await update.message.reply_text("⏹️ تم إنهاء كافة العمليات.")

if __name__ == '__main__':
    TOKEN = os.environ.get("TELEGRAM_TOKEN")
    if TOKEN:
        threading.Thread(target=run_flask, daemon=True).start()
        application = ApplicationBuilder().token(TOKEN).build()
        application.add_handler(CommandHandler("start", start))
        application.add_handler(CommandHandler("stop", stop_stream))
        print("Bot is starting with Virtual Display (Xvfb)...")
        application.run_polling()
