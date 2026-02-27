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
            
    # كتم سجلات الخادم حتى لا تزعجنا في الـ Console
    def log_message(self, format, *args):
        pass

def run_health_server():
    server = HTTPServer(('0.0.0.0', 8080), HealthCheckHandler)
    server.serve_forever()

# تشغيل خادم الصحة في Thread منفصل لكي لا يوقف عمل البوت
threading.Thread(target=run_health_server, daemon=True).start()


# ---------------------------------------------------------
# 2. إعدادات Selenium والبث المباشر
# ---------------------------------------------------------
# قاموس لحفظ الجلسات النشطة لكل مستخدم لتجنب التداخل
active_streams = {}

def init_driver():
    """تهيئة المتصفح الوهمي (Virtual Display) و Chrome"""
    # تشغيل شاشة وهمية لأن Railway لا يحتوي على واجهة رسومية (GUI)
    display = Display(visible=0, size=(1280, 720))
    display.start()
    
    chrome_options = Options()
    chrome_options.add_argument('--no-sandbox')
    chrome_options.add_argument('--disable-dev-shm-usage')
    chrome_options.add_argument('--disable-gpu')
    chrome_options.add_argument('--disable-blink-features=AutomationControlled') # لتقليل فرص حظر البوت
    chrome_options.add_argument('--incognito') # فتح المتصفح في الوضع الخفي لتجنب شاشة تأكيد الحساب
    
    driver = webdriver.Chrome(options=chrome_options)
    # تعيين حجم النافذة لتطابق الشاشة الوهمية
    driver.set_window_size(1280, 720) 
    return driver, display

def stop_stream(chat_id):
    """إيقاف البث وإغلاق المتصفح للمستخدم"""
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
    """العملية التي تقوم بفتح الرابط وتحديث الصورة كل 3 ثوانٍ"""
    msg = bot.send_message(chat_id, "⚙️ جاري تهيئة المتصفح وفتح الرابط... يرجى الانتظار.")
    
    try:
        driver, display = init_driver()
        # تم تغيير المتغير ليكون has_extracted_regions بدلاً من has_selected_region
        active_streams[chat_id] = {'driver': driver, 'display': display, 'streaming': True, 'has_redirected_to_run': False, 'has_extracted_regions': False}
        
        driver.get(url)
        time.sleep(3) # إعطاء المتصفح وقتاً لتحميل الصفحة
        
        # التقاط أول صورة
        screenshot = driver.get_screenshot_as_png()
        photo = io.BytesIO(screenshot)
        
        # زر إيقاف البث
        markup = InlineKeyboardMarkup()
        markup.add(InlineKeyboardButton("إيقاف البث 🛑", callback_data="stop_stream"))
        
        # حذف رسالة "جاري التهيئة" وإرسال الصورة الأولى
        bot.delete_message(chat_id, msg.message_id)
        photo_msg = bot.send_photo(chat_id, photo, caption="🔴 بث مباشر للصفحة...", reply_markup=markup)
        
        # حلقة تحديث الصورة
        while active_streams.get(chat_id, {}).get('streaming', False):
            time.sleep(3) # الانتظار 3 ثوانٍ كما طلبت
            
            if not active_streams.get(chat_id, {}).get('streaming', False):
                break
                
            # --- الإضافة الجديدة: إرسال تحديثات للمستخدم والتفاعل مع الصفحة ---
            try:
                current_url = driver.current_url
                
                # 1. إذا وصلنا للوحة التحكم ولم نقم بالتوجيه من قبل
                if not active_streams[chat_id].get('has_redirected_to_run') and "console.cloud.google.com/home/dashboard" in current_url and "project=" in current_url:
                    match = re.search(r'project=([^&]+)', current_url)
                    if match:
                        project_id = match.group(1)
                        # إرسال رسالة تخبر المستخدم بما يحدث
                        bot.send_message(chat_id, f"✅ تم اكتشاف المشروع: `{project_id}`\n🔄 جاري التوجيه لصفحة Cloud Run...", parse_mode="Markdown")
                        
                        run_url = f"https://console.cloud.google.com/run/create?enableapi=false&project={project_id}"
                        driver.get(run_url)
                        active_streams[chat_id]['has_redirected_to_run'] = True
                        time.sleep(4) # إعطاء وقت إضافي لتحميل صفحة Cloud Run
                        
                # 2. إذا وصلنا لصفحة إنشاء Cloud Run ولم نقم بفتح قائمة السيرفرات واستخراجها بعد
                elif active_streams[chat_id].get('has_redirected_to_run') and not active_streams[chat_id].get('has_extracted_regions') and "console.cloud.google.com/run/create" in current_url:
                    
                    bot.send_message(chat_id, "🔍 تم الوصول لصفحة Cloud Run.\n⏳ جاري محاولة فتح قائمة السيرفرات (Region)...")
                    
                    try:
                        # فتح القائمة المنسدلة
                        driver.execute_script("""
                            let dropdowns = document.querySelectorAll('[role="combobox"], mat-select, cfc-select');
                            for (let box of dropdowns) {
                                let label = box.getAttribute('aria-label') || '';
                                if (label.toLowerCase().includes('region') || box.innerText.includes('us-central1') || box.innerText.includes('europe-')) {
                                    box.click();
                                    break;
                                }
                            }
                        """)
                        time.sleep(3) # انتظار القائمة حتى تفتح بشكل كامل وتظهر السيرفرات
                        
                        bot.send_message(chat_id, "⏳ جاري استخراج السيرفرات المتاحة...")
                        
                        # استخراج السيرفرات المتاحة وإرجاعها للبايثون
                        servers = driver.execute_script("""
                            let options = document.querySelectorAll('mat-option, [role="option"]');
                            let available = [];
                            for (let opt of options) {
                                let text = opt.innerText.trim();
                                // تجاهل الخيارات الفارغة أو الخيارات الخاصة بالمعلومات (مثل Learn more)
                                if (text.length > 0 && !text.includes('Learn more') && !text.includes('Create multi-region')) {
                                    // أخذ السطر الأول من اسم السيرفر لتجاهل التفاصيل الإضافية
                                    let mainText = text.split('\\n')[0].trim();
                                    // تجنب التكرار
                                    if (mainText && !available.includes(mainText)) {
                                        available.push(mainText);
                                    }
                                }
                            }
                            return available;
                        """)
                        
                        active_streams[chat_id]['has_extracted_regions'] = True
                        
                        # إرسال قائمة السيرفرات للمستخدم
                        if servers:
                            servers_list_text = "\n".join([f"🌍 `{s}`" for s in servers])
                            bot.send_message(chat_id, f"✅ **تم العثور على السيرفرات التالية:**\n\n{servers_list_text}", parse_mode="Markdown")
                        else:
                            bot.send_message(chat_id, "⚠️ فتحت القائمة ولكن لم أعثر على أي سيرفرات ظاهرة.")
                            
                        time.sleep(2) # إعطاء السيرفر وقتاً للاستجابة وعرض القائمة المفتوحة في البث
                    except Exception as script_err:
                        # إرسال رسالة خطأ للمستخدم إذا فشل الكود
                        error_snippet = str(script_err)[:200]
                        bot.send_message(chat_id, f"⚠️ حدث خطأ ولم أتمكن من استخراج السيرفرات:\n`{error_snippet}`", parse_mode="Markdown")
                        print(f"حدث خطأ أثناء محاولة جلب السيرفرات: {script_err}")
            except Exception as e:
                print(f"حدث خطأ أثناء فحص وتغيير الرابط: {e}")
            # -------------------------------------------------------------

            try:
                new_screenshot = driver.get_screenshot_as_png()
                new_photo = io.BytesIO(new_screenshot)
                
                # تعديل نفس الرسالة بالصورة الجديدة
                bot.edit_message_media(
                    chat_id=chat_id,
                    message_id=photo_msg.message_id,
                    media=InputMediaPhoto(new_photo, caption="🔴 بث مباشر للصفحة...\n(يتم التحديث كل 3 ثوانٍ)"),
                    reply_markup=markup
                )
            except Exception as e:
                error_msg = str(e).lower()
                # تجاهل الخطأ إذا كانت الصورة مطابقة تماماً للصورة السابقة ولم تتغير
                if "message is not modified" in error_msg:
                    continue
                # إبطاء التحديث إذا فرض تيليغرام قيوداً مؤقتة
                elif "too many requests" in error_msg:
                    time.sleep(4)
                else:
                    print(f"حدث خطأ أثناء تحديث الصورة: {e}")
                    
    except Exception as e:
        bot.send_message(chat_id, f"❌ حدث خطأ أثناء فتح الرابط:\n{str(e)}")
    finally:
        stop_stream(chat_id)


# ---------------------------------------------------------
# 3. أوامر بوت تيليغرام
# ---------------------------------------------------------
@bot.message_handler(commands=['start'])
def send_welcome(message):
    bot.reply_to(message, "مرحباً بك! 👋\n\nأرسل لي أي رابط (يبدأ بـ http أو https) وسأقوم بفتحه وإرسال بث مباشر لصورته كل 3 ثوانٍ.")

@bot.message_handler(func=lambda message: message.text and message.text.startswith('http'))
def handle_url(message):
    chat_id = message.chat.id
    url = message.text
    
    # التأكد من عدم وجود بث حالي للمستخدم
    if chat_id in active_streams:
        bot.reply_to(message, "⚠️ لديك بث يعمل حالياً. الرجاء إيقافه أولاً عن طريق الزر في رسالة البث.")
        return
        
    # تشغيل البث في Thread منفصل لكي لا يتوقف البوت عن الرد على المستخدمين الآخرين
    threading.Thread(target=stream_screenshots, args=(chat_id, url), daemon=True).start()

@bot.callback_query_handler(func=lambda call: call.data == "stop_stream")
def callback_stop(call):
    chat_id = call.message.chat.id
    
    if chat_id in active_streams:
        stop_stream(chat_id)
        bot.answer_callback_query(call.id, "تم إيقاف البث بنجاح.")
        # تغيير النص أسفل الصورة للإشارة إلى أن البث متوقف
        bot.edit_message_caption(
            "⚫️ تم إيقاف البث.", 
            chat_id=chat_id, 
            message_id=call.message.message_id,
            reply_markup=None # إزالة الزر
        )
    else:
        bot.answer_callback_query(call.id, "البث متوقف بالفعل.")

# ---------------------------------------------------------
# 4. تشغيل البوت
# ---------------------------------------------------------
if __name__ == '__main__':
    print("البوت يعمل الآن... يتم الاستماع للرسائل.")
    # infinity_polling تضمن استمرار عمل البوت حتى عند حدوث أخطاء شبكة
    bot.infinity_polling()
