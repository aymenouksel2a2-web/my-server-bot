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

# 2. وظيفة الترحيب أو تصوير الروابط
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    
    # التحقق مما إذا كان هناك نص (رابط) بعد الأمر /start
    if context.args:
        url = context.args[0]
        
        # التأكد من أن الرابط يبدأ بـ http:// أو https://
        if not url.startswith(('http://', 'https://')):
            url = 'https://' + url
            
        await update.message.reply_text(f"⏳ جاري فتح الرابط:\n{url}\nالرجاء الانتظار قليلاً...")
        
        try:
            async with async_playwright() as p:
                browser = await p.chromium.launch(headless=True)
                # تحديد أبعاد شاشة واضحة (مثل شاشة اللابتوب)
                page = await browser.new_page(viewport={'width': 1280, 'height': 800})
                
                # الذهاب للرابط (مع إعطائه وقت إضافي للتحميل في حال كان الموقع ثقيلاً)
                await page.goto(url, timeout=60000)
                
                # التقاط الصورة
                screenshot_path = "website.png"
                await page.screenshot(path=screenshot_path)
                
                # إرسال الصورة لتيليجرام
                await context.bot.send_photo(
                    chat_id=chat_id, 
                    photo=open(screenshot_path, 'rb'), 
                    caption=f"📸 لقطة شاشة للموقع:\n{url}"
                )
                
                await browser.close()
        except Exception as e:
            # في حال كان الرابط خاطئاً أو الموقع لا يعمل
            await update.message.reply_text(f"❌ حدث خطأ أثناء محاولة فتح الرابط:\n{str(e)}")
            
    else:
        # إذا أرسل المستخدم /start فقط بدون روابط
        welcome_message = (
            "أهلاً بك! 👋\n\n"
            "أنا بوت تصوير المواقع. لتصوير أي موقع، فقط أرسل الأمر متبوعاً بالرابط.\n\n"
            "**مثال:**\n"
            "`/start github.com`\nأو\n`/start https://render.com`"
        )
        await update.message.reply_text(welcome_message, parse_mode='Markdown')


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
        
        # ربط أمر /start بالوظيفة الجديدة
        application.add_handler(CommandHandler("start", start))
        
        print("Bot is starting...")
        application.run_polling()
