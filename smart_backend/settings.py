"""
Django settings for smart_backend project.

未蓝科技 · 智慧教育 —— 后端配置

敏感配置（SECRET_KEY / 微信 AppSecret）从 .env 读取，不入库。
首次使用：cp .env.example .env，然后填入真实值。
"""

from pathlib import Path
import os
from datetime import timedelta

from dotenv import load_dotenv

# Build paths inside the project like this: BASE_DIR / 'subdir'.
BASE_DIR = Path(__file__).resolve().parent.parent

# 加载 .env（文件不存在时静默跳过，取下面的默认值）
load_dotenv(BASE_DIR / '.env')


def env_bool(name, default=False):
    """把 'True' / '1' / 'yes' 转成布尔值"""
    val = os.getenv(name)
    if val is None:
        return default
    return val.strip().lower() in ('1', 'true', 'yes', 'on')


def env_list(name, default=None):
    """逗号分隔的字符串转成列表"""
    val = os.getenv(name)
    if not val:
        return default or []
    return [item.strip() for item in val.split(',') if item.strip()]


# ============================================================
# 安全配置
# ============================================================

# 生产环境必须在 .env 里换成随机长字符串；缺失时用不安全值兜底，仅够本地开发
SECRET_KEY = os.getenv(
    'DJANGO_SECRET_KEY',
    'django-insecure-dev-only-key-please-set-DJANGO_SECRET_KEY-in-env'
)

DEBUG = env_bool('DJANGO_DEBUG', True)

# 开发期允许本机访问；上线时在 .env 里写真实域名
ALLOWED_HOSTS = env_list('DJANGO_ALLOWED_HOSTS', ['127.0.0.1', 'localhost'])

# Application definition

INSTALLED_APPS = [
    'simpleui',
    'django.contrib.admin',
    'django.contrib.auth',
    'django.contrib.contenttypes',
    'django.contrib.sessions',
    'django.contrib.messages',
    'django.contrib.staticfiles',
    'smart.apps.SmartConfig',
    'rest_framework'
]

# ============================================================
# DRF 与 JWT
# ============================================================

REST_FRAMEWORK = {
    # 默认不强制登录：首页 banner/notice 要匿名可访问。
    # 需要登录的接口在 view 里显式写 permission_classes。
    'DEFAULT_AUTHENTICATION_CLASSES': [
        'smart.auth.MyJSONWebTokenAuthentication',
    ],
    'DEFAULT_PERMISSION_CLASSES': [
        'rest_framework.permissions.AllowAny',
    ],
    'UNAUTHENTICATED_USER': None,
    # 统一把异常响应归一成 {code, msg}，否则前端拿不到 code=401 无法触发重新登录
    'EXCEPTION_HANDLER': 'smart.exceptions.custom_exception_handler',
}

SIMPLE_JWT = {
    # token 有效期 30 天。家长端不是高频操作，频繁掉登录体验很差；
    # 过期后前端会收到 code=401 并跳回登录页。
    'ACCESS_TOKEN_LIFETIME': timedelta(days=30),
    'REFRESH_TOKEN_LIFETIME': timedelta(days=60),
    # 本项目不用 refresh 机制，过期就重新登录（微信静默登录成本极低）
    'ROTATE_REFRESH_TOKENS': False,
    'AUTH_HEADER_TYPES': ('Bearer',),
}

# ============================================================
# 缓存（验证码存这里）
# ============================================================

# 开发期用内存缓存，进程重启验证码就失效 —— 可接受。
# 上线换 Redis 只需改这一处，代码里用的是 django.core.cache 标准接口。
CACHES = {
    'default': {
        'BACKEND': 'django.core.cache.backends.locmem.LocMemCache',
        'LOCATION': 'weilan-sms',
    }
}

# ============================================================
# 短信验证码
# ============================================================

# 开发期固定验证码。个人主体拿不到短信通道，且这是本地联调阶段。
# 上线前必须置为 False 并接入真实短信服务商，否则任何人 8888 就能登录。
SMS_DEV_FIXED_CODE = env_bool('SMS_DEV_FIXED_CODE', True)
SMS_DEV_CODE = os.getenv('SMS_DEV_CODE', '8888')

# 验证码有效期（秒）与重发间隔（秒）
SMS_CODE_TTL = 300
SMS_RESEND_INTERVAL = 60

# 开发期放宽：固定码模式下允许重复使用同一个验证码、且不限制重发间隔。
# 否则每测一次登录就要等 60 秒，联调根本没法进行。
# 仅当 SMS_DEV_FIXED_CODE=True 时生效，上线置 False 后自动恢复严格模式。
SMS_DEV_RELAX = env_bool('SMS_DEV_RELAX', True)

# 单个验证码允许的最大试错次数，防止暴力枚举 6 位数字
SMS_MAX_VERIFY_ATTEMPTS = 5

MIDDLEWARE = [
    'django.middleware.security.SecurityMiddleware',
    'django.contrib.sessions.middleware.SessionMiddleware',
    'django.middleware.common.CommonMiddleware',
    'django.middleware.csrf.CsrfViewMiddleware',
    'django.contrib.auth.middleware.AuthenticationMiddleware',
    'django.contrib.messages.middleware.MessageMiddleware',
    'django.middleware.clickjacking.XFrameOptionsMiddleware',
]

ROOT_URLCONF = 'smart_backend.urls'

TEMPLATES = [
    {
        'BACKEND': 'django.template.backends.django.DjangoTemplates',
        # 当前项目纯 API + Admin，无自定义模板目录
        'DIRS': [],
        'APP_DIRS': True,
        'OPTIONS': {
            'context_processors': [
                'django.template.context_processors.request',
                'django.contrib.auth.context_processors.auth',
                'django.contrib.messages.context_processors.messages',
            ],
        },
    },
]

WSGI_APPLICATION = 'smart_backend.wsgi.application'

# Database
# https://docs.djangoproject.com/en/6.0/ref/settings/#databases

DATABASES = {
    'default': {
        'ENGINE': 'django.db.backends.sqlite3',
        'NAME': BASE_DIR / 'db.sqlite3',
    }
}

# Password validation
# https://docs.djangoproject.com/en/6.0/ref/settings/#auth-password-validators

AUTH_PASSWORD_VALIDATORS = [
    {
        'NAME': 'django.contrib.auth.password_validation.UserAttributeSimilarityValidator',
    },
    {
        'NAME': 'django.contrib.auth.password_validation.MinimumLengthValidator',
    },
    {
        'NAME': 'django.contrib.auth.password_validation.CommonPasswordValidator',
    },
    {
        'NAME': 'django.contrib.auth.password_validation.NumericPasswordValidator',
    },
]

# Internationalization
# https://docs.djangoproject.com/en/6.0/topics/i18n/

LANGUAGE_CODE = 'zh-hans'

TIME_ZONE = 'Asia/Shanghai'

USE_I18N = True
USE_TZ = False

# Static files (CSS, JavaScript, Images)
# https://docs.djangoproject.com/en/6.0/howto/static-files/

STATIC_URL = 'static/'

# 开启media访问配置
MEDIA_URL = '/media/'
MEDIA_ROOT = os.path.join(BASE_DIR, 'media')

# ============================================================
# 微信小程序
# ============================================================

# AppID / AppSecret 从 .env 读取。
# 缺失时不要在启动阶段报错——本地开发还没配密钥时也要能跑通其他接口，
# 真正调用 code2session 的地方（阶段 1.3）再做校验并给出明确提示。
WECHAT_APPID = os.getenv('WECHAT_APPID', '')
WECHAT_APPSECRET = os.getenv('WECHAT_APPSECRET', '')

# ============================================================
# 公开访问地址
# ============================================================

# 用于拼接图片等资源的完整 URL（小程序端无法使用相对路径）
PUBLIC_BASE_URL = os.getenv('PUBLIC_BASE_URL', 'http://127.0.0.1:8000').rstrip('/')

# ============================================================
# 日志
# ============================================================

# 只在 DEBUG 时把业务日志打到控制台。
# 生产环境应改为写文件或接入日志采集，避免 stdout 丢失。
if DEBUG:
    LOGGING = {
        'version': 1,
        'disable_existing_loggers': False,
        'formatters': {
            'simple': {
                'format': '[{levelname}] {asctime} {name}: {message}',
                'style': '{',
                'datefmt': '%H:%M:%S',
            },
        },
        'handlers': {
            'console': {
                'class': 'logging.StreamHandler',
                'formatter': 'simple',
            },
        },
        'root': {
            'handlers': ['console'],
            'level': 'INFO',
        },
        # 微信接口封装的日志单独给 INFO，方便排查登录问题
        'loggers': {
            'libs.wechat': {
                'handlers': ['console'],
                'level': 'INFO',
                'propagate': False,
            },
        },
    }
