import os
import threading
import asyncio
from flask import Flask
from telegram import Update, InputMediaPhoto
from telegram.ext import ApplicationBuilder, CommandHandler, ContextTypes
from playwright.async_api import async_playwright

# إعدادات Flask (لإبقاء البوت حياً)
app = Flask(__name__)

@app.route('/')
def home():
    return "Bot is running with Screen Share automation!"

def run_flask():
    port = int(os.environ.get("PORT", 5000))
    app.run(host='0.0.0.0', port=port)

# قاموس لحفظ حالة البث لكل مستخدم (لتشغيله وإيقافه)
active_sessions = {}

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
    await update.message.reply_text(f"⏳ جاري تجهيز البث المباشر للموقع:\n{url}")

    try:
        async with async_playwright() as p:
            browser = await p.chromium.launch(headless=True)
            page = await browser.new_page(viewport={'width': 1280, 'height': 800})
            
            await page.goto(url, timeout=60000)
            
            # التقاط أول صورة في الذاكرة (بدون حفظها في ملف لتسريع العملية)
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
            await browser.close()
            await context.bot.send_message(chat_id=chat_id, text="⏹️ تم إيقاف البث وإغلاق المتصفح بنجاح.")
            
    except Exception as e:
        await update.message.reply_text(f"❌ حدث خطأ: {str(e)}")
    finally:
        # تنظيف الجلسة
        if chat_id in active_sessions:
            del active_sessions[chat_id]


async def stop_stream(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    
    # التحقق مما إذا كان هناك بث يعمل لنقوم بإيقافه
    if chat_id in active_sessions and active_sessions[chat_id].get('is_running'):
        active_sessions[chat_id]['is_running'] = False
        await update.message.reply_text("⏳ جاري إنهاء البث...")
    else:
        await update.message.reply_text("لا يوجد بث يعمل حالياً لإيقافه.")


# التشغيل الرئيسي
if __name__ == '__main__':
    TOKEN = os.environ.get("TELEGRAM_TOKEN")
    
    if not TOKEN:
        print("Error: TELEGRAM_TOKEN not found!")
    else:
        flask_thread = threading.Thread(target=run_flask)
        flask_thread.start()

        application = ApplicationBuilder().token(TOKEN).build()
        
        # ربط الأوامر بالوظائف
        application.add_handler(CommandHandler("start", start))
        application.add_handler(CommandHandler("stop", stop_stream))
        
        print("Bot is starting...")
        application.run_polling()
