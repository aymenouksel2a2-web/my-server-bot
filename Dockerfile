FROM python:3.11-slim

# ── تثبيت Firefox و Xvfb والخطوط المطلوبة ──
RUN apt-get update && apt-get install -y --no-install-recommends \
    firefox-esr \
    xvfb \
    fonts-liberation \
    fonts-noto-color-emoji \
    && rm -rf /var/lib/apt/lists/* \
    && apt-get clean

WORKDIR /app

# ── تثبيت مكتبات Python ──
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

EXPOSE 8080

# ── فحص صحة داخلي لـ Railway ──
HEALTHCHECK --interval=30s --timeout=10s --start-period=60s --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://localhost:8080/health')" || exit 1

# ── تشغيل البوت ──
CMD ["python", "-u", "main.py"]
