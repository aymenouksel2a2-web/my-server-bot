# نستخدم صورة رسمية من Playwright تحتوي على بايثون والمتصفحات جاهزة
FROM mcr.microsoft.com/playwright/python:v1.41.0-jammy

# تحديد مجلد العمل
WORKDIR /app

# نسخ ملفات المشروع إلى السيرفر
COPY . .

# تنصيب مكتبات البايثون (Telegram + Flask)
RUN pip install --no-cache-dir -r requirements.txt

# تحميل متصفح كروم فقط (لضمان وجوده)
RUN playwright install chromium

# أمر التشغيل
CMD ["python", "main.py"]
