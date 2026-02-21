import telebot
import os
import time
import threading
import io
import shutil
from datetime import datetime
from telebot.types import InputMediaPhoto, InlineKeyboardMarkup, InlineKeyboardButton
from selenium import webdriver
from selenium.webdriver.chrome.options import Options

TOKEN = os.getenv('BOT_TOKEN')
if not TOKEN:
    raise ValueError("لم يتم العثور على التوكن! تأكد من تشغيل أمر export BOT_TOKEN أولاً.")

bot = telebot.TeleBot(TOKEN)

user_sessions = {}

def get_driver():
    options = Options()
    options.add_argument('--headless=new') 
    options.add_argument('--incognito') # الحفاظ على وضع التخفي كما طلبت
    options.add_argument('--no-sandbox')
    options.add_argument('--disable-dev-shm-usage')
    options.add_argument('--disable-gpu')
    options.add_argument('--window-size=1280,720')
    
    # 🎭 1. خطوات التمويه (Stealth Options) لإخفاء علامات الأتمتة
    options.add_argument('--disable-blink-features=AutomationControlled')
    options.add_experimental_option("excludeSwitches", ["enable-automation"])
    options.add_experimental_option('useAutomationExtension', False)
    options.add_argument('user-agent=Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36')
    
    browser_path = shutil.which('google-chrome') or shutil.which('chromium') or shutil.which('chromium-browser')
    options.binary_location = browser_path
    
    driver = webdriver.Chrome(options=options)
    
    # 🎭 2. حقن أكواد JavaScript متقدمة لجعل الموقع يظن أنك إنسان حقيقي
    # هذا يزيل المتغيرات التي تستخدمها جوجل لكشف البوتات
    driver.execute_cdp_cmd("Page.addScriptToEvaluateOnNewDocument", {
        "source": """
            Object.defineProperty(navigator, 'webdriver', {get: () => undefined});
            window.navigator.chrome = {runtime: {}};
            Object.defineProperty(navigator, 'languages', {get: () => ['en-US', 'en']});
            Object.defineProperty(navigator, 'plugins', {get: () => [1, 2, 3, 4, 5]});
        """
    })
    
    driver.set_page_load_timeout(15)
    return driver

def create_control_panel():
    markup = InlineKeyboardMarkup()
    btn_stop = InlineKeyboardButton("⏹ إيقاف البث", callback_data="stop_stream")
    btn_refresh = InlineKeyboardButton("🔄 تحديث الصفحة", callback_data="refresh_page")
    markup.row(btn_stop, btn_refresh)
    return markup

def stream_loop(chat_id):
    session = user_sessions[chat_id]
    driver = session['driver']
    
    flash_state = True 
    
    while session['running']:
        time.sleep(4) 
        
        if not session['running']:
            break
            
        try:
            png_data = driver.get_screenshot_as_png()
            bio = io.BytesIO(png_data)
            bio.name = 'image.png'
            
            flash_state = not flash_state
            live_icon = "🔴" if flash_state else "⭕"
            now = datetime.now().strftime("%H:%M:%S")
            caption_text = f"{live_icon} بث حي ومستمر...\n🔗 {session['url']}\n⏱ {now}"
            
            bot.edit_message_media(
                media=InputMediaPhoto(bio, caption=caption_text),
                chat_id=chat_id,
                message_id=session['message_id'],
                reply_markup=create_control_panel()
            )
        except Exception as e:
            if "Too Many Requests" in str(e) or "retry after" in str(e).lower():
                time.sleep(2)
            pass 

def start_stream(chat_id, url):
    bot.send_message(chat_id, "⚡ جاري إعداد المتصفح المموه لتخطي حماية جوجل...")
    
    if chat_id not in user_sessions:
        user_sessions[chat_id] = {'driver': get_driver(), 'running': False, 'message_id': None, 'url': url}
    else:
        user_sessions[chat_id]['url'] = url
    
    session = user_sessions[chat_id]
    driver = session['driver']
    
    session['running'] = False 
    time.sleep(1) 
    
    try:
        driver.get(url)
    except:
        pass 
        
    time.sleep(3) # زيادة وقت الانتظار قليلاً للسماح للموقع بالتحميل وتجاوز الفحص
    
    png_data = driver.get_screenshot_as_png()
    bio = io.BytesIO(png_data)
    bio.name = 'image.png'
    
    msg = bot.send_photo(
        chat_id, 
        bio, 
        caption=f"🔴 بث حي ومستمر...\n🔗 {url}\n⏱ جاري الاتصال...",
        reply_markup=create_control_panel()
    )
    
    session['message_id'] = msg.message_id
    session['running'] = True
    
    threading.Thread(target=stream_loop, args=(chat_id,)).start()

@bot.message_handler(commands=['start'])
def send_welcome(message):
    bot.reply_to(message, "مرحباً! أرسل لي أي رابط وسأقوم بفتحه متخفياً كإنسان حقيقي. 🚀")

@bot.message_handler(func=lambda message: message.text.startswith('http'))
def handle_url(message):
    threading.Thread(target=start_stream, args=(message.chat.id, message.text)).start()

@bot.callback_query_handler(func=lambda call: True)
def callback_query(call):
    chat_id = call.message.chat.id
    
    try:
        if chat_id not in user_sessions:
            bot.answer_callback_query(call.id, "لا توجد جلسة نشطة.")
            return
            
        session = user_sessions[chat_id]
        
        if call.data == "stop_stream":
            session['running'] = False
            bot.answer_callback_query(call.id, "تم إيقاف البث.")
            bot.edit_message_caption("🛑 تم إيقاف البث.", chat_id=chat_id, message_id=session['message_id'])
            
        elif call.data == "refresh_page":
            bot.answer_callback_query(call.id, "جاري تحديث الصفحة...")
            try:
                session['driver'].refresh()
            except:
                pass
    except Exception as e:
        if "query is too old" not in str(e).lower():
            pass

print("البوت يعمل الآن (نظام التمويه المتقدم مفعل)...")
bot.polling()
