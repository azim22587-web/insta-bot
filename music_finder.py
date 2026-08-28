import asyncio
import logging
import os
import re
from pathlib import Path
from typing import Optional, Dict, Any

from config import TEMP_DIR
from video_processor import extract_audio_mp3, get_video_duration

logger = logging.getLogger(__name__)


def _clean_track_title(raw_text: str) -> str:
    """Sarlavhadan ortiqcha belgilarni tozalash"""
    if not raw_text:
        return "Asl Audio"
    # Hashtaglar va @mentions olib tashlash
    cleaned = re.sub(r'[@#][\w_]+', '', raw_text)
    # Linklarni olib tashlash
    cleaned = re.sub(r'https?:\/\/\S+', '', cleaned)
    # Ortiqcha belgilarni tozalash
    cleaned = re.sub(r'[^\w\s\-\.\,\(\)\'\"]', ' ', cleaned, flags=re.UNICODE)
    cleaned = re.sub(r'\s+', ' ', cleaned).strip()
    
    if not cleaned or len(cleaned) < 2:
        return "Asl Audio"
    
    words = cleaned.split()
    if len(words) > 8:
        cleaned = " ".join(words[:8]) + "..."
    return cleaned


async def get_full_music_for_video(
    video_path: str,
    post_title: str,
    session_id: str,
    author_hint: str = ""
) -> Optional[Dict[str, Any]]:
    """
    Videodagi ASL musiqani (Original HQ Audio) eng yuqori sifatda ajratib beradi.
    Noto'g'ri yoki begona musiqalar tushib qolmasligi uchun to'g'ridan-to'g'ri videoning o'zidan olinadi.
    """
    clip_mp3 = str(TEMP_DIR / f"{session_id}_audio.mp3")
    
    title = _clean_track_title(post_title)
    artist = author_hint if author_hint and author_hint.lower() not in ("instagram", "youtube", "tiktok") else "Original Sound"

    # Agar allaqachon ajratilgan bo'lsa
    if not os.path.exists(clip_mp3):
        success = await extract_audio_mp3(
            video_path=video_path,
            output_mp3_path=clip_mp3,
            title=title,
            artist=artist
        )
        if not success or not os.path.exists(clip_mp3):
            logger.error(f"Videodan audio ajratib bo'lmadi: {video_path}")
            return None

    duration = int(get_video_duration(clip_mp3))

    return {
        'file_path': clip_mp3,
        'title': title,
        'artist': artist,
        'is_full': False,
        'duration': duration
    }
