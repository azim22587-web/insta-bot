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


def extract_instagram_shortcode(url_or_text: str) -> Optional[str]:
    """Instagram havolasidan post/reel shortcode ini ajratib oladi"""
    match = INSTAGRAM_REGEX.search(url_or_text)
    if match:
        return match.group(1)
    return None


def detect_supported_url(text: str) -> Optional[Tuple[str, str]]:
    """
    Matn ichidan qo'llab-quvvatlanadigan havolani va uning turini aniqlaydi.
    """
    # 1. Instagram
    ig_match = INSTAGRAM_REGEX.search(text)
    if ig_match:
        code = ig_match.group(1)
        # Agar URL ichida reel yoki p bo'lsa
        if "/p/" in text:
            return "instagram", f"https://www.instagram.com/p/{code}/"
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
        }
        if 'instagram' in url or 'cdninstagram' in url or 'fbcdn' in url:
            headers['Referer'] = 'https://www.instagram.com/'
        elif 'tiktok' in url or 'tiktokcdn' in url or 'tikwm' in url:
            headers['Referer'] = 'https://www.tiktok.com/'

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
    """Instaloader orqali Instagram Reels/Post/Karuselni yuklab olish (Server IP bloklariga chidamli)"""
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

        # Davomiylikni aniqlash
        duration = 0.0
        if items[0]['is_video']:
            duration = get_video_duration(items[0]['file_path'])

        return {
            'items': items,
            'title': clean_title,
            'uploader': uploader,
            'duration': float(duration),
            'platform': 'instagram',
            'source_url': f"https://www.instagram.com/reel/{shortcode}/"
        }
    except Exception as e:
        logger.warning(f"Instaloader bilan yuklab olishda xatolik ({shortcode}): {e}")
        return None


def _download_tiktok_tikwm(
    url: str,
    output_id: str,
    progress_callback: Optional[Callable[[int, str], None]] = None
) -> Optional[Dict[str, Any]]:
    """TikWM API va bir nechta muqobil API lar orqali TikTok yuklash"""
    # 1. TikWM API
    try:
        headers = {
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36',
            'Accept': 'application/json, text/plain, */*',
        }
        res = requests.post(
            'https://www.tikwm.com/api/',
            data={'url': url, 'count': 12, 'cursor': 0, 'web': 1, 'hd': 1},
            headers=headers,
            timeout=15
        )
        if res.status_code == 200:
            data = res.json()
            if data.get('code') == 0 and 'data' in data:
                item_data = data['data']
                raw_title = item_data.get('title') or 'TikTok Video'
                title = raw_title.strip()
                if len(title) > 90:
                    title = title[:87] + "..."
                uploader = item_data.get('author', {}).get('nickname') or item_data.get('author', {}).get('unique_id') or 'TikTok'
                duration = float(item_data.get('duration', 0.0))

                items: List[Dict[str, Any]] = []
                images = item_data.get('images')
                if images and isinstance(images, list):
                    for idx, img_url in enumerate(images, 1):
                        dest = DOWNLOADS_DIR / f"{output_id}_{idx:02d}.jpg"
                        if _download_stream_file(img_url, dest, progress_callback):
                            items.append({'file_path': str(dest), 'is_video': False, 'ext': '.jpg'})
                else:
                    play_url = item_data.get('hdplay') or item_data.get('play') or item_data.get('wmplay')
                    if play_url:
                        if not play_url.startswith('http'):
                            play_url = 'https://www.tikwm.com' + play_url
                        dest = DOWNLOADS_DIR / f"{output_id}_01.mp4"
                        if _download_stream_file(play_url, dest, progress_callback):
                            items.append({'file_path': str(dest), 'is_video': True, 'ext': '.mp4'})

                music_info = item_data.get('music_info', {}) or {}
                music_title = music_info.get('title') or ''
                music_author = music_info.get('author') or ''
                music_play_url = music_info.get('play') or item_data.get('music') or ''
                if music_play_url and not music_play_url.startswith('http'):
                    music_play_url = 'https://www.tikwm.com' + music_play_url

                if items:
                    return {
                        'items': items,
                        'title': title,
                        'uploader': uploader,
                        'duration': duration,
                        'platform': 'tiktok',
                        'source_url': url,
                        'music_info': {
                            'title': music_title,
                            'artist': music_author,
                            'play_url': music_play_url
                        }
                    }
    except Exception as e:
        logger.warning(f"TikWM xatosi: {e}")

    # 2. Muqobil API: TikSave / SSSTik fallback
    try:
        ss_res = requests.get(f"https://api.tiklydown.eu.org/api/download?url={url}", timeout=15)
        if ss_res.status_code == 200:
            sdata = ss_res.json()
            video_url = sdata.get('video', {}).get('noWatermark') or sdata.get('video', {}).get('watermark')
            music_url = sdata.get('music', {}).get('play_url') or ''
            if video_url:
                dest = DOWNLOADS_DIR / f"{output_id}_01.mp4"
                if _download_stream_file(video_url, dest, progress_callback):
                    return {
                        'items': [{'file_path': str(dest), 'is_video': True, 'ext': '.mp4'}],
                        'title': sdata.get('title', 'TikTok Video')[:85],
                        'uploader': sdata.get('author', {}).get('name', 'TikTok'),
                        'duration': 0.0,
                        'platform': 'tiktok',
                        'source_url': url,
                        'music_info': {
                            'title': sdata.get('music', {}).get('title', ''),
                            'artist': sdata.get('music', {}).get('author', ''),
                            'play_url': music_url
                        }
                    }
    except Exception as e:
        logger.warning(f"Tiklydown xatosi: {e}")

    return None


def _download_media_ytdlp(
    url: str,
    output_id: str,
    platform: str,
    progress_callback: Optional[Callable[[int, str], None]] = None
) -> Optional[Dict[str, Any]]:
    """yt-dlp orqali yuklash funksiyasi (Server IP bloklariga chidamli multi-client bilan)"""
    out_template = str(DOWNLOADS_DIR / f"{output_id}_%(autonumber)02d.%(ext)s")

    # YouTube va boshqa platformalar uchun ishonchli format
    format_selector = (
        'best[ext=mp4]/bestvideo[ext=mp4]+bestaudio[ext=m4a]/bestvideo+bestaudio/best'
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

    # YouTube uchun sinab ko'riladigan client kombinatsiyalari
    client_strategies = [
        ['ios', 'android', 'tv'],
        ['android'],
        ['ios'],
        ['tv'],
        ['mweb', 'web'],
    ] if "youtube" in platform else [['default']]

    for clients in client_strategies:
        ydl_opts = {
            'format': format_selector,
            'outtmpl': out_template,
            'quiet': True,
            'no_warnings': True,
            'noplaylist': True,
            'socket_timeout': 30,
            'geo_bypass': True,
            'nocheckcertificate': True,
            'ffmpeg_location': FFMPEG_PATH,
            'progress_hooks': [ydl_hook] if progress_callback else [],
            'http_headers': {
                'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36',
                'Accept-Language': 'en-US,en;q=0.9',
            }
        }

        if "youtube" in platform:
            ydl_opts['extractor_args'] = {
                'youtube': {
                    'player_client': clients,
                    'player_skip': ['webpage', 'configs'],
                }
            }
            ydl_opts['match_filter'] = yt_dlp.utils.match_filter_func("duration <= 900")

        if COOKIES_FILE:
            ydl_opts['cookiefile'] = COOKIES_FILE

        try:
            with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                info = ydl.extract_info(url, download=True)
                if not info:
                    continue

                raw_title = info.get('title') or ('YouTube Video' if 'youtube' in platform else 'Instagram Post')
                title = raw_title.strip()
                if len(title) > 90:
                    title = title[:87] + "..."

                duration = info.get('duration') or 0.0
                uploader = info.get('uploader') or info.get('channel') or info.get('creator') or ''

                # Musiqa metadata
                track_title = info.get('track') or ''
                track_artist = info.get('artist') or ''

                # Yuklangan barcha fayllarni topish
                found_files = list(DOWNLOADS_DIR.glob(f"{output_id}*.*"))

                if not found_files:
                    single_template = str(DOWNLOADS_DIR / f"{output_id}.%(ext)s")
                    ydl_opts['outtmpl'] = single_template
                    with yt_dlp.YoutubeDL(ydl_opts) as ydl_single:
                        info = ydl_single.extract_info(url, download=True)
                        found_files = list(DOWNLOADS_DIR.glob(f"{output_id}.*"))

                if not found_files:
                    continue

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
                    continue

                return {
                    'items': items,
                    'title': title,
                    'uploader': uploader,
                    'duration': float(duration),
                    'platform': platform,
                    'source_url': url,
                    'music_info': {
                        'title': track_title,
                        'artist': track_artist,
                        'play_url': ''
                    }
                }
        except Exception as e:
            logger.warning(f"yt-dlp urinishda xatolik (clients={clients}): {e}")
            continue

    logger.error(f"Media yuklab olish barcha strategiyalar bo'yicha muvaffaqiyatsiz bo'ldi ({platform} - {url})")
    return None


def _download_media_sync(
    url: str,
    output_id: str,
    platform: str,
    progress_callback: Optional[Callable[[int, str], None]] = None
) -> Optional[Dict[str, Any]]:
    """Sinxron yuklash funksiyasi (TikTok uchun TikWM, Instagram uchun multi-engine, YouTube uchun yt-dlp)"""
    if platform == "tiktok":
        # 1-qadam: TikWM API orqali (serverlar uchun ultra-tez va IP blocksiz)
        logger.info(f"TikTok: TikWM orqali yuklanmoqda ({url})...")
        res_tt = _download_tiktok_tikwm(url, output_id, progress_callback)
        if res_tt and res_tt.get('items'):
            return res_tt

        # 2-qadam: yt-dlp orqali urinib ko'rish
        logger.info(f"TikTok: yt-dlp orqali urinib ko'rilmoqda ({url})...")
        res_yt = _download_media_ytdlp(url, output_id, platform, progress_callback)
        if res_yt and res_yt.get('items'):
            return res_yt

        return None

    elif platform == "instagram":
        # 1-qadam: Instaloader orqali urinib ko'rish (Serverlar uchun eng ishonchli)
        shortcode = extract_instagram_shortcode(url)
        if shortcode:
            logger.info(f"Instagram: Instaloader orqali yuklanmoqda ({shortcode})...")
            res = _download_instagram_instaloader(shortcode, output_id, progress_callback)
            if res and res.get('items'):
                return res

        # 2-qadam: yt-dlp orqali urinib ko'rish
        logger.info(f"Instagram: yt-dlp orqali urinib ko'rilmoqda ({url})...")
        res_yt = _download_media_ytdlp(url, output_id, platform, progress_callback)
        if res_yt and res_yt.get('items'):
            return res_yt

        return None
    else:
        # YouTube uchun
        return _download_media_ytdlp(url, output_id, platform, progress_callback)



async def download_media(
    url: str,
    platform: str = "generic",
    progress_callback: Optional[Callable[[int, str], None]] = None
) -> Optional[Dict[str, Any]]:
    """Istalgan platformadan media yuklab olish (Asinxron)"""
    prefix = "yt" if "youtube" in platform else ("tt" if platform == "tiktok" else "ig")
    output_id = f"{prefix}_{uuid.uuid4().hex[:10]}"
    return await asyncio.to_thread(_download_media_sync, url, output_id, platform, progress_callback)


async def download_instagram_media(url: str) -> Optional[Dict[str, Any]]:
    """Eski muvofiqlik uchun"""
    return await download_media(url, "instagram")
