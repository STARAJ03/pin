# ──────────────────────────────────────────────
#  Base image
# ──────────────────────────────────────────────
FROM python:3.10-slim

# ──────────────────────────────────────────────
#  Environment setup
# ──────────────────────────────────────────────
ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    TZ=Asia/Kolkata

# ──────────────────────────────────────────────
#  System dependencies
# ──────────────────────────────────────────────
RUN pip install --no-cache-dir \
    pyrogram \
    tgcrypto \
    yt-dlp \
    aiohttp \
    aiofiles \
    requests \
    flask \
    waitress


# ──────────────────────────────────────────────
#  Copy project files
# ──────────────────────────────────────────────
WORKDIR /app
COPY . /app

# ──────────────────────────────────────────────
#  Python dependencies
# ──────────────────────────────────────────────
RUN pip install --no-cache-dir -U pip setuptools wheel \
 && pip install --no-cache-dir \
    pyrogram \
    tgcrypto \
    yt-dlp \
    aiohttp \
    aiofiles \
    requests

# ──────────────────────────────────────────────
#  Runtime environment variables
# ──────────────────────────────────────────────
# You can also pass BOT_TOKEN as an environment variable in `docker run`
#ENV BOT_TOKEN=""

# ──────────────────────────────────────────────
#  Start command
# ──────────────────────────────────────────────
CMD ["python", "main.py"]
