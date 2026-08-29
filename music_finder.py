import asyncio
import logging
import os
import re
from pathlib import Path
from typing import Optional, Dict, Any
import requests
import yt_dlp

from config import TEMP_DIR, FFMPEG_PATH, COOKIES_FILE
from video_processor import extract_audio_mp3, get_video_duration

logger = logging.getLogger(__name__)


def _clean_track_title(raw_text: str) -> str:
    """Sarlavhadan ortiqcha belgilarni tozalab, qo'shiq qidiruv so'zini hosil qilish"""
    if not raw_text:
        return ""
    # Hashtaglar va @mentions olib tashlash
    cleaned = re.sub(r'[@#][\w_]+', '', raw_text)
    # Linklarni olib tashlash
    cleaned = re.sub(r'https?:\/\/\S+', '', cleaned)
    # Ortiqcha so'zlarni tozalash
    cleaned = re.sub(r'(?i)\b(reels?|shorts?|tiktok|instagram|video|original sound|original audio|asl ovoz|rasmiy|official|video klip|clip)\b', ' ', cleaned)
    # Ortiqcha belgilarni tozalash
    cleaned = re.sub(r'[^\w\s\-\.\,\(\)\'\"]', ' ', cleaned, flags=re.UNICODE)
    cleaned = re.sub(r'\s+', ' ', cleaned).strip()
    
    if not cleaned or len(cleaned) < 2:
        return ""
    
    words = cleaned.split()
    if len(words) > 7:
        cleaned = " ".join(words[:7])
    return cleaned


def _search_deezer(query: str) -> Optional[Dict[str, Any]]:
    """Deezer API orqali qo'shiqning aniq nomi va ijrochisini topish (Bepul va tezkor)"""
    try:
        url = "https://api.deezer.com/search"
        res = requests.get(url, params={"q": query, "limit": 1}, timeout=6)
        if res.status_code == 200:
            data = res.json()
            if data.get("data") and len(data["data"]) > 0:
                track = data["data"][0]
                return {
                    "title": track.get("title", ""),
                    "artist": track.get("artist", {}).get("name", ""),
                    "duration": int(track.get("duration", 0)),
                    "preview": track.get("preview", "")
                }
    except Exception as e:
        logger.debug(f"Deezer qidiruv xatosi: {e}")
    return None


def _download_full_track_ytdlp(search_term: str, output_path: str) -> Optional[Dict[str, Any]]:
    """yt-dlp orqali to'liq musiqani YouTube dan yuklab olish"""
    ydl_opts = {
        'format': 'bestaudio[ext=m4a]/bestaudio/best',
        'outtmpl': str(Path(output_path).with_suffix('')) + '.%(ext)s',
        'postprocessors': [{
            'key': 'FFmpegExtractAudio',
            'preferredcodec': 'mp3',
            'preferredquality': '192',
        }],
        'ffmpeg_location': FFMPEG_PATH,
        'quiet': True,
        'no_warnings': True,
        'noplaylist': True,
        'socket_timeout': 25,
        'default_search': 'ytsearch1',
        'extractor_args': {
            'youtube': {
                'player_client': ['android', 'ios', 'tv_embedded', 'mweb'],
                'player_skip': ['webpage', 'configs'],
            }
        },
        'http_headers': {
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36',
            'Accept-Language': 'en-US,en;q=0.9',
        }
    }
    if COOKIES_FILE:
        ydl_opts['cookiefile'] = COOKIES_FILE

    try:
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            search_query = f"ytsearch1:{search_term}"
            info = ydl.extract_info(search_query, download=True)
            if not info:
                return None
            
            entries = info.get('entries') if 'entries' in info else [info]
            if not entries or not entries[0]:
                return None
            
            first_entry = entries[0]
            track_title = first_entry.get('title') or search_term
            track_artist = first_entry.get('uploader') or first_entry.get('channel') or 'Musiqa'
            duration = int(first_entry.get('duration') or 0)

            # Agar yuklangan mp3 mavjud bo'lsa
            expected_mp3 = Path(output_path)
            if expected_mp3.exists() and expected_mp3.stat().st_size > 50000:
                return {
                    'file_path': str(expected_mp3),
                    'title': track_title,
                    'artist': track_artist,
                    'duration': duration,
                    'is_full': True
                }
    except Exception as e:
        logger.warning(f"yt-dlp musiqa yuklashda xatolik ({search_term}): {e}")
    return None


def _download_stream_music(url: str, dest_path: str) -> bool:
    """To'g'ridan-to'g'ri CDN audio havolasini oqimli yuklab olish"""
    try:
        headers = {
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36',
            'Accept': '*/*',
        }
        with requests.get(url, stream=True, timeout=25, headers=headers) as r:
            r.raise_for_status()
            with open(dest_path, 'wb') as f:
                for chunk in r.iter_content(chunk_size=64 * 1024):
                    if chunk:
                        f.write(chunk)
        return os.path.exists(dest_path) and os.path.getsize(dest_path) > 30000
    except Exception as e:
        logger.warning(f"Direct stream audio xatosi: {e}")
        return False


def _find_and_download_full_music_sync(
    video_path: str,
    post_title: str,
    session_id: str,
    author_hint: str = "",
    direct_music_url: str = "",
    music_title_hint: str = "",
    music_artist_hint: str = ""
) -> Dict[str, Any]:
    """
    To'liq musiqani topish va yuklab olish (Sinxron ishchi funksiya)
    1. To'g'ridan-to'g'ri musiqa havolasi (TikTok CDN) mavjud bo'lsa
    2. Deezer + YouTube orqali to'liq MP3 trekni topib yuklash
    3. Agar to'liq musiqa topilmasa, videodan toza HQ audioni ajratish (fallback)
    """
    full_mp3_path = str(TEMP_DIR / f"{session_id}_full.mp3")
    clip_mp3_path = str(TEMP_DIR / f"{session_id}_audio.mp3")

    # 1. To'g'ridan-to'g'ri musiqa havolasi berilgan bo'lsa (masalan TikTok rasmiy audiosi)
    if direct_music_url and direct_music_url.startswith("http"):
        if _download_stream_music(direct_music_url, full_mp3_path):
            dur = int(get_video_duration(full_mp3_path))
            title = music_title_hint or _clean_track_title(post_title) or "Asl Musiqa"
            artist = music_artist_hint or author_hint or "Original Sound"
            return {
                'file_path': full_mp3_path,
                'title': title,
                'artist': artist,
                'is_full': True,
                'duration': dur
            }

    # 2. Qidiruv so'zini tayyorlash
    search_query = ""
    if music_title_hint:
        search_query = f"{music_artist_hint} {music_title_hint}".strip()
    elif post_title:
        search_query = _clean_track_title(post_title)
        if author_hint and author_hint.lower() not in ("instagram", "youtube", "tiktok", "reels", "shorts"):
            if author_hint.lower() not in search_query.lower():
                search_query = f"{author_hint} {search_query}".strip()

    # 3. Deezer orqali aniq qo'shiq nomini qidirish
    deezer_match = None
    if search_query:
        deezer_match = _search_deezer(search_query)

    # 4. To'liq MP3 ni YouTube orqali yuklash
    if deezer_match and deezer_match.get("title") and deezer_match.get("artist"):
        target_term = f"{deezer_match['artist']} - {deezer_match['title']} audio"
        logger.info(f"Deezer orqali topildi: {target_term}. Yuklab olinmoqda...")
        full_res = _download_full_track_ytdlp(target_term, full_mp3_path)
        if full_res and full_res.get('duration', 0) >= 40:
            full_res['title'] = deezer_match['title']
            full_res['artist'] = deezer_match['artist']
            return full_res

    # 5. Agar Deezer topa olmasa, tozalangan sarlavha bilan to'g'ridan-to'g'ri YouTube qidiruv
    if search_query and len(search_query) >= 3:
        target_term = f"{search_query} audio"
        logger.info(f"YouTube orqali to'liq musiqa qidirilmoqda: {target_term}")
        full_res = _download_full_track_ytdlp(target_term, full_mp3_path)
        if full_res and full_res.get('duration', 0) >= 40:
            return full_res

    # 6. Agar to'liq qo'shiq topilmasa, videoning o'zidan HQ audioni ajratish (ishonchli fallback)
    logger.info(f"To'liq qo'shiq topilmadi, videodan audio ajratilmoqda: {video_path}")
    title = music_title_hint or _clean_track_title(post_title) or "Asl Ovoz"
    artist = music_artist_hint or (author_hint if author_hint and author_hint.lower() not in ("instagram", "youtube", "tiktok") else "Original Sound")

    # ffmpeg orqali videodan audio ajratish
    import subprocess
    cmd = [
        FFMPEG_PATH, "-y",
        "-i", str(video_path),
        "-vn",
        "-c:a", "libmp3lame",
        "-b:a", "192k",
        "-metadata", f"title={title}",
        "-metadata", f"artist={artist}",
        str(clip_mp3_path)
    ]
    try:
        subprocess.run(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=30)
    except Exception as e:
        logger.error(f"Fallback audio ajratishda xatolik: {e}")

    dur = int(get_video_duration(clip_mp3_path)) if os.path.exists(clip_mp3_path) else 0

    return {
        'file_path': clip_mp3_path,
        'title': title,
        'artist': artist,
        'is_full': False,
        'duration': dur
    }


async def get_full_music_for_video(
    video_path: str,
    post_title: str,
    session_id: str,
    author_hint: str = "",
    direct_music_url: str = "",
    music_title_hint: str = "",
    music_artist_hint: str = ""
) -> Optional[Dict[str, Any]]:
    """
    To'liq musiqani topish va yuklab berish (Asinxron)
    """
    res = await asyncio.to_thread(
        _find_and_download_full_music_sync,
        video_path,
        post_title,
        session_id,
        author_hint,
        direct_music_url,
        music_title_hint,
        music_artist_hint
    )
    if res and os.path.exists(res.get('file_path', '')):
        return res
    return None

