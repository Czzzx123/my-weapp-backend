"""微信小程序服务端接口封装。

目前只负责一件事：code2session（用 wx.login 的临时 code 换 openid）。

为什么必须放在后端做
--------------------
换 openid 需要 AppSecret。小程序代码包会被反编译，密钥放前端等于公开，
任何人都能拿去冒充本小程序调用微信接口、消耗调用额度。
所以前端只拿一次性 code，密钥只存在于服务器。

微信接口的两个坑
----------------
1. **失败也返回 HTTP 200**。靠 JSON 里的 ``errcode`` 判断，不能只看状态码，
   否则"code 无效"会被当成"登录成功"。
2. **code 是一次性的**。重复使用返回 40163，5 分钟过期返回 40029。
   所以拿到 openid 后必须立刻落库/换发自家 token，不能存着以后再用。
"""

from __future__ import annotations

import logging

import requests
from django.conf import settings

logger = logging.getLogger(__name__)

# 微信 code2session 接口地址
JSCODE2SESSION_URL = 'https://api.weixin.qq.com/sns/jscode2session'

# 网络超时（连接, 读取），单位秒。
# 微信偶发慢响应，但不能让它拖死我们的接口 —— 家长端最多等 3 秒就该有结果。
REQUEST_TIMEOUT = (3, 5)

# errcode -> 给人看的中文提示。
# 只翻译家长/前端需要感知的，其余统一走兜底文案，避免暴露内部细节。
ERRCODE_MESSAGES = {
    40013: '小程序 AppID 配置有误，请联系管理员',
    40029: '登录凭证已失效，请重新进入小程序',
    40125: '小程序密钥配置有误，请联系管理员',
    40163: '登录凭证已被使用，请重新进入小程序',
    40226: '当前账号存在风险，暂时无法登录',
    45011: '操作太频繁了，请稍后再试',
    -1: '微信服务繁忙，请稍后再试',
}


class WeChatError(Exception):
    """微信接口返回了 errcode，属于**业务失败**。

    与网络故障区分开：业务失败通常要求前端重新 ``wx.login()`` 换个 code，
    而网络故障可以原样重试。调用方据此决定给用户看什么提示。

    Attributes:
        errcode: 微信返回的错误码
        errmsg: 微信返回的英文原文（写日志用）
        message: 给用户看的中文提示
    """

    def __init__(self, errcode: int, errmsg: str = ''):
        self.errcode = errcode
        self.errmsg = errmsg
        self.message = ERRCODE_MESSAGES.get(errcode, f'微信登录失败（{errcode}）')
        super().__init__(self.message)

    def __str__(self) -> str:
        return f'[{self.errcode}] {self.errmsg}'


class WeChatConfigError(Exception):
    """AppID / AppSecret 未配置。属于部署问题，不是用户操作问题。"""

    pass


def code2session(code: str) -> dict:
    """用前端 ``wx.login()`` 拿到的 code 换取 openid。

    Args:
        code: 前端传来的临时登录凭证，5 分钟有效，仅能使用一次。

    Returns:
        ``{'openid': str, 'session_key': str, 'unionid': str | None}``

    Raises:
        WeChatConfigError: AppID / AppSecret 没配。
        WeChatError: 微信返回了 errcode（code 无效、频率超限等）。
        requests.RequestException: 网络超时或连接失败，调用方可重试。
    """
    appid = getattr(settings, 'WECHAT_APPID', '')
    secret = getattr(settings, 'WECHAT_APPSECRET', '')

    # 在真正需要密钥的地方校验，而不是启动时 —— 本地开发没配密钥也要能跑其他接口
    if not appid or not secret:
        raise WeChatConfigError(
            'WECHAT_APPID / WECHAT_APPSECRET 未配置，请检查 .env 文件'
        )

    if not code:
        raise WeChatError(40029, 'empty code')

    params = {
        'appid': appid,
        'secret': secret,
        'js_code': code,
        'grant_type': 'authorization_code',
    }

    # 注意：日志里绝不能出现 secret 和完整 code
    logger.info('code2session 请求发起 code=%s...', code[:6])

    resp = requests.get(JSCODE2SESSION_URL, params=params, timeout=REQUEST_TIMEOUT)
    resp.raise_for_status()

    # 微信部分错误会返回 text/plain，不能无条件 resp.json()
    try:
        data = resp.json()
    except ValueError:
        logger.error('code2session 返回非 JSON：%s', resp.text[:200])
        raise WeChatError(-1, 'invalid json response')

    errcode = data.get('errcode')
    if errcode:
        # errcode 可能是字符串，统一转 int 便于比对
        errcode = int(errcode)
        errmsg = data.get('errmsg', '')
        logger.warning('code2session 失败 errcode=%s errmsg=%s', errcode, errmsg)
        raise WeChatError(errcode, errmsg)

    openid = data.get('openid')
    if not openid:
        # 极少见：微信返回 200 但没有 openid
        logger.error('code2session 未返回 openid：%s', data)
        raise WeChatError(-1, 'no openid in response')

    logger.info('code2session 成功 openid=%s...', openid[:8])

    return {
        'openid': openid,
        'session_key': data.get('session_key', ''),
        # unionid 只有小程序绑定了开放平台账号才返回，本项目个人主体拿不到
        'unionid': data.get('unionid'),
    }
