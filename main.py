import os
from flask import Flask
from telegram import Update
from telegram.ext import ApplicationBuilder, CommandHandler, MessageHandler, filters, ContextTypes
import threading

# 1. إعدادات Flask (لإبقاء البوت نشطاً على Render)
app = Flask(__name__)

@app.route('/')
def home():
    return "Bot is alive!"

def run_flask():
    # Render يعطيك منفذ PORT عبر متغيرات البيئة
    port = int(os.environ.get("PORT", 5000))
    app.run(host='0.0.0.0', port=port)

# 2. وظائف البوت
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text('أهلاً! أنا بوت تجريبي يعمل على Render.')

async def echo(update: Update, context: ContextTypes.DEFAULT_TYPE):
    # يرد بنفس الرسالة التي أرسلتها
    await update.message.reply_text(f'أنت قلت: {update.message.text}')

# 3. التشغيل الرئيسي
if __name__ == '__main__':
    # الحصول على التوكن من متغيرات البيئة (سنضيفه في Render لاحقاً)
    TOKEN = os.environ.get("TELEGRAM_TOKEN")
    
    if not TOKEN:
        print("Error: TELEGRAM_TOKEN not found!")
    else:
        # تشغيل Flask في الخلفية (Thread)
        flask_thread = threading.Thread(target=run_flask)
        flask_thread.start()

        # تشغيل البوت
        print("Bot is starting...")
        application = ApplicationBuilder().token(TOKEN).build()
        
        application.add_handler(CommandHandler("start", start))
        application.add_handler(MessageHandler(filters.TEXT & (~filters.COMMAND), echo))
        
        # نستخدم polling هنا للبساطة
        application.run_polling()
