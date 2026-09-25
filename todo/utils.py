"""Утилиты: безопасный редирект, лимиты файлов, сжатие картинок."""

from io import BytesIO

from django.core.exceptions import ValidationError
from django.core.files.base import ContentFile
from django.db.models import Sum
from django.shortcuts import redirect
from django.urls import reverse
from django.utils.http import url_has_allowed_host_and_scheme


FILE_SIZE_LIMIT = 50 * 1024 * 1024                 # 50 МБ на файл
GROUP_STORAGE_LIMIT = 30 * 1024 * 1024 * 1024      # 30 ГБ на группу
IMAGE_MAX_DIMENSION = 1920                         # макс. ширина/высота картинки
IMAGE_JPEG_QUALITY = 85
IMAGE_EXTS = ('jpg', 'jpeg', 'png', 'webp', 'bmp', 'gif')


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
        img = ImageOps.exif_transpose(img)   # учитываем поворот камеры

        # прозрачность → PNG, иначе JPEG
        has_alpha = img.mode in ('RGBA', 'LA', 'P')
        if has_alpha:
            fmt, new_ext = 'PNG', 'png'
        else:
            if img.mode != 'RGB':
                img = img.convert('RGB')
            fmt, new_ext = 'JPEG', 'jpg'

        # уменьшение
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
        # если что-то не так — отдаём оригинал
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