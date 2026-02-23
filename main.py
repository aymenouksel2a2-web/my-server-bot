"""
╔══════════════════════════════════════════════════════════╗
║  🤖 Google Cloud Shell — Telegram Bot                    ║
║  📌 Premium Edition v2.0 (With VLESS Auto Deploy)        ║
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
    VERSION = "2.0-VLESS"

    # ── المتصفح ──
    PAGE_LOAD_TIMEOUT = 45
    SCRIPT_TIMEOUT = 20
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
            payload = json.dumps(
                {
                    "status": "running",
                    "version": Config.VERSION,
                    "sessions": active,
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
# ║  6 · UTILITY HELPERS                                   ║
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

        dst = "/tmp/chromedriver_patched"
        try:
            if os.path.exists(dst):
                os.remove(dst)
        except OSError:
            # إذا كان الملف محجوزاً (Text file busy)، ننشئ اسماً فريداً
            dst = f"/tmp/chromedriver_patched_{random.randint(1000, 9999)}"

        shutil.copy2(orig, dst)
        os.chmod(dst, 0o755)
        with open(dst, "r+b") as f:
            data = f.read()
            cnt = data.count(b"cdc_")
            if cnt:
                f.seek(0)
                f.write(data.replace(b"cdc_", b"aaa_"))
                log.info(f"🔧 chromedriver: {cnt} markers patched")
        PATCHED_DRIVER_PATH = dst
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


def send_safe(chat_id, text, **kw):
    """إرسال رسالة مع حماية من الأخطاء"""
    try:
        return bot.send_message(chat_id, text, **kw)
    except Exception as e:
        log.warning(f"send_safe: {e}")
        return None


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
        "waiting_for_region": False,    # ← متغير جديد لانتظار اختيار المستخدم
        "selected_region": None,        # ← السيرفر المختار
        "vless_installed": False,       # ← تم التثبيت أم لا
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


def cleanup_session(chat_id):
    with sessions_lock:
        s = user_sessions.pop(chat_id, None)
    if s:
        s["running"] = False
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
    """إرسال أمر للتيرمنال مع دعم الأوامر الطويلة جداً"""
    if not driver:
        return False

    _focus_terminal(driver)

    def inject_keys(el, text):
        # إذا كان الأمر طويلاً (مثل سكريبت Base64)، نرسله على دفعات سريعة لتجنب التوقف
        if len(text) > 150:
            chunk_size = 200
            for i in range(0, len(text), chunk_size):
                el.send_keys(text[i:i+chunk_size])
                time.sleep(0.05)
        else:
            # طباعة بشرية واقعية للأوامر القصيرة
            for ch in text:
                el.send_keys(ch)
                time.sleep(random.uniform(0.01, 0.04))
        el.send_keys(Keys.RETURN)

    # ── الطريقة 1: textarea عبر JS ──
    try:
        found = driver.execute_script("""
            function f(doc){
                var ta=doc.querySelector('.xterm-helper-textarea');
                if(ta) return ta;
                var all=doc.querySelectorAll('textarea');
                for(var i=0;i<all.length;i++){
                    if(all[i].className.indexOf('xterm')!==-1
                       || all[i].closest('.xterm')
                       || all[i].closest('.terminal')) return all[i];
                }
                return null;
            }
            var ta=f(document);
            if(!ta){
                var fr=document.querySelectorAll('iframe');
                for(var i=0;i<fr.length;i++){
                    try{ta=f(fr[i].contentDocument);if(ta)break;}catch(e){}
                }
            }
            if(ta){ta.focus();return ta;}
            return null;
        """)
        if found:
            time.sleep(0.2)
            inject_keys(found, command)
            log.info(f"⌨️ [textarea] ← {command[:60]}")
            return True
    except Exception as e:
        log.debug(f"M1: {e}")

    # ── الطريقة 2: Active Element ──
    try:
        driver.execute_script("""
            var el = document.querySelector('.xterm-helper-textarea')
                  || document.querySelector('.xterm-screen')
                  || document.querySelector('.xterm');
            if(el) el.focus();
        """)
        time.sleep(0.2)
        active = driver.switch_to.active_element
        inject_keys(active, command)
        log.info(f"⌨️ [active] ← {command[:60]}")
        return True
    except Exception as e:
        log.debug(f"M2: {e}")

    # ── الطريقة 3: نقر على عنصر xterm ──
    try:
        els = driver.find_elements(
            By.CSS_SELECTOR,
            ".xterm-screen, .xterm-rows, canvas.xterm-link-layer, "
            ".xterm, [class*='xterm']",
        )
        for el in els:
            try:
                if el.is_displayed() and el.size["width"] > 100:
                    ActionChains(driver).move_to_element(el).click().perform()
                    time.sleep(0.3)
                    active = driver.switch_to.active_element
                    inject_keys(active, command)
                    log.info(f"⌨️ [click] ← {command[:60]}")
                    return True
            except Exception:
                continue
    except Exception as e:
        log.debug(f"M3: {e}")

    log.warning(f"❌ فشل إرسال الأمر: {command[:60]}")
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
        var clicked = false;
        var dd = document.querySelectorAll('mat-select, [role="combobox"]');
        for (var i = 0; i < dd.length; i++) {
            var a = (dd[i].getAttribute('aria-label') || '').toLowerCase();
            var id = (dd[i].getAttribute('id') || '').toLowerCase();
            if (a.indexOf('region') !== -1 || id.indexOf('region') !== -1) {
                dd[i].click(); clicked = true; break;
            }
        }
        if (!clicked) {
            var lbl = document.querySelectorAll('label, .mat-form-field-label');
            for (var j = 0; j < lbl.length; j++) {
                if (lbl[j].innerText && lbl[j].innerText.indexOf('Region') !== -1) {
                    lbl[j].click(); clicked = true; break;
                }
            }
        }
        if (!clicked) { callback('NO_DROPDOWN'); return; }
        setTimeout(function() {
            var opts = document.querySelectorAll('mat-option, [role="option"]');
            var res = [];
            for (var k = 0; k < opts.length; k++) {
                var o = opts[k];
                var r = o.getBoundingClientRect();
                var s = window.getComputedStyle(o);
                if (r.width === 0 || r.height === 0 ||
                    s.display === 'none' || s.visibility === 'hidden') continue;
                if (o.classList.contains('mat-option-disabled') ||
                    o.getAttribute('aria-disabled') === 'true') continue;
                var t = (o.innerText || '').trim().split('\\n')[0];
                if (t && t.indexOf('-') !== -1 &&
                    t.toLowerCase().indexOf('learn') === -1) res.push(t);
            }
            document.dispatchEvent(new KeyboardEvent('keydown', {'key':'Escape'}));
            var bk = document.querySelector('.cdk-overlay-backdrop');
            if (bk) bk.click();
            callback(res.length ? res.join('\\n') : 'NO_REGIONS');
        }, 1500);
    } catch(e) { callback('ERROR:' + e); }
}, 4000);
"""


def do_cloud_run_extraction(driver, chat_id, session):
    pid = session.get("project_id")
    if not pid:
        return True

    cur = current_url(driver)

    if "run/create" not in cur:
        send_safe(chat_id,
            "⚙️ جاري فتح صفحة Cloud Run "
            "(مع تفعيل الـ API إن لزم الأمر)...")
        safe_navigate(
            driver,
            f"https://console.cloud.google.com/run/create"
            f"?enableapi=true&project={pid}",
        )
        return False

    send_safe(chat_id, "🔍 جاري قراءة السيرفرات المتوفرة والمسموحة...")

    try:
        driver.set_script_timeout(Config.SCRIPT_TIMEOUT)
        result = driver.execute_async_script(REGION_JS)

        if result is None:
            send_safe(chat_id, "⚠️ لم يتم الحصول على نتيجة.")
        elif result == "NO_DROPDOWN":
            send_safe(chat_id, "❌ لم أجد قائمة السيرفرات (Region).")
        elif result == "NO_REGIONS":
            send_safe(chat_id, "⚠️ جميع السيرفرات مقيّدة.")
        elif result.startswith("ERROR:"):
            send_safe(chat_id, f"⚠️ خطأ: {result[6:][:200]}")
        else:
            # ── تم التعديل هنا: تحويل السيرفرات إلى أزرار وإيقاف الانتقال التلقائي ──
            regions = [r.strip() for r in result.split("\n") if r.strip()]
            mk = InlineKeyboardMarkup(row_width=1)
            for r in regions:
                region_code = r.split()[0]  # يستخرج us-east1 من (us-east1 (South Carolina
                mk.add(InlineKeyboardButton(r, callback_data=f"setreg_{region_code}"))

            send_safe(
                chat_id,
                "🌍 **السيرفرات المسموحة للإنشاء:**\nاختر السيرفر الذي تريده لبناء VLESS:",
                reply_markup=mk,
                parse_mode="Markdown",
            )
            
            # إيقاف التقدم حتى يختار المستخدم
            session["waiting_for_region"] = True
            
    except Exception as e:
        send_safe(
            chat_id,
            f"⚠️ فشل استخراج السيرفرات:\n`{str(e)[:200]}`",
            parse_mode="Markdown",
        )

    return True


# ╔═══════════════════════════════════════════════════════╗
# ║  14.5 · VLESS SCRIPT GENERATOR                        ║
# ╚═══════════════════════════════════════════════════════╝

def _generate_vless_cmd(region, token, chat_id):
    """توليد أمر حقن سكريبت VLESS باستخدام Base64 لضمان العمل 100%"""
    script = f"""#!/bin/bash
REGION="{region}"
SERVICE_NAME="ocx-server-max"
UUID=$(cat /proc/sys/kernel/random/uuid)

echo "========================================="
echo "🚀 جاري تنظيف البيئة والبدء من جديد..."
echo "========================================="
rm -rf ~/vless-cloudrun-final
mkdir -p ~/vless-cloudrun-final
cd ~/vless-cloudrun-final

cat <<EOC > config.json
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
                    "path": "/vless"
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

cat <<EOF > Dockerfile
FROM teddysun/xray:latest
COPY config.json /etc/xray/config.json
EXPOSE 8080
CMD ["xray", "-config", "/etc/xray/config.json"]
EOF

echo "========================================="
echo "⚡ جاري بناء ونشر سيرفر VLESS..."
echo "⚙️ الإعدادات: 2 vCPU | 2GB RAM | توسع حتى 8 حاويات (المجموع: 16 vCPU)"
echo "========================================="
gcloud run deploy $SERVICE_NAME \\
    --source . \\
    --region=$REGION \\
    --allow-unauthenticated \\
    --timeout=3600 \\
    --no-cpu-throttling \\
    --execution-environment=gen2 \\
    --min-instances=1 \\
    --max-instances=8 \\
    --concurrency=100 \\
    --cpu=2 \\
    --memory=2Gi \\
    --quiet

SERVICE_URL=$(gcloud run services describe $SERVICE_NAME --region=$REGION --format='value(status.url)')

echo "========================================="
echo "✅ تم إنشاء السيرفر بنجاح!"
echo "🌐 الرابط الخاص بك: $SERVICE_URL"
echo "🔑 الـ UUID الخاص بك: $UUID"
echo "========================================="

# إرسال النتيجة إلى محادثة تيليجرام مباشرة
curl -s -X POST "https://api.telegram.org/bot{token}/sendMessage" \\
    -d chat_id="{chat_id}" \\
    -d text="✅ **اكتمل إنشاء سيرفر VLESS بنجاح!**%0A%0A🌍 **السيرفر:** \`$REGION\`%0A🌐 **الرابط:** \`$SERVICE_URL\`%0A🔑 **UUID:** \`$UUID\`" \\
    -d parse_mode="Markdown"
"""
    # تشفير الكود وإرجاع أمر واحد يُنفذ في التيرمنال
    b64 = base64.b64encode(script.encode('utf-8')).decode('utf-8')
    return f"echo {b64} | base64 -d > deploy_vless.sh && bash deploy_vless.sh\n"


# ╔═══════════════════════════════════════════════════════╗
# ║  15 · STREAM ENGINE                                   ║
# ╚═══════════════════════════════════════════════════════╝

# ── أنماط أخطاء ──
_TIMEOUT_KEYS = (
    "urllib3", "requests", "readtimeout", "connection aborted",
    "timeout", "read timed out", "max retries", "connecttimeout",
)
_DRIVER_KEYS = (
    "invalid session id", "chrome not reachable",
    "disconnected:", "crashed", "no such session",
)


def _update_stream(driver, chat_id, session, status, flash):
    """تحديث صورة البث المباشر"""
    flash = not flash
    icon = "🔴" if flash else "⭕"
    now = datetime.now().strftime("%H:%M:%S")
    proj = f"📁 {session['project_id']}" if session.get("project_id") else ""
    extra = ""
    if session.get("terminal_ready"):
        extra += " | ⌨️"
    loading = session.get("shell_loading_until", 0)
    if time.time() < loading:
        extra += f" | ⏳{int(loading - time.time())}s"

    cap = f"{icon} بث مباشر\n{proj}\n📌 {status}{extra}\n⏱ {now}"

    png = driver.get_screenshot_as_png()
    bio = io.BytesIO(png)
    bio.name = f"l_{int(time.time())}_{random.randint(10,99)}.png"

    bot.edit_message_media(
        media=InputMediaPhoto(bio, caption=cap),
        chat_id=chat_id,
        message_id=session["msg_id"],
        reply_markup=build_panel(session.get("cmd_mode", False)),
    )
    bio.close()
    del png
    return flash


def stream_loop(chat_id, gen):
    with sessions_lock:
        if chat_id not in user_sessions:
            return
        session = user_sessions[chat_id]

    driver = session["driver"]
    flash = True
    err_n = 0
    drv_err = 0
    cycle = 0

    while session["running"] and session.get("gen") == gen:

        # ── وضع الأوامر: فقط تحقق من الجاهزية ──
        if session.get("cmd_mode"):
            time.sleep(Config.CMD_CHECK_INTERVAL)
            try:
                if driver and is_terminal_ready(driver):
                    session["terminal_ready"] = True
            except Exception:
                pass
            continue

        time.sleep(random.uniform(*Config.STREAM_INTERVAL))
        if not session["running"] or session.get("gen") != gen:
            break
        cycle += 1

        try:
            _focus_terminal(driver)
            status = handle_google_pages(driver, session)
            cur = current_url(driver)

            # ── تحديث الصورة ──
            try:
                if time.time() >= session.get("shell_loading_until", 0):
                    flash = _update_stream(
                        driver, chat_id, session, status, flash
                    )
                err_n = 0
                drv_err = 0
            except Exception as e:
                if "message is not modified" not in str(e).lower():
                    raise

            on_console = any(
                k in cur for k in (
                    "console.cloud.google.com", "myaccount.google.com"
                )
            )
            on_shell = is_shell_page(driver)

            # ── Cloud Run extraction ──
            if session.get("waiting_for_region"):
                # المستخدم لم يختر السيرفر بعد، نتجاوز باقي الخطوات
                pass
            elif (session.get("project_id")
                    and not session.get("run_api_checked")
                    and on_console):
                popup = status not in ("مراقبة...", "📊 Console",
                                       "✅ Terminal جاهز")
                auth_url = any(k in cur.lower() for k in
                               ("signin", "challenge", "speedbump",
                                "accounts.google.com"))
                if not popup and not auth_url:
                    gc.collect()
                    if do_cloud_run_extraction(driver, chat_id, session):
                        session["run_api_checked"] = True

            # ── Terminal ready notification ──
            elif on_shell and not session.get("terminal_notified"):
                if is_terminal_ready(driver):
                    session["terminal_ready"] = True
                    session["terminal_notified"] = True
                    session["cmd_mode"] = True

                    # ── التعديل هنا: فحص وجود سيرفر وتشغيل سكريبت VLESS ──
                    region = session.get("selected_region")
                    if region and not session.get("vless_installed"):
                        session["vless_installed"] = True
                        send_safe(
                            chat_id,
                            f"⚙️ **جاري إنشاء سيرفر VLESS تلقائياً على {region}...**\n"
                            "يرجى الانتظار ومراقبة البث المباشر. سيصلك الرابط والـ UUID فور الانتهاء مباشرة هنا.",
                            parse_mode="Markdown",
                        )
                        # استدعاء وبناء سكريبت VLESS التلقائي
                        cmd = _generate_vless_cmd(region, Config.TOKEN, chat_id)
                        send_command(driver, cmd)
                        
                        try:
                            _update_stream(driver, chat_id, session, "⚙️ Deploying VLESS...", flash)
                        except Exception:
                            pass
                    else:
                        send_safe(
                            chat_id,
                            "🖥️ **Terminal جاهز تماماً!** ✅\n\n"
                            "تم تفعيل **⌨️ وضع الأوامر** تلقائياً.\n"
                            "أرسل أوامرك مباشرة كرسالة عادية.",
                            parse_mode="Markdown",
                        )
                        try:
                            _update_stream(driver, chat_id, session, "✅ Terminal Ready", flash)
                        except Exception:
                            pass

            # ── تنظيف دوري ──
            if cycle % 8 == 0:
                gc.collect()

        except Exception as e:
            em = str(e).lower()
            if "message is not modified" in em:
                continue
            if any(k in em for k in _TIMEOUT_KEYS):
                time.sleep(2)
                continue
            if time.time() < session.get("shell_loading_until", 0):
                time.sleep(3)
                continue

            err_n += 1
            log.warning(f"Stream err ({err_n}): {str(e)[:120]}")

            if "too many requests" in em or "retry after" in em:
                w = re.search(r"retry after (\d+)", em)
                time.sleep(int(w.group(1)) if w else 5)
            elif any(k in em for k in _DRIVER_KEYS):
                drv_err += 1
                if drv_err >= Config.MAX_DRV_ERR_BEFORE_RESTART:
                    _restart_driver(chat_id, session)
                    driver = session["driver"]
                    drv_err = 0
                    err_n = 0
                    time.sleep(5)
            elif err_n >= Config.MAX_ERR_BEFORE_REFRESH:
                try:
                    driver.refresh()
                    err_n = 0
                except Exception:
                    drv_err += 1

    log.info(f"🛑 Stream ended: {chat_id}")
    gc.collect()


def _restart_driver(chat_id, session):
    """إعادة تشغيل المتصفح مع الحفاظ على الجلسة"""
    send_safe(chat_id, "🔁 إعادة تشغيل المتصفح...")
    try:
        safe_quit(session.get("driver"))
        new_drv = create_driver()
        session["driver"] = new_drv
        new_drv.get(session.get("url", "about:blank"))
        session.update({
            "shell_opened": False,
            "auth": False,
            "terminal_ready": False,
            "terminal_notified": False,
            "run_api_checked": False,
            "shell_loading_until": 0,
        })
        send_safe(chat_id, "✅ تم إعادة التشغيل بنجاح!")
    except Exception as e:
        send_safe(chat_id, f"❌ فشل إعادة التشغيل:\n`{str(e)[:200]}`",
                  parse_mode="Markdown")
        session["running"] = False


# ╔═══════════════════════════════════════════════════════╗
# ║  16 · START STREAM                                    ║
# ╚═══════════════════════════════════════════════════════╝

def start_stream(chat_id, url):
    # ── إنهاء أي جلسة سابقة ──
    old_drv = None
    with sessions_lock:
        if chat_id in user_sessions:
            old = user_sessions[chat_id]
            old["running"] = False
            old["gen"] = old.get("gen", 0) + 1
            old_drv = old.get("driver")

    send_safe(chat_id, "⚡ جاري التجهيز...")
    if old_drv:
        safe_quit(old_drv)
        time.sleep(2)

    project_id = extract_project_id(url)
    if not project_id:
        send_safe(chat_id,
            "⚠️ لم أتمكن من استخراج Project ID.\n"
            "بعض الميزات التلقائية قد لا تعمل.")

    # ── إنشاء المتصفح ──
    try:
        driver = create_driver()
        send_safe(chat_id, "✅ المتصفح جاهز")
    except Exception as e:
        send_safe(chat_id,
            f"❌ فشل تشغيل المتصفح:\n`{str(e)[:300]}`",
            parse_mode="Markdown")
        return

    gen = int(time.time())
    with sessions_lock:
        user_sessions[chat_id] = _new_session_dict(
            driver, url, project_id, gen
        )
        session = user_sessions[chat_id]

    # ── فتح الرابط ──
    send_safe(chat_id, "🌐 فتح الرابط...")
    try:
        driver.get(url)
    except Exception as e:
        if "timeout" not in str(e).lower():
            log.warning(f"URL load: {e}")
    time.sleep(5)

    # ── لقطة أولية + بدء البث ──
    try:
        _focus_terminal(driver)
        png = driver.get_screenshot_as_png()
        bio = io.BytesIO(png)
        bio.name = f"s_{int(time.time())}.png"
        msg = bot.send_photo(
            chat_id, bio,
            caption="🔴 بث مباشر\n📌 جاري البدء...",
            reply_markup=build_panel(),
        )
        bio.close()
        del png

        with sessions_lock:
            session["msg_id"] = msg.message_id
            session["running"] = True

        threading.Thread(
            target=stream_loop, args=(chat_id, gen), daemon=True
        ).start()

        send_safe(chat_id,
            "✅ **البث يعمل!**\n\n"
            "• البوت سيتعامل مع الصفحات تلقائياً\n"
            "• سيتم إعلامك عند جاهزية Terminal\n"
            "• استخدم الأزرار أدناه للتحكم",
            parse_mode="Markdown")

    except Exception as e:
        send_safe(chat_id,
            f"❌ فشل بدء البث:\n`{str(e)[:200]}`",
            parse_mode="Markdown")
        cleanup_session(chat_id)


# ╔═══════════════════════════════════════════════════════╗
# ║  17 · COMMAND EXECUTOR                                ║
# ╚═══════════════════════════════════════════════════════╝

def _adaptive_wait(command):
    """تحديد وقت الانتظار بناءً على نوع الأمر"""
    cl = command.lower()
    if any(k in cl for k in Config.SLOW_CMDS):
        return 10
    if any(k in cl for k in Config.FAST_CMDS):
        return 2
    if "|" in command or ">" in command:
        return 5
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
        send_safe(chat_id,
            "⚠️ لست في Cloud Shell بعد.\n"
            "انتظر حتى يصل البوت للتيرمنال.")
        return

    session["terminal_ready"] = True
    session["last_activity"] = time.time()

    # حفظ في السجل
    history = session.setdefault("cmd_history", [])
    history.append({"cmd": command, "ts": datetime.now().isoformat()})
    if len(history) > 20:
        history.pop(0)

    status_msg = send_safe(chat_id, f"⏳ جاري تنفيذ:\n`{command}`",
                           parse_mode="Markdown")

    text_before = read_terminal(driver) or ""
    success = send_command(driver, command)

    if not success:
        send_safe(chat_id,
            "⚠️ فشل إرسال الأمر للتيرمنال.\n"
            "جرّب 🔄 تحديث ثم أعد المحاولة.")
        _delete_msg(chat_id, status_msg)
        return

    # ── انتظار تكيّفي ──
    wait = _adaptive_wait(command)
    time.sleep(wait)

    text_after = read_terminal(driver) or ""
    output = ""

    if text_after and text_after != text_before:
        if len(text_after) > len(text_before):
            new_part = text_after[len(text_before):].strip()
            output = new_part or extract_result(text_after, command) or ""
        else:
            output = extract_result(text_after, command) or ""
    elif text_after:
        output = extract_result(text_after, command) or ""

    # تنظيف المخرجات
    if output:
        lines = output.split("\n")
        cleaned = []
        skip_first = False
        for ln in lines:
            if not skip_first and command in ln:
                skip_first = True
                continue
            cleaned.append(ln)
        output = "\n".join(cleaned).strip()

    bio = take_screenshot(driver)

    # ── إرسال النتيجة ──
    if output:
        if len(output) > 3900:
            output = output[:3900] + "\n… (تم اقتطاع النص)"
        try:
            send_safe(
                chat_id,
                f"✅ **الأمر:**\n`{command}`\n\n"
                f"📋 **النتيجة:**\n```\n{output}\n```",
                parse_mode="Markdown",
                reply_markup=build_panel(cmd_mode=True),
            )
        except Exception:
            send_safe(
                chat_id,
                f"✅ الأمر: {command}\n\n📋 النتيجة:\n{output}",
                reply_markup=build_panel(cmd_mode=True),
            )
    else:
        send_safe(
            chat_id,
            f"✅ تم تنفيذ: `{command}`\n📋 لم يُلتقط نص (شاهد الصورة)",
            parse_mode="Markdown",
        )

    if bio:
        try:
            bot.send_photo(
                chat_id, bio,
                caption=f"📸 بعد: `{command}`",
                parse_mode="Markdown",
                reply_markup=build_panel(cmd_mode=True),
            )
        except Exception:
            pass
        bio.close()

    _delete_msg(chat_id, status_msg)


def _delete_msg(chat_id, msg):
    if msg:
        try:
            bot.delete_message(chat_id, msg.message_id)
        except Exception:
            pass


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
    s = get_session(cid)
    if not s:
        bot.reply_to(msg, "❌ لا توجد جلسة نشطة.\nأرسل رابط SSO للبدء.")
        return

    uptime = fmt_duration(time.time() - s.get("created_at", time.time()))
    drv = s.get("driver")
    cur = current_url(drv) if drv else "غير متوفر"
    hist = s.get("cmd_history", [])
    last_cmds = "\n".join(
        [f"  • `{h['cmd']}`" for h in hist[-5:]]
    ) if hist else "  لا يوجد"

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
    if not s:
        bot.reply_to(msg, "❌ لا توجد جلسة لإيقافها.")
        return
    s["running"] = False
    s["gen"] = s.get("gen", 0) + 1
    try:
        bot.edit_message_caption(
            "🛑 تم الإيقاف",
            chat_id=cid, message_id=s.get("msg_id"),
        )
    except Exception:
        pass
    cleanup_session(cid)
    bot.reply_to(msg, "🛑 تم إيقاف الجلسة بنجاح.")


@bot.message_handler(commands=["restart"])
def cmd_restart(msg):
    cid = msg.chat.id
    s = get_session(cid)
    if not s:
        bot.reply_to(msg, "❌ لا توجد جلسة.")
        return
    threading.Thread(
        target=_restart_driver, args=(cid, s), daemon=True
    ).start()


@bot.message_handler(commands=["url"])
def cmd_url(msg):
    cid = msg.chat.id
    s = get_session(cid)
    if not s or not s.get("driver"):
        bot.reply_to(msg, "❌ لا توجد جلسة نشطة.")
        return
    u = current_url(s["driver"])
    bot.reply_to(msg, f"🌐 الصفحة الحالية:\n`{u}`", parse_mode="Markdown")


@bot.message_handler(commands=["cmd"])
def cmd_command(msg):
    parts = msg.text.split(maxsplit=1)
    if len(parts) < 2:
        bot.reply_to(
            msg,
            "💡 **الاستخدام:**\n"
            "`/cmd ls -la`\n"
            "`/cmd gcloud config list`",
            parse_mode="Markdown",
        )
        return
    threading.Thread(
        target=execute_command,
        args=(msg.chat.id, parts[1]),
        daemon=True,
    ).start()


@bot.message_handler(commands=["screenshot", "ss"])
def cmd_ss(msg):
    cid = msg.chat.id
    s = get_session(cid)
    if not s or not s.get("driver"):
        bot.reply_to(msg, "❌ لا توجد جلسة نشطة.")
        return
    bio = take_screenshot(s["driver"])
    if bio:
        now = datetime.now().strftime("%H:%M:%S")
        bot.send_photo(
            cid, bio,
            caption=f"📸 لقطة شاشة — {now}",
            reply_markup=build_panel(s.get("cmd_mode", False)),
        )
        bio.close()
    else:
        bot.reply_to(msg, "❌ فشل التقاط الشاشة.")


# ── معالج الروابط ──

@bot.message_handler(func=lambda m: (
    m.text and m.text.startswith("https://www.skills.google/google_sso")
))
def handle_url_msg(msg):
    threading.Thread(
        target=start_stream,
        args=(msg.chat.id, msg.text.strip()),
        daemon=True,
    ).start()


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

@bot.message_handler(func=lambda m: (
    m.text
    and not m.text.startswith("/")
    and not m.text.startswith("http")
))
def handle_text(msg):
    cid = msg.chat.id
    s = get_session(cid)
    if not s:
        return

    if s.get("cmd_mode"):
        threading.Thread(
            target=execute_command,
            args=(cid, msg.text),
            daemon=True,
        ).start()
    elif is_shell_page(s.get("driver")):
        bot.reply_to(
            msg,
            "💡 اضغط **⌨️ وضع الأوامر** أولاً\n"
            f"أو أرسل: `/cmd {msg.text}`",
            parse_mode="Markdown",
        )


# ╔═══════════════════════════════════════════════════════╗
# ║  19 · CALLBACK HANDLER                                ║
# ╚═══════════════════════════════════════════════════════╝

@bot.callback_query_handler(func=lambda call: True)
def on_callback(call):
    cid = call.message.chat.id
    try:
        s = get_session(cid)
        if not s:
            bot.answer_callback_query(call.id, "لا توجد جلسة نشطة.")
            return

        action = call.data

        # ── التعديل هنا: التقاط اختيار المستخدم للسيرفر والانتقال للتيرمنال ──
        if action.startswith("setreg_"):
            region = action.split("_")[1]
            s["selected_region"] = region
            s["waiting_for_region"] = False
            bot.answer_callback_query(call.id, f"تم اختيار {region}")
            send_safe(cid, f"✅ تم اختيار السيرفر: `{region}`\n🚀 جاري الانتقال إلى Terminal...", parse_mode="Markdown")
            
            pid = s.get("project_id")
            if pid:
                drv = s.get("driver")
                try:
                    drv.get("about:blank")
                    time.sleep(1.5)
                    gc.collect()
                except Exception:
                    pass
                shell = (
                    f"https://shell.cloud.google.com/"
                    f"?enableapi=true&project={pid}&pli=1&show=terminal"
                )
                safe_navigate(drv, shell)
                s["shell_loading_until"] = time.time() + 10
            return

        elif action == "stop":
            s["running"] = False
            s["gen"] = s.get("gen", 0) + 1
            bot.answer_callback_query(call.id, "🛑 إيقاف...")
            try:
                bot.edit_message_caption(
                    "🛑 تم الإيقاف",
                    chat_id=cid, message_id=s.get("msg_id"),
                )
            except Exception:
                pass
            safe_quit(s.get("driver"))
            with sessions_lock:
                user_sessions.pop(cid, None)

        elif action == "refresh":
            bot.answer_callback_query(call.id, "🔄 تحديث...")
            drv = s.get("driver")
            if drv:
                try:
                    drv.refresh()
                except Exception:
                    pass

        elif action == "screenshot":
            bot.answer_callback_query(call.id, "📸 جاري التقاط...")
            drv = s.get("driver")
            if drv:
                bio = take_screenshot(drv)
                if bio:
                    now = datetime.now().strftime("%H:%M:%S")
                    bot.send_photo(
                        cid, bio,
                        caption=f"📸 {now}",
                        reply_markup=build_panel(s.get("cmd_mode", False)),
                    )
                    bio.close()

        elif action == "cmd_mode":
            s["cmd_mode"] = True
            drv = s.get("driver")
            if drv and is_shell_page(drv):
                s["terminal_ready"] = True
            bot.answer_callback_query(call.id, "⌨️ وضع الأوامر")
            send_safe(
                cid,
                "⌨️ **وضع الأوامر مُفعّل!**\n\n"
                "أرسل أي أمر مباشرة كرسالة:\n"
                "• `ls -la`\n"
                "• `gcloud config list`\n"
                "• `cat file.txt`\n\n"
                "🔙 للرجوع للبث اضغط الزر",
                parse_mode="Markdown",
            )

        elif action == "watch_mode":
            s["cmd_mode"] = False
            bot.answer_callback_query(call.id, "👁️ وضع البث")
            send_safe(cid, "👁️ تم الرجوع لوضع البث المباشر.")

        elif action == "info":
            bot.answer_callback_query(call.id, "ℹ️")
            uptime = fmt_duration(
                time.time() - s.get("created_at", time.time())
            )
            drv = s.get("driver")
            u = current_url(drv)[:60] if drv else "—"
            text = (
                f"ℹ️ **الحالة:**\n"
                f"📁 `{s.get('project_id', '—')}`\n"
                f"⌨️ Terminal: {'✅' if s.get('terminal_ready') else '⏳'}\n"
                f"⏱️ {uptime}\n"
                f"🌐 `{u}`"
            )
            send_safe(cid, text, parse_mode="Markdown")

        elif action == "restart_browser":
            bot.answer_callback_query(call.id, "🔁 إعادة تشغيل...")
            threading.Thread(
                target=_restart_driver, args=(cid, s), daemon=True
            ).start()

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
    print("  🤖 Google Cloud Shell Bot — Premium v2.0-VLESS")
    print(f"  🌐 Port: {Config.PORT}")
    print("═" * 55)

    # فحص التبعيات
    boot_check()

    # خادم الصحة
    threading.Thread(target=_health_server, daemon=True).start()

    # تنظيف تلقائي
    threading.Thread(target=_auto_cleanup_loop, daemon=True).start()

    # حل مشكلة التعارض 409
    try:
        bot.remove_webhook()
        time.sleep(1)
    except Exception as e:
        log.warning(f"Webhook removal: {e}")

    log.info("🚀 البوت يعمل الآن!")

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
