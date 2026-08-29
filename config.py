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

# Cookies fayli (mavjud bo'lsa server IP bloklarini chetlab o'tish uchun)
COOKIES_FILE = None
cookie_candidates = [
    "cookies.txt",
    "youtube_cookies.txt",
    "yt_cookies.txt",
    "instagram_cookies.txt",
    "www.youtube.com_cookies.txt",
    "www.instagram.com_cookies.txt"
]

for candidate in cookie_candidates:
    candidate_path = BASE_DIR / candidate
    if candidate_path.exists() and candidate_path.is_file():
        COOKIES_FILE = str(candidate_path)
        break

# Agar environment variable orqali cookies berilgan bo'lsa (.env da COOKIES_CONTENT yoki YOUTUBE_COOKIES)
if not COOKIES_FILE:
    env_cookies = os.getenv("COOKIES_CONTENT") or os.getenv("YOUTUBE_COOKIES") or os.getenv("COOKIES_FILE_PATH")
    if env_cookies:
        if os.path.exists(env_cookies) and os.path.isfile(env_cookies):
            COOKIES_FILE = env_cookies
        else:
            # Agar cookies matn ko'rinishida berilgan bo'lsa, faylga yozamiz
            auto_cookie_file = TEMP_DIR / "auto_cookies.txt"
            try:
                with open(auto_cookie_file, "w", encoding="utf-8") as f:
                    f.write(env_cookies.strip())
                COOKIES_FILE = str(auto_cookie_file)
            except Exception:
                pass
