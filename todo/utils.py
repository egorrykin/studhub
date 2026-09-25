"""Утилиты: безопасный редирект, лимиты файлов, сжатие картинок, Yandex SmartCaptcha."""

from io import BytesIO

import requests
from django.conf import settings
from django.core.exceptions import ValidationError
from django.core.files.base import ContentFile
from django.db.models import Sum
from django.shortcuts import redirect
from django.urls import reverse
from django.utils.http import url_has_allowed_host_and_scheme


FILE_SIZE_LIMIT = 50 * 1024 * 1024                 # 50 МБ на файл
GROUP_STORAGE_LIMIT = 30 * 1024 * 1024 * 1024      # 30 ГБ на группу
IMAGE_MAX_DIMENSION = 1920
IMAGE_JPEG_QUALITY = 85
IMAGE_EXTS = ('jpg', 'jpeg', 'png', 'webp', 'bmp', 'gif')


# ═══════════════════════════════════════════════════════════════
# Файлы и хранилище
# ═══════════════════════════════════════════════════════════════

def get_group_storage_used(group) -> int:
    """Суммарный размер всех файлов, привязанных к группе (в байтах)."""
    if not group:
        return 0
    from .models import Attachment, LectureMaterial, AnnouncementImage
    total = 0
    total += (Attachment.objects
              .filter(homework__lecture__groups=group)
              .aggregate(s=Sum('file_size'))['s'] or 0)
    total += (LectureMaterial.objects
              .filter(lecture__groups=group)
              .aggregate(s=Sum('file_size'))['s'] or 0)
    total += (AnnouncementImage.objects
              .filter(announcement__group=group)
              .aggregate(s=Sum('file_size'))['s'] or 0)
    return total


def check_file_size(f):
    if getattr(f, 'size', 0) > FILE_SIZE_LIMIT:
        raise ValidationError(
            f'Файл «{getattr(f, "name", "?")}» больше 50 МБ.'
        )


def check_group_storage(group, incoming_bytes: int):
    if group is None:
        return
    used = get_group_storage_used(group)
    if used + incoming_bytes > GROUP_STORAGE_LIMIT:
        free_gb = max(0, GROUP_STORAGE_LIMIT - used) / (1024 ** 3)
        raise ValidationError(
            f'Превышен лимит хранилища группы «{group.name}» (30 ГБ). '
            f'Свободно: {free_gb:.2f} ГБ.'
        )


def check_groups_storage(groups, incoming_bytes: int):
    """Проверяет лимит по каждой группе из списка."""
    for g in groups:
        check_group_storage(g, incoming_bytes)


def compress_image(uploaded_file):
    """
    Сжимает картинку: ограничивает размер до 1920px и перекодирует в JPEG/PNG.
    Возвращает (файл, размер_в_байтах).
    Не-картинки возвращает без изменений.
    """
    name = getattr(uploaded_file, 'name', 'file')
    ext = name.lower().rsplit('.', 1)[-1] if '.' in name else ''

    if ext not in IMAGE_EXTS:
        return uploaded_file, getattr(uploaded_file, 'size', 0)

    try:
        from PIL import Image, ImageOps
        uploaded_file.seek(0)
        img = Image.open(uploaded_file)
        img = ImageOps.exif_transpose(img)

        has_alpha = img.mode in ('RGBA', 'LA', 'P')
        if has_alpha:
            fmt, new_ext = 'PNG', 'png'
        else:
            if img.mode != 'RGB':
                img = img.convert('RGB')
            fmt, new_ext = 'JPEG', 'jpg'

        if img.width > IMAGE_MAX_DIMENSION or img.height > IMAGE_MAX_DIMENSION:
            img.thumbnail((IMAGE_MAX_DIMENSION, IMAGE_MAX_DIMENSION), Image.LANCZOS)

        buf = BytesIO()
        if fmt == 'JPEG':
            img.save(buf, format=fmt, quality=IMAGE_JPEG_QUALITY, optimize=True, progressive=True)
        else:
            img.save(buf, format=fmt, optimize=True)

        data = buf.getvalue()
        base = name.rsplit('.', 1)[0] if '.' in name else name
        new_name = f'{base}.{new_ext}'
        return ContentFile(data, name=new_name), len(data)
    except Exception:
        try:
            uploaded_file.seek(0)
        except Exception:
            pass
        return uploaded_file, getattr(uploaded_file, 'size', 0)


def safe_redirect(request, fallback_name='schedule'):
    referer = request.META.get('HTTP_REFERER')
    if referer and url_has_allowed_host_and_scheme(
            referer,
            allowed_hosts={request.get_host()},
            require_https=request.is_secure(),
    ):
        return redirect(referer)
    return redirect(reverse(fallback_name))


# ═══════════════════════════════════════════════════════════════
# Yandex SmartCaptcha
# ═══════════════════════════════════════════════════════════════

def get_client_ip(request) -> str:
    """Возвращает реальный IP пользователя (учитывает прокси)."""
    xff = request.META.get('HTTP_X_FORWARDED_FOR')
    if xff:
        return xff.split(',')[0].strip()
    return request.META.get('REMOTE_ADDR', '') or ''


def verify_smartcaptcha(token: str, ip: str = None) -> bool:
    """
    Проверяет токен Yandex SmartCaptcha на сервере.
    Возвращает True, если капча пройдена.

    Fail-open: при сетевой ошибке/таймауте возвращает True,
    чтобы пользователь не оказался заблокирован из-за сбоя на стороне Yandex.
    """
    if not token:
        return False

    server_key = getattr(settings, 'YANDEX_SMARTCAPTCHA_SERVER_KEY', '')
    if not server_key:
        # Если ключ не настроен — не блокируем (для разработки)
        return True

    try:
        r = requests.post(
            'https://smartcaptcha.yandexcloud.net/validate',
            data={
                'secret': server_key,
                'token': token,
                'ip': ip or '',
            },
            timeout=5,
        )

        if r.status_code != 200:
            # При ошибке сервиса — не блокируем
            return True

        data = r.json()
        return data.get('status') == 'ok'

    except Exception:
        # При любой сетевой ошибке — не блокируем
        return True