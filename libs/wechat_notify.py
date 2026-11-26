"""微信订阅消息（小程序「服务通知」）。

为什么单独一个模块
------------------
access_token 是全局限流资源（每天 2000 次获取、有效期 7200 秒），
必须集中缓存与刷新。分散到各业务里各调各的，很快就会撞上
「token 失效 / 获取过于频繁」这类线上故障。

**不要被「长期订阅」误导**
--------------------------
长期订阅（一次授权、永久推送）只向政务民生、医疗、交通、金融、教育等
**线下公共服务**的少数二级类目开放，个人主体基本申请不到。
本项目当前是个人主体，但按「未来移植到公司主体、以教育类目申请」来设计，
所以接口按标准订阅消息写，不降级、不做站内兜底。

一次性订阅的语义必须记清：**家长点一次「允许」，你只能给他发一条。**
发完额度就归零，下次要再推得让家长重新点。因此前端要在
「查看课表 / 查看课时」这类高意愿时刻去要授权，而不是一进小程序就弹
（微信明令禁止，会封消息能力）。

发送失败不能影响签到
--------------------
签到是**账务操作**，通知是**附加动作**。家长没订阅、token 过期、
模板被删，都不该让老师看到「签到失败」。
所以 ``notify_checkin`` 永不抛异常，只记日志。
"""

from __future__ import annotations

import logging
import time

import requests
from django.conf import settings
from django.core.cache import cache

logger = logging.getLogger(__name__)

# 微信接口地址
TOKEN_URL = 'https://api.weixin.qq.com/cgi-bin/token'
SEND_URL = 'https://api.weixin.qq.com/cgi-bin/message/subscribe/send'

# 连接 3 秒、读取 5 秒。微信偶发慢响应，但不能拖死签到请求。
REQUEST_TIMEOUT = (3, 5)

# access_token 的缓存 key。微信给的是 7200 秒，
# 这里提前 5 分钟过期，避免「刚好在第 7199 秒拿到一个马上失效的 token」。
TOKEN_CACHE_KEY = 'wechat:access_token'
TOKEN_SAFETY_MARGIN = 300


class WeChatNotifyError(Exception):
    """发送订阅消息失败。调用方（notify_checkin）会吞掉它，只记日志。"""

    def __init__(self, errcode: int, errmsg: str = ''):
        self.errcode = errcode
        self.errmsg = errmsg
        super().__init__(f'[{errcode}] {errmsg}')


def get_access_token(force_refresh: bool = False) -> str:
    """取 access_token，优先走缓存。

    Args:
        force_refresh: 忽略缓存强制重取。收到 40001（token 无效）时用。

    Raises:
        RuntimeError: AppID/AppSecret 未配置。
        WeChatNotifyError: 微信返回了 errcode。
        requests.RequestException: 网络问题。
    """
    if not force_refresh:
        cached = cache.get(TOKEN_CACHE_KEY)
        if cached:
            return cached

    appid = getattr(settings, 'WECHAT_APPID', '')
    secret = getattr(settings, 'WECHAT_APPSECRET', '')
    if not appid or not secret:
        raise RuntimeError('WECHAT_APPID / WECHAT_APPSECRET 未配置')

    resp = requests.get(
        TOKEN_URL,
        params={
            'grant_type': 'client_credential',
            'appid': appid,
            'secret': secret,
        },
        timeout=REQUEST_TIMEOUT,
    )
    resp.raise_for_status()

    try:
        data = resp.json()
    except ValueError:
        logger.error('获取 access_token 返回非 JSON：%s', resp.text[:200])
        raise WeChatNotifyError(-1, 'invalid json')

    if data.get('errcode'):
        # 注意：不要把 secret 写进日志
        logger.error('获取 access_token 失败 errcode=%s errmsg=%s',
                     data.get('errcode'), data.get('errmsg'))
        raise WeChatNotifyError(int(data['errcode']), data.get('errmsg', ''))

    token = data.get('access_token')
    if not token:
        raise WeChatNotifyError(-1, 'no access_token in response')

    expires_in = int(data.get('expires_in', 7200))
    ttl = max(60, expires_in - TOKEN_SAFETY_MARGIN)
    cache.set(TOKEN_CACHE_KEY, token, timeout=ttl)
    logger.info('access_token 已刷新，缓存 %s 秒', ttl)
    return token


def send_subscribe_message(
    openid: str,
    template_id: str,
    page: str,
    data: dict,
    retry_on_token_error: bool = True,
) -> None:
    """向一个用户发一条订阅消息。

    Args:
        openid: 接收人的 openid。
        template_id: 公众平台「订阅消息」里选的模板 id。
        page: 用户点通知后跳转的小程序页面，如 ``pages/hours/hours``。
            不能以 ``/`` 开头（微信要求）。
        data: 模板字段，形如 ``{'thing1': {'value': '张小明'}}``。
            键名必须与模板定义完全一致，value 必须是字符串且不超长。
        retry_on_token_error: 收到 token 失效类错误时清缓存重试一次。

    Raises:
        WeChatNotifyError: 微信返回了 errcode。
        requests.RequestException: 网络问题。
    """
    if not openid or not template_id:
        # 没 openid（纯手机号登录过但没走过微信授权）或没配模板，
        # 都属于「这条通知发不出去」，不是错误，直接跳过。
        logger.info('跳过订阅消息：openid 或 template_id 为空')
        return

    token = get_access_token()

    payload = {
        'touser': openid,
        'template_id': template_id,
        'page': page,
        'data': data,
        # 开发版/体验版要用这个字段指定跳转版本；
        # 正式版留空即可（不传该字段）。
        'miniprogram_state': getattr(settings, 'WECHAT_MINIPROGRAM_STATE', ''),
        'lang': 'zh_CN',
    }

    def _post(tk: str):
        return requests.post(
            SEND_URL,
            params={'access_token': tk},
            json=payload,
            timeout=REQUEST_TIMEOUT,
        )

    resp = _post(token)
    resp.raise_for_status()
    result = resp.json()

    errcode = int(result.get('errcode', 0) or 0)

    # 40001/42001：token 无效或过期。清缓存重试一次 ——
    # 可能是别处刷新了 token 导致本地这份失效。
    if errcode in (40001, 42001) and retry_on_token_error:
        logger.warning('订阅消息 token 失效（%s），刷新后重试', errcode)
        cache.delete(TOKEN_CACHE_KEY)
        new_token = get_access_token(force_refresh=True)
        resp = _post(new_token)
        resp.raise_for_status()
        result = resp.json()
        errcode = int(result.get('errcode', 0) or 0)

    if errcode:
        # 43101：用户未订阅或已用完额度 —— 这是最常见的「失败」，
        # 属于正常业务状态，用 info 记一笔便于统计授权率即可。
        if errcode == 43101:
            logger.info('用户未订阅/额度已用完，跳过通知 openid=%s...', openid[:8])
            return
        raise WeChatNotifyError(errcode, result.get('errmsg', ''))

    logger.info('订阅消息已发送 openid=%s... template=%s', openid[:8], template_id)


def notify_checkin(enrollment, lesson_date, session, remain: int) -> bool:
    """签到成功后通知家长。**永不抛异常。**

    Args:
        enrollment: ``Enrollment`` 实例（取 student / 家长 openid）。
        lesson_date: 上课日期（``date``）。
        session: ``ClassSession`` 实例（取班级名与时间）。
        remain: 签到后剩余课时。

    Returns:
        True 表示确实发出去了；False 表示跳过或失败（均只记日志）。
    """
    template_id = getattr(settings, 'WECHAT_TEMPLATE_CHECKIN', '')
    if not template_id:
        logger.info('未配置 WECHAT_TEMPLATE_CHECKIN，跳过签到通知')
        return False

    # 找这个孩子的家长。一个孩子可能绑多个家长，逐个发 ——
    # 爸妈都该知道孩子到了。
    parents = [
        ps.parent for ps in
        enrollment.student.parent_students.select_related('parent')
        if ps.parent.openid
    ]
    if not parents:
        logger.info('学生 %s 没有绑定带 openid 的家长，跳过通知', enrollment.student.name)
        return False

    # 模板字段名必须与公众平台里选的模板完全一致。
    # 这里用「上课提醒」类模板的常见字段名，实际以你选的模板为准，
    # 不一致会报 47003（参数不匹配）。
    data = {
        'thing1': {'value': _clip(enrollment.student.name, 20)},
        'thing2': {'value': _clip(session.course_class.name, 20)},
        'time3': {'value': _fmt_time(lesson_date, session)},
        'thing4': {'value': _clip(f'已签到，剩余 {remain} 课时', 20)},
    }

    ok = False
    for parent in parents:
        try:
            send_subscribe_message(
                openid=parent.openid,
                template_id=template_id,
                page='pages/hours/hours',
                data=data,
            )
            ok = True
        except Exception as exc:
            # 通知失败绝不能冒泡 —— 签到是账务操作，已经成功了。
            logger.warning('签到通知发送失败 parent=%s：%s', parent.id, exc)

    return ok


def _clip(text: str, limit: int) -> str:
    """微信对 thing 类字段有长度限制，超了直接报 47003。

    截断而不是报错：宁可让家长看到「Scratch创意B班…」，
    也不要因为班级名多两个字就整条通知发不出去。
    """
    text = str(text or '')
    return text if len(text) <= limit else text[: limit - 1] + '…'


def _fmt_time(lesson_date, session) -> str:
    """拼成「2026年11月27日 15:30」。time 类字段要求这个格式。"""
    return (
        f'{lesson_date.year}年{lesson_date.month}月{lesson_date.day}日 '
        f'{session.start_time.strftime("%H:%M")}'
    )
