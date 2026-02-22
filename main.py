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
import logging
from datetime import datetime
from http.server import HTTPServer, BaseHTTPRequestHandler
from telebot.types import InputMediaPhoto, InlineKeyboardMarkup, InlineKeyboardButton

from selenium import webdriver
from selenium.webdriver.chrome.service import Service
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.common.by import By
from selenium.webdriver.common.keys import Keys
from selenium.webdriver.common.action_chains import ActionChains
from selenium.common.exceptions import TimeoutException
from pyvirtualdisplay import Display

# ══════════════════════════════════════════════════════════
#  Logging
# ══════════════════════════════════════════════════════════
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(levelname)s] %(message)s',
    datefmt='%H:%M:%S'
)
log = logging.getLogger(__name__)

TOKEN = os.environ.get('BOT_TOKEN')
if not TOKEN:
    raise ValueError("BOT_TOKEN غير موجود!")

bot = telebot.TeleBot(TOKEN)
user_sessions = {}
sessions_lock = threading.Lock()
chromedriver_lock = threading.Lock()

# ══════════════════════════════════════════════════════════
#  Health Server
# ══════════════════════════════════════════════════════════

class HealthHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        if self.path in ('/', '/health', '/healthz'):
            self.send_response(200)
            self.send_header('Content-Type', 'text/html')
            self.end_headers()
            with sessions_lock:
                active = len(user_sessions)
            self.wfile.write(
                f"<h1>Bot Running</h1><p>Sessions: {active}</p>".encode()
            )
        else:
            self.send_response(404)
            self.end_headers()

    def log_message(self, *args):
        pass


def start_health_server():
    port = int(os.environ.get('PORT', 8080))
    log.info(f"🌐 Health Check: port {port}")
    HTTPServer(('0.0.0.0', port), HealthHandler).serve_forever()


# ══════════════════════════════════════════════════════════
#  Virtual Display
# ══════════════════════════════════════════════════════════

display = None
try:
    display = Display(visible=0, size=(1024, 768), color_depth=16)
    display.start()
    log.info("✅ Xvfb يعمل")
except Exception:
    try:
        display = Display(visible=0, size=(800, 600))
        display.start()
        log.info("✅ Xvfb يعمل (fallback)")
    except Exception as e:
        log.error(f"❌ Xvfb فشل: {e}")


# ══════════════════════════════════════════════════════════
#  Utilities
# ══════════════════════════════════════════════════════════

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
        r = subprocess.run([path, '--version'],
                           capture_output=True, text=True, timeout=5)
        m = re.search(r'(\d+)', r.stdout)
        return m.group(1) if m else "120"
    except Exception:
        return "120"


def patch_chromedriver(original_path):
    with chromedriver_lock:
        patched = '/tmp/chromedriver_patched'
        shutil.copy2(original_path, patched)
        os.chmod(patched, 0o755)
        with open(patched, 'r+b') as f:
            content = f.read()
            count = content.count(b'cdc_')
            if count > 0:
                f.seek(0)
                f.write(content.replace(b'cdc_', b'aaa_'))
                log.info(f"✅ chromedriver: {count} cdc_ removed")
    return patched


def safe_navigate(driver, url):
    """Navigate using JS first to avoid Selenium timeout crashes."""
    try:
        js_url = json.dumps(url)
        driver.execute_script(f'window.location.href = {js_url};')
        log.info(f"✅ Navigate [JS]: {url[:100]}...")
        return True
    except Exception as e:
        log.debug(f"JS nav failed: {e}")

    try:
        js_url = json.dumps(url)
        driver.execute_script(f'window.location.assign({js_url});')
        log.info(f"✅ Navigate [JS assign]: {url[:100]}...")
        return True
    except Exception as e:
        log.debug(f"JS assign failed: {e}")

    try:
        driver.get(url)
        log.info(f"✅ Navigate [get]: {url[:100]}...")
        return True
    except TimeoutException:
        log.info(f"⏱️ Navigate timeout (page loading): {url[:80]}...")
        return True
    except Exception as e:
        log.error(f"❌ Navigation failed: {e}")
        return False


def get_current_url_safe(driver):
    try:
        return driver.current_url
    except Exception:
        return ""


# ══════════════════════════════════════════════════════════
#  Stealth JavaScript
# ══════════════════════════════════════════════════════════

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
'''


# ══════════════════════════════════════════════════════════
#  Browser Driver
# ══════════════════════════════════════════════════════════

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
    ua = (f"Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
          f"AppleWebKit/537.36 (KHTML, like Gecko) "
          f"Chrome/{version}.0.0.0 Safari/537.36")

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
        driver.execute_cdp_cmd('Page.addScriptToEvaluateOnNewDocument',
                               {'source': STEALTH_JS})
    except Exception:
        pass
    try:
        driver.execute_cdp_cmd('Network.setUserAgentOverride', {
            "userAgent": ua,
            "platform": "Win32",
            "acceptLanguage": "en-US,en;q=0.9"
        })
    except Exception:
        pass

    driver.set_page_load_timeout(45)
    log.info("✅ المتصفح جاهز")
    return driver


# ══════════════════════════════════════════════════════════
#  Session Management
# ══════════════════════════════════════════════════════════

def safe_quit(driver):
    if driver:
        try:
            driver.quit()
        except Exception:
            pass
        gc.collect()


def cleanup_session(chat_id):
    with sessions_lock:
        if chat_id in user_sessions:
            s = user_sessions[chat_id]
            s['running'] = False
            safe_quit(s.get('driver'))
            del user_sessions[chat_id]
            gc.collect()


def get_session(chat_id):
    with sessions_lock:
        return user_sessions.get(chat_id)


# ══════════════════════════════════════════════════════════
#  UI Panel
# ══════════════════════════════════════════════════════════

def panel(cmd_mode=False):
    mk = InlineKeyboardMarkup()
    if cmd_mode:
        mk.row(
            InlineKeyboardButton("📸 لقطة", callback_data="screenshot"),
            InlineKeyboardButton("🔙 رجوع للبث", callback_data="watch_mode")
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


# ══════════════════════════════════════════════════════════
#  Shell Detection
# ══════════════════════════════════════════════════════════

def is_on_shell_page(driver):
    if not driver:
        return False
    try:
        url = driver.current_url
        return ("shell.cloud.google.com" in url
                or "ide.cloud.google.com" in url)
    except Exception:
        return False


# ══════════════════════════════════════════════════════════
#  Terminal Interaction
# ══════════════════════════════════════════════════════════

def send_command_to_terminal(driver, command):
    if not driver:
        return False

    try:
        handles = driver.window_handles
        if handles:
            driver.switch_to.window(handles[-1])
        driver.switch_to.default_content()
    except Exception:
        pass

    # Method 1: xterm textarea via JS
    try:
        result = driver.execute_script("""
            function findTA(doc) {
                var ta = doc.querySelector('.xterm-helper-textarea');
                if (ta) return ta;
                var all = doc.querySelectorAll('textarea');
                for (var i = 0; i < all.length; i++) {
                    if (all[i].className.indexOf('xterm') !== -1 ||
                        all[i].closest('.xterm') || all[i].closest('.terminal'))
                        return all[i];
                }
                return null;
            }
            var ta = findTA(document);
            if (!ta) {
                var frames = document.querySelectorAll('iframe');
                for (var i = 0; i < frames.length; i++) {
                    try { ta = findTA(frames[i].contentDocument);
                          if (ta) break; }
                    catch(e) {}
                }
            }
            if (ta) { ta.focus(); return 'FOUND'; }
            return 'NOT_FOUND';
        """)
        if result == 'FOUND':
            time.sleep(0.2)
            actions = ActionChains(driver)
            for char in command:
                actions.send_keys(char)
                actions.pause(random.uniform(0.02, 0.06))
            actions.send_keys(Keys.RETURN)
            actions.perform()
            log.info(f"⌨️ [M1] أمر: {command[:60]}")
            return True
    except Exception as e:
        log.debug(f"Method 1: {e}")

    # Method 2: Click on xterm element
    try:
        xterm_els = driver.find_elements(By.CSS_SELECTOR,
            ".xterm-screen, .xterm-rows, canvas.xterm-link-layer, "
            ".xterm, [class*='xterm']")
        for el in xterm_els:
            try:
                if el.is_displayed() and el.size['width'] > 100:
                    ActionChains(driver).move_to_element(el).click().perform()
                    time.sleep(0.3)
                    actions = ActionChains(driver)
                    for char in command:
                        actions.send_keys(char)
                        actions.pause(random.uniform(0.02, 0.06))
                    actions.send_keys(Keys.RETURN)
                    actions.perform()
                    log.info(f"⌨️ [M2] أمر: {command[:60]}")
                    return True
            except Exception:
                continue
    except Exception as e:
        log.debug(f"Method 2: {e}")

    # Method 3: Focus + active element
    try:
        driver.execute_script("""
            var el = document.querySelector('.xterm-helper-textarea') ||
                     document.querySelector('.xterm-screen') ||
                     document.querySelector('.xterm');
            if (el) el.focus();
        """)
        time.sleep(0.2)
        active = driver.switch_to.active_element
        for char in command:
            active.send_keys(char)
            time.sleep(random.uniform(0.01, 0.04))
        active.send_keys(Keys.RETURN)
        log.info(f"⌨️ [M3] أمر: {command[:60]}")
        return True
    except Exception as e:
        log.debug(f"Method 3: {e}")

    log.warning(f"❌ فشل إرسال: {command[:60]}")
    return False


def get_terminal_output(driver):
    if not driver:
        return None

    try:
        text = driver.execute_script("""
            var rows = document.querySelectorAll('.xterm-rows > div');
            if (rows.length === 0) {
                var xterm = document.querySelector('.xterm');
                if (xterm) rows = xterm.querySelectorAll('.xterm-rows > div');
            }
            if (rows.length > 0) {
                var lines = [];
                rows.forEach(function(row) {
                    var t = row.textContent || row.innerText || '';
                    if (t.trim().length > 0) lines.push(t);
                });
                return lines.join('\\n');
            }
            return null;
        """)
        if text and text.strip():
            return text.strip()
    except Exception:
        pass

    try:
        text = driver.execute_script("""
            var s = document.querySelector('.xterm-screen');
            if (s) return s.textContent || s.innerText;
            var x = document.querySelector('.xterm');
            if (x) return x.textContent || x.innerText;
            return null;
        """)
        if text and text.strip():
            return text.strip()
    except Exception:
        pass

    try:
        text = driver.execute_script("""
            var live = document.querySelector('[aria-live]');
            if (live) return live.textContent || live.innerText;
            return null;
        """)
        if text and text.strip():
            return text.strip()
    except Exception:
        pass

    return None


def extract_command_result(full_output, command):
    if not full_output:
        return None
    lines = full_output.split('\n')
    cmd_line_idx = -1
    for i, line in enumerate(lines):
        if command in line and ('$' in line or '>' in line or '#' in line):
            cmd_line_idx = i
        elif line.strip() == command:
            cmd_line_idx = i

    if cmd_line_idx == -1:
        result_lines = lines[-20:]
    else:
        result_lines = []
        for i in range(cmd_line_idx + 1, len(lines)):
            line = lines[i]
            if re.match(r'^[\w\-_]+@[\w\-_]+.*\$\s*$', line.strip()):
                break
            if line.strip().endswith('$ ') and len(line.strip()) > 2:
                break
            result_lines.append(line)

    result = '\n'.join(result_lines).strip()
    while '\n\n\n' in result:
        result = result.replace('\n\n\n', '\n\n')
    return result if result else None


def take_screenshot(driver):
    if not driver:
        return None
    try:
        handles = driver.window_handles
        if handles:
            driver.switch_to.window(handles[-1])
        png = driver.get_screenshot_as_png()
        bio = io.BytesIO(png)
        bio.name = f'ss_{int(time.time())}_{random.randint(100, 999)}.png'
        return bio
    except Exception as e:
        log.debug(f"Screenshot failed: {e}")
        return None


# ══════════════════════════════════════════════════════════
#  Google Pages Handler
# ══════════════════════════════════════════════════════════

def handle_google_pages(driver, session):
    status = "مراقبة..."
    try:
        body = driver.find_element(By.TAG_NAME, "body").text[:5000]
    except Exception:
        return status

    body_lower = body.lower()

    # ── Authorize Cloud Shell popup ──
    if "authorize cloud shell" in body_lower:
        try:
            btns = driver.find_elements(By.XPATH,
                "//button[normalize-space(.)='Authorize']|"
                "//button[contains(.,'Authorize')]")
            for btn in btns:
                try:
                    btn_text = (btn.text or "").strip().lower()
                    if btn.is_displayed() and "authorize" in btn_text:
                        time.sleep(random.uniform(0.5, 1.0))
                        try:
                            btn.click()
                        except Exception:
                            driver.execute_script(
                                "arguments[0].click();", btn)
                        session['auth'] = True
                        time.sleep(2)
                        log.info("✅ Authorize Cloud Shell clicked")
                        return "✅ Authorize ✔️"
                except Exception:
                    continue
        except Exception:
            pass
        return "🔐 Authorize..."

    # ── Cloud Shell Continue popup ──
    if ("cloud shell" in body_lower
            and "continue" in body_lower
            and "free" in body_lower):
        try:
            btns = driver.find_elements(By.XPATH,
                "//a[contains(text(),'Continue')]|"
                "//button[contains(text(),'Continue')]|"
                "//button[.//span[contains(text(),'Continue')]]|"
                "//*[@role='button'][contains(.,'Continue')]")
            for btn in btns:
                try:
                    if btn.is_displayed() and btn.is_enabled():
                        time.sleep(random.uniform(0.5, 1.5))
                        try:
                            btn.click()
                        except Exception:
                            driver.execute_script(
                                "arguments[0].click();", btn)
                        time.sleep(3)
                        return "✅ Continue ✔️"
                except Exception:
                    continue
        except Exception:
            pass
        return "☁️ popup..."

    # ── Verify ──
    if "verify it" in body_lower:
        try:
            btns = driver.find_elements(By.XPATH,
                "//button[contains(.,'Continue')]|"
                "//input[@value='Continue']|"
                "//div[@role='button'][contains(.,'Continue')]")
            for btn in btns:
                try:
                    if btn.is_displayed():
                        time.sleep(0.5)
                        btn.click()
                        time.sleep(3)
                        return "✅ Verify ✔️"
                except Exception:
                    continue
        except Exception:
            pass
        return "🔐 Verify..."

    # ── I understand ──
    if "I understand" in body:
        try:
            btns = driver.find_elements(By.XPATH,
                "//*[contains(text(),'I understand')]")
            for btn in btns:
                try:
                    if btn.is_displayed():
                        btn.click()
                        time.sleep(2)
                        return "✅ I understand ✔️"
                except Exception:
                    continue
        except Exception:
            pass

    # ── Sign-in rejected ──
    if "couldn't sign you in" in body_lower:
        try:
            driver.delete_all_cookies()
            time.sleep(1)
            driver.get(session.get('url', 'about:blank'))
            time.sleep(5)
        except Exception:
            pass
        return "⚠️ رفض..."

    # ── Generic Authorize ──
    if ("authorize" in body_lower
            and ("cloud" in body_lower or "google" in body_lower)):
        try:
            btns = driver.find_elements(By.XPATH,
                "//button[normalize-space(.)='Authorize']|"
                "//button[contains(.,'AUTHORIZE')]")
            for btn in btns:
                try:
                    if btn.is_displayed():
                        btn.click()
                        session['auth'] = True
                        time.sleep(2)
                        return "✅ Authorize ✔️"
                except Exception:
                    continue
        except Exception:
            pass

    # ── Dismiss Gemini ──
    if "gemini" in body_lower and "dismiss" in body_lower:
        try:
            btns = driver.find_elements(By.XPATH,
                "//button[contains(.,'Dismiss')]|"
                "//a[contains(.,'Dismiss')]")
            for btn in btns:
                try:
                    if btn.is_displayed():
                        btn.click()
                        time.sleep(1)
                except Exception:
                    continue
        except Exception:
            pass

    # ── Trust project ──
    if "trust this project" in body_lower or "trust project" in body_lower:
        try:
            btns = driver.find_elements(By.XPATH,
                "//button[contains(.,'Trust')]|"
                "//button[contains(.,'Confirm')]")
            for btn in btns:
                try:
                    if btn.is_displayed():
                        btn.click()
                        time.sleep(2)
                        return "✅ Trust ✔️"
                except Exception:
                    continue
        except Exception:
            pass

    # ── Status by URL ──
    try:
        url = driver.current_url
    except Exception:
        return status

    if "shell.cloud.google.com" in url or "ide.cloud.google.com" in url:
        session['terminal_ready'] = True
        return "✅ Terminal ⌨️"
    elif "console.cloud.google.com" in url:
        return "📊 Console"
    elif "accounts.google.com" in url:
        return "🔐 تسجيل..."
    return status


# ══════════════════════════════════════════════════════════
#  Cloud Run Region Extraction
# ══════════════════════════════════════════════════════════

REGION_JS = """
var callback = arguments[arguments.length - 1];
setTimeout(function() {
    try {
        var regionClicked = false;
        var dropdowns = document.querySelectorAll(
            'mat-select, [role="combobox"]');
        for (var i = 0; i < dropdowns.length; i++) {
            var el = dropdowns[i];
            var aria = (el.getAttribute('aria-label') || '').toLowerCase();
            var id = (el.getAttribute('id') || '').toLowerCase();
            if (aria.indexOf('region') !== -1 ||
                id.indexOf('region') !== -1) {
                el.click();
                regionClicked = true;
                break;
            }
        }
        if (!regionClicked) {
            var labels = document.querySelectorAll(
                'label, .mat-form-field-label');
            for (var j = 0; j < labels.length; j++) {
                if (labels[j].innerText &&
                    labels[j].innerText.indexOf('Region') !== -1) {
                    labels[j].click();
                    regionClicked = true;
                    break;
                }
            }
        }
        if (!regionClicked) {
            callback('NO_DROPDOWN');
            return;
        }
        setTimeout(function() {
            var options = document.querySelectorAll(
                'mat-option, [role="option"]');
            var regions = [];
            for (var k = 0; k < options.length; k++) {
                var opt = options[k];
                var rect = opt.getBoundingClientRect();
                var style = window.getComputedStyle(opt);
                var isHidden = rect.width === 0 || rect.height === 0 ||
                    style.display === 'none' ||
                    style.visibility === 'hidden';
                var isDisabled =
                    opt.classList.contains('mat-option-disabled') ||
                    opt.getAttribute('aria-disabled') === 'true';
                if (!isHidden && !isDisabled) {
                    var txt = (opt.innerText || '').trim().split('\\n')[0];
                    if (txt && txt.indexOf('-') !== -1 &&
                        txt.toLowerCase().indexOf('learn') === -1) {
                        regions.push(txt);
                    }
                }
            }
            document.dispatchEvent(
                new KeyboardEvent('keydown', {'key': 'Escape'}));
            var backdrop = document.querySelector('.cdk-overlay-backdrop');
            if (backdrop) backdrop.click();
            callback(regions.length > 0
                     ? regions.join('\\n') : 'NO_REGIONS');
        }, 1500);
    } catch(e) {
        callback('ERROR:' + e.toString());
    }
}, 4000);
"""


def do_cloud_run_extraction(driver, chat_id, session):
    pid = session.get('project_id')
    if not pid:
        return True

    current_url = get_current_url_safe(driver)

    if "run/create" not in current_url:
        try:
            bot.send_message(chat_id,
                "⚙️ جاري فتح صفحة Cloud Run "
                "(مع تفعيل الـ API إن لزم الأمر)...")
            safe_navigate(driver,
                f"https://console.cloud.google.com/run/create"
                f"?enableapi=true&project={pid}")
        except Exception as e:
            log.warning(f"Cloud Run nav: {e}")
        return False

    try:
        bot.send_message(chat_id,
            "🔍 جاري قراءة السيرفرات المتوفرة والمسموحة...")

        driver.set_script_timeout(20)
        result = driver.execute_async_script(REGION_JS)

        if result is None:
            bot.send_message(chat_id,
                "⚠️ لم يتم الحصول على نتيجة من الصفحة.")
        elif result == "NO_DROPDOWN":
            bot.send_message(chat_id,
                "❌ لم أتمكن من إيجاد قائمة السيرفرات (Region).")
        elif result == "NO_REGIONS":
            bot.send_message(chat_id,
                "⚠️ فتحت القائمة لكن جميع السيرفرات مقيدة.")
        elif result.startswith("ERROR:"):
            bot.send_message(chat_id,
                f"⚠️ خطأ: {result[6:][:200]}")
        else:
            bot.send_message(chat_id,
                f"🌍 **السيرفرات المسموحة فقط للإنشاء هي:**\n"
                f"```text\n{result}\n```",
                parse_mode="Markdown")
    except Exception as e:
        bot.send_message(chat_id,
            f"⚠️ فشل استخراج السيرفرات:\n`{str(e)[:200]}`",
            parse_mode="Markdown")

    return True


# ══════════════════════════════════════════════════════════
#  Cloud Shell Navigation
#  ═══ Terminal فقط ═══
#  بدون walkthrough_id → لا tutorial
#  show=terminal → لا editor
# ══════════════════════════════════════════════════════════

def open_cloud_shell(driver, session, chat_id):
    """
    Open Cloud Shell with TERMINAL ONLY.
    
    URL format:
      https://shell.cloud.google.com/
        ?enableapi=true
        &project=PROJECT_ID
        &pli=1
        &show=terminal
    
    ❌ No walkthrough_id  → prevents Tutorial panel
    ❌ No show=ide        → prevents Editor panel
    ✅ show=terminal      → Terminal only
    ✅ enableapi=true     → enables Cloud Shell API
    """
    pid = session.get('project_id')
    if not pid:
        return False

    try:
        # ═══ بناء الرابط النظيف: Terminal فقط ═══
        shell_url = (
            f"https://shell.cloud.google.com/"
            f"?enableapi=true"
            f"&project={pid}"
            f"&pli=1"
            f"&show=terminal"
        )

        bot.send_message(chat_id,
            "🚀 جاري فتح Cloud Shell (Terminal فقط)...")

        log.info(f"🚀 Shell URL: {shell_url}")

        success = safe_navigate(driver, shell_url)

        if success:
            session['shell_opened'] = True
            session['shell_loading_until'] = time.time() + 60
            log.info("✅ Cloud Shell navigation started (terminal only)")
            return True
        else:
            log.error("❌ Cloud Shell navigation failed")
            return False

    except Exception as e:
        log.error(f"Shell Open Error: {e}")
        return False


# ══════════════════════════════════════════════════════════
#  Stream Update Helper
# ══════════════════════════════════════════════════════════

def update_stream_image(driver, chat_id, session, status, flash):
    flash = not flash
    icon = "🔴" if flash else "⭕"
    now = datetime.now().strftime("%H:%M:%S")
    proj = (f"📁 {session.get('project_id')}"
            if session.get('project_id') else "")
    t_st = " | ⌨️" if session.get('terminal_ready') else ""

    loading_until = session.get('shell_loading_until', 0)
    if time.time() < loading_until:
        remaining = int(loading_until - time.time())
        t_st += f" | ⏳{remaining}s"

    cap = f"{icon} بث 🕶️\n{proj}\n📌 {status}{t_st}\n⏱ {now}"

    png = driver.get_screenshot_as_png()
    bio = io.BytesIO(png)
    bio.name = f'l_{int(time.time())}_{random.randint(10, 99)}.png'

    bot.edit_message_media(
        media=InputMediaPhoto(bio, caption=cap),
        chat_id=chat_id,
        message_id=session['msg_id'],
        reply_markup=panel(session.get('cmd_mode', False))
    )
    return flash


# ══════════════════════════════════════════════════════════
#  Error Classification
# ══════════════════════════════════════════════════════════

TIMEOUT_KEYWORDS = (
    "urllib3", "requests", "readtimeout", "connection aborted",
    "timeout", "read timed out", "max retries", "connecttimeout"
)

DRIVER_ERROR_KEYWORDS = (
    'invalid session id', 'chrome not reachable',
    'disconnected:', 'crashed', 'no such session'
)


# ══════════════════════════════════════════════════════════
#  Stream Loop
# ══════════════════════════════════════════════════════════

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

        # Command mode: just monitor
        if session.get('cmd_mode'):
            time.sleep(3)
            try:
                if driver and is_on_shell_page(driver):
                    session['terminal_ready'] = True
            except Exception:
                pass
            continue

        time.sleep(random.uniform(4, 6))
        if not session['running'] or session.get('gen') != gen:
            break
        cycle += 1

        try:
            # ═══ Step 1: Switch to latest window ═══
            try:
                handles = driver.window_handles
                if handles:
                    driver.switch_to.window(handles[-1])
            except Exception:
                pass

            # ═══ Step 2: Handle popups ═══
            status = handle_google_pages(driver, session)

            # ═══ Step 3: Get current URL ═══
            current_url = get_current_url_safe(driver)

            # ═══ Step 4: UPDATE SCREENSHOT FIRST ═══
            try:
                flash = update_stream_image(
                    driver, chat_id, session, status, flash)
                err_count = 0
                drv_err = 0
            except Exception as e:
                em = str(e).lower()
                if "message is not modified" not in em:
                    raise

            # ═══ Step 5: Background tasks ═══

            on_console = ("console.cloud.google.com" in current_url
                          or "myaccount.google.com" in current_url)
            on_shell = is_on_shell_page(driver)

            # 5A: Cloud Run region extraction
            if (session.get('project_id')
                    and not session.get('run_api_checked')
                    and on_console):
                done = do_cloud_run_extraction(
                    driver, chat_id, session)
                if done:
                    session['run_api_checked'] = True

            # 5B: Open Cloud Shell (Terminal ONLY)
            elif (not session.get('shell_opened')
                  and session.get('run_api_checked')
                  and on_console):
                open_cloud_shell(driver, session, chat_id)

            # 5C: Terminal ready notification
            elif on_shell:
                if (session.get('terminal_ready')
                        and not session.get('terminal_notified')):
                    session['terminal_notified'] = True
                    try:
                        bot.send_message(chat_id,
                            "🖥️ **Terminal جاهز!**\n\n"
                            "اضغط **⌨️ وضع الأوامر**\n"
                            "أو `/cmd ls -la`",
                            parse_mode="Markdown")
                    except Exception:
                        pass

            # Memory cleanup
            if cycle % 15 == 0:
                gc.collect()

        except Exception as e:
            em = str(e).lower()

            if "message is not modified" in em:
                continue

            if any(k in em for k in TIMEOUT_KEYWORDS):
                time.sleep(2)
                continue

            # Grace period during Cloud Shell loading
            loading_until = session.get('shell_loading_until', 0)
            if time.time() < loading_until:
                log.info(f"⏳ Shell loading, ignoring: {str(e)[:80]}")
                time.sleep(3)
                continue

            err_count += 1
            log.warning(f"Stream err ({err_count}): {str(e)[:120]}")

            if "too many requests" in em or "retry after" in em:
                w = re.search(r'retry after (\d+)', em)
                time.sleep(int(w.group(1)) if w else 5)

            elif any(k in em for k in DRIVER_ERROR_KEYWORDS):
                drv_err += 1
                if drv_err >= 3:
                    try:
                        bot.send_message(chat_id,
                            "⚠️ إعادة تشغيل المتصفح...")
                    except Exception:
                        pass
                    try:
                        safe_quit(driver)
                        new_drv = get_driver()
                        session['driver'] = new_drv
                        driver = new_drv
                        driver.get(session.get('url', 'about:blank'))
                        session['shell_opened'] = False
                        session['auth'] = False
                        session['terminal_ready'] = False
                        session['terminal_notified'] = False
                        session['run_api_checked'] = False
                        session['shell_loading_until'] = 0
                        drv_err = 0
                        err_count = 0
                        time.sleep(5)
                    except Exception:
                        session['running'] = False
                        break

            elif err_count >= 5:
                try:
                    driver.refresh()
                    err_count = 0
                except Exception:
                    drv_err += 1

    log.info(f"🛑 Stream ended: {chat_id}")
    gc.collect()


# ══════════════════════════════════════════════════════════
#  Start Stream
# ══════════════════════════════════════════════════════════

def start_stream(chat_id, url):
    old_drv = None
    with sessions_lock:
        if chat_id in user_sessions:
            old = user_sessions[chat_id]
            old['running'] = False
            old['gen'] = old.get('gen', 0) + 1
            old_drv = old.get('driver')

    bot.send_message(chat_id, "⚡ جاري التجهيز...")
    if old_drv:
        safe_quit(old_drv)
        time.sleep(2)

    project_match = re.search(r'(qwiklabs-gcp-[\w-]+)', url)
    project_id = project_match.group(1) if project_match else None

    if not project_id:
        bot.send_message(chat_id,
            "⚠️ تحذير: لم أتمكن من استخراج Project ID، "
            "بعض الميزات قد لا تعمل.")

    try:
        driver = get_driver()
        bot.send_message(chat_id, "✅ المتصفح جاهز")
    except Exception as e:
        bot.send_message(chat_id,
            f"❌ فشل:\n`{str(e)[:300]}`", parse_mode="Markdown")
        return

    gen = int(time.time())
    with sessions_lock:
        user_sessions[chat_id] = {
            'driver': driver,
            'running': False,
            'msg_id': None,
            'url': url,
            'project_id': project_id,
            'shell_opened': False,
            'auth': False,
            'terminal_ready': False,
            'terminal_notified': False,
            'cmd_mode': False,
            'gen': gen,
            'run_api_checked': False,
            'shell_loading_until': 0
        }
        session = user_sessions[chat_id]

    bot.send_message(chat_id, "🌐 فتح الرابط...")

    try:
        driver.get(url)
    except Exception as e:
        if "timeout" not in str(e).lower():
            log.warning(f"URL load: {e}")
    time.sleep(5)

    try:
        handles = driver.window_handles
        if handles:
            driver.switch_to.window(handles[-1])
        png = driver.get_screenshot_as_png()
        bio = io.BytesIO(png)
        bio.name = f's_{int(time.time())}.png'
        msg = bot.send_photo(chat_id, bio,
            caption="🔴 بث 🕶️\n📌 بدء...", reply_markup=panel())

        with sessions_lock:
            session['msg_id'] = msg.message_id
            session['running'] = True

        t = threading.Thread(target=stream_loop,
                             args=(chat_id, gen), daemon=True)
        t.start()
        bot.send_message(chat_id, "✅ البث يعمل!")
    except Exception as e:
        bot.send_message(chat_id,
            f"❌ فشل:\n`{str(e)[:200]}`", parse_mode="Markdown")
        cleanup_session(chat_id)


# ══════════════════════════════════════════════════════════
#  Execute Command
# ══════════════════════════════════════════════════════════

SLOW_COMMANDS = ('install', 'apt', 'pip', 'gcloud', 'docker',
                 'kubectl', 'terraform', 'build', 'deploy')
FAST_COMMANDS = ('cat', 'echo', 'ls', 'pwd', 'whoami',
                 'date', 'hostname', 'uname', 'id', 'env')


def execute_command(chat_id, command):
    session = get_session(chat_id)
    if not session:
        bot.send_message(chat_id, "❌ لا توجد جلسة.")
        return

    driver = session.get('driver')
    if not driver:
        bot.send_message(chat_id, "❌ المتصفح غير متوفر.")
        return

    if not is_on_shell_page(driver):
        bot.send_message(chat_id, "⚠️ لست في Cloud Shell بعد.")
        return

    session['terminal_ready'] = True
    status_msg = bot.send_message(chat_id, f"⏳ `{command}`",
                                  parse_mode="Markdown")

    text_before = get_terminal_output(driver) or ""
    success = send_command_to_terminal(driver, command)

    if success:
        cmd_lower = command.lower()
        if any(k in cmd_lower for k in SLOW_COMMANDS):
            wait_time = 10
        elif any(k in cmd_lower for k in FAST_COMMANDS):
            wait_time = 2
        else:
            wait_time = 3
        time.sleep(wait_time)

        text_after = get_terminal_output(driver) or ""
        output_text = ""

        if text_after and text_after != text_before:
            if len(text_after) > len(text_before):
                new_part = text_after[len(text_before):].strip()
                output_text = (
                    new_part if new_part
                    else extract_command_result(text_after, command) or "")
            else:
                output_text = (
                    extract_command_result(text_after, command) or "")
        elif text_after:
            output_text = (
                extract_command_result(text_after, command) or "")

        if output_text:
            lines = output_text.split('\n')
            cleaned = []
            skipped = False
            for line in lines:
                if not skipped and command in line:
                    skipped = True
                    continue
                cleaned.append(line)
            output_text = '\n'.join(cleaned).strip()

        bio = take_screenshot(driver)

        if output_text:
            if len(output_text) > 3900:
                output_text = (
                    output_text[:3900] + "\n... (تم اقتطاع النص)")
            try:
                bot.send_message(chat_id,
                    f"✅ **الأمر:**\n`{command}`\n\n"
                    f"📋 **النتيجة:**\n```\n{output_text}\n```",
                    parse_mode="Markdown",
                    reply_markup=panel(cmd_mode=True))
            except Exception:
                try:
                    bot.send_message(chat_id,
                        f"✅ الأمر: {command}\n\n"
                        f"📋 النتيجة:\n{output_text}",
                        reply_markup=panel(cmd_mode=True))
                except Exception:
                    bot.send_message(chat_id, "✅ تم التنفيذ")
        else:
            bot.send_message(chat_id,
                f"✅ تم تنفيذ: `{command}`\n"
                f"📋 لم يتم التقاط النص (شاهد الصورة)",
                parse_mode="Markdown")

        if bio:
            try:
                bot.send_photo(chat_id, bio,
                    caption=f"📸 بعد: `{command}`",
                    parse_mode="Markdown",
                    reply_markup=panel(cmd_mode=True))
            except Exception:
                pass
    else:
        bot.send_message(chat_id,
            "⚠️ فشل الإرسال.\n🔄 تحديث ثم أعد")

    try:
        bot.delete_message(chat_id, status_msg.message_id)
    except Exception:
        pass


# ══════════════════════════════════════════════════════════
#  Bot Handlers
# ══════════════════════════════════════════════════════════

@bot.message_handler(commands=['start'])
def cmd_start(message):
    bot.reply_to(message,
        "🚀 مرحباً!\n\n"
        "أرسل رابط:\n`https://www.skills.google/google_sso`\n\n"
        "بعد Terminal:\n⌨️ وضع الأوامر أو `/cmd ls`\n📸 `/ss`",
        parse_mode="Markdown")


@bot.message_handler(commands=['cmd'])
def cmd_command(message):
    parts = message.text.split(maxsplit=1)
    if len(parts) < 2:
        bot.reply_to(message, "`/cmd الأمر`", parse_mode="Markdown")
        return
    threading.Thread(target=execute_command,
                     args=(message.chat.id, parts[1]),
                     daemon=True).start()


@bot.message_handler(commands=['screenshot', 'ss'])
def cmd_ss(message):
    cid = message.chat.id
    session = get_session(cid)
    if not session:
        bot.reply_to(message, "❌")
        return
    driver = session.get('driver')
    if not driver:
        bot.reply_to(message, "❌ المتصفح غير متوفر")
        return
    bio = take_screenshot(driver)
    if bio:
        bot.send_photo(cid, bio, caption="📸")
    else:
        bot.reply_to(message, "❌")


@bot.message_handler(func=lambda m: (
    m.text and
    m.text.startswith('https://www.skills.google/google_sso')))
def handle_url(message):
    threading.Thread(target=start_stream,
                     args=(message.chat.id, message.text.strip()),
                     daemon=True).start()


@bot.message_handler(func=lambda m: m.text and m.text.startswith('http'))
def handle_bad(message):
    bot.reply_to(message,
        "❌ يجب أن يبدأ بـ:\n`https://www.skills.google/google_sso`",
        parse_mode="Markdown")


@bot.message_handler(func=lambda m: (
    m.text and
    not m.text.startswith('/') and
    not m.text.startswith('http')))
def handle_text(message):
    cid = message.chat.id
    session = get_session(cid)
    if not session:
        return
    if session.get('cmd_mode'):
        threading.Thread(target=execute_command,
                         args=(cid, message.text),
                         daemon=True).start()
    elif is_on_shell_page(session.get('driver')):
        bot.reply_to(message,
            "💡 اضغط **⌨️ وضع الأوامر** أولاً\n"
            "أو `/cmd " + message.text + "`",
            parse_mode="Markdown")


# ══════════════════════════════════════════════════════════
#  Callback Handler
# ══════════════════════════════════════════════════════════

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
            bot.answer_callback_query(call.id, "إيقاف")
            try:
                bot.edit_message_caption("🛑",
                    chat_id=cid, message_id=s['msg_id'])
            except Exception:
                pass
            safe_quit(s.get('driver'))
            with sessions_lock:
                if cid in user_sessions:
                    del user_sessions[cid]

        elif call.data == "refresh":
            bot.answer_callback_query(call.id, "تحديث...")
            driver = s.get('driver')
            if driver:
                try:
                    driver.refresh()
                except Exception:
                    pass

        elif call.data == "screenshot":
            bot.answer_callback_query(call.id, "📸")
            driver = s.get('driver')
            if driver:
                bio = take_screenshot(driver)
                if bio:
                    bot.send_photo(cid, bio, caption="📸",
                        reply_markup=panel(s.get('cmd_mode', False)))

        elif call.data == "cmd_mode":
            s['cmd_mode'] = True
            driver = s.get('driver')
            if driver and is_on_shell_page(driver):
                s['terminal_ready'] = True
            bot.answer_callback_query(call.id, "⌨️")
            bot.send_message(cid,
                "⌨️ **وضع الأوامر!**\n\n"
                "اكتب أي أمر:\n`ls -la`\n`gcloud config list`\n\n"
                "🔙 للرجوع",
                parse_mode="Markdown")

        elif call.data == "watch_mode":
            s['cmd_mode'] = False
            bot.answer_callback_query(call.id, "🔙")
            bot.send_message(cid, "👁️ وضع البث")

    except Exception as e:
        log.debug(f"Callback error: {e}")


# ══════════════════════════════════════════════════════════
#  Main
# ══════════════════════════════════════════════════════════

if __name__ == '__main__':
    print("=" * 50)
    print("🚂 Terminal Control + Output Reading")
    print(f"🌐 Port: {os.environ.get('PORT', 8080)}")
    print("=" * 50)
    threading.Thread(target=start_health_server, daemon=True).start()
    while True:
        try:
            bot.polling(non_stop=True, timeout=60,
                        long_polling_timeout=60)
        except Exception as e:
            log.error(f"Polling error: {e}")
            time.sleep(5)
