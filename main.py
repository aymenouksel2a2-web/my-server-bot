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
from selenium.webdriver.common.keys import Keys
from selenium.webdriver.common.action_chains import ActionChains
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
    print("✅ Xvfb يعمل")
except:
    try:
        display = Display(visible=0, size=(800, 600))
        display.start()
    except Exception as e:
        print(f"❌ Xvfb: {e}")


def find_path(names, extras=None):
    for n in names:
        p = shutil.which(n)
        if p: return p
    for p in (extras or []):
        if os.path.isfile(p): return p
    return None

def get_browser_version(path):
    try:
        r = subprocess.run([path, '--version'], capture_output=True, text=True, timeout=5)
        m = re.search(r'(\d+)', r.stdout)
        return m.group(1) if m else "120"
    except: return "120"

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
Object.defineProperty(navigator,'plugins',{get:function(){return[
{name:'Chrome PDF Plugin',filename:'internal-pdf-viewer',length:1},
{name:'Chrome PDF Viewer',filename:'mhjfbmdgcfjbbpaeojofohoefgiehjai',length:1},
{name:'Native Client',filename:'internal-nacl-plugin',length:2}];}});
Object.defineProperty(navigator,'languages',{get:()=>['en-US','en']});
Object.defineProperty(navigator,'platform',{get:()=>'Win32'});
Object.defineProperty(navigator,'vendor',{get:()=>'Google Inc.'});
Object.defineProperty(navigator,'hardwareConcurrency',{get:()=>4});
Object.defineProperty(navigator,'deviceMemory',{get:()=>8});
window.chrome=window.chrome||{};
window.chrome.runtime={onMessage:{addListener:function(){}},sendMessage:function(){}};
if(navigator.permissions){var o=navigator.permissions.query;
navigator.permissions.query=function(p){if(p.name==='notifications')
return Promise.resolve({state:'prompt'});return o.call(navigator.permissions,p);};}
Object.defineProperty(screen,'width',{get:()=>1920});
Object.defineProperty(screen,'height',{get:()=>1080});
for(var p in window){if(/^cdc_/.test(p)){try{delete window[p]}catch(e){}}}
'''


def get_driver():
    browser = find_path(['chromium','chromium-browser'],['/usr/bin/chromium','/usr/bin/chromium-browser'])
    drv = find_path(['chromedriver'],['/usr/bin/chromedriver','/usr/lib/chromium/chromedriver'])
    if not browser: raise Exception("المتصفح غير موجود!")
    if not drv: raise Exception("ChromeDriver غير موجود!")

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
    try: driver.execute_cdp_cmd('Page.addScriptToEvaluateOnNewDocument', {'source': STEALTH_JS})
    except: pass
    try: driver.execute_cdp_cmd('Network.setUserAgentOverride', {"userAgent": ua, "platform": "Win32", "acceptLanguage": "en-US,en;q=0.9"})
    except: pass
    driver.set_page_load_timeout(30)
    print("✅ المتصفح جاهز")
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


def panel(cmd_mode=False):
    mk = InlineKeyboardMarkup()
    if cmd_mode:
        mk.row(
            InlineKeyboardButton("📸 لقطة", callback_data="screenshot"),
            InlineKeyboardButton("🔙 رجوع للبث", callback_data="watch_mode")
        )
        mk.row(
            InlineKeyboardButton("⏹ إيقاف", callback_data="stop"),
            InlineKeyboardButton("🔄 تحديث", callback_data="refresh")
        )
    else:
        mk.row(
            InlineKeyboardButton("⌨️ وضع الأوامر", callback_data="cmd_mode"),
            InlineKeyboardButton("📸 لقطة", callback_data="screenshot")
        )
        mk.row(
            InlineKeyboardButton("⏹ إيقاف", callback_data="stop"),
            InlineKeyboardButton("🔄 تحديث", callback_data="refresh")
        )
    return mk


def is_on_shell_page(driver):
    try:
        url = driver.current_url
        return "shell.cloud.google.com" in url or "ide.cloud.google.com" in url
    except: return False


# ─────────────────────────────────────────────
# ⌨️ إرسال أمر للترمينال (محسّن بالكامل)
# ─────────────────────────────────────────────
def send_command_to_terminal(driver, command):
    """إرسال أمر للترمينال - 4 طرق مع تحسينات"""

    try:
        handles = driver.window_handles
        if handles:
            driver.switch_to.window(handles[-1])
    except: pass

    # إلغاء أي iframe
    try:
        driver.switch_to.default_content()
    except: pass

    # ═══════════════════════════════════════════
    # طريقة 1: البحث عن xterm textarea وتركيزه
    # ═══════════════════════════════════════════
    try:
        # البحث الشامل عن textarea (قد يكون مخفي)
        result = driver.execute_script("""
            // البحث في كل iframes أيضاً
            function findTerminalTextarea(doc) {
                var ta = doc.querySelector('.xterm-helper-textarea');
                if (ta) return ta;
                ta = doc.querySelector('textarea.xterm-helper-textarea');
                if (ta) return ta;
                // بحث في جميع textareas
                var all = doc.querySelectorAll('textarea');
                for (var i = 0; i < all.length; i++) {
                    if (all[i].className.indexOf('xterm') !== -1 ||
                        all[i].closest('.xterm') ||
                        all[i].closest('.terminal')) {
                        return all[i];
                    }
                }
                return null;
            }

            var ta = findTerminalTextarea(document);

            // بحث في iframes
            if (!ta) {
                var frames = document.querySelectorAll('iframe');
                for (var i = 0; i < frames.length; i++) {
                    try {
                        var fdoc = frames[i].contentDocument || frames[i].contentWindow.document;
                        ta = findTerminalTextarea(fdoc);
                        if (ta) break;
                    } catch(e) {}
                }
            }

            if (ta) {
                ta.focus();
                return 'FOUND';
            }
            return 'NOT_FOUND';
        """)

        if result == 'FOUND':
            time.sleep(0.2)
            # الآن نستخدم ActionChains للكتابة
            actions = ActionChains(driver)
            for char in command:
                actions.send_keys(char)
                actions.pause(random.uniform(0.02, 0.06))
            actions.send_keys(Keys.RETURN)
            actions.perform()
            print(f"⌨️ [JS+Actions] أمر: {command}")
            return True
    except Exception as e:
        print(f"⚠️ طريقة 1: {e}")

    # ═══════════════════════════════════════════
    # طريقة 2: النقر على xterm-screen ثم الكتابة
    # ═══════════════════════════════════════════
    try:
        # البحث عن عنصر xterm المرئي
        xterm_els = driver.find_elements(By.CSS_SELECTOR,
            ".xterm-screen, .xterm-rows, canvas.xterm-link-layer, "
            "canvas.xterm-text-layer, canvas.xterm-cursor-layer, "
            ".xterm, [class*='xterm']"
        )

        clicked = False
        for el in xterm_els:
            try:
                if el.is_displayed() and el.size['width'] > 100:
                    # النقر في منتصف العنصر
                    ActionChains(driver).move_to_element(el).click().perform()
                    clicked = True
                    break
            except: continue

        if clicked:
            time.sleep(0.3)
            # كتابة الأمر
            actions = ActionChains(driver)
            for char in command:
                actions.send_keys(char)
                actions.pause(random.uniform(0.02, 0.06))
            actions.send_keys(Keys.RETURN)
            actions.perform()
            print(f"⌨️ [Click+Actions] أمر: {command}")
            return True
    except Exception as e:
        print(f"⚠️ طريقة 2: {e}")

    # ═══════════════════════════════════════════
    # طريقة 3: Clipboard paste (لصق الأمر)
    # ═══════════════════════════════════════════
    try:
        # تركيز على terminal أولاً
        driver.execute_script("""
            var el = document.querySelector('.xterm-helper-textarea') ||
                     document.querySelector('.xterm-screen') ||
                     document.querySelector('.xterm');
            if (el) el.focus();
        """)
        time.sleep(0.2)

        # استخدام Ctrl+Shift+V أو إدخال مباشر
        # لكن الأسهل: نستخدم send_keys على active element
        active = driver.switch_to.active_element
        for char in command:
            active.send_keys(char)
            time.sleep(random.uniform(0.01, 0.04))
        active.send_keys(Keys.RETURN)
        print(f"⌨️ [ActiveElement] أمر: {command}")
        return True
    except Exception as e:
        print(f"⚠️ طريقة 3: {e}")

    # ═══════════════════════════════════════════
    # طريقة 4: إرسال KeyboardEvent مباشر عبر JS
    # ═══════════════════════════════════════════
    try:
        cmd_escaped = command.replace("\\", "\\\\").replace("'", "\\'").replace('"', '\\"')
        result = driver.execute_script(f"""
            var target = document.querySelector('.xterm-helper-textarea') ||
                        document.activeElement;
            if (!target) return 'NO_TARGET';

            target.focus();

            function sendKey(el, char) {{
                var keyCode = char.charCodeAt(0);
                var events = ['keydown', 'keypress', 'input', 'keyup'];
                events.forEach(function(type) {{
                    var opts = {{
                        key: char,
                        code: char === ' ' ? 'Space' : 'Key' + char.toUpperCase(),
                        keyCode: keyCode,
                        charCode: type === 'keypress' ? keyCode : 0,
                        which: keyCode,
                        bubbles: true,
                        cancelable: true,
                        composed: true
                    }};
                    if (type === 'input') {{
                        el.dispatchEvent(new InputEvent('input', {{
                            data: char,
                            inputType: 'insertText',
                            bubbles: true
                        }}));
                    }} else {{
                        el.dispatchEvent(new KeyboardEvent(type, opts));
                    }}
                }});
            }}

            var text = '{cmd_escaped}';
            for (var i = 0; i < text.length; i++) {{
                sendKey(target, text[i]);
            }}

            // Enter
            ['keydown','keypress','keyup'].forEach(function(type) {{
                target.dispatchEvent(new KeyboardEvent(type, {{
                    key: 'Enter', code: 'Enter',
                    keyCode: 13, charCode: type==='keypress'?13:0,
                    which: 13, bubbles: true, cancelable: true, composed: true
                }}));
            }});

            return 'OK';
        """)
        if result == 'OK':
            print(f"⌨️ [JS Events] أمر: {command}")
            return True
    except Exception as e:
        print(f"⚠️ طريقة 4: {e}")

    print(f"❌ فشل كل الطرق: {command}")
    return False


def take_screenshot(driver):
    try:
        handles = driver.window_handles
        if handles: driver.switch_to.window(handles[-1])
        png = driver.get_screenshot_as_png()
        bio = io.BytesIO(png)
        bio.name = f'ss_{int(time.time())}.png'
        return bio
    except: return None


# ─────────────────────────────────────────────
# 🤖 معالجة صفحات Google
# ─────────────────────────────────────────────
def handle_google_pages(driver, session):
    status = "مراقبة..."
    try: body = driver.find_element(By.TAG_NAME, "body").text
    except: return status

    if "cloud shell" in body.lower() and "continue" in body.lower() and "free" in body.lower():
        try:
            btns = driver.find_elements(By.XPATH,
                "//a[contains(text(),'Continue')]|//button[contains(text(),'Continue')]|"
                "//button[.//span[contains(text(),'Continue')]]|//*[@role='button'][contains(.,'Continue')]|"
                "//*[contains(text(),'Continue')]")
            for btn in btns:
                try:
                    if btn.is_displayed() and btn.is_enabled():
                        time.sleep(random.uniform(0.5,1.5))
                        try: btn.click()
                        except: driver.execute_script("arguments[0].click();",btn)
                        time.sleep(3)
                        return "✅ Cloud Shell Continue ✔️"
                except: continue
            css_btns = driver.find_elements(By.CSS_SELECTOR,"button.cfc-dialog-action,a.cfc-dialog-action")
            for btn in css_btns:
                try:
                    if btn.is_displayed() and "continue" in btn.text.lower():
                        driver.execute_script("arguments[0].click();",btn)
                        time.sleep(3)
                        return "✅ Continue ✔️"
                except: continue
        except: pass
        return "☁️ popup..."

    if "verify it" in body.lower():
        try:
            btns = driver.find_elements(By.XPATH,"//button[contains(.,'Continue')]|//input[@value='Continue']|//div[@role='button'][contains(.,'Continue')]")
            for btn in btns:
                if btn.is_displayed():
                    time.sleep(0.5); btn.click(); time.sleep(3)
                    return "✅ Verify ✔️"
        except: pass
        return "🔐 Verify..."

    if "I understand" in body:
        try:
            btns = driver.find_elements(By.XPATH,"//*[contains(text(),'I understand')]")
            for btn in btns:
                if btn.is_displayed(): btn.click(); time.sleep(2); return "✅ I understand ✔️"
        except: pass

    if "couldn't sign you in" in body.lower():
        try: driver.delete_all_cookies(); time.sleep(1); driver.get(session.get('url','about:blank')); time.sleep(5)
        except: pass
        return "⚠️ رفض..."

    if "authorize" in body.lower() and ("cloud" in body.lower() or "google" in body.lower()):
        try:
            btns = driver.find_elements(By.XPATH,"//button[contains(.,'Authorize')]|//button[contains(.,'AUTHORIZE')]")
            for btn in btns:
                if btn.is_displayed(): btn.click(); session['auth']=True; time.sleep(2); return "✅ Authorize ✔️"
        except: pass

    if "gemini" in body.lower() and "dismiss" in body.lower():
        try:
            btns = driver.find_elements(By.XPATH,"//button[contains(.,'Dismiss')]|//a[contains(.,'Dismiss')]")
            for btn in btns:
                if btn.is_displayed(): btn.click(); time.sleep(1)
        except: pass

    url = driver.current_url
    if "shell.cloud.google.com" in url or "ide.cloud.google.com" in url:
        session['terminal_ready'] = True
        return "✅ Terminal جاهز ⌨️"
    elif "console.cloud.google.com" in url: return "📊 Console"
    elif "accounts.google.com" in url: return "🔐 تسجيل..."
    return status


# ─────────────────────────────────────────────
# 🎬 حلقة البث
# ─────────────────────────────────────────────
def stream_loop(chat_id, gen):
    with sessions_lock:
        if chat_id not in user_sessions: return
        session = user_sessions[chat_id]

    driver = session['driver']
    flash = True; err_count = 0; drv_err = 0; cycle = 0

    while session['running'] and session.get('gen') == gen:
        if session.get('cmd_mode'):
            time.sleep(3)
            try:
                if is_on_shell_page(driver): session['terminal_ready'] = True
            except: pass
            continue

        time.sleep(random.uniform(4, 6))
        if not session['running'] or session.get('gen') != gen: break
        cycle += 1

        try:
            handles = driver.window_handles
            if handles: driver.switch_to.window(handles[-1])

            status = handle_google_pages(driver, session)

            url = driver.current_url
            if not session.get('shell_opened'):
                if "console.cloud.google.com" in url or "myaccount.google.com" in url:
                    pid = session.get('project_id')
                    if pid:
                        try:
                            driver.get(f"https://shell.cloud.google.com/?project={pid}&pli=1&show=terminal")
                            session['shell_opened'] = True; time.sleep(5); status = "🚀 Shell..."
                        except: pass

            if session.get('terminal_ready') and not session.get('terminal_notified'):
                session['terminal_notified'] = True
                try: bot.send_message(chat_id,"🖥️ **Terminal جاهز!**\n\nاضغط **⌨️ وضع الأوامر**\nأو `/cmd ls -la`",parse_mode="Markdown")
                except: pass

            png = driver.get_screenshot_as_png()
            bio = io.BytesIO(png); bio.name = f'l_{int(time.time())}.png'

            flash = not flash
            icon = "🔴" if flash else "⭕"
            now = datetime.now().strftime("%H:%M:%S")
            proj = f"📁 {session.get('project_id')}" if session.get('project_id') else ""
            t_st = " | ⌨️" if session.get('terminal_ready') else ""
            cap = f"{icon} بث 🕶️\n{proj}\n📌 {status}{t_st}\n⏱ {now}"

            bot.edit_message_media(
                media=InputMediaPhoto(bio, caption=cap),
                chat_id=chat_id, message_id=session['msg_id'],
                reply_markup=panel(session.get('cmd_mode', False))
            )
            err_count = 0; drv_err = 0
            if cycle % 15 == 0: gc.collect()

        except Exception as e:
            em = str(e).lower()
            if "message is not modified" in em: continue
            err_count += 1
            if "too many requests" in em or "retry after" in em:
                w = re.search(r'retry after (\d+)',em); time.sleep(int(w.group(1)) if w else 5)
            elif any(k in em for k in ['session','disconnected','crashed','not reachable']):
                drv_err += 1
                if drv_err >= 3:
                    try: bot.send_message(chat_id,"⚠️ إعادة تشغيل...")
                    except: pass
                    try:
                        safe_quit(driver); new_drv = get_driver()
                        session['driver'] = new_drv; driver = new_drv
                        driver.get(session.get('url','about:blank'))
                        session['shell_opened']=False;session['auth']=False;session['terminal_ready']=False
                        drv_err=0;err_count=0;time.sleep(5)
                    except: session['running']=False;break
            elif err_count >= 5:
                try: driver.refresh(); err_count=0
                except: drv_err+=1

    print(f"🛑 {chat_id}"); gc.collect()


def start_stream(chat_id, url):
    old_drv = None
    with sessions_lock:
        if chat_id in user_sessions:
            old = user_sessions[chat_id]; old['running']=False
            old['gen']=old.get('gen',0)+1; old_drv=old.get('driver')

    bot.send_message(chat_id, "⚡ جاري التجهيز...")
    if old_drv: safe_quit(old_drv); time.sleep(2)

    project_match = re.search(r'(qwiklabs-gcp-[\w-]+)', url)
    project_id = project_match.group(1) if project_match else None

    try:
        driver = get_driver()
        bot.send_message(chat_id, "✅ المتصفح جاهز")
    except Exception as e:
        bot.send_message(chat_id, f"❌ فشل:\n`{str(e)[:300]}`", parse_mode="Markdown"); return

    gen = int(time.time())
    with sessions_lock:
        user_sessions[chat_id] = {
            'driver':driver,'running':False,'msg_id':None,'url':url,
            'project_id':project_id,'shell_opened':False,'auth':False,
            'terminal_ready':False,'terminal_notified':False,'cmd_mode':False,'gen':gen
        }

    session = user_sessions[chat_id]
    bot.send_message(chat_id, "🌐 فتح الرابط...")

    try: driver.get(url)
    except Exception as e:
        if "timeout" not in str(e).lower(): print(f"⚠️ {e}")
    time.sleep(5)

    try:
        handles = driver.window_handles
        if handles: driver.switch_to.window(handles[-1])
        png = driver.get_screenshot_as_png()
        bio = io.BytesIO(png); bio.name = f's_{int(time.time())}.png'
        msg = bot.send_photo(chat_id, bio, caption="🔴 بث 🕶️\n📌 بدء...", reply_markup=panel())
        session['msg_id'] = msg.message_id; session['running'] = True
        t = threading.Thread(target=stream_loop, args=(chat_id, gen), daemon=True); t.start()
        bot.send_message(chat_id, "✅ البث يعمل!")
    except Exception as e:
        bot.send_message(chat_id, f"❌ فشل:\n`{str(e)[:200]}`", parse_mode="Markdown")
        cleanup_session(chat_id)


# ─────────────────────────────────────────────
# ⌨️ تنفيذ أمر
# ─────────────────────────────────────────────
def execute_command(chat_id, command):
    with sessions_lock:
        if chat_id not in user_sessions:
            bot.send_message(chat_id, "❌ لا توجد جلسة."); return
        session = user_sessions[chat_id]

    driver = session['driver']

    if not is_on_shell_page(driver):
        bot.send_message(chat_id, "⚠️ لست في Cloud Shell بعد."); return

    session['terminal_ready'] = True

    status_msg = bot.send_message(chat_id, f"⏳ `{command}`", parse_mode="Markdown")

    success = send_command_to_terminal(driver, command)

    if success:
        # ✅ انتظار أطول لظهور النتيجة
        wait_time = 3
        # أوامر طويلة تحتاج وقت أكثر
        if any(k in command.lower() for k in ['install','apt','pip','npm','build','deploy','gcloud']):
            wait_time = 8
        time.sleep(wait_time)

        bio = take_screenshot(driver)
        if bio:
            try:
                bot.send_photo(chat_id, bio,
                    caption=f"✅ `{command}`\n⌨️ أرسل أمر آخر",
                    parse_mode="Markdown", reply_markup=panel(cmd_mode=True))
            except:
                bot.send_message(chat_id, "✅ تم التنفيذ")
        else:
            bot.send_message(chat_id, "✅ تم (فشل الصورة)")
    else:
        bot.send_message(chat_id,
            "⚠️ فشل الإرسال.\n"
            "جرّب: 🔄 تحديث ثم أعد")

    try: bot.delete_message(chat_id, status_msg.message_id)
    except: pass


# ─────────────────────────────────────────────
# 📨 أوامر تيليغرام
# ─────────────────────────────────────────────
@bot.message_handler(commands=['start'])
def cmd_start(message):
    bot.reply_to(message,
        "🚀 مرحباً!\n\nأرسل رابط:\n`https://www.skills.google/google_sso`\n\n"
        "بعد Terminal:\n⌨️ وضع الأوامر أو `/cmd ls`\n📸 `/ss`",
        parse_mode="Markdown")

@bot.message_handler(commands=['cmd'])
def cmd_command(message):
    parts = message.text.split(maxsplit=1)
    if len(parts) < 2:
        bot.reply_to(message, "`/cmd الأمر`", parse_mode="Markdown"); return
    threading.Thread(target=execute_command, args=(message.chat.id, parts[1]), daemon=True).start()

@bot.message_handler(commands=['screenshot','ss'])
def cmd_ss(message):
    cid = message.chat.id
    with sessions_lock:
        if cid not in user_sessions: bot.reply_to(message,"❌"); return
        s = user_sessions[cid]
    bio = take_screenshot(s['driver'])
    if bio: bot.send_photo(cid, bio, caption="📸")
    else: bot.reply_to(message, "❌")

@bot.message_handler(func=lambda m: m.text and m.text.startswith('https://www.skills.google/google_sso'))
def handle_url(message):
    threading.Thread(target=start_stream, args=(message.chat.id, message.text), daemon=True).start()

@bot.message_handler(func=lambda m: m.text and m.text.startswith('http'))
def handle_bad(message):
    bot.reply_to(message, "❌ يجب أن يبدأ بـ:\n`https://www.skills.google/google_sso`", parse_mode="Markdown")

@bot.message_handler(func=lambda m: m.text and not m.text.startswith('/') and not m.text.startswith('http'))
def handle_text(message):
    cid = message.chat.id
    with sessions_lock:
        if cid not in user_sessions: return
        session = user_sessions[cid]

    if session.get('cmd_mode'):
        threading.Thread(target=execute_command, args=(cid, message.text), daemon=True).start()
    elif is_on_shell_page(session.get('driver')):
        bot.reply_to(message, "💡 اضغط **⌨️ وضع الأوامر** أولاً\nأو `/cmd "+message.text+"`", parse_mode="Markdown")


@bot.callback_query_handler(func=lambda call: True)
def on_cb(call):
    cid = call.message.chat.id
    try:
        with sessions_lock:
            if cid not in user_sessions:
                bot.answer_callback_query(call.id, "لا توجد جلسة."); return
            s = user_sessions[cid]

        if call.data == "stop":
            s['running']=False; s['gen']=s.get('gen',0)+1
            bot.answer_callback_query(call.id, "إيقاف")
            try: bot.edit_message_caption("🛑", chat_id=cid, message_id=s['msg_id'])
            except: pass
            safe_quit(s.get('driver'))
            with sessions_lock:
                if cid in user_sessions: del user_sessions[cid]

        elif call.data == "refresh":
            bot.answer_callback_query(call.id, "تحديث...")
            try: s['driver'].refresh()
            except: pass

        elif call.data == "screenshot":
            bot.answer_callback_query(call.id, "📸")
            bio = take_screenshot(s['driver'])
            if bio: bot.send_photo(cid, bio, caption="📸", reply_markup=panel(s.get('cmd_mode',False)))

        elif call.data == "cmd_mode":
            s['cmd_mode'] = True
            if is_on_shell_page(s.get('driver')): s['terminal_ready'] = True
            bot.answer_callback_query(call.id, "⌨️")
            bot.send_message(cid,
                "⌨️ **وضع الأوامر!**\n\nاكتب أي أمر:\n`ls -la`\n`gcloud config list`\n\n🔙 للرجوع",
                parse_mode="Markdown")

        elif call.data == "watch_mode":
            s['cmd_mode'] = False
            bot.answer_callback_query(call.id, "🔙")
            bot.send_message(cid, "👁️ وضع البث")
    except: pass


if __name__ == '__main__':
    print("=" * 50)
    print("🚂 Terminal Control v2")
    print(f"🌐 Port: {os.environ.get('PORT', 8080)}")
    print("=" * 50)
    threading.Thread(target=start_health_server, daemon=True).start()
    while True:
        try: bot.polling(non_stop=True, timeout=60, long_polling_timeout=60)
        except Exception as e: print(f"⚠️ {e}"); time.sleep(5)
