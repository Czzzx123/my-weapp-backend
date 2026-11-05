from django.db import models

# ============================================================
# 未蓝科技 · 智慧教育 —— 数据模型
# ============================================================


class Welcome(models.Model):
    """启动欢迎页（App 开屏广告）"""

    img = models.ImageField(upload_to='welcome', default='/welcome/slash.png')
    order = models.IntegerField()
    create_time = models.DateTimeField(auto_now_add=True)
    is_delete = models.BooleanField(default=False)

    class Meta:
        verbose_name_plural = '欢迎表'

    def __str__(self):
        return str(self.img)


class Banner(models.Model):
    """首页轮播图"""

    img = models.ImageField(upload_to='banner', default='banner1.png', verbose_name='图片')
    order = models.IntegerField(verbose_name='顺序')
    create_time = models.DateTimeField(auto_now=True, verbose_name='创建时间')
    is_delete = models.BooleanField(default=False, verbose_name='是否删除')

    class Meta:
        verbose_name_plural = '轮播图'

    def __str__(self):
        return str(self.img)


class Notice(models.Model):
    """公告 / 通知"""

    title = models.CharField(max_length=64, verbose_name='公告标题')
    content = models.TextField(verbose_name='内容')
    img = models.ImageField(upload_to='notice', default='notice.png', verbose_name='公告图片')
    create_time = models.DateTimeField(auto_now=True, verbose_name='创建时间')

    class Meta:
        verbose_name_plural = '公告表'

    def __str__(self):
        return self.title


class UserInfo(models.Model):
    """用户（登录主体）

    注意：这是旧智慧社区项目的遗留表，不是本项目的主要用户表。
    本项目的家长/老师身份用下方 Parent 表承载，两者互不影响。
    """

    name = models.CharField(verbose_name='姓名', max_length=32)
    avatar = models.FileField(verbose_name='头像', max_length=128, upload_to='avatar')
    create_date = models.DateTimeField(verbose_name='日期', auto_now_add=True)
    score = models.IntegerField(verbose_name='积分', default=0)

    # 用户需要用手机号登录
    mobile = models.CharField(max_length=11, null=True, verbose_name='手机号')

    class Meta:
        verbose_name_plural = '用户表（遗留）'

    def __str__(self):
        return self.name


# ============================================================
# A 组 · 家长体系（登录 / 绑定用）
# ============================================================


class Parent(models.Model):
    """家长 / 老师 / 管理员统一身份

    两个登录入口共用这一张表：
      - 入口 A（手机号验证码）：先有 mobile
      - 入口 B（微信静默）：先有 openid
    后到的字段用 UPDATE 补上，不新建记录。
    """

    ROLE_CHOICES = (
        ('parent', '家长'),
        ('teacher', '老师'),
        ('admin', '管理员'),
    )

    openid = models.CharField(
        verbose_name='微信 openid', max_length=64,
        null=True, blank=True, db_index=True,
        help_text='wx.login 换来的唯一身份，微信登录入口的判断依据')
    unionid = models.CharField(
        verbose_name='微信 unionid', max_length=64,
        null=True, blank=True,
        help_text='小程序暂无，将来接开放平台时用')
    mobile = models.CharField(
        verbose_name='手机号', max_length=11,
        null=True, blank=True, db_index=True,
        help_text='孩子绑定的匹配依据，非唯一（二孩家庭同号）')
    nickname = models.CharField(
        verbose_name='昵称', max_length=32, blank=True, default='')
    avatar = models.CharField(
        verbose_name='头像 URL', max_length=255,
        null=True, blank=True,
        help_text='微信头像本就是外链，直接存 URL 字符串')
    role = models.CharField(
        verbose_name='角色', max_length=16,
        choices=ROLE_CHOICES, default='parent')
    create_time = models.DateTimeField(verbose_name='创建时间', auto_now_add=True)
    last_login = models.DateTimeField(
        verbose_name='最后登录', null=True, blank=True)

    class Meta:
        verbose_name_plural = '家长'
        ordering = ['-create_time']

    def __str__(self):
        return f'{self.nickname or self.mobile or "未命名"}({self.get_role_display()})'


class Student(models.Model):
    """学生（教务在 Admin 登记）

    mobile 存的是「家长手机号」，家长登录后按它匹配绑定。
    允许为空：先建学生、后补手机号。
    """

    STATUS_CHOICES = (
        ('active', '在读'),
        ('quit', '退课'),
        ('graduated', '结业'),
    )

    name = models.CharField(verbose_name='姓名', max_length=32)
    mobile = models.CharField(
        verbose_name='家长手机号', max_length=11,
        null=True, blank=True, db_index=True,
        help_text='家长报名时提供，登录时按此匹配。不唯一（二孩同号）')
    grade = models.CharField(
        verbose_name='年级', max_length=16, blank=True, default='')
    status = models.CharField(
        verbose_name='状态', max_length=16,
        choices=STATUS_CHOICES, default='active')
    remark = models.TextField(
        verbose_name='备注', blank=True, default='')
    create_time = models.DateTimeField(verbose_name='创建时间', auto_now_add=True)

    class Meta:
        verbose_name_plural = '学生'
        ordering = ['-create_time']

    def __str__(self):
        return self.name


class ParentStudent(models.Model):
    """家长 ↔ 学生 绑定关系（多对多）

    家长登录后由 match_students() 自动写入，也支持 Admin 手工补绑。
    """

    parent = models.ForeignKey(
        Parent, verbose_name='家长', on_delete=models.CASCADE,
        related_name='parent_students')
    student = models.ForeignKey(
        Student, verbose_name='学生', on_delete=models.CASCADE,
        related_name='parent_students')
    relation = models.CharField(
        verbose_name='关系', max_length=16, blank=True, default='家长')
    bind_time = models.DateTimeField(verbose_name='绑定时间', auto_now_add=True)

    class Meta:
        verbose_name_plural = '家长-学生绑定'
        unique_together = ('parent', 'student')  # 幂等保证
        ordering = ['-bind_time']

    def __str__(self):
        return f'{self.parent} ↔ {self.student}'


# ============================================================
# B 组 · 教学体系（课表 / 课时用）
# ============================================================


class Course(models.Model):
    """课程目录：无人机基础 / 固定翼基础 / C++ 信奥 / 创客 …"""

    name = models.CharField(verbose_name='课程名', max_length=64, unique=True)
    category = models.CharField(
        verbose_name='类别', max_length=32, blank=True, default='',
        help_text='科技 / 体育 / 学科')
    remark = models.TextField(verbose_name='备注', blank=True, default='')
    create_time = models.DateTimeField(verbose_name='创建时间', auto_now_add=True)

    class Meta:
        verbose_name_plural = '课程'
        ordering = ['id']

    def __str__(self):
        return self.name


class CourseClass(models.Model):
    """班级：同一课程下的具体班

    命名注意：不能叫 Class（Python 关键字），所以用 CourseClass。
    """

    STATUS_CHOICES = (
        ('active', '开班中'),
        ('closed', '已结班'),
    )

    course = models.ForeignKey(
        Course, verbose_name='课程', on_delete=models.PROTECT,
        related_name='classes')
    name = models.CharField(
        verbose_name='班级名', max_length=64,
        help_text="如「无人机基础B班」，课表里的格子名")
    level = models.CharField(
        verbose_name='层级', max_length=16, blank=True, default='',
        help_text='基础 / 中级 / 高阶')
    teacher = models.CharField(
        verbose_name='主教师', max_length=32, blank=True, default='',
        help_text='MVP 用字符串，暂不建老师表')
    room = models.CharField(
        verbose_name='教室', max_length=32, blank=True, default='',
        help_text='课表里没有，需要时后补')
    status = models.CharField(
        verbose_name='状态', max_length=16,
        choices=STATUS_CHOICES, default='active')
    remark = models.TextField(verbose_name='备注', blank=True, default='')
    create_time = models.DateTimeField(verbose_name='创建时间', auto_now_add=True)

    class Meta:
        verbose_name_plural = '班级'
        ordering = ['course_id', 'id']

    def __str__(self):
        return self.name


class ClassSession(models.Model):
    """上课时段：一个班一周可有 N 条

    不设「上午/下午/晚上」时段枚举。
    时间只认 weekday + start_time + end_time，
    因为原课表里 16:00-18:00 和 15:30-17:30 都被划进「晚上」，标签不可信。
    """

    STATUS_CHOICES = (
        ('normal', '正常'),
        ('suspended', '停课'),
    )

    WEEKDAY_CHOICES = (
        (1, '周一'), (2, '周二'), (3, '周三'), (4, '周四'),
        (5, '周五'), (6, '周六'), (7, '周日'),
    )

    course_class = models.ForeignKey(
        CourseClass, verbose_name='班级', on_delete=models.CASCADE,
        related_name='sessions')
    weekday = models.SmallIntegerField(
        verbose_name='星期', choices=WEEKDAY_CHOICES,
        help_text='1=周一 … 7=周日')
    start_time = models.TimeField(verbose_name='开始时间')
    end_time = models.TimeField(verbose_name='结束时间')
    substitute_teacher = models.CharField(
        verbose_name='代课教师', max_length=32, blank=True, default='')
    status = models.CharField(
        verbose_name='状态', max_length=16,
        choices=STATUS_CHOICES, default='normal')
    remark = models.TextField(verbose_name='备注', blank=True, default='')

    class Meta:
        verbose_name_plural = '上课时段'
        ordering = ['weekday', 'start_time']
        indexes = [
            models.Index(fields=['weekday', 'start_time']),
        ]

    def __str__(self):
        return f'{self.course_class.name} {self.get_weekday_display()} {self.start_time:%H:%M}-{self.end_time:%H:%M}'


class Enrollment(models.Model):
    """报名：学生 ↔ 班级，并承载课时额度

    课时额度是报名时约定的（「X 元 12 课时 / 16 次课」），
    不来自课表。已用多少、剩余多少一律由 HourLog 流水汇总，这里不存余额。
    """

    STATUS_CHOICES = (
        ('active', '在读'),
        ('finished', '已结课'),
        ('refunded', '已退费'),
    )

    student = models.ForeignKey(
        Student, verbose_name='学生', on_delete=models.CASCADE,
        related_name='enrollments')
    course_class = models.ForeignKey(
        CourseClass, verbose_name='班级', on_delete=models.PROTECT,
        related_name='enrollments')
    total_hours = models.IntegerField(
        verbose_name='购买课时', default=0,
        help_text='报名时约定的额度，如 12 或 16')
    unit_label = models.CharField(
        verbose_name='单位', max_length=8, default='课时',
        help_text="'课时' 或 '次课'，纯展示不参与计算")
    amount = models.DecimalField(
        verbose_name='报名金额', max_digits=10, decimal_places=2,
        null=True, blank=True)
    status = models.CharField(
        verbose_name='状态', max_length=16,
        choices=STATUS_CHOICES, default='active')
    enroll_time = models.DateTimeField(verbose_name='报名时间', auto_now_add=True)
    remark = models.TextField(verbose_name='备注', blank=True, default='')

    class Meta:
        verbose_name_plural = '报名记录'
        unique_together = ('student', 'course_class')
        ordering = ['-enroll_time']

    def __str__(self):
        return f'{self.student.name} - {self.course_class.name}'

    @property
    def used_hours(self):
        """已用课时数（正数表示已消耗）"""
        total = self.hour_logs.aggregate(s=models.Sum('delta'))['s'] or 0
        return -total

    @property
    def remain_hours(self):
        """剩余课时 = 购买额度 + 所有流水（流水里消课是负数）"""
        total = self.hour_logs.aggregate(s=models.Sum('delta'))['s'] or 0
        return self.total_hours + total


class HourLog(models.Model):
    """课时流水：唯一的课时账本

    余额永远由本表汇总得出，绝不在 Enrollment 上存一个数字。
    理由：签到可能重复提交、需要冲正、需要查账，
    存单一数字就会算错且无从追溯。
    """

    REASON_CHOICES = (
        ('consumed', '消课'),
        ('grant', '报名充值'),
        ('refund', '冲正/退课'),
        ('adjust', '手工调整'),
    )

    enrollment = models.ForeignKey(
        Enrollment, verbose_name='报名记录', on_delete=models.CASCADE,
        related_name='hour_logs')
    delta = models.IntegerField(
        verbose_name='变动量',
        help_text='负数 = 消课，正数 = 充值或冲正')
    reason = models.CharField(
        verbose_name='变动原因', max_length=16,
        choices=REASON_CHOICES, default='consumed')
    session = models.ForeignKey(
        ClassSession, verbose_name='上课时段', on_delete=models.SET_NULL,
        null=True, blank=True,
        related_name='hour_logs',
        help_text='手工调整时留空')
    operator = models.CharField(
        verbose_name='操作人', max_length=32, blank=True, default='')
    remark = models.CharField(
        verbose_name='备注', max_length=64, blank=True, default='')
    create_time = models.DateTimeField(verbose_name='记录时间', auto_now_add=True)

    class Meta:
        verbose_name_plural = '课时流水'
        ordering = ['-create_time']
        indexes = [
            models.Index(fields=['enrollment', 'create_time']),
        ]

    def __str__(self):
        sign = '+' if self.delta > 0 else ''
        return f'{self.enrollment} {sign}{self.delta} ({self.get_reason_display()})'
