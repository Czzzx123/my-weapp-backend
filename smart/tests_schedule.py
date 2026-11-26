"""教务（课表 / 课时）接口与课时账本测试。

运行方式：

    ./.venv/Scripts/python.exe manage.py test smart.tests_schedule -v 2

**本文件守的是四条线**：

1. **越权隔离** —— 家长 A 只能看到自己孩子的课表和课时。
   这条最容易在重构时被破坏：有人为了「少一次 join」把
   ``student_id__in`` 过滤去掉，测试就全绿但数据全泄。
2. **剩余课时只能由流水汇总** —— 任何「在 Enrollment 上存一个余额列」
   的改动都会让 ``test_balance_comes_from_logs`` 失败。
3. **同一节课不能扣两次课时** —— 靠数据库唯一约束（迁移 0017），
   不是靠视图层的 if 判断。视图层判断在并发下会失效。
4. **未登录必须 401** —— 课表含孩子姓名，属隐私数据。
"""

from datetime import date
from decimal import Decimal

from django.db import IntegrityError, transaction
from django.test import TestCase
from rest_framework.test import APIClient
from rest_framework_simplejwt.tokens import RefreshToken

from smart.models import (
    ClassSession,
    Course,
    CourseClass,
    Enrollment,
    HourLog,
    Parent,
    ParentStudent,
    Student,
)

SCHEDULE_URL = '/smart/schedule/'
HOURS_URL = '/smart/hours/'


class BaseEduTestCase(TestCase):
    """建好「两个家长、三个孩子、两个班」的基础场景。

    刻意做成两个家长各有一个孩子，且让其中一个孩子同时报两个班 ——
    这样「越权」和「多班」两类 bug 都能被同一个 fixture 抓出来。
    """

    def setUp(self):
        self.client = APIClient(HTTP_HOST='127.0.0.1')

        # 两个家长
        self.parent_a = Parent.objects.create(openid='o_PARENT_A', mobile='13800000001')
        self.parent_b = Parent.objects.create(openid='o_PARENT_B', mobile='13800000002')

        # 各一个孩子
        self.kid_a = Student.objects.create(
            name='张小明', mobile='13800000001', grade='三年级')
        self.kid_b = Student.objects.create(
            name='李小红', mobile='13800000002', grade='五年级')
        ParentStudent.objects.create(parent=self.parent_a, student=self.kid_a)
        ParentStudent.objects.create(parent=self.parent_b, student=self.kid_b)

        # 课程 + 班级 + 时段
        self.course_py = Course.objects.create(name='Python编程', category='科技')
        self.course_sc = Course.objects.create(name='Scratch创意', category='科技')
        self.class_py = CourseClass.objects.create(
            course=self.course_py, name='Python基础A班',
            teacher='王老师', room='301')
        self.class_sc = CourseClass.objects.create(
            course=self.course_sc, name='Scratch创意B班',
            teacher='李老师', room='302')

        self.sess_py_wed = ClassSession.objects.create(
            course_class=self.class_py, weekday=3,
            start_time='16:00', end_time='18:00')
        self.sess_py_sat = ClassSession.objects.create(
            course_class=self.class_py, weekday=6,
            start_time='10:00', end_time='12:00')
        self.sess_sc_fri = ClassSession.objects.create(
            course_class=self.class_sc, weekday=5,
            start_time='15:30', end_time='17:30')

        # 报名：kid_a 报两个班，kid_b 报一个
        self.enroll_a_py = Enrollment.objects.create(
            student=self.kid_a, course_class=self.class_py,
            total_hours=12, amount=Decimal('2400.00'))
        self.enroll_a_sc = Enrollment.objects.create(
            student=self.kid_a, course_class=self.class_sc,
            total_hours=16, amount=Decimal('3200.00'))
        self.enroll_b_py = Enrollment.objects.create(
            student=self.kid_b, course_class=self.class_py,
            total_hours=8, amount=Decimal('1600.00'))

    def token_for(self, parent):
        return str(RefreshToken.for_user(parent).access_token)

    def get(self, url, parent):
        return self.client.get(url, HTTP_TOKEN=self.token_for(parent))


class ScheduleApiTests(BaseEduTestCase):
    """GET /smart/schedule/"""

    def test_anonymous_is_rejected(self):
        """未登录必须 401。

        课表里有孩子姓名、班级、上课时间，是隐私数据。
        若有人给视图加 ``AllowAny``，这条会失败 —— 那是数据泄漏。
        """
        resp = self.client.get(SCHEDULE_URL)
        self.assertEqual(resp.status_code, 401)

    def test_returns_own_children_only(self):
        """家长 A 看不到家长 B 的孩子。"""
        resp = self.get(SCHEDULE_URL, self.parent_a)
        body = resp.json()
        self.assertEqual(body['code'], 100)

        names = {item['student_name'] for item in body['schedule']}
        self.assertEqual(names, {'张小明'})

    def test_other_parent_sees_their_own(self):
        """反向验证：家长 B 也只看得到自己的孩子。

        只测一边不够 —— 万一过滤条件写反了（拿到的是「别人的」），
        单向断言照样能过。
        """
        body = self.get(SCHEDULE_URL, self.parent_b).json()
        names = {item['student_name'] for item in body['schedule']}
        self.assertEqual(names, {'李小红'})

    def test_one_class_two_sessions_expands_to_two_items(self):
        """一个班上两次课 → 课表里两条。

        这是家长端最直观的预期：周三一次、周六一次，各自独立显示。
        """
        body = self.get(SCHEDULE_URL, self.parent_a).json()
        py_items = [i for i in body['schedule'] if i['class_name'] == 'Python基础A班']
        self.assertEqual(len(py_items), 2)
        self.assertEqual({i['weekday'] for i in py_items}, {3, 6})

    def test_total_count_for_multi_class_child(self):
        """张小明报两个班（A班两个时段 + B班一个时段）= 3 条。"""
        body = self.get(SCHEDULE_URL, self.parent_a).json()
        self.assertEqual(len(body['schedule']), 3)

    def test_sorted_by_weekday_then_time(self):
        """必须按周几排序，前端直接渲染就是一列有序的课表。"""
        body = self.get(SCHEDULE_URL, self.parent_a).json()
        weekdays = [i['weekday'] for i in body['schedule']]
        self.assertEqual(weekdays, sorted(weekdays))

    def test_time_format_is_hh_mm(self):
        """时间是 ``16:00``，不是 ``16:00:00``。

        前端直接显示在卡片上，多两个 ``:00`` 很显眼。
        """
        body = self.get(SCHEDULE_URL, self.parent_a).json()
        item = body['schedule'][0]
        self.assertRegex(item['start_time'], r'^\d{2}:\d{2}$')
        self.assertRegex(item['end_time'], r'^\d{2}:\d{2}$')

    def test_carries_display_labels(self):
        """``weekday_label`` / ``status_label`` 由后端给，前端不自己翻译。

        理由：前端若自己维护一份「1=周一」映射，后端改枚举时两边就会不一致。
        """
        body = self.get(SCHEDULE_URL, self.parent_a).json()
        item = body['schedule'][0]
        self.assertIn(item['weekday_label'], ('周一', '周二', '周三', '周四', '周五', '周六', '周日'))
        self.assertEqual(item['status_label'], '正常')

    def test_course_and_class_names_both_present(self):
        """课程名和班级名都要给。

        卡片上显示「Python基础A班」，但分组标题要「Python编程」。
        少一个前端就得自己再查一次。
        """
        body = self.get(SCHEDULE_URL, self.parent_a).json()
        item = body['schedule'][0]
        self.assertEqual(item['course_name'], 'Python编程')
        self.assertEqual(item['class_name'], 'Python基础A班')

    def test_no_children_returns_empty_array_not_error(self):
        """没绑孩子的家长：返回空数组，不是报错。

        报错会让家长以为系统坏了；空数组前端显示「还没有课表」。
        """
        lonely = Parent.objects.create(openid='o_LONELY', mobile='13900000000')
        resp = self.get(SCHEDULE_URL, lonely)
        self.assertEqual(resp.status_code, 200)
        body = resp.json()
        self.assertEqual(body['code'], 100)
        self.assertEqual(body['schedule'], [])

    def test_closed_class_is_hidden(self):
        """已结班的班不该出现在课表上，否则家长会白跑一趟。"""
        self.class_py.status = 'closed'
        self.class_py.save(update_fields=['status'])

        body = self.get(SCHEDULE_URL, self.parent_a).json()
        class_names = {i['class_name'] for i in body['schedule']}
        self.assertNotIn('Python基础A班', class_names)
        # 另一个班仍在，说明不是「全被过滤掉了」的假阳性
        self.assertIn('Scratch创意B班', class_names)

    def test_suspended_session_still_returned_but_marked(self):
        """停课的时段照样返回，用 status 标出来。

        **不能直接过滤掉** —— 家长会以为「这周没课」，
        实际是「课停了但没人通知」。两种情况的处置完全不同。
        """
        self.sess_py_wed.status = 'suspended'
        self.sess_py_wed.save(update_fields=['status'])

        body = self.get(SCHEDULE_URL, self.parent_a).json()
        wed = [i for i in body['schedule'] if i['session_id'] == self.sess_py_wed.id]
        self.assertEqual(len(wed), 1, '停课时段被过滤掉了，家长会以为没课')
        self.assertEqual(wed[0]['status'], 'suspended')
        self.assertEqual(wed[0]['status_label'], '停课')


class HoursApiTests(BaseEduTestCase):
    """GET /smart/hours/"""

    def test_anonymous_is_rejected(self):
        resp = self.client.get(HOURS_URL)
        self.assertEqual(resp.status_code, 401)

    def test_returns_own_children_only(self):
        body = self.get(HOURS_URL, self.parent_a).json()
        self.assertEqual(body['code'], 100)
        names = {i['student_name'] for i in body['hours']}
        self.assertEqual(names, {'张小明'})

    def test_no_logs_means_full_balance(self):
        """刚报名的状态：已用 0，剩余 = 总额度，进度 100%。"""
        body = self.get(HOURS_URL, self.parent_a).json()
        item = next(i for i in body['hours'] if i['class_name'] == 'Python基础A班')
        self.assertEqual(item['total'], 12)
        self.assertEqual(item['used'], 0)
        self.assertEqual(item['remain'], 12)
        self.assertEqual(item['remain_percent'], 100)

    def test_consumed_log_reduces_balance(self):
        """消课 2 次 → 已用 2、剩余 10、进度 83%。"""
        HourLog.objects.create(
            enrollment=self.enroll_a_py, delta=-1, reason='consumed',
            session=self.sess_py_wed, lesson_date=date(2026, 11, 18))
        HourLog.objects.create(
            enrollment=self.enroll_a_py, delta=-1, reason='consumed',
            session=self.sess_py_wed, lesson_date=date(2026, 11, 25))

        body = self.get(HOURS_URL, self.parent_a).json()
        item = next(i for i in body['hours'] if i['class_name'] == 'Python基础A班')
        self.assertEqual(item['used'], 2)
        self.assertEqual(item['remain'], 10)
        self.assertEqual(item['remain_percent'], 83)  # round(10/12*100)

    def test_balance_comes_from_logs_not_a_column(self):
        """直接往流水插 +5，剩余必须跟着变。

        这条是「余额不落冗余列」的锁。如果有人图省事在
        Enrollment 上加个 ``remain_hours`` 列并缓存，这里会失败。
        """
        HourLog.objects.create(
            enrollment=self.enroll_a_py, delta=5, reason='adjust',
            remark='补课赠送')

        body = self.get(HOURS_URL, self.parent_a).json()
        item = next(i for i in body['hours'] if i['class_name'] == 'Python基础A班')
        self.assertEqual(item['remain'], 17)
        self.assertEqual(item['total'], 12, 'total 是购买额度，不该被流水改动')

    def test_remain_percent_clamped_when_overdrawn(self):
        """超额消课（剩余为负）时进度条贴底 0%，不能出现负宽度。

        正常流程下不该出现，但历史数据或手工调整可能造出来，
        前端拿到负数会渲染出诡异的进度条。
        """
        HourLog.objects.create(
            enrollment=self.enroll_b_py, delta=-20, reason='adjust',
            remark='手工造超额场景')

        body = self.get(HOURS_URL, self.parent_b).json()
        item = body['hours'][0]
        self.assertEqual(item['remain'], -12)
        self.assertEqual(item['remain_percent'], 0)

    def test_zero_total_does_not_divide_by_zero(self):
        """报名时忘填额度（total=0）不能让接口 500。"""
        self.enroll_b_py.total_hours = 0
        self.enroll_b_py.save(update_fields=['total_hours'])

        resp = self.get(HOURS_URL, self.parent_b)
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.json()['hours'][0]['remain_percent'], 0)

    def test_finished_enrollment_still_visible(self):
        """已结课的报名照样返回，前端按 status 分组显示。

        直接过滤掉的话，家长就查不到历史账了。
        """
        self.enroll_a_sc.status = 'finished'
        self.enroll_a_sc.save(update_fields=['status'])

        body = self.get(HOURS_URL, self.parent_a).json()
        item = next(i for i in body['hours'] if i['class_name'] == 'Scratch创意B班')
        self.assertEqual(item['status'], 'finished')
        self.assertEqual(item['status_label'], '已结课')

    def test_unit_label_passed_through(self):
        """单位是「课时」还是「次课」纯展示，原样传给前端。"""
        self.enroll_a_py.unit_label = '次课'
        self.enroll_a_py.save(update_fields=['unit_label'])

        body = self.get(HOURS_URL, self.parent_a).json()
        item = next(i for i in body['hours'] if i['class_name'] == 'Python基础A班')
        self.assertEqual(item['unit_label'], '次课')

    def test_no_children_returns_empty_array(self):
        lonely = Parent.objects.create(openid='o_LONELY_H', mobile='13900000001')
        body = self.get(HOURS_URL, lonely).json()
        self.assertEqual(body['hours'], [])


class HourLogConstraintTests(BaseEduTestCase):
    """课时账本的数据库约束（迁移 0017）。"""

    def test_same_lesson_cannot_be_charged_twice(self):
        """同一节课消课两次 → 数据库直接拒绝。

        **必须由数据库拒绝，不能只靠视图层 if 判断** ——
        老师手滑连点两次提交时，两个请求可能同时通过 if 检查。
        """
        HourLog.objects.create(
            enrollment=self.enroll_a_py, delta=-1, reason='consumed',
            session=self.sess_py_wed, lesson_date=date(2026, 11, 25))

        with self.assertRaises(IntegrityError):
            with transaction.atomic():
                HourLog.objects.create(
                    enrollment=self.enroll_a_py, delta=-1, reason='consumed',
                    session=self.sess_py_wed, lesson_date=date(2026, 11, 25))

    def test_different_dates_are_allowed(self):
        """同一时段不同日期是不同课，必须都能扣。"""
        HourLog.objects.create(
            enrollment=self.enroll_a_py, delta=-1, reason='consumed',
            session=self.sess_py_wed, lesson_date=date(2026, 11, 25))
        HourLog.objects.create(
            enrollment=self.enroll_a_py, delta=-1, reason='consumed',
            session=self.sess_py_wed, lesson_date=date(2026, 12, 2))

        self.assertEqual(self.enroll_a_py.remain_hours, 10)

    def test_refund_on_same_lesson_is_allowed(self):
        """冲正必须能针对同一节课反向记一笔。

        约束带 ``condition=Q(reason='consumed')`` 就是为了这个：
        若约束覆盖所有 reason，冲正会被自己挡住，账永远无法修正。
        """
        HourLog.objects.create(
            enrollment=self.enroll_a_py, delta=-1, reason='consumed',
            session=self.sess_py_wed, lesson_date=date(2026, 11, 25))
        HourLog.objects.create(
            enrollment=self.enroll_a_py, delta=1, reason='refund',
            session=self.sess_py_wed, lesson_date=date(2026, 11, 25))

        self.assertEqual(self.enroll_a_py.remain_hours, 12)

    def test_manual_adjust_without_session_can_repeat(self):
        """手工调整不填时段，可以记任意多条。

        SQL 唯一约束里 NULL 不等于 NULL，所以这些记录天然不会互相冲突。
        这条测试是那个「隐式行为」的说明书 —— 将来有人把约束改成
        覆盖全部 reason 时，这里会失败。
        """
        HourLog.objects.create(
            enrollment=self.enroll_a_py, delta=1, reason='adjust', remark='赠送1')
        HourLog.objects.create(
            enrollment=self.enroll_a_py, delta=1, reason='adjust', remark='赠送2')

        self.assertEqual(self.enroll_a_py.remain_hours, 14)

    def test_used_and_remain_properties_agree_with_api(self):
        """模型属性和接口返回值必须一致。

        两处算余额（``Enrollment.remain_hours`` 和 serializer）
        一旦算法分叉，后台看到的数和家长看到的数就会不一样。
        """
        HourLog.objects.create(
            enrollment=self.enroll_a_py, delta=-3, reason='consumed',
            session=self.sess_py_wed, lesson_date=date(2026, 11, 25))

        body = self.get(HOURS_URL, self.parent_a).json()
        item = next(i for i in body['hours'] if i['class_name'] == 'Python基础A班')

        self.enroll_a_py.refresh_from_db()
        self.assertEqual(item['used'], self.enroll_a_py.used_hours)
        self.assertEqual(item['remain'], self.enroll_a_py.remain_hours)
