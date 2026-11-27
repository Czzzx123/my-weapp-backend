"""检查订阅消息模板配置是否正确 —— 不发送，只比对。

    python manage.py check_notify_templates

为什么需要这个命令
------------------
模板字段名（``thing1`` / ``time5`` / ``name10``）是微信在**你添加模板时**
临时生成的，没有任何规律，而且两个模板的 ``thing1`` 含义可能完全不同
（签到通知的 thing4 是课程名，上课提醒的 thing1 才是课程名）。

一旦字段名对不上，微信返回 **47003 参数不匹配**。这个错误码不告诉你是
哪个字段错了，只说「参数不匹配」—— 只能一个个猜。

更麻烦的是：**只有真的发一条才能触发校验**，而发送又要求家长已授权
（否则先撞 43101，根本走不到字段校验那一步）。所以「家长还没授权」
的开发期，字段名写错了也看不出来，等家长授权了才发现，那时已经上线。

这个命令直接从微信拉模板定义，和代码里构造的 data 做集合比对，
不需要家长授权，也不消耗订阅额度。
"""

from __future__ import annotations

import datetime
import re

import requests
from django.conf import settings
from django.core.management.base import BaseCommand

from libs import wechat_notify

TEMPLATE_URL = 'https://api.weixin.qq.com/wxaapi/newtmpl/gettemplate'


class Command(BaseCommand):
    help = '比对代码里的模板字段名与微信实际模板是否一致（只读，不发送）'

    def handle(self, *args, **options):
        checkin = (getattr(settings, 'WECHAT_TEMPLATE_CHECKIN', '') or '').strip()
        before = (getattr(settings, 'WECHAT_TEMPLATE_BEFORE_CLASS', '') or '').strip()

        if not checkin and not before:
            self.stderr.write(
                '两个模板 id 都没配。先填 .env 的 '
                'WECHAT_TEMPLATE_CHECKIN / WECHAT_TEMPLATE_BEFORE_CLASS')
            return

        try:
            token = wechat_notify.get_access_token()
        except Exception as exc:  # noqa: BLE001 —— 诊断命令，把原因原样告诉人
            self.stderr.write(f'取 access_token 失败：{exc}')
            self.stderr.write('检查 .env 的 WECHAT_APPID / WECHAT_APPSECRET')
            return

        resp = requests.get(TEMPLATE_URL, params={'access_token': token}, timeout=15)
        resp.raise_for_status()
        body = resp.json()
        if body.get('errcode'):
            self.stderr.write(f'拉取模板列表失败：{body}')
            return

        # priTmplId → (标题, 字段名集合)
        remote = {}
        for t in body.get('data') or []:
            fields = set(re.findall(r'\{\{(\w+)\.DATA\}\}', t.get('content', '')))
            remote[t['priTmplId']] = (t.get('title', ''), fields)

        # 用一组假数据走一遍真实的构造逻辑，拿到的就是会发出去的东西。
        # 这样比对的是「代码实际行为」，不是「我以为代码会怎么写」。
        expected = {}
        fake_session = _FakeSession()
        try:
            expected['checkin'] = ('签到通知', checkin,
                                   _capture(wechat_notify.notify_checkin,
                                            [_FakeEnrollment(), _DATE, fake_session, 8]))
            expected['before'] = ('上课提醒', before,
                                  _capture(wechat_notify.notify_before_class,
                                           [_FakeParent(), _FakeStudent(),
                                            fake_session, _DATE, 8]))
        finally:
            wechat_notify.send_subscribe_message = _ORIGINAL_SEND

        all_ok = True
        for name, (label, tid, sent) in expected.items():
            if not tid:
                self.stdout.write(f'\n[{label}] 未配置，跳过')
                continue

            title, required = remote.get(tid, (None, None))
            self.stdout.write(f'\n[{label}] 模板 id = {tid}')
            if required is None:
                all_ok = False
                self.stderr.write('  ✗ 这个 id 不在「我的模板」里。'
                                  '可能模板被删了，或复制时少了字符')
                continue

            self.stdout.write(f'  微信模板标题：{title}')
            self.stdout.write(f'  微信要求字段：{sorted(required)}')
            self.stdout.write(f'  代码发送字段：{sorted(sent)}')

            missing = required - sent
            extra = sent - required
            if missing or extra:
                all_ok = False
                if missing:
                    self.stderr.write(f'  ✗ 少发了（微信要求但代码没给）：{sorted(missing)}')
                if extra:
                    self.stderr.write(f'  ✗ 多发了（代码给了但模板没有）：{sorted(extra)}')
                self.stderr.write('  后果：发送时微信返回 47003 参数不匹配')
                self.stderr.write('  改 libs/wechat_notify.py 里对应的 data 字典')
            else:
                self.stdout.write('  ✓ 字段名一致')

        self.stdout.write('')
        if all_ok:
            self.stdout.write(self.style.SUCCESS(
                '全部匹配。剩下的唯一变量是家长有没有授权（43101）。'))
        else:
            self.stderr.write('有字段不匹配，见上面的提示。')


# ---------------------------------------------------------------
# 下面是给「假数据走真实构造逻辑」用的桩。
# 目的是拿到 notify_* 内部构造的 data 字典，而不是重新写一遍构造代码 ——
# 重写一遍就变成「测我新写的代码和我旧写的代码是否一致」，没有意义。
# ---------------------------------------------------------------

_ORIGINAL_SEND = wechat_notify.send_subscribe_message
_DATE = datetime.date(2026, 11, 27)


def _capture(func, args):
    """调一次 func，截获它交给 send_subscribe_message 的 data。"""
    box = {}

    def fake(openid, template_id, page, data, **kw):
        box['data'] = data

    wechat_notify.send_subscribe_message = fake
    try:
        func(*args)
    except Exception:  # noqa: BLE001 —— 桩不完整时不该让诊断命令崩掉
        pass
    finally:
        wechat_notify.send_subscribe_message = _ORIGINAL_SEND
    return set((box.get('data') or {}).keys())


class _FakeStudent:
    name = '张小明'


class _FakeParent:
    openid = 'o_dry_run_placeholder'


class _FakeParentStudent:
    """``_parents_of`` 遍历的是 ``student.parent_students``。

    桩必须给出这个属性，否则 notify_checkin 会在「没有家长」处提前
    return False，一个字段都不构造 —— 那样比对出来的就是空集合，
    会把「字段名正确」误报成「少发了 4 个字段」。
    """

    def __init__(self, parent):
        self.parent = parent


class _FakeManager:
    def __init__(self, items):
        self._items = items

    def select_related(self, *args):
        return self._items

    def all(self):
        return self._items


class _FakeCourseClass:
    name = 'Python基础A班'


class _FakeSession:
    course_class = _FakeCourseClass()
    start_time = datetime.time(16, 0)


class _FakeEnrollment:
    def __init__(self):
        self.student = _FakeStudent()
        self.student.parent_students = _FakeManager([_FakeParentStudent(_FakeParent())])
