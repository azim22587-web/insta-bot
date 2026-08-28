import os
import sys
from pathlib import Path
from dotenv import load_dotenv
import imageio_ffmpeg

# Loyiha ildiz papkasi
BASE_DIR = Path(__file__).resolve().parent

# .env faylini yuklash
load_dotenv(BASE_DIR / ".env")

# Telegram Bot Token
BOT_TOKEN = os.getenv("BOT_TOKEN", "").strip()

# Papkalar
DOWNLOADS_DIR = BASE_DIR / "downloads"
TEMP_DIR = BASE_DIR / "temp"

DOWNLOADS_DIR.mkdir(parents=True, exist_ok=True)
TEMP_DIR.mkdir(parents=True, exist_ok=True)

# FFmpeg dasturi manzili (imageio-ffmpeg yoki tizimdagi ffmpeg)
try:
    FFMPEG_PATH = imageio_ffmpeg.get_ffmpeg_exe()
except Exception:
    FFMPEG_PATH = "ffmpeg"

# Maksimal video hajmi (Telegram bot uchun 50 MB limit)
MAX_FILE_SIZE_MB = 50
