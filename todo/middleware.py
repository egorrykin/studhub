"""Простейший rate-limit по IP и пользователю.

Внимание: использует LocMemCache — работает в рамках одного процесса.
Для продакшена заменить на Redis / Memcached.
"""

from django.core.cache import cache
from django.http import HttpResponse, JsonResponse


class RateLimitMiddleware:
    # Обычные запросы
    GLOBAL_LIMIT = 200          # 200 запросов
    GLOBAL_WINDOW = 60          # в минуту на IP

    # API-другалька
    API_LIMIT = 30              # 30 запросов
    API_WINDOW = 1              # в секунду на пользователя

    # Логин
    LOGIN_LIMIT = 15            # 15 попыток
    LOGIN_WINDOW = 60           # в минуту на IP

    SKIP_PREFIXES = ('/static/', '/media/')

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        path = request.path
        if any(path.startswith(p) for p in self.SKIP_PREFIXES):
            return self.get_response(request)

        ip = self._client_ip(request)

        # общий лимит
        if not self._check(f'rl_g:{ip}', self.GLOBAL_LIMIT, self.GLOBAL_WINDOW):
            return HttpResponse(
                'Слишком много запросов. Попробуйте через минуту.',
                status=429,
                content_type='text/plain; charset=utf-8',
            )

        # лимит на API другалька
        if path.startswith('/api/clicker/'):
            uid = request.user.pk if getattr(request.user, 'is_authenticated', False) else ip
            if not self._check(f'rl_api:{uid}', self.API_LIMIT, self.API_WINDOW):
                return JsonResponse({'ok': False, 'error': 'slow_down'}, status=429)

        # лимит на попытки логина
        if path.startswith('/login/'):
            if not self._check(f'rl_login:{ip}', self.LOGIN_LIMIT, self.LOGIN_WINDOW):
                return HttpResponse(
                    'Слишком много попыток входа. Подождите минуту.',
                    status=429,
                    content_type='text/plain; charset=utf-8',
                )

        return self.get_response(request)

    @staticmethod
    def _check(key, limit, window):
        count = cache.get(key, 0)
        if count >= limit:
            return False
        cache.set(key, count + 1, window)
        return True

    @staticmethod
    def _client_ip(request):
        return request.META.get('REMOTE_ADDR', '0.0.0.0')