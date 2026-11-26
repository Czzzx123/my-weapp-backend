"""上课前提醒的行为约定。

这些测试锁的是**会不会打扰家长**，不是界面。

重点守的四条：
1. 同一天同一节课，一个家长只收一条 —— 任务跑一百遍也一样
2. 已签到的孩子不再催
3. 课时不足的家庭不催来上课（来了也签不了，白跑）
4. 「周几换算成日期」的方向不能错（周五查周三要跨到下周三，不是这周三）

第 4 条最不容易发现：写错了周一~周三看起来都正常，
只有周四之后才暴露，而那时已经在线上跑了一周。
"""

from __future__ import annotations

import datetime
from io import StringIO

from django.core.management import call_command
from django.test import TestCase, override_settings
from django.utils import timezone

from . import reminder
from .models import (
    ClassSession, Course, CourseClass, Enrollment, HourLog,
    NotifyLog, Parent, ParentStudent, Student,
)


class BaseReminderTestCase(TestCase):
    """一个班两个家长，一个已签到、一个课时不足，用来验筛选。

    星期取 3（周三），日期取 2026-11-25（也是周三）。
    刻意让两者不一致的写法（比如拿 weekday 直接当日期用）
    在这个 fixture 下会立刻暴露。
    """

    def setUp(self):
        self.parent_a = Parent.objects.create(openid='o_A', mobile='13800000001')
        self.parent_b = Parent.objects.create(openid='o_B', mobile='13800000002')
        self.parent_c = Parent.objects.create(openid='', mobile='13800000003')

        self.kid_a = Student.objects.create(name='张小明', grade='三年级')
        self.kid_b = Student.objects.create(name='李小红', grade='五年级')
        self.kid_c = Student.objects.create(name='王小刚', grade='三年级')

        ParentStudent.objects.create(parent=self.parent_a, student=self.kid_a)
        ParentStudent.objects.create(parent=self.parent_b, student=self.kid_b)
        # 这个学生绑了一个没 openid 的家长，用来验「家长没微信」的降级
        ParentStudent.objects.create(parent=self.parent_c, student=self.kid_c)

        self.course = Course.objects.create(name='Python编程')
        self.klass = CourseClass.objects.create(
            name='Python基础A班', course=self.course,
            teacher='王老师', room='301', status='active',
        )
        self.session = ClassSession.objects.create(
            course_class=self.klass, weekday=3,
            start_time=datetime.time(16, 0), end_time=datetime.time(18, 0),
        )

        self.enr_a = Enrollment.objects.create(
            student=self.kid_a, course_class=self.klass,
            total_hours=12, status='active')
        self.enr_b = Enrollment.objects.create(
            student=self.kid_b, course_class=self.klass,
            total_hours=8, status='active')
        self.enr_c = Enrollment.objects.create(
            student=self.kid_c, course_class=self.klass,
            total_hours=8, status='active')

        # 2026-11-25 是周三
        self.wed = datetime.date(2026, 11, 25)


class WeekdayMappingTests(BaseReminderTestCase):
    """「周几」到「日期」的换算方向。"""

    def test_wednesday_session_found_on_wednesday(self):
        sessions = reminder.sessions_of_day(self.wed)
        self.assertEqual([s.id for s in sessions], [self.session.id])

    def test_thursday_finds_nothing(self):
        """周四不该查出周三的课。差一天是最常见的 off-by-one。"""
        thursday = self.wed + datetime.timedelta(days=1)
        self.assertEqual(reminder.sessions_of_day(thursday), [])

    def test_isoweekday_matches_model_convention(self):
        """date.isoweekday() 与 ClassSession.weekday 都是 1=周一 … 7=周日。

        这两个口径如果搞混（用 date.weekday() 就会差 1），
        周一的课会被当成周日的课，整套提醒全部错位。
        """
        self.assertEqual(self.wed.isoweekday(), 3)
        self.assertEqual(reminder._to_weekday(self.wed),
                         self.session.weekday)

    def test_suspended_session_excluded(self):
        """停课的时段不提醒 —— 催家长去上一个已经停了的课。"""
        self.session.status = 'suspended'
        self.session.save(update_fields=['status'])
        self.assertEqual(reminder.sessions_of_day(self.wed), [])

    def test_closed_class_excluded(self):
        """结班的班级不提醒。"""
        self.klass.status = 'closed'
        self.klass.save(update_fields=['status'])
        self.assertEqual(reminder.sessions_of_day(self.wed), [])


class DueWindowTests(BaseReminderTestCase):
    """定时任务的「该发了没」判定。"""

    def _now(self, hh, mm):
        return timezone.make_aware(datetime.datetime(2026, 11, 25, hh, mm))

    def test_not_due_too_early(self):
        """提前 120 分钟该在 14:00 到期（16:00 的课），13:00 不命中。"""
        self.assertEqual(reminder.due_sessions(self._now(13, 0), 120, 10), [])

    def test_due_exactly_at_lead_time(self):
        got = reminder.due_sessions(self._now(14, 0), 120, 10)
        self.assertEqual(len(got), 1)
        self.assertEqual(got[0][0].id, self.session.id)
        self.assertEqual(got[0][1], self.wed)

    def test_due_within_window(self):
        """窗口内（14:00 之后 10 分钟内）仍然命中。

        任务每 5 分钟跑一次，如果只认「正好等于」，
        14:03 那次跑就永远抓不到 14:00 该发的提醒。
        """
        self.assertEqual(len(reminder.due_sessions(self._now(14, 5), 120, 10)), 1)

    def test_not_due_after_window(self):
        """超出窗口就不再命中，避免补发一堆过期的提醒。"""
        self.assertEqual(reminder.due_sessions(self._now(14, 30), 120, 10), [])

    def test_already_started_not_reminded(self):
        """已经开课了还发「马上要上课了」是荒谬的。"""
        self.assertEqual(reminder.due_sessions(self._now(16, 30), 120, 10), [])


class CollectRecipientsTests(BaseReminderTestCase):
    """该通知谁、不该通知谁。"""

    def test_returns_all_with_skip_reason(self):
        rows = reminder.collect_recipients(self.session, self.wed)
        by_name = {r['student'].name: r for r in rows}

        # 三个孩子都要出现在结果里 —— 包括不该发的。
        # 不返回的话后台看不出「为什么这个家长没收到」，会以为漏发了。
        self.assertEqual(len(rows), 3)
        self.assertEqual(by_name['张小明']['skip_reason'], '')
        self.assertEqual(by_name['李小红']['skip_reason'], '')

    def test_checked_in_student_skipped(self):
        """已签到的孩子不再催。"""
        HourLog.objects.create(
            enrollment=self.enr_a, delta=-1, reason='consumed',
            session=self.session, lesson_date=self.wed,
        )
        rows = reminder.collect_recipients(self.session, self.wed)
        by_name = {r['student'].name: r for r in rows}
        self.assertIn('已签到', by_name['张小明']['skip_reason'])
        self.assertEqual(by_name['李小红']['skip_reason'], '')

    def test_revoked_checkin_does_not_skip(self):
        """撤销过的签到不算签到 —— 这孩子还没到，照样要提醒。"""
        HourLog.objects.create(
            enrollment=self.enr_a, delta=-1, reason='consumed',
            session=self.session, lesson_date=self.wed, is_revoked=True,
        )
        rows = reminder.collect_recipients(self.session, self.wed)
        by_name = {r['student'].name: r for r in rows}
        self.assertEqual(by_name['张小明']['skip_reason'], '')

    def test_no_hours_skipped(self):
        """课时不足不催：来了也签不了到，等于让人白跑。"""
        HourLog.objects.create(
            enrollment=self.enr_b, delta=-8, reason='consumed',
            session=self.session, lesson_date=datetime.date(2026, 11, 18),
        )
        rows = reminder.collect_recipients(self.session, self.wed)
        by_name = {r['student'].name: r for r in rows}
        self.assertIn('课时不足', by_name['李小红']['skip_reason'])

    def test_parent_without_openid_still_listed(self):
        """家长没 openid 也要出现在名单里，只是发不出去。"""
        rows = reminder.collect_recipients(self.session, self.wed)
        by_name = {r['student'].name: r for r in rows}
        self.assertFalse(by_name['王小刚']['parent'].openid)


class ClaimIdempotencyTests(BaseReminderTestCase):
    """「只发一次」的闸门。"""

    def test_second_claim_returns_none(self):
        first = reminder.claim(
            self.parent_a.id, self.kid_a.id, self.session.id,
            self.wed, 'before_class')
        self.assertIsNotNone(first)

        second = reminder.claim(
            self.parent_a.id, self.kid_a.id, self.session.id,
            self.wed, 'before_class')
        self.assertIsNone(second, '同一节课同一个家长不能占两次名额')

    def test_different_type_can_claim(self):
        """签到通知和上课前提醒是两种通知，各自可以有额度。"""
        reminder.claim(self.parent_a.id, self.kid_a.id, self.session.id,
                       self.wed, 'before_class')
        log = reminder.claim(self.parent_a.id, self.kid_a.id, self.session.id,
                             self.wed, 'checkin')
        self.assertIsNotNone(log)

    def test_same_parent_two_kids_both_notified(self):
        """一个家长两个孩子同上一个班，两条提醒都该发。"""
        self.enr_c.delete()  # 王小刚退班，把张小明的弟弟塞进来
        ParentStudent.objects.create(parent=self.parent_a, student=self.kid_c)
        Enrollment.objects.create(
            student=self.kid_c, course_class=self.klass,
            total_hours=8, status='active')

        a = reminder.claim(self.parent_a.id, self.kid_a.id, self.session.id,
                           self.wed, 'before_class')
        c = reminder.claim(self.parent_a.id, self.kid_c.id, self.session.id,
                           self.wed, 'before_class')
        self.assertIsNotNone(a)
        self.assertIsNotNone(c, '两个孩子要能各占一个名额')

    def test_different_date_can_claim(self):
        """下周三的同一节课，是另一次提醒。"""
        reminder.claim(self.parent_a.id, self.kid_a.id, self.session.id,
                       self.wed, 'before_class')
        next_wed = self.wed + datetime.timedelta(days=7)
        log = reminder.claim(self.parent_a.id, self.kid_a.id, self.session.id,
                             next_wed, 'before_class')
        self.assertIsNotNone(log)


@override_settings(
    WECHAT_TEMPLATE_BEFORE_CLASS='',
    WECHAT_TEMPLATE_CHECKIN='',
)
class CommandTests(BaseReminderTestCase):
    """管理命令的整体行为。"""

    def _run(self, *args):
        out = StringIO()
        try:
            call_command('send_class_reminders', *args, stdout=out, stderr=out)
        except SystemExit:
            pass
        return out.getvalue()

    def test_dry_run_writes_nothing(self):
        """预演绝不能写库 —— 否则预演一遍就把名额占了，真跑全变「已发过」。"""
        self._run('--date', '2026-11-25', '--force', '--dry-run')
        self.assertEqual(NotifyLog.objects.count(), 0)

    def test_no_template_records_failure(self):
        """模板没配时记 failed，不能静默什么都不留。"""
        self._run('--date', '2026-11-25', '--force')
        logs = NotifyLog.objects.filter(status='failed')
        self.assertTrue(logs.exists())
        self.assertIn('WECHAT_TEMPLATE_BEFORE_CLASS', logs.first().remark)

    def test_rerun_does_not_resend(self):
        """再跑一次不会重复发 —— 这是 cron 场景的核心保证。"""
        self._run('--date', '2026-11-25', '--force')
        first = NotifyLog.objects.count()
        self._run('--date', '2026-11-25', '--force')
        self.assertEqual(NotifyLog.objects.count(), first)

    def test_checked_in_child_not_claimed(self):
        """已签到的孩子连记录都不该占。

        占用会把「这次先不催」变成永久结论 —— 老师撤销签到后
        本该重新提醒，但那条记录还挡着，等于永远收不到。
        """
        HourLog.objects.create(
            enrollment=self.enr_a, delta=-1, reason='consumed',
            session=self.session, lesson_date=self.wed,
        )
        self._run('--date', '2026-11-25', '--force')
        self.assertFalse(
            NotifyLog.objects.filter(student=self.kid_a).exists(),
            '已签到的孩子不该占提醒名额')

    def test_recheckin_after_revoke_still_reminded(self):
        """撤销签到后重新跑，应该能补发提醒。

        这是上一条的存在理由：如果之前占了名额，这一步会失败。
        """
        log = HourLog.objects.create(
            enrollment=self.enr_a, delta=-1, reason='consumed',
            session=self.session, lesson_date=self.wed,
        )
        self._run('--date', '2026-11-25', '--force')
        self.assertFalse(NotifyLog.objects.filter(student=self.kid_a).exists())

        # 老师发现签错了，撤销
        log.is_revoked = True
        log.save(update_fields=['is_revoked'])

        # 再跑一次，张小明应该在名单里（会因模板没配而记 failed）
        self._run('--date', '2026-11-25', '--force')
        self.assertTrue(
            NotifyLog.objects.filter(student=self.kid_a).exists(),
            '撤销签到后应该能重新提醒')

    def test_no_openid_parent_not_claimed(self):
        """家长没 openid 时不占名额 —— 将来绑了微信还得能收到。"""
        self._run('--date', '2026-11-25', '--force')
        self.assertFalse(
            NotifyLog.objects.filter(student=self.kid_c).exists(),
            '没 openid 的家长不该占名额，否则绑定微信后永远收不到')
