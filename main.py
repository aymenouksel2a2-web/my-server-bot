import telebot
import os
import time
import threading
import io
import re
import random
import shutil
import gc
import subprocess
from datetime import datetime
from http.server import HTTPServer, BaseHTTPRequestHandler
from telebot.types import InputMediaPhoto, InlineKeyboardMarkup, InlineKeyboardButton

from selenium import webdriver
from selenium.webdriver.chrome.service import Service
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.common.by import By
from pyvirtualdisplay import Display

TOKEN = os.environ.get('BOT_TOKEN')
if not TOKEN:
    raise ValueError("BOT_TOKEN غير موجود!")

bot = telebot.TeleBot(TOKEN)
user_sessions = {}
sessions_lock = threading.Lock()


class HealthHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.send_header('Content-Type', 'text/html')
        self.end_headers()
        with sessions_lock:
            active = len(user_sessions)
        self.wfile.write(f"<h1>Bot Running</h1><p>Sessions: {active}</p>".encode())
    def log_message(self, *args):
        pass

def start_health_server():
    port = int(os.environ.get('PORT', 8080))
    print(f"🌐 Health Check: port {port}")
    HTTPServer(('0.0.0.0', port), HealthHandler).serve_forever()


display = None
try:
    display = Display(visible=0, size=(1024, 768), color_depth=16)
    display.start()
    print("✅ Xvfb يعمل (1024x768)")
except:
    try:
        display = Display(visible=0, size=(800, 600))
        display.start()
        print("✅ Xvfb يعمل (800x600)")
    except Exception as e:
        print(f"❌ Xvfb: {e}")


def find_path(names, extras=None):
    for n in names:
        p = shutil.which(n)
        if p:
            return p
    for p in (extras or []):
        if os.path.isfile(p):
            return p
    return None

def get_browser_version(path):
    try:
        r = subprocess.run([path, '--version'], capture_output=True, text=True, timeout=5)
        m = re.search(r'(\d+)', r.stdout)
        return m.group(1) if m else "120"
    except:
        return "120"


def patch_chromedriver(original_path):
    patched = '/tmp/chromedriver_patched'
    shutil.copy2(original_path, patched)
    os.chmod(patched, 0o755)
    with open(patched, 'r+b') as f:
        content = f.read()
        count = content.count(b'cdc_')
        if count > 0:
            f.seek(0)
            f.write(content.replace(b'cdc_', b'aaa_'))
            print(f"✅ chromedriver: {count} cdc_ removed")
    return patched


STEALTH_JS = '''
Object.defineProperty(navigator,'webdriver',{get:()=>undefined});
Object.defineProperty(navigator,'plugins',{
    get:function(){return[
        {name:'Chrome PDF Plugin',filename:'internal-pdf-viewer',length:1},
        {name:'Chrome PDF Viewer',filename:'mhjfbmdgcfjbbpaeojofohoefgiehjai',length:1},
        {name:'Native Client',filename:'internal-nacl-plugin',length:2}
    ];}
});
Object.defineProperty(navigator,'languages',{get:()=>['en-US','en']});
Object.defineProperty(navigator,'platform',{get:()=>'Win32'});
Object.defineProperty(navigator,'vendor',{get:()=>'Google Inc.'});
Object.defineProperty(navigator,'hardwareConcurrency',{get:()=>4});
Object.defineProperty(navigator,'deviceMemory',{get:()=>8});
Object.defineProperty(navigator,'maxTouchPoints',{get:()=>0});
window.chrome=window.chrome||{};
window.chrome.runtime={onMessage:{addListener:function(){}},sendMessage:function(){},
connect:function(){return{onMessage:{addListener:function(){}},postMessage:function(){}};}};
window.chrome.loadTimes=function(){return{commitLoadTime:Date.now()/1000,
connectionInfo:'http/1.1',finishLoadTime:Date.now()/1000,navigationType:'Other',
requestTime:Date.now()/1000-0.16,startLoadTime:Date.now()/1000}};
window.chrome.csi=function(){return{onloadT:Date.now(),pageT:Date.now()/1000,
startE:Date.now(),tran:15}};
if(navigator.permissions){var o=navigator.permissions.query;
navigator.permissions.query=function(p){if(p.name==='notifications')
return Promise.resolve({state:'prompt'});return o.call(navigator.permissions,p);};}
try{var g=WebGLRenderingContext.prototype.getParameter;
WebGLRenderingContext.prototype.getParameter=function(p){
if(p===37445)return'Intel Inc.';if(p===37446)return'Intel Iris OpenGL Engine';
return g.call(this,p);};}catch(e){}
Object.defineProperty(screen,'width',{get:()=>1920});
Object.defineProperty(screen,'height',{get:()=>1080});
Object.defineProperty(screen,'colorDepth',{get:()=>24});
for(var p in window){if(/^cdc_/.test(p)){try{delete window[p]}catch(e){}}}
'''


def get_driver():
    browser = find_path(['chromium', 'chromium-browser'],
                       ['/usr/bin/chromium', '/usr/bin/chromium-browser'])
    drv = find_path(['chromedriver'],
                   ['/usr/bin/chromedriver', '/usr/lib/chromium/chromedriver'])

    if not browser:
        raise Exception("المتصفح غير موجود!")
    if not drv:
        raise Exception("ChromeDriver غير موجود!")

    patched_drv = patch_chromedriver(drv)
    version = get_browser_version(browser)
    ua = f"Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/{version}.0.0.0 Safari/537.36"

    options = Options()
    options.binary_location = browser

    options.add_argument('--incognito')
    options.add_argument('--disable-blink-features=AutomationControlled')
    options.add_experimental_option("excludeSwitches", ["enable-automation"])
    options.add_experimental_option('useAutomationExtension', False)
    options.add_argument(f'--user-agent={ua}')
    options.add_argument('--lang=en-US')
    options.add_argument('--no-sandbox')
    options.add_argument('--disable-dev-shm-usage')
    options.add_argument('--disable-gpu')
    options.add_argument('--window-size=1024,768')
    options.add_argument('--no-first-run')
    options.add_argument('--no-default-browser-check')
    options.add_argument('--mute-audio')
    options.add_argument('--disable-features=TranslateUI')
    options.add_argument('--disable-extensions')
    options.add_argument('--disable-component-update')
    options.add_argument('--disable-sync')
    options.add_argument('--disable-background-timer-throttling')
    options.add_argument('--disable-backgrounding-occluded-windows')
    options.add_argument('--disable-renderer-backgrounding')

    options.page_load_strategy = 'eager'

    service = Service(executable_path=patched_drv)
    driver = webdriver.Chrome(service=service, options=options)

    try:
        driver.execute_cdp_cmd('Page.addScriptToEvaluateOnNewDocument', {'source': STEALTH_JS})
    except: pass

    try:
        driver.execute_cdp_cmd('Network.setUserAgentOverride', {
            "userAgent": ua, "platform": "Win32", "acceptLanguage": "en-US,en;q=0.9"
        })
    except: pass

    driver.set_page_load_timeout(30)

    try:
        driver.get("about:blank")
        wd = driver.execute_script("return navigator.webdriver")
        print(f"🔍 webdriver={wd} {'✅' if not wd else '❌'}")
    except: pass

    print("✅ المتصفح جاهز (incognito 🕶️)")
    return driver


def safe_quit(driver):
    if driver:
        try: driver.quit()
        except: pass
        gc.collect()

def cleanup_session(chat_id):
    with sessions_lock:
        if chat_id in user_sessions:
            s = user_sessions[chat_id]
            s['running'] = False
            safe_quit(s.get('driver'))
            del user_sessions[chat_id]
            gc.collect()


def panel():
    mk = InlineKeyboardMarkup()
    mk.row(
        InlineKeyboardButton("⏹ إيقاف", callback_data="stop"),
        InlineKeyboardButton("🔄 تحديث", callback_data="refresh")
    )
    return mk


# ─────────────────────────────────────────────
# 🤖 معالجة جميع صفحات Google + Cloud Shell
# ─────────────────────────────────────────────
def handle_google_pages(driver, session):
    status = "مراقبة..."

    try:
        body = driver.find_element(By.TAG_NAME, "body").text
    except:
        return status

    # ═══════════════════════════════════════════════
    # 1️⃣ نافذة Cloud Shell المنبثقة → Continue
    # "Cloud Shell" + "Continue" في نفس الصفحة
    # ═══════════════════════════════════════════════
    if "cloud shell" in body.lower() and "continue" in body.lower():
        # محاولة إغلاق أي نافذة منبثقة أولاً
        try:
            # البحث عن زر Continue في النافذة المنبثقة
            continue_btns = driver.find_elements(By.XPATH,
                # زر Continue نصي (رابط أزرق)
                "//a[contains(text(), 'Continue')] | "
                # زر Continue عادي
                "//button[contains(text(), 'Continue')] | "
                # span داخل button
                "//button[.//span[contains(text(), 'Continue')]] | "
                # material button
                "//button[contains(@class, 'continue')] | "
                "//button[contains(@class, 'cfc-dialog')] | "
                # أي عنصر قابل للنقر يحتوي Continue
                "//*[@role='button'][contains(., 'Continue')] | "
                "//a[contains(@class, 'button')][contains(., 'Continue')] | "
                # البحث في dialog/modal
                "//div[contains(@class, 'modal')]//button[contains(., 'Continue')] | "
                "//div[contains(@class, 'dialog')]//button[contains(., 'Continue')] | "
                "//div[contains(@class, 'popup')]//button[contains(., 'Continue')] | "
                "//mat-dialog-container//button[contains(., 'Continue')] | "
                # أي عنصر أخير
                "//*[contains(text(), 'Continue')]"
            )
            
            for btn in continue_btns:
                try:
                    if btn.is_displayed() and btn.is_enabled():
                        time.sleep(random.uniform(0.5, 1.5))
                        
                        # محاولة الضغط العادي
                        try:
                            btn.click()
                        except:
                            # إذا فشل الضغط العادي، نستخدم JavaScript
                            driver.execute_script("arguments[0].click();", btn)
                        
                        print("🤖 Cloud Shell Continue clicked!")
                        time.sleep(3)
                        return "✅ Cloud Shell Continue ✔️"
                except:
                    continue
            
            # إذا لم نجد بالـ XPath، نبحث بـ CSS
            css_btns = driver.find_elements(By.CSS_SELECTOR,
                "button.cfc-dialog-action, "
                "a.cfc-dialog-action, "
                "button[data-prober='cloud-shell-welcome-dialog-continue'], "
                ".cfc-dialog-actions button, "
                ".modal-footer button, "
                "button.mdc-button"
            )
            for btn in css_btns:
                try:
                    if btn.is_displayed() and "continue" in btn.text.lower():
                        time.sleep(random.uniform(0.5, 1.0))
                        driver.execute_script("arguments[0].click();", btn)
                        print("🤖 Cloud Shell Continue (CSS) clicked!")
                        time.sleep(3)
                        return "✅ Cloud Shell Continue ✔️"
                except:
                    continue
                    
        except Exception as e:
            print(f"⚠️ Cloud Shell Continue: {e}")
        
        return "☁️ Cloud Shell - جاري الضغط على Continue..."

    # ═══════════════════════════════════════════════
    # 2️⃣ Verify it's you → Continue
    # ═══════════════════════════════════════════════
    if "verify it" in body.lower():
        try:
            btns = driver.find_elements(By.XPATH,
                "//button[contains(., 'Continue')] | "
                "//span[contains(., 'Continue')]/ancestor::button | "
                "//div[contains(., 'Continue')]/ancestor::button | "
                "//input[@value='Continue'] | "
                "//div[@role='button'][contains(., 'Continue')]"
            )
            for btn in btns:
                if btn.is_displayed():
                    time.sleep(random.uniform(0.5, 1.5))
                    btn.click()
                    print("🤖 Verify Continue clicked")
                    time.sleep(3)
                    return "✅ Verify Continue ✔️"
        except Exception as e:
            print(f"⚠️ Verify: {e}")
        return "🔐 Verify..."

    # ═══════════════════════════════════════════════
    # 3️⃣ I understand
    # ═══════════════════════════════════════════════
    if "I understand" in body:
        try:
            btns = driver.find_elements(By.XPATH, "//*[contains(text(), 'I understand')]")
            for btn in btns:
                if btn.is_displayed():
                    time.sleep(0.5)
                    btn.click()
                    time.sleep(2)
                    return "✅ I understand ✔️"
        except: pass

    # ═══════════════════════════════════════════════
    # 4️⃣ Couldn't sign you in
    # ═══════════════════════════════════════════════
    if "couldn't sign you in" in body.lower():
        try:
            driver.delete_all_cookies()
            time.sleep(1)
            driver.get(session.get('url', 'about:blank'))
            time.sleep(5)
        except: pass
        return "⚠️ رفض - إعادة..."

    # ═══════════════════════════════════════════════
    # 5️⃣ Accept / I agree
    # ═══════════════════════════════════════════════
    if "before you continue" in body.lower() or "I agree" in body:
        try:
            btns = driver.find_elements(By.XPATH,
                "//button[contains(., 'I agree')] | //button[contains(., 'Accept')]")
            for btn in btns:
                if btn.is_displayed():
                    btn.click()
                    time.sleep(2)
                    return "✅ Accept ✔️"
        except: pass

    # ═══════════════════════════════════════════════
    # 6️⃣ Authorize (Cloud Shell permission)
    # ═══════════════════════════════════════════════
    if "authorize" in body.lower():
        try:
            btns = driver.find_elements(By.XPATH,
                "//button[contains(., 'Authorize')] | "
                "//button[contains(., 'AUTHORIZE')]")
            for btn in btns:
                if btn.is_displayed():
                    time.sleep(random.uniform(0.5, 1.0))
                    btn.click()
                    session['auth'] = True
                    time.sleep(2)
                    return "✅ Authorize ✔️"
        except: pass

    # ═══════════════════════════════════════════════
    # 7️⃣ Trust this device / Stay signed in
    # ═══════════════════════════════════════════════
    if "trust this" in body.lower() or "stay signed in" in body.lower():
        try:
            btns = driver.find_elements(By.XPATH,
                "//button[contains(., 'Yes')] | "
                "//button[contains(., 'Trust')] | "
                "//button[contains(., 'Next')] | "
                "//button[contains(., 'Continue')]"
            )
            for btn in btns:
                if btn.is_displayed():
                    btn.click()
                    time.sleep(2)
                    return "✅ Trust/Continue ✔️"
        except: pass

    # ═══════════════════════════════════════════════
    # 8️⃣ حالة الصفحة الحالية
    # ═══════════════════════════════════════════════
    url = driver.current_url
    if "shell.cloud.google.com" in url:
        # فحص إذا الترمينال ظاهر
        try:
            terminal = driver.find_elements(By.CSS_SELECTOR,
                "xterm-screen, .xterm, .terminal, [class*='terminal']")
            if terminal:
                session['auth'] = True
                return "✅ Terminal جاهز! 🖥️"
        except: pass
        return "✅ Cloud Shell" if not session.get('auth') else "✅ Shell جاهز! 🖥️"
    elif "console.cloud.google.com" in url:
        return "📊 Console"
    elif "accounts.google.com" in url:
        return "🔐 تسجيل دخول..."
    elif "myaccount.google.com" in url:
        return "👤 الحساب"

    return status


# ─────────────────────────────────────────────
# 🎬 حلقة البث
# ─────────────────────────────────────────────
def stream_loop(chat_id, gen):
    with sessions_lock:
        if chat_id not in user_sessions:
            return
        session = user_sessions[chat_id]

    driver = session['driver']
    flash = True
    err_count = 0
    drv_err = 0
    cycle = 0

    while session['running'] and session.get('gen') == gen:
        time.sleep(random.uniform(4, 6))

        if not session['running'] or session.get('gen') != gen:
            break

        cycle += 1

        try:
            handles = driver.window_handles
            if handles:
                driver.switch_to.window(handles[-1])

            # ✅ معالجة كل الصفحات تلقائياً
            status = handle_google_pages(driver, session)

            # القفز للشل
            url = driver.current_url
            if not session.get('shell_opened'):
                if "console.cloud.google.com" in url or "myaccount.google.com" in url:
                    pid = session.get('project_id')
                    if pid:
                        try:
                            driver.get(f"https://shell.cloud.google.com/?project={pid}&pli=1&show=terminal")
                            session['shell_opened'] = True
                            time.sleep(5)
                            status = "🚀 Cloud Shell..."
                        except: pass

            # 📸 لقطة
            png = driver.get_screenshot_as_png()
            bio = io.BytesIO(png)
            bio.name = f'l_{int(time.time())}.png'

            flash = not flash
            icon = "🔴" if flash else "⭕"
            now = datetime.now().strftime("%H:%M:%S")
            proj = f"📁 {session.get('project_id')}" if session.get('project_id') else ""
            cap = f"{icon} بث مباشر 🕶️\n{proj}\n📌 {status}\n⏱ {now}"

            bot.edit_message_media(
                media=InputMediaPhoto(bio, caption=cap),
                chat_id=chat_id,
                message_id=session['msg_id'],
                reply_markup=panel()
            )

            err_count = 0
            drv_err = 0

            if cycle % 15 == 0:
                gc.collect()

        except Exception as e:
            em = str(e).lower()
            if "message is not modified" in em:
                continue
            err_count += 1
            if "too many requests" in em or "retry after" in em:
                w = re.search(r'retry after (\d+)', em)
                time.sleep(int(w.group(1)) if w else 5)
            elif any(k in em for k in ['session','disconnected','crashed','not reachable']):
                drv_err += 1
                if drv_err >= 3:
                    try: bot.send_message(chat_id, "⚠️ إعادة تشغيل...")
                    except: pass
                    try:
                        safe_quit(driver)
                        new_drv = get_driver()
                        session['driver'] = new_drv
                        driver = new_drv
                        driver.get(session.get('url', 'about:blank'))
                        session['shell_opened'] = False
                        session['auth'] = False
                        drv_err = 0
                        err_count = 0
                        time.sleep(5)
                    except:
                        session['running'] = False
                        break
            elif err_count >= 5:
                try:
                    driver.refresh()
                    err_count = 0
                except:
                    drv_err += 1

    print(f"🛑 انتهى: {chat_id}")
    gc.collect()


def start_stream(chat_id, url):
    old_drv = None
    with sessions_lock:
        if chat_id in user_sessions:
            old = user_sessions[chat_id]
            old['running'] = False
            old['gen'] = old.get('gen', 0) + 1
            old_drv = old.get('driver')

    bot.send_message(chat_id, "⚡ جاري التجهيز (incognito 🕶️)...")

    if old_drv:
        safe_quit(old_drv)
        time.sleep(2)

    project_match = re.search(r'(qwiklabs-gcp-[\w-]+)', url)
    project_id = project_match.group(1) if project_match else None

    try:
        driver = get_driver()
        bot.send_message(chat_id, "✅ المتصفح جاهز")
    except Exception as e:
        bot.send_message(chat_id, f"❌ فشل:\n`{str(e)[:300]}`", parse_mode="Markdown")
        return

    gen = int(time.time())

    with sessions_lock:
        user_sessions[chat_id] = {
            'driver': driver, 'running': False,
            'msg_id': None, 'url': url,
            'project_id': project_id,
            'shell_opened': False, 'auth': False,
            'gen': gen
        }

    session = user_sessions[chat_id]
    bot.send_message(chat_id, "🌐 فتح الرابط...")

    try:
        driver.get(url)
    except Exception as e:
        if "timeout" not in str(e).lower():
            print(f"⚠️ {e}")

    time.sleep(5)

    try:
        handles = driver.window_handles
        if handles:
            driver.switch_to.window(handles[-1])

        png = driver.get_screenshot_as_png()
        bio = io.BytesIO(png)
        bio.name = f's_{int(time.time())}.png'

        msg = bot.send_photo(
            chat_id, bio,
            caption="🔴 بث مباشر 🕶️\n📌 بدء...",
            reply_markup=panel()
        )

        session['msg_id'] = msg.message_id
        session['running'] = True

        t = threading.Thread(target=stream_loop, args=(chat_id, gen), daemon=True)
        t.start()

        bot.send_message(chat_id,
            "✅ البث يعمل! 🕶️\n"
            "🤖 طيار آلي يتعامل مع:\n"
            "• Cloud Shell popup → Continue\n"
            "• Verify → Continue\n"
            "• I understand → ✔️\n"
            "• Authorize → ✔️\n"
            "• Trust/Stay signed → ✔️"
        )

    except Exception as e:
        bot.send_message(chat_id, f"❌ فشل:\n`{str(e)[:200]}`", parse_mode="Markdown")
        cleanup_session(chat_id)


@bot.message_handler(commands=['start'])
def cmd_start(message):
    bot.reply_to(message,
        "🚀 مرحباً!\n"
        "🕶️ وضع متخفي incognito\n\n"
        "أرسل رابط يبدأ بـ:\n"
        "`https://www.skills.google/google_sso`",
        parse_mode="Markdown"
    )

@bot.message_handler(func=lambda m: m.text and m.text.startswith('https://www.skills.google/google_sso'))
def handle_url(message):
    threading.Thread(target=start_stream, args=(message.chat.id, message.text), daemon=True).start()

@bot.message_handler(func=lambda m: m.text and m.text.startswith('http'))
def handle_bad(message):
    bot.reply_to(message, "❌ يجب أن يبدأ بـ:\n`https://www.skills.google/google_sso`", parse_mode="Markdown")

@bot.callback_query_handler(func=lambda call: True)
def on_cb(call):
    cid = call.message.chat.id
    try:
        with sessions_lock:
            if cid not in user_sessions:
                bot.answer_callback_query(call.id, "لا توجد جلسة.")
                return
            s = user_sessions[cid]

        if call.data == "stop":
            s['running'] = False
            s['gen'] = s.get('gen', 0) + 1
            bot.answer_callback_query(call.id, "تم الإيقاف.")
            try: bot.edit_message_caption("🛑 توقف.", chat_id=cid, message_id=s['msg_id'])
            except: pass
            safe_quit(s.get('driver'))
            with sessions_lock:
                if cid in user_sessions:
                    del user_sessions[cid]

        elif call.data == "refresh":
            bot.answer_callback_query(call.id, "تحديث...")
            try: s['driver'].refresh()
            except: pass
    except: pass


if __name__ == '__main__':
    print("=" * 50)
    print("🚂 Railway + incognito 🕶️ + Cloud Shell handler")
    print(f"🌐 Port: {os.environ.get('PORT', 8080)}")
    print("=" * 50)

    threading.Thread(target=start_health_server, daemon=True).start()

    while True:
        try:
            bot.polling(non_stop=True, timeout=60, long_polling_timeout=60)
        except Exception as e:
            print(f"⚠️ {e}")
            time.sleep(5)
