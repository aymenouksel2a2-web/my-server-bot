# استخدام نسخة بايثون خفيفة
FROM python:3.10-slim

# تحديث النظام وتثبيت متصفح Chromium و WebDriver
RUN apt-get update && apt-get install -y \
    chromium \
    chromium-driver \
    && rm -rf /var/lib/apt/lists/*

# تحديد مجلد العمل داخل الخادم
WORKDIR /app

# نسخ ملف المتطلبات وتثبيت مكاتب بايثون
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# نسخ باقي ملفات المشروع (بما فيها main.py)
COPY . .

# أمر تشغيل البوت
CMD ["python", "main.py"]
