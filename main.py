import telebot
import os
import time
import traceback
import urllib.parse
import re
from io import BytesIO
import undetected_chromedriver as uc
from pyvirtualdisplay import Display
from telebot.types import InputMediaPhoto
from PIL import Image

from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC

import threading
from http.server import BaseHTTPRequestHandler, HTTPServer

# --- الخادم الوهمي لتخطي فحص Render السحابي ---
class DummyHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.send_header('Content-type', 'text/plain')
        self.end_headers()
        self.wfile.write(b"Bot is Healthy, Fast, and Running on Render!")
    
    def log_message(self, format, *args):
        pass

def run_dummy_server():
    # Render يعطينا البورت تلقائياً عبر متغير النظام PORT
    port = int(os.environ.get("PORT", 10000))
    server = HTTPServer(('0.0.0.0', port), DummyHandler)
    server.serve_forever()
# -------------------------------------------------

BOT_TOKEN = os.environ.get('BOT_TOKEN')
bot = telebot.TeleBot(BOT_TOKEN)

# --- دالة التقاط الصور التيربو ---
def get_light_jpg_screenshot(driver):
    png_data = driver.get_screenshot_as_png()
    img = Image.open(BytesIO(png_data))
    img = img.convert('RGB')
    img.thumbnail((800, 600)) 
    output = BytesIO()
    img.save(output, format='JPEG', quality=30, optimize=True)
    output.seek(0)
    output.name = 'screen.jpg'
    return output

@bot.message_handler(commands=['start'])
def send_welcome(message):
    bot.reply_to(message, "أهلاً بك! البوت يعمل الآن بقوة على سيرفرات Render ☁️⚡. أرسل /live للبدء 🚀")

@bot.message_handler(commands=['live'])
def ask_for_sso_url(message):
    msg = bot.reply_to(message, "🔗 الرجاء إرسال **رابط تسجيل الدخول** الطويل:")
    bot.register_next_step_handler(msg, start_livestream)

def start_livestream(message):
    sso_url = message.text
    if not sso_url.startswith("http"):
        bot.reply_to(message, "❌ الرابط غير صالح. أرسل /live للمحاولة مجدداً.")
        return

    # --- استخراج بيانات المشروع ---
    try:
        parsed_url = urllib.parse.urlparse(sso_url)
        query_params = urllib.parse.parse_qs(parsed_url.query)
        project_id = None
        walkthrough_id = ""
        
        if 'relay' in query_params:
            relay_url = query_params['relay'][0]
            relay_parsed = urllib.parse.urlparse(relay_url)
            relay_params = urllib.parse.parse_qs(relay_parsed.query)
            if 'project' in relay_params:
                project_id = relay_params['project'][0]
            if 'walkthrough_id' in relay_params:
                walkthrough_id = relay_params['walkthrough_id'][0]
        
        if not project_id:
            match = re.search(r'project(?:%3D|=)(qwiklabs-gcp-[a-zA-Z0-9-]+)', sso_url)
            if match:
                project_id = match.group(1)
                
        if not project_id:
            bot.reply_to(message, "❌ لم أتمكن من العثور على اسم المشروع. تأكد من الرابط.")
            return
        
        shell_url = f"https://shell.cloud.google.com/?project={project_id}&show=terminal"
        if walkthrough_id:
            shell_url += f"&walkthrough_id={urllib.parse.quote(walkthrough_id, safe='')}"
            
        bot.send_message(message.chat.id, f"✅ تم اكتشاف المشروع: `{project_id}`\n🚀 سيتم الانتقال للـ Shell بسرعة!", parse_mode="Markdown")
        
    except Exception as e:
        bot.reply_to(message, f"❌ حدث خطأ أثناء تحليل الرابط:\n{e}")
        return

    msg = bot.reply_to(message, "⚡ [1/7] جاري تجهيز بيئة Render...")
    
    display = Display(visible=0, size=(1280, 720), color_depth=24)
    display.start()
    
    try:
        options = uc.ChromeOptions()
        options.page_load_strategy = 'eager'
        options.add_argument("--incognito")
        options.add_argument("--disable-site-isolation-trials")
        options.add_argument("--no-sandbox")
        options.add_argument("--disable-setuid-sandbox")
        options.add_argument("--disable-dev-shm-usage")
        options.add_argument("--disable-gpu")
        options.add_argument("--disable-software-rasterizer")
        options.add_argument("--window-size=1280,720")
        
        options.add_argument("--disable-extensions")
        options.add_argument("--mute-audio")
        options.add_argument("--disable-notifications")
        options.add_argument("--disable-popup-blocking")
        options.add_argument("--disable-default-apps")
        
        driver = uc.Chrome(
            options=options, 
            use_subprocess=True,
            driver_executable_path="/usr/bin/chromedriver",
            browser_executable_path="/usr/bin/chromium"
        )
        
        driver.set_window_size(1280, 720)
        driver.set_page_load_timeout(45) 
        
        bot.edit_message_text("⚡ [2/7] المحرك جاهز! بدء عملية الاختراق...", chat_id=message.chat.id, message_id=msg.message_id)
        
        live_msg = bot.send_photo(message.chat.id, get_light_jpg_screenshot(driver), caption="🔴 بث مباشر (التهيئة)...")
        
        try: driver.get(sso_url)
        except Exception: pass 
            
        time.sleep(2)
        
        bot.edit_message_text("⚡ [3/7] جاري تسجيل الدخول والقفز الفوري...", chat_id=message.chat.id, message_id=msg.message_id)
        
        try:
            bot.edit_message_media(chat_id=message.chat.id, message_id=live_msg.message_id, media=InputMediaPhoto(get_light_jpg_screenshot(driver), caption="🔴 بث مباشر (مرحلة الموافقة)..."))
        except: pass

        # --- قفزة النينجا ---
        try:
            understand_btn = WebDriverWait(driver, 5).until(EC.presence_of_element_located((By.ID, "confirm")))
            driver.execute_script("arguments[0].click();", understand_btn)
            driver.get(shell_url) 
        except Exception:
            driver.get(shell_url)

        bot.edit_message_text("⚡ [4/7] جاري تحميل واجهة Cloud Shell...", chat_id=message.chat.id, message_id=msg.message_id)
        bot.edit_message_text("⚡ [5/7] تخويل الصلاحيات (Authorize)...", chat_id=message.chat.id, message_id=msg.message_id)
        
        for _ in range(4): 
            time.sleep(3)
            try:
                bot.edit_message_media(chat_id=message.chat.id, message_id=live_msg.message_id, media=InputMediaPhoto(get_light_jpg_screenshot(driver), caption="🔴 بث مباشر (جاري تحميل الـ Cloud Shell)..."))
            except: pass

        try:
            js_auth_script = """
            var btns = document.querySelectorAll('button, span, div');
            for(var i=0; i<btns.length; i++){
                if(btns[i].innerText && btns[i].innerText.trim().toLowerCase() === 'authorize'){
                    btns[i].click();
                    return true;
                }
            }
            return false;
            """
            clicked_auth = driver.execute_script(js_auth_script)
            if not clicked_auth:
                auth_btn = WebDriverWait(driver, 10).until(
                    EC.element_to_be_clickable((By.XPATH, "//*[contains(translate(text(), 'AUTHORIZE', 'authorize'), 'authorize')] | //button[contains(., 'Authorize')]"))
                )
                auth_btn.click()
        except Exception:
            pass

        time.sleep(3)

        bot.edit_message_text("⚡ [6/7] تنظيف الشاشة للمحطة (Terminal)...", chat_id=message.chat.id, message_id=msg.message_id)
        try:
            js_close_editor = """
            var btns = document.querySelectorAll('button, a');
            for(var i=0; i<btns.length; i++){
                var title = btns[i].getAttribute('title') || btns[i].getAttribute('aria-label') || '';
                if(title.toLowerCase().includes('close editor') || title.toLowerCase().includes('toggle editor')){
                    btns[i].click();
                    return true;
                }
            }
            return false;
            """
            driver.execute_script(js_close_editor)
        except Exception:
            pass
            
        bot.delete_message(message.chat.id, msg.message_id)

        # --- حلقة البث المستقرة ---
        while True:
            time.sleep(3) 
            try:
                photo = get_light_jpg_screenshot(driver)
                bot.edit_message_media(
                    chat_id=message.chat.id,
                    message_id=live_msg.message_id,
                    media=InputMediaPhoto(photo, caption=f"🔴 بث مباشر ⚡: {project_id}")
                )
            except Exception as update_error:
                error_msg = str(update_error).lower()
                if "is not modified" in error_msg:
                    continue
                elif "too many requests" in error_msg or "flood" in error_msg:
                    print("⚠️ تيليغرام غاضب من السرعة، استراحة 5 ثوانٍ...")
                    time.sleep(5) 
                else:
                    print(f"⚠️ خطأ تحديث: {update_error}")
            
    except Exception as e:
        error_details = traceback.format_exc()
        bot.send_message(message.chat.id, f"❌ حدث خطأ داخلي:\n{e}\n\nالتفاصيل:\n{error_details[-800:]}")
    finally:
        if 'driver' in locals() and driver is not None:
            try: driver.quit()
            except: pass
        if 'display' in locals():
            try: display.stop()
            except: pass

print("جاري تشغيل خادم الويب الوهمي لـ Render...")
threading.Thread(target=run_dummy_server, daemon=True).start()

print("البوت يعمل بقوة على Render ⚡...")

while True:
    try:
        bot.polling(non_stop=True, timeout=60)
    except Exception as e:
        print(f"⚠️ انقطع الاتصال، جاري إعادة المحاولة... ({e})")
        time.sleep(5)
