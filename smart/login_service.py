"""登录相关的业务逻辑。

与 view 分开，是因为「按手机号匹配孩子」这块逻辑在多个入口都要用：
手机号登录、微信登录后补手机号、Admin 手工绑定。写在 view 里会重复三遍。

核心规则（见 PROJECT_BRIEF 6.1）
--------------------------------
- 家长表只有 Parent 一个，openid 与 mobile 都可空，谁先有就先用谁
- 孩子绑定靠 **手机号自动匹配**：Parent.mobile == Student.mobile
- 一个家长可对应多个孩子（二孩家庭同号）
- 未绑定也能登录，只是 children 为 []
"""

from __future__ import annotations

import logging

from django.db import transaction

from .models import Parent, ParentStudent, Student

logger = logging.getLogger(__name__)


def get_or_create_parent(*, openid: str = '', mobile: str = '') -> tuple[Parent, bool]:
    """按 openid 或 mobile 找到家长，找不到就建一个。

    查找优先级：**openid 优先**。
    因为同一个人的 openid 是稳定唯一的，而手机号可能变（换号、填错）。

    Returns:
        ``(parent, created)``
    """
    parent = None

    if openid:
        parent = Parent.objects.filter(openid=openid).first()

    if parent is None and mobile:
        # 该手机号可能已被「手机号先登录」的账号占用
        parent = Parent.objects.filter(mobile=mobile).first()
        # 顺着手机号找到老账号时，把 openid 补上，实现两个入口合并成同一个人
        if parent and openid and not parent.openid:
            parent.openid = openid
            parent.save(update_fields=['openid'])
            logger.info('为既有家长补 openid：parent_id=%s', parent.id)

    if parent is None:
        parent = Parent.objects.create(
            openid=openid or None,
            mobile=mobile or None,
        )
        logger.info('新建家长：parent_id=%s openid=%s mobile=%s',
                    parent.id, openid[:8] if openid else '-', mobile or '-')
        return parent, True

    # 已有账号：补齐缺失的字段（比如微信登录的用户后来补了手机号）
    changed = []
    if openid and not parent.openid:
        parent.openid = openid
        changed.append('openid')
    if mobile and not parent.mobile:
        parent.mobile = mobile
        changed.append('mobile')
    if changed:
        parent.save(update_fields=changed)

    return parent, False


def match_children(parent: Parent) -> list[Student]:
    """按手机号把学生绑到家长名下，返回该家长的所有孩子。

    幂等：反复调用不会产生重复绑定记录（靠 unique_together 兜住 + 先查后建）。
    """
    if not parent.mobile:
        return []

    students = list(
        Student.objects.filter(mobile=parent.mobile, status='active').order_by('id')
    )

    if not students:
        return []

    existing_ids = set(
        ParentStudent.objects.filter(parent=parent).values_list('student_id', flat=True)
    )

    to_create = [
        ParentStudent(parent=parent, student=s)
        for s in students
        if s.id not in existing_ids
    ]
    if to_create:
        # ignore_conflicts 兜住并发下的唯一约束冲突，避免两个人同时登录报错
        ParentStudent.objects.bulk_create(to_create, ignore_conflicts=True)
        logger.info('家长 %s 新绑定 %s 个孩子', parent.id, len(to_create))

    return students


@transaction.atomic
def bind_mobile(parent: Parent, mobile: str) -> list[Student]:
    """为已登录的家长补手机号，并立即跑一次匹配。

    典型场景：用户走微信静默登录（只有 openid，没有手机号），
    前端提示「补手机号以查看孩子的课表」，补完就能看到孩子。

    为什么**不检查手机号是否已被其他 Parent 占用**
    -------------------------------------------------
    ``Parent.mobile`` 的字段定义就是「非唯一（二孩家庭同号）」，因为：
    - 手机号在本项目里是「绑定孩子的凭据」，不是唯一身份标识
    - 爸爸、妈妈各自微信登录后填同一个手机号，都应该看到同一批孩子
    - 同一个人先用验证码登录、后改用微信登录，产生两条记录也无害

    早先的实现会做「把 openid 合并到老账号」的处理，但那样会让 JWT 里的
    user_id 指向一个已被弃用的账号，补号后立刻拿不到数据，是自找麻烦。
    """
    parent.mobile = mobile
    parent.save(update_fields=['mobile'])
    return match_children(parent)


def build_login_payload(parent: Parent) -> dict:
    """组装登录接口的返回体。

    前端要靠 ``need_mobile`` 决定是否弹「补手机号」引导，
    靠 ``children`` 是否为空决定首页显示孩子卡片还是空态。
    """
    children = list(
        Student.objects.filter(
            parent_students__parent=parent, status='active'
        ).order_by('id')
    )

    return {
        'parent': {
            'id': parent.id,
            'mobile': parent.mobile or '',
            'nickname': parent.nickname or '',
            'avatar': parent.avatar or '',
            'role': parent.role,
        },
        'children': [
            {
                'id': s.id,
                'name': s.name,
                'grade': s.grade,
                'status': s.status,
            }
            for s in children
        ],
        # 有 openid 但没手机号 → 需要引导补号，否则看不到孩子
        'need_mobile': bool(parent.openid and not parent.mobile),
    }
