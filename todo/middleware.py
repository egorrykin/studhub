"""Rate-limit и античит."""

from django.core.cache import cache
from django.http import HttpResponse, JsonResponse


class RateLimitMiddleware:
    USER_LIMIT = 900
    USER_WINDOW = 60
    ANON_LIMIT = 120
    ANON_WINDOW = 60
    API_LIMIT = 180
    API_WINDOW = 1
    LOGIN_LIMIT = 15
    LOGIN_WINDOW = 60

    SKIP_PREFIXES = ('/static/', '/media/')

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        path = request.path
        if any(path.startswith(p) for p in self.SKIP_PREFIXES):
            return self.get_response(request)

        ip = self._client_ip(request)
        is_auth = getattr(request.user, 'is_authenticated', False)

        if path.startswith('/login/'):
            if not self._check(f'rl_login:{ip}', self.LOGIN_LIMIT, self.LOGIN_WINDOW):
                return HttpResponse(
                    'Слишком много попыток входа. Подождите минуту.',
                    status=429, content_type='text/plain; charset=utf-8'
                )
            return self.get_response(request)

        if path.startswith('/api/'):
            key = f'rl_api_u:{request.user.pk}' if is_auth else f'rl_api_ip:{ip}'
            if not self._check(key, self.API_LIMIT, self.API_WINDOW):
                return JsonResponse({'ok': False, 'error': 'slow_down'}, status=429)
            return self.get_response(request)

        if is_auth:
            key = f'rl_g_u:{request.user.pk}'
            limit, window = self.USER_LIMIT, self.USER_WINDOW
        else:
            key = f'rl_g_ip:{ip}'
            limit, window = self.ANON_LIMIT, self.ANON_WINDOW

        if not self._check(key, limit, window):
            return HttpResponse(
                'Слишком много запросов. Попробуйте через минуту.',
                status=429, content_type='text/plain; charset=utf-8'
            )

        return self.get_response(request)

    @staticmethod
    def _check(key, limit, window):
        try:
            count = cache.incr(key)
        except ValueError:
            cache.add(key, 1, window)
            return True
        return count <= limit

    @staticmethod
    def _client_ip(request):
        xff = request.META.get('HTTP_X_FORWARDED_FOR')
        if xff:
            return xff.split(',')[0].strip()
        return request.META.get('REMOTE_ADDR', '0.0.0.0')