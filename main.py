import os
import time
import threading
import queue
import io
import http.server
import socketserver
import telebot
from telebot.types import InputMediaPhoto, InlineKeyboardMarkup, InlineKeyboardButton, ReplyKeyboardMarkup, KeyboardButton
import re
import base64
import pymongo
from selenium import webdriver
from selenium.webdriver.chrome.options import Options
from pyvirtualdisplay import Display
from selenium.webdriver.common.by import By
from selenium.webdriver.common.action_chains import ActionChains
from selenium.webdriver.common.keys import Keys

# ==========================================
# 💀 إعدادات النظام الأساسية
# ==========================================
BOT_TOKEN = os.environ.get('BOT_TOKEN', '')
ADMIN_ID = os.environ.get('ADMIN_ID', '')
MONGO_URI = os.environ.get('MONGO_URI', '')

bot = telebot.TeleBot(BOT_TOKEN)

# ==========================================
# 💾 إعدادات قاعدة البيانات MongoDB
# ==========================================
if MONGO_URI:
    try:
        mongo_client = pymongo.MongoClient(MONGO_URI, serverSelectionTimeoutMS=5000)
        mongo_client.server_info() 
        db = mongo_client['ocx_server_db']
        users_col = db['users']
        vips_col = db['vips']
        servers_col = db['servers']
        
        users_col.update_many({}, {"$set": {"active": False, "status": "idle"}})
        USE_MONGO = True
        print("✅ [DB] MongoDB Connected Successfully.")
    except Exception as e:
        users_col, servers_col, ram_vips = {}, {}, set()
        USE_MONGO = False
        print(f"⚠️ [DB] MongoDB Connection Failed: {e}. Using RAM mode.")
else:
    users_col, servers_col, ram_vips = {}, {}, set()
    USE_MONGO = False
    print("⚠️ [DB] No MONGO_URI provided. Using RAM mode.")

task_queue = queue.Queue()

if ADMIN_ID and not USE_MONGO:
    ram_vips.add(str(ADMIN_ID))

# ==========================================
# 🧹 محرك تنظيف الكوكيز (كل 12 ساعة)
# ==========================================
def cookie_cleanup_worker():
    """يقوم بحذف جميع الكوكيز المحفوظة كل 12 ساعة لتخفيف الضغط على قاعدة البيانات"""
    cleanup_interval = 12 * 60 * 60 # 12 ساعة بالثواني
    while True:
        time.sleep(cleanup_interval)
        try:
            if USE_MONGO:
                result = servers_col.update_many({}, {"$set": {"cookies": []}})
                print(f"🧹 [System Cleanup] Cleared cookies from {result.modified_count} servers in MongoDB (12h cycle).")
            else:
                for url in servers_col:
                    servers_col[url]['cookies'] = []
                print("🧹 [System Cleanup] Cleared cookies from RAM (12h cycle).")
        except Exception as e:
            print(f"❌ [Cleanup Error] {e}")

threading.Thread(target=cookie_cleanup_worker, daemon=True).start()

# ==========================================
# 🛡️ نظام الحماية (VIP System)
# ==========================================
def is_vip(user_id):
    str_id = str(user_id)
    if str_id == str(ADMIN_ID): return True
    if USE_MONGO: return vips_col.find_one({"user_id": str_id}) is not None
    else: return str_id in ram_vips

def add_vip_user(user_id):
    str_id = str(user_id)
    if USE_MONGO: vips_col.update_one({"user_id": str_id}, {"$set": {"user_id": str_id}}, upsert=True)
    else: ram_vips.add(str_id)

def remove_vip_user(user_id):
    str_id = str(user_id)
    if USE_MONGO: vips_col.delete_one({"user_id": str_id})
    else: ram_vips.discard(str_id)

def get_all_vips():
    if USE_MONGO: return [doc['user_id'] for doc in vips_col.find()]
    else: return list(ram_vips)

def send_unauthorized_msg(chat_id):
    try:
        m = bot.send_message(chat_id, "...", reply_markup=telebot.types.ReplyKeyboardRemove())
        bot.delete_message(chat_id, m.message_id)
    except: pass
    markup = InlineKeyboardMarkup().add(InlineKeyboardButton("📞 التواصل لشراء البوت", url="https://t.me/aynX1"))
    msg = bot.send_message(chat_id, "⛔️ **عذراً، أنت غير مشترك في هذا البوت.**\n\nللاشتراك والحصول على الصلاحيات، يرجى التواصل مع الإدارة.", reply_markup=markup, parse_mode="Markdown")
    
    update_session(chat_id, {'unauth_msg_id': msg.message_id})

# ==========================================
# ⚙️ إدارة الجلسات
# ==========================================
def get_session(chat_id):
    if USE_MONGO:
        res = users_col.find_one({"chat_id": str(chat_id)})
        return res if res else {}
    return users_col.get(str(chat_id), {})

def update_session(chat_id, data):
    if USE_MONGO:
        users_col.update_one({"chat_id": str(chat_id)}, {"$set": data}, upsert=True)
    else:
        if str(chat_id) not in users_col: users_col[str(chat_id)] = {"chat_id": str(chat_id)}
        users_col[str(chat_id)].update(data)

def clear_session(chat_id):
    update_session(chat_id, {
        "active": False, "status": "idle", "selected_region": None, 
        "protocol": None, "target_url": None, "available_regions": {}, "replace_mode": False,
        "ui_msg_id": None, "email": None, "password": None, "interaction_time": 0
    })

def get_server_by_url(url):
    if USE_MONGO: return servers_col.find_one({"url": url})
    return servers_col.get(url)

def save_successful_server(chat_id, url, server_name, region, protocol, project_id, cookies=None):
    data = {
        "chat_id": str(chat_id), "url": url, "server_name": server_name,
        "region": region, "protocol": protocol, "project_id": project_id,
        "cookies": cookies or [], "timestamp": time.time()
    }
    if USE_MONGO: servers_col.update_one({"url": url}, {"$set": data}, upsert=True)
    else: servers_col[url] = data

def update_server_cookies(url, cookies):
    if USE_MONGO: servers_col.update_one({"url": url}, {"$set": {"cookies": cookies}}, upsert=True)
    else:
        if url not in servers_col: servers_col[url] = {}
        servers_col[url]['cookies'] = cookies

# ==========================================
# 💀 السكربت المولد (BASH PAYLOAD - محسن ومحمي ومطور للألعاب)
# ==========================================
VPN_SCRIPT_TEMPLATE = r"""#!/bin/bash
PROJECT_ID=$(gcloud config get-value project)
PROJECT_NUMBER=$(gcloud projects describe $PROJECT_ID --format="value(projectNumber)")

# تفعيل الـ APIs المطلوبة لتفادي أخطاء الحسابات الجديدة
gcloud services enable run.googleapis.com cloudbuild.googleapis.com artifactregistry.googleapis.com --quiet 2>/dev/null || true

UUID=$(cat /proc/sys/kernel/random/uuid)
if [ "REPLACE_MODE_PLACEHOLDER" == "True" ]; then
    SERVICE_NAME="OLD_SERVER_NAME_PLACEHOLDER"
else
    SERVICE_NAME="ocx-server-max"
fi

REGION="TARGET_REGION_PLACEHOLDER"
PORT=8080
WS_PATH="/@O_C_X7"
PROTOCOL="PROTOCOL_NAME_PLACEHOLDER"

rm -rf ~/ultra-v4 && mkdir -p ~/ultra-v4 && cd ~/ultra-v4

cat > Dockerfile << 'DEOF'
FROM alpine:3.19
RUN apk add --no-cache wget unzip ca-certificates bash curl jq
RUN wget -qO /tmp/xray.zip "https://github.com/XTLS/Xray-core/releases/download/v1.8.7/Xray-linux-64.zip" && \
    mkdir -p /opt/xray && unzip /tmp/xray.zip -d /opt/xray && chmod +x /opt/xray/xray && \
    rm -f /tmp/xray.zip && apk del wget unzip && rm -rf /var/cache/apk/*
COPY config.json /opt/xray/config.json
COPY start.sh /start.sh
RUN chmod +x /start.sh
ENV XRAY_LOCATION_ASSET=/opt/xray
ENV GOMAXPROCS=2
ENV GOMEMLIMIT=1500MiB
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
exec /opt/xray/xray run -config /opt/xray/config.json
EEOF

cat > .dockerignore << 'EOF'
.git
*.md
EOF

# إعدادات الأداء القصوى للألعاب والتصفح (VLESS Optimized)
gcloud run deploy ${SERVICE_NAME} \
  --source . \
  --region=${REGION} \
  --platform=managed \
  --allow-unauthenticated \
  --cpu=2 \
  --memory=2048Mi \
  --min-instances=1 \
  --max-instances=8 \
  --concurrency=80 \
  --no-cpu-throttling \
  --session-affinity \
  --timeout=3600 \
  --port=${PORT} \
  --quiet

if [ $? -ne 0 ]; then
    # إرسال رسالة توضيحية للمستخدم في حال رفض Google للعملية بتنسيق نظيف
    ERROR_PAYLOAD=$(jq -n \
      --arg chat_id "<CHAT_ID_PLACEHOLDER>" \
      --arg text "❌ **فشل البناء (Deployment Failed):**

نظام حماية Google قام برفض العملية. الأسباب المحتملة:
1️⃣ الحساب مقيد، محظور، أو لا يملك صلاحيات لتفعيل ميزة (No CPU Throttling).
2️⃣ لا توجد موارد (سيرفرات) كافية في منطقة \`${REGION}\`.

💡 **الحل:** استخدم أمر /cancel ، وجرب منطقة (Region) مختلفة، أو استخدم حساب Qwiklabs جديد." \
      '{chat_id: $chat_id, text: $text, parse_mode: "Markdown"}')
      
    curl -s -X POST "https://api.telegram.org/bot<BOT_TOKEN_PLACEHOLDER>/sendMessage" \
      -H "Content-Type: application/json" \
      -d "$ERROR_PAYLOAD" > /dev/null
      
    echo "ERROR_DEPLOYMENT_FAILED_OCX_CATCH"
    exit 1
fi

SERVICE_URL=$(gcloud run services describe ${SERVICE_NAME} --region=${REGION} --format='value(status.url)' 2>/dev/null)
SERVICE_HOST="${SERVICE_NAME}-${PROJECT_NUMBER}.${REGION}.run.app"

<LINK_GENERATION_PLACEHOLDER>

echo "OCX_DATA_SYNC: ${SERVICE_NAME}|${REGION}|${PROTOCOL}|${UUID}"
sleep 2

# حل مشكلة تشوه الرسالة وظهور الأسطر بطريقة خاطئة: الاعتماد على أسطر حقيقية (Multiline)
JSON_PAYLOAD=$(jq -n \
  --arg chat_id "<CHAT_ID_PLACEHOLDER>" \
  --arg text "✅ **تم بناء السيرفر بنجاح واحترافية!** 🚀🔥

🛡️ **البروتوكول:** \`${PROTOCOL}\`
📍 **المنطقـــة:** \`${REGION}\`
🆔 **المعرف (UUID):** \`${UUID}\`

🔗 **رابط الاتصال المباشر (اضغط للنسخ):**
\`\`\`
${VPN_LINK}
\`\`\`

*تمت العملية بواسطة 💎 OCX PRO System.*" \
  '{chat_id: $chat_id, text: $text, parse_mode: "Markdown"}')

curl -s -X POST "https://api.telegram.org/bot<BOT_TOKEN_PLACEHOLDER>/sendMessage" \
  -H "Content-Type: application/json" \
  -d "$JSON_PAYLOAD" > /dev/null

echo "SUCCESS_OCX_FINISH"
"""

def translate_region(name):
    translations = {'Netherlands': 'هولندا 🇳🇱', 'South Carolina': 'ساوث كارولينا 🇺🇸', 'Oregon': 'أوريغون 🇺🇸', 'Iowa': 'آيوا 🇺🇸', 'Belgium': 'بلجيكا 🇧🇪', 'London': 'لندن 🇬🇧', 'Frankfurt': 'فرانكفورت 🇩🇪', 'Taiwan': 'تايوان 🇹🇼', 'Tokyo': 'طوكيو 🇯🇵', 'Singapore': 'سنغافورة 🇸🇬', 'Sydney': 'سيدني 🇦🇺', 'Mumbai': 'مومباي 🇮🇳', 'Oslo': 'أوسلو 🇳🇴', 'Finland': 'فنلندا 🇫🇮', 'Montreal': 'مونتريال 🇨🇦', 'Toronto': 'تورونتو 🇨🇦', 'Sao Paulo': 'ساو باولو 🇧🇷', 'Jakarta': 'جاكرتا 🇮🇩', 'Las Vegas': 'لاس فيغاس 🇺🇸', 'لوس أنجلوس': 'لوس أنجلوس 🇺🇸', 'Los Angeles': 'لوس أنجلوس 🇺🇸', 'Northern Virginia': 'فرجينيا 🇺🇸', 'Salt Lake City': 'سولت ليك 🇺🇸', 'Seoul': 'سيول 🇰🇷', 'Zurich': 'زيورخ 🇨🇭', 'Milan': 'ميلانو 🇮🇹', 'Madrid': 'مدريد 🇪🇸', 'Paris': 'باريس 🇫🇷', 'Warsaw': 'وارسو 🇵🇱'}
    for key, val in translations.items():
        if key.lower() in name.lower(): return val
    return f"{name} 🏳️"

class HealthCheckHandler(http.server.SimpleHTTPRequestHandler):
    def do_GET(self):
        if self.path == '/health':
            self.send_response(200)
            self.send_header('Content-type', 'text/plain')
            self.end_headers()
            self.wfile.write(b"OK")
        else: self.send_response(404); self.end_headers()

def run_health_server():
    socketserver.TCPServer.allow_reuse_address = True
    port = int(os.environ.get("PORT", 8080))
    with socketserver.TCPServer(("", port), HealthCheckHandler) as httpd:
        httpd.serve_forever()

threading.Thread(target=run_health_server, daemon=True).start()

# ==========================================
# 🚀 محرك المتصفح السريع (Optimized)
# ==========================================
display = Display(visible=0, size=(1280, 800))
display.start()

def get_driver():
    options = Options()
    options.add_argument('--headless=new')
    options.add_argument('--incognito')
    options.add_argument('--no-sandbox')
    options.add_argument('--disable-dev-shm-usage')
    options.add_argument('--disable-gpu')
    options.add_argument('--window-size=1280,800')
    options.add_argument('--disable-blink-features=AutomationControlled')
    options.page_load_strategy = 'eager'
    options.add_experimental_option("excludeSwitches", ["enable-automation", "enable-logging"])
    options.add_experimental_option('useAutomationExtension', False)
    options.add_argument('--user-agent=Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36')
    driver = webdriver.Chrome(options=options)
    driver.execute_cdp_cmd("Page.addScriptToEvaluateOnNewDocument", {"source": "Object.defineProperty(navigator, 'webdriver', {get: () => undefined})"})
    driver.set_page_load_timeout(30)
    return driver

def inject_cookies_safely(driver, cookies):
    if not cookies: return
    try:
        driver.get("https://google.com/robots.txt")
        for c in cookies:
            if 'google.com' in c.get('domain', ''):
                try: driver.add_cookie(c)
                except: pass
        driver.get("https://console.cloud.google.com/robots.txt")
        for c in cookies:
            if 'cloud.google.com' in c.get('domain', ''):
                try: driver.add_cookie(c)
                except: pass
    except: pass

def update_live_stream(chat_id, msg_id, status_text, logs=None, driver=None, is_photo=False):
    if not msg_id: return
    
    if logs is not None:
        final_text = (
            f"🟢 *نظام OCX | التتبع المباشر*\n"
            f"━━━━━━━━━━━━━━━━━\n"
            f"**العملية:** {status_text}\n"
            f"```bash\n> {logs}\n```\n"
            f"━━━━━━━━━━━━━━━━━"
        )
    else:
        final_text = status_text
        
    try:
        markup = InlineKeyboardMarkup().add(InlineKeyboardButton("🛑 إلغاء فوري", callback_data="abort_mission"))
        if is_photo:
            if driver:
                try:
                    image_data = driver.get_screenshot_as_png()
                    media = InputMediaPhoto(image_data, caption=final_text, parse_mode="Markdown")
                    bot.edit_message_media(chat_id=chat_id, message_id=msg_id, media=media, reply_markup=markup)
                    return
                except Exception as e:
                    pass # تجاهل الأخطاء المؤقتة للصور
            # في حال فشل التقاط الصورة، يتم تحديث النص (الكابشن) فقط
            bot.edit_message_caption(chat_id=chat_id, message_id=msg_id, caption=final_text, parse_mode="Markdown", reply_markup=markup)
        else:
            bot.edit_message_text(chat_id=chat_id, message_id=msg_id, text=final_text, parse_mode="Markdown", reply_markup=markup)
    except Exception as e:
        pass 

# ==========================================
# ⚙️ محرك الطابور الأساسي (الذكي والسريع)
# ==========================================
def worker_loop():
    while True:
        task = task_queue.get()
        chat_id, url = task['chat_id'], task['url']
        
        session = get_session(chat_id)
        if not session.get('active') or session.get('status') != 'queued':
            task_queue.task_done()
            continue
            
        update_session(chat_id, {'status': 'processing'})
        current_session = get_session(chat_id)
        
        ui_msg_id = current_session.get('ui_msg_id')
        if ui_msg_id:
            try: bot.delete_message(chat_id, ui_msg_id)
            except: pass
        
        driver = None
        status_msg_id = None
        
        try:
            driver = get_driver()
            existing_server = get_server_by_url(url)
            saved_project_id = existing_server.get('project_id', '') if existing_server else ''
            saved_cookies = existing_server.get('cookies', []) if existing_server else []

            target_url_to_load = url
            initial_state = "INIT"
            sso_tried = True 

            if saved_project_id and (current_session.get('replace_mode') or current_session.get('add_new_mode')):
                if current_session.get('replace_mode'):
                    target_url_to_load = f"https://shell.cloud.google.com/?enableapi=true&project={saved_project_id}&pli=1&show=terminal"
                    initial_state = "AUTHORIZE_SHELL"
                else:
                    target_url_to_load = f"https://console.cloud.google.com/run/services?project={saved_project_id}"
                    initial_state = "WAIT_DEPLOY"
                sso_tried = False 

            driver.get(target_url_to_load)
            state = initial_state
            cookies_tried = False
            
            time.sleep(2) 
            markup = InlineKeyboardMarkup().add(InlineKeyboardButton("🛑 إلغاء فوري", callback_data="abort_mission"))
            is_status_photo = False
            try:
                ss = driver.get_screenshot_as_png()
                msg = bot.send_photo(chat_id, photo=ss, caption="🟢 **سجل العمليات المباشر:**\n⚡ جاري تهيئة الاتصال المشفر...", parse_mode="Markdown", reply_markup=markup)
                is_status_photo = True
            except:
                msg = bot.send_message(chat_id, "🟢 **سجل العمليات المباشر:**\n⚡ جاري تهيئة الاتصال المشفر...", parse_mode="Markdown", reply_markup=markup)
            status_msg_id = msg.message_id
                
            loop_count = 0
            selection_timeout = 0
            project_id = saved_project_id if saved_project_id else ""
            
            while get_session(chat_id).get('active') and loop_count < 250:
                loop_count += 1
                time.sleep(3) 
                if not get_session(chat_id).get('active'): break
                    
                current_url = driver.current_url
                current_session = get_session(chat_id)

                if current_session.get('status') == 'waiting_credentials' or state == "WAIT_USER_SELECTION":
                    last_interaction = current_session.get('interaction_time', time.time())
                    if time.time() - last_interaction > 90:
                        try: bot.delete_message(chat_id, status_msg_id)
                        except: pass
                        ui_msg = current_session.get('ui_msg_id')
                        if ui_msg:
                            try: bot.delete_message(chat_id, ui_msg)
                            except: pass
                        
                        msg_to = bot.send_message(chat_id, "⏳ **تم إنهاء الجلسة تلقائياً!**\n\nتجاوزت مهلة الاستجابة (دقيقة ونصف). تم طردك وإخلاء مكانك في الطابور للسماح بدخول المستخدم التالي.\nيرجى إرسال الرابط مرة أخرى عندما تكون جاهزاً.", parse_mode="Markdown")
                        threading.Timer(300.0, lambda m=msg_to.message_id: bot.delete_message(chat_id, m)).start()
                        break

                if 'accounts.google.com' in current_url:
                    page_source_lower = driver.page_source.lower()
                    if "couldn't sign you in" in page_source_lower or "domain admin" in page_source_lower or "admin for help" in page_source_lower:
                        try: bot.delete_message(chat_id, status_msg_id)
                        except: pass
                        bot.send_message(chat_id, "❌ **تم حظر تسجيل الدخول بواسطة Google:**\nحساب Qwiklabs المستخدم مقيد أو يتطلب صلاحيات مسؤول (Domain Admin).\n\n💡 **الحل:** يرجى إغلاق اللاب (End Lab) الحالي، وبدء لاب جديد للحصول على حساب نظيف وغير محظور.", parse_mode="Markdown")
                        break

                    email_inputs = driver.find_elements(By.XPATH, "//input[@type='email']")
                    pass_inputs = driver.find_elements(By.XPATH, "//input[@type='password']")
                    
                    if email_inputs and email_inputs[0].is_displayed() and not (pass_inputs and pass_inputs[0].is_displayed()):
                        if not sso_tried:
                            update_live_stream(chat_id, status_msg_id, "🔄 **جاري التحويل:**\nأحاول الدخول عبر رابط Qwiklabs الأصلي لتسريع العملية...", driver=driver, is_photo=is_status_photo)
                            sso_tried = True
                            driver.get(url) 
                            state = "INIT"
                            time.sleep(2)
                            continue
                        elif not cookies_tried and saved_cookies:
                            update_live_stream(chat_id, status_msg_id, "⚡ **استعادة ذكية:**\nجاري حقن الكوكيز السابقة لتخطي تسجيل الدخول...", driver=driver, is_photo=is_status_photo)
                            inject_cookies_safely(driver, saved_cookies)
                            cookies_tried = True
                            driver.get(target_url_to_load) 
                            state = initial_state
                            time.sleep(2)
                            continue
                        elif current_session.get('status') != 'waiting_credentials' and not current_session.get('email'):
                            try: bot.delete_message(chat_id, status_msg_id)
                            except: pass
                            msg = bot.send_message(chat_id, "⚠️ **توقف - مطلوب بيانات الدخول.**\n\nالرجاء إرسال **الإيميل** و **الباسورد** الخاصين بـ Qwiklabs.\n*(يمكنك إرسالهم في سطر واحد أو سطرين)*\n\nمثال:\n`student-02-xxx@qwiklabs.net Password123`", parse_mode="Markdown")
                            update_session(chat_id, {'status': 'waiting_credentials', 'ui_msg_id': msg.message_id, 'interaction_time': time.time()})
                            status_msg_id = msg.message_id
                            continue
                    
                    if current_session.get('email') and current_session.get('password'):
                        try:
                            if email_inputs and email_inputs[0].is_displayed():
                                update_live_stream(chat_id, status_msg_id, "مصادقة الحساب", f"[المصادقة] إدخال البريد: {current_session.get('email')}", driver=driver, is_photo=is_status_photo)
                                email_inputs[0].clear()
                                email_inputs[0].send_keys(current_session.get('email'))
                                email_inputs[0].send_keys(Keys.ENTER)
                                time.sleep(2)
                                continue
                            elif pass_inputs and pass_inputs[0].is_displayed():
                                update_live_stream(chat_id, status_msg_id, "مصادقة الحساب", "[المصادقة] إدخال كلمة المرور السريّة... ***", driver=driver, is_photo=is_status_photo)
                                pass_inputs[0].clear()
                                pass_inputs[0].send_keys(current_session.get('password'))
                                pass_inputs[0].send_keys(Keys.ENTER)
                                time.sleep(3)
                                
                                update_session(chat_id, {'email': None, 'password': None})
                                state = "INIT"
                                
                                try: bot.delete_message(chat_id, status_msg_id)
                                except: pass
                                markup = InlineKeyboardMarkup().add(InlineKeyboardButton("🛑 إلغاء فوري", callback_data="abort_mission"))
                                try:
                                    ss = driver.get_screenshot_as_png()
                                    msg = bot.send_photo(chat_id, photo=ss, caption="🟢 **سجل العمليات المباشر:**\n✅ تمت المصادقة بنجاح، جاري استكمال بناء السيرفر...", parse_mode="Markdown", reply_markup=markup)
                                    is_status_photo = True
                                except:
                                    msg = bot.send_message(chat_id, "🟢 **سجل العمليات المباشر:**\n✅ تمت المصادقة بنجاح، جاري استكمال بناء السيرفر...", parse_mode="Markdown", reply_markup=markup)
                                    is_status_photo = False
                                status_msg_id = msg.message_id
                                continue
                        except Exception as e:
                            print("Login Error:", e)

                if current_session.get('status') == 'waiting_credentials':
                    continue 
                
                if state == "WAIT_USER_SELECTION":
                    if current_session.get('replace_mode') or current_session.get('add_new_mode'): pass 
                    if current_session.get('selected_region') and current_session.get('protocol'):
                        selected_reg = current_session.get('selected_region')
                        if project_id:
                            driver.get(f"https://shell.cloud.google.com/?enableapi=true&project={project_id}&pli=1&show=terminal")
                            state = "AUTHORIZE_SHELL" 
                    continue
                    
                elif state == "SILENT_BUILD":
                    page_source = driver.page_source
                    if "ERROR_DEPLOYMENT_FAILED_OCX_CATCH" in page_source:
                        try: bot.delete_message(chat_id, status_msg_id)
                        except: pass
                        break
                    
                    sync_match = re.search(r'OCX_DATA_SYNC:\s*(.*?)\|(.*?)\|(.*?)\|(.*?)(?:\n|<)', page_source)
                    if sync_match:
                         s_name, s_reg, s_proto, _ = sync_match.groups()
                         final_cookies = driver.get_cookies()
                         save_successful_server(chat_id, url, s_name, s_reg, s_proto, project_id, final_cookies)

                    if "SUCCESS_OCX_FINISH" in page_source:
                        try: bot.delete_message(chat_id, status_msg_id) 
                        except: pass
                        break 
                    else:
                        update_live_stream(chat_id, status_msg_id, f"🟢 **سجل العمليات المباشر:**\n⚙️ يتم الآن تجميع وحقن موارد الحاوية (Container)...\n⏳ الوقت المنقضي: {loop_count*3} ثانية", driver=driver, is_photo=is_status_photo)
                        continue
                else:
                    ar_state = state
                    if state == "INIT": ar_state = "التهيئة واجتياز الشروط"
                    elif state == "WAIT_DEPLOY": ar_state = "البحث عن واجهة البناء"
                    elif state == "WAIT_REGION": ar_state = "تحميل خريطة السيرفرات"
                    elif state == "EXTRACT_REGIONS": ar_state = "استخراج البيانات المعمارية"
                    elif state == "AUTHORIZE_SHELL": ar_state = "تفويض صلاحيات الطرفية"
                    elif state == "WAIT_TERMINAL_BOOT": ar_state = "تشغيل بيئة Linux"
                    elif state == "INJECT_PAYLOAD": ar_state = "حقن السكربت الخبيث/الرئيسي"
                    
                    update_live_stream(chat_id, status_msg_id, f"🟢 **سجل العمليات المباشر:**\n🌐 المرحلة الحالية: `{ar_state}`", driver=driver, is_photo=is_status_photo)
                
                try:
                    agree_btns = driver.find_elements(By.XPATH, "//button[contains(., 'Agree and continue') or contains(., 'موافق ومتابعة') or contains(., 'Akkoord en doorgaan')]")
                    visible_btn = next((b for b in agree_btns if b.is_displayed()), None)
                    if visible_btn:
                        for cb in driver.find_elements(By.XPATH, "//*[@role='checkbox'] | //mat-checkbox | //input[@type='checkbox']"):
                            driver.execute_script("arguments[0].click();", cb)
                        time.sleep(1) 
                        driver.execute_script("arguments[0].click();", visible_btn)
                except: pass
                
                if state == "INIT":
                    if 'accounts.google.com' in current_url:
                        try:
                            elements = driver.find_elements(By.XPATH, "//*[@id='confirm'] | //input[@type='submit'] | //button | //div[@role='button'] | //span")
                            for el in elements:
                                text, el_id = (el.text or el.get_attribute('value') or '').lower(), el.get_attribute('id') or ''
                                if 'understand' in text or 'begrijp' in text or 'accept' in text or 'أفهم' in text or 'موافق' in text or 'continue' in text or 'متابعة' in text or el_id == 'confirm':
                                    driver.execute_script("arguments[0].click();", el)
                                    break
                        except: pass
                    elif 'console.cloud.google.com' in current_url:
                        match = re.search(r'project=([^&#]+)', current_url)
                        if match:
                            extracted_project_id = match.group(1)
                            project_id = saved_project_id if (saved_project_id and (current_session.get('replace_mode') or current_session.get('add_new_mode'))) else extracted_project_id
                            
                            try:
                                fresh_cookies = driver.get_cookies()
                                update_server_cookies(url, fresh_cookies)
                                update_live_stream(chat_id, status_msg_id, "🟢 **سجل العمليات المباشر:**\n🔐 تم الوصول بنجاح. تم حفظ الكوكيز في القاعدة.", driver=driver, is_photo=is_status_photo)
                                time.sleep(1)
                            except Exception as e:
                                pass
                            
                            if current_session.get('replace_mode'):
                                driver.get(f"https://shell.cloud.google.com/?enableapi=true&project={project_id}&pli=1&show=terminal")
                                state = "AUTHORIZE_SHELL" 
                            else:
                                driver.get(f"https://console.cloud.google.com/run/services?project={project_id}")
                                state = "WAIT_DEPLOY" 
                            
                elif state == "WAIT_DEPLOY":
                    try:
                        deploy_btn = driver.find_element(By.XPATH, "//*[contains(text(), 'Deploy container')]")
                        if deploy_btn.is_displayed():
                            driver.execute_script("arguments[0].click();", deploy_btn)
                            state = "WAIT_REGION"
                    except: pass 
                        
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
                    except: driver.execute_script("window.scrollBy(0, 300);")
                        
                elif state == "EXTRACT_REGIONS":
                    if current_session.get('replace_mode'):
                         state = "WAIT_USER_SELECTION"
                         continue

                    try:
                        time.sleep(1) 
                        options = driver.find_elements(By.XPATH, "//*[@role='option'] | //mat-option | //*[contains(@class, 'mat-option-text')]")
                        regions_list = []
                        for opt in options:
                            text = (opt.get_attribute('textContent') or opt.text or '').strip()
                            if len(text) > 3 and "Select" not in text and text not in [r['raw'] for r in regions_list]:
                                text = " ".join(text.split())
                                match = re.search(r'^([a-z0-9-]+)\s*\(([^)]+)\)', text)
                                if match: reg_id, reg_name = match.group(1), match.group(2)
                                else: reg_id, reg_name = text.split()[0], text
                                
                                if reg_id.startswith('us-') or reg_id.startswith('northamerica-') or reg_id.startswith('southamerica-'): continent = 'أمريكا 🌎'
                                elif reg_id.startswith('europe-'): continent = 'أوروبا 🌍'
                                elif reg_id.startswith('asia-'): continent = 'آسيا 🌏'
                                elif reg_id.startswith('australia-'): continent = 'أستراليا 🦘'
                                elif reg_id.startswith('me-') or reg_id.startswith('africa-'): continent = 'الشرق الأوسط وأفريقيا 🐪'
                                else: continent = 'أخرى 🗺️'
                                    
                                regions_list.append({'id': reg_id, 'name': reg_name, 'continent': continent, 'raw': text})
                                
                        if len(regions_list) > 0: 
                            grouped_regions = {}
                            for r in regions_list: grouped_regions.setdefault(r['continent'], []).append(r)
                            update_session(chat_id, {'available_regions': grouped_regions, 'project_id': project_id})
                            
                            try: bot.delete_message(chat_id, status_msg_id)
                            except: pass
                            status_msg_id = None
                            
                            markup = InlineKeyboardMarkup(row_width=2)
                            markup.add(*[InlineKeyboardButton(text=c, callback_data=f"cont_{c}") for c in grouped_regions.keys()])
                            msg = bot.send_message(chat_id, "📍 **تم جلب السيرفرات المتاحة بنجاح.**\n\n👇 الرجاء اختيار القارة لتحديد السيرفر:", reply_markup=markup, parse_mode="Markdown")
                            update_session(chat_id, {'ui_msg_id': msg.message_id, 'interaction_time': time.time()})
                            state = "WAIT_USER_SELECTION"
                        else:
                            driver.execute_script("document.body.click();") 
                            time.sleep(1)
                            try:
                                current_val = driver.find_element(By.XPATH, "//*[contains(text(), 'Region')]/following::*[@role='combobox'][1]")
                                ActionChains(driver).move_to_element(current_val).click().perform()
                            except: pass
                    except: state = "DONE"
                        
                elif state == "AUTHORIZE_SHELL":
                    if status_msg_id is None:
                        ui_msg_id = current_session.get('ui_msg_id')
                        if ui_msg_id:
                            try: bot.delete_message(chat_id, ui_msg_id)
                            except: pass
                        markup = InlineKeyboardMarkup().add(InlineKeyboardButton("🛑 إلغاء فوري", callback_data="abort_mission"))
                        try:
                            ss = driver.get_screenshot_as_png()
                            msg = bot.send_photo(chat_id, photo=ss, caption="🟢 **سجل العمليات المباشر:**\n🚀 يتم الآن إجبار الطرفية (Shell) على الفتح...", parse_mode="Markdown", reply_markup=markup)
                            is_status_photo = True
                        except:
                            msg = bot.send_message(chat_id, "🟢 **سجل العمليات المباشر:**\n🚀 يتم الآن إجبار الطرفية (Shell) على الفتح...", parse_mode="Markdown", reply_markup=markup)
                            is_status_photo = False
                        status_msg_id = msg.message_id

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
                    js_check_term = "function checkTerm(root){if(root.querySelector('textarea.xterm-helper-textarea'))return true;for(let f of root.querySelectorAll('iframe')){try{if(checkTerm(f.contentDocument))return true;}catch(e){}}return false;} return checkTerm(document);"
                    if driver.execute_script(js_check_term):
                        time.sleep(1) 
                        state = "INJECT_PAYLOAD"

                elif state == "INJECT_PAYLOAD":
                    update_live_stream(chat_id, status_msg_id, "تثبيت النواة الأساسية", "[الأنظمة] جاري حقن كود OCX السري في خوادم Google...", driver=driver, is_photo=is_status_photo)
                    current_session = get_session(chat_id)
                    selected_reg = current_session.get('selected_region', 'europe-west4')
                    protocol = current_session.get('protocol', 'vless')
                    replace_mode = current_session.get('replace_mode', False)
                    old_server_name = current_session.get('old_server_name', '')
                    
                    inbound_cfg, link_gen = "", ""
                    proto_name = protocol.upper()
                    
                    if protocol == 'vmess':
                        inbound_cfg = r"""{"log": {"loglevel": "none"},"inbounds": [{"listen": "0.0.0.0", "port": ${PORT}, "protocol": "vmess","settings": {"clients": [{"id": "${UUID}", "alterId": 0}]},"streamSettings": {"network": "ws", "wsSettings": {"path": "${WS_PATH}", "maxEarlyData": 1024, "earlyDataHeaderName": "Sec-WebSocket-Protocol"}},"sniffing": {"enabled": false}}],"outbounds": [{"protocol": "freedom", "settings": {"domainStrategy": "AsIs"}}],"policy": {"levels": {"0": {"handshake": 1, "connIdle": 600, "uplinkOnly": 1, "downlinkOnly": 1}}}}"""
                        link_gen = r"""VMESS_JSON="{\"v\":\"2\",\"ps\":\"𝗢 𝗖 𝗫 ⚡️\",\"add\":\"vpn.googleapis.com\",\"port\":\"443\",\"id\":\"${UUID}\",\"aid\":\"0\",\"net\":\"ws\",\"type\":\"none\",\"host\":\"${SERVICE_HOST}\",\"path\":\"/%40O_C_X7\",\"tls\":\"tls\",\"sni\":\"yt.be\"}" && VPN_LINK="vmess://$(echo -n "$VMESS_JSON" | base64 -w 0)" """
                    elif protocol == 'trojan':
                        inbound_cfg = r"""{"log": {"loglevel": "none"},"inbounds": [{"listen": "0.0.0.0", "port": ${PORT}, "protocol": "trojan","settings": {"clients": [{"password": "${UUID}"}]},"streamSettings": {"network": "ws", "wsSettings": {"path": "${WS_PATH}", "maxEarlyData": 1024, "earlyDataHeaderName": "Sec-WebSocket-Protocol"}},"sniffing": {"enabled": false}}],"outbounds": [{"protocol": "freedom", "settings": {"domainStrategy": "AsIs"}}],"policy": {"levels": {"0": {"handshake": 1, "connIdle": 600, "uplinkOnly": 1, "downlinkOnly": 1}}}}"""
                        link_gen = r"""VPN_LINK="trojan://${UUID}@vpn.googleapis.com:443?path=/%40O_C_X7&security=tls&host=${SERVICE_HOST}&type=ws&sni=yt.be#𝗢 𝗖 𝗫 ⚡️" """
                    else:
                        inbound_cfg = r"""{"log": {"loglevel": "none"},"inbounds": [{"listen": "0.0.0.0", "port": ${PORT}, "protocol": "vless","settings": {"clients": [{"id": "${UUID}", "level": 0}], "decryption": "none"},"streamSettings": {"network": "ws", "wsSettings": {"path": "${WS_PATH}", "maxEarlyData": 1024, "earlyDataHeaderName": "Sec-WebSocket-Protocol"}},"sniffing": {"enabled": false}}],"outbounds": [{"protocol": "freedom", "settings": {"domainStrategy": "AsIs"}}],"policy": {"levels": {"0": {"handshake": 1, "connIdle": 600, "uplinkOnly": 1, "downlinkOnly": 1}}}}"""
                        link_gen = r"""VPN_LINK="vless://${UUID}@vpn.googleapis.com:443?path=/%40O_C_X7&security=tls&encryption=none&host=${SERVICE_HOST}&type=ws&sni=yt.be#𝗢 𝗖 𝗫 ⚡️" """

                    final_script = VPN_SCRIPT_TEMPLATE.replace("<INBOUND_CONFIG_PLACEHOLDER>", inbound_cfg).replace("<LINK_GENERATION_PLACEHOLDER>", link_gen).replace("TARGET_REGION_PLACEHOLDER", selected_reg).replace("PROTOCOL_NAME_PLACEHOLDER", proto_name).replace("<BOT_TOKEN_PLACEHOLDER>", BOT_TOKEN).replace("<CHAT_ID_PLACEHOLDER>", str(chat_id))
                    
                    if replace_mode and old_server_name: final_script = final_script.replace("REPLACE_MODE_PLACEHOLDER", "True").replace("OLD_SERVER_NAME_PLACEHOLDER", old_server_name)
                    else: final_script = final_script.replace("REPLACE_MODE_PLACEHOLDER", "False")
                    
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
                            setTimeout(() => { ta.dispatchEvent(new KeyboardEvent('keydown', {bubbles: true, cancelable: true, keyCode: 13, key: 'Enter'})); }, 500);
                            return true;
                        }
                        for (let f of root.querySelectorAll('iframe')) { try { if (pasteToTerminal(f.contentDocument, text)) return true; } catch(e) {} }
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
                    
            if not get_session(chat_id).get('active'):
                try: bot.delete_message(chat_id, status_msg_id)
                except: pass
                
        except Exception as e:
            try: bot.delete_message(chat_id, status_msg_id)
            except: pass
            bot.send_message(chat_id, f"❌ **حدث خطأ داخلي غير متوقع:**\n`{str(e)[:150]}`", parse_mode="Markdown")
        finally:
            if driver:
                try: driver.quit()
                except: pass
            clear_session(chat_id)
            task_queue.task_done()

threading.Thread(target=worker_loop, daemon=True).start()

# ==========================================
# 🎛️ إدارة واجهة المستخدم الأحادية الاحترافية
# ==========================================
@bot.message_handler(commands=['start', 'help'])
def send_welcome(message):
    chat_id = message.chat.id
    try: bot.delete_message(chat_id, message.message_id) 
    except: pass
    
    if not is_vip(chat_id):
        send_unauthorized_msg(chat_id)
        return
        
    text = (
        "💎 **مرحباً بك في نظام OCX PRO** 💎\n\n"
        "⚡ أسرع نظام لإنشاء سيرفرات Qwiklabs المشفرة.\n"
        "🔗 **فقط قم بإرسال رابط الدخول المباشر لبدء العملية.**"
    )
    
    if str(chat_id) == str(ADMIN_ID):
        markup = ReplyKeyboardMarkup(resize_keyboard=True, row_width=1)
        markup.add(KeyboardButton("👑 لوحة الإدارة"))
        bot.send_message(chat_id, text, reply_markup=markup, parse_mode="Markdown")
    else:
        bot.send_message(chat_id, text, reply_markup=telebot.types.ReplyKeyboardRemove(), parse_mode="Markdown")

@bot.message_handler(commands=['cancel', 'stop'])
def force_cancel(message):
    chat_id = message.chat.id
    if not is_vip(chat_id): return
    try: bot.delete_message(chat_id, message.message_id)
    except: pass
    clear_session(chat_id)
    bot.send_message(chat_id, "🛑 **تم إلغاء أي مهام قيد التنفيذ وتفريغ الجلسة بنجاح.**\nيمكنك الآن إرسال رابط جديد بحرية.", parse_mode="Markdown")

def process_add_vip(message):
    new_id = message.text.strip()
    if new_id.isdigit():
        add_vip_user(new_id)
        bot.reply_to(message, f"✅ تم إضافة العميل `{new_id}` بنجاح إلى قائمة الـ VIP.", parse_mode="Markdown")
        
        try:
            session = get_session(new_id)
            unauth_msg_id = session.get('unauth_msg_id')
            if unauth_msg_id:
                try: bot.delete_message(new_id, unauth_msg_id)
                except: pass
                update_session(new_id, {'unauth_msg_id': None})
            
            welcome_text = (
                "🎉 **تم تفعيل اشتراكك بنجاح!**\n\n"
                "💎 **مرحباً بك في نظام OCX PRO** 💎\n\n"
                "⚡ أسرع نظام لإنشاء سيرفرات Qwiklabs المشفرة.\n"
                "🔗 **فقط قم بإرسال رابط الدخول المباشر لبدء العملية.**"
            )
            bot.send_message(new_id, welcome_text, parse_mode="Markdown")
        except: pass
    else:
        bot.reply_to(message, "❌ معرف خاطئ، الرجاء إرسال أرقام فقط.")

def process_del_vip(message):
    del_id = message.text.strip()
    if del_id.isdigit():
        remove_vip_user(del_id)
        bot.reply_to(message, f"🗑️ تم حذف العميل `{del_id}` بنجاح وتم سحب صلاحياته.", parse_mode="Markdown")
        
        try:
            bot.send_message(del_id, "⛔️ **تم سحب صلاحياتك وإلغاء اشتراكك من البوت.**\nللاستفسار، يرجى التواصل مع الإدارة.", parse_mode="Markdown")
        except: pass
    else:
        bot.reply_to(message, "❌ معرف خاطئ، الرجاء إرسال أرقام فقط.")

def process_broadcast(message):
    text = message.text
    if text in ["👥 قائمة الـ VIP", "📊 حالة النظام", "➕ إضافة عميل", "➖ إزالة عميل", "📢 إذاعة رسالة", "🔙 القائمة الرئيسية"]:
        bot.reply_to(message, "❌ تم إلغاء الإذاعة.")
        return
        
    vips = get_all_vips()
    success_count = 0
    bot.reply_to(message, "⏳ جاري الإرسال للجميع...")
    for uid in vips:
        try:
            bot.send_message(uid, f"📢 **إشعار من الإدارة:**\n\n{text}", parse_mode="Markdown")
            success_count += 1
        except: pass
    bot.send_message(message.chat.id, f"✅ **تمت الإذاعة بنجاح!**\nتم إرسال الرسالة إلى `{success_count}` من المشتركين.", parse_mode="Markdown")

@bot.message_handler(func=lambda message: message.text == "👑 لوحة الإدارة")
def handle_admin_panel(message):
    chat_id = message.chat.id
    if not is_vip(chat_id): return
    if str(chat_id) == str(ADMIN_ID):
        markup = ReplyKeyboardMarkup(resize_keyboard=True, row_width=2)
        markup.add(KeyboardButton("👥 قائمة الـ VIP"), KeyboardButton("📊 حالة النظام"))
        markup.add(KeyboardButton("➕ إضافة عميل"), KeyboardButton("➖ إزالة عميل"))
        markup.add(KeyboardButton("📢 إذاعة رسالة"), KeyboardButton("🔙 القائمة الرئيسية"))
        bot.reply_to(message, "👑 **لوحة تحكم الإدارة (Admin Dashboard)** 👑", reply_markup=markup, parse_mode="Markdown")

@bot.message_handler(func=lambda message: message.text in ["👥 قائمة الـ VIP", "📊 حالة النظام", "➕ إضافة عميل", "➖ إزالة عميل", "📢 إذاعة رسالة", "🔙 القائمة الرئيسية"])
def handle_admin_keyboard(message):
    chat_id = message.chat.id
    if str(chat_id) != str(ADMIN_ID): return
    
    text = message.text
    if text == "👥 قائمة الـ VIP":
        vips = get_all_vips()
        res_text = "👥 **قائمة العملاء (VIPs):**\n\n" + ("\n".join([f"🔹 `{uid}`" for uid in vips]) if vips else "القائمة فارغة.")
        bot.reply_to(message, res_text, parse_mode="Markdown")
    elif text == "📊 حالة النظام":
        q_size = task_queue.qsize()
        db_type = 'MongoDB 🟢' if USE_MONGO else 'RAM (مؤقت) 🟡'
        res_text = (
            f"📊 **حالة النظام:**\n\n"
            f"📦 مهام في الطابور: `{q_size}`\n"
            f"💾 نوع التخزين: `{db_type}`\n"
            f"🌐 المتصفح: `Headless v2 ⚡`"
        )
        bot.reply_to(message, res_text, parse_mode="Markdown")
    elif text == "➕ إضافة عميل":
        msg = bot.send_message(chat_id, "✏️ **الرجاء إرسال الـ ID الخاص بالعميل:**", parse_mode="Markdown")
        bot.register_next_step_handler(msg, process_add_vip)
    elif text == "➖ إزالة عميل":
        msg = bot.send_message(chat_id, "✏️ **الرجاء إرسال الـ ID الخاص بالعميل:**", parse_mode="Markdown")
        bot.register_next_step_handler(msg, process_del_vip)
    elif text == "📢 إذاعة رسالة":
        msg = bot.send_message(chat_id, "📢 **الرجاء إرسال الرسالة التي تريد إيصالها لجميع المشتركين:**", parse_mode="Markdown")
        bot.register_next_step_handler(msg, process_broadcast)
    elif text == "🔙 القائمة الرئيسية":
        markup = ReplyKeyboardMarkup(resize_keyboard=True, row_width=1)
        markup.add(KeyboardButton("👑 لوحة الإدارة"))
        bot.reply_to(message, "🔙 تم الرجوع للواجهة الرئيسية.", reply_markup=markup)

@bot.message_handler(func=lambda message: get_session(message.chat.id).get('status') == 'waiting_credentials')
def handle_credentials(message):
    chat_id = message.chat.id
    text = message.text.strip()
    
    email_match = re.search(r'[\w\.-]+@[\w\.-]+\.\w+', text)
    
    if email_match:
        email = email_match.group(0)
        password = text.replace(email, '').strip()
        
        if password:
            update_session(chat_id, {'email': email, 'password': password, 'status': 'processing', 'interaction_time': time.time()})
            try: bot.delete_message(chat_id, message.message_id)
            except: pass
            bot.send_message(chat_id, "✅ **تم استلام البيانات بنجاح!**\nيتم الآن المصادقة عبر المحرك...", parse_mode="Markdown")
            return
            
    bot.send_message(chat_id, "⚠️ **تنسيق خاطئ!**\nالرجاء إرسال الإيميل والباسورد معاً بأي شكل (سطر واحد أو سطرين).\n\nمثال:\n`student-02-1234@qwiklabs.net Password123`", parse_mode="Markdown")

@bot.callback_query_handler(func=lambda call: True)
def handle_query(call):
    chat_id = call.message.chat.id
    data = call.data
    
    if not is_vip(chat_id):
        bot.answer_callback_query(call.id, "❌ عذراً، ليس لديك صلاحية.")
        return

    session = get_session(chat_id)
    update_session(chat_id, {'interaction_time': time.time()})
    
    if data == "cancel_ui":
        clear_session(chat_id)
        bot.edit_message_text("🛑 تم إلغاء العملية بأمر منك.", chat_id=chat_id, message_id=call.message.message_id)
        threading.Timer(300.0, lambda m=call.message.message_id: bot.delete_message(chat_id, m)).start()
        return

    if data == "abort_mission":
        clear_session(chat_id)
        bot.answer_callback_query(call.id, "تم الإلغاء الفوري!")
        try: bot.delete_message(chat_id, call.message.message_id)
        except: pass
        msg = bot.send_message(chat_id, "🛑 **تم إلغاء المهمة وتفريغ الجلسة.**\nالنظام الآن جاهز لاستقبال رابط جديد.", parse_mode="Markdown")
        threading.Timer(300.0, lambda m=msg.message_id: bot.delete_message(chat_id, m)).start()
        return

    if data in ["replace_server", "add_new_server"]:
        url = session.get('target_url')
        if not url: return
        update_data = {'active': True, 'status': 'queued'}
        
        if data == "replace_server":
             old_server = get_server_by_url(url)
             update_data.update({'replace_mode': True, 'old_server_name': old_server.get('server_name', ''), 'selected_region': old_server.get('region', ''), 'protocol': old_server.get('protocol', 'vless')})
             bot.edit_message_text("🔄 **تم اختيار: استبدال السيرفر القديم.**\nجاري تجهيز بيئة العمل...", chat_id=chat_id, message_id=call.message.message_id, parse_mode="Markdown")
        else:
             update_data.update({'replace_mode': False, 'add_new_mode': True})
             bot.edit_message_text("➕ **تم اختيار: إضافة سيرفر جديد للمشروع.**\nجاري تجهيز بيئة العمل...", chat_id=chat_id, message_id=call.message.message_id, parse_mode="Markdown")

        update_session(chat_id, update_data)
        task_queue.put({'chat_id': chat_id, 'url': url})
        return

    if not session.get('active'):
        bot.answer_callback_query(call.id, "❌ الجلسة منتهية أو ملغية مسبقاً.")
        return
        
    if data.startswith("cont_"):
        continent = data.split("cont_")[1]
        regions = session.get('available_regions', {}).get(continent, [])
        markup = InlineKeyboardMarkup(row_width=1)
        for r in regions:
            markup.add(InlineKeyboardButton(text=f"{translate_region(r['name'])} ({r['id']})", callback_data=f"reg_{r['id']}"))
        markup.add(InlineKeyboardButton(text="🔙 العودة للقارات", callback_data="back_to_conts"))
        bot.edit_message_text(f"📍 سيرفرات نطاق {continent}:", chat_id=chat_id, message_id=call.message.message_id, reply_markup=markup)
        
    elif data.startswith("reg_"):
        reg_id = data.split("reg_")[1]
        update_session(chat_id, {'selected_region': reg_id})
        markup = InlineKeyboardMarkup(row_width=3)
        markup.add(InlineKeyboardButton("⚡ VLESS", callback_data="proto_vless"), InlineKeyboardButton("🛡️ VMESS", callback_data="proto_vmess"), InlineKeyboardButton("🐎 TROJAN", callback_data="proto_trojan"))
        bot.edit_message_text(f"✅ تم اختيار المنطقة: `{reg_id}`\n\n👇 الرجاء اختيار بروتوكول التشفير المفضل:", chat_id=chat_id, message_id=call.message.message_id, reply_markup=markup, parse_mode="Markdown")
                              
    elif data.startswith("proto_"):
        protocol = data.split("_")[1]
        update_session(chat_id, {'protocol': protocol})
        try: bot.delete_message(chat_id, call.message.message_id)
        except: pass

    elif data == "back_to_conts":
        grouped_regions = session.get('available_regions', {})
        markup = InlineKeyboardMarkup(row_width=2)
        markup.add(*[InlineKeyboardButton(text=c, callback_data=f"cont_{c}") for c in grouped_regions.keys()])
        bot.edit_message_text("👇 الرجاء اختيار القارة لتحديد السيرفر:", chat_id=chat_id, message_id=call.message.message_id, reply_markup=markup)

@bot.message_handler(func=lambda message: message.text.startswith('http'))
def handle_url(message):
    chat_id = message.chat.id
    url = message.text
    
    try: bot.delete_message(chat_id, message.message_id)
    except: pass

    if not is_vip(chat_id):
        send_unauthorized_msg(chat_id)
        return

    if not url.startswith("https://www.skills.google/google_sso"):
        return

    session = get_session(chat_id)
    if session.get('active'):
        msg = bot.send_message(chat_id, "⚠️ **تنبيه: لديك مهمة قيد التنفيذ حالياً!**\n\nإذا كنت عالقاً، أرسل الأمر /cancel لإلغاء الجلسة السابقة، ثم أرسل الرابط الجديد.", parse_mode="Markdown")
        threading.Timer(15.0, lambda m=msg.message_id: bot.delete_message(chat_id, m)).start()
        return

    existing_server = get_server_by_url(url)
    if existing_server and existing_server.get('project_id'):
        update_session(chat_id, {'target_url': url})
        markup = InlineKeyboardMarkup(row_width=1)
        markup.add(
            InlineKeyboardButton("🔄 استبدال السيرفر القديم بنفس الرابط", callback_data="replace_server"),
            InlineKeyboardButton("➕ بناء سيرفر جديد بجانب القديم", callback_data="add_new_server"),
            InlineKeyboardButton("🛑 إلغاء العملية", callback_data="cancel_ui")
        )
        msg = bot.send_message(chat_id, "⚠️ **لقد قمت باستخدام هذا الرابط مسبقاً!**\nكيف تفضل التعامل معه؟", reply_markup=markup, parse_mode="Markdown")
        update_session(chat_id, {'ui_msg_id': msg.message_id})
        return

    msg = bot.send_message(chat_id, "⏳ **تمت الإضافة للطابور بنجاح...**\nسيتم البدء فور توفر الموارد.", parse_mode="Markdown")
    update_session(chat_id, {'active': True, 'status': 'queued', 'target_url': url, 'ui_msg_id': msg.message_id})
    task_queue.put({'chat_id': chat_id, 'url': url})

@bot.message_handler(func=lambda message: True, content_types=['text', 'photo', 'video', 'document', 'audio', 'sticker', 'voice'])
def delete_spam_and_unrelated_messages(message):
    chat_id = message.chat.id
    try:
        bot.delete_message(chat_id, message.message_id)
    except:
        pass

if __name__ == "__main__":
    print("💎 OCX PRO SYSTEM IS ACTIVE & READY...")
    try: bot.remove_webhook()
    except: pass
    while True:
        try: bot.polling(none_stop=True)
        except Exception as e: time.sleep(3)
