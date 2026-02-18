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

# --- 1. إعداد Flask (لإبقاء البوت حياً على Render) ---
app = Flask(__name__)

@app.route('/')
def home():
    return "Bot is running. Service is active!"

def run_flask():
    port = int(os.environ.get("PORT", 10000))
    app.run(host='0.0.0.0', port=port)

# --- 2. قاموس الجلسات (تخزين حالة التشغيل وكائن المتصفح) ---
active_sessions = {}

# --- 3. وظيفة البث المباشر (Start) ---
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id

    if not context.args:
        await update.message.reply_text("❌ أرسل الرابط بعد الأمر...")
        return

    raw_url = context.args[0]
    if not raw_url.startswith(('http://', 'https://')):
        raw_url = 'https://' + raw_url

    # تهيئة الجلسة (نخزن المتصفح كـ None في البداية)
    active_sessions[chat_id] = {
        'is_running': True, 
        'step': 'accept_terms',
        'browser_instance': None 
    }
    
    await update.message.reply_text("🎭 جاري تشغيل المتصفح السحابي وتنفيذ المهام...")

    try:
        async with async_playwright() as p:
            # تشغيل المتصفح وتخزينه في القاموس ليتمكن /stop من إغلاقه
            browser = await p.chromium.launch(headless=True)
            active_sessions[chat_id]['browser_instance'] = browser
            
            browser_context = await browser.new_context(
                viewport={'width': 1280, 'height': 800},
                user_agent='Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36',
                locale='en-US',
                timezone_id='America/New_York'
            )
            
            page = await browser_context.new_page()
            
            # تفعيل التخفي
            try:
                await p_stealth.stealth_async(page)
            except:
                await page.add_init_script("Object.defineProperty(navigator, 'webdriver', {get: () => undefined})")

            await page.goto(raw_url, timeout=120000, wait_until="load")
            
            screenshot_bytes = await page.screenshot()
            live_message = await context.bot.send_photo(
                chat_id=chat_id, 
                photo=screenshot_bytes, 
                caption="🔴 بث مباشر من السيرفر\n⏳ جاري تنفيذ المهام الآلية..."
            )

            # حلقة الرادار
            while active_sessions.get(chat_id, {}).get('is_running'):
                current_step = active_sessions[chat_id].get('step')

                try:
                    # المرحلة 1: قبول الشروط
                    if current_step == 'accept_terms':
                        for text in ["I understand", "Ik begrijp het", "Accept all", "I agree", "Agree"]:
                            btn = page.get_by_text(text, exact=False).first
                            if await btn.is_visible(timeout=300):
                                await btn.click(force=True)
                                active_sessions[chat_id]['step'] = 'wait_for_console'
                                break

                    # المرحلة 2: رصد لوحة التحكم
                    elif current_step == 'wait_for_console':
                        if "console.cloud.google.com" in page.url:
                            page_text = await page.content()
                            match = re.search(r'qwiklabs-gcp-[a-zA-Z0-9\-]+', page_text)
                            project_id = match.group(0) if match else ""
                            shell_url = f"https://console.cloud.google.com/cloudshell?project={project_id}"
                            await page.goto(shell_url, timeout=120000)
                            active_sessions[chat_id]['step'] = 'start_cloud_shell'

                    # المرحلة 3: شروط Cloud Shell
                    elif current_step == 'start_cloud_shell':
                        start_btn = page.get_by_text("Start Cloud Shell", exact=False).first
                        if await start_btn.is_visible(timeout=300):
                            checkbox = page.get_by_role("checkbox").first
                            if await checkbox.is_visible(): await checkbox.check(force=True)
                            await asyncio.sleep(1)
                            await start_btn.click(force=True)
                            active_sessions[chat_id]['step'] = 'wait_for_authorize'

                    # المرحلة 4: Authorize
                    elif current_step == 'wait_for_authorize':
                        auth_btn = page.get_by_text("Authorize", exact=True).first
                        if await auth_btn.is_visible(timeout=300):
                            await auth_btn.click(force=True)
                            active_sessions[chat_id]['step'] = 'done'
                            await context.bot.send_message(chat_id=chat_id, text="🎉 تم تجهيز التيرمينال!")
                except:
                    pass

                await asyncio.sleep(4)
                if not active_sessions.get(chat_id, {}).get('is_running'):
                    break
                
                try:
                    new_screenshot = await page.screenshot()
                    await context.bot.edit_message_media(
                        chat_id=chat_id,
                        message_id=live_message.message_id,
                        media=InputMediaPhoto(new_screenshot)
                    )
                except BadRequest as e:
                    if "Message is not modified" in str(e): continue
                except Exception: continue

            # إغلاق نهائي عند انتهاء الحلقة بشكل طبيعي
            if browser:
                await browser.close()
            
    except Exception as e:
        # إذا تم إغلاق المتصفح بواسطة /stop، سيظهر خطأ هنا، نقوم بتجاهله
        if "Target closed" not in str(e):
            await update.message.reply_text(f"❌ خطأ: {str(e)}")
    finally:
        if chat_id in active_sessions:
            del active_sessions[chat_id]

# --- 4. وظيفة إيقاف العملية بالكامل (Stop) ---
async def stop_stream(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    
    if chat_id in active_sessions:
        await update.message.reply_text("⏳ جاري إيقاف العملية وإغلاق المتصفح تماماً...")
        
        # 1. إيقاف حلقة الـ While
        active_sessions[chat_id]['is_running'] = False
        
        # 2. إغلاق المتصفح فوراً إذا كان مفتوحاً
        browser = active_sessions[chat_id].get('browser_instance')
        if browser:
            try:
                await browser.close()
                print(f"Browser for chat {chat_id} closed via /stop")
            except Exception as e:
                print(f"Error closing browser: {e}")
        
        await update.message.reply_text("⏹️ تم إنهاء كافة العمليات بنجاح.")
    else:
        await update.message.reply_text("❌ لا توجد عملية نشطة لإيقافها.")

# --- 5. التشغيل ---
if __name__ == '__main__':
    TOKEN = os.environ.get("TELEGRAM_TOKEN")
    
    if TOKEN:
        threading.Thread(target=run_flask, daemon=True).start()
        application = ApplicationBuilder().token(TOKEN).build()
        application.add_handler(CommandHandler("start", start))
        application.add_handler(CommandHandler("stop", stop_stream))
        print("Bot is starting...")
        application.run_polling()
