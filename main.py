"""
╔══════════════════════════════════════════════════════════╗
║  🤖 Google Cloud Shell — Telegram Bot                    ║
║  📌 Premium Edition v4.0 (MongoDB, Queue, Auto-Cleanup)  ║
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
import certifi  # 💡 تم إضافة مكتبة certifi لحل مشكلة SSL/TLS مع MongoDB
from datetime import datetime
from http.server import HTTPServer, BaseHTTPRequestHandler
from telebot.types import (
    InputMediaPhoto,
    InlineKeyboardMarkup,
    InlineKeyboardButton,
)
from pymongo import MongoClient

from selenium import webdriver
from selenium.webdriver.chrome.service import Service
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.common.by import By
from selenium.webdriver.common.keys import Keys
from selenium.webdriver.common.action_chains import ActionChains
from selenium.common.exceptions import TimeoutException, WebDriverException
from pyvirtualdisplay import Display


# ╔═══════════════════════════════════════════════════════╗
# ║  1 · CONFIGURATION & MONGODB                          ║
# ╚═══════════════════════════════════════════════════════╝

class Config:
    """جميع الإعدادات في مكان واحد لتسهيل التعديل"""

    TOKEN = os.environ.get("BOT_TOKEN")
    PORT = int(os.environ.get("PORT", 8080))
    MONGO_URI = os.environ.get("MONGO_URI", "mongodb://localhost:27017/")
    VERSION = "4.0-VLESS-MongoQueue"

    # ── المتصفح ──
    PAGE_LOAD_TIMEOUT = 45
    SCRIPT_TIMEOUT = 20
    WINDOW_SIZE = (1024, 768)

    # ── البث المباشر ──
    STREAM_INTERVAL = (4, 6)          # (min, max) ثانية
    CMD_CHECK_INTERVAL = 3            # ثانية في وضع الأوامر

    # ── إعدادات الطابور والحظر ──
    REGION_TIMEOUT_SEC = 30           # مهلة اختيار السيرفر 30 ثانية
    COOLDOWN_MINUTES = 15             # مدة الانتظار قبل إنشاء سيرفر جديد

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


# ── إعداد قاعدة البيانات ──
try:
    # 💡 تم تمرير tlsCAFile=certifi.where() لضمان نجاح الـ SSL Handshake بدون أخطاء
    mongo_client = MongoClient(Config.MONGO_URI, serverSelectionTimeoutMS=5000, tlsCAFile=certifi.where())
    db = mongo_client["cloudshell_bot"]
    queue_col = db["deployment_queue"]
    cooldown_col = db["cooldowns"]
    mongo_client.server_info() # فحص الاتصال
    MONGO_READY = True
except Exception as e:
    print(f"⚠️ تحذير: تعذر الاتصال بـ MongoDB. تأكد من إعداد MONGO_URI. الخطأ: {e}")
    MONGO_READY = False

# ╔═══════════════════════════════════════════════════════╗
# ║  2 · LOGGING                                          ║
# ╚═══════════════════════════════════════════════════════╝

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
log = logging.getLogger("CSBot")


# ╔═══════════════════════════════════════════════════════╗
# ║  3 · BOT + GLOBAL STATE                               ║
# ╚═══════════════════════════════════════════════════════╝

if not Config.TOKEN:
    log.critical("❌ BOT_TOKEN غير موجود! أضفه كمتغير بيئة.")
    sys.exit(1)

bot = telebot.TeleBot(Config.TOKEN)

user_sessions: dict = {}
sessions_lock = threading.Lock()
chromedriver_lock = threading.Lock()
shutdown_event = threading.Event()

# 💡 نظام الطابور المباشر
active_task_cid = None
queue_lock = threading.Lock()


# ╔═══════════════════════════════════════════════════════╗
# ║  4 · HEALTH SERVER                                    ║
# ╚═══════════════════════════════════════════════════════╝

class HealthHandler(BaseHTTPRequestHandler):
    """يُرجع JSON بحالة البوت والجلسات"""

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
            q_size = queue_col.count_documents({}) if MONGO_READY else 0
            payload = json.dumps(
                {
                    "status": "running",
                    "version": Config.VERSION,
                    "sessions": active,
                    "queue_size": q_size,
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
if display is None:
    log.warning("⚠️ Xvfb غير متوفر — قد لا تعمل لقطات الشاشة")


# ╔═══════════════════════════════════════════════════════╗
# ║  6 · UTILITY HELPERS & MONGODB LOGIC                  ║
# ╚═══════════════════════════════════════════════════════╝

def is_user_in_cooldown(chat_id):
    """فحص ما إذا كان المستخدم في فترة الحظر"""
    if not MONGO_READY: return False
    record = cooldown_col.find_one({"chat_id": chat_id})
    if record and time.time() < record.get("expires_at", 0):
        return True
    return False

def set_user_cooldown(chat_id):
    """تطبيق الحظر على المستخدم"""
    if not MONGO_READY: return
    expires = time.time() + (Config.COOLDOWN_MINUTES * 60)
    cooldown_col.update_one({"chat_id": chat_id}, {"$set": {"expires_at": expires}}, upsert=True)

def track_message(session, msg_id):
    """تتبع رسائل البوت ليتم حذفها لاحقاً لتنظيف الشات"""
    if msg_id and "tracked_messages" in session:
        session["tracked_messages"].append(msg_id)

def send_safe(chat_id, text, session=None, **kw):
    """إرسال رسالة مع حماية من الأخطاء وتسجيل الـ ID للحذف"""
    try:
        msg = bot.send_message(chat_id, text, **kw)
        if session: track_message(session, msg.message_id)
        return msg
    except Exception as e:
        log.warning(f"send_safe: {e}")
        return None

def edit_safe(chat_id, message_id, text, **kw):
    """تحديث رسالة موجودة بدلاً من إرسال رسالة جديدة لمنع التشتت"""
    try:
        return bot.edit_message_text(chat_id=chat_id, message_id=message_id, text=text, **kw)
    except Exception as e:
        if "is not modified" not in str(e).lower():
            log.warning(f"edit_safe: {e}")
        return None

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
        r = subprocess.run([path, "--version"], capture_output=True,
                           text=True, timeout=5)
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
                log.info(f"🔧 chromedriver: {cnt} markers patched in memory")
                
            with open(dst, "wb") as f:
                f.write(data)
                
            os.chmod(dst, 0o755)
            PATCHED_DRIVER_PATH = dst
        except Exception as e:
            log.error(f"❌ Patching failed: {e}")
            return orig

    return dst


def safe_navigate(driver, url):
    for label, fn in [
        ("JS", lambda: driver.execute_script(
            f"window.location.href={json.dumps(url)};")),
        ("assign", lambda: driver.execute_script(
            f"window.location.assign({json.dumps(url)});")),
        ("get", lambda: driver.get(url)),
    ]:
        try:
            fn()
            log.info(f"✅ Nav [{label}]: {url[:80]}")
            return True
        except TimeoutException:
            log.info(f"⏱️ Nav [{label}] timeout (page loading)")
            return True
        except Exception as e:
            log.debug(f"Nav [{label}] fail: {e}")
    log.error(f"❌ Navigation failed: {url[:80]}")
    return False


def current_url(driver):
    try:
        return driver.current_url
    except Exception:
        return ""


def extract_project_id(url):
    for pat in [r"(qwiklabs-gcp-[\w-]+)", r"project[=/]([\w-]+)",
                r"(gcp-[\w-]+)"]:
        m = re.search(pat, url)
        if m:
            return m.group(1)
    return None


def fmt_duration(secs):
    if secs < 60:
        return f"{int(secs)}ث"
    if secs < 3600:
        return f"{int(secs // 60)}د {int(secs % 60)}ث"
    return f"{int(secs // 3600)}س {int((secs % 3600) // 60)}د"


# ╔═══════════════════════════════════════════════════════╗
# ║  7 · STEALTH JAVASCRIPT                               ║
# ╚═══════════════════════════════════════════════════════╝

STEALTH_JS = r"""
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
Object.defineProperty(navigator,'maxTouchPoints',{get:()=>0});
window.chrome=window.chrome||{};
window.chrome.runtime={onMessage:{addListener:function(){}},sendMessage:function(){},
connect:function(){return{onMessage:{addListener:function(){}},postMessage:function(){}};}};
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
"""


# ╔═══════════════════════════════════════════════════════╗
# ║  8 · BROWSER DRIVER FACTORY                           ║
# ╚═══════════════════════════════════════════════════════╝

def create_driver():
    browser = find_path(
        ["chromium", "chromium-browser"],
        ["/usr/bin/chromium", "/usr/bin/chromium-browser"],
    )
    drv = find_path(
        ["chromedriver"],
        ["/usr/bin/chromedriver", "/usr/lib/chromium/chromedriver"],
    )
    if not browser:
        raise RuntimeError("المتصفح Chromium غير موجود!")
    if not drv:
        raise RuntimeError("ChromeDriver غير موجود!")

    patched = patch_driver(drv)
    ver = browser_version(browser)
    ua = (f"Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
          f"AppleWebKit/537.36 (KHTML, like Gecko) "
          f"Chrome/{ver}.0.0.0 Safari/537.36")

    opts = Options()
    opts.binary_location = browser

    # ── مقاومة الاكتشاف ──
    opts.add_argument("--incognito")
    opts.add_argument("--disable-blink-features=AutomationControlled")
    opts.add_experimental_option("excludeSwitches", ["enable-automation"])
    opts.add_experimental_option("useAutomationExtension", False)
    opts.add_argument(f"--user-agent={ua}")
    opts.add_argument("--lang=en-US")

    # ── تحسين الذاكرة ──
    for flag in [
        "--no-sandbox",
        "--disable-dev-shm-usage",
        "--disable-gpu",
        "--disable-features=site-per-process",
        "--disable-software-rasterizer",
        '--js-flags="--max-old-space-size=256"',
        "--disable-notifications",
        f"--window-size={Config.WINDOW_SIZE[0]},{Config.WINDOW_SIZE[1]}",
        "--no-first-run",
        "--no-default-browser-check",
        "--mute-audio",
        "--disable-features=TranslateUI",
        "--disable-extensions",
        "--disable-component-update",
        "--disable-sync",
        "--disable-background-timer-throttling",
        "--disable-backgrounding-occluded-windows",
        "--disable-renderer-backgrounding",
    ]:
        opts.add_argument(flag)

    opts.page_load_strategy = "eager"

    driver = webdriver.Chrome(
        service=Service(executable_path=patched), options=opts
    )

    # ── Stealth CDP ──
    try:
        driver.execute_cdp_cmd(
            "Page.addScriptToEvaluateOnNewDocument", {"source": STEALTH_JS}
        )
    except Exception:
        pass
    try:
        driver.execute_cdp_cmd("Network.setUserAgentOverride", {
            "userAgent": ua,
            "platform": "Win32",
            "acceptLanguage": "en-US,en;q=0.9",
        })
    except Exception:
        pass

    driver.set_page_load_timeout(Config.PAGE_LOAD_TIMEOUT)
    log.info("✅ متصفح جاهز (محسّن للذاكرة)")
    return driver


# ╔═══════════════════════════════════════════════════════╗
# ║  9 · SESSION MANAGER                                  ║
# ╚═══════════════════════════════════════════════════════╝

def _new_session_dict(driver, url, project_id, gen):
    """إنشاء قاموس جلسة جديد بقيم مبدئية"""
    return {
        "driver": driver,
        "running": False,
        "msg_id": None,
        "url": url,
        "project_id": project_id,
        "shell_opened": False,
        "auth": False,
        "terminal_ready": False,
        "terminal_notified": False,
        "cmd_mode": False,
        "gen": gen,
        "run_api_checked": False,
        "shell_loading_until": 0,
        "waiting_for_region": False,
        "region_ask_time": 0,           # 💡 لتتبع الـ 30 ثانية
        "selected_region": None,
        "vless_installed": False,
        "status_msg_id": None,
        "tracked_messages": [],         # 💡 تتبع الرسائل لحذفها لاحقاً
        "created_at": time.time(),
        "cmd_history": [],
        "last_activity": time.time(),
    }


def safe_quit(driver):
    if driver:
        try:
            driver.quit()
        except Exception:
            pass
        gc.collect()


def cleanup_session(chat_id, force_delete_messages=False):
    with sessions_lock:
        s = user_sessions.pop(chat_id, None)
    if s:
        s["running"] = False
        
        # 💡 التنظيف الشامل للرسائل المتبقية إذا طُلب ذلك
        if force_delete_messages:
            for m_id in s.get("tracked_messages", []):
                try: bot.delete_message(chat_id, m_id)
                except Exception: pass
            if s.get("msg_id"):
                try: bot.delete_message(chat_id, s["msg_id"])
                except Exception: pass
                
        safe_quit(s.get("driver"))
        gc.collect()


def get_session(chat_id):
    with sessions_lock:
        return user_sessions.get(chat_id)


def _auto_cleanup_loop():
    """حذف الجلسات القديمة تلقائياً كل 30 دقيقة"""
    while not shutdown_event.is_set():
        shutdown_event.wait(Config.CLEANUP_INTERVAL_SEC)
        if shutdown_event.is_set():
            break
        cutoff = time.time() - Config.SESSION_MAX_AGE_HOURS * 3600
        stale = []
        with sessions_lock:
            for cid, s in list(user_sessions.items()):
                if s.get("created_at", 0) < cutoff:
                    stale.append(cid)
        for cid in stale:
            log.info(f"🧹 Auto-cleanup session: {cid}")
            try:
                send_safe(cid, "⏰ تم إنهاء الجلسة تلقائياً (تجاوزت الحد الأقصى).")
            except Exception:
                pass
            cleanup_session(cid)
        gc.collect()


# ╔═══════════════════════════════════════════════════════╗
# ║  10 · UI COMPONENTS (Panels & Messages)                ║
# ╚═══════════════════════════════════════════════════════╝

def build_panel(cmd_mode=False):
    mk = InlineKeyboardMarkup(row_width=2)
    if cmd_mode:
        mk.row(
            InlineKeyboardButton("📸 لقطة شاشة", callback_data="screenshot"),
            InlineKeyboardButton("🔙 رجوع للبث", callback_data="watch_mode"),
        )
    else:
        mk.row(
            InlineKeyboardButton("⌨️ وضع الأوامر", callback_data="cmd_mode"),
            InlineKeyboardButton("📸 لقطة شاشة", callback_data="screenshot"),
        )
    mk.row(
        InlineKeyboardButton("🔄 تحديث الصفحة", callback_data="refresh"),
        InlineKeyboardButton("ℹ️ حالة الجلسة", callback_data="info"),
    )
    mk.row(
        InlineKeyboardButton("🔁 إعادة تشغيل", callback_data="restart_browser"),
        InlineKeyboardButton("⏹ إيقاف", callback_data="stop"),
    )
    return mk


# ── رسائل ثابتة ──

WELCOME_MSG = """
🤖 **مرحباً بك في بوت Cloud Shell!**

━━━━━━━━━━━━━━━━━━━━━━━━━━

📋 **طريقة الاستخدام:**
1️⃣ أرسل رابط SSO من المختبر
2️⃣ البوت يفتح المتصفح تلقائياً
3️⃣ يتعامل مع صفحات Google تلقائياً
4️⃣ يستخرج السيرفرات المتاحة
5️⃣ ينتقل لـ Terminal ويُفعّل الأوامر

━━━━━━━━━━━━━━━━━━━━━━━━━━

💡 **الأوامر:**
`/help`  ← دليل كامل
`/cmd ls`  ← تنفيذ أمر
`/ss`  ← لقطة شاشة
`/status`  ← حالة الجلسة
`/stop`  ← إيقاف
`/restart`  ← إعادة تشغيل المتصفح
`/url`  ← رابط الصفحة الحالية

━━━━━━━━━━━━━━━━━━━━━━━━━━

🔗 **أرسل الرابط الآن للبدء!**
"""

HELP_MSG = """
📖 **دليل الاستخدام الكامل**

━━━ 🔗 **بدء جلسة** ━━━
أرسل رابط SSO:
`https://www.skills.google/google_sso...`

━━━ ⌨️ **تنفيذ الأوامر** ━━━
• في وضع الأوامر: اكتب مباشرة
• `/cmd ls -la`
• `/cmd gcloud config list`

━━━ 📸 **لقطات الشاشة** ━━━
• `/ss` أو `/screenshot`
• أو زر 📸 من اللوحة

━━━ ℹ️ **المعلومات** ━━━
• `/status` — حالة الجلسة
• `/url` — رابط الصفحة الحالية

━━━ 🔧 **التحكم** ━━━
• `/stop` — إيقاف الجلسة
• `/restart` — إعادة تشغيل المتصفح
• الأزرار التفاعلية أسفل البث

━━━ 💡 **نصائح** ━━━
• البوت يضغط الأزرار تلقائياً
• Terminal يُكتشف ويُفعّل تلقائياً
• أرسل رابط جديد لبدء جلسة جديدة
• الجلسة تنتهي تلقائياً بعد {hours}س
""".format(hours=Config.SESSION_MAX_AGE_HOURS)


# ╔═══════════════════════════════════════════════════════╗
# ║  11 · SHELL DETECTION                                 ║
# ╚═══════════════════════════════════════════════════════╝

def is_shell_page(driver):
    if not driver:
        return False
    try:
        u = driver.current_url
        return "shell.cloud.google.com" in u or "ide.cloud.google.com" in u
    except Exception:
        return False


def is_terminal_ready(driver):
    if not is_shell_page(driver):
        return False
    try:
        return driver.execute_script("""
            var rows = document.querySelectorAll('.xterm-rows > div');
            if (!rows.length) return false;
            for (var i = 0; i < rows.length; i++) {
                var t = (rows[i].textContent || '');
                if (t.indexOf('$') !== -1 || t.indexOf('@') !== -1
                    || t.indexOf('#') !== -1) return true;
            }
            return false;
        """)
    except Exception:
        return False


# ╔═══════════════════════════════════════════════════════╗
# ║  12 · TERMINAL INTERACTION                             ║
# ╚═══════════════════════════════════════════════════════╝

def _focus_terminal(driver):
    """حاول الدخول لآخر نافذة وإلغاء أي iframe"""
    try:
        handles = driver.window_handles
        if handles:
            driver.switch_to.window(handles[-1])
            driver.switch_to.default_content()
    except Exception:
        pass


def send_command(driver, command):
    """إرسال أمر للتيرمنال مع حل جذري لضمان ضغط زر الإدخال (Enter)"""
    if not driver:
        return False

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
        }
        return null;
    }
    var ta = getTa();
    if (ta) {
        ta.focus();
        var dt = new DataTransfer();
        dt.setData('text/plain', text + '\\n'); 
        var ev = new ClipboardEvent('paste', { clipboardData: dt, bubbles: true });
        ta.dispatchEvent(ev);
        return true;
    }
    return false;
    """
    
    try:
        success = driver.execute_script(js_paste, command_clean)
        if success:
            time.sleep(1) 
            try:
                driver.switch_to.default_content()
                frames = driver.find_elements(By.TAG_NAME, "iframe")
                entered = False
                for f in frames:
                    try:
                        driver.switch_to.frame(f)
                        el = driver.find_element(By.CSS_SELECTOR, '.xterm-helper-textarea')
                        el.send_keys(Keys.RETURN)
                        entered = True
                        break
                    except:
                        driver.switch_to.default_content()
                
                driver.switch_to.default_content()
                if not entered:
                    try:
                        el = driver.find_element(By.CSS_SELECTOR, '.xterm-helper-textarea')
                        el.send_keys(Keys.RETURN)
                    except:
                        driver.switch_to.active_element.send_keys(Keys.RETURN)
            except Exception as e:
                log.debug(f"Extra Enter failed: {e}")

            log.info(f"📋 [Paste + Enter] ← Injected {len(command_clean)} chars")
            return True
    except Exception as e:
        log.debug(f"JS Paste failed: {e}")

    # --- Fallback (الكتابة السريعة الآمنة) ---
    try:
        driver.switch_to.default_content()
        frames = driver.find_elements(By.TAG_NAME, "iframe")
        target_el = None
        for f in frames:
            try:
                driver.switch_to.frame(f)
                target_el = driver.find_element(By.CSS_SELECTOR, '.xterm-helper-textarea')
                break
            except:
                driver.switch_to.default_content()
        
        if not target_el:
            driver.switch_to.default_content()
            target_el = driver.find_element(By.CSS_SELECTOR, '.xterm-helper-textarea')

        chunk_size = 200
        for i in range(0, len(command_clean), chunk_size):
            target_el.send_keys(command_clean[i:i+chunk_size])
            time.sleep(0.05)
        target_el.send_keys(Keys.RETURN)
        driver.switch_to.default_content()
        log.info(f"⌨️ [Fallback keys] ← sent {len(command_clean)} chars")
        return True
    except Exception as e:
        driver.switch_to.default_content()
        log.error(f"Fallback send keys failed: {e}")
        return False


def read_terminal(driver):
    """قراءة محتوى التيرمنال بعدة طرق"""
    if not driver:
        return None

    for js in [
        # طريقة 1: xterm-rows
        """var rows=document.querySelectorAll('.xterm-rows > div');
           if(!rows.length){var x=document.querySelector('.xterm');
           if(x) rows=x.querySelectorAll('.xterm-rows > div');}
           if(rows.length){var l=[];rows.forEach(function(r){
           var t=(r.textContent||'');if(t.trim())l.push(t);});
           return l.join('\\n');}return null;""",
        # طريقة 2: xterm-screen
        """var s=document.querySelector('.xterm-screen');
           if(s) return s.textContent||s.innerText;
           var x=document.querySelector('.xterm');
           if(x) return x.textContent||x.innerText;return null;""",
        # طريقة 3: aria-live
        """var l=document.querySelector('[aria-live]');
           if(l) return l.textContent||l.innerText;return null;""",
    ]:
        try:
            txt = driver.execute_script(js)
            if txt and txt.strip():
                return txt.strip()
        except Exception:
            continue
    return None


def extract_result(full_output, command):
    """استخراج نتيجة أمر من مخرجات التيرمنال"""
    if not full_output:
        return None
    lines = full_output.split("\n")
    idx = -1
    for i, ln in enumerate(lines):
        if command in ln and any(c in ln for c in ("$", ">", "#")):
            idx = i
        elif ln.strip() == command:
            idx = i

    if idx == -1:
        result_lines = lines[-20:]
    else:
        result_lines = []
        for i in range(idx + 1, len(lines)):
            ln = lines[i]
            if re.match(r"^[\w\-_]+@[\w\-_]+.*\$\s*$", ln.strip()):
                break
            if ln.strip().endswith("$ ") and len(ln.strip()) > 2:
                break
            result_lines.append(ln)

    result = "\n".join(result_lines).strip()
    while "\n\n\n" in result:
        result = result.replace("\n\n\n", "\n\n")
    return result or None


def take_screenshot(driver):
    if not driver:
        return None
    try:
        _focus_terminal(driver)
        png = driver.get_screenshot_as_png()
        bio = io.BytesIO(png)
        bio.name = f"ss_{int(time.time())}_{random.randint(100,999)}.png"
        del png
        return bio
    except Exception as e:
        log.debug(f"Screenshot fail: {e}")
        return None


# ╔═══════════════════════════════════════════════════════╗
# ║  13 · GOOGLE PAGES AUTO-HANDLER                       ║
# ╚═══════════════════════════════════════════════════════╝

def _click_if_visible(driver, xpath_list, delay_before=0.5, delay_after=2):
    """محاولة نقر أول زر مرئي من قائمة XPath"""
    for xp in xpath_list:
        try:
            btns = driver.find_elements(By.XPATH, xp)
            for btn in btns:
                try:
                    if btn.is_displayed():
                        time.sleep(delay_before)
                        try:
                            btn.click()
                        except Exception:
                            driver.execute_script("arguments[0].click();", btn)
                        time.sleep(delay_after)
                        return True
                except Exception:
                    continue
        except Exception:
            continue
    return False


def handle_google_pages(driver, session):
    """التعامل التلقائي مع جميع صفحات / نوافذ Google"""
    status = "مراقبة..."
    try:
        body = driver.find_element(By.TAG_NAME, "body").text[:5000]
    except Exception:
        return status

    bl = body.lower()

    # ── Terms of Service ──
    if "agree and continue" in bl and "terms of service" in bl:
        try:
            for cb in driver.find_elements(By.XPATH,
                    "//mat-checkbox|//input[@type='checkbox']|//*[@role='checkbox']"):
                try:
                    driver.execute_script("arguments[0].click();", cb)
                except Exception:
                    pass
            time.sleep(1)
        except Exception:
            pass
        if _click_if_visible(driver, [
            "//button[contains(translate(.,'ABCDEFGHIJKLMNOPQRSTUVWXYZ',"
            "'abcdefghijklmnopqrstuvwxyz'),'agree and continue')]"
        ], 0.5, 3):
            log.info("✅ Terms accepted")
            return "✅ تم قبول الشروط"

    # ── Authorize Cloud Shell ──
    if "authorize cloud shell" in bl:
        if _click_if_visible(driver, [
            "//button[normalize-space(.)='Authorize']",
            "//button[contains(.,'Authorize')]",
        ]):
            session["auth"] = True
            return "✅ تم التفويض"
        return "🔐 بانتظار التفويض..."

    # ── Continue (Cloud Shell free) ──
    if "cloud shell" in bl and "continue" in bl and "free" in bl:
        if _click_if_visible(driver, [
            "//a[contains(text(),'Continue')]",
            "//button[contains(text(),'Continue')]",
            "//button[.//span[contains(text(),'Continue')]]",
            "//*[@role='button'][contains(.,'Continue')]",
        ], 0.5, 3):
            return "✅ Continue"
        return "☁️ نافذة Cloud Shell..."

    # ── Verify ──
    if "verify it" in bl:
        if _click_if_visible(driver, [
            "//button[contains(.,'Continue')]",
            "//input[@value='Continue']",
            "//div[@role='button'][contains(.,'Continue')]",
        ]):
            return "✅ Verify"
        return "🔐 تحقق..."

    # ── I understand ──
    if _click_if_visible(driver, [
        "//*[contains(text(),'I understand')]",
        "//input[@value='I understand']",
        "//input[@id='confirm']",
    ], 1, 4):
        return "✅ I understand"

    # ── Sign-in rejected ──
    if "couldn't sign you in" in bl:
        try:
            driver.delete_all_cookies()
            time.sleep(1)
            driver.get(session.get("url", "about:blank"))
            time.sleep(5)
        except Exception:
            pass
        return "⚠️ تم رفض الدخول — إعادة محاولة"

    # ── Generic Authorize ──
    if "authorize" in bl and ("cloud" in bl or "google" in bl):
        if _click_if_visible(driver, [
            "//button[normalize-space(.)='Authorize']",
            "//button[contains(.,'AUTHORIZE')]",
        ]):
            session["auth"] = True
            return "✅ تم التفويض"

    # ── Dismiss Gemini ──
    if "gemini" in bl and "dismiss" in bl:
        _click_if_visible(driver, [
            "//button[contains(.,'Dismiss')]",
            "//a[contains(.,'Dismiss')]",
        ], 0.3, 1)

    # ── Trust project ──
    if "trust this project" in bl or "trust project" in bl:
        if _click_if_visible(driver, [
            "//button[contains(.,'Trust')]",
            "//button[contains(.,'Confirm')]",
        ]):
            return "✅ Trust"

    # ── الحالة بحسب الرابط ──
    try:
        u = driver.current_url
    except Exception:
        return status

    if "shell.cloud.google.com" in u or "ide.cloud.google.com" in u:
        session["terminal_ready"] = True
        return "✅ Terminal جاهز"
    if "console.cloud.google.com" in u:
        return "📊 Console"
    if "accounts.google.com" in u:
        return "🔐 تسجيل الدخول..."
    return status


# ╔═══════════════════════════════════════════════════════╗
# ║  14 · CLOUD RUN REGION EXTRACTION                     ║
# ╚═══════════════════════════════════════════════════════╝

REGION_JS = """
var callback = arguments[arguments.length - 1];
setTimeout(function() {
    try {
        var dd = document.querySelectorAll('mat-select, [role="combobox"]');
        for (var i = 0; i < dd.length; i++) {
            if ((dd[i].getAttribute('aria-label') || '').toLowerCase().includes('region')) {
                dd[i].click(); break;
            }
        }
        setTimeout(function() {
            var opts = document.querySelectorAll('mat-option, [role="option"]');
            var res = [];
            for (var k = 0; k < opts.length; k++) {
                var o = opts[k];
                if (o.getBoundingClientRect().width > 0 && !o.classList.contains('mat-option-disabled')) {
                    var t = (o.innerText || '').trim().split('\\n')[0];
                    if (t.includes('-') && !t.toLowerCase().includes('learn')) res.push(t);
                }
            }
            document.dispatchEvent(new KeyboardEvent('keydown', {'key':'Escape'}));
            callback(res.length ? res.join('\\n') : 'NO_REGIONS');
        }, 1500);
    } catch(e) { callback('ERROR:' + e); }
}, 3000);
"""

def do_cloud_run_extraction(driver, chat_id, session):
    pid = session.get("project_id")
    if not pid: return True

    cur = current_url(driver)

    if "run/create" not in cur:
        if not session.get("status_msg_id"):
            msg = send_safe(chat_id, "⚙️ جاري فتح صفحة Cloud Run لاستخراج السيرفرات...", session)
            if msg: session["status_msg_id"] = msg.message_id
        else: edit_safe(chat_id, session["status_msg_id"], "⚙️ جاري فتح صفحة Cloud Run لاستخراج السيرفرات...")
        
        safe_navigate(driver, f"https://console.cloud.google.com/run/create?enableapi=true&project={pid}")
        return False

    if session.get("status_msg_id"):
        edit_safe(chat_id, session["status_msg_id"], "🔍 جاري قراءة السيرفرات المسموحة...")

    try:
        driver.set_script_timeout(15)
        result = driver.execute_async_script(REGION_JS)
        if result and result not in ("NO_DROPDOWN", "NO_REGIONS") and not result.startswith("ERROR:"):
            regions = [r.strip() for r in result.split("\n") if r.strip()]
            
            # 💡 عرض الأزرار في عمودين
            mk = InlineKeyboardMarkup(row_width=2)
            mk.add(*[InlineKeyboardButton(r, callback_data=f"setreg_{r.split()[0]}") for r in regions])

            if session.get("status_msg_id"):
                edit_safe(
                    chat_id, session["status_msg_id"],
                    "🌍 **السيرفرات المسموحة للإنشاء:**\nاختر السيرفر الذي تريده لبناء VLESS:\n*(لديك 30 ثانية للاختيار قبل إلغاء الجلسة)*",
                    reply_markup=mk,
                    parse_mode="Markdown"
                )
            
            session["waiting_for_region"] = True
            session["region_ask_time"] = time.time()  # 💡 بدء عداد الـ 30 ثانية
    except Exception as e:
        if session.get("status_msg_id"):
            edit_safe(chat_id, session["status_msg_id"], f"⚠️ فشل استخراج السيرفرات:\n`{str(e)[:100]}`", parse_mode="Markdown")

    return True


# ╔═══════════════════════════════════════════════════════╗
# ║  14.5 · VLESS SCRIPT GENERATOR                        ║
# ╚═══════════════════════════════════════════════════════╝

def _generate_vless_cmd(region):
    """
    توليد السكريبت مع علامة انتهاء (===VLESS_DATA_END===).
    البايثون هو من سيقرأ البيانات ويرسل الرسالة لتفادي أخطاء الـ Bash.
    """
    script = f"""#!/bin/bash
REGION="{region}"
SERVICE_NAME="ocx-server-max"
UUID=$(cat /proc/sys/kernel/random/uuid)

mkdir -p ~/vless-cloudrun-final
cd ~/vless-cloudrun-final

cat << EOC > config.json
{{
    "inbounds": [
        {{
            "port": 8080,
            "protocol": "vless",
            "settings": {{
                "clients": [
                    {{
                        "id": "$UUID",
                        "level": 0
                    }}
                ],
                "decryption": "none"
            }},
            "streamSettings": {{
                "network": "ws",
                "wsSettings": {{
                    "path": "/@O_C_X7"
                }}
            }}
        }}
    ],
    "outbounds": [
        {{
            "protocol": "freedom",
            "settings": {{}}
        }}
    ]
}}
EOC

cat << EOF > Dockerfile
FROM teddysun/xray:latest
COPY config.json /etc/xray/config.json
EXPOSE 8080
CMD ["xray", "-config", "/etc/xray/config.json"]
EOF

gcloud run deploy $SERVICE_NAME --source . --region=$REGION --allow-unauthenticated --timeout=3600 --no-cpu-throttling --execution-environment=gen2 --min-instances=1 --max-instances=8 --concurrency=100 --cpu=2 --memory=2Gi --quiet

PROJECT_ID=$(gcloud config get-value project)
PROJECT_NUM=$(gcloud projects describe $PROJECT_ID --format='value(projectNumber)')
DETERMINISTIC_HOST="${{SERVICE_NAME}}-${{PROJECT_NUM}}.${{REGION}}.run.app"
DETERMINISTIC_URL="https://${{DETERMINISTIC_HOST}}"

VLESS_LINK="vless://${{UUID}}@googlevideo.com:443?path=/%40O_C_X7&security=tls&encryption=none&host=${{DETERMINISTIC_HOST}}&type=ws&sni=googlevideo.com#𝗢 𝗖 𝗫 ⚡"

echo "===VLESS_DATA_START==="
echo "URL|${{DETERMINISTIC_URL}}"
echo "VLINK|${{VLESS_LINK}}"
echo "===VLESS_DATA_END==="
"""
    b64 = base64.b64encode(script.encode('utf-8')).decode('utf-8')
    return f"echo {b64} | base64 -d > deploy_vless.sh && bash deploy_vless.sh\n"


# ╔═══════════════════════════════════════════════════════╗
# ║  15 · STREAM ENGINE & AUTOMATION                      ║
# ╚═══════════════════════════════════════════════════════╝

def _update_stream(driver, chat_id, session, status, flash):
    flash = not flash
    icon = "🔴" if flash else "⭕"
    now = datetime.now().strftime("%H:%M:%S")
    proj = f"📁 {session.get('project_id','')}" if session.get("project_id") else ""
    extra = " | ⌨️" if session.get("terminal_ready") else ""
    loading = session.get("shell_loading_until", 0)
    if time.time() < loading: extra += f" | ⏳{int(loading - time.time())}s"

    cap = f"{icon} بث مباشر\n{proj}\n📌 {status}{extra}\n⏱ {now}"

    png = driver.get_screenshot_as_png()
    bio = io.BytesIO(png)
    bio.name = f"l_{int(time.time())}.png"

    try:
        bot.edit_message_media(
            media=InputMediaPhoto(bio, caption=cap),
            chat_id=chat_id,
            message_id=session["msg_id"],
            reply_markup=build_panel(session.get("cmd_mode", False)),
        )
    except Exception:
        pass
    bio.close()
    return flash


def stream_loop(chat_id, gen):
    with sessions_lock:
        if chat_id not in user_sessions: return
        session = user_sessions[chat_id]

    driver = session["driver"]
    flash = True
    err_n = 0
    drv_err = 0
    cycle = 0

    while session["running"] and session.get("gen") == gen:

        if session.get("cmd_mode"):
            time.sleep(Config.CMD_CHECK_INTERVAL)
            try:
                if driver and is_terminal_ready(driver):
                    session["terminal_ready"] = True
            except Exception:
                pass
            
            # 💡 نظام التنظيف والإنهاء وقراءة النتيجة من التيرمنال
            if session.get("vless_installed"):
                term_text = read_terminal(driver) or ""
                if "===VLESS_DATA_END===" in term_text:
                    url_match = re.search(r"URL\|(https://[^\n]+)", term_text)
                    vlink_match = re.search(r"VLINK\|(vless://[^\n]+)", term_text)
                    
                    if url_match and vlink_match:
                        final_url = url_match.group(1).strip()
                        final_vlink = vlink_match.group(1).strip()
                        
                        # استخراج رابط المراقبة Web Preview لـ Port 8080
                        web_preview = ""
                        try:
                            host = driver.execute_script("return window.location.hostname;")
                            if host and "cloudshell.dev" in host:
                                web_preview = f"\n\n📊 **مراقبة السيرفر:**\n`https://8080-{host}`"
                        except: pass

                        # 1. تنظيف المحادثة تماماً (حذف البث ورسائل التحديثات)
                        cleanup_session(chat_id, force_delete_messages=True)
                        
                        # 2. إرسال الرسالة الختامية الأنيقة بصيغة HTML للصندوق الأسود
                        final_msg = f"✅ Create\n\n{final_url}\n\n<pre>{final_vlink}</pre>{web_preview}"
                        bot.send_message(chat_id, final_msg, parse_mode="HTML")
                        
                        # 3. وضع المستخدم في قائمة الحظر المؤقت (Cooldown)
                        set_user_cooldown(chat_id)
                        break # إيقاف اللوب (وبالتالي إنهاء الجلسة)
                continue

        time.sleep(random.uniform(*Config.STREAM_INTERVAL))
        if not session["running"] or session.get("gen") != gen:
            break
        cycle += 1

        try:
            _focus_terminal(driver)
            status = handle_google_pages(driver, session)
            cur = current_url(driver)

            try:
                if time.time() >= session.get("shell_loading_until", 0):
                    flash = _update_stream(driver, chat_id, session, status, flash)
                err_n = 0
                drv_err = 0
            except Exception as e:
                if "message is not modified" not in str(e).lower(): raise

            on_console = any(k in cur for k in ("console.cloud.google.com", "myaccount.google.com"))
            on_shell = is_shell_page(driver)

            # 💡 التحقق من مهلة الـ 30 ثانية لاختيار السيرفر
            if session.get("waiting_for_region"):
                if time.time() - session.get("region_ask_time", 0) > Config.REGION_TIMEOUT_SEC:
                    if session.get("status_msg_id"):
                        edit_safe(chat_id, session["status_msg_id"], "⏳ **انتهى الوقت!**\nلم تقم باختيار سيرفر خلال 30 ثانية. تم إنهاء الجلسة لإفساح المجال للآخرين.", parse_mode="Markdown")
                    cleanup_session(chat_id, force_delete_messages=False)
                    break
                continue

            elif (session.get("project_id") and not session.get("run_api_checked") and on_console):
                popup = status not in ("مراقبة...", "📊 Console", "✅ Terminal")
                auth_url = any(k in cur.lower() for k in ("signin", "challenge", "speedbump", "accounts.google.com"))
                if not popup and not auth_url:
                    gc.collect()
                    if do_cloud_run_extraction(driver, chat_id, session):
                        session["run_api_checked"] = True

            elif on_shell and not session.get("terminal_notified"):
                if is_terminal_ready(driver):
                    session["terminal_ready"] = True
                    session["terminal_notified"] = True
                    session["cmd_mode"] = True

                    region = session.get("selected_region")
                    if region and not session.get("vless_installed"):
                        session["vless_installed"] = True
                        
                        cmd = _generate_vless_cmd(region)
                        send_command(driver, cmd)
                        
                        try: _update_stream(driver, chat_id, session, "⚙️ Deploying VLESS...", flash)
                        except Exception: pass
                    else:
                        send_safe(
                            chat_id,
                            "🖥️ **Terminal جاهز تماماً!** ✅\n\nتم تفعيل **⌨️ وضع الأوامر** تلقائياً.",
                            session, parse_mode="Markdown",
                        )

            if cycle % 8 == 0: gc.collect()

        except Exception as e:
            em = str(e).lower()
            if "message is not modified" in em: continue
            if any(k in em for k in _TIMEOUT_KEYS): time.sleep(2); continue
            if time.time() < session.get("shell_loading_until", 0): time.sleep(3); continue

            err_n += 1
            if "too many requests" in em or "retry after" in em:
                w = re.search(r"retry after (\d+)", em)
                time.sleep(int(w.group(1)) if w else 5)
            elif any(k in em for k in _DRIVER_KEYS):
                drv_err += 1
                if drv_err >= Config.MAX_DRV_ERR_BEFORE_RESTART:
                    _restart_driver(chat_id, session)
                    driver = session["driver"]
                    drv_err = 0; err_n = 0; time.sleep(5)
            elif err_n >= Config.MAX_ERR_BEFORE_REFRESH:
                try: driver.refresh(); err_n = 0
                except Exception: drv_err += 1

    gc.collect()


def _restart_driver(chat_id, session):
    send_safe(chat_id, "🔁 إعادة تشغيل المتصفح...", session)
    try:
        safe_quit(session.get("driver"))
        new_drv = create_driver()
        session["driver"] = new_drv
        new_drv.get(session.get("url", "about:blank"))
        session.update({
            "shell_opened": False, "auth": False, "terminal_ready": False,
            "terminal_notified": False, "run_api_checked": False, "shell_loading_until": 0,
        })
        send_safe(chat_id, "✅ تم إعادة التشغيل بنجاح!", session)
    except Exception as e:
        send_safe(chat_id, f"❌ فشل إعادة التشغيل:\n`{str(e)[:200]}`", session, parse_mode="Markdown")
        session["running"] = False


# ╔═══════════════════════════════════════════════════════╗
# ║  16 · QUEUE WORKER & START STREAM                     ║
# ╚═══════════════════════════════════════════════════════╝

def start_stream_sync(chat_id, url):
    """دالة البدء المخصصة لنظام الطابور لضمان عدم تداخل العمليات"""
    old_drv = None
    with sessions_lock:
        if chat_id in user_sessions:
            old = user_sessions[chat_id]
            old["running"] = False
            old["gen"] = old.get("gen", 0) + 1
            old_drv = old.get("driver")

    msg = send_safe(chat_id, "⚡ جاري التجهيز لإنشاء السيرفر الخاص بك...")
    status_msg_id = msg.message_id if msg else None

    if old_drv:
        safe_quit(old_drv)
        time.sleep(2)

    project_id = extract_project_id(url)

    try:
        driver = create_driver()
        if status_msg_id: edit_safe(chat_id, status_msg_id, "✅ المتصفح جاهز\n🌐 جاري فتح الرابط...")
    except Exception as e:
        if status_msg_id: edit_safe(chat_id, status_msg_id, f"❌ فشل تشغيل المتصفح:\n`{str(e)[:300]}`", parse_mode="Markdown")
        return

    gen = int(time.time())
    with sessions_lock:
        user_sessions[chat_id] = _new_session_dict(driver, url, project_id, gen)
        session = user_sessions[chat_id]
        if status_msg_id: session["tracked_messages"].append(status_msg_id)

    try: driver.get(url)
    except Exception: pass
    time.sleep(5)

    try:
        _focus_terminal(driver)
        png = driver.get_screenshot_as_png()
        bio = io.BytesIO(png)
        bio.name = f"s_{int(time.time())}.png"
        
        # إخفاء رسالة المتصفح جاهز واستبدالها بالبث
        if status_msg_id:
            try: bot.delete_message(chat_id, status_msg_id)
            except: pass

        m = bot.send_photo(
            chat_id, bio,
            caption="🔴 بث مباشر\n📌 جاري البدء...",
            reply_markup=build_panel(),
        )
        bio.close()
        del png

        with sessions_lock:
            session["msg_id"] = m.message_id
            session["running"] = True

        # تشغيل الجلسة بوضع مزامن (Sync) لضمان إيقاف الطابور حتى ينتهي
        stream_loop(chat_id, gen)

    except Exception:
        cleanup_session(chat_id)


def queue_worker():
    """نظام الطابور المعتمد على MongoDB لمعالجة طلب واحد في كل مرة"""
    global active_task_cid
    while not shutdown_event.is_set():
        if not MONGO_READY: time.sleep(5); continue
        
        try:
            task = queue_col.find_one_and_delete({}, sort=[("ts", 1)])
            if task:
                cid = task["chat_id"]
                url = task["url"]
                
                with queue_lock: active_task_cid = cid
                start_stream_sync(cid, url)
                
                # عند الانتهاء (أو الإيقاف)، تنظيف الذاكرة وأخذ الطلب التالي
                cleanup_session(cid)
                with queue_lock: active_task_cid = None
            else: time.sleep(2)
        except Exception as e:
            log.error(f"Queue worker error: {e}")
            with queue_lock: active_task_cid = None
            time.sleep(2)


# ╔═══════════════════════════════════════════════════════╗
# ║  17 · COMMAND EXECUTOR                                ║
# ╚═══════════════════════════════════════════════════════╝

def _adaptive_wait(command):
    cl = command.lower()
    if any(k in cl for k in Config.SLOW_CMDS): return 10
    if any(k in cl for k in Config.FAST_CMDS): return 2
    if "|" in command or ">" in command: return 5
    return 3

def execute_command(chat_id, command):
    session = get_session(chat_id)
    if not session:
        send_safe(chat_id, "❌ لا توجد جلسة نشطة.\nأرسل رابط SSO أولاً.")
        return

    driver = session.get("driver")
    if not driver:
        send_safe(chat_id, "❌ المتصفح غير متوفر.")
        return

    if not is_shell_page(driver):
        send_safe(chat_id, "⚠️ لست في Cloud Shell بعد.\nانتظر حتى يصل البوت للتيرمنال.")
        return

    session["terminal_ready"] = True
    session["last_activity"] = time.time()

    history = session.setdefault("cmd_history", [])
    history.append({"cmd": command, "ts": datetime.now().isoformat()})
    if len(history) > 20: history.pop(0)

    status_msg = send_safe(chat_id, f"⏳ جاري تنفيذ:\n`{command}`", session, parse_mode="Markdown")

    text_before = read_terminal(driver) or ""
    success = send_command(driver, command)

    if not success:
        send_safe(chat_id, "⚠️ فشل إرسال الأمر للتيرمنال.\nجرّب 🔄 تحديث ثم أعد المحاولة.", session)
        if status_msg:
            try: bot.delete_message(chat_id, status_msg.message_id)
            except: pass
        return

    wait = _adaptive_wait(command)
    time.sleep(wait)

    text_after = read_terminal(driver) or ""
    output = ""

    if text_after and text_after != text_before:
        if len(text_after) > len(text_before):
            new_part = text_after[len(text_before):].strip()
            output = new_part or extract_result(text_after, command) or ""
        else: output = extract_result(text_after, command) or ""
    elif text_after: output = extract_result(text_after, command) or ""

    if output:
        lines = output.split("\n")
        cleaned = []
        skip_first = False
        for ln in lines:
            if not skip_first and command in ln:
                skip_first = True; continue
            cleaned.append(ln)
        output = "\n".join(cleaned).strip()

    bio = take_screenshot(driver)

    if output:
        if len(output) > 3900: output = output[:3900] + "\n… (تم اقتطاع النص)"
        try:
            send_safe(chat_id, f"✅ **الأمر:**\n`{command}`\n\n📋 **النتيجة:**\n```\n{output}\n```", session, parse_mode="Markdown", reply_markup=build_panel(cmd_mode=True))
        except Exception:
            send_safe(chat_id, f"✅ الأمر: {command}\n\n📋 النتيجة:\n{output}", session, reply_markup=build_panel(cmd_mode=True))
    else:
        send_safe(chat_id, f"✅ تم تنفيذ: `{command}`\n📋 لم يُلتقط نص (شاهد الصورة)", session, parse_mode="Markdown")

    if bio:
        try: bot.send_photo(chat_id, bio, caption=f"📸 بعد: `{command}`", parse_mode="Markdown", reply_markup=build_panel(cmd_mode=True))
        except Exception: pass
        bio.close()

    if status_msg:
        try: bot.delete_message(chat_id, status_msg.message_id)
        except: pass


# ╔═══════════════════════════════════════════════════════╗
# ║  18 · BOT COMMAND HANDLERS                            ║
# ╚═══════════════════════════════════════════════════════╝

@bot.message_handler(commands=["start"])
def cmd_start(msg):
    bot.reply_to(msg, WELCOME_MSG, parse_mode="Markdown")

@bot.message_handler(commands=["help", "h"])
def cmd_help(msg):
    bot.reply_to(msg, HELP_MSG, parse_mode="Markdown")

@bot.message_handler(commands=["status"])
def cmd_status(msg):
    cid = msg.chat.id
    if MONGO_READY:
        in_queue = queue_col.count_documents({"chat_id": cid}) > 0
        if in_queue: bot.reply_to(msg, "⏳ أنت حالياً في طابور الانتظار."); return
        
        cd = cooldown_col.find_one({"chat_id": cid})
        if cd and time.time() < cd["expires_at"]:
            bot.reply_to(msg, "⏳ لديك حظر مؤقت نشط لمنع الضغط. يرجى الانتظار."); return

    s = get_session(cid)
    if not s: bot.reply_to(msg, "❌ لا توجد جلسة نشطة."); return
    
    uptime = fmt_duration(time.time() - s.get("created_at", time.time()))
    drv = s.get("driver")
    cur = current_url(drv) if drv else "غير متوفر"
    hist = s.get("cmd_history", [])
    last_cmds = "\n".join([f"  • `{h['cmd']}`" for h in hist[-5:]]) if hist else "  لا يوجد"

    text = (
        "ℹ️ **حالة الجلسة**\n"
        "━━━━━━━━━━━━━━━━━━━━\n"
        f"📁 **المشروع:** `{s.get('project_id', 'غير معروف')}`\n"
        f"🔄 **الحالة:** {'🟢 يعمل' if s.get('running') else '🔴 متوقف'}\n"
        f"⌨️ **Terminal:** {'✅ جاهز' if s.get('terminal_ready') else '⏳ غير جاهز'}\n"
        f"🎯 **الوضع:** {'⌨️ أوامر' if s.get('cmd_mode') else '👁️ بث'}\n"
        f"⏱️ **المدة:** {uptime}\n"
        f"🌐 **الصفحة:**\n  `{cur[:80]}`\n"
        f"\n📜 **آخر الأوامر:**\n{last_cmds}"
    )
    bot.reply_to(msg, text, parse_mode="Markdown")

@bot.message_handler(commands=["stop", "s"])
def cmd_stop(msg):
    cid = msg.chat.id
    s = get_session(cid)
    
    # محاولة سحبه من الطابور إذا كان موجوداً
    if MONGO_READY:
        res = queue_col.delete_one({"chat_id": cid})
        if res.deleted_count > 0:
            bot.reply_to(msg, "🛑 تم سحب طلبك من الطابور بنجاح.")
            return
            
    if not s:
        bot.reply_to(msg, "❌ لا توجد جلسة نشطة أو طلب في الطابور لإيقافه.")
        return
        
    s["running"] = False
    s["gen"] = s.get("gen", 0) + 1
    try: bot.edit_message_caption("🛑 تم الإيقاف", chat_id=cid, message_id=s.get("msg_id"))
    except Exception: pass
    cleanup_session(cid)
    bot.reply_to(msg, "🛑 تم إيقاف الجلسة بنجاح.")

@bot.message_handler(commands=["restart"])
def cmd_restart(msg):
    cid = msg.chat.id
    s = get_session(cid)
    if not s: bot.reply_to(msg, "❌ لا توجد جلسة."); return
    threading.Thread(target=_restart_driver, args=(cid, s), daemon=True).start()

@bot.message_handler(commands=["url"])
def cmd_url(msg):
    cid = msg.chat.id
    s = get_session(cid)
    if not s or not s.get("driver"): bot.reply_to(msg, "❌ لا توجد جلسة نشطة."); return
    u = current_url(s["driver"])
    bot.reply_to(msg, f"🌐 الصفحة الحالية:\n`{u}`", parse_mode="Markdown")

@bot.message_handler(commands=["cmd"])
def cmd_command(msg):
    parts = msg.text.split(maxsplit=1)
    if len(parts) < 2:
        bot.reply_to(msg, "💡 **الاستخدام:**\n`/cmd ls -la`\n`/cmd gcloud config list`", parse_mode="Markdown")
        return
    threading.Thread(target=execute_command, args=(msg.chat.id, parts[1]), daemon=True).start()

@bot.message_handler(commands=["screenshot", "ss"])
def cmd_ss(msg):
    cid = msg.chat.id
    s = get_session(cid)
    if not s or not s.get("driver"): bot.reply_to(msg, "❌ لا توجد جلسة نشطة."); return
    bio = take_screenshot(s["driver"])
    if bio:
        now = datetime.now().strftime("%H:%M:%S")
        bot.send_photo(cid, bio, caption=f"📸 لقطة شاشة — {now}", reply_markup=build_panel(s.get("cmd_mode", False)))
        bio.close()
    else: bot.reply_to(msg, "❌ فشل التقاط الشاشة.")

# ── معالج الروابط و نظام الطابور ──
@bot.message_handler(func=lambda m: (m.text and m.text.startswith("https://www.skills.google/google_sso")))
def handle_url_msg(msg):
    cid = msg.chat.id
    url = msg.text.strip()
    
    if not MONGO_READY:
        bot.reply_to(msg, "❌ قاعدة البيانات غير متصلة. لا يمكن حفظ الطلبات في الطابور.")
        return

    # 1. التحقق من الانتظار (Cooldown)
    if is_user_in_cooldown(cid):
        bot.reply_to(msg, "⏳ **عذراً!** لقد قمت بإنشاء سيرفر مؤخراً.\nيرجى الانتظار لبعض الوقت لإفساح المجال للآخرين.", parse_mode="Markdown")
        return

    # 2. التحقق من الجلسات الحالية والطابور
    with sessions_lock:
        if cid in user_sessions and user_sessions[cid].get("running"):
            bot.reply_to(msg, "❌ لديك جلسة قيد العمل حالياً.")
            return
            
    if queue_col.count_documents({"chat_id": cid}) > 0 or active_task_cid == cid:
        bot.reply_to(msg, "❌ طلبك موجود في الطابور أو قيد المعالجة بالفعل.")
        return
        
    pos = queue_col.count_documents({})
    queue_col.insert_one({"chat_id": cid, "url": url, "ts": time.time()})
    
    if active_task_cid is not None or pos > 0:
        bot.reply_to(msg, f"⏳ **البوت مشغول حالياً!**\nتم وضعك في الطابور.\n🔹 دورك رقم: `{pos + 1}`\nسيبدأ عملك تلقائياً.", parse_mode="Markdown")
    else:
        bot.reply_to(msg, "✅ تم استلام الرابط ووضعه في النظام، سيتم البدء فوراً.")


@bot.message_handler(func=lambda m: m.text and m.text.startswith("http"))
def handle_bad_url(msg):
    bot.reply_to(
        msg,
        "❌ الرابط غير صحيح.\n\n"
        "يجب أن يبدأ بـ:\n"
        "`https://www.skills.google/google_sso`",
        parse_mode="Markdown",
    )

# ── معالج النصوص (الأوامر المباشرة) ──
@bot.message_handler(func=lambda m: (m.text and not m.text.startswith("/") and not m.text.startswith("http")))
def handle_text(msg):
    cid = msg.chat.id
    s = get_session(cid)
    if not s: return

    if s.get("cmd_mode"):
        threading.Thread(target=execute_command, args=(cid, msg.text), daemon=True).start()
    elif is_shell_page(s.get("driver")):
        bot.reply_to(msg, "💡 اضغط **⌨️ وضع الأوامر** أولاً\nأو أرسل: `/cmd {msg.text}`", parse_mode="Markdown")


# ╔═══════════════════════════════════════════════════════╗
# ║  19 · CALLBACK HANDLER                                ║
# ╚═══════════════════════════════════════════════════════╝

@bot.callback_query_handler(func=lambda call: True)
def on_callback(call):
    cid = call.message.chat.id
    try:
        s = get_session(cid)
        if not s: bot.answer_callback_query(call.id, "لا توجد جلسة نشطة."); return

        action = call.data

        if action.startswith("setreg_"):
            region = action.split("_")[1]
            s["selected_region"] = region
            s["waiting_for_region"] = False
            bot.answer_callback_query(call.id, f"تم اختيار {region}")
            
            msg_id = s.get("status_msg_id")
            if msg_id:
                edit_safe(cid, msg_id, f"✅ تم اختيار السيرفر: `{region}`\n🚀 جاري الانتقال وتجهيز السيرفر...", parse_mode="Markdown")
            
            pid = s.get("project_id")
            if pid:
                drv = s.get("driver")
                try: drv.get("about:blank"); time.sleep(1.5); gc.collect()
                except Exception: pass
                safe_navigate(drv, f"https://shell.cloud.google.com/?enableapi=true&project={pid}&pli=1&show=terminal")
                s["shell_loading_until"] = time.time() + 10
            return

        elif action == "stop":
            s["running"] = False
            s["gen"] = s.get("gen", 0) + 1
            bot.answer_callback_query(call.id, "🛑 إيقاف...")
            try: bot.edit_message_caption("🛑 تم الإيقاف", chat_id=cid, message_id=s.get("msg_id"))
            except Exception: pass
            cleanup_session(cid)

        elif action == "refresh":
            bot.answer_callback_query(call.id, "🔄 تحديث...")
            if s.get("driver"):
                try: s["driver"].refresh()
                except Exception: pass

        elif action == "screenshot":
            bot.answer_callback_query(call.id, "📸 جاري التقاط...")
            if s.get("driver"):
                bio = take_screenshot(s["driver"])
                if bio:
                    now = datetime.now().strftime("%H:%M:%S")
                    msg = bot.send_photo(cid, bio, caption=f"📸 {now}", reply_markup=build_panel(s.get("cmd_mode", False)))
                    if msg: s["tracked_messages"].append(msg.message_id)
                    bio.close()

        elif action == "cmd_mode":
            s["cmd_mode"] = True
            if s.get("driver") and is_shell_page(s.get("driver")): s["terminal_ready"] = True
            bot.answer_callback_query(call.id, "⌨️ وضع الأوامر")
            send_safe(cid, "⌨️ **وضع الأوامر مُفعّل!**\n\nأرسل أي أمر مباشرة كرسالة:\n• `ls -la`\n• `gcloud config list`\n• `cat file.txt`\n\n🔙 للرجوع للبث اضغط الزر", s, parse_mode="Markdown")

        elif action == "watch_mode":
            s["cmd_mode"] = False
            bot.answer_callback_query(call.id, "👁️ وضع البث")
            send_safe(cid, "👁️ تم الرجوع لوضع البث المباشر.", s)

        elif action == "info":
            bot.answer_callback_query(call.id, "ℹ️")
            uptime = fmt_duration(time.time() - s.get("created_at", time.time()))
            drv = s.get("driver")
            u = current_url(drv)[:60] if drv else "—"
            text = f"ℹ️ **الحالة:**\n📁 `{s.get('project_id', '—')}`\n⌨️ Terminal: {'✅' if s.get('terminal_ready') else '⏳'}\n⏱️ {uptime}\n🌐 `{u}`"
            send_safe(cid, text, s, parse_mode="Markdown")

        elif action == "restart_browser":
            bot.answer_callback_query(call.id, "🔁 إعادة تشغيل...")
            threading.Thread(target=_restart_driver, args=(cid, s), daemon=True).start()

    except Exception as e:
        log.debug(f"Callback error: {e}")


# ╔═══════════════════════════════════════════════════════╗
# ║  20 · BOOT CHECK & GRACEFUL SHUTDOWN                  ║
# ╚═══════════════════════════════════════════════════════╝

def boot_check():
    """فحص التبعيات عند بدء التشغيل"""
    log.info("🔍 فحص التبعيات...")

    browser = find_path(
        ["chromium", "chromium-browser"],
        ["/usr/bin/chromium", "/usr/bin/chromium-browser"],
    )
    drv = find_path(
        ["chromedriver"],
        ["/usr/bin/chromedriver", "/usr/lib/chromium/chromedriver"],
    )

    if not browser:
        log.critical("❌ Chromium غير موجود!")
        sys.exit(1)
    log.info(f"  ✅ Browser: {browser}")

    if not drv:
        log.critical("❌ ChromeDriver غير موجود!")
        sys.exit(1)
    log.info(f"  ✅ Driver:  {drv}")

    ver = browser_version(browser)
    log.info(f"  ✅ Version: {ver}")
    log.info(f"  ✅ Display: {'Active' if display else 'None'}")
    log.info("✅ جميع التبعيات متوفرة!")


def graceful_shutdown(signum, frame):
    """إنهاء نظيف عند إيقاف التطبيق"""
    log.info("🛑 إيقاف نظيف...")
    shutdown_event.set()

    with sessions_lock:
        for cid in list(user_sessions):
            try:
                s = user_sessions[cid]
                s["running"] = False
                safe_quit(s.get("driver"))
            except Exception:
                pass
        user_sessions.clear()

    log.info("👋 تم الإنهاء.")
    sys.exit(0)


signal.signal(signal.SIGTERM, graceful_shutdown)
signal.signal(signal.SIGINT, graceful_shutdown)


# ╔═══════════════════════════════════════════════════════╗
# ║  21 · MAIN ENTRY POINT                                ║
# ╚═══════════════════════════════════════════════════════╝

if __name__ == "__main__":
    print("═" * 55)
    print("  🤖 Google Cloud Shell Bot — Premium v4.0-Queue")
    print(f"  🌐 Port: {Config.PORT}")
    print("═" * 55)

    # فحص التبعيات
    boot_check()

    # خادم الصحة
    threading.Thread(target=_health_server, daemon=True).start()

    # تنظيف تلقائي
    threading.Thread(target=_auto_cleanup_loop, daemon=True).start()
    
    # عامل الطابور المرتبط بـ MongoDB
    threading.Thread(target=queue_worker, daemon=True).start()

    # حل مشكلة التعارض 409
    try:
        bot.remove_webhook()
        time.sleep(1)
    except Exception as e:
        log.warning(f"Webhook removal: {e}")

    log.info("🚀 البوت يعمل الآن ويستقبل الطلبات!")

    while not shutdown_event.is_set():
        try:
            bot.polling(
                non_stop=True,
                skip_pending=True,
                timeout=60,
                long_polling_timeout=60,
            )
        except Exception as e:
            log.error(f"Polling error: {e}")
            if shutdown_event.is_set():
                break
            time.sleep(5)
