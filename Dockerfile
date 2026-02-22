FROM python:3.11-slim

RUN apt-get update && apt-get install -y --no-install-recommends \
    chromium \
    chromium-driver \
    xvfb \
    fonts-liberation \
    && rm -rf /var/lib/apt/lists/* \
    && apt-get clean

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

# Render سيقوم بتمرير البورت ديناميكياً، ولكن نضع 8000 كقيمة افتراضية
EXPOSE 8000

# استخدمنا -u لضمان ظهور السجلات (Logs) فوراً في لوحة تحكم Render
CMD ["python", "-u", "main.py"]
