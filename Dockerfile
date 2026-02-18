# نستخدم صورة رسمية من Playwright
FROM mcr.microsoft.com/playwright/python:v1.41.0-jammy

# تحديد مجلد العمل
WORKDIR /app

# 🚀 تثبيت أداة الشاشة الوهمية Xvfb (مهم جداً لتخطي حماية جوجل)
RUN apt-get update && apt-get install -y xvfb

# نسخ ملفات المشروع
COPY . .

# تنصيب مكتبات البايثون
RUN pip install --no-cache-dir -r requirements.txt

# تحميل المتصفح واعتماداته
RUN playwright install chromium
RUN playwright install-deps chromium

# أمر التشغيل
CMD ["python", "main.py"]
