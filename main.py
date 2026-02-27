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
                        
                # 2. الفحص البصري الدقيق (لمنع خدعة الشاشة البيضاء)
                elif active_streams[chat_id].get('has_redirected_to_run') and not active_streams[chat_id].get('has_extracted_regions') and "console.cloud.google.com/run/create" in current_url:
                    
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
                            bot.send_message(chat_id, "⏳ جاري انتظار بناء الواجهة (تخطي الانهيار الوهمي)...")
                        if active_streams[chat_id]['white_screen_attempts'] >= 4:
                            bot.send_message(chat_id, "⚠️ الشاشة بيضاء ومُعلقة! جاري عمل Refresh إجباري لإنعاشها...")
                            driver.refresh()
                            active_streams[chat_id]['white_screen_attempts'] = 0
                            time.sleep(6)
                        continue
                    
                    bot.send_message(chat_id, "🔍 تم تحميل الواجهة ورسمها بنجاح.\n🧹 جاري تنظيف الشاشة من النوافذ...")
                    
                    try:
                        driver.execute_script("""
                            document.querySelectorAll('button[aria-label="Close"], button[aria-label="Close tutorial"], .cfc-coachmark-close, .close-button').forEach(btn => btn.click());
                            document.querySelectorAll('cfc-coachmark, cfc-tooltip, mat-tooltip-component, .cfc-coachmark-container, [role="dialog"], .guided-tour, cfc-panel').forEach(el => el.remove());
                        """)
                        time.sleep(2)

                        # =================================================================
                        # الإضافة الخارقة: استخدام سكريبت الفحص الخاص بك (Diagnostic Script)
                        # =================================================================
                        bot.send_message(chat_id, "🧬 جاري حقن سكريبت الفحص (Diagnostic) الخاص بك لاستخراج الزر...")
                        
                        diagnostic_js = """
                            let regionElements = [];
                            let allElements = document.querySelectorAll('mat-select, cfc-select, [role="combobox"], button, input');
                            
                            allElements.forEach(el => {
                                let text = (el.innerText || '').toLowerCase();
                                let label = (el.getAttribute('aria-label') || '').toLowerCase();
                                let id = (el.id || '').toLowerCase();
                                
                                if (label.includes('region') || id.includes('region') || text.includes('us-central') || text.includes('europe-')) {
                                    // استبعاد شريط البحث لعدم الانخداع به
                                    if (!id.includes('search') && !label.includes('search')) {
                                        regionElements.push({
                                            tag: el.tagName.toLowerCase(),
                                            id: el.id
                                        });
                                    }
                                }
                            });

                            let labels = Array.from(document.querySelectorAll('label, .cfc-form-field-label-text')).filter(l => (l.innerText || '').toLowerCase().includes('region'));
                            let labelData = labels.map(l => ({
                                htmlFor: l.getAttribute('for')
                            }));

                            return JSON.stringify({ dropdowns: regionElements, labels: labelData });
                        """
                        
                        diag_result = driver.execute_script(diagnostic_js)
                        diag_data = json.loads(diag_result)
                        
                        target_id = None
                        
                        # المحاولة 1: استخراج الـ ID من خاصية for الخاصة بالنص Region
                        if diag_data.get('labels'):
                            for l in diag_data['labels']:
                                if l.get('htmlFor'):
                                    target_id = l['htmlFor']
                                    break
                                    
                        # المحاولة 2: استخراج الـ ID من قائمة العناصر المشتبه بها
                        if not target_id and diag_data.get('dropdowns'):
                            for d in diag_data['dropdowns']:
                                if d.get('id'):
                                    target_id = d['id']
                                    break
                        
                        clicked = False
                        
                        if target_id:
                            bot.send_message(chat_id, f"🎯 **نتيجة الفحص:** تم اكتشاف المعرف السري للزر:\n`{target_id}`\n\n⚡ جاري توجيه النقرة مباشرة إليه...")
                            
                            click_js = f"""
                                let targetBox = document.getElementById('{target_id}');
                                if (targetBox) {{
                                    targetBox.scrollIntoView({{block: 'center', behavior: 'instant'}});
                                    let evtDown = new MouseEvent('mousedown', {{ bubbles: true, cancelable: true, view: window }});
                                    let evtUp = new MouseEvent('mouseup', {{ bubbles: true, cancelable: true, view: window }});
                                    let evtClick = new MouseEvent('click', {{ bubbles: true, cancelable: true, view: window }});
                                    targetBox.dispatchEvent(evtDown);
                                    targetBox.dispatchEvent(evtUp);
                                    targetBox.dispatchEvent(evtClick);
                                    targetBox.click(); // نقرة تأكيدية
                                    return true;
                                }}
                                return false;
                            """
                            clicked = driver.execute_script(click_js)
                        else:
                            bot.send_message(chat_id, "⚠️ تم تشغيل السكريبت ولكن لم أتمكن من استخراج ID واضح. سأحاول النقر على أول عنصر متاح.")
                            
                            fallback_click_js = """
                                let selects = document.querySelectorAll('cfc-select');
                                if (selects.length > 0) {
                                    let targetBox = selects[0];
                                    targetBox.scrollIntoView({block: 'center', behavior: 'instant'});
                                    let evtDown = new MouseEvent('mousedown', { bubbles: true, cancelable: true, view: window });
                                    let evtUp = new MouseEvent('mouseup', { bubbles: true, cancelable: true, view: window });
                                    let evtClick = new MouseEvent('click', { bubbles: true, cancelable: true, view: window });
                                    targetBox.dispatchEvent(evtDown);
                                    targetBox.dispatchEvent(evtUp);
                                    targetBox.dispatchEvent(evtClick);
                                    targetBox.click();
                                    return true;
                                }
                                return false;
                            """
                            clicked = driver.execute_script(fallback_click_js)

                        if not clicked:
                            bot.send_message(chat_id, "⚠️ لم ينجح النقر على القائمة. سأحاول مجدداً في التحديث القادم...")
                            continue

                        bot.send_message(chat_id, "⏳ تم النقر! جاري استخراج السيرفرات...")
                        
                        servers = []
                        for _ in range(4):
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
