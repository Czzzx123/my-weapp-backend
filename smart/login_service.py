"""登录相关的业务逻辑。

与 view 分开，是因为「按手机号匹配孩子」这块逻辑在多个入口都要用：
手机号登录、微信登录后补手机号、Admin 手工绑定。写在 view 里会重复三遍。

核心规则（见 PROJECT_BRIEF 6.1）
--------------------------------
- 家长表只有 Parent 一个，openid 与 mobile 都可空，谁先有就先用谁
- 孩子绑定靠 **手机号自动匹配**：Parent.mobile == Student.mobile
- 一个家长可对应多个孩子（二孩家庭同号）
- 未绑定也能登录，只是 children 为 []

身份合并（2026-11-06 定稿）
---------------------------
同一个人可能先后用两个入口登录，产生两条 Parent：
先微信（有 openid 无 mobile）、先手机号（有 mobile 无 openid）。
两条都要认成「一个人」，靠 :func:`resolve_identity` 自动合并。
合并后必须用返回值重新签发 JWT，否则 token 里的 user_id 指向已删除的记录。
"""

from __future__ import annotations

import logging

from django.db import transaction

from .models import Parent, ParentStudent, Student

logger = logging.getLogger(__name__)


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


def _merge_into(target: Parent, source: Parent) -> None:
    """把 source 账号的所有痕迹搬到 target 上，然后删掉 source。

    迁移内容三样，漏一样都会丢数据：
    1. ``ParentStudent`` 绑定关系 —— 孩子不能丢
    2. source 独有的 openid / unionid / mobile —— 身份不能丢
    3. source 的昵称头像 —— 只在 target 没有时才搬，不覆盖已有的
    """
    # 1. 绑定关系：孩子不能丢，但也不能撞唯一约束
    #    ParentStudent 有 unique_together(parent, student)，如果两条账号都绑了
    #    同一个孩子（例如都匹配到同一个 Student.mobile），直接 update 改外键会抛
    #    IntegrityError。所以先剔掉重复的那几条（target 已经有了，等价），再迁剩下的。
    target_student_ids = set(
        ParentStudent.objects.filter(parent=target).values_list('student_id', flat=True)
    )
    if target_student_ids:
        dup_deleted, _ = ParentStudent.objects.filter(
            parent=source, student_id__in=target_student_ids
        ).delete()
        if dup_deleted:
            logger.info('账号合并：source=%s 有 %s 条绑定与 target 重复，丢弃',
                        source.id, dup_deleted)
    # 剩下的都是 target 没有的，安全迁移（用 update 保留原 bind_time）
    moved = ParentStudent.objects.filter(parent=source).update(parent=target)
    if moved:
        logger.info('账号合并：source=%s 迁移 %s 条孩子绑定到 target=%s',
                    source.id, moved, target.id)

    # 2. 身份与资料：source 有、target 没有的才搬
    changed = []
    for field in ('openid', 'unionid', 'mobile', 'nickname', 'avatar'):
        src_val = getattr(source, field)
        if src_val and not getattr(target, field):
            setattr(target, field, src_val)
            changed.append(field)
    if changed:
        target.save(update_fields=changed)

    logger.info('账号合并：source=%s → target=%s（迁移字段 %s）',
                source.id, target.id, changed or '无')
    source.delete()


def resolve_identity(*, openid: str = '', mobile: str = '') -> Parent:
    """按 openid / mobile 找到唯一的家长账号，必要时合并，返回**存活的那个**。

    这是「自动合并」的核心。规则如下：

    - 只命中 1 个  → 直接用（顺手补齐缺失字段）
    - 命中 2 个    → 合并成一个（同一个人的两半）
    - 都没命中      → 新建

    两个都命中时，**保留 mobile 那条**（通常是教务登记过、或老用户），
    把 openid 那条并进去。理由：mobile 关联着孩子绑定，是更完整的那一半。

    **调用方必须用它返回的 Parent 重新签发 JWT** —— 合并可能删掉了
    你 JWT 里指向的那个账号，继续用旧的会导致后续请求全部 401。
    """
    by_openid = Parent.objects.filter(openid=openid).first() if openid else None
    by_mobile = (
        Parent.objects.filter(mobile=mobile).order_by('id').first()
        if mobile else None
    )

    # ---- 情况 1：两个入口都命中，且不是同一条 → 合并 ----
    if by_openid and by_mobile and by_openid.pk != by_mobile.pk:
        logger.info(
            '同一人两条账号：openid 侧 id=%s / mobile 侧 id=%s，合并保留后者',
            by_openid.id, by_mobile.id,
        )
        survivor = by_mobile
        _merge_into(survivor, by_openid)
        return survivor

    # ---- 情况 2：只命中一条 ----
    if by_openid:
        if mobile and not by_openid.mobile:
            by_openid.mobile = mobile
            by_openid.save(update_fields=['mobile'])
        return by_openid

    if by_mobile:
        if openid and not by_mobile.openid:
            by_mobile.openid = openid
            by_mobile.save(update_fields=['openid'])
            logger.info('为既有家长补 openid：parent_id=%s', by_mobile.id)
        return by_mobile

    # ---- 情况 3：都没命中，新建 ----
    parent = Parent.objects.create(
        openid=openid or None,
        mobile=mobile or None,
    )
    logger.info('新建家长：parent_id=%s openid=%s mobile=%s',
                parent.id, openid[:8] if openid else '-', mobile or '-')
    return parent


@transaction.atomic
def bind_mobile(parent: Parent, mobile: str) -> Parent:
    """为已登录的家长补手机号，并立即跑一次匹配。返回**存活的账号**。

    典型场景：用户走微信静默登录（只有 openid，没有手机号），
    前端提示「补手机号以查看孩子的课表」，补完就能看到孩子。

    **补号可能触发账号合并**：这个手机号若已属于另一个 Parent
    （用户以前用验证码登录过），那就是同一个人的两半，应合并成一条。
    合并后 ``parent`` 对象可能已被删除，所以必须用返回值重签 JWT。

    Returns:
        存活的 :class:`Parent`（可能就是 ``parent`` 本身）
    """
    if not parent.openid:
        # 纯手机号账号来补号，理论上不该发生（它本来就有手机号），
        # 直接写上去即可，避免 resolve_identity 按空 openid 误判
        parent.mobile = mobile
        parent.save(update_fields=['mobile'])
        match_children(parent)
        return parent

    survivor = resolve_identity(openid=parent.openid, mobile=mobile)
    match_children(survivor)
    return survivor


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
