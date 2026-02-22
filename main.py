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
import json
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


# ═══════════════════════════════════════════════
# 🗺️ قاعدة بيانات مناطق Google Cloud
# ═══════════════════════════════════════════════
GCP_REGIONS = {
    "us": {
        "name": "🇺🇸 أمريكا",
        "regions": {
            "us-central1": "آيوا (مجاني e2-micro ⭐)",
            "us-east1": "كارولينا الجنوبية (مجاني e2-micro ⭐)",
            "us-west1": "أوريغون (مجاني e2-micro ⭐)",
            "us-east4": "فيرجينيا",
            "us-west2": "لوس أنجلوس",
            "us-west3": "سالت ليك سيتي",
            "us-west4": "لاس فيغاس",
            "us-south1": "دالاس",
            "northamerica-northeast1": "مونتريال 🇨🇦",
            "northamerica-northeast2": "تورنتو 🇨🇦",
            "southamerica-east1": "ساو باولو 🇧🇷",
            "southamerica-west1": "سانتياغو 🇨🇱",
        }
    },
    "eu": {
        "name": "🇪🇺 أوروبا",
        "regions": {
            "europe-west1": "بلجيكا 🇧🇪",
            "europe-west2": "لندن 🇬🇧",
            "europe-west3": "فرانكفورت 🇩🇪",
            "europe-west4": "هولندا 🇳🇱",
            "europe-west6": "زيوريخ 🇨🇭",
            "europe-west8": "ميلان 🇮🇹",
            "europe-west9": "باريس 🇫🇷",
            "europe-west10": "برلين 🇩🇪",
            "europe-west12": "تورين 🇮🇹",
            "europe-north1": "فنلندا 🇫🇮",
            "europe-central2": "وارسو 🇵🇱",
            "europe-southwest1": "مدريد 🇪🇸",
        }
    },
    "asia": {
        "name": "🌏 آسيا",
        "regions": {
            "asia-east1": "تايوان 🇹🇼",
            "asia-east2": "هونغ كونغ 🇭🇰",
            "asia-northeast1": "طوكيو 🇯🇵",
            "asia-northeast2": "أوساكا 🇯🇵",
            "asia-northeast3": "سيول 🇰🇷",
            "asia-south1": "مومباي 🇮🇳",
            "asia-south2": "دلهي 🇮🇳",
            "asia-southeast1": "سنغافورة 🇸🇬",
            "asia-southeast2": "جاكرتا 🇮🇩",
        }
    },
    "me": {
        "name": "🌍 الشرق الأوسط وأفريقيا",
        "regions": {
            "me-west1": "تل أبيب 🇮🇱",
            "me-central1": "الدوحة 🇶🇦",
            "me-central2": "الدمام 🇸🇦",
            "africa-south1": "جوهانسبرغ 🇿🇦",
        }
    },
    "au": {
        "name": "🇦🇺 أستراليا",
        "regions": {
            "australia-southeast1": "سيدني 🇦🇺",
            "australia-southeast2": "ملبورن 🇦🇺",
        }
    }
}

# الخدمات المتاحة
SERVICES = {
    "cloudrun": {"name": "🚀 Cloud Run", "stars": "⭐⭐⭐⭐⭐", "desc": "الأسهل - TLS تلقائي"},
    "vm": {"name": "🖥️ VM (Compute)", "stars": "⭐⭐⭐⭐", "desc": "تحكم كامل - IP ثابت"},
    "gke": {"name": "☸️ GKE Kubernetes", "stars": "⭐⭐⭐", "desc": "متقدم - مكلف"},
    "appengine": {"name": "📱 App Engine", "stars": "⭐⭐⭐", "desc": "مستقر"},
    "functions": {"name": "⚡ Cloud Functions", "stars": "⭐⭐", "desc": "بسيط"},
    "shell": {"name": "🐚 Cloud Shell", "stars": "⭐⭐⭐⭐", "desc": "مجاني - 4 ساعات"},
}


# ═══════════════════════════════════════════════
# 🌐 Health Check + Xvfb + Browser (نفس السابق)
# ═══════════════════════════════════════════════
class HealthHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.end_headers()
        self.wfile.write(b"OK")
    def log_message(self, *a): pass

def start_health_server():
    port = int(os.environ.get('PORT', 8080))
    HTTPServer(('0.0.0.0', port), HealthHandler).serve_forever()

display = None
try:
    display = Display(visible=0, size=(1024, 768), color_depth=16)
    display.start()
    print("✅ Xvfb")
except:
    try: display = Display(visible=0, size=(800, 600)); display.start()
    except: pass

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
        c = f.read(); n = c.count(b'cdc_')
        if n > 0: f.seek(0); f.write(c.replace(b'cdc_', b'aaa_'))
    return patched

STEALTH_JS = '''
Object.defineProperty(navigator,'webdriver',{get:()=>undefined});
Object.defineProperty(navigator,'languages',{get:()=>['en-US','en']});
Object.defineProperty(navigator,'platform',{get:()=>'Win32'});
Object.defineProperty(navigator,'vendor',{get:()=>'Google Inc.'});
window.chrome=window.chrome||{};window.chrome.runtime={onMessage:{addListener:function(){}},sendMessage:function(){}};
Object.defineProperty(screen,'width',{get:()=>1920});Object.defineProperty(screen,'height',{get:()=>1080});
for(var p in window){if(/^cdc_/.test(p)){try{delete window[p]}catch(e){}}}
'''

def get_driver():
    browser = find_path(['chromium','chromium-browser'],['/usr/bin/chromium','/usr/bin/chromium-browser'])
    drv = find_path(['chromedriver'],['/usr/bin/chromedriver','/usr/lib/chromium/chromedriver'])
    if not browser or not drv: raise Exception("المتصفح غير موجود!")
    patched = patch_chromedriver(drv)
    ver = get_browser_version(browser)
    ua = f"Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/{ver}.0.0.0 Safari/537.36"
    o = Options(); o.binary_location = browser
    for a in ['--incognito','--disable-blink-features=AutomationControlled',f'--user-agent={ua}',
              '--lang=en-US','--no-sandbox','--disable-dev-shm-usage','--disable-gpu',
              '--window-size=1024,768','--no-first-run','--mute-audio','--disable-features=TranslateUI',
              '--disable-extensions','--disable-sync','--disable-background-timer-throttling',
              '--disable-backgrounding-occluded-windows','--disable-renderer-backgrounding']:
        o.add_argument(a)
    o.add_experimental_option("excludeSwitches",["enable-automation"])
    o.add_experimental_option('useAutomationExtension',False)
    o.page_load_strategy = 'eager'
    d = webdriver.Chrome(service=Service(executable_path=patched), options=o)
    try: d.execute_cdp_cmd('Page.addScriptToEvaluateOnNewDocument',{'source':STEALTH_JS})
    except: pass
    try: d.execute_cdp_cmd('Network.setUserAgentOverride',{"userAgent":ua,"platform":"Win32","acceptLanguage":"en-US,en;q=0.9"})
    except: pass
    d.set_page_load_timeout(30)
    return d

def safe_quit(d):
    if d:
        try: d.quit()
        except: pass
        gc.collect()

def cleanup_session(cid):
    with sessions_lock:
        if cid in user_sessions:
            s = user_sessions[cid]; s['running']=False; safe_quit(s.get('driver'))
            del user_sessions[cid]; gc.collect()

def is_on_shell_page(d):
    try: return "shell.cloud.google.com" in d.current_url or "ide.cloud.google.com" in d.current_url
    except: return False


# ═══════════════════════════════════════════════
# ⌨️ Terminal (إرسال أمر + قراءة نتيجة)
# ═══════════════════════════════════════════════
def send_cmd(driver, command):
    try:
        handles = driver.window_handles
        if handles: driver.switch_to.window(handles[-1])
        driver.switch_to.default_content()
    except: pass

    try:
        r = driver.execute_script("""
            function f(d){var t=d.querySelector('.xterm-helper-textarea');if(t)return t;
            var a=d.querySelectorAll('textarea');for(var i=0;i<a.length;i++){
            if(a[i].className.indexOf('xterm')!==-1||a[i].closest('.xterm'))return a[i];}return null;}
            var t=f(document);if(!t){var fr=document.querySelectorAll('iframe');
            for(var i=0;i<fr.length;i++){try{t=f(fr[i].contentDocument);if(t)break;}catch(e){}}}
            if(t){t.focus();return'OK';}return'NO';
        """)
        if r == 'OK':
            time.sleep(0.2)
            ac = ActionChains(driver)
            for c in command: ac.send_keys(c); ac.pause(random.uniform(0.01,0.04))
            ac.send_keys(Keys.RETURN); ac.perform()
            return True
    except: pass

    try:
        els = driver.find_elements(By.CSS_SELECTOR, ".xterm-screen,.xterm,[class*='xterm']")
        for el in els:
            try:
                if el.is_displayed() and el.size['width']>100:
                    ActionChains(driver).move_to_element(el).click().perform()
                    time.sleep(0.3)
                    ac = ActionChains(driver)
                    for c in command: ac.send_keys(c); ac.pause(0.03)
                    ac.send_keys(Keys.RETURN); ac.perform(); return True
            except: continue
    except: pass

    try:
        driver.execute_script("var e=document.querySelector('.xterm-helper-textarea');if(e)e.focus();")
        time.sleep(0.2)
        a = driver.switch_to.active_element
        for c in command: a.send_keys(c); time.sleep(0.02)
        a.send_keys(Keys.RETURN); return True
    except: pass
    return False


def read_terminal(driver):
    try:
        return driver.execute_script("""
            var r=document.querySelectorAll('.xterm-rows > div');
            if(!r.length){var x=document.querySelector('.xterm');if(x)r=x.querySelectorAll('.xterm-rows > div');}
            if(r.length){var l=[];r.forEach(function(row){var t=row.textContent||'';if(t.trim())l.push(t);});return l.join('\\n');}
            var s=document.querySelector('.xterm-screen');if(s)return s.textContent;return null;
        """)
    except: return None


def take_ss(driver):
    try:
        h = driver.window_handles
        if h: driver.switch_to.window(h[-1])
        p = driver.get_screenshot_as_png()
        b = io.BytesIO(p); b.name=f'ss_{int(time.time())}.png'; return b
    except: return None


# ═══════════════════════════════════════════════
# 🔍 نظام فحص السيرفرات + الأزرار التفاعلية
# ═══════════════════════════════════════════════
def run_scan(chat_id):
    """فحص الخدمات المفعّلة وإرسال القوائم"""
    with sessions_lock:
        if chat_id not in user_sessions:
            bot.send_message(chat_id, "❌ لا توجد جلسة."); return
        session = user_sessions[chat_id]

    driver = session['driver']

    if not is_on_shell_page(driver):
        bot.send_message(chat_id, "⚠️ يجب أن تكون في Cloud Shell أولاً."); return

    msg = bot.send_message(chat_id, "🔍 جاري فحص الخدمات المتاحة...")

    # ═══ تفعيل الخدمات ═══
    bot.edit_message_text("🔍 [1/4] تفعيل الخدمات...", chat_id=chat_id, message_id=msg.message_id)

    enable_script = (
        "gcloud services enable run.googleapis.com cloudbuild.googleapis.com "
        "containerregistry.googleapis.com compute.googleapis.com "
        "container.googleapis.com appengine.googleapis.com "
        "cloudfunctions.googleapis.com 2>/dev/null && echo 'ENABLE_DONE'"
    )
    send_cmd(driver, enable_script)
    time.sleep(10)

    # ═══ فحص Cloud Run regions ═══
    bot.edit_message_text("🔍 [2/4] فحص Cloud Run...", chat_id=chat_id, message_id=msg.message_id)

    send_cmd(driver, "echo '###CR_START###' && gcloud run regions list --format='value(locationId)' 2>/dev/null && echo '###CR_END###'")
    time.sleep(8)

    terminal_text = read_terminal(driver) or ""
    cr_regions = []
    if '###CR_START###' in terminal_text and '###CR_END###' in terminal_text:
        cr_section = terminal_text.split('###CR_START###')[1].split('###CR_END###')[0]
        cr_regions = [r.strip() for r in cr_section.strip().split('\n') if r.strip() and not r.startswith('#')]

    # ═══ فحص VM regions ═══
    bot.edit_message_text("🔍 [3/4] فحص Compute Engine...", chat_id=chat_id, message_id=msg.message_id)

    send_cmd(driver, "echo '###VM_START###' && gcloud compute regions list --filter='status=UP' --format='value(name)' 2>/dev/null && echo '###VM_END###'")
    time.sleep(8)

    terminal_text = read_terminal(driver) or ""
    vm_regions = []
    if '###VM_START###' in terminal_text and '###VM_END###' in terminal_text:
        vm_section = terminal_text.split('###VM_START###')[1].split('###VM_END###')[0]
        vm_regions = [r.strip() for r in vm_section.strip().split('\n') if r.strip() and not r.startswith('#')]

    # ═══ فحص الخدمات المفعّلة ═══
    bot.edit_message_text("🔍 [4/4] فحص الخدمات المفعّلة...", chat_id=chat_id, message_id=msg.message_id)

    send_cmd(driver, "echo '###SVC_START###' && gcloud services list --enabled --format='value(name)' 2>/dev/null && echo '###SVC_END###'")
    time.sleep(8)

    terminal_text = read_terminal(driver) or ""
    enabled_services = []
    if '###SVC_START###' in terminal_text and '###SVC_END###' in terminal_text:
        svc_section = terminal_text.split('###SVC_START###')[1].split('###SVC_END###')[0]
        enabled_services = [s.strip() for s in svc_section.strip().split('\n') if s.strip()]

    # ═══ حفظ نتائج الفحص ═══
    scan_results = {
        'cr_regions': cr_regions,
        'vm_regions': vm_regions,
        'enabled_services': enabled_services,
        'has_cloudrun': any('run.googleapis.com' in s for s in enabled_services),
        'has_compute': any('compute.googleapis.com' in s for s in enabled_services),
        'has_gke': any('container.googleapis.com' in s for s in enabled_services),
        'has_appengine': any('appengine.googleapis.com' in s for s in enabled_services),
        'has_functions': any('cloudfunctions.googleapis.com' in s for s in enabled_services),
    }
    session['scan_results'] = scan_results

    # ═══ عرض النتائج ═══
    cr_count = len(cr_regions) if cr_regions else "?"
    vm_count = len(vm_regions) if vm_regions else "?"

    result_text = (
        "✅ **تم الفحص!**\n\n"
        f"🚀 Cloud Run: **{cr_count}** منطقة\n"
        f"🖥️ Compute VM: **{vm_count}** منطقة\n"
        f"☸️ GKE: {'✅' if scan_results['has_gke'] else '❌'}\n"
        f"📱 App Engine: {'✅' if scan_results['has_appengine'] else '❌'}\n"
        f"⚡ Functions: {'✅' if scan_results['has_functions'] else '❌'}\n"
        f"🐚 Cloud Shell: ✅\n\n"
        "اختر المنطقة الجغرافية:"
    )

    # أزرار القارات
    mk = InlineKeyboardMarkup()
    for continent_key, continent_data in GCP_REGIONS.items():
        # حساب عدد المناطق المتاحة في كل قارة
        available = 0
        for region in continent_data['regions']:
            if region in cr_regions or region in vm_regions:
                available += 1
        # حتى لو الفحص لم يلتقط، نعرض الكل
        if available == 0:
            available = len(continent_data['regions'])

        mk.add(InlineKeyboardButton(
            f"{continent_data['name']} ({available} منطقة)",
            callback_data=f"continent_{continent_key}"
        ))

    mk.add(InlineKeyboardButton("🐚 Cloud Shell مباشر (بدون نشر)", callback_data="svc_shell_direct"))
    mk.add(InlineKeyboardButton("🔙 رجوع", callback_data="watch_mode"))

    bot.edit_message_text(result_text, chat_id=chat_id, message_id=msg.message_id,
                         parse_mode="Markdown", reply_markup=mk)


def show_continent_regions(chat_id, continent_key):
    """عرض مناطق قارة معينة"""
    if continent_key not in GCP_REGIONS:
        bot.send_message(chat_id, "❌ قارة غير معروفة"); return

    continent = GCP_REGIONS[continent_key]

    with sessions_lock:
        session = user_sessions.get(chat_id, {})
    scan = session.get('scan_results', {})
    cr = scan.get('cr_regions', [])
    vm = scan.get('vm_regions', [])

    text = f"📍 **{continent['name']}**\n\nاختر المنطقة:\n"

    mk = InlineKeyboardMarkup()
    for region_id, region_name in continent['regions'].items():
        services = []
        if region_id in cr or not cr:
            services.append("🚀")
        if region_id in vm or not vm:
            services.append("🖥️")

        # علامة مجاني
        free_tag = ""
        if region_id in ['us-central1', 'us-east1', 'us-west1']:
            free_tag = " 🆓"

        svc_text = "".join(services)
        btn_text = f"{svc_text} {region_id} - {region_name}{free_tag}"

        mk.add(InlineKeyboardButton(btn_text, callback_data=f"region_{region_id}"))

    mk.add(InlineKeyboardButton("🔙 رجوع للقارات", callback_data="scan_back"))

    bot.send_message(chat_id, text, parse_mode="Markdown", reply_markup=mk)


def show_region_services(chat_id, region_id):
    """عرض الخدمات المتاحة في منطقة معينة"""
    # البحث عن اسم المنطقة
    region_name = region_id
    for cont in GCP_REGIONS.values():
        if region_id in cont['regions']:
            region_name = cont['regions'][region_id]
            break

    with sessions_lock:
        session = user_sessions.get(chat_id, {})
    scan = session.get('scan_results', {})

    free_tag = ""
    if region_id in ['us-central1', 'us-east1', 'us-west1']:
        free_tag = "\n🆓 **هذه المنطقة تدعم VM مجاني (e2-micro)**"

    text = (
        f"📍 **{region_id}** - {region_name}{free_tag}\n\n"
        "اختر الخدمة للنشر:"
    )

    mk = InlineKeyboardMarkup()

    # Cloud Run
    if scan.get('has_cloudrun', True):
        mk.add(InlineKeyboardButton(
            "🚀 Cloud Run (الأسهل ⭐⭐⭐⭐⭐)",
            callback_data=f"deploy_cloudrun_{region_id}"
        ))

    # VM
    if scan.get('has_compute', True):
        vm_label = "🖥️ VM Compute"
        if region_id in ['us-central1', 'us-east1', 'us-west1']:
            vm_label += " (e2-micro مجاني 🆓)"
        else:
            vm_label += " (⭐⭐⭐⭐)"
        mk.add(InlineKeyboardButton(vm_label, callback_data=f"deploy_vm_{region_id}"))

    # GKE
    if scan.get('has_gke', False):
        mk.add(InlineKeyboardButton(
            "☸️ GKE Kubernetes (متقدم)",
            callback_data=f"deploy_gke_{region_id}"
        ))

    # App Engine (منطقة واحدة فقط)
    if scan.get('has_appengine', False):
        mk.add(InlineKeyboardButton(
            "📱 App Engine Flex",
            callback_data=f"deploy_appengine_{region_id}"
        ))

    # Functions
    if scan.get('has_functions', False):
        mk.add(InlineKeyboardButton(
            "⚡ Cloud Functions Gen2",
            callback_data=f"deploy_functions_{region_id}"
        ))

    mk.add(InlineKeyboardButton("🔙 رجوع", callback_data=f"continent_{get_continent(region_id)}"))

    bot.send_message(chat_id, text, parse_mode="Markdown", reply_markup=mk)


def get_continent(region_id):
    """تحديد القارة من اسم المنطقة"""
    for key, data in GCP_REGIONS.items():
        if region_id in data['regions']:
            return key
    if region_id.startswith('us-') or region_id.startswith('north') or region_id.startswith('south'):
        return 'us'
    elif region_id.startswith('europe'):
        return 'eu'
    elif region_id.startswith('asia'):
        return 'asia'
    elif region_id.startswith('me-') or region_id.startswith('africa'):
        return 'me'
    elif region_id.startswith('australia'):
        return 'au'
    return 'us'


def handle_deploy(chat_id, service_type, region_id):
    """معالجة طلب النشر"""
    with sessions_lock:
        if chat_id not in user_sessions:
            bot.send_message(chat_id, "❌ لا توجد جلسة."); return
        session = user_sessions[chat_id]

    driver = session['driver']

    service_names = {
        'cloudrun': '🚀 Cloud Run',
        'vm': '🖥️ VM Compute',
        'gke': '☸️ GKE',
        'appengine': '📱 App Engine',
        'functions': '⚡ Functions',
        'shell': '🐚 Cloud Shell',
    }

    svc_name = service_names.get(service_type, service_type)

    # تأكيد
    mk = InlineKeyboardMarkup()
    mk.row(
        InlineKeyboardButton("✅ نعم، ابدأ النشر", callback_data=f"confirm_{service_type}_{region_id}"),
        InlineKeyboardButton("❌ إلغاء", callback_data="scan_back")
    )

    bot.send_message(chat_id,
        f"🚀 **تأكيد النشر**\n\n"
        f"الخدمة: **{svc_name}**\n"
        f"المنطقة: **{region_id}**\n\n"
        f"هل تريد المتابعة؟",
        parse_mode="Markdown", reply_markup=mk
    )


def execute_deploy(chat_id, service_type, region_id):
    """تنفيذ النشر الفعلي"""
    with sessions_lock:
        if chat_id not in user_sessions:
            bot.send_message(chat_id, "❌ لا توجد جلسة."); return
        session = user_sessions[chat_id]

    driver = session['driver']
    session['cmd_mode'] = True

    msg = bot.send_message(chat_id, f"⏳ جاري تجهيز النشر على {region_id}...")

    # أوامر النشر حسب نوع الخدمة
    if service_type == 'cloudrun':
        commands = [
            f"# 🚀 نشر VLESS على Cloud Run في {region_id}",
            f"gcloud config set run/region {region_id}",
            f"echo '✅ تم تحديد المنطقة: {region_id}'",
            "echo '📋 جاهز للنشر! أرسل أوامر Dockerfile و deploy'",
        ]
    elif service_type == 'vm':
        zone = f"{region_id}-a"  # أول zone
        machine = "e2-micro" if region_id in ['us-central1','us-east1','us-west1'] else "e2-small"
        commands = [
            f"# 🖥️ إنشاء VM في {region_id}",
            f"gcloud config set compute/region {region_id}",
            f"gcloud config set compute/zone {zone}",
            f"echo '✅ المنطقة: {region_id}, Zone: {zone}'",
            f"echo '📋 نوع الجهاز: {machine}'",
            "echo '📋 جاهز! أرسل أمر إنشاء VM'",
        ]
    elif service_type == 'shell':
        commands = [
            "# 🐚 تشغيل VLESS مباشرة في Cloud Shell",
            "echo '✅ Cloud Shell جاهز!'",
            "echo '📋 يمكنك تشغيل VLESS مباشرة هنا'",
            "echo '⏰ تذكر: Cloud Shell مؤقت (4 ساعات)'",
        ]
    else:
        commands = [
            f"gcloud config set compute/region {region_id}",
            f"echo '✅ تم تحديد المنطقة: {region_id}'",
        ]

    # تنفيذ الأوامر
    for cmd in commands:
        if cmd.startswith('#'):
            continue
        send_cmd(driver, cmd)
        time.sleep(2)

    time.sleep(3)

    # لقطة شاشة + نتيجة
    bio = take_ss(driver)
    output = read_terminal(driver) or ""

    # أخذ آخر 10 أسطر
    lines = output.split('\n')
    last_lines = '\n'.join(lines[-10:]) if len(lines) > 10 else output

    result_text = (
        f"✅ **تم التجهيز!**\n\n"
        f"📍 المنطقة: `{region_id}`\n\n"
        f"📋 النتيجة:\n```\n{last_lines[:2000]}\n```\n\n"
        f"⌨️ يمكنك الآن إرسال أوامر النشر"
    )

    mk = InlineKeyboardMarkup()
    mk.row(
        InlineKeyboardButton("⌨️ وضع الأوامر", callback_data="cmd_mode"),
        InlineKeyboardButton("📸 لقطة", callback_data="screenshot")
    )
    mk.row(
        InlineKeyboardButton("🔍 فحص جديد", callback_data="scan"),
        InlineKeyboardButton("🔙 رجوع للبث", callback_data="watch_mode")
    )

    try:
        bot.edit_message_text(result_text, chat_id=chat_id, message_id=msg.message_id,
                           parse_mode="Markdown", reply_markup=mk)
    except:
        bot.send_message(chat_id, result_text, parse_mode="Markdown", reply_markup=mk)

    if bio:
        bot.send_photo(chat_id, bio, caption="📸 حالة Terminal")


# ═══════════════════════════════════════════════
# 🎛️ لوحة التحكم (مُحدَّثة مع زر الفحص)
# ═══════════════════════════════════════════════
def panel(cmd_mode=False):
    mk = InlineKeyboardMarkup()
    if cmd_mode:
        mk.row(
            InlineKeyboardButton("📸 لقطة", callback_data="screenshot"),
            InlineKeyboardButton("🔙 رجوع", callback_data="watch_mode")
        )
        mk.row(
            InlineKeyboardButton("🔍 فحص سيرفرات", callback_data="scan"),
            InlineKeyboardButton("⏹ إيقاف", callback_data="stop")
        )
    else:
        mk.row(
            InlineKeyboardButton("⌨️ أوامر", callback_data="cmd_mode"),
            InlineKeyboardButton("🔍 فحص سيرفرات", callback_data="scan")
        )
        mk.row(
            InlineKeyboardButton("📸 لقطة", callback_data="screenshot"),
            InlineKeyboardButton("⏹ إيقاف", callback_data="stop")
        )
        mk.row(InlineKeyboardButton("🔄 تحديث", callback_data="refresh"))
    return mk


# ═══════════════════════════════════════════════
# 🤖 صفحات Google + حلقة البث (مثل السابق)
# ═══════════════════════════════════════════════
def handle_google_pages(driver, session):
    status = "مراقبة..."
    try: body = driver.find_element(By.TAG_NAME, "body").text
    except: return status

    if "cloud shell" in body.lower() and "continue" in body.lower() and "free" in body.lower():
        try:
            for btn in driver.find_elements(By.XPATH,
                "//a[contains(text(),'Continue')]|//button[contains(text(),'Continue')]|//*[contains(text(),'Continue')]"):
                try:
                    if btn.is_displayed() and btn.is_enabled():
                        time.sleep(0.5); 
                        try: btn.click()
                        except: driver.execute_script("arguments[0].click();",btn)
                        time.sleep(3); return "✅ Continue ✔️"
                except: continue
        except: pass
        return "☁️ popup..."

    if "verify it" in body.lower():
        try:
            for btn in driver.find_elements(By.XPATH,"//button[contains(.,'Continue')]|//input[@value='Continue']"):
                if btn.is_displayed(): btn.click(); time.sleep(3); return "✅ Verify ✔️"
        except: pass
        return "🔐 Verify..."

    if "I understand" in body:
        try:
            for btn in driver.find_elements(By.XPATH,"//*[contains(text(),'I understand')]"):
                if btn.is_displayed(): btn.click(); time.sleep(2); return "✅ ✔️"
        except: pass

    if "couldn't sign you in" in body.lower():
        try: driver.delete_all_cookies(); time.sleep(1); driver.get(session.get('url','about:blank')); time.sleep(5)
        except: pass; return "⚠️ رفض..."

    if "authorize" in body.lower():
        try:
            for btn in driver.find_elements(By.XPATH,"//button[contains(.,'Authorize')]"):
                if btn.is_displayed(): btn.click(); session['auth']=True; time.sleep(2); return "✅ Auth ✔️"
        except: pass

    if "gemini" in body.lower() and "dismiss" in body.lower():
        try:
            for btn in driver.find_elements(By.XPATH,"//button[contains(.,'Dismiss')]"):
                if btn.is_displayed(): btn.click(); time.sleep(1)
        except: pass

    url = driver.current_url
    if "shell.cloud.google.com" in url or "ide.cloud.google.com" in url:
        session['terminal_ready']=True; return "✅ Terminal ⌨️"
    elif "console.cloud.google.com" in url: return "📊 Console"
    elif "accounts.google.com" in url: return "🔐 تسجيل..."
    return status


def stream_loop(chat_id, gen):
    with sessions_lock:
        if chat_id not in user_sessions: return
        session = user_sessions[chat_id]
    driver = session['driver']; flash=True; ec=0; de=0; cy=0

    while session['running'] and session.get('gen')==gen:
        if session.get('cmd_mode'): time.sleep(3); continue
        time.sleep(random.uniform(4,6))
        if not session['running'] or session.get('gen')!=gen: break
        cy+=1
        try:
            h=driver.window_handles
            if h: driver.switch_to.window(h[-1])
            st=handle_google_pages(driver,session)
            url=driver.current_url
            if not session.get('shell_opened'):
                if "console.cloud.google.com" in url or "myaccount.google.com" in url:
                    pid=session.get('project_id')
                    if pid:
                        try: driver.get(f"https://shell.cloud.google.com/?project={pid}&pli=1&show=terminal"); session['shell_opened']=True; time.sleep(5); st="🚀 Shell..."
                        except: pass
            if session.get('terminal_ready') and not session.get('terminal_notified'):
                session['terminal_notified']=True
                try: bot.send_message(chat_id,"🖥️ **Terminal جاهز!**\n\n🔍 اضغط **فحص سيرفرات** للبدء\nأو **⌨️ أوامر** للكتابة مباشرة",parse_mode="Markdown")
                except: pass
            p=driver.get_screenshot_as_png(); b=io.BytesIO(p); b.name=f'l_{int(time.time())}.png'
            flash=not flash; ic="🔴" if flash else "⭕"; now=datetime.now().strftime("%H:%M:%S")
            pr=f"📁 {session.get('project_id')}" if session.get('project_id') else ""
            ts=" | ⌨️" if session.get('terminal_ready') else ""
            cap=f"{ic} بث 🕶️\n{pr}\n📌 {st}{ts}\n⏱ {now}"
            bot.edit_message_media(media=InputMediaPhoto(b,caption=cap),chat_id=chat_id,message_id=session['msg_id'],reply_markup=panel(session.get('cmd_mode',False)))
            ec=0;de=0
            if cy%15==0: gc.collect()
        except Exception as e:
            em=str(e).lower()
            if "message is not modified" in em: continue
            ec+=1
            if "too many requests" in em or "retry after" in em:
                w=re.search(r'retry after (\d+)',em); time.sleep(int(w.group(1)) if w else 5)
            elif any(k in em for k in ['session','disconnected','crashed']):
                de+=1
                if de>=3:
                    try: safe_quit(driver); d=get_driver(); session['driver']=d; driver=d; driver.get(session.get('url','about:blank')); session['shell_opened']=False; de=0; ec=0; time.sleep(5)
                    except: session['running']=False; break
            elif ec>=5:
                try: driver.refresh(); ec=0
                except: de+=1
    gc.collect()


def start_stream(chat_id, url):
    old_d=None
    with sessions_lock:
        if chat_id in user_sessions:
            o=user_sessions[chat_id]; o['running']=False; o['gen']=o.get('gen',0)+1; old_d=o.get('driver')
    bot.send_message(chat_id,"⚡ جاري التجهيز...")
    if old_d: safe_quit(old_d); time.sleep(2)
    pm=re.search(r'(qwiklabs-gcp-[\w-]+)',url); pid=pm.group(1) if pm else None
    try: driver=get_driver(); bot.send_message(chat_id,"✅ المتصفح جاهز")
    except Exception as e: bot.send_message(chat_id,f"❌ `{str(e)[:300]}`",parse_mode="Markdown"); return
    gen=int(time.time())
    with sessions_lock:
        user_sessions[chat_id]={'driver':driver,'running':False,'msg_id':None,'url':url,'project_id':pid,'shell_opened':False,'auth':False,'terminal_ready':False,'terminal_notified':False,'cmd_mode':False,'gen':gen,'scan_results':{}}
    session=user_sessions[chat_id]
    bot.send_message(chat_id,"🌐 فتح الرابط...")
    try: driver.get(url)
    except: pass
    time.sleep(5)
    try:
        h=driver.window_handles
        if h: driver.switch_to.window(h[-1])
        p=driver.get_screenshot_as_png(); b=io.BytesIO(p); b.name=f's_{int(time.time())}.png'
        m=bot.send_photo(chat_id,b,caption="🔴 بث 🕶️\n📌 بدء...",reply_markup=panel())
        session['msg_id']=m.message_id; session['running']=True
        threading.Thread(target=stream_loop,args=(chat_id,gen),daemon=True).start()
        bot.send_message(chat_id,"✅ البث يعمل!")
    except Exception as e:
        bot.send_message(chat_id,f"❌ `{str(e)[:200]}`",parse_mode="Markdown"); cleanup_session(chat_id)


# ═══════════════════════════════════════════════
# ⌨️ تنفيذ أمر مع النتيجة
# ═══════════════════════════════════════════════
def execute_command(chat_id, command):
    with sessions_lock:
        if chat_id not in user_sessions: bot.send_message(chat_id,"❌"); return
        session=user_sessions[chat_id]
    driver=session['driver']
    if not is_on_shell_page(driver): bot.send_message(chat_id,"⚠️ لست في Shell"); return
    session['terminal_ready']=True
    sm=bot.send_message(chat_id,f"⏳ `{command}`",parse_mode="Markdown")
    tb=read_terminal(driver) or ""
    ok=send_cmd(driver,command)
    if ok:
        wt=3
        if any(k in command.lower() for k in ['install','apt','pip','gcloud','docker','kubectl']): wt=10
        elif any(k in command.lower() for k in ['cat','echo','ls','pwd','whoami']): wt=2
        time.sleep(wt)
        ta=read_terminal(driver) or ""
        out=""
        if ta and ta!=tb:
            if len(ta)>len(tb): out=ta[len(tb):].strip()
            if not out:
                lines=ta.split('\n'); cl=[]
                found=False
                for l in lines:
                    if command in l and ('$' in l or '>' in l): found=True; continue
                    if found:
                        if re.match(r'^[\w\-]+@.*\$\s*$',l.strip()): break
                        cl.append(l)
                out='\n'.join(cl).strip()
        bio=take_ss(driver)
        if out:
            if len(out)>3900: out=out[:3900]+"\n..."
            try: bot.send_message(chat_id,f"✅ `{command}`\n\n```\n{out}\n```",parse_mode="Markdown",reply_markup=panel(True))
            except: bot.send_message(chat_id,f"✅ {command}\n\n{out}",reply_markup=panel(True))
        else:
            bot.send_message(chat_id,f"✅ `{command}`\n📋 (شاهد الصورة)",parse_mode="Markdown")
        if bio: bot.send_photo(chat_id,bio,caption=f"📸 `{command}`",parse_mode="Markdown",reply_markup=panel(True))
    else:
        bot.send_message(chat_id,"⚠️ فشل. 🔄 حدّث وأعد")
    try: bot.delete_message(chat_id,sm.message_id)
    except: pass


# ═══════════════════════════════════════════════
# 📨 أوامر تيليغرام
# ═══════════════════════════════════════════════
@bot.message_handler(commands=['start'])
def c_start(m):
    bot.reply_to(m,"🚀 مرحباً!\n\nأرسل رابط:\n`https://www.skills.google/google_sso`\n\n"
        "بعد Shell:\n🔍 فحص سيرفرات\n⌨️ أوامر\n`/cmd ls`\n`/scan`",parse_mode="Markdown")

@bot.message_handler(commands=['scan'])
def c_scan(m):
    threading.Thread(target=run_scan,args=(m.chat.id,),daemon=True).start()

@bot.message_handler(commands=['cmd'])
def c_cmd(m):
    p=m.text.split(maxsplit=1)
    if len(p)<2: bot.reply_to(m,"`/cmd الأمر`",parse_mode="Markdown"); return
    threading.Thread(target=execute_command,args=(m.chat.id,p[1]),daemon=True).start()

@bot.message_handler(commands=['ss','screenshot'])
def c_ss(m):
    with sessions_lock:
        if m.chat.id not in user_sessions: return
        s=user_sessions[m.chat.id]
    b=take_ss(s['driver'])
    if b: bot.send_photo(m.chat.id,b,caption="📸")

@bot.message_handler(func=lambda m: m.text and m.text.startswith('https://www.skills.google/google_sso'))
def h_url(m): threading.Thread(target=start_stream,args=(m.chat.id,m.text),daemon=True).start()

@bot.message_handler(func=lambda m: m.text and m.text.startswith('http'))
def h_bad(m): bot.reply_to(m,"❌ يجب أن يبدأ بـ:\n`https://www.skills.google/google_sso`",parse_mode="Markdown")

@bot.message_handler(func=lambda m: m.text and not m.text.startswith('/') and not m.text.startswith('http'))
def h_txt(m):
    cid=m.chat.id
    with sessions_lock:
        if cid not in user_sessions: return
        s=user_sessions[cid]
    if s.get('cmd_mode'):
        threading.Thread(target=execute_command,args=(cid,m.text),daemon=True).start()
    elif is_on_shell_page(s.get('driver')):
        bot.reply_to(m,"💡 اضغط **⌨️ أوامر** أولاً أو `/cmd "+m.text+"`",parse_mode="Markdown")


# ═══════════════════════════════════════════════
# 🎛️ Callbacks (أزرار)
# ═══════════════════════════════════════════════
@bot.callback_query_handler(func=lambda call: True)
def on_cb(call):
    cid=call.message.chat.id
    data=call.data
    try:
        with sessions_lock:
            if cid not in user_sessions:
                bot.answer_callback_query(call.id,"لا توجد جلسة."); return
            s=user_sessions[cid]

        # ─── أزرار الأساسية ───
        if data=="stop":
            s['running']=False; s['gen']=s.get('gen',0)+1
            bot.answer_callback_query(call.id,"إيقاف")
            try: bot.edit_message_caption("🛑",chat_id=cid,message_id=s['msg_id'])
            except: pass
            safe_quit(s.get('driver'))
            with sessions_lock:
                if cid in user_sessions: del user_sessions[cid]

        elif data=="refresh":
            bot.answer_callback_query(call.id,"تحديث...")
            try: s['driver'].refresh()
            except: pass

        elif data=="screenshot":
            bot.answer_callback_query(call.id,"📸")
            b=take_ss(s['driver'])
            if b: bot.send_photo(cid,b,caption="📸",reply_markup=panel(s.get('cmd_mode',False)))

        elif data=="cmd_mode":
            s['cmd_mode']=True
            if is_on_shell_page(s.get('driver')): s['terminal_ready']=True
            bot.answer_callback_query(call.id,"⌨️")
            bot.send_message(cid,"⌨️ **وضع الأوامر!**\n\nاكتب أي أمر:\n`ls -la`\n`/scan` للفحص\n🔙 للرجوع",parse_mode="Markdown")

        elif data=="watch_mode":
            s['cmd_mode']=False
            bot.answer_callback_query(call.id,"🔙")
            bot.send_message(cid,"👁️ وضع البث")

        # ─── فحص السيرفرات ───
        elif data=="scan":
            bot.answer_callback_query(call.id,"🔍 بدء الفحص...")
            threading.Thread(target=run_scan,args=(cid,),daemon=True).start()

        elif data=="scan_back":
            # إعادة عرض القارات
            bot.answer_callback_query(call.id,"🔙")
            threading.Thread(target=run_scan,args=(cid,),daemon=True).start()

        # ─── اختيار قارة ───
        elif data.startswith("continent_"):
            continent_key = data.replace("continent_","")
            bot.answer_callback_query(call.id, GCP_REGIONS.get(continent_key,{}).get('name',''))
            show_continent_regions(cid, continent_key)

        # ─── اختيار منطقة ───
        elif data.startswith("region_"):
            region_id = data.replace("region_","")
            bot.answer_callback_query(call.id, region_id)
            show_region_services(cid, region_id)

        # ─── اختيار خدمة للنشر ───
        elif data.startswith("deploy_"):
            parts = data.split("_", 2)  # deploy_cloudrun_us-central1
            if len(parts) >= 3:
                svc_type = parts[1]
                region = parts[2]
                bot.answer_callback_query(call.id, f"🚀 {svc_type}")
                handle_deploy(cid, svc_type, region)

        # ─── تأكيد النشر ───
        elif data.startswith("confirm_"):
            parts = data.split("_", 2)
            if len(parts) >= 3:
                svc_type = parts[1]
                region = parts[2]
                bot.answer_callback_query(call.id, "⚙️ جاري النشر...")
                threading.Thread(target=execute_deploy,args=(cid,svc_type,region),daemon=True).start()

        # ─── Cloud Shell مباشر ───
        elif data=="svc_shell_direct":
            bot.answer_callback_query(call.id,"🐚")
            s['cmd_mode']=True
            bot.send_message(cid,
                "🐚 **Cloud Shell مباشر**\n\n"
                "✅ يمكنك تشغيل VLESS مباشرة هنا!\n"
                "⏰ مؤقت: 4 ساعات\n\n"
                "اكتب أوامر النشر مباشرة ⌨️",
                parse_mode="Markdown",
                reply_markup=panel(True))

    except Exception as e:
        print(f"⚠️ callback: {e}")


# ═══════════════════════════════════════════════
# 🏁 التشغيل
# ═══════════════════════════════════════════════
if __name__ == '__main__':
    print("="*50)
    print("🚂 Server Scanner + Deploy System")
    print(f"🌐 Port: {os.environ.get('PORT',8080)}")
    print("="*50)
    threading.Thread(target=start_health_server,daemon=True).start()
    while True:
        try: bot.polling(non_stop=True,timeout=60,long_polling_timeout=60)
        except Exception as e: print(f"⚠️ {e}"); time.sleep(5)
