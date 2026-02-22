FROM python:3.11-slim

RUN apt-get update && apt-get install -y --no-install-recommends \
    chromium \
    chromium-driver \
    fonts-liberation \
    && rm -rf /var/lib/apt/lists/* \
    && apt-get clean

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

# Render سيقوم بتمرير البورت ديناميكياً
EXPOSE 8000

# استخدمنا -u لضمان ظهور السجلات (Logs) فوراً
CMD ["python", "-u", "main.py"]
