import asyncio
import logging
import os
import re
from pathlib import Path
from typing import Optional, Dict, Any
import requests
import yt_dlp
from config import TEMP_DIR, FFMPEG_PATH
from video_processor import extract_audio_mp3

logger = logging.getLogger(__name__)


def _clean_track_title(raw_text: str) -> str:
    """Sarlavhadan keraksiz belgilarni, teglarni va so'zlarni tozalash"""
    if not raw_text:
        return ""
    # Emojilar va ortiqcha belgilarni tozalash
    cleaned = re.sub(r'[^\w\s\-\.\,\(\)\'\"]', '', raw_text, flags=re.UNICODE)
    # Hashtaglar va @mentions
    cleaned = re.sub(r'[@#][\w_]+', '', cleaned)
    # Keraksiz so'zlar
    unwanted_patterns = [
        r'\b(?:official\s+video|official\s+audio|lyrics|lyric\s+video|music\s+video|hd|4k|remix|clip|video\s+by|reels\s+by|shorts)\b',
        r'\[.*?\]',
        r'\(.*?official.*?\)',
        r'\(.*?lyrics.*?\)',
        r'\(.*?video.*?\)',
    ]
    for pattern in unwanted_patterns:
        cleaned = re.sub(pattern, '', cleaned, flags=re.IGNORECASE)
    
    # Ortiqcha bo'shliqlarni olib tashlash
    cleaned = re.sub(r'\s+', ' ', cleaned).strip()
    return cleaned


def _recognize_music_audd(audio_path: str) -> Optional[Dict[str, str]]:
    """Musiqani AudD orqali aniqlash (Shazam analogi)"""
    try:
        data = {
            'api_token': 'test',
            'return': 'apple_music,spotify',
        }
        with open(audio_path, 'rb') as f:
            files = {'file': f}
            response = requests.post('https://api.audd.io/', data=data, files=files, timeout=15)
            if response.status_code == 200:
                res = response.json()
                if res.get('status') == 'success' and res.get('result'):
                    r = res['result']
                    title = r.get('title')
                    artist = r.get('artist')
                    if title and artist:
                        return {'title': str(title).strip(), 'artist': str(artist).strip()}
    except Exception as e:
        logger.warning(f"AudD orqali aniqlashda xatolik: {e}")
    return None


def _download_full_song_sync(search_query: str, output_template: str) -> Optional[Dict[str, Any]]:
    """YouTube orqali to'liq musiqani (Full MP3) qidirish va yuklab olish"""
    ydl_opts = {
        'format': 'bestaudio/best',
        'outtmpl': output_template,
        'postprocessors': [{
            'key': 'FFmpegExtractAudio',
            'preferredcodec': 'mp3',
            'preferredquality': '192',
        }],
        'ffmpeg_location': FFMPEG_PATH,
        'quiet': True,
        'no_warnings': True,
        'noplaylist': True,
        'socket_timeout': 30,
        'http_headers': {
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36'
        }
    }

    try:
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            # 3 ta natijani qidirish va eng mosini tanlash
            search_url = f"ytsearch3:{search_query}"
            info = ydl.extract_info(search_url, download=False)
            if not info or not info.get('entries'):
                return None

            # Eng mos yozuvni topish (kamida 30s, ko'pi bilan 10 min)
            selected_entry = None
            for entry in info['entries']:
                if not entry:
                    continue
                dur = entry.get('duration') or 0
                if 30 <= dur <= 600:
                    selected_entry = entry
                    break
            
            if not selected_entry:
                selected_entry = info['entries'][0]

            # Tanlangan qo'shiqni yuklab olish
            video_url = selected_entry.get('webpage_url') or selected_entry.get('url')
            if not video_url:
                return None

            ydl.download([video_url])

            expected_mp3 = output_template.replace('%(ext)s', 'mp3')
            if not os.path.exists(expected_mp3):
                p = Path(output_template).parent
                stem = Path(output_template).stem.replace('%(ext)s', '')
                matching = list(p.glob(f"{stem}*.mp3"))
                if matching:
                    expected_mp3 = str(matching[0])
                else:
                    return None

            song_title = selected_entry.get('title', search_query)
            artist_name = selected_entry.get('uploader') or selected_entry.get('channel') or ''
            
            # Agar sarlavhada "Artist - Title" bo'lsa, uni ajratish
            if " - " in song_title:
                parts = song_title.split(" - ", 1)
                artist_name = parts[0].strip()
                song_title = parts[1].strip()

            return {
                'file_path': expected_mp3,
                'title': _clean_track_title(song_title),
                'artist': _clean_track_title(artist_name),
                'duration': selected_entry.get('duration', 0)
            }
    except Exception as e:
        logger.error(f"To'liq musiqani yuklab olishda xatolik ({search_query}): {e}")
        return None


async def get_full_music_for_video(video_path: str, post_title: str, session_id: str, author_hint: str = "") -> Optional[Dict[str, Any]]:
    """
    Videodagi musiqani aniqlab, uning TO'LIQ (full HQ MP3) variantini topib yuklab beradi.
    
    Bosqichlar:
    1. Videodan audio (MP3) ajratiladi.
    2. Musiqa Shazam/AudD orqali aniqlanadi.
    3. Agar aniqlansa: to'liq qo'shiq YouTube dan HQ yuklanadi.
    4. Agar aniqlanmasa: Post sarlavhasi tozalanib, to'liq musiqasi qidiriladi.
    5. Agar to'liq variant topilmasa: Videoning o'zidagi toza audio MP3 qilib beriladi.
    """
    clip_mp3 = str(TEMP_DIR / f"{session_id}_clip.mp3")
    
    # 1. Videodan audio ajratib olish
    if not os.path.exists(clip_mp3):
        success = await extract_audio_mp3(video_path, clip_mp3, title=post_title[:40], artist=author_hint)
        if not success:
            return None

    # 2. AudD (Shazam) orqali musiqani aniqlash
    track_info = await asyncio.to_thread(_recognize_music_audd, clip_mp3)

    search_query = None
    clean_title = ""
    clean_artist = ""

    if track_info:
        clean_title = track_info['title']
        clean_artist = track_info['artist']
        search_query = f"{clean_artist} - {clean_title} audio"
    else:
        # Post sarlavhasidan tozalangan qidiruv so'zini hosil qilish
        cleaned = _clean_track_title(post_title)
        if len(cleaned) >= 3 and cleaned.lower() not in ("video", "instagram post", "youtube shorts", "reels"):
            search_query = f"{cleaned} audio"
            clean_title = cleaned
            clean_artist = author_hint or "Musiqa"
        elif author_hint:
            search_query = f"{author_hint} audio"
            clean_title = post_title
            clean_artist = author_hint

    # 3. Agar qidiruv so'zi bo'lsa, YouTube orqali to'liq musiqani yuklash
    if search_query:
        full_template = str(TEMP_DIR / f"{session_id}_full.%(ext)s")
        full_res = await asyncio.to_thread(_download_full_song_sync, search_query, full_template)
        if full_res and os.path.exists(full_res['file_path']):
            return {
                'file_path': full_res['file_path'],
                'title': track_info['title'] if track_info else full_res['title'],
                'artist': track_info['artist'] if track_info else full_res['artist'],
                'is_full': True,
                'duration': full_res['duration']
            }

    # 4. Fallback: videodan ajratilgan asl audio (MP3)
    return {
        'file_path': clip_mp3,
        'title': clean_title or _clean_track_title(post_title) or "Musiqa",
        'artist': clean_artist or author_hint or "Original Audio",
        'is_full': False,
        'duration': 0
    }
