"""
Django settings for config project.
"""

import os
from pathlib import Path

from dotenv import load_dotenv
import dj_database_url

# Загружаем переменные из .env
load_dotenv()

BASE_DIR = Path(__file__).resolve().parent.parent


# ═══════════════════════════════════════════════════════════════
# Безопасность
# ═══════════════════════════════════════════════════════════════

SECRET_KEY = os.getenv(
    'DJANGO_SECRET_KEY',
    'django-insecure-dev-only-CHANGE-ME-IN-PRODUCTION-xxxxxxxxxxxx'
)

DEBUG = os.getenv('DJANGO_DEBUG', 'True').lower() == 'true'

ALLOWED_HOSTS = [
    h.strip() for h in os.getenv(
        'DJANGO_ALLOWED_HOSTS', 'localhost,127.0.0.1'
    ).split(',') if h.strip()
]

CSRF_TRUSTED_ORIGINS = [
    o.strip() for o in os.getenv('DJANGO_CSRF_TRUSTED_ORIGINS', '').split(',')
    if o.strip()
]


# ═══════════════════════════════════════════════════════════════
# Приложения
# ═══════════════════════════════════════════════════════════════

INSTALLED_APPS = [
    'django.contrib.admin',
    'django.contrib.auth',
    'django.contrib.contenttypes',
    'django.contrib.sessions',
    'django.contrib.messages',
    'django.contrib.staticfiles',
    'rest_framework',
    'todo',
]

MIDDLEWARE = [
    'django.middleware.security.SecurityMiddleware',
    'django.contrib.sessions.middleware.SessionMiddleware',
    'django.middleware.common.CommonMiddleware',
    'django.middleware.csrf.CsrfViewMiddleware',
    'django.contrib.auth.middleware.AuthenticationMiddleware',
    'django.contrib.messages.middleware.MessageMiddleware',
    'django.middleware.clickjacking.XFrameOptionsMiddleware',
    'todo.middleware.RateLimitMiddleware',
]

ROOT_URLCONF = 'config.urls'

TEMPLATES = [
    {
        'BACKEND': 'django.template.backends.django.DjangoTemplates',
        'DIRS': [],
        'APP_DIRS': True,
        'OPTIONS': {
            'context_processors': [
                'django.template.context_processors.debug',
                'django.template.context_processors.request',
                'django.contrib.auth.context_processors.auth',
                'django.contrib.messages.context_processors.messages',
                'todo.context_processors.group_context',
            ],
        },
    },
]

WSGI_APPLICATION = 'config.wsgi.application'


# ═══════════════════════════════════════════════════════════════
# База данных
# ═══════════════════════════════════════════════════════════════

if os.getenv('DATABASE_URL'):
    DATABASES = {
        'default': dj_database_url.config(
            default=os.getenv('DATABASE_URL'),
            conn_max_age=600,
            conn_health_checks=True,
        )
    }
else:
    # Локальный SQLite (dev)
    DATABASES = {
        'default': {
            'ENGINE': 'django.db.backends.sqlite3',
            'NAME': BASE_DIR / 'db.sqlite3',
            'OPTIONS': {'timeout': 20},
        }
    }


# ═══════════════════════════════════════════════════════════════
# Аутентификация
# ═══════════════════════════════════════════════════════════════

AUTH_USER_MODEL = 'todo.User'

AUTH_PASSWORD_VALIDATORS = [
    {'NAME': 'django.contrib.auth.password_validation.UserAttributeSimilarityValidator'},
    {'NAME': 'django.contrib.auth.password_validation.MinimumLengthValidator',
     'OPTIONS': {'min_length': 6}},
    {'NAME': 'django.contrib.auth.password_validation.CommonPasswordValidator'},
    {'NAME': 'django.contrib.auth.password_validation.NumericPasswordValidator'},
]


# ═══════════════════════════════════════════════════════════════
# Локализация
# ═══════════════════════════════════════════════════════════════

LANGUAGE_CODE = 'ru-ru'
TIME_ZONE = 'Europe/Moscow'
USE_I18N = True
USE_TZ = True


# ═══════════════════════════════════════════════════════════════
# Статика и медиа
# ═══════════════════════════════════════════════════════════════

STATIC_URL = 'static/'
STATIC_ROOT = BASE_DIR / 'staticfiles'

MEDIA_URL = '/media/'
MEDIA_ROOT = BASE_DIR / 'media'

DEFAULT_AUTO_FIELD = 'django.db.models.BigAutoField'


# ═══════════════════════════════════════════════════════════════
# Авторизация
# ═══════════════════════════════════════════════════════════════

LOGIN_URL = 'login'
LOGIN_REDIRECT_URL = 'schedule'
LOGOUT_REDIRECT_URL = 'login'


# ═══════════════════════════════════════════════════════════════
# Загрузка файлов
# ═══════════════════════════════════════════════════════════════

FILE_UPLOAD_MAX_MEMORY_SIZE = 10 * 1024 * 1024
DATA_UPLOAD_MAX_MEMORY_SIZE = 100 * 1024 * 1024
DATA_UPLOAD_MAX_NUMBER_FILES = 20


# ═══════════════════════════════════════════════════════════════
# Кэш (для rate-limit)
# ═══════════════════════════════════════════════════════════════

CACHES = {
    'default': {
        'BACKEND': 'django.core.cache.backends.locmem.LocMemCache',
        'LOCATION': 'studhub',
        'TIMEOUT': 300,
    }
}


# ═══════════════════════════════════════════════════════════════
# Безопасность (Production)
# ═══════════════════════════════════════════════════════════════
# Все настройки ниже включаются ТОЛЬКО когда DEBUG=False.
# Локально их не трогаем, чтобы не сломать разработку.

if not DEBUG:
    # ─── HTTPS ───────────────────────────────────────────────
    # Редирект всего HTTP → HTTPS (W008)
    SECURE_SSL_REDIRECT = True

    # HSTS — браузер запомнит, что только HTTPS (W004)
    SECURE_HSTS_SECONDS = 31536000            # 1 год
    SECURE_HSTS_INCLUDE_SUBDOMAINS = True
    SECURE_HSTS_PRELOAD = True

    # ─── Cookies ─────────────────────────────────────────────
    SESSION_COOKIE_SECURE = True              # W012
    CSRF_COOKIE_SECURE = True                 # W016
    SESSION_COOKIE_HTTPONLY = True
    SESSION_COOKIE_SAMESITE = 'Lax'
    CSRF_COOKIE_SAMESITE = 'Lax'

    # ─── Прочее ──────────────────────────────────────────────
    SECURE_CONTENT_TYPE_NOSNIFF = True
    SECURE_BROWSER_XSS_FILTER = True
    SECURE_REFERRER_POLICY = 'same-origin'
    X_FRAME_OPTIONS = 'DENY'

    # Если стоит Nginx с proxy_pass — Django должен доверять его заголовкам
    SECURE_PROXY_SSL_HEADER = ('HTTP_X_FORWARDED_PROTO', 'https')

    # ─── Логирование ошибок ──────────────────────────────────
    LOGGING = {
        'version': 1,
        'disable_existing_loggers': False,
        'handlers': {
            'file': {
                'level': 'WARNING',
                'class': 'logging.FileHandler',
                'filename': BASE_DIR / 'logs' / 'django.log',
            },
        },
        'root': {
            'handlers': ['file'],
            'level': 'WARNING',
        },
    }

    # Создаём папку для логов
    (BASE_DIR / 'logs').mkdir(exist_ok=True)