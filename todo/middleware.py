"""Rate-limit по IP и пользователю.

Важно: используется LocMemCache — работает в рамках одного процесса.
При `--workers 3` Gunicorn запускает 3 независимых процесса, у каждого свой кеш,
поэтому фактический лимит может быть в 3 раза выше номинального.
Для строгого лимита в продакшене — заменить LocMemCache на Redis (см. settings.py).

Логика:
    * Логин — по IP, 15/мин (защита от брутфорса)
    * API-эндпоинты (/api/) — по user_id, 120/сек (для кликера)
    * Обычные страницы:
        - авторизованные — 600/мин по user_id
        - анонимные     — 120/мин по IP
    * Статика и медиа не считаются вообще
"""

from django.core.cache import cache
from django.http import HttpResponse, JsonResponse


class RateLimitMiddleware:
    # Обычные страницы (HTML, шаблоны) — на авторизованного
    USER_LIMIT = 600
    USER_WINDOW = 60

    # Обычные страницы — на анонимного (по IP)
    ANON_LIMIT = 120
    ANON_WINDOW = 60

    # API кликера — на пользователя
    API_LIMIT = 120
    API_WINDOW = 1

    # Логин — на IP
    LOGIN_LIMIT = 15
    LOGIN_WINDOW = 60

    SKIP_PREFIXES = ('/static/', '/media/')

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        path = request.path

        # Пропускаем статику и медиа
        if any(path.startswith(p) for p in self.SKIP_PREFIXES):
            return self.get_response(request)

        ip = self._client_ip(request)
        is_auth = getattr(request.user, 'is_authenticated', False)

        # ─── Логин — отдельная защита по IP ─────────────────────
        if path.startswith('/login/'):
            if not self._check(f'rl_login:{ip}', self.LOGIN_LIMIT, self.LOGIN_WINDOW):
                return HttpResponse(
                    'Слишком много попыток входа. Подождите минуту.',
                    status=429,
                    content_type='text/plain; charset=utf-8',
                )
            return self.get_response(request)

        # ─── API — по пользователю (или IP для анонимных) ───────
        if path.startswith('/api/'):
            if is_auth:
                key = f'rl_api_u:{request.user.pk}'
            else:
                key = f'rl_api_ip:{ip}'

            if not self._check(key, self.API_LIMIT, self.API_WINDOW):
                return JsonResponse(
                    {'ok': False, 'error': 'slow_down'},
                    status=429,
                )
            return self.get_response(request)

        # ─── Обычные страницы ──────────────────────────────────
        if is_auth:
            key = f'rl_g_u:{request.user.pk}'
            limit, window = self.USER_LIMIT, self.USER_WINDOW
        else:
            key = f'rl_g_ip:{ip}'
            limit, window = self.ANON_LIMIT, self.ANON_WINDOW

        if not self._check(key, limit, window):
            return HttpResponse(
                'Слишком много запросов. Попробуйте через минуту.',
                status=429,
                content_type='text/plain; charset=utf-8',
            )

        return self.get_response(request)

    @staticmethod
    def _check(key, limit, window):
        """
        Проверяет и инкрементирует счётчик.

        Использует cache.incr(), который НЕ сбрасывает timeout —
        окно остаётся фиксированным. Это исправляет старую проблему,
        когда при каждом запросе таймаут продлевался и блокировка
        становилась «вечной».
        """
        try:
            count = cache.incr(key)
        except ValueError:
            # Ключа нет — создаём с timeout=window
            cache.add(key, 1, window)
            return True

        return count <= limit

    @staticmethod
    def _client_ip(request):
        # Учитываем X-Forwarded-For (Nginx прокидывает его)
        xff = request.META.get('HTTP_X_FORWARDED_FOR')
        if xff:
            return xff.split(',')[0].strip()
        return request.META.get('REMOTE_ADDR', '0.0.0.0')