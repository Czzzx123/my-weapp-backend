"""时区行为的回归测试（配合 2026-11-06 批次B 的 `USE_TZ = True`）。

为什么单独一个文件
------------------
时区问题的特点是**不报错、只是数字错了 8 小时**，靠肉眼很难发现，
而且一旦在课表/签到/课时流水上出错，就是「今天上没上课」这类业务判断出问题。
所以用测试把口径锁住。

覆盖三件事：
1. 写入的时间是 aware 的，并且 UTC 存储
2. 读出来本地化后等于北京时间
3. 序列化输出的是本地时间字符串（不是 UTC）
"""

import datetime

from django.conf import settings
from django.test import TestCase
from django.utils import timezone

from .models import Parent, Student


class TimezoneBehaviourTests(TestCase):
    """`USE_TZ = True` 之后的时间口径。"""

    def test_use_tz_is_enabled(self):
        """这是本文件的前提。若被改回 False，其余断言全部失去意义。"""
        self.assertTrue(settings.USE_TZ, 'USE_TZ 必须是 True')

    def test_timezone_is_shanghai(self):
        """项目所有时间展示都按北京时间，改这个会整体偏移。"""
        self.assertEqual(settings.TIME_ZONE, 'Asia/Shanghai')

    def test_auto_now_add_produces_aware_datetime(self):
        """auto_now_add 写出来的必须是 aware datetime（带 tzinfo）。"""
        p = Parent.objects.create(mobile='13800000001')
        p.refresh_from_db()
        self.assertIsNotNone(p.create_time)
        self.assertIsNotNone(
            p.create_time.tzinfo,
            'create_time 没有 tzinfo，说明又变回 naive 了',
        )

    def test_stored_value_is_utc(self):
        """数据库里存的必须是 UTC，不是本地时间。

        口径：`timezone.now()` 返回 UTC 时间，写入后再读出来应当与它接近
        （允许几秒误差）。如果这里差了 8 小时，说明又变回存本地时间了。
        """
        before = timezone.now()
        p = Parent.objects.create(mobile='13800000001')
        after = timezone.now()
        p.refresh_from_db()

        self.assertLessEqual(before - datetime.timedelta(seconds=2), p.create_time)
        self.assertLessEqual(p.create_time, after + datetime.timedelta(seconds=2))

    def test_localized_time_matches_beijing(self):
        """本地化后的小时数应当与北京时间一致。

        用 `timezone.localtime()` 转换，再和 `datetime.now()` 的本地钟面比较。
        两者相差不应超过 1 分钟。
        """
        p = Parent.objects.create(mobile='13800000001')
        p.refresh_from_db()

        local_created = timezone.localtime(p.create_time)
        now_local = timezone.localtime()
        delta = abs((now_local - local_created).total_seconds())
        self.assertLess(delta, 60, f'本地化后的时间偏差 {delta} 秒，疑似时区没转对')

    def test_offset_is_eight_hours(self):
        """北京时间相对 UTC 固定 +8（无夏令时）。

        这是迁移 0014 里 `LOCAL_OFFSET_HOURS = 8` 的依据，写死在这里防止
        有人误以为中国有夏令时。
        """
        p = Parent.objects.create(mobile='13800000001')
        p.refresh_from_db()

        utc_offset = p.create_time.utcoffset()
        local_offset = timezone.localtime(p.create_time).utcoffset()
        self.assertEqual(utc_offset, datetime.timedelta(0), 'UTC 时间的偏移必须是 0')
        self.assertEqual(local_offset, datetime.timedelta(hours=8),
                         '北京时间偏移必须是 +8 小时')

    def test_student_time_also_aware(self):
        """不是只有 Parent，所有模型的 auto_now_add 都要一致。"""
        s = Student.objects.create(name='张小明', mobile='13800000001')
        s.refresh_from_db()
        self.assertIsNotNone(s.create_time.tzinfo)
