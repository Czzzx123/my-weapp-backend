"""签到消课与上课记录的行为约定。

这些测试锁的是**账务正确性**，不是界面。每一个用例都对应一条
「上线后真出事会造成麻烦」的规则，改实现时它们必须先过。

重点守的四条：
1. 课时不足整批拒绝，不写一半
2. 重复签到由数据库拦，不靠视图 if
3. 撤销是打标+冲正，不删行；撤销后能重新签到
4. 家长只能看到自己孩子的记录
"""

from __future__ import annotations

import datetime
from decimal import Decimal

from django.db import IntegrityError, transaction
from django.test import SimpleTestCase, TestCase, override_settings
from rest_framework.test import APIClient
from rest_framework_simplejwt.tokens import RefreshToken

from . import checkin as checkin_service
from .checkin import CheckinError
from .models import (
    ClassSession, Course, CourseClass, Enrollment, HourLog,
    Parent, ParentStudent, Student,
)

RECORDS_URL = '/smart/lesson_records/'


class BaseCheckinTestCase(TestCase):
    """两个家长、三个孩子、两个班。

    刻意让一个孩子报两个班、另一个孩子报一个班：
    「扣错班」「课时按班独立」这两类 bug 用同一个 fixture 就能抓出来。
    """

    def setUp(self):
        self.client = APIClient(HTTP_HOST='127.0.0.1')

        self.parent_a = Parent.objects.create(openid='o_PARENT_A', mobile='13800000001')
        self.parent_b = Parent.objects.create(openid='o_PARENT_B', mobile='13800000002')

        self.kid_a = Student.objects.create(name='张小明', mobile='13800000001', grade='三年级')
        self.kid_b = Student.objects.create(name='李小红', mobile='13800000002', grade='五年级')
        self.kid_c = Student.objects.create(name='王小刚', mobile='13800000001', grade='四年级')

        ParentStudent.objects.create(parent=self.parent_a, student=self.kid_a)
        ParentStudent.objects.create(parent=self.parent_a, student=self.kid_c)
        ParentStudent.objects.create(parent=self.parent_b, student=self.kid_b)

        self.course = Course.objects.create(name='Python编程')
        self.klass = CourseClass.objects.create(
            name='Python基础A班', course=self.course,
            teacher='王老师', room='301', status='active',
        )
        self.klass2 = CourseClass.objects.create(
            name='Scratch创意B班', course=self.course,
            teacher='李老师', room='302', status='active',
        )

        self.sess = ClassSession.objects.create(
            course_class=self.klass, weekday=3,
            start_time=datetime.time(16, 0), end_time=datetime.time(18, 0),
        )
        self.sess2 = ClassSession.objects.create(
            course_class=self.klass, weekday=6,
            start_time=datetime.time(10, 0), end_time=datetime.time(12, 0),
        )
        self.sess_other = ClassSession.objects.create(
            course_class=self.klass2, weekday=5,
            start_time=datetime.time(15, 30), end_time=datetime.time(17, 30),
        )

        self.enr_a = Enrollment.objects.create(
            student=self.kid_a, course_class=self.klass,
            total_hours=12, status='active',
        )
        self.enr_c = Enrollment.objects.create(
            student=self.kid_c, course_class=self.klass,
            total_hours=12, status='active',
        )
        self.enr_b = Enrollment.objects.create(
            student=self.kid_b, course_class=self.klass2,
            total_hours=8, status='active',
        )
        self.enr_a2 = Enrollment.objects.create(
            student=self.kid_a, course_class=self.klass2,
            total_hours=16, status='active',
        )

        self.today = datetime.date(2026, 11, 25)

    def token_for(self, parent):
        return str(RefreshToken.for_user(parent).access_token)

    def get(self, url, parent, params=None):
        return self.client.get(
            url, params or {}, HTTP_TOKEN=self.token_for(parent),
        )


class CheckinServiceTests(BaseCheckinTestCase):
    """签到服务的核心账务规则。"""

    def test_checkin_deducts_one_hour(self):
        """签到扣 1 课时，剩余从 12 变 11。"""
        checkin_service.checkin(
            class_id=self.klass.id,
            lesson_date=self.today,
            session_id=self.sess.id,
            enrollment_ids=[self.enr_a.id],
            operator='root',
        )
        self.enr_a.refresh_from_db()
        self.assertEqual(self.enr_a.remain_hours, 11)
        self.assertEqual(self.enr_a.used_hours, 1)

    def test_checkin_writes_lesson_date(self):
        """流水必须带上 lesson_date，否则「哪一天的那节课」就丢了。"""
        result = checkin_service.checkin(
            class_id=self.klass.id,
            lesson_date=self.today,
            session_id=self.sess.id,
            enrollment_ids=[self.enr_a.id],
        )
        log = result['created'][0]
        self.assertEqual(log.lesson_date, self.today)
        self.assertEqual(log.session_id, self.sess.id)
        self.assertEqual(log.reason, 'consumed')
        self.assertEqual(log.delta, -1)

    def test_insufficient_hours_rejects_whole_batch(self):
        """课时不足 → 整批拒绝，**一个人都不许扣**。

        这是最重要的一条。「先上课后补费」听起来合理，但账会滚乱。
        如果只写了一半就报错，老师会以为失败而重试，造成重复扣费。
        """
        # 把张小明掏空
        HourLog.objects.create(
            enrollment=self.enr_a, delta=-12, reason='adjust',
        )
        self.enr_a.refresh_from_db()
        self.assertEqual(self.enr_a.remain_hours, 0)

        with self.assertRaises(CheckinError) as ctx:
            checkin_service.checkin(
                class_id=self.klass.id,
                lesson_date=self.today,
                session_id=self.sess.id,
                enrollment_ids=[self.enr_a.id, self.enr_c.id],
            )

        self.assertIn('课时不足', str(ctx.exception))
        # 关键：王小刚（课时够的那个）也一条都不许写
        self.enr_c.refresh_from_db()
        self.assertEqual(self.enr_c.remain_hours, 12, '课时够的学生被误扣了')
        self.assertFalse(
            HourLog.objects.filter(enrollment=self.enr_a, reason='consumed').exists()
        )

    def test_duplicate_checkin_is_skipped(self):
        """同一节课重复签到 → 第二个人被跳过，不会扣两次。

        注意这里是「跳过」而不是报错：老师可能勾了两个人，其中先签过一个。
        整批报错会逼他把已签的人剔除再重试，反而增加误操作。
        """
        first = checkin_service.checkin(
            class_id=self.klass.id, lesson_date=self.today,
            session_id=self.sess.id, enrollment_ids=[self.enr_a.id],
        )
        self.assertEqual(len(first['created']), 1)

        second = checkin_service.checkin(
            class_id=self.klass.id, lesson_date=self.today,
            session_id=self.sess.id, enrollment_ids=[self.enr_a.id],
        )
        self.assertEqual(len(second['created']), 0)
        self.assertEqual(len(second['skipped']), 1)

        self.enr_a.refresh_from_db()
        self.assertEqual(self.enr_a.remain_hours, 11, '重复签到被扣了两次')

    def test_same_student_different_date_is_allowed(self):
        """同一时段、不同的上课日期 → 两次都该成功。"""
        checkin_service.checkin(
            class_id=self.klass.id, lesson_date=self.today,
            session_id=self.sess.id, enrollment_ids=[self.enr_a.id],
        )
        checkin_service.checkin(
            class_id=self.klass.id,
            lesson_date=self.today + datetime.timedelta(days=7),
            session_id=self.sess.id, enrollment_ids=[self.enr_a.id],
        )
        self.enr_a.refresh_from_db()
        self.assertEqual(self.enr_a.remain_hours, 10)

    def test_db_rejects_duplicate_consumed(self):
        """数据库层面也拦：绕过服务层直接插两条，必须 IntegrityError。

        **必须由数据库拦，不能只靠服务层的 exists() 判断** ——
        两个请求同时通过检查时，只有唯一约束救得了。
        """
        HourLog.objects.create(
            enrollment=self.enr_a, delta=-1, reason='consumed',
            session=self.sess, lesson_date=self.today,
        )
        with self.assertRaises(IntegrityError):
            with transaction.atomic():
                HourLog.objects.create(
                    enrollment=self.enr_a, delta=-1, reason='consumed',
                    session=self.sess, lesson_date=self.today,
                )

    def test_session_must_belong_to_class(self):
        """时段不属于该班 → 拒绝。

        前端两个下拉是独立的，选错组合会把 A 班的课记到 B 班学生头上，
        账面上完全看不出来。
        """
        with self.assertRaises(CheckinError) as ctx:
            checkin_service.checkin(
                class_id=self.klass.id,
                lesson_date=self.today,
                session_id=self.sess_other.id,   # 属于 klass2
                enrollment_ids=[self.enr_a.id],
            )
        self.assertIn('不属于该班级', str(ctx.exception))

    def test_enrollment_must_belong_to_class(self):
        """报名不属于该班 → 拒绝。"""
        with self.assertRaises(CheckinError):
            checkin_service.checkin(
                class_id=self.klass.id,
                lesson_date=self.today,
                session_id=self.sess.id,
                enrollment_ids=[self.enr_b.id],   # 属于 klass2
            )

    def test_session_required(self):
        """不传时段 → 拒绝。

        没有 session 就无法防重复（NULL ≠ NULL），
        等于把唯一约束废掉，所以宁可不让签。
        """
        with self.assertRaises(CheckinError):
            checkin_service.checkin(
                class_id=self.klass.id,
                lesson_date=self.today,
                session_id=None,
                enrollment_ids=[self.enr_a.id],
            )


class RevokeTests(BaseCheckinTestCase):
    """撤销签到：打标 + 冲正，不删行。"""

    def _do_checkin(self):
        result = checkin_service.checkin(
            class_id=self.klass.id, lesson_date=self.today,
            session_id=self.sess.id, enrollment_ids=[self.enr_a.id],
        )
        return result['created'][0]

    def test_revoke_returns_hour(self):
        """撤销后课时退回。"""
        log = self._do_checkin()
        self.enr_a.refresh_from_db()
        self.assertEqual(self.enr_a.remain_hours, 11)

        checkin_service.revoke(log.id)
        self.enr_a.refresh_from_db()
        self.assertEqual(self.enr_a.remain_hours, 12)

    def test_revoke_keeps_original_record(self):
        """原记录必须还在，不能被删。

        账本要能回答「这 1 课时去哪了」。删了就成了黑洞。
        """
        log = self._do_checkin()
        checkin_service.revoke(log.id)

        log.refresh_from_db()
        self.assertTrue(log.is_revoked)
        self.assertIsNotNone(log.revoked_time)
        # 冲正记录存在
        self.assertTrue(
            HourLog.objects.filter(
                enrollment=self.enr_a, reason='refund', delta=1,
            ).exists()
        )

    def test_revoke_twice_is_rejected(self):
        """重复撤销要拦住，否则会退两次课时。"""
        log = self._do_checkin()
        checkin_service.revoke(log.id)

        with self.assertRaises(CheckinError) as ctx:
            checkin_service.revoke(log.id)
        self.assertIn('已经撤销过', str(ctx.exception))

        self.enr_a.refresh_from_db()
        self.assertEqual(self.enr_a.remain_hours, 12, '重复撤销退了两次')

    def test_can_recheckin_after_revoke(self):
        """撤销后必须能重新签到 —— 这是整个功能的存在理由。

        若唯一约束不过滤 is_revoked，重新签到会撞上历史记录，
        等于签错一次就永远签不了。
        """
        log = self._do_checkin()
        checkin_service.revoke(log.id)

        result = checkin_service.checkin(
            class_id=self.klass.id, lesson_date=self.today,
            session_id=self.sess.id, enrollment_ids=[self.enr_a.id],
        )
        self.assertEqual(len(result['created']), 1, '撤销后无法重新签到')

        self.enr_a.refresh_from_db()
        self.assertEqual(self.enr_a.remain_hours, 11)

    def test_cannot_revoke_grant(self):
        """充值记录不能撤销 —— 撤销只对消课有意义。"""
        grant = HourLog.objects.create(
            enrollment=self.enr_a, delta=5, reason='grant',
        )
        with self.assertRaises(CheckinError) as ctx:
            checkin_service.revoke(grant.id)
        self.assertIn('只有', str(ctx.exception))


class LessonRosterTests(BaseCheckinTestCase):
    """签到页的名单。"""

    def test_roster_lists_active_enrollments(self):
        """只列在读报名。已退费的不该出现在点名册上。"""
        self.enr_c.status = 'refunded'
        self.enr_c.save(update_fields=['status'])

        roster = checkin_service.lesson_roster(
            class_id=self.klass.id, lesson_date=self.today,
            session_id=self.sess.id,
        )
        names = [r['student'].name for r in roster]
        self.assertIn('张小明', names)
        self.assertNotIn('王小刚', names)

    def test_roster_marks_checked(self):
        """已签到的人要标出来，且仍在名单里（老师需要看全员）。"""
        checkin_service.checkin(
            class_id=self.klass.id, lesson_date=self.today,
            session_id=self.sess.id, enrollment_ids=[self.enr_a.id],
        )
        roster = checkin_service.lesson_roster(
            class_id=self.klass.id, lesson_date=self.today,
            session_id=self.sess.id,
        )
        checked = {r['student'].name: r['checked'] for r in roster}
        self.assertEqual(checked['张小明'], True)
        self.assertEqual(checked['王小刚'], False)

    def test_roster_after_revoke_shows_unchecked(self):
        """撤销后名单上应显示「未签到」，方便重新签。"""
        result = checkin_service.checkin(
            class_id=self.klass.id, lesson_date=self.today,
            session_id=self.sess.id, enrollment_ids=[self.enr_a.id],
        )
        checkin_service.revoke(result['created'][0].id)

        roster = checkin_service.lesson_roster(
            class_id=self.klass.id, lesson_date=self.today,
            session_id=self.sess.id,
        )
        checked = {r['student'].name: r['checked'] for r in roster}
        self.assertEqual(checked['张小明'], False)


class LessonRecordsApiTests(BaseCheckinTestCase):
    """家长端上课记录接口。"""

    def setUp(self):
        super().setUp()
        checkin_service.checkin(
            class_id=self.klass.id, lesson_date=self.today,
            session_id=self.sess.id, enrollment_ids=[self.enr_a.id],
        )

    def test_requires_login(self):
        resp = self.client.get(RECORDS_URL, HTTP_HOST='127.0.0.1')
        self.assertEqual(resp.status_code, 401)

    def test_returns_own_records(self):
        body = self.get(RECORDS_URL, self.parent_a).json()
        self.assertEqual(body['code'], 100)
        names = {r['student_name'] for r in body['records']}
        self.assertEqual(names, {'张小明'})

    def test_other_parent_sees_their_own(self):
        """反向验证：家长 B 不该看到张小明。

        只测一边不够 —— 过滤条件写反（拿到「别人的」）时单向断言照样能过。
        """
        body = self.get(RECORDS_URL, self.parent_b).json()
        names = {r['student_name'] for r in body['records']}
        self.assertNotIn('张小明', names)

    def test_cannot_query_other_student(self):
        """传别人的 student_id 必须 403，不能靠前端不传。

        改个数字就能看到别人家孩子的上课记录，这类越权最容易漏。
        """
        resp = self.get(
            RECORDS_URL, self.parent_b, {'student_id': self.kid_a.id},
        )
        body = resp.json()
        self.assertEqual(body['code'], 403)
        self.assertEqual(body.get('records'), None)

    def test_excludes_non_consumed(self):
        """充值/冲正/调整不出现在上课记录里。

        家长要看的是「孩子上了哪些课」，不是账本全貌。
        """
        HourLog.objects.create(
            enrollment=self.enr_a, delta=8, reason='grant',
        )
        HourLog.objects.create(
            enrollment=self.enr_a, delta=2, reason='adjust',
        )
        body = self.get(RECORDS_URL, self.parent_a).json()
        reasons = {r['delta'] for r in body['records']}
        self.assertEqual(reasons, {-1}, '非消课流水混进了上课记录')

    def test_revoked_record_still_returned(self):
        """撤销过的记录照样返回并带标记。

        若直接过滤掉，家长之前看到的「11月25日 扣 1 课时」会凭空消失，
        他会以为系统偷偷改账。
        """
        log = HourLog.objects.get(reason='consumed', enrollment=self.enr_a)
        checkin_service.revoke(log.id)

        body = self.get(RECORDS_URL, self.parent_a).json()
        self.assertEqual(len(body['records']), 1)
        self.assertTrue(body['records'][0]['is_revoked'])

    def test_record_has_lesson_info(self):
        """记录要带上「哪节课、几点、哪天」，这是这个接口的全部意义。"""
        body = self.get(RECORDS_URL, self.parent_a).json()
        rec = body['records'][0]
        self.assertEqual(rec['class_name'], 'Python基础A班')
        self.assertEqual(rec['lesson_date'], '2026-11-25')
        self.assertEqual(rec['start_time'], '16:00')
        self.assertEqual(rec['weekday_label'], '周三')


class NotifyConfigTests(SimpleTestCase):
    """订阅消息模板 id 下发。

    为什么要测这个「只返回两个字段」的接口：
    它是匿名可访问的（登录页也要能拿到模板 id 去授权），
    一旦被误加上 IsAuthenticated，家长在未登录时就永远授权不了，
    而这个错误的表现是「点了没反应」，很难查。
    """

    def test_anonymous_can_read(self):
        resp = self.client.get('/smart/notify_config/')
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.json()['code'], 100)

    @override_settings(WECHAT_TEMPLATE_CHECKIN='')
    def test_disabled_when_template_missing(self):
        """模板没配时 enabled=false，前端据此隐藏按钮。"""
        body = self.client.get('/smart/notify_config/').json()
        self.assertEqual(body['template_id'], '')
        self.assertFalse(body['enabled'])

    @override_settings(WECHAT_TEMPLATE_CHECKIN='TPL_abc123')
    def test_returns_template_when_configured(self):
        body = self.client.get('/smart/notify_config/').json()
        self.assertEqual(body['template_id'], 'TPL_abc123')
        self.assertTrue(body['enabled'])
