"""
╔══════════════════════════════════════════════════════════╗
║  🤖 Google Cloud Shell — Telegram Bot                    ║
║  📌 Premium Edition v3.1 (Regions + Short UI + Cookies)  ║
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
    TOKEN = os.environ.get("BOT_TOKEN")
    PORT = int(os.environ.get("PORT", 8080))
    MONGO_URI = os.environ.get("MONGO_URI", "")
    VERSION = "3.1-PRO-Edition"

    PAGE_LOAD_TIMEOUT = 45
    SCRIPT_TIMEOUT = 20
    WINDOW_SIZE = (1024, 768)

    STREAM_INTERVAL = (4, 6)
    CMD_CHECK_INTERVAL = 3
    SESSION_MAX_AGE_HOURS = 4
    CLEANUP_INTERVAL_SEC = 1800

    MAX_ERR_BEFORE_REFRESH = 5
    MAX_DRV_ERR_BEFORE_RESTART = 3

    SLOW_CMDS = ("install", "apt", "pip", "gcloud", "docker", "kubectl", "terraform", "build", "deploy", "npm", "yarn", "wget", "curl", "git clone")
    FAST_CMDS = ("cat", "echo", "ls", "pwd", "whoami", "date", "hostname", "uname", "id", "env", "which", "type", "head", "tail", "wc")


# ╔═══════════════════════════════════════════════════════╗
# ║  2 · LOGGING & GLOBAL STATE                           ║
# ╚═══════════════════════════════════════════════════════╝

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s", datefmt="%H:%M:%S")
log = logging.getLogger("CSBot")

if not Config.TOKEN:
    log.critical("❌ BOT_TOKEN غير موجود!")
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
        log.info("✅ MongoDB Connected")
    except Exception as e:
        log.error(f"❌ MongoDB Failed: {e}")

user_sessions = {}
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
        if not cookies: return
        if users_col is not None:
            users_col.update_one({"_id": chat_id}, {"$set": {"cookies": cookies}}, upsert=True)
        else:
            session_cookies[chat_id] = cookies
        log.info(f"🍪 Cookies Saved: {chat_id}")
    except: pass

def load_user_cookies(driver, chat_id):
    try:
        cookies = users_col.find_one({"_id": chat_id}).get("cookies") if users_col is not None else session_cookies.get(chat_id)
        if cookies:
            driver.get("https://myaccount.google.com/")
            time.sleep(1)
            for cookie in cookies:
                if 'expiry' in cookie: cookie['expiry'] = int(cookie['expiry'])
                try: driver.add_cookie(cookie)
                except: continue
            log.info(f"🍪 Cookies Injected: {chat_id}")
            return True
    except: pass
    return False


# ╔═══════════════════════════════════════════════════════╗
# ║  4 · HEALTH SERVER & DISPLAY                          ║
# ╚═══════════════════════════════════════════════════════╝

class HealthHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        if self.path in ("/", "/health"):
            self.send_response(200)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.end_headers()
            payload = json.dumps({"status": "running", "version": Config.VERSION, "queue": deployment_queue.qsize()})
            self.wfile.write(payload.encode())
        else:
            self.send_response(404)
            self.end_headers()
    def log_message(self, *_): pass

def _health_server():
    try: HTTPServer(("0.0.0.0", Config.PORT), HealthHandler).serve_forever()
    except: pass

display = None
for size, depth in [(Config.WINDOW_SIZE, 16), ((800, 600), 24)]:
    try:
        display = Display(visible=0, size=size, color_depth=depth)
        display.start()
        break
    except: continue


# ╔═══════════════════════════════════════════════════════╗
# ║  5 · UTILITIES & BROWSER FACTORY                      ║
# ╚═══════════════════════════════════════════════════════╝

def find_path(names, extras=None):
    for n in names:
        p = shutil.which(n)
        if p: return p
    for p in extras or []:
        if os.path.isfile(p): return p
    return None

def browser_version(path):
    try:
        r = subprocess.run([path, "--version"], capture_output=True, text=True, timeout=5)
        m = re.search(r"(\d+)", r.stdout)
        return m.group(1) if m else "120"
    except: return "120"

PATCHED_DRIVER_PATH = None
def patch_driver(orig):
    global PATCHED_DRIVER_PATH
    with chromedriver_lock:
        if PATCHED_DRIVER_PATH and os.path.exists(PATCHED_DRIVER_PATH): return PATCHED_DRIVER_PATH
        dst = f"/tmp/chromedriver_patched_{os.getpid()}_{random.randint(1000, 9999)}"
        try:
            with open(orig, "rb") as f: data = f.read()
            cnt = data.count(b"cdc_")
            if cnt: data = data.replace(b"cdc_", b"aaa_")
            with open(dst, "wb") as f: f.write(data)
            os.chmod(dst, 0o755)
            PATCHED_DRIVER_PATH = dst
        except: return orig
    return dst

def safe_navigate(driver, url):
    try: driver.get(url); return True
    except: return False

def current_url(driver):
    try: return driver.current_url
    except: return ""

def extract_project_id(url):
    for pat in [r"(qwiklabs-gcp-[\w-]+)", r"project[=/]([\w-]+)", r"(gcp-[\w-]+)"]:
        m = re.search(pat, url)
        if m: return m.group(1)
    return None

def fmt_duration(secs):
    if secs < 60: return f"{int(secs)}ث"
    if secs < 3600: return f"{int(secs // 60)}د {int(secs % 60)}ث"
    return f"{int(secs // 3600)}س {int((secs % 3600) // 60)}د"

def send_safe(chat_id, text, **kw):
    try: return bot.send_message(chat_id, text, **kw)
    except: return None

def edit_safe(chat_id, message_id, text, **kw):
    try: return bot.edit_message_text(chat_id=chat_id, message_id=message_id, text=text, **kw)
    except: return None

STEALTH_JS = r"""
Object.defineProperty(navigator,'webdriver',{get:()=>undefined});
Object.defineProperty(navigator,'languages',{get:()=>['en-US','en']});
Object.defineProperty(navigator,'platform',{get:()=>'Win32'});
for(var p in window){if(/^cdc_/.test(p)){try{delete window[p]}catch(e){}}}
"""

def create_driver():
    browser = find_path(["chromium", "chromium-browser"], ["/usr/bin/chromium", "/usr/bin/chromium-browser"])
    drv = find_path(["chromedriver"], ["/usr/bin/chromedriver", "/usr/lib/chromium/chromedriver"])
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
    opts.add_argument("--lang=en-US")

    for flag in ["--no-sandbox", "--disable-dev-shm-usage", "--disable-gpu", '--js-flags="--max-old-space-size=256"', "--disable-notifications", f"--window-size={Config.WINDOW_SIZE[0]},{Config.WINDOW_SIZE[1]}", "--mute-audio"]:
        opts.add_argument(flag)

    opts.page_load_strategy = "eager"
    driver = webdriver.Chrome(service=Service(executable_path=patched), options=opts)
    try: driver.execute_cdp_cmd("Page.addScriptToEvaluateOnNewDocument", {"source": STEALTH_JS})
    except: pass
    driver.set_page_load_timeout(Config.PAGE_LOAD_TIMEOUT)
    return driver


# ╔═══════════════════════════════════════════════════════╗
# ║  6 · UI COMPONENTS & MESSAGES                         ║
# ╚═══════════════════════════════════════════════════════╝

def build_panel(cmd_mode=False):
    mk = InlineKeyboardMarkup(row_width=2)
    if cmd_mode: mk.row(InlineKeyboardButton("📸 لقطة شاشة", callback_data="screenshot"), InlineKeyboardButton("🔙 بث مباشر", callback_data="watch_mode"))
    else: mk.row(InlineKeyboardButton("⌨️ أوامر", callback_data="cmd_mode"), InlineKeyboardButton("📸 لقطة شاشة", callback_data="screenshot"))
    mk.row(InlineKeyboardButton("🔄 تحديث", callback_data="refresh"), InlineKeyboardButton("ℹ️ حالة", callback_data="info"))
    mk.row(InlineKeyboardButton("🔁 إعادة تشغيل", callback_data="restart_browser"), InlineKeyboardButton("⏹ إيقاف", callback_data="stop"))
    return mk

WELCOME_MSG = """🤖 **بوت Cloud Shell السريع** ⚡

أرسل رابط `SSO` الخاص بك للبدء.
البوت سيتخطى تسجيل الدخول، يستخرج السيرفرات، ويبني `VLESS` + لوحة `3X-UI` تلقائياً!

📌 **أوامر سريعة:**
`/status` ℹ️ حالة الجلسة
`/stop` 🛑 إيقاف
`/clearcookies` 🗑️ مسح الجلسة لحساب جديد
"""

HELP_MSG = """📖 **المساعدة**
1️⃣ أرسل رابط SSO الذي يبدأ بـ `https://www.skills.google/google_sso...`
2️⃣ اختر السيرفر.
3️⃣ انتظر الرابط الجاهز!

⚙️ **أوامر:**
`/cmd [أمر]` ← تنفيذ أمر Terminal
`/ss` ← لقطة شاشة
`/clearcookies` ← بدء حساب جديد
"""


# ╔═══════════════════════════════════════════════════════╗
# ║  7 · SHELL & TERMINAL INTERACTION                     ║
# ╚═══════════════════════════════════════════════════════╝

def is_shell_page(driver):
    try: return "shell.cloud.google.com" in driver.current_url or "ide.cloud.google.com" in driver.current_url
    except: return False

def is_terminal_ready(driver):
    if not is_shell_page(driver): return False
    try:
        return driver.execute_script("""
            var rows = document.querySelectorAll('.xterm-rows > div');
            if (!rows.length) return false;
            for (var i = 0; i < rows.length; i++) {
                var t = (rows[i].textContent || '');
                if (t.indexOf('$') !== -1 || t.indexOf('@') !== -1 || t.indexOf('#') !== -1) return true;
            } return false; """)
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
    var ta = document.querySelector('.xterm-helper-textarea');
    if (!ta) {
        var frames = document.querySelectorAll('iframe');
        for (var i=0; i<frames.length; i++) {
            try { ta = frames[i].contentDocument.querySelector('.xterm-helper-textarea'); if(ta) break; } catch(e) {}
        }
    }
    if (ta) {
        ta.focus();
        var dt = new DataTransfer(); dt.setData('text/plain', arguments[0] + '\\n'); 
        ta.dispatchEvent(new ClipboardEvent('paste', { clipboardData: dt, bubbles: true }));
        return true;
    } return false; """
    
    try:
        if driver.execute_script(js_paste, command_clean):
            time.sleep(0.5)
            driver.switch_to.default_content()
            try: driver.find_element(By.CSS_SELECTOR, '.xterm-helper-textarea').send_keys(Keys.RETURN)
            except: pass
            return True
    except: pass
    return False

def read_terminal(driver):
    if not driver: return None
    try: return driver.execute_script("var s=document.querySelector('.xterm-screen'); return s ? (s.textContent||s.innerText) : null;")
    except: return None

def take_screenshot(driver):
    if not driver: return None
    try:
        _focus_terminal(driver)
        png = driver.get_screenshot_as_png()
        bio = io.BytesIO(png)
        bio.name = f"ss_{int(time.time())}.png"
        return bio
    except: return None


# ╔═══════════════════════════════════════════════════════╗
# ║  8 · GOOGLE PAGES HANDLER                             ║
# ╚═══════════════════════════════════════════════════════╝

def _click_if_visible(driver, xpaths):
    for xp in xpaths:
        try:
            btns = driver.find_elements(By.XPATH, xp)
            for btn in btns:
                if btn.is_displayed():
                    try: btn.click()
                    except: driver.execute_script("arguments[0].click();", btn)
                    return True
        except: pass
    return False

def handle_google_pages(driver, session, chat_id):
    status = "مراقبة..."
    try: body = driver.find_element(By.TAG_NAME, "body").text[:5000].lower()
    except: return status

    try:
        if "email" in session.get("waiting_for_input", "") or driver.find_elements(By.XPATH, "//input[@type='email']"):
            if session.get("waiting_for_input") != "email":
                session["waiting_for_input"] = "email"
                send_safe(chat_id, "⚠️ **تسجيل الدخول مطلوب!**\n👉 أرسل **اسم المستخدم (Username)**:")
            return "🔐 ننتظر الإيميل..."
        if "password" in session.get("waiting_for_input", "") or driver.find_elements(By.XPATH, "//input[@type='password']"):
            if session.get("waiting_for_input") != "password":
                session["waiting_for_input"] = "password"
                send_safe(chat_id, "🔐 👉 أرسل **كلمة المرور (Password)**:")
            return "🔐 ننتظر الباسورد..."
    except: pass

    if "agree and continue" in body and "terms of service" in body:
        _click_if_visible(driver, ["//mat-checkbox", "//input[@type='checkbox']"])
        if _click_if_visible(driver, ["//button[contains(translate(.,'A-Z','a-z'),'agree and continue')]"]): return "✅ شروط مقبولة"

    if "welcome to your new account" in body or "i understand" in body:
        if _click_if_visible(driver, ["//span[text()='I understand']", "//button[contains(.,'I understand')]"]): return "✅ شروط الحساب"

    if "authorize cloud shell" in body:
        if _click_if_visible(driver, ["//button[normalize-space(.)='Authorize']"]): return "✅ تفويض"

    if "cloud shell" in body and "continue" in body and "free" in body:
        if _click_if_visible(driver, ["//a[contains(text(),'Continue')]", "//button[contains(text(),'Continue')]"]): return "✅ Continue"

    if "verify it" in body:
        if _click_if_visible(driver, ["//button[contains(.,'Continue')]"]): return "✅ Verify"

    if "trust project" in body:
        if _click_if_visible(driver, ["//button[contains(.,'Trust')]"]): return "✅ Trust"

    u = current_url(driver)
    if "shell.cloud.google.com" in u: return "✅ Terminal"
    if "console.cloud.google.com" in u: return "📊 Console"
    return status


# ╔═══════════════════════════════════════════════════════╗
# ║  9 · CLOUD RUN REGION EXTRACTION (CATEGORIZED)        ║
# ╚═══════════════════════════════════════════════════════╝

REGION_JS = """
var cb = arguments[arguments.length - 1];
setTimeout(function() {
    var clicked = false;
    var dd = document.querySelectorAll('mat-select, [role="combobox"]');
    for (var i=0; i<dd.length; i++) { if((dd[i].getAttribute('aria-label')||'').toLowerCase().includes('region')) { dd[i].click(); clicked = true; break; } }
    if (!clicked) return cb('NO_DROPDOWN');
    setTimeout(function() {
        var opts = document.querySelectorAll('mat-option, [role="option"]');
        var res = [];
        for (var k=0; k<opts.length; k++) {
            if (opts[k].getBoundingClientRect().height > 0 && !opts[k].classList.contains('mat-option-disabled')) {
                var t = (opts[k].innerText || '').trim().split('\\n')[0];
                if (t && t.includes('-') && !t.toLowerCase().includes('learn')) res.push(t);
            }
        }
        document.querySelector('.cdk-overlay-backdrop')?.click();
        cb(res.length ? res.join('\\n') : 'NO_REGIONS');
    }, 1500);
}, 4000);
"""

def do_cloud_run_extraction(driver, chat_id, session):
    pid = session.get("project_id")
    if not pid: return True

    if "run/create" not in current_url(driver):
        msg_text = "🔍 جاري استخراج السيرفرات..."
        if not session.get("status_msg_id"):
            msg = send_safe(chat_id, msg_text)
            if msg: session["status_msg_id"] = msg.message_id
        else: edit_safe(chat_id, session["status_msg_id"], msg_text)
        safe_navigate(driver, f"https://console.cloud.google.com/run/create?enableapi=true&project={pid}")
        return False

    try:
        driver.set_script_timeout(Config.SCRIPT_TIMEOUT)
        result = driver.execute_async_script(REGION_JS)

        if not result or result in ("NO_DROPDOWN", "NO_REGIONS") or result.startswith("ERROR"):
            edit_safe(chat_id, session.get("status_msg_id"), "⚠️ تعذر جلب السيرفرات، سيتم التخطي.")
        else:
            regions = [r.strip() for r in result.split("\n") if r.strip()]
            
            # 💡 تصنيف السيرفرات بشكل جميل ومرتب
            categorized = {
                "🌎 أمريكا (US/Americas)": [],
                "🇪🇺 أوروبا (Europe)": [],
                "🌏 آسيا وأستراليا (Asia/PAC)": [],
                "🌍 الشرق الأوسط وأفريقيا (MEA)": [],
                "🌐 أخرى (Others)": []
            }
            
            for r in regions:
                name = r.split()[0].lower()
                if name.startswith(("us-", "northamerica-", "southamerica-")): categorized["🌎 أمريكا (US/Americas)"].append(r)
                elif name.startswith("europe-"): categorized["🇪🇺 أوروبا (Europe)"].append(r)
                elif name.startswith(("asia-", "australia-")): categorized["🌏 آسيا وأستراليا (Asia/PAC)"].append(r)
                elif name.startswith(("me-", "africa-")): categorized["🌍 الشرق الأوسط وأفريقيا (MEA)"].append(r)
                else: categorized["🌐 أخرى (Others)"].append(r)
            
            mk = InlineKeyboardMarkup(row_width=2)
            for cat_name, cat_regs in categorized.items():
                if cat_regs:
                    mk.row(InlineKeyboardButton(f"── {cat_name} ──", callback_data="ignore"))
                    buttons = [InlineKeyboardButton(r.split()[0], callback_data=f"setreg_{r.split()[0]}") for r in cat_regs]
                    for i in range(0, len(buttons), 2): mk.row(*buttons[i:i+2])

            msg_text = "🌍 **تم استخراج السيرفرات!**\nاختر السيرفر الذي تريده:\n\n⏱️ *لديك 30 ثانية للاختيار*"
            if session.get("status_msg_id"): edit_safe(chat_id, session["status_msg_id"], msg_text, reply_markup=mk, parse_mode="Markdown")
            else:
                msg = send_safe(chat_id, msg_text, reply_markup=mk, parse_mode="Markdown")
                if msg: session["status_msg_id"] = msg.message_id
            
            session["waiting_for_region"] = True
            session["region_prompt_time"] = time.time()
    except: pass
    return True


# ╔═══════════════════════════════════════════════════════╗
# ║  10 · VLESS SCRIPT GENERATOR                          ║
# ╚═══════════════════════════════════════════════════════╝

def _generate_vless_cmd(region, token, chat_id):
    raw_script = """#!/bin/bash
REGION="<<REGION>>"
SERVICE_NAME="ocx-server-max"
UUID=$(cat /proc/sys/kernel/random/uuid)

mkdir -p ~/vless-cloudrun-final
cd ~/vless-cloudrun-final

cat << 'EOC' > config.json
{ "inbounds": [ { "port": 8080, "protocol": "vless", "settings": { "clients": [ { "id": "REPLACE_UUID", "level": 0 } ], "decryption": "none" }, "streamSettings": { "network": "ws", "wsSettings": { "path": "/@O_C_X7" } } } ], "outbounds": [ { "protocol": "freedom", "settings": {} } ] }
EOC
sed -i "s/REPLACE_UUID/$UUID/g" config.json

cat << 'EOF' > Dockerfile
FROM teddysun/xray:latest
COPY config.json /etc/xray/config.json
EXPOSE 8080
CMD ["xray", "-config", "/etc/xray/config.json"]
EOF

gcloud run deploy $SERVICE_NAME --source . --region=$REGION --allow-unauthenticated --timeout=3600 --no-cpu-throttling --execution-environment=gen2 --min-instances=1 --max-instances=8 --concurrency=250 --cpu=2 --memory=4096Mi --quiet >/dev/null 2>&1

PROJECT_ID=$(gcloud config get-value project)
PROJECT_NUM=$(gcloud projects describe $PROJECT_ID --format='value(projectNumber)')
DETERMINISTIC_HOST="${SERVICE_NAME}-${PROJECT_NUM}.${REGION}.run.app"
VLESS_LINK="vless://${UUID}@googlevideo.com:443?path=/%40O_C_X7&security=tls&encryption=none&host=${DETERMINISTIC_HOST}&type=ws&sni=googlevideo.com#𝗢 𝗖 𝗫 ⚡"

sudo pkill -9 xray 2>/dev/null; sudo pkill -9 x-ui 2>/dev/null; sudo fuser -k 8080/tcp 2>/dev/null; sudo fuser -k 2096/tcp 2>/dev/null
wget -qO install.sh https://raw.githubusercontent.com/mhsanaei/3x-ui/master/install.sh
echo -e "y\n8080\n2\n\n\n" | sudo bash install.sh > /dev/null 2>&1
sudo pkill -9 xray 2>/dev/null; sudo pkill -9 x-ui 2>/dev/null; sudo fuser -k 8080/tcp 2>/dev/null; sudo fuser -k 2096/tcp 2>/dev/null
nohup sudo /usr/local/x-ui/x-ui > /dev/null 2>&1 &

for i in {1..20}; do
    if curl -s http://127.0.0.1:8080 > /dev/null; then break; fi
    sleep 2
done
sleep 3 

USERNAME=$(sudo sqlite3 /etc/x-ui/x-ui.db "SELECT value FROM settings WHERE key='username';" 2>/dev/null)
PASSWORD=$(sudo sqlite3 /etc/x-ui/x-ui.db "SELECT value FROM settings WHERE key='password';" 2>/dev/null)
BASEPATH=$(sudo sqlite3 /etc/x-ui/x-ui.db "SELECT value FROM settings WHERE key='webBasePath';" 2>/dev/null)
CLEAN_PATH=$(echo "$BASEPATH" | tr -d '/')

if [ -n "$WEB_HOST" ]; then
    PANEL_LINK="https://8080-${WEB_HOST}/${CLEAN_PATH}/"
else
    CS_URL=$(cloudshell get-web-preview-url --port 8080 | sed 's|/$||')
    PANEL_LINK="${CS_URL}/${CLEAN_PATH}/"
fi

MSG="✅ <b>تم التجهيز بنجاح!</b> 🚀

🌐 <b>VLESS (Cloud Run):</b>
<pre>${VLESS_LINK}</pre>

📊 <b>3X-UI Panel:</b>
${PANEL_LINK}

🔑 <b>دخول اللوحة:</b> <code>${USERNAME}</code> | <code>${PASSWORD}</code>

⚠️ <i>افتح اللوحة بمتصفح مسجل بحساب Qwiklabs لتجنب Error 400. شاشة 404 لـ VLESS طبيعية.</i>"

curl -s -X POST "https://api.telegram.org/bot<<TOKEN>>/sendMessage" -d chat_id="<<CHAT_ID>>" -d parse_mode="HTML" --data-urlencode text="$MSG"
echo "=== VLESS_DEPLOYMENT_COMPLETE ==="
"""
    raw_script = raw_script.replace("<<REGION>>", region).replace("<<TOKEN>>", token).replace("<<CHAT_ID>>", str(chat_id))
    b64 = base64.b64encode(raw_script.encode('utf-8')).decode('utf-8')
    return f"echo {b64} | base64 -d > deploy_vless.sh && bash deploy_vless.sh\n"


# ╔═══════════════════════════════════════════════════════╗
# ║  11 · STREAM ENGINE & MAIN LOOP                       ║
# ╚═══════════════════════════════════════════════════════╝

def _update_stream(driver, chat_id, session, status, flash):
    flash = not flash
    icon = "🔴" if flash else "⭕"
    cap = f"{icon} بث مباشر\n📌 {status} | ⏱ {datetime.now().strftime('%H:%M:%S')}"
    try:
        png = driver.get_screenshot_as_png()
        bio = io.BytesIO(png)
        bio.name = f"l_{random.randint(10,99)}.png"
        bot.edit_message_media(InputMediaPhoto(bio, caption=cap), chat_id=chat_id, message_id=session["msg_id"], reply_markup=build_panel(session.get("cmd_mode", False)))
        bio.close()
    except: pass
    return flash

def stream_loop(chat_id, gen):
    session = get_session(chat_id)
    if not session: return
    driver = session["driver"]
    flash, err_n, drv_err, cookies_saved = True, 0, 0, False

    while session["running"] and session.get("gen") == gen:
        if session.get("cmd_mode"):
            time.sleep(Config.CMD_CHECK_INTERVAL)
            if session.get("vless_installed") and "=== VLESS_DEPLOYMENT_COMPLETE ===" in (read_terminal(driver) or ""):
                time.sleep(2)
                try: bot.delete_message(chat_id, session["msg_id"])
                except: pass
                if session.get("status_msg_id"):
                    try: bot.delete_message(chat_id, session["status_msg_id"])
                    except: pass
                cooldown_time = time.time() + (15 * 60)
                if users_col is not None: users_col.update_one({"_id": chat_id}, {"$set": {"vless_cooldown": cooldown_time}}, upsert=True)
                else: local_cooldowns[chat_id] = cooldown_time
                session["running"] = False
                break
            continue

        time.sleep(random.uniform(*Config.STREAM_INTERVAL))
        if not session["running"]: break

        try:
            _focus_terminal(driver)
            status = handle_google_pages(driver, session, chat_id)
            if time.time() >= session.get("shell_loading_until", 0):
                flash = _update_stream(driver, chat_id, session, status, flash)
            
            on_console = "console.cloud.google.com" in current_url(driver)
            if session.get("waiting_for_region"):
                if time.time() - session.get("region_prompt_time", time.time()) > 30:
                    send_safe(chat_id, "⏱️ **انتهى الوقت!**\nالرجاء إعادة إرسال الرابط.")
                    try: bot.delete_message(chat_id, session.get("status_msg_id")); bot.delete_message(chat_id, session.get("msg_id"))
                    except: pass
                    session["running"] = False
                    break
            elif session.get("project_id") and not session.get("run_api_checked") and on_console:
                if status not in ("مراقبة...", "📊 Console", "✅ Terminal"):
                    if do_cloud_run_extraction(driver, chat_id, session): session["run_api_checked"] = True

            elif is_shell_page(driver) and not session.get("terminal_notified") and is_terminal_ready(driver):
                session.update({"terminal_ready": True, "terminal_notified": True, "cmd_mode": True})
                if not cookies_saved: save_user_cookies(driver, chat_id); cookies_saved = True
                
                region = session.get("selected_region")
                if region and not session.get("vless_installed"):
                    session["vless_installed"] = True
                    send_command(driver, _generate_vless_cmd(region, Config.TOKEN, chat_id))
                    _update_stream(driver, chat_id, session, "⚙️ جاري البناء...", flash)
                else: send_safe(chat_id, "🖥️ **Terminal جاهز!** ✅\nأرسل أوامرك الآن.")

            err_n, drv_err = 0, 0
        except Exception as e:
            if "not modified" in str(e).lower(): continue
            err_n += 1
            if err_n >= 5:
                try: driver.refresh()
                except: drv_err += 1
    gc.collect()

def _restart_driver(chat_id, session):
    send_safe(chat_id, "🔁 إعادة تشغيل المتصفح...")
    try:
        safe_quit(session.get("driver"))
        new_drv = create_driver()
        session["driver"] = new_drv
        load_user_cookies(new_drv, chat_id)
        new_drv.get(session.get("url", "about:blank"))
        session.update({"shell_opened": False, "auth": False, "terminal_ready": False, "terminal_notified": False, "run_api_checked": False, "shell_loading_until": 0})
        send_safe(chat_id, "✅ تم إعادة التشغيل بنجاح!")
    except: session["running"] = False

def start_stream_sync(chat_id, url):
    old_drv = None
    with sessions_lock:
        if chat_id in user_sessions:
            old = user_sessions[chat_id]
            old["running"], old["gen"], old_drv = False, old.get("gen", 0) + 1, old.get("driver")
    
    status_msg = send_safe(chat_id, "⚡ جاري التجهيز...")
    if old_drv: safe_quit(old_drv); time.sleep(2)
    
    try:
        driver = create_driver()
        if status_msg: edit_safe(chat_id, status_msg.message_id, "🌐 جاري فتح الرابط (تخطي الدخول)...")
        load_user_cookies(driver, chat_id)
    except: return

    gen = int(time.time())
    with sessions_lock:
        user_sessions[chat_id] = _new_session_dict(driver, url, extract_project_id(url), gen)
        session = user_sessions[chat_id]
    
    try: driver.get(url)
    except: pass
    time.sleep(5)
    
    try:
        png = driver.get_screenshot_as_png()
        bio = io.BytesIO(png)
        if status_msg: bot.delete_message(chat_id, status_msg.message_id)
        msg = bot.send_photo(chat_id, bio, caption="🔴 بث مباشر\n📌 بدء...", reply_markup=build_panel())
        bio.close()
        session.update({"msg_id": msg.message_id, "running": True})
        stream_loop(chat_id, gen)
    except: cleanup_session(chat_id)

def queue_worker():
    global active_task_cid
    while not shutdown_event.is_set():
        try:
            task = deployment_queue.get(timeout=2)
            cid = task["chat_id"]
            with queue_lock: active_task_cid = cid
            start_stream_sync(cid, task["url"])
            cleanup_session(cid)
            with queue_lock: active_task_cid = None
            deployment_queue.task_done()
        except:
            with queue_lock: active_task_cid = None

def execute_command(chat_id, command):
    s = get_session(chat_id)
    if not s or not s.get("driver"): return
    driver = s["driver"]
    if not is_shell_page(driver): return
    s["terminal_ready"], s["last_activity"] = True, time.time()
    
    status = send_safe(chat_id, f"⏳ جاري تنفيذ:\n`{command}`", parse_mode="Markdown")
    t_before = read_terminal(driver) or ""
    if not send_command(driver, command): return
    
    time.sleep(10 if any(k in command.lower() for k in Config.SLOW_CMDS) else 3)
    t_after = read_terminal(driver) or ""
    out = (t_after[len(t_before):].strip() if len(t_after) > len(t_before) else "") or extract_result(t_after, command) or ""
    
    bio = take_screenshot(driver)
    if out:
        out = out[:3900] + "..." if len(out) > 3900 else out
        send_safe(chat_id, f"📋 **النتيجة:**\n```\n{out}\n```", parse_mode="Markdown", reply_markup=build_panel(True))
    if bio:
        try: bot.send_photo(chat_id, bio, reply_markup=build_panel(True))
        except: pass
        bio.close()
    if status: bot.delete_message(chat_id, status.message_id)


# ╔═══════════════════════════════════════════════════════╗
# ║  12 · TELEGRAM HANDLERS                               ║
# ╚═══════════════════════════════════════════════════════╝

@bot.message_handler(commands=["start"])
def cmd_start(msg): bot.reply_to(msg, WELCOME_MSG, parse_mode="Markdown")

@bot.message_handler(commands=["help", "h"])
def cmd_help(msg): bot.reply_to(msg, HELP_MSG, parse_mode="Markdown")

@bot.message_handler(commands=["clearcookies"])
def cmd_clearcookies(msg):
    cid = msg.chat.id
    try:
        if users_col is not None: users_col.update_one({"_id": cid}, {"$unset": {"cookies": ""}})
        session_cookies.pop(cid, None)
        bot.reply_to(msg, "🗑️ **تم مسح جلسة المتصفح (Cookies)!**\nالرابط القادم سيطلب تسجيل الدخول من جديد.", parse_mode="Markdown")
    except: pass

@bot.message_handler(commands=["status"])
def cmd_status(msg):
    s = get_session(msg.chat.id)
    if not s:
        bot.reply_to(msg, "⏳ أنت في الطابور." if any(t["chat_id"] == msg.chat.id for t in list(deployment_queue.queue)) else "❌ لا توجد جلسة نشطة.")
        return
    bot.reply_to(msg, f"ℹ️ **الحالة:** {'🟢 يعمل' if s.get('running') else '🔴 متوقف'}\n🌐 **الصفحة:**\n`{current_url(s['driver'])[:80]}`", parse_mode="Markdown")

@bot.message_handler(commands=["stop", "s"])
def cmd_stop(msg):
    cid = msg.chat.id
    s = get_session(cid)
    if s:
        s["running"], s["gen"] = False, s.get("gen", 0) + 1
        cleanup_session(cid)
        bot.reply_to(msg, "🛑 تم الإيقاف بنجاح.")
    else:
        for i, item in enumerate(deployment_queue.queue):
            if item['chat_id'] == cid:
                del deployment_queue.queue[i]; bot.reply_to(msg, "🛑 تم سحب طلبك من الطابور."); return
        bot.reply_to(msg, "❌ لا توجد جلسة نشطة.")

@bot.message_handler(func=lambda m: m.text and m.text.startswith("https://www.skills.google/google_sso"))
def handle_url_msg(msg):
    cid = msg.chat.id
    expiry = users_col.find_one({"_id": cid}).get("vless_cooldown", 0) if users_col is not None else local_cooldowns.get(cid, 0)
    if time.time() < expiry:
        bot.reply_to(msg, "⏳ **يرجى الانتظار!** لديك سيرفر قيد العمل حالياً.", parse_mode="Markdown"); return
    with sessions_lock:
        if cid in user_sessions and user_sessions[cid].get("running"):
            bot.reply_to(msg, "❌ لديك جلسة تعمل حالياً."); return
    if any(t["chat_id"] == cid for t in list(deployment_queue.queue)) or active_task_cid == cid:
        bot.reply_to(msg, "❌ طلبك قيد المعالجة."); return
    
    if active_task_cid is not None: bot.reply_to(msg, f"⏳ **البوت مشغول!** ترتيبك في الطابور: `{deployment_queue.qsize() + 1}`", parse_mode="Markdown")
    deployment_queue.put({"chat_id": cid, "url": msg.text.strip()})

@bot.message_handler(func=lambda m: m.text and not m.text.startswith("/"))
def handle_text(msg):
    s, cid = get_session(msg.chat.id), msg.chat.id
    if not s: return
    w = s.get("waiting_for_input")
    if w in ["email", "password"]:
        try:
            els = s["driver"].find_elements(By.XPATH, f"//input[@type='{w}']")
            if els:
                els[0].clear(); els[0].send_keys(msg.text); els[0].send_keys(Keys.RETURN)
                s["waiting_for_input"] = None
                send_safe(cid, f"✅ تم إدخال {'الاسم' if w=='email' else 'الرمز'}...")
        except: pass
    elif s.get("cmd_mode"): threading.Thread(target=execute_command, args=(cid, msg.text), daemon=True).start()

@bot.callback_query_handler(func=lambda call: True)
def on_callback(call):
    cid, action = call.message.chat.id, call.data
    s = get_session(cid)
    if not s: bot.answer_callback_query(call.id, "لا توجد جلسة نشطة."); return

    if action == "ignore": bot.answer_callback_query(call.id, "تصنيف السيرفرات"); return
    if action.startswith("setreg_"):
        region = action.split("_")[1]
        s["selected_region"], s["waiting_for_region"] = region, False
        if s.get("status_msg_id"): edit_safe(cid, s["status_msg_id"], f"✅ تم اختيار: `{region}`\n🚀 جاري البناء، يرجى الانتظار...", parse_mode="Markdown", reply_markup=None)
        if s.get("project_id"): safe_navigate(s["driver"], f"https://shell.cloud.google.com/?enableapi=true&project={s['project_id']}&pli=1&show=terminal")
    elif action == "stop": cmd_stop(call.message)
    elif action == "refresh":
        try: s["driver"].refresh(); bot.answer_callback_query(call.id, "تحديث...")
        except: pass
    elif action == "screenshot": cmd_ss(call.message)
    elif action == "cmd_mode":
        s["cmd_mode"] = True
        bot.answer_callback_query(call.id, "وضع الأوامر")
        send_safe(cid, "⌨️ **أرسل أوامرك مباشرة الآن.**")
    elif action == "watch_mode":
        s["cmd_mode"] = False
        bot.answer_callback_query(call.id, "وضع البث")


def boot_check():
    b = find_path(["chromium", "chromium-browser"], ["/usr/bin/chromium", "/usr/bin/chromium-browser"])
    d = find_path(["chromedriver"], ["/usr/bin/chromedriver", "/usr/lib/chromium/chromedriver"])
    if not b or not d: sys.exit(1)

def graceful_shutdown(s, f):
    shutdown_event.set()
    for cid in list(user_sessions): safe_quit(user_sessions.get(cid, {}).get("driver"))
    sys.exit(0)

signal.signal(signal.SIGTERM, graceful_shutdown)
signal.signal(signal.SIGINT, graceful_shutdown)

if __name__ == "__main__":
    boot_check()
    threading.Thread(target=_health_server, daemon=True).start()
    threading.Thread(target=_auto_cleanup_loop, daemon=True).start()
    threading.Thread(target=queue_worker, daemon=True).start()
    try: bot.remove_webhook(); time.sleep(1)
    except: pass
    log.info("🚀 Bot is running!")
    while not shutdown_event.is_set():
        try: bot.polling(non_stop=True, skip_pending=True, timeout=60)
        except: time.sleep(5)
