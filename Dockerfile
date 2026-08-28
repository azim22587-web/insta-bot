FROM python:3.11-slim

# FFmpeg va tizim utilitalarini o'rnatish
RUN apt-get update && \
    apt-get install -y --no-install-recommends ffmpeg curl && \
    rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Kutubxonalarni o'rnatish
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Loyiha fayllarini ko'chirish
COPY . .

# Papkalarni yaratish
RUN mkdir -p downloads temp

# Botni ishga tushirish
CMD ["python", "bot.py"]
