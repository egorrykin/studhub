from datetime import timedelta

from django.core.cache import cache
from django.db.models import Sum
from django.utils import timezone
from rest_framework.decorators import api_view, permission_classes
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response

from .models import ClickerProfile, Respect, User


MAX_CLICKS_PER_BATCH = 150     # максимум в одном запросе
MIN_INTERVAL_MS = 30           # быстрее 30ms — физически не человек
HUMAN_MAX_MEAN_MS = 700        # быстрее этого — подозрительно
CV_THRESHOLD_LONG = 0.15       # для длинных серий (>10 кликов)
CV_THRESHOLD_SHORT = 0.05      # для коротких серий (6-9 кликов)


def _profile(user):
    p, _ = ClickerProfile.objects.get_or_create(user=user)
    return p


def _serialize(p):
    return {
        'score': p.score,
        'per_click': p.per_click,
        'auto_per_sec': p.auto_per_sec,
        'level': p.level_num,
        'level_name': p.level_name,
        'stage_name': p.level_name,
        'stage_emoji': p.stage_emoji,
        'next_level_score': p.next_level_score,
        'progress_percent': p.progress_percent,
        'per_click_cost': p.per_click_cost,
        'auto_cost': p.auto_cost,
        'total_clicks': p.total_clicks,
    }


# ═══════════════════════════════════════════════════════════════
# АНТИЧИТ: анализ интервалов между кликами
# ═══════════════════════════════════════════════════════════════

def _detect_autoclicker(timestamps):
    """
    Принимает список временных меток (в мс) кликов пользователя.
    Возвращает True, если это похоже на автокликер.

    Логика:
      • Клики быстрее 30ms — физически невозможно
      • Если средний интервал < 700ms И коэффициент вариации (stddev/mean)
        очень маленький — интервалы подозрительно одинаковые → бот
      • Для длинных серий (>10 кликов) порог CV мягче (0.15),
        для коротких (6-9 кликов) — строже (0.05), чтобы не ловить случайность
    """
    if not timestamps or len(timestamps) < 6:
        return False

    # Ограничиваем выборку последними 50 метками
    try:
        ts = sorted(int(t) for t in timestamps)[-50:]
    except (ValueError, TypeError):
        return False

    # Вычисляем интервалы
    intervals = [ts[i + 1] - ts[i] for i in range(len(ts) - 1)]
    intervals = [x for x in intervals if x > 0]

    if not intervals:
        return False

    mean = sum(intervals) / len(intervals)

    # Слишком быстро физически
    if mean < MIN_INTERVAL_MS:
        return True

    # Слишком медленно — это человек
    if mean > 2000:
        return False

    # Стандартное отклонение
    variance = sum((x - mean) ** 2 for x in intervals) / len(intervals)
    stddev = variance ** 0.5

    # Коэффициент вариации
    cv = stddev / mean if mean > 0 else 0

    # Длинные серии — основной анализ
    if len(intervals) >= 10:
        return cv < CV_THRESHOLD_LONG and mean < HUMAN_MAX_MEAN_MS

    # Короткие серии — строгий порог, чтобы не забанить случайно
    return cv < CV_THRESHOLD_SHORT and mean < 500


# ═══════════════════════════════════════════════════════════════
# API
# ═══════════════════════════════════════════════════════════════

@api_view(['GET'])
@permission_classes([IsAuthenticated])
def clicker_state(request):
    p = _profile(request.user)
    p.apply_auto()
    return Response(_serialize(p))


@api_view(['POST'])
@permission_classes([IsAuthenticated])
def clicker_click(request):
    """
    Принимает {count: N, timestamps: [ms, ms, ...]}.
    Проверяет паттерн кликов и не засчитывает, если похоже на бота.
    """
    # ─── Парсим count ───
    try:
        count = int(request.data.get('count', 1) or 1)
    except (ValueError, TypeError):
        count = 1
    if count < 1:
        count = 1
    if count > MAX_CLICKS_PER_BATCH:
        count = MAX_CLICKS_PER_BATCH

    # ─── Парсим timestamps ───
    raw_ts = request.data.get('timestamps', [])
    if not isinstance(raw_ts, list):
        raw_ts = []
    try:
        timestamps = [int(t) for t in raw_ts][-MAX_CLICKS_PER_BATCH:]
    except (ValueError, TypeError):
        timestamps = []

    # ─── Профиль ───
    p, _ = ClickerProfile.objects.get_or_create(user=request.user)

    # Автоприрост
    now = timezone.now()
    delta = (now - p.last_tick).total_seconds()
    if delta > 3600:
        delta = 3600
    auto_gain = int(delta * p.auto_per_sec) if (delta > 0 and p.auto_per_sec > 0) else 0

    # ─── Античит ───
    autoclicker_detected = False
    if timestamps and len(timestamps) >= 6:
        autoclicker_detected = _detect_autoclicker(timestamps)
    elif count > 1 and not timestamps:
        # Нет timestamps и count > 1 — подозрительно, но не блокируем
        # (может быть, старый клиент). Засчитываем только 1.
        p.suspicious_score += (count - 1) * p.per_click
        count = 1

    if autoclicker_detected:
        # Отклоняем все ручные клики, но начисляем автокликер
        p.suspicious_score += count * p.per_click
        p.score += auto_gain
        p.last_tick = now
        p.save()

        return Response({
            'ok': True,
            'accepted': 0,
            'rejected': count,
            'autoclicker_detected': True,
            **_serialize(p),
        })

    # ─── Нормальный клик ───
    p.score += auto_gain + (p.per_click * count)
    p.total_clicks += count
    p.last_tick = now
    p.save()

    cache.delete(f'lb:{p.user.group_id}:{p.user_id}')
    cache.delete('clicker_top_site_v2')

    return Response({
        'ok': True,
        'accepted': count,
        'rejected': 0,
        'autoclicker_detected': False,
        **_serialize(p),
    })


@api_view(['POST'])
@permission_classes([IsAuthenticated])
def clicker_upgrade_click(request):
    p = _profile(request.user)
    p.apply_auto()
    cost = p.per_click_cost
    if p.score < cost:
        return Response({'ok': False, 'error': 'Недостаточно очков'}, status=400)
    p.score -= cost
    p.per_click += 1
    p.save()
    cache.delete(f'lb:{p.user.group_id}:{p.user_id}')
    cache.delete('clicker_top_site_v2')
    return Response({'ok': True, **_serialize(p)})


@api_view(['POST'])
@permission_classes([IsAuthenticated])
def clicker_upgrade_auto(request):
    p = _profile(request.user)
    p.apply_auto()
    cost = p.auto_cost
    if p.score < cost:
        return Response({'ok': False, 'error': 'Недостаточно очков'}, status=400)
    p.score -= cost
    p.auto_per_sec += 1
    p.save()
    cache.delete(f'lb:{p.user.group_id}:{p.user_id}')
    cache.delete('clicker_top_site_v2')
    return Response({'ok': True, **_serialize(p)})


def _build_leaderboard(user):
    """Сортировка по score в БД — эквивалентна сортировке по уровню, т.к. уровни
    монотонно зависят от очков."""
    if not user.group_id:
        return {'leaderboard': [], 'my_rank': None}
    profiles = list(
        ClickerProfile.objects
        .select_related('user')
        .only('id', 'score', 'user_id',
              'user__id', 'user__full_name', 'user__email')
        .filter(user__is_active=True, user__group_id=user.group_id)
        .order_by('-score')[:50]
    )
    my_rank = None
    for i, pr in enumerate(profiles, 1):
        if pr.user_id == user.pk:
            my_rank = i
            break
    return {
        'leaderboard': [
            {
                'rank': i,
                'name': pr.user.short_name,
                'full_name': pr.user.full_name or pr.user.email,
                'score': pr.score,
                'level': pr.level_num,
                'level_name': pr.level_name,
                'stage_emoji': pr.stage_emoji,
                'is_me': pr.user_id == user.pk,
            }
            for i, pr in enumerate(profiles, 1)
        ],
        'my_rank': my_rank,
    }


@api_view(['GET'])
@permission_classes([IsAuthenticated])
def clicker_leaderboard(request):
    user = request.user
    cache_key = f'lb:{user.group_id}:{user.pk}'
    cached = cache.get(cache_key)
    if cached:
        return Response(cached)
    data = _build_leaderboard(user)
    cache.set(cache_key, data, 5)
    return Response(data)


@api_view(['GET'])
@permission_classes([IsAuthenticated])
def clicker_state_and_leaderboard(request):
    user = request.user
    p = _profile(user)
    p.apply_auto()
    state = _serialize(p)

    cache_key = f'lb:{user.group_id}:{user.pk}'
    lb = cache.get(cache_key)
    if not lb:
        lb = _build_leaderboard(user)
        cache.set(cache_key, lb, 5)
    return Response({**state, **lb})


@api_view(['GET'])
@permission_classes([IsAuthenticated])
def top_clicker(request):
    if not request.user.group_id:
        return Response({'has_top': False})
    p = (ClickerProfile.objects
         .select_related('user')
         .filter(user__is_active=True, user__group_id=request.user.group_id)
         .order_by('-score')
         .first())
    if not p or p.score == 0:
        return Response({'has_top': False})
    return Response({
        'has_top': True,
        'name': p.user.short_name,
        'score': p.score,
        'level': p.level_num,
        'stage_emoji': p.stage_emoji,
        'stage_name': p.level_name,
    })


@api_view(['GET'])
@permission_classes([IsAuthenticated])
def respect_list(request, pk):
    student = User.objects.filter(pk=pk).first()
    if not student:
        return Response({'error': 'not found'}, status=404)
    if (not request.user.is_superuser
            and student.group_id != request.user.group_id):
        return Response({'error': 'forbidden'}, status=403)
    items = Respect.objects.filter(student=student).select_related('author')
    total = items.aggregate(total=Sum('value'))['total'] or 0
    return Response({
        'total': total,
        'can_edit': request.user.can_edit,
        'items': [
            {
                'id': r.pk,
                'value': r.value,
                'is_plus': r.is_plus,
                'comment': r.comment,
                'author': r.author.short_name if r.author else '—',
                'date': r.created_at.strftime('%d.%m.%Y %H:%M'),
            }
            for r in items
        ],
    })