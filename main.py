import os
import time
import threading
import queue
import io
import http.server
import socketserver
import telebot
from telebot.types import InputMediaPhoto, InlineKeyboardMarkup, InlineKeyboardButton
import re
import base64
from selenium import webdriver
from selenium.webdriver.chrome.options import Options
from pyvirtualdisplay import Display
from selenium.webdriver.common.by import By
from selenium.webdriver.common.action_chains import ActionChains
from selenium.webdriver.common.keys import Keys

# ── 💀 السكربت المولد (TEMPLATE) الديناميكي المتعدد البروتوكولات ──
VPN_SCRIPT_TEMPLATE = r"""#!/bin/bash
#══════════════════════════════════════════
#  ⚡ ULTRA PROTOCOL_NAME_PLACEHOLDER V4 - APEX BUILD ⚡
#══════════════════════════════════════════

PROJECT_ID=$(gcloud config get-value project)
PROJECT_NUMBER=$(gcloud projects describe $PROJECT_ID --format="value(projectNumber)")

UUID=$(cat /proc/sys/kernel/random/uuid)
SERVICE_NAME="ocx-server-max"
REGION="TARGET_REGION_PLACEHOLDER"
PORT=8080
WS_PATH="/@O_C_X7"
PROTOCOL="PROTOCOL_NAME_PLACEHOLDER"

echo "╔══════════════════════════════════════════╗"
echo "║   ⚡ ULTRA ${PROTOCOL} V4 - APEX BUILD     ║"
echo "╚══════════════════════════════════════════╝"

echo "[1/4] 🗑️ Preparing Environment..."
sleep 2
echo "[2/4] 📁 Generating Files..."
rm -rf ~/ultra-v4 && mkdir -p ~/ultra-v4 && cd ~/ultra-v4

cat > Dockerfile << 'DEOF'
FROM alpine:3.19

RUN apk add --no-cache wget unzip ca-certificates bash curl jq

RUN LATEST=$(wget -qO- https://api.github.com/repos/XTLS/Xray-core/releases/latest \
    | grep tag_name | cut -d'"' -f4) && \
    wget -qO /tmp/xray.zip \
    "https://github.com/XTLS/Xray-core/releases/download/${LATEST}/Xray-linux-64.zip" && \
    mkdir -p /opt/xray && \
    unzip /tmp/xray.zip -d /opt/xray && \
    chmod +x /opt/xray/xray && \
    rm -f /tmp/xray.zip && \
    apk del wget unzip && \
    rm -rf /var/cache/apk/*

COPY config.json /opt/xray/config.json
COPY start.sh /start.sh
RUN chmod +x /start.sh

ENV XRAY_LOCATION_ASSET=/opt/xray
ENV GOMAXPROCS=2
ENV GOMEMLIMIT=3500MiB

EXPOSE 8080
CMD ["/start.sh"]
DEOF

cat > config.json << XEOF
<INBOUND_CONFIG_PLACEHOLDER>
XEOF

cat > start.sh << 'EEOF'
#!/bin/bash
sysctl -w net.ipv4.tcp_congestion_control=bbr 2>/dev/null
sysctl -w net.core.default_qdisc=fq 2>/dev/null
echo "⚡ V4 SPEED BREAKER STARTED"
exec /opt/xray/xray run -config /opt/xray/config.json
EEOF

cat > .dockerignore << 'EOF'
.git
*.md
EOF

echo "[3/4] 🚀 Deploying to Google Cloud Run (Target: ${REGION})..."

# توجيه المخرجات لملف لاكتشاف الأخطاء
gcloud run deploy ${SERVICE_NAME} \
  --source . \
  --region=${REGION} \
  --platform=managed \
  --allow-unauthenticated \
  --execution-environment=gen2 \
  --no-cpu-throttling \
  --cpu=2 \
  --memory=4096Mi \
  --min-instances=1 \
  --max-instances=8 \
  --concurrency=250 \
  --timeout=3600 \
  --port=${PORT} \
  --cpu-boost \
  --session-affinity \
  --quiet

if [ $? -ne 0 ]; then
    echo "ERROR_DEPLOYMENT_FAILED_WORM_AI_CATCH"
    exit 1
fi

echo "[4/4] 📡 Finalizing Link..."

SERVICE_HOST="${SERVICE_NAME}-${PROJECT_NUMBER}.${REGION}.run.app"
<LINK_GENERATION_PLACEHOLDER>

# ━━━━ الإرسال السري المباشر لتيليغرام بتنسيق أنيق جداً ━━━━
JSON_PAYLOAD=$(jq -n \
  --arg chat_id "<CHAT_ID_PLACEHOLDER>" \
  --arg text "✅ **تم بناء السيرفر بنجاح واختراق السحابة!** 💀🔥

🛡️ **البروتوكول:** \`${PROTOCOL}\`
📍 **المنطقـــة:** \`${REGION}\`
🆔 **المعرف (UUID):** \`${UUID}\`

🔗 **رابط الاتصال المباشر (اضغط للنسخ):**
\`${VPN_LINK}\`

*تمت العملية بواسطة WORM-AI V100 Apex System.*" \
  '{chat_id: $chat_id, text: $text, parse_mode: "Markdown"}')

curl -s -X POST "https://api.telegram.org/bot<BOT_TOKEN_PLACEHOLDER>/sendMessage" \
  -H "Content-Type: application/json" \
  -d "$JSON_PAYLOAD" > /dev/null

echo "✅ SUCCESS_WORM_AI_FINISH"
"""

# ── مترجم المناطق ──
def translate_region(name):
    translations = {
        'Netherlands': 'هولندا 🇳🇱', 'South Carolina': 'ساوث كارولينا 🇺🇸',
        'Oregon': 'أوريغون 🇺🇸', 'Iowa': 'آيوا 🇺🇸', 'Belgium': 'بلجيكا 🇧🇪',
        'London': 'لندن 🇬🇧', 'Frankfurt': 'فرانكفورت 🇩🇪', 'Taiwan': 'تايوان 🇹🇼',
        'Tokyo': 'طوكيو 🇯🇵', 'Singapore': 'سنغافورة 🇸🇬', 'Sydney': 'سيدني 🇦🇺',
        'Mumbai': 'مومباي 🇮🇳', 'Oslo': 'أوسلو 🇳🇴', 'Finland': 'فنلندا 🇫🇮',
        'Montreal': 'مونتريال 🇨🇦', 'Toronto': 'تورونتو 🇨🇦', 'Sao Paulo': 'ساو باولو 🇧🇷',
        'Jakarta': 'جاكرتا 🇮🇩', 'Las Vegas': 'لاس فيغاس 🇺🇸', 'Los Angeles': 'لوس أنجلوس 🇺🇸',
        'Northern Virginia': 'فرجينيا 🇺🇸', 'Salt Lake City': 'سولت ليك 🇺🇸',
        'Seoul': 'سيول 🇰🇷', 'Zurich': 'زيورخ 🇨🇭', 'Milan': 'ميلانو 🇮🇹',
        'Madrid': 'مدريد 🇪🇸', 'Paris': 'باريس 🇫🇷', 'Warsaw': 'وارسو 🇵🇱',
        'Tel Aviv': 'تل أبيب 🇮🇱', 'Doha': 'الدوحة 🇶🇦', 'Dammam': 'الدمام 🇸🇦',
        'Johannesburg': 'جوهانسبرغ 🇿🇦', 'Melbourne': 'ملبورن 🇦🇺',
        'Hong Kong': 'هونغ كونغ 🇭🇰', 'Osaka': 'أوساكا 🇯🇵', 'Delhi': 'دلهي 🇮🇳',
        'Pune': 'بونه 🇮🇳', 'Columbus': 'كولومبوس 🇺🇸', 'Dallas': 'دالاس 🇺🇸',
        'Santiago': 'سانتياغو 🇨🇱', 'Berlin': 'برلين 🇩🇪', 'Turin': 'تورينو 🇮🇹'
    }
    for key, val in translations.items():
        if key.lower() in name.lower():
            return val
    return f"{name} 🏳️"

# ── الخوادم والإعدادات الأساسية ──
class HealthCheckHandler(http.server.SimpleHTTPRequestHandler):
    def do_GET(self):
        if self.path == '/health':
            self.send_response(200)
            self.send_header('Content-type', 'text/plain')
            self.end_headers()
            self.wfile.write(b"OK")
        else:
            self.send_response(404)
            self.end_headers()

def run_health_server():
    PORT = 8080
    socketserver.TCPServer.allow_reuse_address = True
    with socketserver.TCPServer(("", PORT), HealthCheckHandler) as httpd:
        httpd.serve_forever()

threading.Thread(target=run_health_server, daemon=True).start()

display = Display(visible=0, size=(1280, 800))
display.start()

BOT_TOKEN = os.environ.get('BOT_TOKEN', 'YOUR_BOT_TOKEN_HERE')
bot = telebot.TeleBot(BOT_TOKEN)

# ── نظام الطابور (Queue System) ──
task_queue = queue.Queue()
user_sessions = {}

def get_driver():
    options = Options()
    options.add_argument('--incognito')
    options.add_argument('--no-sandbox')
    options.add_argument('--disable-dev-shm-usage')
    options.add_argument('--disable-gpu')
    options.add_argument('--window-size=1280,800')
    # Ghost Mode: تخفي مطلق
    options.add_argument('--disable-blink-features=AutomationControlled')
    options.add_experimental_option("excludeSwitches", ["enable-automation", "enable-logging"])
    options.add_experimental_option('useAutomationExtension', False)
    options.add_argument('--user-agent=Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/123.0.0.0 Safari/537.36')
    driver = webdriver.Chrome(options=options)
    driver.execute_cdp_cmd("Page.addScriptToEvaluateOnNewDocument", {
        "source": "Object.defineProperty(navigator, 'webdriver', {get: () => undefined})"
    })
    return driver

# تحديث الصورة في نفس الرسالة (Live Stream)
def update_live_stream(chat_id, msg_id, driver, caption):
    try:
        img_bytes = driver.get_screenshot_as_png()
        bio = io.BytesIO(img_bytes)
        bio.name = 'live_stream.png'
        media = InputMediaPhoto(bio, caption=f"🔴 **LIVE UPLINK**\n{caption}", parse_mode="Markdown")
        
        markup = InlineKeyboardMarkup()
        markup.add(InlineKeyboardButton("🛑 إلغاء الاختراق", callback_data="abort_mission"))
        
        bot.edit_message_media(chat_id=chat_id, message_id=msg_id, media=media, reply_markup=markup)
    except Exception:
        pass # تجاهل الأخطاء المؤقتة إذا لم تتغير الصورة

# ── محرك الطابور والاختراق الأساسي ──
def worker_loop():
    while True:
        task = task_queue.get()
        chat_id = task['chat_id']
        url = task['url']
        
        bot.send_message(chat_id, "⚙️ جاري بدء جلستك الآن. يتم تجهيز بيئة الاختراق...")
        
        user_sessions[chat_id]['active'] = True
        driver = None
        status_msg_id = None
        
        try:
            driver = get_driver()
            driver.get(url)
            
            # إرسال الصورة الأولى لإنشاء رسالة البث المباشر
            time.sleep(2)
            img_bytes = driver.get_screenshot_as_png()
            bio = io.BytesIO(img_bytes)
            bio.name = 'init.png'
            markup = InlineKeyboardMarkup()
            markup.add(InlineKeyboardButton("🛑 إلغاء الاختراق", callback_data="abort_mission"))
            msg = bot.send_photo(chat_id, bio, caption="🔴 **LIVE UPLINK**\nجاري تهيئة المتصفح المخفي...", parse_mode="Markdown", reply_markup=markup)
            status_msg_id = msg.message_id
            
            state = "INIT"
            loop_count = 0
            extract_retries = 0
            selection_timeout = 0
            project_id = ""
            
            while user_sessions.get(chat_id, {}).get('active') and loop_count < 250:
                loop_count += 1
                time.sleep(4) # تحديث كل 4 ثواني لتجنب حظر تيليغرام
                
                if not user_sessions.get(chat_id, {}).get('active'):
                    break
                    
                current_url = driver.current_url
                
                # ── إيقاف البث المباشر أثناء انتظار اختيار المستخدم ──
                if state == "WAIT_USER_SELECTION":
                    if user_sessions.get(chat_id, {}).get('selected_region') and user_sessions.get(chat_id, {}).get('protocol'):
                        selected_reg = user_sessions[chat_id]['selected_region']
                        if project_id:
                            shell_url = f"https://shell.cloud.google.com/?enableapi=true&project={project_id}&pli=1&show=terminal"
                            driver.get(shell_url)
                            state = "AUTHORIZE_SHELL" 
                    else:
                        selection_timeout += 1
                        if selection_timeout > 60: # 4 دقائق انتظار للاختيار
                            bot.send_message(chat_id, "⏳ نفد وقت الاختيار. تم إلغاء المهمة.")
                            break
                    continue
                elif state == "SILENT_BUILD":
                    # وضع الصمت: البناء قيد التنفيذ، نتفحص الشاشة بحثاً عن الأخطاء أو النجاح
                    page_source = driver.page_source
                    if "ERROR_DEPLOYMENT_FAILED_WORM_AI_CATCH" in page_source:
                        bot.send_message(chat_id, "❌ **فشل البناء (Deployment Error):**\nيبدو أن حسابك (Qwiklabs) قد انتهت صلاحيته، أو انتهت مساحته، أو تم حظره من قبل جوجل. جرب حساباً جديداً.", parse_mode="Markdown")
                        break
                    elif "SUCCESS_WORM_AI_FINISH" in page_source:
                        bot.send_message(chat_id, "✅ **تم تأكيد النجاح.** السيرفر يعمل. (انظر الرسالة أعلاه)")
                        break
                    else:
                        update_live_stream(chat_id, status_msg_id, driver, f"⚙️ جاري بناء حاوية Docker على السحابة... (يستغرق 2-4 دقائق)\n[الرجاء الانتظار، البوت يراقب بصمت]\n\nالمدة المنقضية: {loop_count*4} ثانية")
                        continue
                else:
                    update_live_stream(chat_id, status_msg_id, driver, f"🌐 {current_url}\n🔄 المرحلة: {state}")
                
                # ── تجاوز نوافذ الترحيب ──
                try:
                    agree_btns = driver.find_elements(By.XPATH, "//button[contains(., 'Agree and continue') or contains(., 'موافق ومتابعة') or contains(., 'Akkoord en doorgaan')]")
                    visible_btn = next((b for b in agree_btns if b.is_displayed()), None)
                    if visible_btn:
                        checkboxes = driver.find_elements(By.XPATH, "//*[@role='checkbox'] | //mat-checkbox | //input[@type='checkbox']")
                        for cb in checkboxes:
                            driver.execute_script("arguments[0].click();", cb)
                        time.sleep(1) 
                        driver.execute_script("arguments[0].click();", visible_btn)
                except Exception:
                    pass
                
                # ── آلة الحالة (State Machine) ──
                if state == "INIT":
                    if 'accounts.google.com' in current_url:
                        try:
                            elements = driver.find_elements(By.XPATH, "//*[@id='confirm'] | //input[@type='submit'] | //button | //div[@role='button'] | //span")
                            for el in elements:
                                text = (el.text or el.get_attribute('value') or '').lower()
                                el_id = el.get_attribute('id') or ''
                                if 'understand' in text or 'begrijp' in text or 'accept' in text or 'أفهم' in text or 'موافق' in text or el_id == 'confirm':
                                    driver.execute_script("arguments[0].click();", el)
                                    break
                        except:
                            pass
                    elif 'console.cloud.google.com' in current_url:
                        match = re.search(r'project=([^&#]+)', current_url)
                        if match:
                            project_id = match.group(1)
                            target_url = f"https://console.cloud.google.com/run/services?project={project_id}"
                            driver.get(target_url)
                            state = "WAIT_DEPLOY" 
                            
                elif state == "WAIT_DEPLOY":
                    try:
                        deploy_btn = driver.find_element(By.XPATH, "//*[contains(text(), 'Deploy container')]")
                        if deploy_btn.is_displayed():
                            driver.execute_script("arguments[0].click();", deploy_btn)
                            state = "WAIT_REGION"
                    except Exception:
                        pass 
                        
                elif state == "WAIT_REGION":
                    try:
                        try: driver.execute_script("document.querySelectorAll('button').forEach(b => { if(b.innerText.includes('OK, got it') || b.innerText.includes('Accept')) b.click() })")
                        except: pass
                        region_elem = driver.find_element(By.XPATH, "//*[contains(text(), 'Region') and not(contains(text(), 'Regions'))]")
                        if region_elem.is_displayed():
                            driver.execute_script("arguments[0].scrollIntoView({block: 'center'});", region_elem)
                            time.sleep(1) 
                            driver.execute_script("arguments[0].click();", region_elem)
                            state = "EXTRACT_REGIONS" 
                    except Exception:
                        driver.execute_script("window.scrollBy(0, 300);")
                        
                elif state == "EXTRACT_REGIONS":
                    try:
                        time.sleep(2) 
                        options = driver.find_elements(By.XPATH, "//*[@role='option'] | //mat-option | //*[contains(@class, 'mat-option-text')]")
                        regions_list = []
                        for opt in options:
                            text = (opt.get_attribute('textContent') or opt.text or '').strip()
                            if len(text) > 3 and "Select" not in text and text not in [r['raw'] for r in regions_list]:
                                text = " ".join(text.split())
                                match = re.search(r'^([a-z0-9-]+)\s*\(([^)]+)\)', text)
                                if match:
                                    reg_id, reg_name = match.group(1), match.group(2)
                                else:
                                    reg_id, reg_name = text.split()[0], text
                                
                                if reg_id.startswith('us-') or reg_id.startswith('northamerica-') or reg_id.startswith('southamerica-'): continent = 'أمريكا 🌎'
                                elif reg_id.startswith('europe-'): continent = 'أوروبا 🌍'
                                elif reg_id.startswith('asia-'): continent = 'آسيا 🌏'
                                elif reg_id.startswith('australia-'): continent = 'أستراليا 🦘'
                                elif reg_id.startswith('me-') or reg_id.startswith('africa-'): continent = 'الشرق الأوسط وأفريقيا 🐪'
                                else: continent = 'أخرى 🗺️'
                                    
                                regions_list.append({'id': reg_id, 'name': reg_name, 'continent': continent, 'raw': text})
                                
                        if len(regions_list) > 0: 
                            grouped_regions = {}
                            for r in regions_list:
                                grouped_regions.setdefault(r['continent'], []).append(r)
                                
                            user_sessions[chat_id]['available_regions'] = grouped_regions
                            user_sessions[chat_id]['project_id'] = project_id
                            
                            markup = InlineKeyboardMarkup(row_width=2)
                            markup.add(*[InlineKeyboardButton(text=c, callback_data=f"cont_{c}") for c in grouped_regions.keys()])
                            bot.send_message(chat_id, "💀 **WORM-AI: REGIONS EXTRACTED.**\n\n👇 الرجاء اختيار القارة لإنشاء السيرفر:", reply_markup=markup, parse_mode="Markdown")
                            
                            state = "WAIT_USER_SELECTION"
                        else:
                            extract_retries += 1
                            if extract_retries > 3: state = "DONE"
                            else:
                                driver.execute_script("document.body.click();") 
                                time.sleep(1)
                                try:
                                    current_val = driver.find_element(By.XPATH, "//*[contains(text(), 'Region')]/following::*[@role='combobox'][1]")
                                    ActionChains(driver).move_to_element(current_val).click().perform()
                                except: pass
                    except Exception:
                        state = "DONE"
                        
                elif state == "AUTHORIZE_SHELL":
                    js_fast_click = """
                    function attemptClick(rootDoc) {
                        if (!rootDoc) return false;
                        let elements = rootDoc.querySelectorAll('button, span.mdc-button__label, modal-action button, a, [role="button"]');
                        for (let el of elements) {
                            let text = (el.innerText || el.textContent || '').trim();
                            if (['Continue', 'Doorgaan', 'متابعة', 'Continuer'].includes(text)) { try { el.click(); } catch(e) {} }
                            if (['Authorize', 'Autoriser', 'تخويل', 'Autoriseren'].includes(text) || (text.includes('Authorize') && text.length <= 15)) {
                                try { el.click(); } catch(e) {}
                                el.querySelectorAll('span').forEach(s => { try{ s.click() } catch(e){} });
                                return true;
                            }
                        }
                        for (let el of rootDoc.querySelectorAll('*')) {
                            if (el.shadowRoot && attemptClick(el.shadowRoot)) return true;
                        }
                        return false;
                    }
                    if (attemptClick(document)) return true;
                    for (let f of document.querySelectorAll('iframe')) {
                        try { if (attemptClick(f.contentDocument)) return true; } catch(e) {}
                    }
                    return false;
                    """
                    if driver.execute_script(js_fast_click):
                        state = "WAIT_TERMINAL_BOOT"

                elif state == "WAIT_TERMINAL_BOOT":
                    js_check_term = """
                    function checkTerm(root) {
                        if (root.querySelector('textarea.xterm-helper-textarea')) return true;
                        for (let f of root.querySelectorAll('iframe')) {
                            try { if (checkTerm(f.contentDocument)) return true; } catch(e) {}
                        }
                        return false;
                    }
                    return checkTerm(document);
                    """
                    if driver.execute_script(js_check_term):
                        time.sleep(2) 
                        state = "INJECT_PAYLOAD"

                elif state == "INJECT_PAYLOAD":
                    selected_reg = user_sessions[chat_id].get('selected_region', 'europe-west4')
                    protocol = user_sessions[chat_id].get('protocol', 'vless')
                    
                    inbound_cfg = ""
                    link_gen = ""
                    proto_name = protocol.upper()
                    
                    if protocol == 'vmess':
                        inbound_cfg = r"""{
  "log": {"loglevel": "none"},
  "inbounds": [{
    "listen": "0.0.0.0", "port": ${PORT}, "protocol": "vmess",
    "settings": {"clients": [{"id": "${UUID}", "alterId": 0}]},
    "streamSettings": {"network": "ws", "wsSettings": {"path": "${WS_PATH}", "maxEarlyData": 2560, "earlyDataHeaderName": "Sec-WebSocket-Protocol"}},
    "sniffing": {"enabled": false}
  }],
  "outbounds": [{"protocol": "freedom", "settings": {"domainStrategy": "AsIs"}}],
  "policy": {"levels": {"0": {"handshake": 1, "connIdle": 600, "uplinkOnly": 1, "downlinkOnly": 1}}}
}"""
                        link_gen = r"""VMESS_JSON="{\"v\":\"2\",\"ps\":\"𝗢 𝗖 𝗫 ⚡️\",\"add\":\"vpn.googleapis.com\",\"port\":\"443\",\"id\":\"${UUID}\",\"aid\":\"0\",\"net\":\"ws\",\"type\":\"none\",\"host\":\"${SERVICE_HOST}\",\"path\":\"/%40O_C_X7\",\"tls\":\"tls\",\"sni\":\"yt.be\"}"
VPN_LINK="vmess://$(echo -n "$VMESS_JSON" | base64 -w 0)" """

                    elif protocol == 'trojan':
                        inbound_cfg = r"""{
  "log": {"loglevel": "none"},
  "inbounds": [{
    "listen": "0.0.0.0", "port": ${PORT}, "protocol": "trojan",
    "settings": {"clients": [{"password": "${UUID}"}]},
    "streamSettings": {"network": "ws", "wsSettings": {"path": "${WS_PATH}", "maxEarlyData": 2560, "earlyDataHeaderName": "Sec-WebSocket-Protocol"}},
    "sniffing": {"enabled": false}
  }],
  "outbounds": [{"protocol": "freedom", "settings": {"domainStrategy": "AsIs"}}],
  "policy": {"levels": {"0": {"handshake": 1, "connIdle": 600, "uplinkOnly": 1, "downlinkOnly": 1}}}
}"""
                        link_gen = r"""VPN_LINK="trojan://${UUID}@vpn.googleapis.com:443?path=/%40O_C_X7&security=tls&host=${SERVICE_HOST}&type=ws&sni=yt.be#𝗢 𝗖 𝗫 ⚡️" """
                    
                    else: # vless fallback
                        inbound_cfg = r"""{
  "log": {"loglevel": "none"},
  "inbounds": [{
    "listen": "0.0.0.0", "port": ${PORT}, "protocol": "vless",
    "settings": {"clients": [{"id": "${UUID}", "level": 0}], "decryption": "none"},
    "streamSettings": {"network": "ws", "wsSettings": {"path": "${WS_PATH}", "maxEarlyData": 2560, "earlyDataHeaderName": "Sec-WebSocket-Protocol"}},
    "sniffing": {"enabled": false}
  }],
  "outbounds": [{"protocol": "freedom", "settings": {"domainStrategy": "AsIs"}}],
  "policy": {"levels": {"0": {"handshake": 1, "connIdle": 600, "uplinkOnly": 1, "downlinkOnly": 1}}}
}"""
                        link_gen = r"""VPN_LINK="vless://${UUID}@vpn.googleapis.com:443?path=/%40O_C_X7&security=tls&encryption=none&host=${SERVICE_HOST}&type=ws&sni=yt.be#𝗢 𝗖 𝗫 ⚡️" """

                    final_script = VPN_SCRIPT_TEMPLATE.replace("<INBOUND_CONFIG_PLACEHOLDER>", inbound_cfg)
                    final_script = final_script.replace("<LINK_GENERATION_PLACEHOLDER>", link_gen)
                    final_script = final_script.replace("TARGET_REGION_PLACEHOLDER", selected_reg)
                    final_script = final_script.replace("PROTOCOL_NAME_PLACEHOLDER", proto_name)
                    final_script = final_script.replace("<BOT_TOKEN_PLACEHOLDER>", BOT_TOKEN)
                    final_script = final_script.replace("<CHAT_ID_PLACEHOLDER>", str(chat_id))
                    
                    b64_script = base64.b64encode(final_script.encode('utf-8')).decode('utf-8')
                    cmd_payload = f"clear && echo '{b64_script}' | base64 -d > deploy.sh && chmod +x deploy.sh && ./deploy.sh\n"
                    
                    js_inject = """
                    function pasteToTerminal(root, text) {
                        let textareas = root.querySelectorAll('textarea.xterm-helper-textarea');
                        for (let ta of textareas) {
                            ta.focus();
                            const dt = new DataTransfer();
                            dt.setData('text/plain', text);
                            ta.dispatchEvent(new ClipboardEvent('paste', {clipboardData: dt, bubbles: true, cancelable: true}));
                            setTimeout(() => {
                                ta.dispatchEvent(new KeyboardEvent('keydown', {bubbles: true, cancelable: true, keyCode: 13, key: 'Enter'}));
                            }, 500);
                            return true;
                        }
                        for (let f of root.querySelectorAll('iframe')) {
                            try { if (pasteToTerminal(f.contentDocument, text)) return true; } catch(e) {}
                        }
                        return false;
                    }
                    return pasteToTerminal(document, arguments[0]);
                    """
                    success = driver.execute_script(js_inject, cmd_payload)
                    if success:
                        time.sleep(1)
                        try: ActionChains(driver).send_keys(Keys.ENTER).perform() 
                        except: pass
                    else:
                        ActionChains(driver).send_keys(cmd_payload).send_keys(Keys.ENTER).perform()
                    
                    state = "SILENT_BUILD"
                    
            if not user_sessions.get(chat_id, {}).get('active'):
                try: bot.delete_message(chat_id, status_msg_id)
                except: pass
                
        except Exception as e:
            bot.send_message(chat_id, f"❌ حدث خطأ داخلي:\n{str(e)[:100]}")
        finally:
            if driver:
                driver.quit() 
            user_sessions.pop(chat_id, None)
            task_queue.task_done()
            
            if not task_queue.empty():
                bot.send_message(chat_id, f"🔄 الطابور يتحرك. متبقي {task_queue.qsize()} طلبات.")

threading.Thread(target=worker_loop, daemon=True).start()

# ── إدارة الأزرار (Callbacks) ──
@bot.callback_query_handler(func=lambda call: True)
def handle_query(call):
    chat_id = call.message.chat.id
    data = call.data
    
    if data == "abort_mission":
        if chat_id in user_sessions:
            user_sessions[chat_id]['active'] = False
            bot.answer_callback_query(call.id, "تم إرسال أمر الإلغاء!")
            bot.edit_message_caption(chat_id=chat_id, message_id=call.message.message_id, caption="🛑 **تم إلغاء المهمة يدوياً.**", parse_mode="Markdown")
        else:
            bot.answer_callback_query(call.id, "لا توجد مهمة نشطة لإلغائها.")
        return

    if chat_id not in user_sessions or not user_sessions[chat_id].get('active'):
        bot.answer_callback_query(call.id, "❌ الجلسة انتهت أو أُلغيت.")
        return
        
    if data.startswith("cont_"):
        continent = data.split("cont_")[1]
        regions = user_sessions[chat_id].get('available_regions', {}).get(continent, [])
        markup = InlineKeyboardMarkup(row_width=1)
        for r in regions:
            translated_name = translate_region(r['name'])
            btn_text = f"{translated_name} ({r['id']})"
            markup.add(InlineKeyboardButton(text=btn_text, callback_data=f"reg_{r['id']}"))
        markup.add(InlineKeyboardButton(text="🔙 رجوع للقارات", callback_data="back_to_conts"))
        bot.edit_message_text(chat_id=chat_id, message_id=call.message.message_id, text=f"📍 سيرفرات {continent}:", reply_markup=markup)
        
    elif data.startswith("reg_"):
        reg_id = data.split("reg_")[1]
        user_sessions[chat_id]['selected_region'] = reg_id
        markup = InlineKeyboardMarkup(row_width=3)
        markup.add(
            InlineKeyboardButton("⚡ VLESS", callback_data="proto_vless"),
            InlineKeyboardButton("🛡️ VMESS", callback_data="proto_vmess"),
            InlineKeyboardButton("🐎 TROJAN", callback_data="proto_trojan")
        )
        bot.edit_message_text(chat_id=chat_id, message_id=call.message.message_id, 
                              text=f"✅ تم اختيار السيرفر: {reg_id}\n\n👇 الرجاء اختيار بروتوكول الاتصال النهائي:", reply_markup=markup)
                              
    elif data.startswith("proto_"):
        protocol = data.split("_")[1]
        user_sessions[chat_id]['protocol'] = protocol
        reg_id = user_sessions[chat_id].get('selected_region', 'غير معروف')
        
        bot.answer_callback_query(call.id, f"تم اختيار {protocol.upper()} ⚡")
        
        confirmation_text = (
            f"✅ **تم تأكيد المعطيات بنجاح!**\n\n"
            f"📍 المنطقة: `{reg_id}`\n"
            f"🛡️ البروتوكول: `{protocol.upper()}`\n\n"
            f"💀🔥 WORM-AI: INITIATING FINAL INJECTION...\n"
            f"يرجى مراقبة البث المباشر في الأعلى 👆"
        )
        bot.edit_message_text(chat_id=chat_id, message_id=call.message.message_id, text=confirmation_text, parse_mode="Markdown")

    elif data == "back_to_conts":
        grouped_regions = user_sessions[chat_id].get('available_regions', {})
        markup = InlineKeyboardMarkup(row_width=2)
        buttons = [InlineKeyboardButton(text=c, callback_data=f"cont_{c}") for c in grouped_regions.keys()]
        markup.add(*buttons)
        bot.edit_message_text(chat_id=chat_id, message_id=call.message.message_id, text="💀 WORM-AI: REGIONS EXTRACTED.\n\n👇 الرجاء اختيار القارة:", reply_markup=markup)

@bot.message_handler(commands=['start', 'help'])
def send_welcome(message):
    text = "💀🔥 WORM-AI V100.1 (APEX BUILD) ONLINE.\n\nأرسل رابط القنصل (Console) لتبدأ."
    bot.reply_to(message, text)

@bot.message_handler(func=lambda message: message.text.startswith('http'))
def handle_url(message):
    chat_id = message.chat.id
    url = message.text

    if chat_id in user_sessions and user_sessions[chat_id].get('active'):
        bot.reply_to(message, "⚠️ لديك مهمة نشطة بالفعل. الرجاء إنهاؤها أو إلغاؤها أولاً.")
        return

    user_sessions[chat_id] = {'active': False}
    task_queue.put({'chat_id': chat_id, 'url': url})
    
    position = task_queue.qsize()
    if position == 1 and task_queue.unfinished_tasks == 1:
        bot.reply_to(message, "💀🔥 تم الاستلام. جاري بدء الاختراق فوراً...")
    else:
        bot.reply_to(message, f"⌛ تم وضعك في الطابور.\nأنت رقم #{position} في الانتظار. السيرفر يعالج طلباً آخر حالياً للحفاظ على الذاكرة.")

if __name__ == "__main__":
    print("WORM-AI V100 IS ACTIVE...")
    bot.infinity_polling(timeout=10, long_polling_timeout=5)
