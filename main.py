import os
import threading
import asyncio
from flask import Flask
from telegram import Update
from telegram.ext import ApplicationBuilder, CommandHandler, ContextTypes
from playwright.async_api import async_playwright

# 1. إعدادات Flask (لإبقاء البوت حياً)
app = Flask(__name__)

@app.route('/')
def home():
    return "Bot is running with Browser Automation!"

def run_flask():
    port = int(os.environ.get("PORT", 5000))
    app.run(host='0.0.0.0', port=port)

# 2. وظيفة التصفح (Browser Automation)
async def open_google(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    await update.message.reply_text("⏳ جاري فتح المتصفح... انتظر قليلاً")

    try:
        # تشغيل المتصفح (Playwright)
        async with async_playwright() as p:
            # launch: تشغيل المتصفح في وضع الخفاء (headless=True مهم جداً للسيرفرات)
            browser = await p.chromium.launch(headless=True)
            page = await browser.new_page()

            # الخطوة 1: الذهاب للموقع
            await update.message.reply_text("1️⃣: جاري الدخول إلى Google...")
            await page.goto("https://www.google.com")
            
            # التقاط صورة وإرسالها
            screenshot_path = "step1.png"
            await page.screenshot(path=screenshot_path)
            await context.bot.send_photo(chat_id=chat_id, photo=open(screenshot_path, 'rb'), caption="الصفحة الرئيسية")

            # الخطوة 2: البحث عن شيء ما (مثال: Render)
            await update.message.reply_text("2️⃣: جاري كتابة كلمة البحث...")
            # البحث عن مربع النص وكتابة Render
            await page.fill('textarea[name="q"]', 'Render Cloud Hosting') 
            await page.keyboard.press('Enter')
            
            # الانتظار قليلاً للتحميل
            await page.wait_for_timeout(2000)

            # التقاط صورة للنتائج
            screenshot_path_2 = "step2.png"
            await page.screenshot(path=screenshot_path_2)
            await context.bot.send_photo(chat_id=chat_id, photo=open(screenshot_path_2, 'rb'), caption="نتائج البحث")

            await browser.close()
            await update.message.reply_text("✅ تمت العملية بنجاح!")

    except Exception as e:
        await update.message.reply_text(f"❌ حدث خطأ: {str(e)}")

# 3. التشغيل الرئيسي
if __name__ == '__main__':
    TOKEN = os.environ.get("TELEGRAM_TOKEN")
    
    if not TOKEN:
        print("Error: TELEGRAM_TOKEN not found!")
    else:
        # تشغيل Flask
        flask_thread = threading.Thread(target=run_flask)
        flask_thread.start()

        # تشغيل البوت
        application = ApplicationBuilder().token(TOKEN).build()
        
        # إضافة أمر /google لتجربة المتصفح
        application.add_handler(CommandHandler("google", open_google))
        
        print("Bot is starting...")
        application.run_polling()
