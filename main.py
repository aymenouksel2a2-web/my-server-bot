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
    TOKEN = os.environ.get("BOT_TOKEN")
    PORT = int(os.environ.get("PORT", 8080))
    # ضع رابط MongoDB الخاص بك في متغيرات البيئة، أو سيستخدم المحلي للتجربة
    MONGO_URI = os.environ.get("MONGO_URI", "mongodb://localhost:27017/")
    VERSION = "4.0-VLESS-MongoQueue"

    PAGE_LOAD_TIMEOUT = 45
    SCRIPT_TIMEOUT = 20
    WINDOW_SIZE = (1024, 768)

    STREAM_INTERVAL = (4, 6)
    CMD_CHECK_INTERVAL = 3
    
    REGION_TIMEOUT_SEC = 30           # مهلة اختيار السيرفر 30 ثانية
    COOLDOWN_MINUTES = 15             # مدة الانتظار قبل إنشاء سيرفر جديد

    SESSION_MAX_AGE_HOURS = 4
    CLEANUP_INTERVAL_SEC = 1800

# ── إعداد قاعدة البيانات ──
try:
    mongo_client = MongoClient(Config.MONGO_URI, serverSelectionTimeoutMS=5000)
    db = mongo_client["cloudshell_bot"]
    queue_col = db["deployment_queue"]
    cooldown_col = db["cooldowns"]
    mongo_client.server_info() # فحص الاتصال
    MONGO_READY = True
except Exception as e:
    print(f"⚠️ تحذير: تعذر الاتصال بـ MongoDB. تأكد من إعداد MONGO_URI. الخطأ: {e}")
    MONGO_READY = False

# ╔═══════════════════════════════════════════════════════╗
# ║  2 · LOGGING & GLOBAL STATE                           ║
# ╚═══════════════════════════════════════════════════════╝

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s", datefmt="%Y-%m-%d %H:%M:%S")
log = logging.getLogger("CSBot")

if not Config.TOKEN:
    log.critical("❌ BOT_TOKEN غير موجود! أضفه كمتغير بيئة.")
    sys.exit(1)

bot = telebot.TeleBot(Config.TOKEN)

# المتصفحات المفتوحة يجب أن تبقى في الذاكرة (RAM) لأنها كائنات حية
user_sessions: dict = {}
sessions_lock = threading.Lock()
chromedriver_lock = threading.Lock()
shutdown_event = threading.Event()
active_task_cid = None
queue_lock = threading.Lock()


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
            q_size = queue_col.count_documents({}) if MONGO_READY else 0
            payload = json.dumps({"status": "running", "version": Config.VERSION, "sessions": active, "queue_size": q_size, "ts": datetime.now().isoformat()}, ensure_ascii=False)
            self.wfile.write(payload.encode())
        else:
            self.send_response(404)
            self.end_headers()
    def log_message(self, *_): pass

def _health_server():
    try: HTTPServer(("0.0.0.0", Config.PORT), HealthHandler).serve_forever()
    except Exception as exc: log.error(f"❌ Health-server: {exc}")


display = None
for size, depth in [(Config.WINDOW_SIZE, 16), ((800, 600), 24)]:
    try:
        display = Display(visible=0, size=size, color_depth=depth)
        display.start()
        log.info(f"✅ Xvfb {size[0]}×{size[1]}")
        break
    except Exception: continue


# ╔═══════════════════════════════════════════════════════╗
# ║  6 · UTILITY HELPERS & MONGODB LOGIC                  ║
# ╚═══════════════════════════════════════════════════════╝

def is_user_in_cooldown(chat_id):
    if not MONGO_READY: return False
    record = cooldown_col.find_one({"chat_id": chat_id})
    if record and time.time() < record.get("expires_at", 0):
        return True
    return False

def set_user_cooldown(chat_id):
    if not MONGO_READY: return
    expires = time.time() + (Config.COOLDOWN_MINUTES * 60)
    cooldown_col.update_one({"chat_id": chat_id}, {"$set": {"expires_at": expires}}, upsert=True)

def track_message(session, msg_id):
    """تتبع رسائل البوت ليتم حذفها لاحقاً لتنظيف الشات"""
    if msg_id and "tracked_messages" in session:
        session["tracked_messages"].append(msg_id)

def send_safe(chat_id, text, session=None, **kw):
    try:
        msg = bot.send_message(chat_id, text, **kw)
        if session: track_message(session, msg.message_id)
        return msg
    except Exception: return None

def edit_safe(chat_id, message_id, text, **kw):
    try: return bot.edit_message_text(chat_id=chat_id, message_id=message_id, text=text, **kw)
    except Exception: return None

def find_path(names, extras=None):
    for n in names:
        if shutil.which(n): return shutil.which(n)
    for p in extras or []:
        if os.path.isfile(p): return p
    return None

def browser_version(path):
    try:
        r = subprocess.run([path, "--version"], capture_output=True, text=True, timeout=5)
        m = re.search(r"(\d+)", r.stdout)
        return m.group(1) if m else "120"
    except Exception: return "120"

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
        except Exception: return orig
    return dst

def safe_navigate(driver, url):
    try: driver.get(url); return True
    except TimeoutException: return True
    except Exception: return False

def current_url(driver):
    try: return driver.current_url
    except Exception: return ""

def extract_project_id(url):
    m = re.search(r"(qwiklabs-gcp-[\w-]+|project[=/][\w-]+|gcp-[\w-]+)", url)
    return m.group(1).replace("project=", "").replace("project/", "") if m else None


STEALTH_JS = r"""
Object.defineProperty(navigator,'webdriver',{get:()=>undefined});
Object.defineProperty(navigator,'plugins',{get:function(){return[{name:'Chrome PDF Plugin',filename:'internal-pdf-viewer',length:1}];}});
window.chrome={runtime:{}};
"""

def create_driver():
    browser = find_path(["chromium", "chromium-browser"], ["/usr/bin/chromium", "/usr/bin/chromium-browser"])
    drv = find_path(["chromedriver"], ["/usr/bin/chromedriver", "/usr/lib/chromium/chromedriver"])
    patched = patch_driver(drv)
    opts = Options()
    opts.binary_location = browser
    opts.add_argument("--incognito")
    opts.add_argument("--disable-blink-features=AutomationControlled")
    opts.add_experimental_option("excludeSwitches", ["enable-automation"])
    opts.add_experimental_option("useAutomationExtension", False)
    
    for flag in ["--no-sandbox", "--disable-dev-shm-usage", "--disable-gpu", "--window-size=1024,768", "--mute-audio"]:
        opts.add_argument(flag)

    opts.page_load_strategy = "eager"
    driver = webdriver.Chrome(service=Service(executable_path=patched), options=opts)
    try: driver.execute_cdp_cmd("Page.addScriptToEvaluateOnNewDocument", {"source": STEALTH_JS})
    except Exception: pass
    driver.set_page_load_timeout(Config.PAGE_LOAD_TIMEOUT)
    return driver


def _new_session_dict(driver, url, project_id, gen):
    return {
        "driver": driver, "running": False, "msg_id": None, "url": url, "project_id": project_id,
        "shell_opened": False, "auth": False, "terminal_ready": False, "terminal_notified": False,
        "cmd_mode": False, "gen": gen, "run_api_checked": False, "shell_loading_until": 0,
        "waiting_for_region": False, "region_ask_time": 0, "selected_region": None, "vless_installed": False,
        "status_msg_id": None, "tracked_messages": [], "created_at": time.time()
    }

def safe_quit(driver):
    if driver:
        try: driver.quit()
        except Exception: pass
        gc.collect()

def cleanup_session(chat_id, force_delete_messages=False):
    with sessions_lock:
        s = user_sessions.pop(chat_id, None)
    if s:
        s["running"] = False
        
        # التنظيف الشامل للرسائل المتبقية إذا طُلب ذلك
        if force_delete_messages:
            for m_id in s.get("tracked_messages", []):
                try: bot.delete_message(chat_id, m_id)
                except Exception: pass
            if s.get("msg_id"):
                try: bot.delete_message(chat_id, s["msg_id"])
                except Exception: pass
                
        safe_quit(s.get("driver"))
        gc.collect()


# ╔═══════════════════════════════════════════════════════╗
# ║  11 · SHELL DETECTION & TERMINAL                      ║
# ╚═══════════════════════════════════════════════════════╝

def is_shell_page(driver):
    try: return "shell.cloud.google.com" in driver.current_url or "ide.cloud.google.com" in driver.current_url
    except Exception: return False

def is_terminal_ready(driver):
    if not is_shell_page(driver): return False
    try:
        return driver.execute_script("""
            var rows = document.querySelectorAll('.xterm-rows > div');
            if (!rows.length) return false;
            for (var i=0; i<rows.length; i++) {
                if ((rows[i].textContent||'').match(/[$@#]/)) return true;
            } return false;
        """)
    except Exception: return False

def send_command(driver, command):
    if not driver: return False
    try: driver.switch_to.window(driver.window_handles[-1]); driver.switch_to.default_content()
    except Exception: pass
    
    command_clean = command.rstrip('\n')
    js_paste = """
    var text = arguments[0];
    var ta = document.querySelector('.xterm-helper-textarea');
    if (!ta) {
        var fr = document.querySelectorAll('iframe');
        for (var i=0; i<fr.length; i++) { try { ta = fr[i].contentDocument.querySelector('.xterm-helper-textarea'); if(ta) break; }catch(e){} }
    }
    if (ta) {
        ta.focus();
        var dt = new DataTransfer(); dt.setData('text/plain', text + '\\n'); 
        ta.dispatchEvent(new ClipboardEvent('paste', { clipboardData: dt, bubbles: true }));
        return true;
    } return false;
    """
    try:
        if driver.execute_script(js_paste, command_clean):
            time.sleep(1)
            try: driver.switch_to.active_element.send_keys(Keys.RETURN)
            except Exception: pass
            return True
    except Exception: pass
    return False

def read_terminal(driver):
    if not driver: return None
    try:
        return driver.execute_script("""
            var rows=document.querySelectorAll('.xterm-rows > div');
            if(!rows.length) { var x=document.querySelector('.xterm'); if(x) rows=x.querySelectorAll('.xterm-rows > div'); }
            if(rows.length) { var l=[]; rows.forEach(r => { var t=(r.textContent||'').trim(); if(t) l.push(t); }); return l.join('\\n'); }
            return null;
        """)
    except Exception: return None

def take_screenshot(driver):
    if not driver: return None
    try:
        bio = io.BytesIO(driver.get_screenshot_as_png())
        bio.name = f"ss.png"
        return bio
    except Exception: return None


# ╔═══════════════════════════════════════════════════════╗
# ║  13 · GOOGLE PAGES AUTO-HANDLER                       ║
# ╚═══════════════════════════════════════════════════════╝

def handle_google_pages(driver, session):
    status = "مراقبة..."
    try: body = driver.find_element(By.TAG_NAME, "body").text[:5000].lower()
    except Exception: return status

    def click_btn(xpaths):
        for xp in xpaths:
            try:
                for btn in driver.find_elements(By.XPATH, xp):
                    if btn.is_displayed():
                        try: btn.click()
                        except Exception: driver.execute_script("arguments[0].click();", btn)
                        time.sleep(1.5)
                        return True
            except Exception: continue
        return False

    if "agree and continue" in body:
        try:
            for cb in driver.find_elements(By.XPATH, "//mat-checkbox|//input[@type='checkbox']"): driver.execute_script("arguments[0].click();", cb)
        except Exception: pass
        if click_btn(["//button[contains(translate(.,'ABCDEFGHIJKLMNOPQRSTUVWXYZ','abcdefghijklmnopqrstuvwxyz'),'agree and continue')]"]): return "✅ الشروط"

    if "authorize cloud shell" in body:
        if click_btn(["//button[normalize-space(.)='Authorize']"]): session["auth"] = True; return "✅ التفويض"

    if "cloud shell" in body and "continue" in body and "free" in body:
        if click_btn(["//a[contains(text(),'Continue')]", "//button[contains(text(),'Continue')]"]): return "✅ Continue"

    if click_btn(["//button[contains(.,'Trust')]", "//button[contains(.,'Confirm')]"]): return "✅ Trust"

    u = current_url(driver)
    if "shell.cloud.google.com" in u or "ide.cloud.google.com" in u: return "✅ Terminal"
    if "console.cloud.google.com" in u: return "📊 Console"
    return status


# ╔═══════════════════════════════════════════════════════╗
# ║  14 · CLOUD RUN REGION EXTRACTION                     ║
# ╚═══════════════════════════════════════════════════════╝

REGION_JS = """
var callback = arguments[arguments.length - 1];
setTimeout(function() {
    try {
        var dd = document.querySelectorAll('mat-select, [role="combobox"]');
        for (var i=0; i<dd.length; i++) {
            if ((dd[i].getAttribute('aria-label')||'').toLowerCase().includes('region')) { dd[i].click(); break; }
        }
        setTimeout(function() {
            var opts = document.querySelectorAll('mat-option, [role="option"]');
            var res = [];
            for (var k=0; k<opts.length; k++) {
                if (opts[k].getBoundingClientRect().width > 0 && !opts[k].classList.contains('mat-option-disabled')) {
                    var t = (opts[k].innerText || '').trim().split('\\n')[0];
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

    if "run/create" not in current_url(driver):
        if not session.get("status_msg_id"):
            msg = send_safe(chat_id, "⚙️ جاري فتح صفحة Cloud Run لاستخراج السيرفرات...", session)
            if msg: session["status_msg_id"] = msg.message_id
        else: edit_safe(chat_id, session["status_msg_id"], "⚙️ جاري فتح صفحة Cloud Run لاستخراج السيرفرات...")
        safe_navigate(driver, f"https://console.cloud.google.com/run/create?enableapi=true&project={pid}")
        return False

    if session.get("status_msg_id"): edit_safe(chat_id, session["status_msg_id"], "🔍 جاري قراءة السيرفرات المسموحة...")

    try:
        driver.set_script_timeout(15)
        result = driver.execute_async_script(REGION_JS)
        if result and result not in ("NO_DROPDOWN", "NO_REGIONS") and not result.startswith("ERROR:"):
            regions = [r.strip() for r in result.split("\n") if r.strip()]
            mk = InlineKeyboardMarkup(row_width=2)
            mk.add(*[InlineKeyboardButton(r, callback_data=f"setreg_{r.split()[0]}") for r in regions])

            if session.get("status_msg_id"):
                edit_safe(chat_id, session["status_msg_id"], "🌍 **السيرفرات المسموحة للإنشاء:**\nاختر السيرفر الذي تريده لبناء VLESS:\n*(لديك 30 ثانية للاختيار)*", reply_markup=mk, parse_mode="Markdown")
            
            session["waiting_for_region"] = True
            session["region_ask_time"] = time.time()  # 💡 بدء عداد الـ 30 ثانية
    except Exception: pass
    return True


# ╔═══════════════════════════════════════════════════════╗
# ║  14.5 · VLESS SCRIPT GENERATOR                        ║
# ╚═══════════════════════════════════════════════════════╝

def _generate_vless_cmd(region):
    """
    توليد السكريبت. 
    ملاحظة: البايثون هو من سيقوم بإرسال رسالة التيليجرام وليس الباش!
    الباش سيقوم فقط بطباعة المتغيرات في التيرمنال لنقرأها.
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
            "settings": {{ "clients": [{{"id": "$UUID", "level": 0}}], "decryption": "none" }},
            "streamSettings": {{ "network": "ws", "wsSettings": {{ "path": "/@O_C_X7" }} }}
        }}
    ],
    "outbounds": [{{"protocol": "freedom", "settings": {{}}}}]
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
    icon = "🔴" if not flash else "⭕"
    cap = f"{icon} بث مباشر\n📁 {session.get('project_id','')}\n📌 {status}\n⏱ {datetime.now().strftime('%H:%M:%S')}"
    png = driver.get_screenshot_as_png()
    bio = io.BytesIO(png)
    bio.name = f"l_{int(time.time())}.png"
    try: bot.edit_message_media(media=InputMediaPhoto(bio, caption=cap), chat_id=chat_id, message_id=session["msg_id"])
    except Exception: pass
    bio.close()
    return not flash

def stream_loop(chat_id, gen):
    with sessions_lock:
        if chat_id not in user_sessions: return
        session = user_sessions[chat_id]

    driver = session["driver"]
    flash = True; err_n = 0; cycle = 0

    while session["running"] and session.get("gen") == gen:

        # 💡 نظام التنظيف والإنهاء عند قراءة النتيجة من التيرمنال
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

                    # 1. تنظيف المحادثة (حذف رسائل البث والتحديثات)
                    cleanup_session(chat_id, force_delete_messages=True)
                    
                    # 2. إرسال الرسالة الختامية الأنيقة
                    final_msg = f"✅ Create\n\n{final_url}\n\n`{final_vlink}`{web_preview}"
                    send_safe(chat_id, final_msg, parse_mode="Markdown")
                    
                    # 3. وضع المستخدم في قائمة الحظر المؤقت (Cooldown)
                    set_user_cooldown(chat_id)
                    break
            time.sleep(3)
            continue

        time.sleep(random.uniform(*Config.STREAM_INTERVAL))
        if not session["running"]: break
        cycle += 1

        try:
            _focus_terminal(driver)
            status = handle_google_pages(driver, session)
            cur = current_url(driver)
            flash = _update_stream(driver, chat_id, session, status, flash)

            on_console = "console.cloud.google.com" in cur
            on_shell = is_shell_page(driver)

            # 💡 التحقق من مهلة الـ 30 ثانية لاختيار السيرفر
            if session.get("waiting_for_region"):
                if time.time() - session.get("region_ask_time", 0) > Config.REGION_TIMEOUT_SEC:
                    if session.get("status_msg_id"):
                        edit_safe(chat_id, session["status_msg_id"], "⏳ **انتهى الوقت!**\nلم تقم باختيار سيرفر خلال 30 ثانية. تم إنهاء الجلسة لإفساح المجال للآخرين.", parse_mode="Markdown")
                    cleanup_session(chat_id, force_delete_messages=False)
                    break
                continue

            if session.get("project_id") and not session.get("run_api_checked") and on_console:
                if status not in ("مراقبة...", "📊 Console", "✅ Terminal") and "signin" not in cur:
                    if do_cloud_run_extraction(driver, chat_id, session): session["run_api_checked"] = True

            elif on_shell and not session.get("terminal_notified"):
                if is_terminal_ready(driver):
                    session["terminal_ready"] = True
                    session["terminal_notified"] = True

                    region = session.get("selected_region")
                    if region and not session.get("vless_installed"):
                        session["vless_installed"] = True
                        cmd = _generate_vless_cmd(region)
                        send_command(driver, cmd)
                        if session.get("status_msg_id"):
                            edit_safe(chat_id, session["status_msg_id"], f"⚙️ **جاري بناء سيرفر VLESS على {region}...**\nيرجى الانتظار، سيصلك الرابط فور الانتهاء وسيقوم البوت بتنظيف المحادثة.", parse_mode="Markdown")

        except Exception as e:
            if "timeout" in str(e).lower(): continue
            err_n += 1
            if err_n >= Config.MAX_ERR_BEFORE_REFRESH:
                try: driver.refresh(); err_n = 0
                except Exception: pass

    gc.collect()


# ╔═══════════════════════════════════════════════════════╗
# ║  16 · QUEUE WORKER & START STREAM                     ║
# ╚═══════════════════════════════════════════════════════╝

def start_stream_sync(chat_id, url):
    with sessions_lock:
        if chat_id in user_sessions: safe_quit(user_sessions[chat_id].get("driver"))

    msg = send_safe(chat_id, "⚡ جاري تجهيز المتصفح لك...")
    status_msg_id = msg.message_id if msg else None
    
    try: driver = create_driver()
    except Exception as e:
        if status_msg_id: edit_safe(chat_id, status_msg_id, f"❌ فشل تشغيل المتصفح.")
        return

    gen = int(time.time())
    with sessions_lock:
        user_sessions[chat_id] = _new_session_dict(driver, url, extract_project_id(url), gen)
        session = user_sessions[chat_id]
        if status_msg_id: session["tracked_messages"].append(status_msg_id)

    driver.get(url); time.sleep(5)

    try:
        _focus_terminal(driver)
        bio = take_screenshot(driver)
        if bio:
            m = bot.send_photo(chat_id, bio, caption="🔴 بث مباشر\n📌 جاري البدء...")
            bio.close()
            with sessions_lock:
                session["msg_id"] = m.message_id
                session["running"] = True

        stream_loop(chat_id, gen)
    except Exception: cleanup_session(chat_id)


def queue_worker():
    """نظام الطابور المعتمد على MongoDB لمعالجة طلب واحد في كل مرة"""
    global active_task_cid
    while not shutdown_event.is_set():
        if not MONGO_READY: time.sleep(5); continue
        
        try:
            task = queue_col.find_one_and_delete({}, sort=[("_id", 1)])
            if task:
                cid = task["chat_id"]
                url = task["url"]
                
                with queue_lock: active_task_cid = cid
                start_stream_sync(cid, url)
                
                cleanup_session(cid)
                with queue_lock: active_task_cid = None
            else: time.sleep(2)
        except Exception as e:
            log.error(f"Queue worker error: {e}")
            with queue_lock: active_task_cid = None
            time.sleep(2)


# ╔═══════════════════════════════════════════════════════╗
# ║  18 · BOT HANDLERS                                    ║
# ╚═══════════════════════════════════════════════════════╝

@bot.message_handler(commands=["start", "help"])
def cmd_help(msg):
    bot.reply_to(msg, "🤖 **أهلاً بك!** أرسل رابط SSO ليتم وضعك في الطابور وإنشاء VLESS الخاص بك.", parse_mode="Markdown")

@bot.message_handler(commands=["status"])
def cmd_status(msg):
    cid = msg.chat.id
    if MONGO_READY:
        in_queue = queue_col.count_documents({"chat_id": cid}) > 0
        if in_queue: bot.reply_to(msg, "⏳ أنت حالياً في طابور الانتظار."); return
        
        cd = cooldown_col.find_one({"chat_id": cid})
        if cd and time.time() < cd["expires_at"]:
            bot.reply_to(msg, "⏳ لديك حظر مؤقت نشط لمنع الضغط. يرجى الانتظار."); return

    if not get_session(cid): bot.reply_to(msg, "❌ لا توجد جلسة نشطة."); return
    bot.reply_to(msg, "🟢 الجلسة تعمل حالياً.")

@bot.message_handler(func=lambda m: m.text and m.text.startswith("https://www.skills.google/google_sso"))
def handle_url_msg(msg):
    cid = msg.chat.id
    url = msg.text.strip()
    
    if not MONGO_READY:
        bot.reply_to(msg, "❌ قاعدة البيانات غير متصلة. يرجى مراجعة المسؤول."); return

    # 1. التحقق من الانتظار (Cooldown)
    if is_user_in_cooldown(cid):
        bot.reply_to(msg, "⏳ **عذراً!** لقد قمت بإنشاء سيرفر مؤخراً.\nيرجى الانتظار لبعض الوقت (حتى انتهاء صلاحية سيرفرك الحالي) لإفساح المجال للآخرين.", parse_mode="Markdown")
        return

    # 2. التحقق من الجلسات الحالية والطابور
    with sessions_lock:
        if cid in user_sessions and user_sessions[cid].get("running"):
            bot.reply_to(msg, "❌ لديك جلسة قيد العمل حالياً."); return
            
    if queue_col.count_documents({"chat_id": cid}) > 0 or active_task_cid == cid:
        bot.reply_to(msg, "❌ طلبك موجود في الطابور أو قيد المعالجة بالفعل."); return
        
    pos = queue_col.count_documents({})
    queue_col.insert_one({"chat_id": cid, "url": url, "ts": time.time()})
    
    if active_task_cid is not None or pos > 0:
        bot.reply_to(msg, f"⏳ **البوت مشغول حالياً!**\nتم وضعك في الطابور.\n🔹 دورك رقم: `{pos + 1}`\nسيبدأ عملك تلقائياً.", parse_mode="Markdown")
    else:
        bot.reply_to(msg, "✅ تم استلام الرابط، سيتم البدء فوراً.")

@bot.callback_query_handler(func=lambda call: True)
def on_callback(call):
    cid = call.message.chat.id
    s = get_session(cid)
    if not s: bot.answer_callback_query(call.id, "لا توجد جلسة نشطة."); return

    action = call.data
    if action.startswith("setreg_"):
        region = action.split("_")[1]
        s["selected_region"] = region
        s["waiting_for_region"] = False
        bot.answer_callback_query(call.id, f"تم اختيار {region}")
        
        if s.get("status_msg_id"):
            edit_safe(cid, s["status_msg_id"], f"✅ تم اختيار السيرفر: `{region}`\n🚀 جاري الانتقال وتجهيز السيرفر...", parse_mode="Markdown")
        
        if s.get("project_id"): safe_navigate(s["driver"], f"https://shell.cloud.google.com/?enableapi=true&project={s['project_id']}&pli=1&show=terminal")


# ╔═══════════════════════════════════════════════════════╗
# ║  20 · BOOT CHECK & GRACEFUL SHUTDOWN                  ║
# ╚═══════════════════════════════════════════════════════╝

def graceful_shutdown(*_):
    log.info("🛑 إيقاف نظيف...")
    shutdown_event.set()
    with sessions_lock:
        for cid in list(user_sessions): cleanup_session(cid)
    sys.exit(0)

signal.signal(signal.SIGTERM, graceful_shutdown)
signal.signal(signal.SIGINT, graceful_shutdown)

if __name__ == "__main__":
    threading.Thread(target=_health_server, daemon=True).start()
    threading.Thread(target=_auto_cleanup_loop, daemon=True).start()
    threading.Thread(target=queue_worker, daemon=True).start()

    try: bot.remove_webhook(); time.sleep(1)
    except Exception: pass

    log.info("🚀 البوت يعمل الآن ويستقبل الطلبات!")
    while not shutdown_event.is_set():
        try: bot.polling(non_stop=True, skip_pending=True, timeout=60)
        except Exception: time.sleep(5)
