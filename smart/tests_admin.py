"""教务后台 1.5 的回归测试：课时账本 + 手机号校验 + 后台路径。

为什么值得单独写测试
--------------------
这三块都是**「错了不会报错，只是悄悄算错」**的类型：

1. 课时账本：如果内联的只读判定写错，`.get_readonly_fields()` 会把**新增行**也锁死，
   表现是后台一条流水都存不进去（2026-11-09 实际踩过）。反过来若放得太宽，
   旧账能被事后改写，账就烂了。
2. 手机号校验：录进 `abc` 不会报错，只会让家长**永远匹配不到孩子**，
   而且现场看不出来，等家长投诉才发现。
3. 后台路径：改回 `/admin/` 不会报错，只是把入口重新暴露给扫描器。

这些都能在几十毫秒内跑完，值得钉死。
"""

from decimal import Decimal

from django.contrib.auth.models import User
from django.test import Client, TestCase
from django.urls import reverse

from .models import (
    Course, CourseClass, Enrollment, HourLog, Student,
)

# django.test.Client() 默认 Host 是 `testserver`，不在 ALLOWED_HOSTS 里，
# Admin 页面会全部 400。必须显式指定一个合法 Host。
HOST = {'HTTP_HOST': '127.0.0.1'}


class AdminBase(TestCase):
    """建一个超管并登录，供所有 Admin 用例复用。"""

    def setUp(self):
        self.admin = User.objects.create_superuser(
            username='root', email='root@example.com', password='pw-for-test')
        self.client = Client(**HOST)
        self.client.force_login(self.admin)

    def make_enrollment(self, total_hours=12):
        course = Course.objects.create(name='少儿编程')
        klass = CourseClass.objects.create(
            course=course, name='编程周六班', teacher='王老师')
        student = Student.objects.create(name='张小明', mobile='13800000001')
        return Enrollment.objects.create(
            student=student, course_class=klass, total_hours=total_hours)


class HourLogLedgerTests(AdminBase):
    """课时流水的账本规则：旧账只读，新账只能追加。"""

    def _inline_payload(self, enrollment, logs):
        """拼出报名编辑页的完整 POST 数据（含内联流水）。

        `logs` 是 dict 列表，每项可有 id / delta / reason / remark。
        """
        payload = {
            'student': enrollment.student_id,
            'course_class': enrollment.course_class_id,
            'status': enrollment.status,
            'total_hours': enrollment.total_hours,
            'unit_label': enrollment.unit_label,
            'amount': '', 'remark': '',
        }
        payload.update({
            'hour_logs-TOTAL_FORMS': str(len(logs)),
            'hour_logs-INITIAL_FORMS': str(sum(1 for x in logs if x.get('id'))),
            'hour_logs-MIN_NUM_FORMS': '0',
            'hour_logs-MAX_NUM_FORMS': '1000',
        })
        for i, log in enumerate(logs):
            payload[f'hour_logs-{i}-id'] = str(log.get('id', ''))
            payload[f'hour_logs-{i}-delta'] = str(log.get('delta', ''))
            payload[f'hour_logs-{i}-reason'] = log.get('reason', 'consumed')
            payload[f'hour_logs-{i}-session'] = ''
            payload[f'hour_logs-{i}-operator'] = log.get('operator', 'root')
            payload[f'hour_logs-{i}-remark'] = log.get('remark', '')
        return payload

    def _post_inline(self, enrollment, logs):
        return self.client.post(
            reverse('admin:smart_enrollment_change', args=[enrollment.pk]),
            self._inline_payload(enrollment, logs),
        )

    def test_add_page_renders_editable_delta_field(self):
        """编辑页必须给「新增行」渲染可编辑的 delta 输入框。

        这是最容易写错的点：用 `get_readonly_fields(request, obj)` 时 `obj`
        是父对象（报名记录），编辑页永远不为 None，于是连新增行也被锁死，
        结果全系统一条流水都写不进去。本用例就是防止这个回归。
        """
        enrollment = self.make_enrollment()
        response = self.client.get(
            reverse('admin:smart_enrollment_change', args=[enrollment.pk]))
        html = response.content.decode()
        self.assertIn('name="hour_logs-0-delta"', html)
        self.assertNotIn('name="hour_logs-0-delta" disabled', html)

    def test_can_append_consumption_log(self):
        """签到消课：追加一条 -1，剩余课时应从 12 变 11。"""
        enrollment = self.make_enrollment(total_hours=12)
        response = self._post_inline(enrollment, [
            {'delta': -1, 'reason': 'consumed', 'remark': '签到消课'},
        ])
        self.assertEqual(response.status_code, 302, '表单未通过校验')

        enrollment.refresh_from_db()
        self.assertEqual(HourLog.objects.filter(enrollment=enrollment).count(), 1)
        self.assertEqual(enrollment.used_hours, 1)
        self.assertEqual(enrollment.remain_hours, 11)

    def test_old_log_cannot_be_edited(self):
        """已保存的流水提交新值必须被忽略（账本不可事后改）。"""
        enrollment = self.make_enrollment(total_hours=12)
        self._post_inline(enrollment, [{'delta': -1, 'reason': 'consumed'}])

        old = HourLog.objects.get(enrollment=enrollment)
        response = self._post_inline(enrollment, [
            # 篡改旧记录：把 -1 改成 -100，remark 也改掉
            {'id': old.pk, 'delta': -100, 'reason': 'adjust', 'remark': '篡改'},
            # 同时合法追加一条冲正
            {'delta': 2, 'reason': 'refund', 'remark': '冲正'},
        ])
        self.assertEqual(response.status_code, 302)

        old.refresh_from_db()
        self.assertEqual(old.delta, -1, '旧账 delta 被改写了')
        self.assertEqual(old.remark, '', '旧账 remark 被改写了')
        self.assertEqual(old.reason, 'consumed', '旧账 reason 被改写了')

        self.assertEqual(HourLog.objects.filter(enrollment=enrollment).count(), 2)
        enrollment.refresh_from_db()
        self.assertEqual(enrollment.remain_hours, 13, '12 - 1 + 2 应为 13')

    def test_hourlog_list_has_no_add_button(self):
        """流水列表页不提供全局新增入口（避免选错报名记录）。"""
        response = self.client.get(reverse('admin:smart_hourlog_changelist'))
        self.assertEqual(response.status_code, 200)
        self.assertNotIn(
            reverse('admin:smart_hourlog_add'),
            response.content.decode(),
        )

    def test_hourlog_detail_is_not_editable(self):
        """流水详情页只读：没有保存按钮。"""
        enrollment = self.make_enrollment()
        self._post_inline(enrollment, [{'delta': -1, 'reason': 'consumed'}])
        log = HourLog.objects.get(enrollment=enrollment)

        response = self.client.get(
            reverse('admin:smart_hourlog_change', args=[log.pk]))
        # has_change_permission=False 时 Django 返回 403 或只读视图，
        # 两者都算「不能改」；这里断言不允许出现保存动作。
        if response.status_code == 200:
            self.assertNotIn('name="_save"', response.content.decode())
        else:
            self.assertIn(response.status_code, (403,))


class StudentMobileValidationTests(AdminBase):
    """`Student.mobile` 的手机号格式校验。

    脏号码不会报错，只会让家长永远匹配不到孩子 —— 必须挡在录入那一步。
    """

    def _post_student(self, mobile):
        return self.client.post(reverse('admin:smart_student_add'), {
            'name': '测试娃',
            'grade': '三年级',
            'status': 'active',
            'mobile': mobile,
            'remark': '',
        })

    def test_valid_mobile_accepted(self):
        response = self._post_student('13700000001')
        self.assertEqual(response.status_code, 302)
        self.assertTrue(Student.objects.filter(mobile='13700000001').exists())

    def test_blank_mobile_accepted(self):
        """先建学生、后补手机号是既定用法，空值必须允许。"""
        response = self._post_student('')
        self.assertEqual(response.status_code, 302)
        self.assertTrue(Student.objects.filter(name='测试娃').exists())

    def test_invalid_mobile_rejected(self):
        for bad in ('abc', '1380000000', '138000000001', '23800000001'):
            with self.subTest(mobile=bad):
                Student.objects.all().delete()
                response = self._post_student(bad)
                # 校验失败时 Django 重新渲染表单（200），不会重定向
                self.assertEqual(response.status_code, 200,
                                 f'{bad} 应被拒绝')
                self.assertFalse(Student.objects.exists(),
                                 f'{bad} 竟然存进去了')


class AdminPathTests(AdminBase):
    """后台路径必须是 `/wl-manage/`，默认 `/admin/` 应 404。"""

    def test_wl_manage_works(self):
        response = self.client.get('/wl-manage/')
        self.assertEqual(response.status_code, 200)

    def test_default_admin_path_is_gone(self):
        response = self.client.get('/admin/')
        self.assertEqual(response.status_code, 404)


class EnrollmentHoursTests(AdminBase):
    """课时余额只能由流水汇总得出，不存冗余列。"""

    def test_remain_hours_starts_at_total(self):
        enrollment = self.make_enrollment(total_hours=16)
        self.assertEqual(enrollment.remain_hours, 16)
        self.assertEqual(enrollment.used_hours, 0)

    def test_remain_hours_is_sum_of_logs(self):
        """余额 = 总额度 + Σdelta，正负都算进去。"""
        enrollment = self.make_enrollment(total_hours=12)
        HourLog.objects.create(enrollment=enrollment, delta=-1, reason='consumed')
        HourLog.objects.create(enrollment=enrollment, delta=-1, reason='consumed')

        self.assertEqual(enrollment.used_hours, 2)
        self.assertEqual(enrollment.remain_hours, 10)

        # 冲正 +2 抵消掉两次消课，净消耗归零
        HourLog.objects.create(enrollment=enrollment, delta=2, reason='refund')
        self.assertEqual(enrollment.used_hours, 0)
        self.assertEqual(enrollment.remain_hours, 12)

    def test_negative_remain_hours_is_not_blocked_at_model_layer(self):
        """模型层不禁止负余额，校余额是业务层的事（阶段 3.2 决策）。

        这里把当前行为钉住：后续若决定在模型/服务层加「签到前校余额」，
        应当改这个用例并同步手册 3.2。
        """
        enrollment = self.make_enrollment(total_hours=1)
        HourLog.objects.create(enrollment=enrollment, delta=-1, reason='consumed')
        HourLog.objects.create(enrollment=enrollment, delta=-1, reason='consumed')
        self.assertEqual(enrollment.remain_hours, -1)

    def test_amount_accepts_decimal(self):
        enrollment = self.make_enrollment()
        enrollment.amount = Decimal('2880.00')
        enrollment.full_clean()
        enrollment.save()
        enrollment.refresh_from_db()
        self.assertEqual(enrollment.amount, Decimal('2880.00'))
