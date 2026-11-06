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

from django.db import IntegrityError, transaction

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


@transaction.atomic
def _merge_into(target: Parent, source: Parent) -> None:
    """把 source 账号的所有痕迹搬到 target 上，然后删掉 source。

    迁移内容三样，漏一样都会丢数据：
    1. ``ParentStudent`` 绑定关系 —— 孩子不能丢
    2. source 独有的 openid / unionid / mobile —— 身份不能丢
    3. source 的昵称头像 —— 只在 target 没有时才搬，不覆盖已有的

    **必须整体在一个事务里**：中途失败会留下「绑定已迁移、source 还在」
    或「source 已删、openid 没搬过去」的半合并状态，那比不合并更糟 ——
    用户下次登录会在两条账号间反复横跳。``source.delete()`` 在最后一步，
    事务回滚时前面所有迁移操作一并撤销。
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
    #
    #    注意 avatar 是 ImageField，真值是 FieldFile 而不是字符串，
    #    不能直接 `setattr(target, 'avatar', source.avatar)` —— 那会让两条记录
    #    指向同一个物理文件，而 source.delete() 不删文件，之后任一方改头像
    #    都可能把对方正在用的文件删掉。这里传 name 字符串，让 Django 各自持有引用。
    changed = []
    for field in ('mobile', 'nickname'):
        src_val = getattr(source, field)
        if src_val and not getattr(target, field):
            setattr(target, field, src_val)
            changed.append(field)

    if source.avatar and not target.avatar:
        target.avatar.name = source.avatar.name
        changed.append('avatar')

    if changed:
        target.save(update_fields=changed)

    # 3. openid / unionid 必须**最后处理，且先释放 source 上的值**。
    #
    #    openid 有唯一约束。如果直接 target.openid = source.openid 再 save，
    #    这一刻 source 还活着、还占着同一个 openid，数据库会直接报
    #    UNIQUE constraint failed —— 而且是在事务里，整个合并回滚，
    #    用户永远登录不进去。
    #
    #    正确顺序：先把 source 上的唯一值清空（释放占位），再写给 target。
    #    两步都在同一事务内，任一步失败都会整体回滚，不会出现
    #    「两边都没有 openid」的中间态被提交。
    identity_changed = []
    for field in ('openid', 'unionid'):
        src_val = getattr(source, field)
        if src_val and not getattr(target, field):
            setattr(source, field, None)
            source.save(update_fields=[field])
            setattr(target, field, src_val)
            identity_changed.append(field)

    if identity_changed:
        target.save(update_fields=identity_changed)
        changed.extend(identity_changed)

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
    try:
        parent = Parent.objects.create(
            openid=openid or None,
            mobile=mobile or None,
        )
    except IntegrityError:
        # openid 有唯一约束，并发场景下（用户连点两次登录、或前端超时重试）
        # 两个请求可能同时走到这里，后到的那个会撞约束。
        # 这不是错误 —— 另一个请求已经建好了，直接取来用即可。
        logger.info('并发创建家长撞上 openid 唯一约束，回查已有记录 openid=%s',
                    openid[:8] if openid else '-')
        existing = Parent.objects.filter(openid=openid).first() if openid else None
        if existing is None:
            # 撞的如果是 mobile 之外的约束，说明情况超出预期，不能静默吞掉
            raise
        return existing

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


def default_display_name(parent: Parent, children: list[Student]) -> str:
    """未设置昵称时的展示名兜底。

    规则（2026-11-06 与用户确认）：
      1. 用户设置过 nickname  → 用用户的
      2. 没设置但有孩子       → 「张小明家长」
      3. 没设置也没孩子       → 「家长」

    为什么不把「家长」直接写进 nickname 字段：
    那样就分不清「用户自己起名叫家长」和「系统默认值」，
    以后做「引导设置昵称」时无法判断该不该弹窗。

    「孩子名 + 家长」这个叫法来自学校常用称呼，家长一看就有辨识度，
    而且解决了爸妈两个账号都叫「家长」分不清谁是谁的问题。
    """
    if parent.nickname:
        return parent.nickname
    if children:
        return f'{children[0].name}家长'
    return '家长'


def build_login_payload(parent: Parent, request=None) -> dict:
    """组装登录接口的返回体。

    前端要靠 ``need_mobile`` 决定是否弹「补手机号」引导，
    靠 ``children`` 是否为空决定首页显示孩子卡片还是空态。

    Args:
        request: 传入后会用 ``build_absolute_uri`` 把头像拼成绝对地址。
            **真机必须传** —— 相对路径 ``/media/avatar/x.webp`` 在小程序里
            会被当成包内资源，图片显示空白。这与 Banner 踩的是同一个坑。
    """
    children = list(
        Student.objects.filter(
            parent_students__parent=parent, status='active'
        ).order_by('id')
    )

    # ImageField 的真值判断和 CharField 不同：无图时是空 FieldFile，bool 为 False
    #
    # 但「有值」不等于「文件真的在」。以下情况会让 DB 有记录、磁盘没文件：
    #   - 手工清理过 media/ 目录
    #   - 换服务器时只迁了数据库没迁文件
    #   - 上一版删旧文件的 bug（见 avatar_service 的说明）
    # 此时 .url 仍然会拼出一个路径，前端拿到 404 只能显示空白 —— 比给个
    # 默认头像更糟，因为用户会以为是自己设置丢了。所以这里要真去验一下。
    avatar_url = ''
    if parent.avatar:
        try:
            exists = parent.avatar.storage.exists(parent.avatar.name)
        except Exception:  # noqa: BLE001 - 存储层异常不该拖垮登录流程
            logger.warning('检查头像文件是否存在时出错 parent_id=%s', parent.id, exc_info=True)
            exists = False

        if exists:
            avatar_url = parent.avatar.url
            if request is not None:
                avatar_url = request.build_absolute_uri(avatar_url)
        else:
            # 不在这里清 DB 字段：可能是临时挂载问题，贸然写库会丢数据。
            # 只记日志，让运维能看到；返回空串时前端会落到默认头像。
            logger.warning('头像文件缺失（DB 有记录但磁盘没有）：parent_id=%s name=%s',
                           parent.id, parent.avatar.name)

    return {
        'parent': {
            'id': parent.id,
            'mobile': parent.mobile or '',
            # nickname 保持「用户真实设置的值」，未设置就是空串，不塞默认值
            'nickname': parent.nickname or '',
            # display_name 是给 UI 直接显示的兜底结果，前端不用自己拼
            'display_name': default_display_name(parent, children),
            'avatar': avatar_url,
            'role': parent.role,
            # 昵称和头像都没设置 → 前端据此弹一次「完善资料」引导。
            # 头像这一项必须看 avatar_url 而不是 parent.avatar ——
            # 文件丢失时要当作「没头像」，否则用户永远不会再被引导去设头像。
            'has_custom_profile': bool(parent.nickname or avatar_url),
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
