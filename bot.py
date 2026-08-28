import asyncio
import html
import logging
import os
import shutil
import sys
import time
import uuid
from pathlib import Path
from typing import Dict, Any, Optional

# Windows terminal UTF-8 sozlamasi
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="backslashreplace")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8", errors="backslashreplace")

from aiogram import Bot, Dispatcher, F, types
from aiogram.client.session.aiohttp import AiohttpSession
from aiogram.enums import ParseMode
from aiogram.filters import CommandStart, Command
from aiogram.types import (
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    CallbackQuery,
    FSInputFile
)

from config import BOT_TOKEN, DOWNLOADS_DIR, TEMP_DIR
from downloader import detect_supported_url, download_media
from video_processor import (
    convert_to_square_blur,
    convert_to_square_crop,
    extract_audio_mp3,
    generate_thumbnail,
    make_progress_bar,
    get_video_duration
)
from image_processor import convert_image_to_square_blur, convert_image_to_square_crop
from music_finder import get_full_music_for_video

# Loglarni sozlash
logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")
logger = logging.getLogger(__name__)

dp = Dispatcher()

# Sessiyalar xotirasi
SESSIONS: Dict[str, Dict[str, Any]] = {}


class ProgressTracker:
    """Telegram xabarini rate limitga tushmasdan har 2 soniyada yangilab boruvchi sinf"""
    def __init__(self, message: types.Message, title: str):
        self.message = message
        self.title = title
        self.last_update = 0.0
        self.last_percent = -1

    async def on_progress(self, percent: int, speed_str: str = ""):
        now = time.time()
        if (now - self.last_update >= 2.0 and percent != self.last_percent) or percent >= 100:
            self.last_update = now
            self.last_percent = percent
            bar = make_progress_bar(percent)
            speed_text = f"\n⚡ <i>Tezlik: {speed_str}</i>" if speed_str else ""
            text = (
                f"{self.title}\n\n"
                f"📊 <b>Jarayon:</b> <code>[{bar}]</code> <b>{percent}%</b>{speed_text}"
            )
            try:
                await self.message.edit_text(text, parse_mode=ParseMode.HTML)
            except Exception:
                pass


def get_action_keyboard(session_id: str, media_type: str = "video", current_mode: str = "blur") -> InlineKeyboardMarkup:
    """Format va musiqa tanlash tugmalari"""
    buttons = []
    row1 = []
    
    if current_mode != "blur":
        row1.append(InlineKeyboardButton(text="📐 1:1 Blur fon", callback_data=f"mode:blur:{session_id}"))
    if current_mode != "crop":
        row1.append(InlineKeyboardButton(text="✂️ 1:1 Qirqish (Crop)", callback_data=f"mode:crop:{session_id}"))
    if row1:
        buttons.append(row1)

    row2 = []
    if current_mode != "orig":
        orig_text = "🎞 Asl video" if media_type == "video" else "🖼 Asl rasm"
        row2.append(InlineKeyboardButton(text=orig_text, callback_data=f"mode:orig:{session_id}"))
    
    if media_type == "video":
        row2.append(InlineKeyboardButton(text="🎵 Musiqasi (MP3)", callback_data=f"mode:audio:{session_id}"))

    if row2:
        buttons.append(row2)

    return InlineKeyboardMarkup(inline_keyboard=buttons)


@dp.message(CommandStart())
async def cmd_start(message: types.Message):
    """Start buyrug'i"""
    text = (
        "👋 <b>Assalomu alaykum!</b>\n\n"
        "Men <b>Instagram</b>, <b>YouTube (Shorts/Video)</b> va <b>TikTok</b> dan videolarni eng yuqori sifatda yuklab, "
        "ularni <b>1:1 to'rtburchak (kvadrat)</b> shaklga o'tkazuvchi va <b>asl musiqasini (MP3)</b> ajratib beruvchi botman! 🎥📐🎵✨\n\n"
        "🌟 <b>Asosiy Imkoniyatlar:</b>\n"
        "1️⃣ <b>Instagram & YouTube</b> havolalarini yuboring (Reels, Shorts, Video, Post).\n"
        "2️⃣ Video avtomatik tarzda <b>1:1 to'rtburchak (Blur fon)</b> shakliga keltiriladi.\n"
        "3️⃣ <b>🎵 Musiqasi (MP3)</b> — videodagi asl musiqani toza HQ MP3 formatida ajratib beradi.\n"
        "4️⃣ <b>✂️ Qirqish (Crop)</b> yoki <b>🎞 Asl holatda</b> formatlarini ham tanlash mumkin.\n"
        "5️⃣ Istalgan video yoki rasmni to'g'ridan-to'g'ri yuborsangiz ham 1:1 kvadrat qilib beradi.\n\n"
        "🚀 <i>Havolani yuboring yoki video/rasm tashlang!</i>"
    )
    await message.answer(text, parse_mode=ParseMode.HTML)


@dp.message(Command("help"))
async def cmd_help(message: types.Message):
    """Yordam buyrug'i"""
    text = (
        "ℹ️ <b>Yordam va qo'llanma:</b>\n\n"
        "🔹 <b>Qo'llab-quvvatlanadigan platformalar:</b>\n"
        "• 📸 <b>Instagram:</b> Reels, Post, Karusel, Stories\n"
        "• 🔴 <b>YouTube:</b> Shorts, oddiy videolar (youtu.be / watch)\n"
        "• 🎵 <b>TikTok:</b> Barcha video havolalar\n"
        "• 📁 <b>To'g'ridan-to'g'ri media:</b> Botga yuborilgan har qanday video yoki rasm\n\n"
        "📐 <b>Formatlar:</b>\n"
        "• <b>📐 Blur fon:</b> 1:1 kvadrat, videoning asl sifati va nisbati buzilmaydi, chetiga chiroyli xiralashtirilgan fon qo'yiladi.\n"
        "• <b>✂️ Qirqish (Crop):</b> Markazidan 1:1 kvadrat qilib qirqib beradi.\n"
        "• <b>🎞 Asl holatda:</b> O'zgartirishlarsiz asl formatda beradi.\n"
        "• <b>🎵 To'liq MP3:</b> Qo'shiqni to'liq holda topib yuklaydi."
    )
    await message.answer(text, parse_mode=ParseMode.HTML)


@dp.message(F.text)
async def handle_text_message(message: types.Message, bot: Bot):
    """Instagram yoki YouTube havolasini qabul qilish va qayta ishlash"""
    text = message.text.strip()
    detected = detect_supported_url(text)

    if not detected:
        await message.answer(
            "⚠️ Iltimos, to'g'ri <b>Instagram</b>, <b>YouTube</b> yoki <b>TikTok</b> havolasini yuboring.\n\n"
            "Misollar:\n"
            "• <code>https://www.instagram.com/reel/Cxxxxxx/</code>\n"
            "• <code>https://youtube.com/shorts/xxxxxx</code>\n"
            "• <code>https://youtu.be/xxxxxx</code>",
            parse_mode=ParseMode.HTML
        )
        return

    platform, url = detected
    platform_name = "YouTube" if "youtube" in platform else ("TikTok" if platform == "tiktok" else "Instagram")
    status_msg = await message.answer(f"⏳ <b>{platform_name} dan yuklab olinmoqda...</b>", parse_mode=ParseMode.HTML)

    loop = asyncio.get_running_loop()
    dl_tracker = ProgressTracker(status_msg, f"⬇️ <b>{platform_name} dan yuklab olinmoqda...</b>")

    def sync_dl_progress(percent: int, speed: str):
        asyncio.run_coroutine_threadsafe(dl_tracker.on_progress(percent, speed), loop)

    try:
        # 1. Mediani yuklab olish
        info = await download_media(url, platform, progress_callback=sync_dl_progress)
        if not info or not info.get('items'):
            await status_msg.edit_text(f"❌ {platform_name} dan yuklab bo'lmadi. Havolaning to'g'riligini va ochiqligini (public) tekshiring.")
            return

        bot_user = await bot.get_me()
        items = info['items']
        safe_title = html.escape(info.get('title', f'{platform_name} Media'))
        uploader = html.escape(info.get('uploader', platform_name))
        duration = float(info.get('duration', 0.0))

        for idx, item in enumerate(items):
            orig_file = item['file_path']
            is_video = item['is_video']
            session_id = uuid.uuid4().hex[:8]

            if is_video:
                convert_title = f"🔄 <b>Video ({idx+1}/{len(items)}) 1:1 to'rtburchak shaklga keltirilmoqda...</b>"
                tracker = ProgressTracker(status_msg, convert_title)
                await status_msg.edit_text(f"{convert_title}\n\n📊 <b>Jarayon:</b> <code>[{make_progress_bar(0)}]</code> <b>0%</b>", parse_mode=ParseMode.HTML)
                
                blur_file = str(TEMP_DIR / f"{session_id}_blur.mp4")
                thumb_file = str(TEMP_DIR / f"{session_id}_thumb.jpg")

                # Ultra-tez blur konvertatsiyasi foiz ko'rsatkichi bilan
                success = await convert_to_square_blur(
                    orig_file,
                    blur_file,
                    duration=duration,
                    on_progress=tracker.on_progress,
                    size=720
                )
                if not success or not os.path.exists(blur_file):
                    blur_file = orig_file

                await generate_thumbnail(blur_file, thumb_file)

                SESSIONS[session_id] = {
                    "type": "video",
                    "orig": orig_file,
                    "blur": blur_file,
                    "crop": None,
                    "audio": None,
                    "thumb": thumb_file,
                    "title": safe_title,
                    "uploader": uploader,
                    "platform": platform_name,
                    "duration": duration
                }

                try:
                    await status_msg.edit_text("🚀 <b>Video Telegramga yuborilmoqda...</b>", parse_mode=ParseMode.HTML)
                except Exception:
                    pass

                video_input = FSInputFile(blur_file)
                thumb_input = FSInputFile(thumb_file) if os.path.exists(thumb_file) else None
                keyboard = get_action_keyboard(session_id, media_type="video", current_mode="blur")

                caption = (
                    f"🎬 <b>{safe_title}</b>\n\n"
                    f"📐 <b>Format:</b> 1:1 Kvadrat (Blur fon)\n"
                    f"👤 <b>Kanal / Muallif:</b> {uploader}\n"
                    f"✨ @{bot_user.username}"
                )

                try:
                    await message.answer_video(
                        video=video_input,
                        thumbnail=thumb_input,
                        caption=caption,
                        parse_mode=ParseMode.HTML,
                        reply_markup=keyboard,
                        supports_streaming=True,
                        request_timeout=300
                    )
                except Exception as e:
                    logger.warning(f"Video yuborishda xatolik, sodda caption bilan yuborilmoqda: {e}")
                    await message.answer_video(
                        video=video_input,
                        caption=f"📐 1:1 Kvadrat | @{bot_user.username}",
                        reply_markup=keyboard,
                        supports_streaming=True,
                        request_timeout=300
                    )

            else:
                # Rasm formati
                await status_msg.edit_text(f"🔄 <b>Rasm ({idx+1}/{len(items)}) 1:1 to'rtburchak shaklga keltirilmoqda...</b>", parse_mode=ParseMode.HTML)
                
                blur_file = str(TEMP_DIR / f"{session_id}_blur.jpg")
                success = convert_image_to_square_blur(orig_file, blur_file)
                if not success:
                    blur_file = orig_file

                SESSIONS[session_id] = {
                    "type": "image",
                    "orig": orig_file,
                    "blur": blur_file,
                    "crop": None,
                    "title": safe_title,
                    "uploader": uploader,
                    "platform": platform_name
                }

                photo_input = FSInputFile(blur_file)
                keyboard = get_action_keyboard(session_id, media_type="image", current_mode="blur")

                caption = (
                    f"📸 <b>{safe_title}</b>\n\n"
                    f"📐 <b>Format:</b> 1:1 Kvadrat (Blur fon)\n"
                    f"✨ @{bot_user.username}"
                )

                await message.answer_photo(
                    photo=photo_input,
                    caption=caption,
                    parse_mode=ParseMode.HTML,
                    reply_markup=keyboard,
                    request_timeout=300
                )

        try:
            await status_msg.delete()
        except Exception:
            pass

    except Exception as e:
        logger.error(f"Xatolik: {e}", exc_info=True)
        await status_msg.edit_text(f"❌ Xatolik yuz berdi: {html.escape(str(e))}")


@dp.message(F.photo)
async def handle_direct_photo(message: types.Message, bot: Bot):
    """To'g'ridan-to'g'ri yuborilgan rasmni qabul qilish"""
    status_msg = await message.answer("⏳ <b>Rasm qabul qilinmoqda va 1:1 kvadratga keltirilmoqda...</b>", parse_mode=ParseMode.HTML)

    try:
        session_id = uuid.uuid4().hex[:8]
        orig_file = str(DOWNLOADS_DIR / f"{session_id}_direct.jpg")
        
        photo = message.photo[-1]
        file = await bot.get_file(photo.file_id)
        await bot.download_file(file.file_path, orig_file)

        blur_file = str(TEMP_DIR / f"{session_id}_blur.jpg")
        success = convert_image_to_square_blur(orig_file, blur_file)
        if not success:
            await status_msg.edit_text("❌ Rasmni to'rtburchak qilishda xatolik yuz berdi.")
            return

        SESSIONS[session_id] = {
            "type": "image",
            "orig": orig_file,
            "blur": blur_file,
            "crop": None,
            "title": "Rasm",
            "uploader": "Foydalanuvchi"
        }

        bot_user = await bot.get_me()
        photo_input = FSInputFile(blur_file)
        keyboard = get_action_keyboard(session_id, media_type="image", current_mode="blur")

        caption = (
            f"📸 <b>Rasm tayyor!</b>\n\n"
            f"📐 <b>Format:</b> 1:1 Kvadrat (Blur fon)\n"
            f"✨ Tayyorlandi: @{bot_user.username}"
        )

        await message.answer_photo(
            photo=photo_input,
            caption=caption,
            parse_mode=ParseMode.HTML,
            reply_markup=keyboard,
            request_timeout=300
        )

        try:
            await status_msg.delete()
        except Exception:
            pass

    except Exception as e:
        logger.error(f"To'g'ridan-to'g'ri rasmda xatolik: {e}", exc_info=True)
        await status_msg.edit_text(f"❌ Xatolik yuz berdi: {html.escape(str(e))}")


@dp.message(F.video | F.document)
async def handle_direct_video(message: types.Message, bot: Bot):
    """To'g'ridan-to'g'ri yuborilgan video yoki rasm hujjatni qabul qilish"""
    if message.document and message.document.mime_type and message.document.mime_type.startswith("image/"):
        status_msg = await message.answer("⏳ <b>Rasm yuklanmoqda va qayta ishlanmoqda...</b>", parse_mode=ParseMode.HTML)
        try:
            session_id = uuid.uuid4().hex[:8]
            orig_file = str(DOWNLOADS_DIR / f"{session_id}_direct_doc.jpg")
            file = await bot.get_file(message.document.file_id)
            await bot.download_file(file.file_path, orig_file)

            blur_file = str(TEMP_DIR / f"{session_id}_blur.jpg")
            success = convert_image_to_square_blur(orig_file, blur_file)
            if not success:
                await status_msg.edit_text("❌ Rasmni to'rtburchak qilishda xatolik yuz berdi.")
                return

            SESSIONS[session_id] = {
                "type": "image",
                "orig": orig_file,
                "blur": blur_file,
                "crop": None,
                "title": "Rasm",
                "uploader": "Foydalanuvchi"
            }

            bot_user = await bot.get_me()
            photo_input = FSInputFile(blur_file)
            keyboard = get_action_keyboard(session_id, media_type="image", current_mode="blur")

            caption = (
                f"📸 <b>Rasm tayyor!</b>\n\n"
                f"📐 <b>Format:</b> 1:1 Kvadrat (Blur fon)\n"
                f"✨ @{bot_user.username}"
            )

            await message.answer_photo(
                photo=photo_input,
                caption=caption,
                parse_mode=ParseMode.HTML,
                reply_markup=keyboard,
                request_timeout=300
            )
            await status_msg.delete()
            return
        except Exception as e:
            await status_msg.edit_text(f"❌ Xatolik: {html.escape(str(e))}")
            return

    video = message.video or (message.document if message.document and message.document.mime_type and message.document.mime_type.startswith("video/") else None)
    if not video:
        await message.answer("⚠️ Iltimos, video yoki rasm yuboring.")
        return

    status_msg = await message.answer("⏳ <b>Video qabul qilinmoqda...</b>", parse_mode=ParseMode.HTML)

    try:
        session_id = uuid.uuid4().hex[:8]
        orig_file = str(DOWNLOADS_DIR / f"{session_id}_direct.mp4")
        
        file = await bot.get_file(video.file_id)
        await bot.download_file(file.file_path, orig_file)

        duration = float(getattr(video, 'duration', 0.0) or get_video_duration(orig_file))
        tracker = ProgressTracker(status_msg, "🔄 <b>Video 1:1 to'rtburchak shaklga o'tkazilmoqda...</b>")
        await status_msg.edit_text(f"🔄 <b>Video 1:1 to'rtburchak shaklga o'tkazilmoqda...</b>\n\n📊 <b>Jarayon:</b> <code>[{make_progress_bar(0)}]</code> <b>0%</b>", parse_mode=ParseMode.HTML)
        
        blur_file = str(TEMP_DIR / f"{session_id}_blur.mp4")
        thumb_file = str(TEMP_DIR / f"{session_id}_thumb.jpg")

        success = await convert_to_square_blur(orig_file, blur_file, duration=duration, on_progress=tracker.on_progress, size=720)
        if not success:
            await status_msg.edit_text("❌ Videoni to'rtburchak qilishda xatolik yuz berdi.")
            return

        await generate_thumbnail(blur_file, thumb_file)

        SESSIONS[session_id] = {
            "type": "video",
            "orig": orig_file,
            "blur": blur_file,
            "crop": None,
            "audio": None,
            "thumb": thumb_file,
            "title": "Video",
            "uploader": "Foydalanuvchi",
            "duration": duration
        }

        await status_msg.edit_text("🚀 <b>Video yuborilmoqda...</b>", parse_mode=ParseMode.HTML)

        video_input = FSInputFile(blur_file)
        thumb_input = FSInputFile(thumb_file) if os.path.exists(thumb_file) else None
        
        bot_user = await bot.get_me()
        caption = (
            f"🎬 <b>Video tayyor!</b>\n\n"
            f"📐 <b>Format:</b> 1:1 Kvadrat (Blur fon)\n"
            f"✨ Tayyorlandi: @{bot_user.username}"
        )

        keyboard = get_action_keyboard(session_id, media_type="video", current_mode="blur")

        await message.answer_video(
            video=video_input,
            thumbnail=thumb_input,
            caption=caption,
            parse_mode=ParseMode.HTML,
            reply_markup=keyboard,
            supports_streaming=True,
            request_timeout=300
        )

        try:
            await status_msg.delete()
        except Exception:
            pass

    except Exception as e:
        logger.error(f"To'g'ridan-to'g'ri videoda xatolik: {e}", exc_info=True)
        await status_msg.edit_text(f"❌ Xatolik yuz berdi: {html.escape(str(e))}")


@dp.callback_query(F.data.startswith("mode:"))
async def handle_mode_callback(query: CallbackQuery, bot: Bot):
    """Foydalanuvchi boshqa format yoki musiqa tugmasini bosganda"""
    _, mode, session_id = query.data.split(":")
    
    session = SESSIONS.get(session_id)
    if not session or not os.path.exists(session["orig"]):
        await query.answer("⚠️ Fayl muddati tugagan yoki topilmadi. Iltimos havolani qayta yuboring.", show_alert=True)
        return

    orig_file = session["orig"]
    media_type = session.get("type", "video")
    bot_user = await bot.get_me()
    safe_title = session.get('title', 'Media')
    uploader = session.get('uploader', '')
    duration = float(session.get('duration', 0.0) or 0.0)

    if mode == "audio":
        await query.answer("🎵 Asl musiqa ajratib olinmoqda...")
        status = await query.message.reply("🎵 <b>Videodagi asl musiqa (HQ MP3) ajratib olinmoqda...</b>", parse_mode=ParseMode.HTML)
        
        try:
            music_data = await get_full_music_for_video(orig_file, safe_title, session_id, author_hint=uploader)
            if not music_data or not os.path.exists(music_data['file_path']):
                await status.edit_text("❌ Musiqani ajratib bo'lmadi.")
                return

            audio_file = music_data['file_path']
            title = music_data['title']
            artist = music_data['artist']
            dur = int(music_data.get('duration', 0))

            audio_input = FSInputFile(audio_file)
            caption = (
                f"🎵 <b>{html.escape(title)}</b>\n"
                f"👤 <b>Muallif / Kanal:</b> {html.escape(artist)}\n"
                f"🔥 <i>Videoning asl musiqasi (HQ MP3)</i>\n"
                f"✨ @{bot_user.username}"
            )

            await query.message.answer_audio(
                audio=audio_input,
                title=title,
                performer=artist,
                duration=dur if dur > 0 else None,
                caption=caption,
                parse_mode=ParseMode.HTML,
                request_timeout=300
            )
            await status.delete()

        except Exception as e:
            logger.error(f"Audio ajratishda xatolik: {e}", exc_info=True)
            await status.edit_text(f"❌ Musiqani ajratishda xatolik: {html.escape(str(e))}")
        return

    await query.answer("⏳ Format tayyorlanmoqda...")

    if media_type == "video":
        if mode == "orig":
            video_input = FSInputFile(orig_file)
            caption = (
                f"🎬 <b>{safe_title}</b>\n\n"
                f"🎞 <b>Format:</b> Asl holatda (Original)\n"
                f"✨ @{bot_user.username}"
            )
            keyboard = get_action_keyboard(session_id, media_type="video", current_mode="orig")
            await query.message.answer_video(
                video=video_input,
                caption=caption,
                parse_mode=ParseMode.HTML,
                reply_markup=keyboard,
                supports_streaming=True,
                request_timeout=300
            )

        elif mode == "crop":
            crop_file = session.get("crop")
            if not crop_file or not os.path.exists(crop_file):
                status_msg = await query.message.reply("🔄 <b>1:1 Qirqish (Crop) amalga oshirilmoqda...</b>", parse_mode=ParseMode.HTML)
                crop_file = str(TEMP_DIR / f"{session_id}_crop.mp4")
                tracker = ProgressTracker(status_msg, "✂️ <b>1:1 Qirqish (Crop) amalga oshirilmoqda...</b>")
                success = await convert_to_square_crop(orig_file, crop_file, duration=duration, on_progress=tracker.on_progress, size=720)
                try:
                    await status_msg.delete()
                except Exception:
                    pass

                if not success or not os.path.exists(crop_file):
                    await query.message.answer("❌ Qirqishda (crop) xatolik yuz berdi.")
                    return
                session["crop"] = crop_file

            video_input = FSInputFile(crop_file)
            caption = (
                f"🎬 <b>{safe_title}</b>\n\n"
                f"✂️ <b>Format:</b> 1:1 Kvadrat (Qirqilgan / Crop)\n"
                f"✨ @{bot_user.username}"
            )
            keyboard = get_action_keyboard(session_id, media_type="video", current_mode="crop")
            await query.message.answer_video(
                video=video_input,
                caption=caption,
                parse_mode=ParseMode.HTML,
                reply_markup=keyboard,
                supports_streaming=True,
                request_timeout=300
            )

        elif mode == "blur":
            blur_file = session.get("blur")
            if not blur_file or not os.path.exists(blur_file):
                status_msg = await query.message.reply("🔄 <b>1:1 Kvadratga keltirilmoqda...</b>", parse_mode=ParseMode.HTML)
                blur_file = str(TEMP_DIR / f"{session_id}_blur.mp4")
                tracker = ProgressTracker(status_msg, "📐 <b>1:1 Kvadratga keltirilmoqda...</b>")
                success = await convert_to_square_blur(orig_file, blur_file, duration=duration, on_progress=tracker.on_progress, size=720)
                try:
                    await status_msg.delete()
                except Exception:
                    pass

                if not success or not os.path.exists(blur_file):
                    await query.message.answer("❌ Kvadrat qilishda xatolik yuz berdi.")
                    return
                session["blur"] = blur_file

            video_input = FSInputFile(blur_file)
            caption = (
                f"🎬 <b>{safe_title}</b>\n\n"
                f"📐 <b>Format:</b> 1:1 Kvadrat (Blur fon)\n"
                f"✨ @{bot_user.username}"
            )
            keyboard = get_action_keyboard(session_id, media_type="video", current_mode="blur")
            await query.message.answer_video(
                video=video_input,
                caption=caption,
                parse_mode=ParseMode.HTML,
                reply_markup=keyboard,
                supports_streaming=True,
                request_timeout=300
            )
    else:
        # Rasm uchun tugmalar
        if mode == "orig":
            photo_input = FSInputFile(orig_file)
            caption = (
                f"📸 <b>{safe_title}</b>\n\n"
                f"🖼 <b>Format:</b> Asl holatda (Original)\n"
                f"✨ @{bot_user.username}"
            )
            keyboard = get_action_keyboard(session_id, media_type="image", current_mode="orig")
            await query.message.answer_photo(
                photo=photo_input,
                caption=caption,
                parse_mode=ParseMode.HTML,
                reply_markup=keyboard,
                request_timeout=300
            )

        elif mode == "crop":
            crop_file = session.get("crop")
            if not crop_file or not os.path.exists(crop_file):
                crop_file = str(TEMP_DIR / f"{session_id}_crop.jpg")
                success = convert_image_to_square_crop(orig_file, crop_file)
                if not success:
                    await query.message.answer("❌ Rasmni qirqishda (crop) xatolik yuz berdi.")
                    return
                session["crop"] = crop_file

            photo_input = FSInputFile(crop_file)
            caption = (
                f"📸 <b>{safe_title}</b>\n\n"
                f"✂️ <b>Format:</b> 1:1 Kvadrat (Qirqilgan / Crop)\n"
                f"✨ @{bot_user.username}"
            )
            keyboard = get_action_keyboard(session_id, media_type="image", current_mode="crop")
            await query.message.answer_photo(
                photo=photo_input,
                caption=caption,
                parse_mode=ParseMode.HTML,
                reply_markup=keyboard,
                request_timeout=300
            )

        elif mode == "blur":
            blur_file = session.get("blur")
            if not blur_file or not os.path.exists(blur_file):
                blur_file = str(TEMP_DIR / f"{session_id}_blur.jpg")
                success = convert_image_to_square_blur(orig_file, blur_file)
                if not success:
                    await query.message.answer("❌ Kvadrat qilishda xatolik yuz berdi.")
                    return
                session["blur"] = blur_file

            photo_input = FSInputFile(blur_file)
            caption = (
                f"📸 <b>{safe_title}</b>\n\n"
                f"📐 <b>Format:</b> 1:1 Kvadrat (Blur fon)\n"
                f"✨ @{bot_user.username}"
            )
            keyboard = get_action_keyboard(session_id, media_type="image", current_mode="blur")
            await query.message.answer_photo(
                photo=photo_input,
                caption=caption,
                parse_mode=ParseMode.HTML,
                reply_markup=keyboard,
                request_timeout=300
            )


async def start_dummy_server():
    """Render / Koyeb kabi bepul serverlar uchun health check portini ochish"""
    port = int(os.getenv("PORT", 0))
    if port > 0:
        from aiohttp import web
        app = web.Application()
        async def handle_ping(request):
            return web.Response(text="Bot is online and running! 🚀")
        app.router.add_get("/", handle_ping)
        app.router.add_get("/health", handle_ping)
        runner = web.AppRunner(app)
        await runner.setup()
        site = web.TCPSite(runner, "0.0.0.0", port)
        await site.start()
        print(f"🌐 Health-check server ishga tushdi (Port: {port})")


async def main():
    """Botni ishga tushirish asosiy funksiyasi"""
    if not BOT_TOKEN or ":" not in BOT_TOKEN or BOT_TOKEN == "YOUR_TELEGRAM_BOT_TOKEN_HERE":
        print("=" * 60)
        print("DIQQAT: BOT_TOKEN ko'rsatilmagan yoki noto'g'ri!")
        print("Iltimos, instagram_square_bot\\.env faylini ochib, BOT_TOKEN ga o'z bot tokeningizni yozing.")
        print("=" * 60)
        return

    # Bepul serverlar uchun health-check portini yoqish
    await start_dummy_server()

    session = AiohttpSession(timeout=300.0)
    bot = Bot(token=BOT_TOKEN, session=session)

    bot_info = await bot.get_me()
    print("=" * 60)
    print(f"🚀 Bot muvaffaqiyatli ishga tushdi: @{bot_info.username}")
    print("🎬 Instagram, YouTube va TikTok tezkor qayta ishlash bilan ishlamoqda!")
    print("=" * 60)

    # Eski yangilanishlarni tozalash
    await bot.delete_webhook(drop_pending_updates=True)
    await dp.start_polling(bot)


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except (KeyboardInterrupt, SystemExit):
        print("\nBot to'xtatildi.")

