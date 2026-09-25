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


def _group_leaderboard(user, limit=50):
    if not user.group_id:
        return []
    return list(
        ClickerProfile.objects
        .select_related('user')
        .only('id', 'score', 'level', 'user_id',
              'user__id', 'user__full_name', 'user__email')
        .filter(user__is_active=True, user__group_id=user.group_id)
        .order_by('-score')[:limit]
    )


def _serialize_profile(p):
    """Единый формат ответа для state и click."""
    return {
        'score': p.score,
        'per_click': p.per_click,
        'auto_per_sec': p.auto_per_sec,
        'level': p.level,
        'stage_name': p.stage[0],
        'stage_emoji': p.stage[1],
        'per_click_cost': p.per_click_cost,
        'auto_cost': p.auto_cost,
        'total_clicks': p.total_clicks,
    }


@api_view(['GET'])
@permission_classes([IsAuthenticated])
def clicker_state(request):
    p = _profile(request.user)
    p.apply_auto()
    return Response(_serialize_profile(p))


@api_view(['POST'])
@permission_classes([IsAuthenticated])
def clicker_click(request):
    """
    Принимает {count: N} — сколько кликов пользователь сделал за батч.
    По умолчанию 1 (для обратной совместимости).

    Всё обновление одним save() — быстро и без гонок.
    """
    # ─── Парсим count ──────────────────────────────────────────
    try:
        count = int(request.data.get('count', 1) or 1)
    except (ValueError, TypeError):
        count = 1

    # Защита от абсурда: не более 200 кликов за один батч.
    # Быстрее 200/500мс = 400 кликов в секунду — это уже античит.
    if count < 1:
        count = 1
    if count > 200:
        count = 200

    # ─── Получаем профиль и применяем всё одним апдейтом ──────
    p, _ = ClickerProfile.objects.get_or_create(user=request.user)

    now = timezone.now()
    delta = (now - p.last_tick).total_seconds()

    # Очки от автокликера за прошедшее время
    auto_gain = 0
    if delta > 0 and p.auto_per_sec > 0:
        auto_gain = int(delta * p.auto_per_sec)

    # Прибавляем автоклик + пачку ручных кликов
    p.score += auto_gain + (p.per_click * count)
    p.total_clicks += count
    p.last_tick = now
    p._update_level()
    p.save()

    # Сбрасываем кеш лидерборда, чтобы следующий poll увидел свежие данные
    cache.delete(f'lb:{p.user.group_id}:{p.user_id}')

    return Response({
        'ok': True,
        **_serialize_profile(p),
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
    return Response({
        'ok': True,
        'score': p.score,
        'per_click': p.per_click,
        'per_click_cost': p.per_click_cost,
    })


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
    return Response({
        'ok': True,
        'score': p.score,
        'auto_per_sec': p.auto_per_sec,
        'auto_cost': p.auto_cost,
    })


def _build_leaderboard(user):
    profiles = _group_leaderboard(user)
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
                'level': pr.level,
                'stage_emoji': pr.stage[1],
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
    """Объединённый эндпоинт: state + leaderboard за один запрос."""
    user = request.user

    p = _profile(user)
    p.apply_auto()
    state = _serialize_profile(p)

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
        'level': p.level,
        'stage_emoji': p.stage[1],
        'stage_name': p.stage[0],
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