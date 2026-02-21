import telebot
import os
import time
import threading
import io
import shutil
from telebot.types import InputMediaPhoto

from selenium import webdriver
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.chrome.service import Service

TOKEN = os.getenv('BOT_TOKEN')
if not TOKEN:
    raise ValueError("لم يتم العثور على التوكن!")

bot = telebot.TeleBot(TOKEN)
streaming_status = {}

def capture_stream(chat_id, url):
    streaming_status[chat_id] = True
    bot.send_message(chat_id, "⚡ جاري فتح الرابط بالوضع السريع...")
    driver = None
    try:
        options = Options()
        options.add_argument('--headless=new')
        options.add_argument('--no-sandbox')
        options.add_argument('--disable-dev-shm-usage')
        options.add_argument('--disable-gpu')
        options.add_argument('--window-size=1280,720')
        options.add_argument('--remote-debugging-port=9222')
        options.add_argument('user-agent=Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36')

        # مسار Chromium على Docker/Linux
        browser_path = (
            shutil.which('google-chrome') or
            shutil.which('chromium') or
            shutil.which('chromium-browser') or
            '/usr/bin/chromium'
        )
        options.binary_location = browser_path

        driver_path = (
            shutil.which('chromedriver') or
            '/usr/bin/chromedriver'
        )
        service = Service(executable_path=driver_path)
        driver = webdriver.Chrome(service=service, options=options)
        driver.set_page_load_timeout(10)

        try:
            driver.get(url)
        except:
            pass

        time.sleep(1.5)

        png_data = driver.get_screenshot_as_png()
        bio = io.BytesIO(png_data)
        bio.name = 'image.png'

        message_to_edit = bot.send_photo(
            chat_id,
            bio,
            caption="🎥 البث السريع يعمل الآن...\n(أرسل /stop لإيقافه)"
        )

        while streaming_status.get(chat_id, False):
            time.sleep(1.5)
            if not streaming_status.get(chat_id, False):
                break
            try:
                png_data = driver.get_screenshot_as_png()
                bio = io.BytesIO(png_data)
                bio.name = 'image.png'
                bot.edit_message_media(
                    media=InputMediaPhoto(bio),
                    chat_id=chat_id,
                    message_id=message_to_edit.message_id
                )
            except:
                pass

        driver.quit()
        bot.send_message(chat_id, "🛑 تم إيقاف البث.")

    except Exception as e:
        bot.send_message(chat_id, f"⚠️ حدث خطأ:\n{e}")
        streaming_status[chat_id] = False
        if driver:
            try:
                driver.quit()
            except:
                pass

@bot.message_handler(commands=['stop'])
def stop_stream(message):
    chat_id = message.chat.id
    if streaming_status.get(chat_id, False):
        streaming_status[chat_id] = False
        bot.reply_to(message, "جاري الإيقاف...")
    else:
        bot.reply_to(message, "لا يوجد بث يعمل حالياً.")

@bot.message_handler(func=lambda message: message.text.startswith('http'))
def handle_url(message):
    chat_id = message.chat.id
    url = message.text
    if streaming_status.get(chat_id, False):
        bot.reply_to(message, "هناك بث يعمل حالياً! أرسل /stop لإيقافه أولاً.")
        return
    threading.Thread(target=capture_stream, args=(chat_id, url)).start()

@bot.message_handler(commands=['start'])
def send_welcome(message):
    bot.reply_to(message, "مرحباً! أرسل لي أي رابط وسأقوم بفتحه وبثه لك بسرعة.")

print("البوت السريع يعمل الآن...")
bot.polling(none_stop=True)
