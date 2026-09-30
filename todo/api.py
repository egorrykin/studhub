from datetime import timedelta

from django.core.cache import cache
from django.db.models import Sum
from django.utils import timezone
from rest_framework.decorators import api_view, permission_classes
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response

from .models import ClickerProfile, Respect, User


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


# ─── АНТИЧИТ ────────────────────────────────────────────────────
MAX_CLICKS_PER_SECOND = 12       # человеческий предел ~12 кликов/сек
MAX_CLICKS_PER_BATCH = 150       # максимум в одном запросе
SUSPICIOUS_DROP_RATIO = 0.5      # если кликов больше лимита — половину в бан


def _handle_clicks(p, raw_count):
    """
    Возвращает (accepted, rejected) — сколько кликов зачтено и сколько отклонено.
    Обновляет suspicious_score если обнаружен фрод.
    """
    now = timezone.now()

    # Обнуляем счётчик раз в секунду
    if p.last_click_second is None or (now - p.last_click_second) > timedelta(seconds=1):
        p.clicks_in_last_second = 0
        p.last_click_second = now

    # Считаем, сколько кликов приходится на текущую секунду
    elapsed = (now - p.last_click_second).total_seconds() if p.last_click_second else 0
    # Грубая нормализация: не позволяем за секунду больше MAX_CLICKS_PER_SECOND
    new_total = p.clicks_in_last_second + raw_count
    if new_total > MAX_CLICKS_PER_SECOND:
        accepted = max(0, MAX_CLICKS_PER_SECOND - p.clicks_in_last_second)
        rejected = raw_count - accepted
    else:
        accepted = raw_count
        rejected = 0

    p.clicks_in_last_second = p.clicks_in_last_second + accepted
    p.last_click_second = now
    return accepted, rejected


@api_view(['GET'])
@permission_classes([IsAuthenticated])
def clicker_state(request):
    p = _profile(request.user)
    p.apply_auto()
    return Response(_serialize(p))


@api_view(['POST'])
@permission_classes([IsAuthenticated])
def clicker_click(request):
    try:
        count = int(request.data.get('count', 1) or 1)
    except (ValueError, TypeError):
        count = 1
    if count < 1:
        count = 1
    if count > MAX_CLICKS_PER_BATCH:
        count = MAX_CLICKS_PER_BATCH

    p, _ = ClickerProfile.objects.get_or_create(user=request.user)

    now = timezone.now()
    delta = (now - p.last_tick).total_seconds()
    if delta > 3600:
        delta = 3600
    auto_gain = int(delta * p.auto_per_sec) if (delta > 0 and p.auto_per_sec > 0) else 0

    accepted, rejected = _handle_clicks(p, count)

    if rejected > 0:
        # Записываем в бан — это "лишние" клики
        p.suspicious_score += rejected * p.per_click

    p.score += auto_gain + (p.per_click * accepted)
    p.total_clicks += accepted
    p.last_tick = now
    p.save()

    cache.delete(f'lb:{p.user.group_id}:{p.user_id}')
    cache.delete('clicker_top_site_v2')

    return Response({
        'ok': True,
        'accepted': accepted,
        'rejected': rejected,
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
    if not user.group_id:
        return {'leaderboard': [], 'my_rank': None}
    profiles = list(
        ClickerProfile.objects
        .select_related('user')
        .only('id', 'score', 'user_id',
              'user__id', 'user__full_name', 'user__email')
        .filter(user__is_active=True, user__group_id=user.group_id)
    )
    # Сортировка по уровню, потом по score
    profiles.sort(key=lambda p: (-p.level_num, -p.score))
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
    profiles = list(
        ClickerProfile.objects
        .select_related('user')
        .filter(user__is_active=True, user__group_id=request.user.group_id)
    )
    profiles.sort(key=lambda p: (-p.level_num, -p.score))
    p = profiles[0] if profiles and profiles[0].score > 0 else None
    if not p:
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