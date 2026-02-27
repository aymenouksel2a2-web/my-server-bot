import os
import time
import threading
import io
import re
import json
from http.server import BaseHTTPRequestHandler, HTTPServer
import telebot
from telebot.types import InlineKeyboardMarkup, InlineKeyboardButton, InputMediaPhoto
from selenium import webdriver
from selenium.webdriver.chrome.options import Options
from pyvirtualdisplay import Display

# جلب توكن البوت من متغيرات البيئة في Railway
BOT_TOKEN = os.environ.get('BOT_TOKEN')
if not BOT_TOKEN:
    raise ValueError("الرجاء إضافة BOT_TOKEN في متغيرات البيئة (Environment Variables) في Railway.")

bot = telebot.TeleBot(BOT_TOKEN)

# ---------------------------------------------------------
# 1. إعداد خادم فحص الصحة (Healthcheck) الخاص بـ Railway
# ---------------------------------------------------------
class HealthCheckHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        if self.path == '/health':
            self.send_response(200)
            self.end_headers()
            self.wfile.write(b'OK')
        else:
            self.send_response(404)
            self.end_headers()
            
    def log_message(self, format, *args):
        pass

def run_health_server():
    server = HTTPServer(('0.0.0.0', 8080), HealthCheckHandler)
    server.serve_forever()

threading.Thread(target=run_health_server, daemon=True).start()

# ---------------------------------------------------------
# 2. إعدادات Selenium والبث المباشر
# ---------------------------------------------------------
active_streams = {}

def init_driver():
    display = Display(visible=0, size=(1280, 720))
    display.start()
    
    chrome_options = Options()
    chrome_options.add_argument('--no-sandbox')
    chrome_options.add_argument('--disable-dev-shm-usage')
    chrome_options.add_argument('--disable-gpu')
    chrome_options.add_argument('--disable-blink-features=AutomationControlled')
    chrome_options.add_argument('--incognito')
    
    driver = webdriver.Chrome(options=chrome_options)
    driver.set_window_size(1280, 720) 
    driver.implicitly_wait(3)
    return driver, display

def stop_stream(chat_id):
    if chat_id in active_streams:
        active_streams[chat_id]['streaming'] = False
        try:
            active_streams[chat_id]['driver'].quit()
        except:
            pass
        try:
            active_streams[chat_id]['display'].stop()
        except:
            pass
        del active_streams[chat_id]

def stream_screenshots(chat_id, url):
    msg = bot.send_message(chat_id, "⚙️ جاري تهيئة المتصفح وفتح الرابط... يرجى الانتظار.")
    
    try:
        driver, display = init_driver()
        active_streams[chat_id] = {
            'driver': driver, 'display': display, 'streaming': True, 
            'has_redirected_to_run': False, 'has_prepared_view': False, 
            'white_screen_attempts': 0
        }
        
        driver.get(url)
        time.sleep(3) 
        
        screenshot = driver.get_screenshot_as_png()
        photo = io.BytesIO(screenshot)
        
        markup = InlineKeyboardMarkup()
        markup.add(InlineKeyboardButton("إيقاف البث 🛑", callback_data="stop_stream"))
        
        bot.delete_message(chat_id, msg.message_id)
        photo_msg = bot.send_photo(chat_id, photo, caption="🔴 بث مباشر للصفحة...", reply_markup=markup)
        
        while active_streams.get(chat_id, {}).get('streaming', False):
            time.sleep(3) 
            
            if not active_streams.get(chat_id, {}).get('streaming', False):
                break
                
            try:
                current_url = driver.current_url
                
                # 0. تخطي شاشة التأكيد
                if "accounts.google.com" in current_url:
                    try:
                        driver.execute_script("""
                            let btns = document.querySelectorAll('button');
                            for (let b of btns) {
                                if (b.innerText.includes('Continue') || b.innerText.includes('متابعة')) {
                                    b.click();
                                    break;
                                }
                            }
                        """)
                    except:
                        pass
                
                # 1. التوجيه إلى Cloud Run
                if not active_streams[chat_id].get('has_redirected_to_run') and "console.cloud.google.com/home/dashboard" in current_url and "project=" in current_url:
                    match = re.search(r'project=([^&]+)', current_url)
                    if match:
                        project_id = match.group(1)
                        bot.send_message(chat_id, f"✅ تم اكتشاف المشروع: `{project_id}`\n🔄 جاري التوجيه لصفحة Cloud Run...", parse_mode="Markdown")
                        
                        run_url = f"https://console.cloud.google.com/run/create?enableapi=true&project={project_id}"
                        driver.get(run_url)
                        active_streams[chat_id]['has_redirected_to_run'] = True
                        time.sleep(6) 
                        
                # 2. تجهيز الشاشة وعرض قسم Region للمستخدم (بدون استخراج)
                elif active_streams[chat_id].get('has_redirected_to_run') and not active_streams[chat_id].get('has_prepared_view') and "console.cloud.google.com/run/create" in current_url:
                    
                    form_ready = driver.execute_script("""
                        // الفحص 1: هل الشاشة بيضاء تماماً؟
                        if (document.body.innerText.trim().length < 50) return false;

                        // الفحص 2: هل زر قائمة السيرفرات (cfc-select) موجود ومرئي بوضوح على الشاشة؟
                        let selects = document.querySelectorAll('cfc-select');
                        for (let s of selects) {
                            let rect = s.getBoundingClientRect();
                            if (rect.width > 0 && rect.height > 0) return true;
                        }
                        return false;
                    """)
                    
                    if not form_ready:
                        active_streams[chat_id]['white_screen_attempts'] += 1
                        if active_streams[chat_id]['white_screen_attempts'] == 1:
                            bot.send_message(chat_id, "⏳ جاري انتظار تحميل الصفحة وتخطي الشاشة البيضاء...")
                        if active_streams[chat_id]['white_screen_attempts'] >= 4:
                            bot.send_message(chat_id, "⚠️ الشاشة مُعلقة! جاري عمل Refresh إجباري لإنعاشها...")
                            driver.refresh()
                            active_streams[chat_id]['white_screen_attempts'] = 0
                            time.sleep(6)
                        continue
                    
                    bot.send_message(chat_id, "🔍 تم تحميل الواجهة بنجاح.\n🧹 جاري تنظيف الشاشة وتجهيز العرض لك...")
                    
                    try:
                        # تنظيف الشاشة من النوافذ المنبثقة
                        driver.execute_script("""
                            document.querySelectorAll('button[aria-label="Close"], button[aria-label="Close tutorial"], .cfc-coachmark-close, .close-button').forEach(btn => btn.click());
                            document.querySelectorAll('cfc-coachmark, cfc-tooltip, mat-tooltip-component, .cfc-coachmark-container, [role="dialog"], .guided-tour, cfc-panel').forEach(el => el.remove());
                        """)
                        time.sleep(2)

                        bot.send_message(chat_id, "👀 جاري تمرير الشاشة (Scroll) إلى قسم Region...")

                        # التمرير برفق حتى يكون قسم Region في منتصف الشاشة
                        driver.execute_script("""
                            let targetElement = null;
                            let labels = document.querySelectorAll('label, .cfc-form-field-label-text');
                            
                            for (let l of labels) {
                                if (l.innerText && l.innerText.toLowerCase().includes('region')) {
                                    targetElement = l;
                                    break;
                                }
                            }
                            
                            if (!targetElement) {
                                let selects = document.querySelectorAll('cfc-select');
                                if (selects.length > 0) {
                                    targetElement = selects[0];
                                }
                            }
                            
                            if (targetElement) {
                                targetElement.scrollIntoView({block: 'center', behavior: 'smooth'});
                            } else {
                                // إذا لم يجده، ينزل لمنتصف الصفحة تقريباً
                                window.scrollTo(0, document.body.scrollHeight / 2.5);
                            }
                        """)
                        
                        active_streams[chat_id]['has_prepared_view'] = True
                        bot.send_message(chat_id, "✅ الواجهة جاهزة الآن ومُركزة على Region! يمكنك مراقبة البث المباشر.")
                            
                        time.sleep(2) 
                    except Exception as script_err:
                        error_snippet = str(script_err)[:200]
                        bot.send_message(chat_id, f"⚠️ حدث خطأ بسيط أثناء التمرير:\n`{error_snippet}`", parse_mode="Markdown")
                        active_streams[chat_id]['has_prepared_view'] = True
            except Exception as e:
                pass
            # -------------------------------------------------------------

            try:
                new_screenshot = driver.get_screenshot_as_png()
                new_photo = io.BytesIO(new_screenshot)
                
                bot.edit_message_media(
                    chat_id=chat_id,
                    message_id=photo_msg.message_id,
                    media=InputMediaPhoto(new_photo, caption="🔴 بث مباشر للصفحة...\n(يتم التحديث كل 3 ثوانٍ)"),
                    reply_markup=markup
                )
            except Exception as e:
                error_msg = str(e).lower()
                if "message is not modified" in error_msg:
                    continue
                elif "too many requests" in error_msg:
                    time.sleep(4)
                    
    except Exception as e:
        bot.send_message(chat_id, f"❌ حدث خطأ أثناء فتح الرابط:\n{str(e)}")
    finally:
        stop_stream(chat_id)

@bot.message_handler(commands=['start'])
def send_welcome(message):
    bot.reply_to(message, "مرحباً بك! 👋\n\nأرسل لي أي رابط وسأقوم بفتحه وإرسال بث مباشر لصورته.")

@bot.message_handler(func=lambda message: message.text and message.text.startswith('http'))
def handle_url(message):
    chat_id = message.chat.id
    url = message.text
    
    if chat_id in active_streams:
        bot.reply_to(message, "⚠️ لديك بث يعمل حالياً. الرجاء إيقافه أولاً.")
        return
        
    threading.Thread(target=stream_screenshots, args=(chat_id, url), daemon=True).start()

@bot.callback_query_handler(func=lambda call: call.data == "stop_stream")
def callback_stop(call):
    chat_id = call.message.chat.id
    if chat_id in active_streams:
        stop_stream(chat_id)
        bot.answer_callback_query(call.id, "تم إيقاف البث بنجاح.")
        bot.edit_message_caption("⚫️ تم إيقاف البث.", chat_id=chat_id, message_id=call.message.message_id, reply_markup=None)
    else:
        bot.answer_callback_query(call.id, "البث متوقف بالفعل.")

if __name__ == '__main__':
    print("البوت يعمل الآن... يتم الاستماع للرسائل.")
    bot.infinity_polling()
