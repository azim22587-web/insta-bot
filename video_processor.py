import asyncio
import logging
import os
import re
import subprocess
import time
from pathlib import Path
from typing import Tuple, Optional, Callable, Awaitable
from config import FFMPEG_PATH, TEMP_DIR

logger = logging.getLogger(__name__)


def make_progress_bar(percent: int, length: int = 12) -> str:
    """Chiroyli progress bar generatsiya qilish: [██████░░░░░░]"""
    percent = max(0, min(100, percent))
    filled = int(length * percent / 100)
    empty = length - filled
    return "█" * filled + "░" * empty


def get_video_duration(video_path: str) -> float:
    """Videoning umumiy davomiyligini (sekundlarda) aniqlash"""
    try:
        cmd = [FFMPEG_PATH, "-i", str(video_path)]
        res = subprocess.run(cmd, stderr=subprocess.PIPE, stdout=subprocess.DEVNULL, text=True, errors='ignore')
        match = re.search(r"Duration:\s*(\d+):(\d+):(\d+\.?\d*)", res.stderr)
        if match:
            hours = float(match.group(1))
            minutes = float(match.group(2))
            seconds = float(match.group(3))
            return hours * 3600 + minutes * 60 + seconds
    except Exception as e:
        logger.warning(f"Duration aniqlashda xatolik: {e}")
    return 0.0


def get_file_size_mb(path: str) -> float:
    """Fayl hajmini MB da olish"""
    try:
        return os.path.getsize(path) / (1024 * 1024)
    except Exception:
        return 0.0


async def run_ffmpeg_with_progress(
    cmd: list,
    duration: float = 0.0,
    on_progress: Optional[Callable[[int, str], Awaitable[None]]] = None
) -> bool:
    """
    FFmpeg buyrug'ini asinxron ishga tushirish va har 2 soniyada foizini hisoblab borish
    """
    cmd_with_progress = cmd + ["-progress", "pipe:1"]
    try:
        process = await asyncio.create_subprocess_exec(
            *cmd_with_progress,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE
        )

        last_update = 0.0
        speed_str = "1.0x"

        while True:
            line = await process.stdout.readline()
            if not line:
                break
            text = line.decode(errors='ignore').strip()

            if text.startswith("speed="):
                speed_str = text.split("=")[1].strip()
            elif text.startswith("out_time_us="):
                if duration > 0 and on_progress:
                    try:
                        val = int(text.split("=")[1].strip())
                        curr_sec = val / 1_000_000.0
                        percent = min(99, max(1, int((curr_sec / duration) * 100)))
                        now = time.time()
                        if now - last_update >= 2.0:
                            last_update = now
                            await on_progress(percent, speed_str)
                    except Exception:
                        pass

        stdout, stderr = await process.communicate()
        if process.returncode != 0:
            logger.error(f"FFmpeg xatosi: {stderr.decode(errors='ignore')}")
            return False

        if on_progress and duration > 0:
            try:
                await on_progress(100, speed_str)
            except Exception:
                pass

        return True
    except Exception as e:
        logger.error(f"FFmpeg ishga tushirishda xatolik: {e}")
        return False


async def convert_to_square_blur(
    input_path: str,
    output_path: str,
    duration: float = 0.0,
    on_progress: Optional[Callable[[int, str], Awaitable[None]]] = None,
    size: int = 720
) -> bool:
    """
    Videoni 1:1 kvadrat formatga ultra-tez (optimized blur) o'tkazish.
    Orqa fon past pikselli blur orqali hisoblangani uchun 10-20 barobar tez ishlaydi!
    """
    if duration <= 0:
        duration = get_video_duration(input_path)

    cmd = [
        FFMPEG_PATH, "-y",
        "-i", str(input_path),
        "-filter_complex",
        f"[0:v]scale=240:240:force_original_aspect_ratio=increase,crop=240:240,boxblur=10:2,scale={size}:{size}:flags=fast_bilinear[bg];"
        f"[0:v]scale={size}:{size}:force_original_aspect_ratio=decrease[fg];"
        f"[bg][fg]overlay=(W-w)/2:(H-h)/2[v]",
        "-map", "[v]",
        "-map", "0:a?",
        "-c:v", "libx264",
        "-preset", "ultrafast",
        "-threads", "0",
        "-crf", "23",
        "-c:a", "aac",
        "-b:a", "128k",
        "-pix_fmt", "yuv420p",
        "-movflags", "+faststart",
        str(output_path)
    ]
    
    success = await run_ffmpeg_with_progress(cmd, duration=duration, on_progress=on_progress)

    # Agar fayl 49MB dan oshsa, Telegram limitiga moslab siqish
    if success and get_file_size_mb(output_path) > 49.0:
        compressed_path = output_path.replace(".mp4", "_comp.mp4")
        comp_success = await compress_video_to_size(output_path, compressed_path, target_mb=45.0)
        if comp_success and os.path.exists(compressed_path):
            os.replace(compressed_path, output_path)

    return success


async def convert_to_square_crop(
    input_path: str,
    output_path: str,
    duration: float = 0.0,
    on_progress: Optional[Callable[[int, str], Awaitable[None]]] = None,
    size: int = 720
) -> bool:
    """
    Videoning markaziy qismini 1:1 kvadrat qilib ultra-tez qirqib olish (Center Crop).
    """
    if duration <= 0:
        duration = get_video_duration(input_path)

    cmd = [
        FFMPEG_PATH, "-y",
        "-i", str(input_path),
        "-filter_complex",
        f"[0:v]crop=min(iw\\,ih):min(iw\\,ih),scale={size}:{size}[v]",
        "-map", "[v]",
        "-map", "0:a?",
        "-c:v", "libx264",
        "-preset", "ultrafast",
        "-threads", "0",
        "-crf", "23",
        "-c:a", "aac",
        "-b:a", "128k",
        "-pix_fmt", "yuv420p",
        "-movflags", "+faststart",
        str(output_path)
    ]
    
    success = await run_ffmpeg_with_progress(cmd, duration=duration, on_progress=on_progress)

    if success and get_file_size_mb(output_path) > 49.0:
        compressed_path = output_path.replace(".mp4", "_comp.mp4")
        comp_success = await compress_video_to_size(output_path, compressed_path, target_mb=45.0)
        if comp_success and os.path.exists(compressed_path):
            os.replace(compressed_path, output_path)

    return success


async def compress_video_to_size(input_path: str, output_path: str, target_mb: float = 45.0) -> bool:
    """Videoni Telegram 50MB limitidan oshib ketmasligi uchun tezkor siqish"""
    cmd = [
        FFMPEG_PATH, "-y",
        "-i", str(input_path),
        "-vf", "scale='min(720,iw)':-2",
        "-c:v", "libx264",
        "-preset", "ultrafast",
        "-threads", "0",
        "-crf", "28",
        "-c:a", "aac",
        "-b:a", "96k",
        "-pix_fmt", "yuv420p",
        "-movflags", "+faststart",
        str(output_path)
    ]
    try:
        process = await asyncio.create_subprocess_exec(*cmd, stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.DEVNULL)
        await process.communicate()
        return process.returncode == 0
    except Exception:
        return False


async def extract_audio_mp3(video_path: str, output_mp3_path: str, title: Optional[str] = None, artist: Optional[str] = None) -> bool:
    """Videodan MP3 musiqani sifatli ajratib olish va metadata biriktirish"""
    cmd = [
        FFMPEG_PATH, "-y",
        "-i", str(video_path),
        "-vn",
        "-c:a", "libmp3lame",
        "-b:a", "192k"
    ]
    if title:
        cmd.extend(["-metadata", f"title={title}"])
    if artist:
        cmd.extend(["-metadata", f"artist={artist}"])

    cmd.append(str(output_mp3_path))
    try:
        process = await asyncio.create_subprocess_exec(*cmd, stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.DEVNULL)
        await process.communicate()
        return process.returncode == 0
    except Exception:
        return False


async def generate_thumbnail(video_path: str, thumb_path: str, time_sec: float = 1.0) -> bool:
    """Videodan birinchi kadr rasm (thumbnail) olish"""
    cmd = [
        FFMPEG_PATH, "-y",
        "-ss", str(time_sec),
        "-i", str(video_path),
        "-vframes", "1",
        "-q:v", "2",
        str(thumb_path)
    ]
    try:
        process = await asyncio.create_subprocess_exec(*cmd, stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.DEVNULL)
        await process.communicate()
        return process.returncode == 0
    except Exception:
        return False
