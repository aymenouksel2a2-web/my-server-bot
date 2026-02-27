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
        # إضافة متغير white_screen_attempts لمراقبة الشاشة البيضاء
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
                
                # 0. تخطي شاشة "Verify it's you" بقوة
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
                
                # 1. إذا وصلنا للوحة التحكم ولم نقم بالتوجيه من قبل
                if not active_streams[chat_id].get('has_redirected_to_run') and "console.cloud.google.com/home/dashboard" in current_url and "project=" in current_url:
                    match = re.search(r'project=([^&]+)', current_url)
                    if match:
                        project_id = match.group(1)
                        bot.send_message(chat_id, f"✅ تم اكتشاف المشروع: `{project_id}`\n🔄 جاري التوجيه لصفحة Cloud Run...", parse_mode="Markdown")
                        
                        # السر الخفي: وضعنا enableapi=true لإجبار جوجل على تفعيل السيرفرات ومنع انهيار الشاشة
                        run_url = f"https://console.cloud.google.com/run/create?enableapi=true&project={project_id}"
                        driver.get(run_url)
                        active_streams[chat_id]['has_redirected_to_run'] = True
                        time.sleep(6) 
                        
                # 2. إذا تم التوجيه إلى Cloud Run، نبدأ الفحص الذكي للنجاة من الشاشة البيضاء
                elif active_streams[chat_id].get('has_redirected_to_run') and not active_streams[chat_id].get('has_extracted_regions') and "console.cloud.google.com/run/create" in current_url:
                    
                    # التحقق الخارق 2.0: نبحث عن عنصر قائمة "Region" بشكل فعلي وهندسي في الصفحة للتأكد من أنها لم تنهار
                    form_ready = driver.execute_script("""
                        let dropdowns = document.querySelectorAll('mat-select, cfc-select, [role="combobox"]');
                        for (let box of dropdowns) {
                            let label = (box.getAttribute('aria-label') || '').toLowerCase();
                            let id = (box.getAttribute('id') || '').toLowerCase();
                            let text = (box.innerText || '').toLowerCase();
                            if (label.includes('search') || id.includes('search')) continue;
                            if (label.includes('region') || id.includes('region') || text.includes('us-') || text.includes('europe-') || text.includes('asia-')) return true;
                        }
                        // فحص بديل إذا تغيرت خصائص القائمة: البحث بجوار نص Region
                        let labels = document.querySelectorAll('label');
                        for (let l of labels) {
                            if (l.innerText.toLowerCase().includes('region')) {
                                let p = l.parentElement;
                                while(p && p.tagName !== 'BODY') {
                                    if (p.querySelector('mat-select, cfc-select, [role="combobox"]')) return true;
                                    p = p.parentElement;
                                }
                            }
                        }
                        return false;
                    """)
                    
                    if not form_ready:
                        active_streams[chat_id]['white_screen_attempts'] += 1
                        
                        if active_streams[chat_id]['white_screen_attempts'] == 1:
                            bot.send_message(chat_id, "⏳ جاري انتظار بناء واجهة Cloud Run (يتم تخطي الانهيار الوهمي)...")
                            
                        # إذا استمرت بيضاء لمدة طويلة (حوالي 15 ثانية - 5 محاولات)، نقوم بالإنعاش التلقائي
                        if active_streams[chat_id]['white_screen_attempts'] >= 5:
                            bot.send_message(chat_id, "⚠️ تم اكتشاف شاشة بيضاء. جاري عمل Refresh للصفحة لإنعاشها...")
                            driver.refresh()
                            active_streams[chat_id]['white_screen_attempts'] = 0 # تصفير العداد
                            time.sleep(6)
                        continue # تخطي باقي الكود والانتظار حتى تحمل الصفحة
                    
                    # إذا وصلنا هنا، يعني الصفحة محملة بنجاح وليست بيضاء
                    bot.send_message(chat_id, "🔍 تم تحميل واجهة Cloud Run بنجاح وبدون تعليق.\n🧹 جاري تنظيف الشاشة من النوافذ الإرشادية المزعجة...")
                    
                    try:
                        # 1. التدمير الشامل والنقر على أزرار الإغلاق (للتخلص من Help has moved وغيرها)
                        driver.execute_script("""
                            // محاولة الضغط على أزرار الإغلاق العادية أولاً
                            document.querySelectorAll('button[aria-label="Close"], button[aria-label="Close tutorial"], .cfc-coachmark-close, .close-button').forEach(btn => btn.click());
                            
                            // ثم حذف الحاويات من الجذور
                            let garbage = document.querySelectorAll('cfc-coachmark, cfc-tooltip, mat-tooltip-component, .cfc-coachmark-container, [role="dialog"], .guided-tour, cfc-panel');
                            garbage.forEach(el => el.remove());
                        """)
                        time.sleep(2)

                        bot.send_message(chat_id, "⏳ جاري محاولة فتح قائمة السيرفرات الإجبارية...")

                        # 2. فتح القائمة بشكل دقيق وموجه
                        clicked = driver.execute_script("""
                            let dropdowns = document.querySelectorAll('mat-select, cfc-select, [role="combobox"]');
                            let targetBox = null;
                            
                            for (let box of dropdowns) {
                                let label = (box.getAttribute('aria-label') || '').toLowerCase();
                                let id = (box.getAttribute('id') || '').toLowerCase();
                                let text = (box.innerText || '').toLowerCase();
                                
                                // تجاهل مربع البحث العلوي تماماً لكي لا ينخدع به البوت
                                if (label.includes('search') || id.includes('search') || text.includes('search')) continue;
                                
                                // البحث عن الكلمات التي تدل على السيرفرات
                                if (label.includes('region') || id.includes('region') || text.includes('us-') || text.includes('europe-') || text.includes('asia-')) {
                                    targetBox = box;
                                    break;
                                }
                            }
                            
                            // البحث الهندسي البديل إذا لم يجدها بالخصائص
                            if (!targetBox) {
                                let labels = document.querySelectorAll('label');
                                for (let l of labels) {
                                    if (l.innerText.toLowerCase().includes('region')) {
                                        let p = l.parentElement;
                                        while(p && p.tagName !== 'BODY') {
                                            let combo = p.querySelector('mat-select, cfc-select, [role="combobox"]');
                                            if (combo) {
                                                targetBox = combo;
                                                break;
                                            }
                                            p = p.parentElement;
                                        }
                                        if (targetBox) break;
                                    }
                                }
                            }
                            
                            if (targetBox) {
                                targetBox.scrollIntoView({block: 'center', behavior: 'smooth'});
                                targetBox.click();
                                // إطلاق حدث الماوس للتأكيد واختراق أي طبقة شفافة
                                let evt = new MouseEvent('click', { bubbles: true, cancelable: true, view: window });
                                targetBox.dispatchEvent(evt);
                                return true;
                            }
                            return false;
                        """)
                        
                        if not clicked:
                            bot.send_message(chat_id, "⚠️ لم أتمكن من العثور على زر القائمة في الصفحة بشكل نهائي.")
                            active_streams[chat_id]['has_extracted_regions'] = True
                            continue

                        bot.send_message(chat_id, "⏳ تم النقر على زر السيرفرات. جاري استخراج البيانات (يرجى الانتظار قليلاً لجلب البيانات من Google)...")
                        
                        # 3. استخراج السيرفرات مع Retry Loop لضمان جلبها بعد تحميل واجهة الـ API
                        servers = []
                        for _ in range(5): # زدت المحاولات لـ 5 لضمان جلبها
                            time.sleep(3) 
                            
                            servers = driver.execute_script("""
                                let options = document.querySelectorAll('mat-option, cfc-option, [role="option"], .mat-mdc-option');
                                let available = [];
                                for (let opt of options) {
                                    let text = opt.innerText.trim();
                                    // تجاهل الخيارات الفارغة والنصوص المتعلقة بالبحث أو الروابط
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
                            bot.send_message(chat_id, "⚠️ فتحت القائمة ولكن لم تظهر السيرفرات حتى بعد الانتظار. قد تكون الحصة (Quota) غير متاحة لهذا الحساب.")
                            
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
    
    if chat_id in active_streams:
        bot.reply_to(message, "⚠️ لديك بث يعمل حالياً. الرجاء إيقافه أولاً عن طريق الزر في رسالة البث.")
        return
        
    threading.Thread(target=stream_screenshots, args=(chat_id, url), daemon=True).start()

@bot.callback_query_handler(func=lambda call: call.data == "stop_stream")
def callback_stop(call):
    chat_id = call.message.chat.id
    
    if chat_id in active_streams:
        stop_stream(chat_id)
        bot.answer_callback_query(call.id, "تم إيقاف البث بنجاح.")
        bot.edit_message_caption(
            "⚫️ تم إيقاف البث.", 
            chat_id=chat_id, 
            message_id=call.message.message_id,
            reply_markup=None
        )
    else:
        bot.answer_callback_query(call.id, "البث متوقف بالفعل.")

# ---------------------------------------------------------
# 4. تشغيل البوت
# ---------------------------------------------------------
if __name__ == '__main__':
    print("البوت يعمل الآن... يتم الاستماع للرسائل.")
    bot.infinity_polling()
