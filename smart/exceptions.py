"""DRF 统一异常处理。

为什么需要它
------------
DRF 默认把认证失败返回成 ``{"detail": "身份认证信息未提供。"}``，没有 ``code`` 字段。
而前端 ``utils/request.js`` 靠 ``res.data.code === 401`` 判断「登录已过期，跳回登录页」，
拿不到 code 就只会弹一句「请求失败」，用户卡在原页面不知道该重新登录。

所以把所有异常都归一成 ``{code, msg}`` 结构，与业务接口的成功响应保持同构。
"""

from __future__ import annotations

import logging

from django.http import JsonResponse
from rest_framework import status
from rest_framework.exceptions import NotAuthenticated, AuthenticationFailed, PermissionDenied
from rest_framework.views import exception_handler

logger = logging.getLogger(__name__)

# DRF 异常 -> 业务 code
# 401 保留原值，因为前端专门判断这个值
CODE_MAP = {
    NotAuthenticated: 401,
    AuthenticationFailed: 401,
    PermissionDenied: 403,
}


def custom_exception_handler(exc, context):
    """把 DRF 的异常响应改成 ``{code, msg}``。

    只改写认证/权限类异常的**响应体结构**，HTTP 状态码保持 DRF 的语义
    （401 仍是 401）。因为前端 ``request.js`` 对 statusCode !== 200 走 http 分支，
    这一步不改的话前端拿到的仍是 reject，只是 msg 变成中文了。

    其余异常交给 DRF 默认处理，不做过度包装 —— 避免掩盖真正的 500。
    """
    response = exception_handler(exc, context)

    if response is None:
        # 未被 DRF 处理的异常（编程错误等），记日志后原样抛给 Django
        logger.exception('未处理异常: %s %s', context.get('request'), exc)
        return None

    biz_code = CODE_MAP.get(type(exc))
    if biz_code is None:
        # 其他 DRF 异常（校验失败、404、405 等）用 HTTP 状态码当业务码
        biz_code = response.status_code

    # 从 detail 里取一句给人看的话
    detail = response.data
    if isinstance(detail, dict):
        msg = detail.get('detail') or detail.get('msg')
        if not msg:
            # 校验错误是 {field: [msg, ...]} 结构
            for value in detail.values():
                if isinstance(value, (list, tuple)) and value:
                    msg = str(value[0])
                    break
                if isinstance(value, str):
                    msg = value
                    break
    else:
        msg = str(detail)

    if not msg:
        msg = '请求失败'

    # 认证失败统一换成人话，不暴露 JWT 库的英文 internals
    if biz_code == 401 and isinstance(msg, str) and (
        'token' in msg.lower() or '认证' in msg
    ):
        msg = '登录已过期，请重新登录'

    response.data = {'code': biz_code, 'msg': str(msg)}
    return response
