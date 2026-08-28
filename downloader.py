import asyncio
import os
import re
import uuid
import logging
from pathlib import Path
from typing import Optional, Dict, Any, List, Tuple, Callable
import yt_dlp
from config import DOWNLOADS_DIR, FFMPEG_PATH

logger = logging.getLogger(__name__)

# Instagram URL pattern
INSTAGRAM_REGEX = re.compile(
    r'https?:\/\/(?:www\.)?(?:instagram\.com|instagr\.am)\/(?:p|reel|reels|tv|share\/reel|share\/p)\/([A-Za-z0-9_\-]+)',
    re.IGNORECASE
)
INSTAGRAM_STORIES_REGEX = re.compile(
    r'https?:\/\/(?:www\.)?(?:instagram\.com|instagr\.am)\/stories\/([A-Za-z0-9_\.]+)\/([0-9]+)',
    re.IGNORECASE
)

# YouTube URL pattern (Shorts, Watch, youtu.be, clips, mobile)
YOUTUBE_REGEX = re.compile(
    r'https?:\/\/(?:www\.|m\.)?(?:youtube\.com\/(?:watch\?(?:.*&)?v=|shorts\/|live\/|embed\/|v\/|clip\/)|youtu\.be\/)([A-Za-z0-9_\-]{11}|[A-Za-z0-9_\-]+)',
    re.IGNORECASE
)

# TikTok URL pattern
TIKTOK_REGEX = re.compile(
    r'https?:\/\/(?:www\.|vm\.|vt\.)?tiktok\.com\/[^\s]+',
    re.IGNORECASE
)


def detect_supported_url(text: str) -> Optional[Tuple[str, str]]:
    """
    Matn ichidan qo'llab-quvvatlanadigan havolani va uning turini aniqlaydi.
    """
    # 1. Instagram
    ig_match = INSTAGRAM_REGEX.search(text)
    if ig_match:
        code = ig_match.group(1)
        return "instagram", f"https://www.instagram.com/reel/{code}/"

    ig_stories_match = INSTAGRAM_STORIES_REGEX.search(text)
    if ig_stories_match:
        return "instagram", ig_stories_match.group(0)

    # 2. YouTube
    yt_match = YOUTUBE_REGEX.search(text)
    if yt_match:
        full_match = yt_match.group(0)
        code = yt_match.group(1)
        if "/shorts/" in full_match:
            return "youtube_shorts", f"https://www.youtube.com/shorts/{code}"
        elif "youtu.be" in full_match or "watch?v=" in full_match:
            return "youtube", f"https://www.youtube.com/watch?v={code}"
        return "youtube", full_match

    # 3. TikTok
    tt_match = TIKTOK_REGEX.search(text)
    if tt_match:
        return "tiktok", tt_match.group(0)

    return None


def extract_instagram_url(text: str) -> Optional[str]:
    """Eski muvofiqlik uchun"""
    res = detect_supported_url(text)
    if res and res[0] == "instagram":
        return res[1]
    return None


def _download_media_sync(
    url: str,
    output_id: str,
    platform: str,
    progress_callback: Optional[Callable[[int, str], None]] = None
) -> Optional[Dict[str, Any]]:
    """Sinxron yt-dlp yuklash funksiyasi"""
    out_template = str(DOWNLOADS_DIR / f"{output_id}_%(autonumber)02d.%(ext)s")
    
    # YouTube uchun maxsus 720p/1080p tez yuklanuvchi format
    format_selector = (
        'bestvideo[ext=mp4][height<=720]+bestaudio[ext=m4a]/best[ext=mp4][height<=720]/best[height<=720]/best'
        if "youtube" in platform
        else 'best/bestvideo+bestaudio'
    )

    def ydl_hook(d):
        if progress_callback and d.get('status') == 'downloading':
            total = d.get('total_bytes') or d.get('total_bytes_estimate') or 0
            downloaded = d.get('downloaded_bytes') or 0
            speed = d.get('speed') or 0
            if total > 0:
                percent = min(99, max(1, int(downloaded / total * 100)))
                speed_mb = (speed / (1024 * 1024)) if speed else 0.0
                progress_callback(percent, f"{speed_mb:.1f} MB/s")

    ydl_opts = {
        'format': format_selector,
        'outtmpl': out_template,
        'quiet': True,
        'no_warnings': True,
        'noplaylist': True,
        'socket_timeout': 30,
        'ffmpeg_location': FFMPEG_PATH,
        'progress_hooks': [ydl_hook] if progress_callback else [],
        'http_headers': {
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36',
            'Accept-Language': 'en-US,en;q=0.9',
        }
    }

    if "youtube" in platform:
        ydl_opts['match_filter'] = yt_dlp.utils.match_filter_func("duration <= 900")

    try:
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(url, download=True)
            if not info:
                return None

            raw_title = info.get('title') or ('YouTube Video' if 'youtube' in platform else 'Instagram Post')
            title = raw_title.strip()
            if len(title) > 90:
                title = title[:87] + "..."

            duration = info.get('duration') or 0.0
            uploader = info.get('uploader') or info.get('channel') or info.get('creator') or ''

            # Yuklangan barcha fayllarni topish
            found_files = list(DOWNLOADS_DIR.glob(f"{output_id}*.*"))

            if not found_files:
                single_template = str(DOWNLOADS_DIR / f"{output_id}.%(ext)s")
                ydl_opts['outtmpl'] = single_template
                with yt_dlp.YoutubeDL(ydl_opts) as ydl_single:
                    info = ydl_single.extract_info(url, download=True)
                    found_files = list(DOWNLOADS_DIR.glob(f"{output_id}.*"))

            if not found_files:
                return None

            items = []
            for f in sorted(found_files, key=lambda x: str(x)):
                ext = f.suffix.lower()
                if ext in ('.part', '.ytdl', '.json', '.info'):
                    continue
                is_video = ext in ('.mp4', '.mkv', '.webm', '.mov', '.avi', '.flv')
                items.append({
                    'file_path': str(f),
                    'is_video': is_video,
                    'ext': ext
                })

            if not items:
                return None

            return {
                'items': items,
                'title': title,
                'uploader': uploader,
                'duration': float(duration),
                'platform': platform,
                'source_url': url
            }
    except Exception as e:
        logger.error(f"Media yuklab olishda xatolik ({platform} - {url}): {e}")
        return None


async def download_media(
    url: str,
    platform: str = "generic",
    progress_callback: Optional[Callable[[int, str], None]] = None
) -> Optional[Dict[str, Any]]:
    """Istalgan platformadan media yuklab olish"""
    prefix = "yt" if "youtube" in platform else ("tt" if platform == "tiktok" else "ig")
    output_id = f"{prefix}_{uuid.uuid4().hex[:10]}"
    return await asyncio.to_thread(_download_media_sync, url, output_id, platform, progress_callback)


async def download_instagram_media(url: str) -> Optional[Dict[str, Any]]:
    """Eski muvofiqlik uchun"""
    return await download_media(url, "instagram")
