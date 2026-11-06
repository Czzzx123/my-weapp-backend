"""账号合并的测试。

运行方式：

    ./.venv/Scripts/python.exe manage.py test smart.tests_merge -v 2

背景
----
同一个人可能先后用两个入口登录，产生两条 Parent 记录：

    先微信   → 有 openid，无 mobile
    先手机号 → 有 mobile，无 openid

两条都要认成「一个人」。合并规则见 ``login_service.resolve_identity``。

**这块逻辑最容易出错的两个点，测试必须覆盖**：

1. 合并后孩子绑定不能丢
2. 合并后返回的是**存活的账号**，不是被删掉的那个
   （否则调用方拿它签 JWT，token 里的 user_id 指向已删除记录，后续全 401）
"""

from django.db import IntegrityError
from django.test import TestCase

from smart import login_service
from smart.models import Parent, ParentStudent, Student


class MergeIdentityTests(TestCase):
    """resolve_identity 的合并行为。"""

    def setUp(self):
        self.student_a = Student.objects.create(
            name='张小明', mobile='13800000001', grade='三年级')
        self.student_b = Student.objects.create(
            name='张小明妹妹', mobile='13800000001', grade='一年级')

    # ---------------- 基础场景 ----------------

    def test_new_user_creates_one_parent(self):
        """都没命中 → 新建一个。"""
        p = login_service.resolve_identity(openid='o_NEW', mobile='13900000000')
        self.assertEqual(Parent.objects.count(), 1)
        self.assertEqual(p.openid, 'o_NEW')
        self.assertEqual(p.mobile, '13900000000')

    def test_only_openid_hit(self):
        """只有 openid 命中 → 直接用，并把手机号补上。"""
        existing = Parent.objects.create(openid='o_1')

        p = login_service.resolve_identity(openid='o_1', mobile='13800000001')

        self.assertEqual(p.pk, existing.pk)
        self.assertEqual(p.mobile, '13800000001')
        self.assertEqual(Parent.objects.count(), 1)

    def test_only_mobile_hit(self):
        """只有手机号命中 → 直接用，并把 openid 补上。"""
        existing = Parent.objects.create(mobile='13800000001')

        p = login_service.resolve_identity(openid='o_1', mobile='13800000001')

        self.assertEqual(p.pk, existing.pk)
        self.assertEqual(p.openid, 'o_1')
        self.assertEqual(Parent.objects.count(), 1)

    def test_same_parent_hit_by_both(self):
        """同一条账号两个字段都命中 → 不合并、不新建。"""
        existing = Parent.objects.create(openid='o_1', mobile='13800000001')

        p = login_service.resolve_identity(openid='o_1', mobile='13800000001')

        self.assertEqual(p.pk, existing.pk)
        self.assertEqual(Parent.objects.count(), 1)

    # ---------------- 核心：合并 ----------------

    def test_merge_two_accounts_keeps_mobile_side(self):
        """两条账号命中 → 合并，保留 mobile 那条，删掉 openid 那条。"""
        wx_side = Parent.objects.create(openid='o_WX', mobile=None)
        sms_side = Parent.objects.create(openid=None, mobile='13800000001')

        p = login_service.resolve_identity(openid='o_WX', mobile='13800000001')

        # 存活的是 mobile 那条
        self.assertEqual(p.pk, sms_side.pk)
        # openid 被搬过来了
        self.assertEqual(p.openid, 'o_WX')
        # 只剩一条
        self.assertEqual(Parent.objects.count(), 1)
        self.assertFalse(Parent.objects.filter(pk=wx_side.pk).exists())

    def test_merge_moves_children_bindings(self):
        """合并必须把孩子绑定搬过去，不能因删账号而丢孩子。"""
        wx_side = Parent.objects.create(openid='o_WX')
        sms_side = Parent.objects.create(mobile='13800000001')

        # 孩子绑在即将被删的账号上
        ParentStudent.objects.create(parent=wx_side, student=self.student_a)
        ParentStudent.objects.create(parent=wx_side, student=self.student_b)

        p = login_service.resolve_identity(openid='o_WX', mobile='13800000001')

        # 绑定还在，且指向存活的账号
        bindings = ParentStudent.objects.filter(parent=p)
        self.assertEqual(bindings.count(), 2)
        self.assertEqual(
            set(bindings.values_list('student_id', flat=True)),
            {self.student_a.id, self.student_b.id},
        )

    def test_merge_no_duplicate_binding_conflict(self):
        """两条账号绑了同一个孩子 → 合并后不能报唯一约束错，只剩一条绑定。"""
        wx_side = Parent.objects.create(openid='o_WX')
        sms_side = Parent.objects.create(mobile='13800000001')

        ParentStudent.objects.create(parent=wx_side, student=self.student_a)
        ParentStudent.objects.create(parent=sms_side, student=self.student_a)

        # 不应抛 IntegrityError
        p = login_service.resolve_identity(openid='o_WX', mobile='13800000001')

        self.assertEqual(
            ParentStudent.objects.filter(parent=p, student=self.student_a).count(),
            1,
        )

    def test_merge_keeps_nickname_from_either_side(self):
        """昵称只在目标为空时才搬，不覆盖已有值。"""
        wx_side = Parent.objects.create(openid='o_WX', nickname='微信昵称')
        sms_side = Parent.objects.create(mobile='13800000001', nickname='')

        p = login_service.resolve_identity(openid='o_WX', mobile='13800000001')

        self.assertEqual(p.nickname, '微信昵称')

    def test_merge_does_not_overwrite_target_nickname(self):
        """目标已有昵称时，不被 source 覆盖。"""
        wx_side = Parent.objects.create(openid='o_WX', nickname='微信昵称')
        sms_side = Parent.objects.create(mobile='13800000001', nickname='教务填的')

        p = login_service.resolve_identity(openid='o_WX', mobile='13800000001')

        self.assertEqual(p.nickname, '教务填的')

    def test_merge_is_idempotent(self):
        """反复调用不会反复合并、不会报错。"""
        Parent.objects.create(openid='o_WX')
        Parent.objects.create(mobile='13800000001')

        p1 = login_service.resolve_identity(openid='o_WX', mobile='13800000001')
        p2 = login_service.resolve_identity(openid='o_WX', mobile='13800000001')
        p3 = login_service.resolve_identity(openid='o_WX', mobile='13800000001')

        self.assertEqual(p1.pk, p2.pk)
        self.assertEqual(p2.pk, p3.pk)
        self.assertEqual(Parent.objects.count(), 1)


class BindMobileMergeTests(TestCase):
    """bind_mobile 补号时的合并行为。"""

    def setUp(self):
        self.student = Student.objects.create(
            name='张小明', mobile='13800000001', grade='三年级')

    def test_bind_mobile_simple_case(self):
        """普通补号：没有冲突账号，直接写上去。"""
        p = Parent.objects.create(openid='o_WX')

        survivor = login_service.bind_mobile(p, '13800000001')

        self.assertEqual(survivor.pk, p.pk)
        self.assertEqual(survivor.mobile, '13800000001')

    def test_bind_mobile_triggers_merge_and_returns_survivor(self):
        """补号时手机号已被别的账号占用 → 合并，返回存活的那条。

        这是最容易出错的地方：如果返回了被删掉的 wx_side，
        调用方拿它签 JWT，之后所有请求都会 401。
        """
        wx_side = Parent.objects.create(openid='o_WX')
        sms_side = Parent.objects.create(mobile='13800000001')

        survivor = login_service.bind_mobile(wx_side, '13800000001')

        # 返回值必须是存活的 sms_side，不是被删的 wx_side
        self.assertEqual(survivor.pk, sms_side.pk)
        self.assertTrue(Parent.objects.filter(pk=survivor.pk).exists())
        self.assertFalse(Parent.objects.filter(pk=wx_side.pk).exists())

    def test_bind_mobile_then_children_visible(self):
        """补号合并后，孩子能正常看到（走的是存活账号的手机号匹配）。"""
        wx_side = Parent.objects.create(openid='o_WX')
        Parent.objects.create(mobile='13800000001')

        survivor = login_service.bind_mobile(wx_side, '13800000001')

        self.assertEqual(survivor.mobile, '13800000001')
        self.assertEqual(
            [s.name for s in survivor.children], ['张小明'])


class OpenidUniqueConstraintTests(TestCase):
    """openid 唯一约束（迁移 0013）带来的连锁影响。

    加约束前 openid 只建索引不唯一，两条账号可以持有同一个 openid。
    约束加上后，合并流程里「把 openid 从 source 搬到 target」这一步
    会撞约束 —— 因为搬运的瞬间两条记录同时持有同一个值。
    """

    def test_merge_never_breaks_on_unique_constraint(self):
        """合并时 openid 搬运不能触发 UNIQUE 约束。

        这是加约束后立刻暴露的真实 bug：原实现直接把 openid 写给 target
        再 save，此时 source 还活着占着同一个值，SQLite 直接报
        UNIQUE constraint failed，整个事务回滚，用户永远登录不进去。
        """
        wx_side = Parent.objects.create(openid='o_WX', nickname='微信昵称')
        sms_side = Parent.objects.create(mobile='13800000001')

        survivor = login_service.resolve_identity(openid='o_WX', mobile='13800000001')

        self.assertEqual(survivor.pk, sms_side.pk)
        self.assertEqual(survivor.openid, 'o_WX')
        self.assertEqual(Parent.objects.count(), 1)

    def test_merge_preserves_unionid_too(self):
        """unionid 走同一套「先释放再写入」的逻辑，一并验证。"""
        wx_side = Parent.objects.create(openid='o_WX', unionid='u_1')
        sms_side = Parent.objects.create(mobile='13800000001')

        survivor = login_service.resolve_identity(openid='o_WX', mobile='13800000001')

        self.assertEqual(survivor.unionid, 'u_1')

    def test_resolve_identity_tolerates_concurrent_create(self):
        """并发场景：两个请求同时走到「都没命中 → 新建」。

        唯一约束会让后到的那个抛 IntegrityError。这不该变成 500 ——
        另一个请求已经建好了，直接取来用即可。
        """
        from unittest.mock import patch

        first = Parent.objects.create(openid='o_RACE')

        # 模拟「查询时还没有这条记录，写的时候已经有了」：
        # 只让**第一次**查询（by_openid 的初查）返回空。
        # 注意 mobile 传空时不会有第二次初查，所以不能写成「前两次」。
        origin = Parent.objects.filter

        def fake_filter(*args, **kwargs):
            fake_filter.calls += 1
            if fake_filter.calls == 1:
                return Parent.objects.none()
            return origin(*args, **kwargs)

        fake_filter.calls = 0

        with patch.object(Parent.objects, 'filter', side_effect=fake_filter):
            with patch.object(Parent.objects, 'create',
                              side_effect=IntegrityError('UNIQUE constraint failed')):
                result = login_service.resolve_identity(openid='o_RACE')

        self.assertEqual(result.pk, first.pk)

    def test_blank_openid_not_forced_unique(self):
        """空 openid 不该互相冲突。

        纯手机号账号 openid 是 NULL；SQLite 的 UNIQUE 允许多个 NULL，
        这条是为了防止将来有人把 null=True 改成 default='' 导致第二个
        手机号账号建不出来。
        """
        Parent.objects.create(mobile='13800000001')
        Parent.objects.create(mobile='13800000002')
        self.assertEqual(Parent.objects.filter(openid__isnull=True).count(), 2)
