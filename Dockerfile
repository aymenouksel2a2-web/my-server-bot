FROM python:3.11-slim

# تحديث وتثبيت المتصفح والشاشة الوهمية (Xvfb)
RUN apt-get update && apt-get install -y \
    wget \
    gnupg \
    unzip \
    xvfb \
    chromium \
    chromium-driver \
    && rm -rf /var/lib/apt/lists/*

# إعداد مسار العمل
WORKDIR /app

# نسخ الملفات
COPY requirements.txt .
COPY main.py .

# تثبيت مكتبات بايثون
RUN pip install --no-cache-dir -r requirements.txt

# فتح البورت 8000 ليتعرف عليه Koyeb
EXPOSE 8000

# تشغيل البوت
CMD ["python", "main.py"]
