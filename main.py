import os
import time
import threading
import io
import re
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
            'has_redirected_to_run': False, 'has_extracted_regions': False, 
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
                        
                # 2. الفحص الذكي للصفحة بناءً على التشخيص
                elif active_streams[chat_id].get('has_redirected_to_run') and not active_streams[chat_id].get('has_extracted_regions') and "console.cloud.google.com/run/create" in current_url:
                    
                    form_ready = driver.execute_script("""
                        let labels = document.querySelectorAll('label, .cfc-form-field-label-text');
                        for (let l of labels) {
                            if (l.innerText && l.innerText.toLowerCase().includes('region')) {
                                let targetId = l.getAttribute('for');
                                if (targetId && document.getElementById(targetId)) return true;
                            }
                        }
                        let text = document.body.innerText;
                        return text.includes('Container image URL');
                    """)
                    
                    if not form_ready:
                        active_streams[chat_id]['white_screen_attempts'] += 1
                        if active_streams[chat_id]['white_screen_attempts'] == 1:
                            bot.send_message(chat_id, "⏳ جاري انتظار بناء واجهة Cloud Run...")
                        if active_streams[chat_id]['white_screen_attempts'] >= 5:
                            bot.send_message(chat_id, "⚠️ تم اكتشاف شاشة بيضاء. جاري عمل Refresh للصفحة لإنعاشها...")
                            driver.refresh()
                            active_streams[chat_id]['white_screen_attempts'] = 0
                            time.sleep(6)
                        continue
                    
                    bot.send_message(chat_id, "🔍 تم تحميل الواجهة بنجاح.\n🧹 جاري تنظيف الشاشة من النوافذ الإرشادية...")
                    
                    try:
                        driver.execute_script("""
                            document.querySelectorAll('button[aria-label="Close"], button[aria-label="Close tutorial"], .cfc-coachmark-close, .close-button').forEach(btn => btn.click());
                            document.querySelectorAll('cfc-coachmark, cfc-tooltip, mat-tooltip-component, .cfc-coachmark-container, [role="dialog"], .guided-tour, cfc-panel').forEach(el => el.remove());
                        """)
                        time.sleep(2)

                        bot.send_message(chat_id, "⏳ جاري محاولة فتح القائمة الإجبارية (بالاعتماد على الـ ID الدقيق)...")

                        # القناص: تحديد العنصر بناءً على بيانات التشخيص
                        clicked = driver.execute_script("""
                            let targetBox = null;
                            
                            // 1. البحث باستخدام الـ Label الذي وجدناه في التشخيص
                            let labels = document.querySelectorAll('label, .cfc-form-field-label-text');
                            for (let l of labels) {
                                if (l.innerText && l.innerText.toLowerCase().includes('region')) {
                                    let targetId = l.getAttribute('for');
                                    if (targetId) {
                                        targetBox = document.getElementById(targetId);
                                        if (targetBox) break;
                                    }
                                }
                            }
                            
                            // 2. الفحص البديل في حال فشل الأول: استهداف علامة التشخيص (cfc-select)
                            if (!targetBox) {
                                let selects = document.querySelectorAll('cfc-select');
                                if (selects.length > 0) {
                                    targetBox = selects[0]; // نأخذ أول قائمة cfc-select لأنها التي ظهرت في التشخيص
                                }
                            }
                            
                            if (targetBox) {
                                targetBox.scrollIntoView({block: 'center', behavior: 'smooth'});
                                targetBox.click();
                                let evt = new MouseEvent('click', { bubbles: true, cancelable: true, view: window });
                                targetBox.dispatchEvent(evt);
                                return true;
                            }
                            return false;
                        """)
                        
                        if not clicked:
                            bot.send_message(chat_id, "⚠️ لم أتمكن من العثور على القائمة باستخدام الهندسة العكسية.")
                            active_streams[chat_id]['has_extracted_regions'] = True
                            continue

                        bot.send_message(chat_id, "⏳ تم النقر على زر السيرفرات. جاري استخراج البيانات...")
                        
                        servers = []
                        for _ in range(5):
                            time.sleep(3) 
                            servers = driver.execute_script("""
                                let options = document.querySelectorAll('mat-option, cfc-option, [role="option"], .mat-mdc-option');
                                let available = [];
                                for (let opt of options) {
                                    let text = opt.innerText.trim();
                                    if (text.length > 0 && !text.includes('Learn more') && !text.includes('Create multi-region') && text.includes('-') && !text.toLowerCase().includes('search')) {
                                        let mainText = text.split('\\n')[0].trim();
                                        if (mainText && !available.includes(mainText)) {
                                            available.push(mainText);
                                        }
                                    }
                                }
                                return available;
                            """)
                            if servers and len(servers) > 0:
                                break
                        
                        active_streams[chat_id]['has_extracted_regions'] = True
                        
                        if servers and len(servers) > 0:
                            servers_list_text = "\n".join([f"🌍 `{s}`" for s in servers])
                            bot.send_message(chat_id, f"✅ **تم العثور على السيرفرات التالية:**\n\n{servers_list_text}", parse_mode="Markdown")
                        else:
                            bot.send_message(chat_id, "⚠️ فتحت القائمة ولكن لم تظهر السيرفرات. الحصة (Quota) غير متاحة.")
                            
                        time.sleep(2) 
                    except Exception as script_err:
                        error_snippet = str(script_err)[:200]
                        bot.send_message(chat_id, f"⚠️ حدث خطأ داخلي:\n`{error_snippet}`", parse_mode="Markdown")
                        active_streams[chat_id]['has_extracted_regions'] = True
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
