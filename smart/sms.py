"""短信验证码的生成与校验。

为什么单独一个模块
------------------
验证码逻辑有 4 个约束（有效期内可用、同号限频、限试错次数、用完即焚），
塞进 view 里会让 view 变得很长且难测。集中在这里，view 只负责收发 HTTP。

存储用 Django cache 而不是建表
------------------------------
验证码是短命数据（5 分钟），不值得占一张表、不值得留历史记录。
用 django.core.cache 标准接口，开发期 LocMemCache，上线换 Redis 只改 settings 一处。
"""

from __future__ import annotations

import logging
import random

from django.conf import settings
from django.core.cache import cache

logger = logging.getLogger(__name__)

# cache key 前缀
_CODE_KEY = 'sms:code:{mobile}'
_COOLDOWN_KEY = 'sms:cooldown:{mobile}'
_ATTEMPT_KEY = 'sms:attempt:{mobile}'


def _ttl() -> int:
    return getattr(settings, 'SMS_CODE_TTL', 300)


def _resend_interval() -> int:
    return getattr(settings, 'SMS_RESEND_INTERVAL', 60)


def _max_attempts() -> int:
    return getattr(settings, 'SMS_MAX_VERIFY_ATTEMPTS', 5)


def _relaxed() -> bool:
    """开发期宽松模式：固定码 + 允许重复使用 + 不限重发。

    两个开关必须同时打开才宽松，避免有人只把 SMS_DEV_FIXED_CODE 关掉、
    却留下 SMS_DEV_RELAX=True 导致「验证码可复用」这个严重漏洞。
    """
    return (getattr(settings, 'SMS_DEV_FIXED_CODE', False)
            and getattr(settings, 'SMS_DEV_RELAX', False))


def generate_code() -> str:
    """生成 6 位数字验证码。

    开发期如果开启了 SMS_DEV_FIXED_CODE，直接返回固定码（默认 8888），
    省去每次登录都要去看后端控制台。
    """
    if getattr(settings, 'SMS_DEV_FIXED_CODE', False):
        return getattr(settings, 'SMS_DEV_CODE', '8888')
    return f'{random.randint(0, 999999):06d}'


def send_code(mobile: str) -> tuple[bool, str]:
    """为手机号生成并「发送」验证码。

    Returns:
        ``(True, '')`` 表示已发送；
        ``(False, 原因)`` 表示被限频拦下。
    """
    cooldown_key = _COOLDOWN_KEY.format(mobile=mobile)
    if cache.get(cooldown_key) and not _relaxed():
        left = _resend_interval()
        return False, f'发送太频繁，请 {left} 秒后再试'

    code = generate_code()
    cache.set(_CODE_KEY.format(mobile=mobile), code, timeout=_ttl())
    if not _relaxed():
        cache.set(cooldown_key, 1, timeout=_resend_interval())
    # 新码发出时重置试错计数
    cache.delete(_ATTEMPT_KEY.format(mobile=mobile))

    if getattr(settings, 'SMS_DEV_FIXED_CODE', False):
        logger.warning('【开发模式】验证码固定为 %s，手机号 %s —— 上线前必须关闭', code, mobile)
    else:
        # 真实短信通道未接入。接入后在这里调用服务商 SDK。
        logger.info('验证码已生成 mobile=%s code=%s（尚未接入真实短信，仅打印）', mobile, code)

    return True, ''


def verify_code(mobile: str, code: str) -> tuple[bool, str]:
    """校验验证码。校验成功后立即销毁，且不受重发冷却影响。

    开发期宽松模式下（SMS_DEV_FIXED_CODE + SMS_DEV_RELAX）：
    固定码不销毁、不计数，方便反复联调。上线关闭开关后自动严格。

    Returns:
        ``(True, '')`` 或 ``(False, 原因)``
    """
    if not code:
        return False, '请输入验证码'

    code_key = _CODE_KEY.format(mobile=mobile)
    attempt_key = _ATTEMPT_KEY.format(mobile=mobile)

    expected = cache.get(code_key)
    if not expected:
        # 开发期：没发过码也允许用固定码登录，省去每次都点「发送验证码」
        if _relaxed():
            expected = getattr(settings, 'SMS_DEV_CODE', '8888')
        else:
            return False, '验证码已失效，请重新获取'

    # 试错次数限制：防止有人对着 6 位数字暴力枚举
    if not _relaxed():
        attempts = cache.get(attempt_key, 0)
        if attempts >= _max_attempts():
            cache.delete(code_key)
            logger.warning('验证码试错超限，已作废 mobile=%s', mobile)
            return False, '验证码错误次数过多，请重新获取'

    if code != expected:
        if not _relaxed():
            attempts = cache.get(attempt_key, 0)
            cache.set(attempt_key, attempts + 1, timeout=_ttl())
        return False, '验证码不正确'

    # 校验通过：一次性使用，立即删除（开发期保留，方便重复登录）
    if not _relaxed():
        cache.delete(code_key)
        cache.delete(attempt_key)
    return True, ''


def clear_cooldown(mobile: str) -> None:
    """清掉重发冷却。仅在测试或需要强制重发时使用。"""
    cache.delete(_COOLDOWN_KEY.format(mobile=mobile))
