import os
from PIL import Image, ImageFilter, ImageOps, ImageEnhance
from typing import Tuple


def convert_image_to_square_blur(input_path: str, output_path: str, target_size: int = 1080) -> bool:
    """
    Rasmni 1:1 kvadrat formatga xiralashtirilgan fon (Blur background) bilan o'tkazish.
    Asl rasm to'liq markazda saqlanadi, yonlari esa chiroyli xira fon bilan to'ldiriladi.
    """
    try:
        with Image.open(input_path) as img:
            # RGB formatga o'tkazish (agar RGBA yoki boshqa rejimda bo'lsa)
            if img.mode in ("RGBA", "P"):
                img = img.convert("RGB")

            orig_w, orig_h = img.size

            # Agar rasm allaqachon kvadrat bo'lsa
            if orig_w == orig_h:
                img_resized = img.resize((target_size, target_size), Image.Resampling.LANCZOS)
                img_resized.save(output_path, "JPEG", quality=95)
                return True

            # 1. Orqa fonni tayyorlash (rasmni butun kvadratni qoplaydigan qilib kattalashtirish va blur qilish)
            bg = ImageOps.fit(img, (target_size, target_size), method=Image.Resampling.LANCZOS)
            bg = bg.filter(ImageFilter.GaussianBlur(radius=25))
            
            # Fonni biroz qoraytirish (kontrast chiroyli chiqishi uchun)
            enhancer = ImageEnhance.Brightness(bg)
            bg = enhancer.enhance(0.85)

            # 2. Asosiy (old) rasmni nisbatini buzmasdan kvadrat ichiga sig'dirish
            img_ratio = orig_w / orig_h
            if orig_w > orig_h:
                new_w = target_size
                new_h = int(target_size / img_ratio)
            else:
                new_h = target_size
                new_w = int(target_size * img_ratio)

            fg = img.resize((new_w, new_h), Image.Resampling.LANCZOS)

            # 3. Old rasmni fonning markaziga joylashtirish
            offset_x = (target_size - new_w) // 2
            offset_y = (target_size - new_h) // 2

            bg.paste(fg, (offset_x, offset_y))
            bg.save(output_path, "JPEG", quality=95)
            return True
    except Exception as e:
        print(f"Rasmga ishlov berishda xatolik: {e}")
        return False


def convert_image_to_square_crop(input_path: str, output_path: str, target_size: int = 1080) -> bool:
    """
    Rasmni markazidan 1:1 kvadrat qilib qirqib olish (Crop).
    """
    try:
        with Image.open(input_path) as img:
            if img.mode in ("RGBA", "P"):
                img = img.convert("RGB")

            cropped = ImageOps.fit(img, (target_size, target_size), method=Image.Resampling.LANCZOS)
            cropped.save(output_path, "JPEG", quality=95)
            return True
    except Exception as e:
        print(f"Rasmni qirqishda xatolik: {e}")
        return False
