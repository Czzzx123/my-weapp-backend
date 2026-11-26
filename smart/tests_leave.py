"""请假的行为约定。

这套测试守的四条线，每一条对应一个真实的出错方式：

1. **开课后不能请** —— 落到日期上才判得出来。只判「今天是周三」不行：
   周三 20:00 已经上完了，家长还能给今天请假，等于旷课可以事后补成请假。
2. **重复提交不报错** —— 网络卡顿时家长会连点两次，报错会让他反复重试。
3. **撤销后能重新请** —— 唯一约束必须带 ``status='active'`` 条件，
   否则撤销一次就永远请不了这节课。
4. **请了假不发提醒，撤销后恢复** —— 这是请假功能对提醒链路的价值所在，
   也是最容易写成「永久跳过」的地方。

第 4 条最隐蔽：如果把「已请假」也写进 ``NotifyLog`` 占名额，
家长撤销请假后就再也收不到催课通知，而他完全不知道原因。
"""

from __future__ import annotations

import datetime
from io import StringIO

from django.core.management import call_command
from django.test import TestCase, override_settings
from django.utils import timezone

from . import leave as leave_service
from . import reminder
from .models import (
    ClassSession, Course, CourseClass, Enrollment, HourLog,
    LeaveRequest, Parent, ParentStudent, Student,
)


class BaseLeaveTestCase(TestCase):
    """一个班两个孩子、两个家长，周三 16:00 的课。

    日期 2026-11-25 是周三 —— 与 ``weekday=3`` 对应，
    刻意不在 fixture 里把两者写成同一个数字，
    让「拿 weekday 当日期用」这种错法能立刻暴露。
    """

    def setUp(self):
        self.parent_a = Parent.objects.create(openid='o_A', mobile='13800000001')
        self.parent_b = Parent.objects.create(openid='o_B', mobile='13800000002')

        self.kid_a = Student.objects.create(name='张小明', grade='三年级')
        self.kid_b = Student.objects.create(name='李小红', grade='五年级')

        ParentStudent.objects.create(parent=self.parent_a, student=self.kid_a)
        ParentStudent.objects.create(parent=self.parent_b, student=self.kid_b)

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

        self.wed = datetime.date(2026, 11, 25)

    def _at(self, hh, mm):
        """2026-11-25 的某个时刻（带时区）。"""
        return timezone.make_aware(datetime.datetime(2026, 11, 25, hh, mm))


class SubmitTests(BaseLeaveTestCase):
    """提交请假。"""

    def test_submit_creates_active_leave(self):
        obj = leave_service.submit(
            self.parent_a, self.kid_a.id, self.session.id, self.wed,
            '发烧了', now=self._at(9, 0))
        self.assertEqual(obj.status, 'active')
        self.assertEqual(obj.reason, '发烧了')
        self.assertEqual(obj.lesson_date, self.wed)

    def test_duplicate_submit_returns_same_record(self):
        """连点两次只产生一条记录，且第二次不报错。

        报错的话家长会以为第一次没成功，于是反复重试 ——
        而实际上早就请好了。
        """
        first = leave_service.submit(
            self.parent_a, self.kid_a.id, self.session.id, self.wed,
            now=self._at(9, 0))
        second = leave_service.submit(
            self.parent_a, self.kid_a.id, self.session.id, self.wed,
            now=self._at(9, 1))
        self.assertEqual(first.id, second.id)
        self.assertEqual(LeaveRequest.objects.count(), 1)

    def test_cannot_submit_after_class_started(self):
        """开课后不能请 —— 否则旷课可以事后补成请假，两者就分不出来了。"""
        with self.assertRaises(leave_service.LeaveError) as ctx:
            leave_service.submit(
                self.parent_a, self.kid_a.id, self.session.id, self.wed,
                now=self._at(16, 1))
        self.assertIn('已经开始了', str(ctx.exception))
        self.assertEqual(LeaveRequest.objects.count(), 0)

    def test_can_submit_exactly_at_start(self):
        """正好到开课那一秒仍算「还没开始」，允许请假。

        边界取 ``>`` 而不是 ``>=``：若取 >=，家长在 16:00:00 点提交会
        看到「已经开始了」，而他手机上显示的时间还是 16:00。
        """
        obj = leave_service.submit(
            self.parent_a, self.kid_a.id, self.session.id, self.wed,
            now=self._at(16, 0))
        self.assertEqual(obj.status, 'active')

    def test_cannot_submit_for_other_parents_child(self):
        """家长 A 不能给家长 B 的孩子请假。

        这是越权：请求本身完全合法（参数都对），
        日志里也看不出异常，只有名单会莫名其妙少一个人。
        """
        with self.assertRaises(leave_service.LeaveError) as ctx:
            leave_service.submit(
                self.parent_a, self.kid_b.id, self.session.id, self.wed,
                now=self._at(9, 0))
        self.assertIn('不是你的孩子', str(ctx.exception))
        self.assertEqual(LeaveRequest.objects.count(), 0)

    def test_cannot_submit_for_class_not_enrolled(self):
        """没报这个班就请不了假 —— 名单上本来就没这个人。"""
        other_course = Course.objects.create(name='Scratch')
        other_class = CourseClass.objects.create(
            name='Scratch班', course=other_course, status='active')
        other_session = ClassSession.objects.create(
            course_class=other_class, weekday=3,
            start_time=datetime.time(10, 0), end_time=datetime.time(12, 0))

        with self.assertRaises(leave_service.LeaveError) as ctx:
            leave_service.submit(
                self.parent_a, self.kid_a.id, other_session.id, self.wed,
                now=self._at(9, 0))
        self.assertIn('没有报这个班', str(ctx.exception))

    def test_missing_session_raises(self):
        with self.assertRaises(leave_service.LeaveError):
            leave_service.submit(
                self.parent_a, self.kid_a.id, 999999, self.wed,
                now=self._at(9, 0))

    def test_leave_does_not_touch_hours(self):
        """请假不写任何课时流水 —— 本项目「签到才消课」。"""
        leave_service.submit(
            self.parent_a, self.kid_a.id, self.session.id, self.wed,
            now=self._at(9, 0))
        self.assertEqual(HourLog.objects.count(), 0)
        self.assertEqual(self.enr_a.remain_hours, 12)


class CancelTests(BaseLeaveTestCase):
    """撤销请假。"""

    def test_cancel_sets_status_and_time(self):
        obj = leave_service.submit(
            self.parent_a, self.kid_a.id, self.session.id, self.wed,
            now=self._at(9, 0))
        done = leave_service.cancel(self.parent_a, obj.id, now=self._at(10, 0))
        self.assertEqual(done.status, 'cancelled')
        self.assertIsNotNone(done.cancel_time)

    def test_cannot_resubmit_after_cancel_via_same_row(self):
        """撤销后再请要新建一行 —— 撤销的那行留着当历史。

        这条只是锁「撤销不是删行」：
        记录还在，且状态是 cancelled。
        """
        obj = leave_service.submit(
            self.parent_a, self.kid_a.id, self.session.id, self.wed,
            now=self._at(9, 0))
        leave_service.cancel(self.parent_a, obj.id, now=self._at(10, 0))
        self.assertTrue(LeaveRequest.objects.filter(id=obj.id).exists())
        self.assertEqual(LeaveRequest.objects.get(id=obj.id).status, 'cancelled')

    def test_can_resubmit_after_cancel(self):
        """撤销后能重新请 —— 唯一约束带 status='active' 条件才有这个行为。

        约束若覆盖所有状态，撤销一次就永远请不了这节课，
        而家长只会看到「已经请过假了」这个莫名其妙的提示。
        """
        first = leave_service.submit(
            self.parent_a, self.kid_a.id, self.session.id, self.wed,
            now=self._at(9, 0))
        leave_service.cancel(self.parent_a, first.id, now=self._at(10, 0))

        second = leave_service.submit(
            self.parent_a, self.kid_a.id, self.session.id, self.wed,
            now=self._at(11, 0))
        self.assertNotEqual(first.id, second.id)
        self.assertEqual(second.status, 'active')
        self.assertEqual(
            LeaveRequest.objects.filter(status='active').count(), 1)

    def test_cannot_cancel_two_times(self):
        obj = leave_service.submit(
            self.parent_a, self.kid_a.id, self.session.id, self.wed,
            now=self._at(9, 0))
        leave_service.cancel(self.parent_a, obj.id, now=self._at(10, 0))
        with self.assertRaises(leave_service.LeaveError) as ctx:
            leave_service.cancel(self.parent_a, obj.id, now=self._at(10, 5))
        self.assertIn('已经撤销过', str(ctx.exception))

    def test_cannot_cancel_after_class_started(self):
        """开课后不能撤 —— 课都上完了，撤销只会让系统重新开始催。"""
        obj = leave_service.submit(
            self.parent_a, self.kid_a.id, self.session.id, self.wed,
            now=self._at(9, 0))
        with self.assertRaises(leave_service.LeaveError) as ctx:
            leave_service.cancel(self.parent_a, obj.id, now=self._at(17, 0))
        self.assertIn('已经开始了', str(ctx.exception))

    def test_cannot_cancel_other_parents_leave(self):
        obj = leave_service.submit(
            self.parent_b, self.kid_b.id, self.session.id, self.wed,
            now=self._at(9, 0))
        with self.assertRaises(leave_service.LeaveError):
            leave_service.cancel(self.parent_a, obj.id, now=self._at(10, 0))


class ReminderSuppressionTests(BaseLeaveTestCase):
    """请假与提醒的关系 —— 这块是请假功能真正的价值。"""

    def test_leave_suppresses_reminder(self):
        """请了假就不再催「别忘了送孩子」。

        不抑制的话，家长明明在线上点过请假，系统还是催他送孩子，
        这会被当成系统坏了。
        """
        leave_service.submit(
            self.parent_a, self.kid_a.id, self.session.id, self.wed,
            now=self._at(9, 0))
        rows = reminder.collect_recipients(self.session, self.wed)
        by_name = {r['student'].name: r for r in rows}
        self.assertIn('已请假', by_name['张小明']['skip_reason'])
        # 另一个没请假的孩子不受影响
        self.assertEqual(by_name['李小红']['skip_reason'], '')

    def test_cancelled_leave_restores_reminder(self):
        """撤销请假后提醒恢复。

        这条是最关键的一条：抑制必须是**实时查询**的，
        不能像 NotifyLog 那样把「跳过」固化成一条永久记录。
        固化了的话家长撤了假也永远收不到催课，而他不知道原因。
        """
        obj = leave_service.submit(
            self.parent_a, self.kid_a.id, self.session.id, self.wed,
            now=self._at(9, 0))
        leave_service.cancel(self.parent_a, obj.id, now=self._at(10, 0))

        rows = reminder.collect_recipients(self.session, self.wed)
        by_name = {r['student'].name: r for r in rows}
        self.assertEqual(by_name['张小明']['skip_reason'], '')

    def test_leave_takes_priority_over_low_hours(self):
        """请假优先于「课时不足」。

        家长已经明确说不来了，这时候提示「课时不足请充值」
        既没意义，又像是趁机催钱。
        """
        HourLog.objects.create(
            enrollment=self.enr_a, delta=-12, reason='consumed',
            session=self.session, lesson_date=datetime.date(2026, 11, 18),
        )
        leave_service.submit(
            self.parent_a, self.kid_a.id, self.session.id, self.wed,
            now=self._at(9, 0))
        rows = reminder.collect_recipients(self.session, self.wed)
        by_name = {r['student'].name: r for r in rows}
        self.assertIn('已请假', by_name['张小明']['skip_reason'])
        self.assertNotIn('课时不足', by_name['张小明']['skip_reason'])

    def test_checked_in_takes_priority_over_leave(self):
        """已经签到了就显示已签到 —— 说明孩子来了，假是多余的。

        顺序上「已签到」先判：家长请了假但孩子还是来了，
        老师也签到了，这时候说「已请假」会让人以为签到错了。
        """
        HourLog.objects.create(
            enrollment=self.enr_a, delta=-1, reason='consumed',
            session=self.session, lesson_date=self.wed,
        )
        leave_service.submit(
            self.parent_a, self.kid_a.id, self.session.id, self.wed,
            now=self._at(9, 0))
        rows = reminder.collect_recipients(self.session, self.wed)
        by_name = {r['student'].name: r for r in rows}
        self.assertIn('已签到', by_name['张小明']['skip_reason'])


class CommandLeaveTests(BaseLeaveTestCase):
    """定时任务命令层面的行为 —— 跳过不能占 NotifyLog 名额。"""

    def test_skipped_leave_does_not_claim(self):
        """请假的人不写 NotifyLog。

        占了名额的话，家长撤销请假后也补发不了 ——
        记录会一直挡着，而库里看起来一切正常。
        """
        from .models import NotifyLog
        leave_service.submit(
            self.parent_a, self.kid_a.id, self.session.id, self.wed,
            now=self._at(9, 0))

        out = StringIO()
        call_command(
            'send_class_reminders', '--dry-run',
            '--date', '2026-11-25', '--force', stdout=out)
        text = out.getvalue()
        self.assertIn('已请假', text)
        self.assertEqual(NotifyLog.objects.count(), 0)

    def test_no_leave_still_reminded(self):
        """没人请假时提醒照发，确认请假判断没把正常流程一起挡掉。"""
        out = StringIO()
        call_command(
            'send_class_reminders', '--dry-run',
            '--date', '2026-11-25', '--force', stdout=out)
        text = out.getvalue()
        self.assertIn('将发送', text)
        self.assertNotIn('已请假', text)


class CheckinRosterLeaveTests(BaseLeaveTestCase):
    """签到页要标出谁请假了。"""

    def test_roster_marks_leave(self):
        from . import checkin as checkin_service
        leave_service.submit(
            self.parent_a, self.kid_a.id, self.session.id, self.wed,
            now=self._at(9, 0))
        roster = checkin_service.lesson_roster(
            self.klass.id, self.wed, self.session.id)
        by_name = {r['student'].name: r for r in roster}
        self.assertTrue(by_name['张小明']['on_leave'])
        self.assertFalse(by_name['李小红']['on_leave'])

    def test_checked_in_child_still_checkable_after_leave(self):
        """请了假的孩子仍可以签到 —— 家长请了假、孩子最后又来了很常见。

        如果这里锁死，老师只能去后台改请假状态才能签到，
        而改状态本身就不该是老师的常规操作。
        """
        from . import checkin as checkin_service
        leave_service.submit(
            self.parent_a, self.kid_a.id, self.session.id, self.wed,
            now=self._at(9, 0))
        result = checkin_service.checkin(
            self.klass.id, self.wed, self.session.id,
            [self.enr_a.id], operator='王老师')
        self.assertEqual(len(result['created']), 1)
        self.assertEqual(LeaveRequest.objects.filter(status='active').count(), 1)


class MyLeavesTests(BaseLeaveTestCase):
    """家长端的记录列表。"""

    def test_lists_own_leaves_only(self):
        leave_service.submit(
            self.parent_a, self.kid_a.id, self.session.id, self.wed,
            now=self._at(9, 0))
        leave_service.submit(
            self.parent_b, self.kid_b.id, self.session.id, self.wed,
            now=self._at(9, 0))

        mine = leave_service.my_leaves(self.parent_a)
        self.assertEqual(len(mine), 1)
        self.assertEqual(mine[0].student_id, self.kid_a.id)

    def test_includes_cancelled(self):
        """已撤销的记录也要列出来。

        不列的话家长会反复怀疑自己到底提交成功没有。
        """
        obj = leave_service.submit(
            self.parent_a, self.kid_a.id, self.session.id, self.wed,
            now=self._at(9, 0))
        leave_service.cancel(self.parent_a, obj.id, now=self._at(10, 0))
        mine = leave_service.my_leaves(self.parent_a)
        self.assertEqual(len(mine), 1)
        self.assertEqual(mine[0].status, 'cancelled')

    def test_empty_when_no_children(self):
        loner = Parent.objects.create(openid='o_lone', mobile='13800000009')
        self.assertEqual(leave_service.my_leaves(loner), [])
