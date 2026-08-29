import asyncio
import logging
import os
import re
import time
import uuid
from pathlib import Path
from typing import Optional, Dict, Any, List, Tuple, Callable
import requests
import yt_dlp
import instaloader

from config import DOWNLOADS_DIR, FFMPEG_PATH, COOKIES_FILE
from video_processor import get_video_duration

logger = logging.getLogger(__name__)

# Instagram URL pattern (Reels, Post, TV, Stories)
INSTAGRAM_REGEX = re.compile(
    r'https?:\/\/(?:www\.)?(?:instagram\.com|instagr\.am)\/(?:p|reel|reels|tv|share\/reel|share\/p)\/([A-Za-z0-9_\-]+)',
    re.IGNORECASE
)
INSTAGRAM_STORIES_REGEX = re.compile(
    r'https?:\/\/(?:www\.)?(?:instagram\.com|instagr\.am)\/stories\/([A-Za-z0-9_\.]+)\/([0-9]+)',
    re.IGNORECASE
)


def extract_instagram_shortcode(url_or_text: str) -> Optional[str]:
    """Instagram havolasidan post/reel shortcode ini ajratib oladi"""
    match = INSTAGRAM_REGEX.search(url_or_text)
    if match:
        return match.group(1)
    return None


def detect_supported_url(text: str) -> Optional[Tuple[str, str]]:
    """
    Matn ichidan Instagram havolasini aniqlaydi.
    """
    ig_match = INSTAGRAM_REGEX.search(text)
    if ig_match:
        code = ig_match.group(1)
        if "/p/" in text:
            return "instagram", f"https://www.instagram.com/p/{code}/"
        return "instagram", f"https://www.instagram.com/reel/{code}/"

    ig_stories_match = INSTAGRAM_STORIES_REGEX.search(text)
    if ig_stories_match:
        return "instagram", ig_stories_match.group(0)

    return None


def extract_instagram_url(text: str) -> Optional[str]:
    """Eski muvofiqlik uchun"""
    res = detect_supported_url(text)
    if res and res[0] == "instagram":
        return res[1]
    return None


def _download_stream_file(
    url: str,
    dest_path: Path,
    progress_callback: Optional[Callable[[int, str], None]] = None
) -> bool:
    """To'g'ridan-to'g'ri CDN havolasidan tezkor oqimli yuklab olish"""
    try:
        headers = {
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36',
            'Accept': '*/*',
            'Accept-Language': 'en-US,en;q=0.9',
            'Referer': 'https://www.instagram.com/'
        }

        with requests.get(url, stream=True, timeout=30, headers=headers) as r:
            r.raise_for_status()
            total_size = int(r.headers.get('content-length', 0))
            downloaded = 0
            start_time = time.time()
            with open(dest_path, 'wb') as f:
                for chunk in r.iter_content(chunk_size=128 * 1024):
                    if chunk:
                        f.write(chunk)
                        downloaded += len(chunk)
                        if progress_callback and total_size > 0:
                            percent = min(99, max(1, int(downloaded / total_size * 100)))
                            elapsed = time.time() - start_time
                            speed_mb = (downloaded / (1024 * 1024) / elapsed) if elapsed > 0 else 0.0
                            progress_callback(percent, f"{speed_mb:.1f} MB/s")
        return dest_path.exists() and dest_path.stat().st_size > 0
    except Exception as e:
        logger.error(f"Faylni oqimli yuklab olishda xatolik: {e}")
        return False


def _download_instagram_instaloader(
    shortcode: str,
    output_id: str,
    progress_callback: Optional[Callable[[int, str], None]] = None
) -> Optional[Dict[str, Any]]:
    """Instaloader orqali Instagram Reels/Post/Karuselni yuklab olish (Server IP bloklariga juda chidamli)"""
    try:
        loader = instaloader.Instaloader(
            download_pictures=False,
            download_videos=False,
            download_comments=False,
            save_metadata=False,
            quiet=True
        )
        post = instaloader.Post.from_shortcode(loader.context, shortcode)
        if not post:
            return None

        items: List[Dict[str, Any]] = []
        raw_caption = post.caption or ''
        clean_title = ' '.join(raw_caption.split()[:12]) if raw_caption else "Instagram Video"
        if len(clean_title) > 85:
            clean_title = clean_title[:82] + "..."
        uploader = post.owner_username or "Instagram"

        if post.typename == 'GraphSidecar':
            # Karusel post (bir nechta rasm/video)
            for idx, node in enumerate(post.get_sidecar_nodes(), 1):
                if node.is_video:
                    dest = DOWNLOADS_DIR / f"{output_id}_{idx:02d}.mp4"
                    success = _download_stream_file(node.video_url, dest, progress_callback)
                    if success:
                        items.append({'file_path': str(dest), 'is_video': True, 'ext': '.mp4'})
                else:
                    dest = DOWNLOADS_DIR / f"{output_id}_{idx:02d}.jpg"
                    success = _download_stream_file(node.display_url, dest, progress_callback)
                    if success:
                        items.append({'file_path': str(dest), 'is_video': False, 'ext': '.jpg'})
        elif post.is_video:
            # Video yoki Reel
            dest = DOWNLOADS_DIR / f"{output_id}_01.mp4"
            success = _download_stream_file(post.video_url, dest, progress_callback)
            if success:
                items.append({'file_path': str(dest), 'is_video': True, 'ext': '.mp4'})
        else:
            # Rasm
            dest = DOWNLOADS_DIR / f"{output_id}_01.jpg"
            success = _download_stream_file(post.url, dest, progress_callback)
            if success:
                items.append({'file_path': str(dest), 'is_video': False, 'ext': '.jpg'})

        if not items:
            return None

        duration = 0.0
        if items[0]['is_video']:
            duration = get_video_duration(items[0]['file_path'])

        return {
            'items': items,
            'title': clean_title,
            'uploader': uploader,
            'duration': float(duration),
            'platform': 'instagram',
            'source_url': f"https://www.instagram.com/reel/{shortcode}/",
            'music_info': {
                'title': clean_title,
                'artist': uploader,
                'play_url': ''
            }
        }
    except Exception as e:
        logger.warning(f"Instaloader bilan yuklab olishda xatolik ({shortcode}): {e}")
        return None


def _download_instagram_ytdlp(
    url: str,
    output_id: str,
    progress_callback: Optional[Callable[[int, str], None]] = None
) -> Optional[Dict[str, Any]]:
    """yt-dlp orqali Instagram yuklash zaxira funksiyasi"""
    out_template = str(DOWNLOADS_DIR / f"{output_id}_%(autonumber)02d.%(ext)s")

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
        'format': 'best/bestvideo+bestaudio',
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
            'Referer': 'https://www.instagram.com/'
        }
    }

    if COOKIES_FILE:
        ydl_opts['cookiefile'] = COOKIES_FILE

    try:
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(url, download=True)
            if not info:
                return None

            raw_title = info.get('title') or 'Instagram Video'
            title = raw_title.strip()
            if len(title) > 90:
                title = title[:87] + "..."

            duration = info.get('duration') or 0.0
            uploader = info.get('uploader') or info.get('channel') or 'Instagram'

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
                'platform': 'instagram',
                'source_url': url,
                'music_info': {
                    'title': title,
                    'artist': uploader,
                    'play_url': ''
                }
            }
    except Exception as e:
        logger.error(f"Instagram yuklab olishda xatolik ({url}): {e}")
        return None


def _download_media_sync(
    url: str,
    output_id: str,
    platform: str,
    progress_callback: Optional[Callable[[int, str], None]] = None
) -> Optional[Dict[str, Any]]:
    """Sinxron yuklash funksiyasi (Instagram uchun Instaloader + yt-dlp)"""
    shortcode = extract_instagram_shortcode(url)
    if shortcode:
        logger.info(f"Instagram: Instaloader orqali yuklanmoqda ({shortcode})...")
        res = _download_instagram_instaloader(shortcode, output_id, progress_callback)
        if res and res.get('items'):
            return res

    logger.info(f"Instagram: yt-dlp orqali urinib ko'rilmoqda ({url})...")
    res_yt = _download_instagram_ytdlp(url, output_id, progress_callback)
    if res_yt and res_yt.get('items'):
        return res_yt

    return None


async def download_media(
    url: str,
    platform: str = "instagram",
    progress_callback: Optional[Callable[[int, str], None]] = None
) -> Optional[Dict[str, Any]]:
    """Instagram media yuklab olish (Asinxron)"""
    output_id = f"ig_{uuid.uuid4().hex[:10]}"
    return await asyncio.to_thread(_download_media_sync, url, output_id, platform, progress_callback)


async def download_instagram_media(url: str) -> Optional[Dict[str, Any]]:
    """Eski muvofiqlik uchun"""
    return await download_media(url, "instagram")
