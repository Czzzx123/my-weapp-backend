from django.core.validators import RegexValidator
from django.db import models

# ============================================================
# 未蓝科技 · 智慧教育 —— 数据模型
# ============================================================

# 中国大陆手机号：1 开头，第二位 3-9，共 11 位
MOBILE_VALIDATOR = RegexValidator(
    regex=r'^1[3-9]\d{9}$',
    message='手机号格式不正确，应为 11 位数字且以 1 开头',
)


# ============================================================
# 基础内容（首页轮播 / 公告）
# ============================================================


class Banner(models.Model):
    """首页轮播图"""

    img = models.ImageField(
        upload_to='banner', verbose_name='图片',
        help_text='建议 750x360 以上，单张控制在 200KB 内')
    order = models.IntegerField(verbose_name='顺序')
    create_time = models.DateTimeField(auto_now=True, verbose_name='创建时间')
    is_delete = models.BooleanField(default=False, verbose_name='是否删除')

    class Meta:
        verbose_name_plural = '轮播图'
        ordering = ['order']

    def __str__(self):
        return str(self.img)


class Notice(models.Model):
    """公告 / 通知"""

    title = models.CharField(max_length=64, verbose_name='公告标题')
    content = models.TextField(verbose_name='内容')
    img = models.ImageField(
        upload_to='notice', verbose_name='公告图片',
        null=True, blank=True,
        help_text='可不传；不传时前端不显示配图')
    create_time = models.DateTimeField(auto_now=True, verbose_name='创建时间')

    class Meta:
        verbose_name_plural = '公告表'
        ordering = ['-create_time']

    def __str__(self):
        return self.title


class LearningPost(models.Model):
    """首页学习动态 —— 课程 / 活动的展示

    **这是公开内容，不是孩子的隐私照片。**

    用途是让家长和潜在咨询者直观看到「这儿上课是什么样的」，
    所以不需要登录、不需要按班级隔离，``media/`` 维持公开直出即可。

    与「电子相册」的区别（重要，别混）：
    - 本模型：课程活动宣传照，所有人可见
    - 电子相册（未开发）：学生 / 班级的照片，仅本人或本班可见，
      届时才需要做媒体鉴权和 ``Student ↔ CourseClass`` 的关联

    字段是照着前端 ``pages/index/index.js`` 里 ``daily_list`` 假数据的
    结构设计的，接口接上后页面版式不用改：

        { id, date, course, theme, title, content }
    """

    THEME_CHOICES = (
        ('blue', '蓝色'),
        ('mint', '薄荷绿'),
        ('purple', '紫色'),
    )

    title = models.CharField(
        max_length=64, verbose_name='标题',
        help_text='如「猜数字小游戏完成」')
    course_name = models.CharField(
        max_length=32, verbose_name='课程名',
        help_text='前端卡片上的课程标签，如「Python课堂」')
    content = models.TextField(
        verbose_name='正文', blank=True, default='')
    img = models.ImageField(
        upload_to='learning', verbose_name='配图',
        help_text='建议 750x420 以上；公开可访问')
    theme = models.CharField(
        max_length=16, verbose_name='卡片配色',
        choices=THEME_CHOICES, default='blue',
        help_text='对应前端 course-tag 的配色类名')
    is_delete = models.BooleanField(
        default=False, verbose_name='是否删除')
    create_time = models.DateTimeField(
        auto_now_add=True, verbose_name='发布时间')

    class Meta:
        verbose_name_plural = '学习动态'
        ordering = ['-create_time']
        indexes = [
            # 列表按时间倒序，且过滤 is_delete
            models.Index(fields=['is_delete', '-create_time']),
        ]

    def __str__(self):
        return f'{self.course_name} - {self.title}'


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
        null=True, blank=True, unique=True,
        help_text='wx.login 换来的唯一身份，微信登录入口的判断依据。'
                  '必须唯一：用户连点两次登录或网络重试时，'
                  '并发写入不能产生两条同 openid 的账号')
    unionid = models.CharField(
        verbose_name='微信 unionid', max_length=64,
        null=True, blank=True,
        help_text='小程序暂无，将来接开放平台时用')
    mobile = models.CharField(
        verbose_name='手机号', max_length=11,
        null=True, blank=True, db_index=True,
        help_text='孩子绑定的匹配依据，非唯一（二孩家庭同号）')
    nickname = models.CharField(
        verbose_name='昵称', max_length=32, blank=True, default='',
        help_text='用户主动填写的昵称。留空表示未设置，展示时按「孩子名+家长」兜底')
    avatar = models.ImageField(
        verbose_name='头像', upload_to='avatar/',
        null=True, blank=True,
        help_text=(
            '用户上传的自己服务器上的图片。'
            '注意：不能用微信返回的临时路径（http://tmp/... 会失效），'
            '也不能用 wx.getUserProfile 拿的灰头像 —— 该接口自基础库 2.27.1 起已失效'
        ))
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

    # ---- Django / DRF 认证体系要求的三个属性 ----
    # Parent 不是 auth.User 的子类，但 DRF 的 IsAuthenticated 权限类
    # 会检查 request.user.is_authenticated，缺这个属性会直接 AttributeError。

    @property
    def is_authenticated(self):
        """已通过 JWT 认证的 Parent 恒为 True（DRF 权限类依赖）。"""
        return True

    @property
    def is_anonymous(self):
        return False

    @property
    def is_active(self):
        """账号是否可用。与 Student.status 无关，这里只表达家长账号本身。"""
        return True

    @property
    def children(self):
        """该家长名下的在读学生（按手机号匹配的结果）。"""
        return Student.objects.filter(
            parent_students__parent=self, status='active'
        ).order_by('id')


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
        validators=[MOBILE_VALIDATOR],
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
    lesson_date = models.DateField(
        verbose_name='上课日期', null=True, blank=True,
        help_text='消课必填：这是哪一天的那节课。'
                  '充值/冲正/调整可留空')
    operator = models.CharField(
        verbose_name='操作人', max_length=32, blank=True, default='')
    remark = models.CharField(
        verbose_name='备注', max_length=64, blank=True, default='')
    is_revoked = models.BooleanField(
        verbose_name='已撤销', default=False,
        help_text='签错了撤销时打标。撤销不是删行 —— '
                  '账本必须留住「曾经扣过、又被冲回」这个事实')
    revoked_time = models.DateTimeField(
        verbose_name='撤销时间', null=True, blank=True)
    create_time = models.DateTimeField(verbose_name='记录时间', auto_now_add=True)

    class Meta:
        verbose_name_plural = '课时流水'
        ordering = ['-create_time']
        indexes = [
            models.Index(fields=['enrollment', 'create_time']),
            models.Index(fields=['lesson_date']),
        ]
        constraints = [
            # 同一节课不能重复扣课时。
            #
            # 为什么必须要有这条：老师和家长都可能手滑点两次「签到」，
            # 而 delta=-1 的流水记两条，从账面上完全看不出是重复提交
            # 还是孩子真的上了两节课 —— 只能靠人工比对时间戳去猜。
            #
            # 为什么加 condition=Q(reason='consumed')：
            # 冲正（refund）本来就要针对同一节课反向记一笔 +1，
            # 如果约束覆盖所有 reason，冲正会被自己挡住。
            #
            # 为什么还要加 is_revoked=False：
            # 签错了要能「撤销后重新签到」。撤销只是给原记录打标并补一笔
            # refund，原记录仍在表里；若约束不过滤已撤销的记录，
            # 重新签到会撞上那条历史记录，等于永远签不了。
            # 加上这个条件后，同一节课最多只有一条「有效消课」，
            # 撤销过的可以有任意多条 —— 正好符合「签错几次都留痕」。
            #
            # 为什么 session 为空的记录不会互相冲突：
            # SQL 的唯一约束里 NULL 不等于 NULL，所以手工调整
            # （session 留空）可以记任意多条，不会误拦。
            models.UniqueConstraint(
                fields=['enrollment', 'session', 'lesson_date'],
                condition=models.Q(reason='consumed', is_revoked=False),
                name='uniq_consumed_per_lesson',
            ),
        ]

    def __str__(self):
        sign = '+' if self.delta > 0 else ''
        when = f' {self.lesson_date}' if self.lesson_date else ''
        revoked = ' [已撤销]' if self.is_revoked else ''
        return f'{self.enrollment} {sign}{self.delta}{when} ({self.get_reason_display()}){revoked}'


class NotifyLog(models.Model):
    """通知发送记录：防重复 + 留痕

    为什么必须有这张表
    ------------------
    开课前提醒是**定时任务**发的，每 5 分钟跑一次。同一个班有 20 个孩子，
    如果不记「这节课的这个孩子已经提醒过了」，一早上家长会收到几十条
    一样的短信/通知 —— 会被当成骚扰直接屏蔽，比不发还糟。

    唯一约束 ``parent + session + lesson_date + notify_type`` 就是这道闸门。
    任务每轮都插一条，插得进去才发；插不进去说明发过了，跳过。
    这比「查一下有没有发过再发」可靠：并发跑两个任务实例时，
    check-then-act 会漏，唯一约束不会。

    发送失败也要留记录吗
    --------------------
    要，但要能在后台重发。``status`` 区分三种：
      sent   已发出
      failed 发了但微信/短信服务商报错 —— 可能是临时故障，值得重试
      skipped 根本不该发（没 openid、没授权、已签到、课时不足…）

    ``skipped`` 与 ``failed`` 必须分开：压在一起的话，
    「家长没授权」这种正常状态会混进错误日志里，把真正的故障淹掉。
    """

    TYPE_CHOICES = (
        ('before_class', '上课前提醒'),
        ('checkin', '签到成功通知'),
    )

    STATUS_CHOICES = (
        ('sent', '已发送'),
        ('failed', '发送失败'),
        ('skipped', '未发送'),
    )

    parent = models.ForeignKey(
        Parent, verbose_name='家长', on_delete=models.CASCADE,
        related_name='notify_logs')
    student = models.ForeignKey(
        Student, verbose_name='学生', on_delete=models.CASCADE,
        related_name='notify_logs',
        help_text='一个家长两个孩子时，要能分清是哪個孩子的课')
    session = models.ForeignKey(
        ClassSession, verbose_name='上课时段', on_delete=models.CASCADE,
        related_name='notify_logs')
    lesson_date = models.DateField(verbose_name='上课日期')
    notify_type = models.CharField(
        verbose_name='通知类型', max_length=16, choices=TYPE_CHOICES)
    status = models.CharField(
        verbose_name='结果', max_length=16, choices=STATUS_CHOICES,
        default='skipped')
    channel = models.CharField(
        verbose_name='渠道', max_length=16, blank=True, default='',
        help_text='subscribe / sms；留空表示没走到发送这一步')
    errcode = models.IntegerField(
        verbose_name='错误码', null=True, blank=True,
        help_text='43101 = 家长未订阅或额度用完，属正常状态')
    remark = models.CharField(
        verbose_name='备注', max_length=128, blank=True, default='')
    create_time = models.DateTimeField(verbose_name='记录时间', auto_now_add=True)

    class Meta:
        verbose_name_plural = '通知记录'
        ordering = ['-create_time']
        indexes = [
            models.Index(fields=['lesson_date', 'notify_type']),
            models.Index(fields=['parent', 'create_time']),
        ]
        constraints = [
            # 同一个家长、同一节课、同一种通知，只发一次。
            #
            # 定时任务每 5 分钟扫一次，同一个时段的课会被扫到几十遍；
            # 去掉这条约束，家长一早上会收到几十条重复提醒。
            #
            # 为什么把 student 放进约束里：
            # 一个家长有两个孩子上不同的班，同一时段巧合撞课时，
            # 两条提醒都该发（「小明和小红的课都在 16:00」和
            # 只提小明，信息量完全不同）。
            models.UniqueConstraint(
                fields=['parent', 'student', 'session', 'lesson_date', 'notify_type'],
                name='uniq_notify_per_lesson',
            ),
        ]

    def __str__(self):
        return (f'{self.parent} {self.lesson_date} '
                f'{self.get_notify_type_display()}({self.get_status_display()})')


class LeaveRequest(models.Model):
    """请假：某一天的那一节固定课，这个孩子不来了。

    为什么需要 ``lesson_date``
    --------------------------
    课表只存「周三 16:00-18:00」，不存具体日期（排课每周重复，落成日期
    就要每周批量生成一批记录）。但「请假」这个动作必然指向**某一天**：
    这周三不来，不代表下周三不来。

    所以请假记录必须自带日期。``(student, session, lesson_date)``
    三者才唯一确定「哪一节具体的课」。

    为什么不扣课时
    --------------
    本项目口径是「签到才消课」——`HourLog` 只在 ``checkin()`` 里写。
    家长请假 → 老师不签到 → 一课时都不扣。这是自然的，不需要额外逻辑。

    早期文档写过「请假是否退课时 `[K6]`，建议提前 24h 退」，
    那个前提是错的：根本不存在「已经扣了要退」的环节。
    2026-11-26 用户确认口径：**请假、旷课都不扣课时**。

    为什么不用审批
    --------------
    用户 2026-11-26 决定：**提交即生效**。理由是没有账务风险
    （不签到就不扣课时），而「家长请了假、老师没审、最后算旷课」
    才是真正会引起纠纷的情况。``status`` 留了 ``rejected`` 这个值，
    但 MVP 不产生它 —— 将来若要加审批不必改表结构。

    为什么撤销是改状态而不是删行
    ----------------------------
    删行会让「这周三请过假」这件事彻底消失。家长撤了假又后悔、
    或者老师发现名单对不上时，没有记录就无从解释。
    唯一约束带 ``status='active'`` 条件，撤销后就放行重新提交。
    """

    STATUS_CHOICES = (
        ('active', '已请假'),
        ('cancelled', '已撤销'),
        ('rejected', '已驳回'),
    )

    student = models.ForeignKey(
        Student, verbose_name='学生', on_delete=models.CASCADE,
        related_name='leave_requests')
    session = models.ForeignKey(
        ClassSession, verbose_name='上课时段', on_delete=models.CASCADE,
        related_name='leave_requests')
    lesson_date = models.DateField(
        verbose_name='请假日期',
        help_text='哪一天的那节课。课表只存周几，请假必须落到具体日期')
    reason = models.CharField(
        verbose_name='请假原因', max_length=128, blank=True, default='',
        help_text='家长填写，可不填')
    status = models.CharField(
        verbose_name='状态', max_length=16,
        choices=STATUS_CHOICES, default='active')
    operator = models.CharField(
        verbose_name='操作人', max_length=32, blank=True, default='',
        help_text='家长线上提交时留空；老师后台代填时写老师名字')
    cancel_time = models.DateTimeField(
        verbose_name='撤销时间', null=True, blank=True)
    create_time = models.DateTimeField(verbose_name='提交时间', auto_now_add=True)

    class Meta:
        verbose_name_plural = '请假记录'
        ordering = ['-lesson_date', '-create_time']
        indexes = [
            models.Index(fields=['lesson_date', 'status']),
            models.Index(fields=['student', 'lesson_date']),
        ]
        constraints = [
            # 同一节课不能请两次假。
            #
            # 为什么带 condition=Q(status='active')：
            # 撤销（cancelled）之后家长应该能重新提交。若约束覆盖所有状态，
            # 撤销记录会一直挡着，等于「撤销一次就永远请不了这节课」。
            #
            # 为什么 rejected 也要放行：
            # 被驳回后家长改个理由再请是正常的，驳回记录不该成为永久阻塞。
            models.UniqueConstraint(
                fields=['student', 'session', 'lesson_date'],
                condition=models.Q(status='active'),
                name='uniq_active_leave_per_lesson',
            ),
        ]

    def __str__(self):
        return (f'{self.student.name} {self.lesson_date} '
                f'{self.session.course_class.name}'
                f'({self.get_status_display()})')

    @property
    def is_active(self) -> bool:
        return self.status == 'active'
