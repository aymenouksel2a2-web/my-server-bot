"""
╔══════════════════════════════════════════════════════════╗
║  🤖 Google Cloud Shell — Telegram Bot                    ║
║  📌 Premium Edition v3.0 (Queue + Auto Cleanup + Cookies)║
║  🔧 Railway Optimized · Low RAM · Anti-Detection         ║
╚══════════════════════════════════════════════════════════╝
"""

import telebot
import os
import sys
import time
import threading
import io
import re
import random
import shutil
import gc
import subprocess
import json
import logging
import signal
import base64
import queue
import pymongo
from datetime import datetime
from http.server import HTTPServer, BaseHTTPRequestHandler
from telebot.types import (
    InputMediaPhoto,
    InlineKeyboardMarkup,
    InlineKeyboardButton,
)

from selenium import webdriver
from selenium.webdriver.chrome.service import Service
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.common.by import By
from selenium.webdriver.common.keys import Keys
from selenium.webdriver.common.action_chains import ActionChains
from selenium.common.exceptions import TimeoutException, WebDriverException
from pyvirtualdisplay import Display


# ╔═══════════════════════════════════════════════════════╗
# ║  1 · CONFIGURATION                                    ║
# ╚═══════════════════════════════════════════════════════╝

class Config:
    """جميع الإعدادات في مكان واحد لتسهيل التعديل"""

    TOKEN = os.environ.get("BOT_TOKEN")
    PORT = int(os.environ.get("PORT", 8080))
    MONGO_URI = os.environ.get("MONGO_URI", "")
    VERSION = "3.1-Stable-Wait"

    # ── المتصفح ──
    PAGE_LOAD_TIMEOUT = 45
    SCRIPT_TIMEOUT = 25
    WINDOW_SIZE = (1024, 768)

    # ── البث المباشر ──
    STREAM_INTERVAL = (4, 6)          # (min, max) ثانية
    CMD_CHECK_INTERVAL = 3            # ثانية في وضع الأوامر

    # ── الجلسات ──
    SESSION_MAX_AGE_HOURS = 4
    CLEANUP_INTERVAL_SEC = 1800       # 30 دقيقة

    # ── عتبات الأخطاء ──
    MAX_ERR_BEFORE_REFRESH = 5
    MAX_DRV_ERR_BEFORE_RESTART = 3

    # ── تصنيف الأوامر ──
    SLOW_CMDS = (
        "install", "apt", "pip", "gcloud", "docker",
        "kubectl", "terraform", "build", "deploy",
        "npm", "yarn", "wget", "curl", "git clone",
    )
    FAST_CMDS = (
        "cat", "echo", "ls", "pwd", "whoami",
        "date", "hostname", "uname", "id", "env",
        "which", "type", "head", "tail", "wc",
    )


# ╔═══════════════════════════════════════════════════════╗
# ║  2 · LOGGING & GLOBAL STATE                           ║
# ╚═══════════════════════════════════════════════════════╝

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
log = logging.getLogger("CSBot")

if not Config.TOKEN:
    log.critical("❌ BOT_TOKEN غير موجود! أضفه كمتغير بيئة.")
    sys.exit(1)

bot = telebot.TeleBot(Config.TOKEN)

mongo_client = None
db = None
users_col = None
local_cooldowns = {} 
session_cookies = {} 

if Config.MONGO_URI:
    try:
        mongo_client = pymongo.MongoClient(Config.MONGO_URI, serverSelectionTimeoutMS=5000)
        db = mongo_client["cloudshell_bot"]
        users_col = db["users"]
        log.info("✅ تم الاتصال بقاعدة بيانات MongoDB بنجاح")
    except Exception as e:
        log.error(f"❌ فشل الاتصال بـ MongoDB سيتم استخدام الذاكرة المؤقتة: {e}")

user_sessions: dict = {}
sessions_lock = threading.Lock()
chromedriver_lock = threading.Lock()
shutdown_event = threading.Event()

deployment_queue = queue.Queue()
active_task_cid = None
queue_lock = threading.Lock()


# ╔═══════════════════════════════════════════════════════╗
# ║  3 · COOKIES MANAGEMENT                               ║
# ╚═══════════════════════════════════════════════════════╝

def save_user_cookies(driver, chat_id):
    try:
        cookies = driver.get_cookies()
        if not cookies:
            return
        
        if users_col is not None:
            users_col.update_one({"_id": chat_id}, {"$set": {"cookies": cookies}}, upsert=True)
        else:
            session_cookies[chat_id] = cookies
        log.info(f"🍪 تم حفظ الكوكيز للمستخدم {chat_id} بنجاح.")
    except Exception as e:
        log.debug(f"⚠️ فشل حفظ الكوكيز: {e}")

def load_user_cookies(driver, chat_id):
    try:
        cookies = None
        if users_col is not None:
            user_record = users_col.find_one({"_id": chat_id})
            if user_record and "cookies" in user_record:
                cookies = user_record["cookies"]
        else:
            cookies = session_cookies.get(chat_id)

        if cookies:
            driver.get("https://myaccount.google.com/")
            time.sleep(1)
            
            for cookie in cookies:
                if 'expiry' in cookie:
                    cookie['expiry'] = int(cookie['expiry'])
                try:
                    driver.add_cookie(cookie)
                except Exception:
                    continue
                    
            log.info(f"🍪 تم حقن الكوكيز للمستخدم {chat_id} بنجاح. سيتم تخطي تسجيل الدخول!")
            return True
    except Exception as e:
        log.debug(f"⚠️ فشل تحميل الكوكيز: {e}")
    return False


# ╔═══════════════════════════════════════════════════════╗
# ║  4 · HEALTH SERVER                                    ║
# ╚═══════════════════════════════════════════════════════╝

class HealthHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        if self.path in ("/", "/health", "/healthz"):
            self.send_response(200)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.end_headers()
            with sessions_lock:
                active = len(user_sessions)
                details = [
                    {
                        "chat_id": cid,
                        "running": s.get("running", False),
                        "terminal": s.get("terminal_ready", False),
                        "project": s.get("project_id", "N/A"),
                    }
                    for cid, s in user_sessions.items()
                ]
            payload = json.dumps(
                {
                    "status": "running",
                    "version": Config.VERSION,
                    "sessions": active,
                    "queue_size": deployment_queue.qsize(),
                    "details": details,
                    "ts": datetime.now().isoformat(),
                },
                ensure_ascii=False,
            )
            self.wfile.write(payload.encode())
        else:
            self.send_response(404)
            self.end_headers()

    def log_message(self, *_):
        pass

def _health_server():
    try:
        HTTPServer(("0.0.0.0", Config.PORT), HealthHandler).serve_forever()
    except Exception as exc:
        log.error(f"❌ Health-server: {exc}")


# ╔═══════════════════════════════════════════════════════╗
# ║  5 · VIRTUAL DISPLAY                                  ║
# ╚═══════════════════════════════════════════════════════╝

display = None
for size, depth in [(Config.WINDOW_SIZE, 16), ((800, 600), 24)]:
    try:
        display = Display(visible=0, size=size, color_depth=depth)
        display.start()
        log.info(f"✅ Xvfb {size[0]}×{size[1]}")
        break
    except Exception:
        continue


# ╔═══════════════════════════════════════════════════════╗
# ║  6 · UTILITY HELPERS                                  ║
# ╚═══════════════════════════════════════════════════════╝

def find_path(names, extras=None):
    for n in names:
        p = shutil.which(n)
        if p:
            return p
    for p in extras or []:
        if os.path.isfile(p):
            return p
    return None

def browser_version(path):
    try:
        r = subprocess.run([path, "--version"], capture_output=True, text=True, timeout=5)
        m = re.search(r"(\d+)", r.stdout)
        return m.group(1) if m else "120"
    except Exception:
        return "120"

PATCHED_DRIVER_PATH = None

def patch_driver(orig):
    global PATCHED_DRIVER_PATH
    with chromedriver_lock:
        if PATCHED_DRIVER_PATH and os.path.exists(PATCHED_DRIVER_PATH):
            return PATCHED_DRIVER_PATH
        dst = f"/tmp/chromedriver_patched_{os.getpid()}_{random.randint(1000, 9999)}"
        try:
            with open(orig, "rb") as f:
                data = f.read()
            cnt = data.count(b"cdc_")
            if cnt:
                data = data.replace(b"cdc_", b"aaa_")
            with open(dst, "wb") as f:
                f.write(data)
            os.chmod(dst, 0o755)
            PATCHED_DRIVER_PATH = dst
        except Exception as e:
            return orig
    return dst

def safe_navigate(driver, url):
    for label, fn in [
        ("JS", lambda: driver.execute_script(f"window.location.href={json.dumps(url)};")),
        ("assign", lambda: driver.execute_script(f"window.location.assign({json.dumps(url)});")),
        ("get", lambda: driver.get(url)),
    ]:
        try:
            fn()
            log.info(f"✅ Nav [{label}]: {url[:80]}")
            return True
        except TimeoutException:
            return True
        except Exception:
            pass
    return False

def current_url(driver):
    try:
        return driver.current_url
    except Exception:
        return ""

def extract_project_id(url):
    for pat in [r"(qwiklabs-gcp-[\w-]+)", r"project[=/]([\w-]+)", r"(gcp-[\w-]+)"]:
        m = re.search(pat, url)
        if m:
            return m.group(1)
    return None

def fmt_duration(secs):
    if secs < 60: return f"{int(secs)}ث"
    if secs < 3600: return f"{int(secs // 60)}د {int(secs % 60)}ث"
    return f"{int(secs // 3600)}س {int((secs % 3600) // 60)}د"

def send_safe(chat_id, text, **kw):
    try:
        return bot.send_message(chat_id, text, **kw)
    except Exception as e:
        return None

def edit_safe(chat_id, message_id, text, **kw):
    try:
        return bot.edit_message_text(chat_id=chat_id, message_id=message_id, text=text, **kw)
    except Exception as e:
        return None

STEALTH_JS = r"""
Object.defineProperty(navigator,'webdriver',{get:()=>undefined});
Object.defineProperty(navigator,'plugins',{get:function(){return[{name:'Chrome PDF Plugin',filename:'internal-pdf-viewer',length:1}];}});
window.chrome=window.chrome||{};
Object.defineProperty(navigator,'languages',{get:()=>['en-US','en']});
"""

def create_driver():
    browser = find_path(["chromium", "chromium-browser"], ["/usr/bin/chromium"])
    drv = find_path(["chromedriver"], ["/usr/bin/chromedriver"])
    if not browser or not drv:
        raise RuntimeError("Chromium/ChromeDriver غير موجود!")

    patched = patch_driver(drv)
    ver = browser_version(browser)
    ua = f"Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/{ver}.0.0.0 Safari/537.36"

    opts = Options()
    opts.binary_location = browser
    opts.add_argument("--incognito")
    opts.add_argument("--disable-blink-features=AutomationControlled")
    opts.add_experimental_option("excludeSwitches", ["enable-automation"])
    opts.add_experimental_option("useAutomationExtension", False)
    opts.add_argument(f"--user-agent={ua}")

    for flag in [
        "--no-sandbox", "--disable-dev-shm-usage", "--disable-gpu",
        "--disable-features=site-per-process", "--disable-software-rasterizer",
        "--disable-notifications", f"--window-size={Config.WINDOW_SIZE[0]},{Config.WINDOW_SIZE[1]}",
        "--mute-audio"
    ]:
        opts.add_argument(flag)

    opts.page_load_strategy = "eager"
    driver = webdriver.Chrome(service=Service(executable_path=patched), options=opts)
    try:
        driver.execute_cdp_cmd("Page.addScriptToEvaluateOnNewDocument", {"source": STEALTH_JS})
    except Exception:
        pass
    driver.set_page_load_timeout(Config.PAGE_LOAD_TIMEOUT)
    return driver

def _new_session_dict(driver, url, project_id, gen):
    return {
        "driver": driver, "running": False, "msg_id": None, "url": url,
        "project_id": project_id, "shell_opened": False, "auth": False,
        "terminal_ready": False, "terminal_notified": False, "cmd_mode": False,
        "gen": gen, "run_api_checked": False, "shell_loading_until": 0,
        "waiting_for_region": False, "selected_region": None, "vless_installed": False,
        "status_msg_id": None, "created_at": time.time(), "cmd_history": [],
    }

def safe_quit(driver):
    if driver:
        try: driver.quit()
        except: pass
        gc.collect()

def cleanup_session(chat_id):
    with sessions_lock:
        s = user_sessions.pop(chat_id, None)
    if s:
        s["running"] = False
        safe_quit(s.get("driver"))

def get_session(chat_id):
    with sessions_lock:
        return user_sessions.get(chat_id)

def _auto_cleanup_loop():
    while not shutdown_event.is_set():
        shutdown_event.wait(Config.CLEANUP_INTERVAL_SEC)
        if shutdown_event.is_set(): break
        cutoff = time.time() - Config.SESSION_MAX_AGE_HOURS * 3600
        stale = [cid for cid, s in list(user_sessions.items()) if s.get("created_at", 0) < cutoff]
        for cid in stale:
            try: send_safe(cid, "⏰ تم إنهاء الجلسة تلقائياً.")
            except: pass
            cleanup_session(cid)

def build_panel(cmd_mode=False):
    mk = InlineKeyboardMarkup(row_width=2)
    if cmd_mode:
        mk.row(InlineKeyboardButton("📸 لقطة شاشة", callback_data="screenshot"),
               InlineKeyboardButton("🔙 رجوع للبث", callback_data="watch_mode"))
    else:
        mk.row(InlineKeyboardButton("⌨️ وضع الأوامر", callback_data="cmd_mode"),
               InlineKeyboardButton("📸 لقطة شاشة", callback_data="screenshot"))
    mk.row(InlineKeyboardButton("🔄 تحديث الصفحة", callback_data="refresh"),
           InlineKeyboardButton("ℹ️ حالة الجلسة", callback_data="info"))
    mk.row(InlineKeyboardButton("🔁 إعادة تشغيل", callback_data="restart_browser"),
           InlineKeyboardButton("⏹ إيقاف", callback_data="stop"))
    return mk

# ╔═══════════════════════════════════════════════════════╗
# ║  TERMINAL INTERACTION & PAGES                         ║
# ╚═══════════════════════════════════════════════════════╝

def is_shell_page(driver):
    if not driver: return False
    try:
        u = driver.current_url
        return "shell.cloud.google.com" in u or "ide.cloud.google.com" in u
    except: return False

def is_terminal_ready(driver):
    if not is_shell_page(driver): return False
    try:
        return driver.execute_script("""
            var rows = document.querySelectorAll('.xterm-rows > div');
            if (!rows.length) return false;
            for (var i = 0; i < rows.length; i++) {
                if (rows[i].textContent.indexOf('$') !== -1) return true;
            } return false;
        """)
    except: return False

def _focus_terminal(driver):
    try:
        handles = driver.window_handles
        if handles:
            driver.switch_to.window(handles[-1])
            driver.switch_to.default_content()
    except: pass

def send_command(driver, command):
    if not driver: return False
    _focus_terminal(driver)
    command_clean = command.rstrip('\n')
    js_paste = """
    var text = arguments[0];
    function getTa() {
        var ta = document.querySelector('.xterm-helper-textarea');
        if (ta) return ta;
        var frames = document.querySelectorAll('iframe');
        for (var i=0; i<frames.length; i++) {
            try { ta = frames[i].contentDocument.querySelector('.xterm-helper-textarea'); if (ta) return ta; } catch(e) {}
        } return null;
    }
    var ta = getTa();
    if (ta) {
        ta.focus();
        var dt = new DataTransfer();
        dt.setData('text/plain', text + '\\n'); 
        ta.dispatchEvent(new ClipboardEvent('paste', { clipboardData: dt, bubbles: true }));
        return true;
    } return false;
    """
    try:
        if driver.execute_script(js_paste, command_clean):
            time.sleep(1)
            driver.switch_to.default_content()
            try:
                el = driver.find_element(By.CSS_SELECTOR, '.xterm-helper-textarea')
                el.send_keys(Keys.RETURN)
            except: pass
            return True
    except: pass
    return False

def read_terminal(driver):
    if not driver: return None
    try:
        txt = driver.execute_script("""
           var rows=document.querySelectorAll('.xterm-rows > div');
           if(!rows.length){var x=document.querySelector('.xterm'); if(x) rows=x.querySelectorAll('.xterm-rows > div');}
           if(rows.length){var l=[];rows.forEach(function(r){ var t=(r.textContent||'');if(t.trim())l.push(t);}); return l.join('\\n');}
           return null;
        """)
        if txt and txt.strip(): return txt.strip()
    except: pass
    return None

def extract_result(full_output, command):
    if not full_output: return None
    lines = full_output.split("\n")
    idx = -1
    for i, ln in enumerate(lines):
        if command in ln and "$" in ln: idx = i
        elif ln.strip() == command: idx = i
    if idx == -1: return "\n".join(lines[-20:]).strip()
    result_lines = []
    for i in range(idx + 1, len(lines)):
        ln = lines[i]
        if re.match(r"^[\w\-_]+@[\w\-_]+.*\$\s*$", ln.strip()): break
        result_lines.append(ln)
    return "\n".join(result_lines).strip() or None

def take_screenshot(driver):
    if not driver: return None
    try:
        _focus_terminal(driver)
        png = driver.get_screenshot_as_png()
        bio = io.BytesIO(png)
        bio.name = f"ss_{int(time.time())}.png"
        return bio
    except: return None

def _click_if_visible(driver, xpath_list):
    for xp in xpath_list:
        try:
            btns = driver.find_elements(By.XPATH, xp)
            for btn in btns:
                if btn.is_displayed():
                    try: btn.click()
                    except: driver.execute_script("arguments[0].click();", btn)
                    return True
        except: continue
    return False

def handle_google_pages(driver, session, chat_id):
    status = "مراقبة..."
    try: body = driver.find_element(By.TAG_NAME, "body").text[:5000].lower()
    except: return status

    # معالجة شاشة الضياع (حساب جوجل العام)
    if "go to google account" in body or "create an account" in body:
        pid = session.get("project_id")
        if pid and "accounts.google.com" in driver.current_url:
            driver.get(f"https://console.cloud.google.com/home/dashboard?project={pid}")
            return "🔄 إعادة توجيه للوحة التحكم..."

    if _click_if_visible(driver, ["//button[contains(translate(.,'ABCDEFGHIJKLMNOPQRSTUVWXYZ','abcdefghijklmnopqrstuvwxyz'),'agree and continue')]"]):
        return "✅ تم قبول الشروط"
    if _click_if_visible(driver, ["//span[text()='I understand']", "//button[contains(.,'I understand')]"]):
        return "✅ Welcome terms accepted"
    if _click_if_visible(driver, ["//button[normalize-space(.)='Authorize']", "//button[contains(.,'AUTHORIZE')]"]):
        session["auth"] = True
        return "✅ تم التفويض"
    if _click_if_visible(driver, ["//a[contains(text(),'Continue')]", "//button[contains(text(),'Continue')]"]):
        return "✅ Continue"

    u = driver.current_url
    if "shell.cloud.google.com" in u: return "✅ Terminal جاهز"
    if "console.cloud.google.com" in u: return "📊 Console"
    if "accounts.google.com" in u: return "🔐 تسجيل الدخول..."
    return status

# ╔═══════════════════════════════════════════════════════╗
# ║  CLOUD RUN EXTRACTION (FIXED)                         ║
# ╚═══════════════════════════════════════════════════════╝

REGION_JS = """
var callback = arguments[arguments.length - 1];
setTimeout(function() {
    try {
        var clicked = false;
        var dd = document.querySelectorAll('mat-select, [role="combobox"]');
        for (var i = 0; i < dd.length; i++) {
            var a = (dd[i].getAttribute('aria-label') || '').toLowerCase();
            var id = (dd[i].getAttribute('id') || '').toLowerCase();
            if (a.indexOf('region') !== -1 || id.indexOf('region') !== -1) {
                dd[i].click(); clicked = true; break;
            }
        }
        if (!clicked) { callback('NO_DROPDOWN'); return; }
        setTimeout(function() {
            var opts = document.querySelectorAll('mat-option, [role="option"]');
            var res = [];
            for (var k = 0; k < opts.length; k++) {
                var o = opts[k];
                var s = window.getComputedStyle(o);
                if (s.display === 'none' || s.visibility === 'hidden') continue;
                if (o.classList.contains('mat-option-disabled') || o.getAttribute('aria-disabled') === 'true') continue;
                var t = (o.innerText || '').trim().split('\\n')[0];
                if (t && t.indexOf('-') !== -1 && t.toLowerCase().indexOf('learn') === -1) res.push(t);
            }
            document.dispatchEvent(new KeyboardEvent('keydown', {'key':'Escape'}));
            var bk = document.querySelector('.cdk-overlay-backdrop');
            if (bk) bk.click();
            callback(res.length ? res.join('\\n') : 'NO_REGIONS');
        }, 1500);
    } catch(e) { callback('ERROR:' + e); }
}, 1000); // قللنا الوقت هنا لأننا ننتظر في البايثون
"""

def do_cloud_run_extraction(driver, chat_id, session):
    pid = session.get("project_id")
    if not pid: return True
    cur = current_url(driver)

    # 1. التنقل للصفحة إذا لم نكن فيها
    if "run/create" not in cur:
        if not session.get("run_navigated"):
            msg = send_safe(chat_id, "⚙️ جاري فتح صفحة Cloud Run لاستخراج السيرفرات...\n⏳ يرجى الانتظار، واجهة جوجل كلاود تحتاج وقتاً للتحميل...")
            if msg: session["status_msg_id"] = msg.message_id
            
            safe_navigate(driver, f"https://console.cloud.google.com/run/create?enableapi=true&project={pid}")
            session["run_navigated"] = True
            session["run_load_start"] = time.time()
        return False

    # 2. الصبر الاستراتيجي: ننتظر 15 ثانية لتكتمل عناصر الصفحة الثقيلة
    if "run_load_start" not in session:
        session["run_load_start"] = time.time()
        
    elapsed = time.time() - session["run_load_start"]
    if elapsed < 15:
        # تحديث الرسالة كل 5 ثواني
        if int(elapsed) % 5 == 0 and session.get("status_msg_id"):
            edit_safe(chat_id, session["status_msg_id"], f"⏳ جاري تجهيز القوائم... نرجو الانتظار ({int(15-elapsed)}ث)")
        return False # نعطي فرصة للوب البث المباشر لأخذ لقطات

    # 3. الآن وبعد أن أعطينا الصفحة وقتاً كافياً، ننفذ الكود
    if session.get("status_msg_id"):
        edit_safe(chat_id, session["status_msg_id"], "🔍 جاري قراءة السيرفرات المتوفرة والمسموحة...")

    try:
        driver.set_script_timeout(Config.SCRIPT_TIMEOUT)
        result = driver.execute_async_script(REGION_JS)

        if result is None or result in ("NO_DROPDOWN", "NO_REGIONS") or str(result).startswith("ERROR:"):
            if session.get("status_msg_id"):
                edit_safe(chat_id, session["status_msg_id"], f"⚠️ لم يتم العثور على سيرفرات مسموحة أو تأخرت الصفحة بالاستجابة.\nالنتيجة: {result}")
        else:
            regions = [r.strip() for r in result.split("\n") if r.strip()]
            mk = InlineKeyboardMarkup(row_width=2)
            mk.add(*[InlineKeyboardButton(r, callback_data=f"setreg_{r.split()[0]}") for r in regions])

            txt = "🌍 **السيرفرات المسموحة للإنشاء:**\nاختر السيرفر الذي تريده لبناء VLESS:\n\n⏱️ *تنبيه: لديك 30 ثانية فقط للاختيار*"
            if session.get("status_msg_id"):
                edit_safe(chat_id, session["status_msg_id"], txt, reply_markup=mk, parse_mode="Markdown")
            else:
                msg = send_safe(chat_id, txt, reply_markup=mk, parse_mode="Markdown")
                if msg: session["status_msg_id"] = msg.message_id
            
            session["waiting_for_region"] = True
            session["region_prompt_time"] = time.time()
            
    except Exception as e:
        if session.get("status_msg_id"):
            edit_safe(chat_id, session["status_msg_id"], f"⚠️ فشل استخراج السيرفرات: `{str(e)[:100]}`", parse_mode="Markdown")

    return True

# ╔═══════════════════════════════════════════════════════╗
# ║  VLESS SCRIPT                                         ║
# ╚═══════════════════════════════════════════════════════╝

def _generate_vless_cmd(region, token, chat_id):
    raw_script = """#!/bin/bash
REGION="<<REGION>>"
SERVICE_NAME="ocx-server-max"
UUID=$(cat /proc/sys/kernel/random/uuid)

mkdir -p ~/vless-cloudrun-final && cd ~/vless-cloudrun-final
cat << 'EOC' > config.json
{"inbounds":[{"port":8080,"protocol":"vless","settings":{"clients":[{"id":"REPLACE_UUID","level":0}],"decryption":"none"},"streamSettings":{"network":"ws","wsSettings":{"path":"/@O_C_X7"}}}],"outbounds":[{"protocol":"freedom","settings":{}}]}
EOC
sed -i "s/REPLACE_UUID/$UUID/g" config.json

cat << 'EOF' > Dockerfile
FROM teddysun/xray:latest
COPY config.json /etc/xray/config.json
EXPOSE 8080
CMD ["xray", "-config", "/etc/xray/config.json"]
EOF

gcloud run deploy $SERVICE_NAME --source . --region=$REGION --allow-unauthenticated --timeout=3600 --no-cpu-throttling --execution-environment=gen2 --min-instances=1 --max-instances=8 --concurrency=250 --cpu=2 --memory=4096Mi --quiet

PROJECT_ID=$(gcloud config get-value project)
PROJECT_NUM=$(gcloud projects describe $PROJECT_ID --format='value(projectNumber)')
HOST="${SERVICE_NAME}-${PROJECT_NUM}.${REGION}.run.app"
VLESS_LINK="vless://${UUID}@googlevideo.com:443?path=/%40O_C_X7&security=tls&encryption=none&host=${HOST}&type=ws&sni=googlevideo.com#𝗢_𝗖_𝗫"

sudo pkill -9 xray 2>/dev/null; sudo pkill -9 x-ui 2>/dev/null; sudo fuser -k 8080/tcp 2>/dev/null; sudo fuser -k 2096/tcp 2>/dev/null
wget -qO install.sh https://raw.githubusercontent.com/mhsanaei/3x-ui/master/install.sh
echo -e "y\n8080\n2\n\n\n" | sudo bash install.sh > /dev/null 2>&1
nohup sudo /usr/local/x-ui/x-ui > /dev/null 2>&1 &
sleep 5

USERNAME=$(sudo sqlite3 /etc/x-ui/x-ui.db "SELECT value FROM settings WHERE key='username';" 2>/dev/null)
PASSWORD=$(sudo sqlite3 /etc/x-ui/x-ui.db "SELECT value FROM settings WHERE key='password';" 2>/dev/null)
BASEPATH=$(sudo sqlite3 /etc/x-ui/x-ui.db "SELECT value FROM settings WHERE key='webBasePath';" 2>/dev/null)
CS_URL=$(cloudshell get-web-preview-url --port 8080 | sed 's|/$||')
PANEL_LINK="${CS_URL}/$(echo "$BASEPATH" | tr -d '/')/"

MSG="✅ <b>تم انشاء السيرفر واللوحة بنجاح</b>\n\n🌐 <b>رابط VLESS الأساسي:</b>\n<pre>${VLESS_LINK}</pre>\n\n📊 <b>رابط لوحة التحكم:</b>\n${PANEL_LINK}\n\n🔑 <b>البيانات:</b>\nاليوزر: <code>${USERNAME}</code>\nالباسورد: <code>${PASSWORD}</code>"
curl -s -X POST "https://api.telegram.org/bot<<TOKEN>>/sendMessage" -d chat_id="<<CHAT_ID>>" -d parse_mode="HTML" --data-urlencode text="$MSG"
echo "=== VLESS_DEPLOYMENT_COMPLETE ==="
"""
    raw_script = raw_script.replace("<<REGION>>", region).replace("<<TOKEN>>", token).replace("<<CHAT_ID>>", str(chat_id))
    b64 = base64.b64encode(raw_script.encode('utf-8')).decode('utf-8')
    return f"echo {b64} | base64 -d > deploy_vless.sh && bash deploy_vless.sh\n"

# ╔═══════════════════════════════════════════════════════╗
# ║  STREAM ENGINE                                        ║
# ╚═══════════════════════════════════════════════════════╝

def _update_stream(driver, chat_id, session, status, flash):
    flash = not flash
    cap = f"{'🔴' if flash else '⭕'} بث مباشر\n📁 {session.get('project_id','')}\n📌 {status}\n⏱ {datetime.now().strftime('%H:%M:%S')}"
    bio = take_screenshot(driver)
    if bio:
        try:
            bot.edit_message_media(media=InputMediaPhoto(bio, caption=cap), chat_id=chat_id, message_id=session["msg_id"], reply_markup=build_panel(session.get("cmd_mode", False)))
        except: pass
        bio.close()
    return flash

def stream_loop(chat_id, gen):
    with sessions_lock:
        if chat_id not in user_sessions: return
        session = user_sessions[chat_id]

    driver = session["driver"]
    flash = True
    err_n = 0
    cookies_saved = False

    while session["running"] and session.get("gen") == gen:
        if session.get("cmd_mode"):
            time.sleep(Config.CMD_CHECK_INTERVAL)
            if session.get("vless_installed"):
                term_text = read_terminal(driver) or ""
                if "=== VLESS_DEPLOYMENT_COMPLETE ===" in term_text:
                    time.sleep(2) 
                    if session.get("msg_id"):
                        try: bot.delete_message(chat_id, session["msg_id"])
                        except: pass
                    if session.get("status_msg_id"):
                        try: bot.delete_message(chat_id, session["status_msg_id"])
                        except: pass
                    cooldown = time.time() + (15 * 60)
                    if users_col is not None: users_col.update_one({"_id": chat_id}, {"$set": {"vless_cooldown": cooldown}}, upsert=True)
                    else: local_cooldowns[chat_id] = cooldown
                    session["running"] = False
                    break
            continue

        time.sleep(random.uniform(*Config.STREAM_INTERVAL))
        if not session["running"] or session.get("gen") != gen: break

        try:
            _focus_terminal(driver)
            status = handle_google_pages(driver, session, chat_id)
            cur = current_url(driver)

            try: flash = _update_stream(driver, chat_id, session, status, flash)
            except: pass

            # منع تداخل الصفحات: يجب أن نكون إما في لوحة التحكم حصراً أو في صفحة Cloud Run مسبقاً
            on_console_home = "console.cloud.google.com/home" in cur or "console.cloud.google.com/welcome" in cur
            on_run_page = "run/create" in cur
            on_shell = is_shell_page(driver)

            if session.get("waiting_for_region"):
                if time.time() - session.get("region_prompt_time", time.time()) > 30:
                    send_safe(chat_id, "⏱️ انتهى الوقت! سيتم التخطي لعدم الاختيار.")
                    if session.get("status_msg_id"):
                        try: bot.delete_message(chat_id, session["status_msg_id"])
                        except: pass
                    session["running"] = False
                    break
            
            # هنا التعديل الجوهري: لا يبدأ البحث إلا إذا كان المتصفح قد استقر في لوحة التحكم
            elif (session.get("project_id") and not session.get("run_api_checked") and (on_console_home or on_run_page)):
                auth_url = any(k in cur.lower() for k in ("signin", "challenge", "accounts.google.com"))
                if not auth_url:
                    if do_cloud_run_extraction(driver, chat_id, session):
                        session["run_api_checked"] = True

            elif on_shell and not session.get("terminal_notified"):
                if is_terminal_ready(driver):
                    session["terminal_ready"] = True
                    session["terminal_notified"] = True
                    session["cmd_mode"] = True
                    if not cookies_saved:
                        save_user_cookies(driver, chat_id)
                        cookies_saved = True

                    region = session.get("selected_region")
                    if region and not session.get("vless_installed"):
                        session["vless_installed"] = True
                        send_command(driver, _generate_vless_cmd(region, Config.TOKEN, chat_id))
                    else:
                        send_safe(chat_id, "🖥️ **Terminal جاهز تماماً!**", parse_mode="Markdown")

            gc.collect()

        except Exception as e:
            if "message is not modified" in str(e).lower(): continue
            err_n += 1
            if err_n >= Config.MAX_ERR_BEFORE_REFRESH:
                try: driver.refresh(); err_n = 0
                except: pass

    cleanup_session(chat_id)

def start_stream_sync(chat_id, url):
    old_drv = None
    with sessions_lock:
        if chat_id in user_sessions:
            old = user_sessions[chat_id]
            old["running"] = False
            old["gen"] = old.get("gen", 0) + 1
            old_drv = old.get("driver")

    status_msg = send_safe(chat_id, "⚡ جاري التجهيز...")
    status_msg_id = status_msg.message_id if status_msg else None

    if old_drv: safe_quit(old_drv); time.sleep(2)
    project_id = extract_project_id(url)

    try:
        driver = create_driver()
        load_user_cookies(driver, chat_id)
    except Exception as e:
        if status_msg_id: edit_safe(chat_id, status_msg_id, f"❌ فشل: {e}")
        return

    gen = int(time.time())
    with sessions_lock:
        user_sessions[chat_id] = _new_session_dict(driver, url, project_id, gen)
        session = user_sessions[chat_id]

    try: driver.get(url)
    except: pass
    time.sleep(5)

    try:
        if status_msg_id:
            try: bot.delete_message(chat_id, status_msg_id)
            except: pass
        
        bio = take_screenshot(driver)
        if bio:
            msg = bot.send_photo(chat_id, bio, caption="🔴 بث مباشر\n📌 جاري البدء...", reply_markup=build_panel())
            bio.close()
            with sessions_lock:
                session["msg_id"] = msg.message_id
                session["running"] = True
            stream_loop(chat_id, gen)
    except Exception as e:
        cleanup_session(chat_id)

def queue_worker():
    global active_task_cid
    while not shutdown_event.is_set():
        try:
            task = deployment_queue.get(timeout=2)
            cid, url = task["chat_id"], task["url"]
            with queue_lock: active_task_cid = cid
            start_stream_sync(cid, url)
            cleanup_session(cid)
            with queue_lock: active_task_cid = None
            deployment_queue.task_done()
        except queue.Empty: continue
        except:
            with queue_lock: active_task_cid = None

# ╔═══════════════════════════════════════════════════════╗
# ║  TELEGRAM HANDLERS                                    ║
# ╚═══════════════════════════════════════════════════════╝

@bot.message_handler(commands=["start", "help", "clearcookies", "status", "stop"])
def handle_basic_commands(msg):
    cmd = msg.text.split()[0].lower()
    cid = msg.chat.id
    if cmd in ["/start", "/help"]:
        bot.reply_to(msg, "🤖 أهلاً بك في بوت Cloud Shell Premium! أرسل رابط SSO للبدء.")
    elif cmd == "/clearcookies":
        if users_col is not None: users_col.update_one({"_id": cid}, {"$unset": {"cookies": ""}})
        session_cookies.pop(cid, None)
        bot.reply_to(msg, "🗑️ تم مسح الكوكيز بنجاح.")
    elif cmd == "/stop":
        s = get_session(cid)
        if s:
            s["running"] = False
            bot.reply_to(msg, "🛑 تم إيقاف الجلسة بنجاح.")
        else:
            bot.reply_to(msg, "لا توجد جلسة نشطة.")

@bot.message_handler(func=lambda m: m.text and m.text.startswith("https://www.skills.google/google_sso"))
def handle_url_msg(msg):
    cid = msg.chat.id
    url = msg.text.strip()
    
    cooldown = 0
    if users_col is not None:
        rec = users_col.find_one({"_id": cid})
        if rec and "vless_cooldown" in rec: cooldown = rec["vless_cooldown"]
    else: cooldown = local_cooldowns.get(cid, 0)

    if time.time() < cooldown:
        bot.reply_to(msg, "⏳ لديك سيرفر يعمل مسبقاً. يرجى الانتظار.")
        return

    in_queue = any(t["chat_id"] == cid for t in list(deployment_queue.queue))
    if in_queue or active_task_cid == cid or (get_session(cid) and get_session(cid).get("running")):
        bot.reply_to(msg, "❌ طلبك قيد المعالجة أو لديك جلسة نشطة.")
        return
        
    pos = deployment_queue.qsize()
    if active_task_cid is not None:
        bot.reply_to(msg, f"⏳ تم الوضع في الطابور. ترتيبك: {pos + 1}")
    
    deployment_queue.put({"chat_id": cid, "url": url})

@bot.callback_query_handler(func=lambda call: True)
def on_callback(call):
    cid = call.message.chat.id
    s = get_session(cid)
    if not s: return bot.answer_callback_query(call.id, "لا توجد جلسة نشطة.")

    action = call.data
    if action.startswith("setreg_"):
        region = action.split("_")[1]
        s["selected_region"] = region
        s["waiting_for_region"] = False
        bot.answer_callback_query(call.id, f"تم اختيار {region}")
        if s.get("status_msg_id"):
            edit_safe(cid, s["status_msg_id"], f"✅ تم اختيار السيرفر: `{region}`\n🚀 جاري الانتقال إلى Terminal...", parse_mode="Markdown")
        if s.get("project_id"):
            safe_navigate(s.get("driver"), f"https://shell.cloud.google.com/?enableapi=true&project={s.get('project_id')}&pli=1&show=terminal")
    elif action == "stop":
        s["running"] = False
        bot.answer_callback_query(call.id, "🛑 إيقاف...")
    elif action == "refresh":
        try: s.get("driver").refresh()
        except: pass
        bot.answer_callback_query(call.id, "🔄 تحديث...")

if __name__ == "__main__":
    threading.Thread(target=_health_server, daemon=True).start()
    threading.Thread(target=_auto_cleanup_loop, daemon=True).start()
    threading.Thread(target=queue_worker, daemon=True).start()
    try: bot.remove_webhook()
    except: pass
    log.info("🚀 البوت يعمل الآن...")
    while not shutdown_event.is_set():
        try: bot.polling(non_stop=True)
        except: time.sleep(5)
