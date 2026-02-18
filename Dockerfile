# نستخدم صورة رسمية من Playwright تحتوي على بايثون والمتصفحات جاهزة
FROM mcr.microsoft.com/playwright/python:v1.41.0-jammy

# تحديد مجلد العمل
WORKDIR /app

# نسخ ملفات المشروع إلى السيرفر
COPY . .

# تنصيب مكتبات البايثون
RUN pip install --no-cache-dir -r requirements.txt

# تحميل متصفح Chromium والاعتمادات اللازمة لنظام Linux
RUN playwright install chromium
RUN playwright install-deps chromium

# أمر التشغيل
CMD ["python", "main.py"]
