import os
import threading
import asyncio
from flask import Flask
from telegram import Update, InputMediaPhoto
from telegram.ext import ApplicationBuilder, CommandHandler, ContextTypes
from playwright.async_api import async_playwright

# 1. إعدادات Flask (لإبقاء البوت حياً على Render)
app = Flask(__name__)

@app.route('/')
def home():
    return "Bot is running with Screen Share automation!"

def run_flask():
    port = int(os.environ.get("PORT", 5000))
    app.run(host='0.0.0.0', port=port)

# 2. قاموس لحفظ حالة البث لكل مستخدم (لتشغيله وإيقافه)
active_sessions = {}

# 3. وظيفة البث المباشر (Start)
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id

    # إذا أرسل المستخدم /start فقط
    if not context.args:
        await update.message.reply_text("أهلاً بك! لتشغيل مشاركة الشاشة، أرسل الرابط هكذا:\n`/start google.com`", parse_mode='Markdown')
        return

    # التحقق مما إذا كان هناك بث يعمل بالفعل لهذا المستخدم
    if chat_id in active_sessions and active_sessions[chat_id].get('is_running'):
        await update.message.reply_text("⚠️ هناك بث يعمل حالياً! أرسل /stop لإيقافه أولاً.")
        return

    url = context.args[0]
    if not url.startswith(('http://', 'https://')):
        url = 'https://' + url

    # تعيين حالة البث إلى "يعمل"
    active_sessions[chat_id] = {'is_running': True}
    await update.message.reply_text(f"⏳ جاري تجهيز البث المباشر للموقع...\nقد يستغرق الأمر بضع ثوانٍ لتخطي شاشات التحميل.")

    try:
        async with async_playwright() as p:
            # تشغيل المتصفح في الخلفية
            browser = await p.chromium.launch(headless=True)
            
            # إضافة User-Agent لكي لا يتم حظر البوت من قبل مواقع مثل جوجل
            context_browser = await browser.new_context(
                viewport={'width': 1280, 'height': 800},
                user_agent='Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36'
            )
            page = await context_browser.new_page()
            
            # الذهاب للرابط والانتظار حتى يكتمل تحميل المحتوى الأساسي
            await page.goto(url, timeout=60000, wait_until="domcontentloaded")
            
            # انتظار إضافي (5 ثوانٍ) للسماح لأي سكريبتات أو تحويلات بالانتهاء قبل التقاط أول صورة
            await asyncio.sleep(5)
            
            # التقاط أول صورة في الذاكرة
            screenshot_bytes = await page.screenshot()
            
            # إرسال الرسالة الأولى التي سنقوم بتحديثها لاحقاً
            live_message = await context.bot.send_photo(
                chat_id=chat_id, 
                photo=screenshot_bytes, 
                caption=f"🔴 **بث مباشر يعمل الآن**\nلإيقاف البث أرسل /stop",
                parse_mode='Markdown'
            )

            # حلقة التحديث المستمر (Screen Share)
            while active_sessions.get(chat_id, {}).get('is_running'):
                # الانتظار 3 ثوانٍ لتجنب حظر تيليجرام (Rate Limits)
                await asyncio.sleep(3)
                
                # التأكد مرة أخرى أن المستخدم لم يرسل /stop أثناء الانتظار
                if not active_sessions.get(chat_id, {}).get('is_running'):
                    break

                try:
                    # التقاط صورة جديدة
                    new_screenshot = await page.screenshot()
                    
                    # تحديث نفس الرسالة بالصورة الجديدة
                    await context.bot.edit_message_media(
                        chat_id=chat_id,
                        message_id=live_message.message_id,
                        media=InputMediaPhoto(new_screenshot)
                    )
                except Exception as e:
                    # تيليجرام يرفض التحديث إذا كانت الصورة الجديدة مطابقة تماماً للقديمة
                    if "Message is not modified" in str(e):
                        continue # تجاهل الخطأ لأن الشاشة لم تتغير
                    else:
                        print(f"Update error: {e}")

            # عند الخروج من الحلقة (إرسال /stop)
            await context_browser.close()
            await browser.close()
            await context.bot.send_message(chat_id=chat_id, text="⏹️ تم إيقاف البث وإغلاق المتصفح بنجاح.")
            
    except Exception as e:
        await update.message.reply_text(f"❌ حدث خطأ: {str(e)}")
    finally:
        # تنظيف الجلسة
        if chat_id in active_sessions:
            del active_sessions[chat_id]

# 4. وظيفة إيقاف البث (Stop)
async def stop_stream(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    
    # التحقق مما إذا كان هناك بث يعمل لنقوم بإيقافه
    if chat_id in active_sessions and active_sessions[chat_id].get('is_running'):
        active_sessions[chat_id]['is_running'] = False
        await update.message.reply_text("⏳ جاري إنهاء البث...")
    else:
        await update.message.reply_text("لا يوجد بث يعمل حالياً لإيقافه.")

# 5. التشغيل الرئيسي
if __name__ == '__main__':
    TOKEN = os.environ.get("TELEGRAM_TOKEN")
    
    if not TOKEN:
        print("Error: TELEGRAM_TOKEN not found!")
    else:
        # تشغيل سيرفر الويب الوهمي
        flask_thread = threading.Thread(target=run_flask)
        flask_thread.start()

        # بناء البوت
        application = ApplicationBuilder().token(TOKEN).build()
        
        # ربط الأوامر بالوظائف
        application.add_handler(CommandHandler("start", start))
        application.add_handler(CommandHandler("stop", stop_stream))
        
        print("Bot is starting...")
        application.run_polling()
