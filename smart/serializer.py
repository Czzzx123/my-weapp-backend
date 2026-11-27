import re

from django.db.models import Sum
from django.utils import timezone
from rest_framework import serializers

from .models import Banner, LearningPost, Notice

# 中国大陆手机号：1 开头，第二位 3-9，共 11 位
MOBILE_REGEX = r'^1[3-9]\d{9}$'


class BannerSerializer(serializers.ModelSerializer):
    """轮播图表序列化类

    字段显式列出而不是 `fields = '__all__'`：
    一来 `is_delete` 这类内部字段不该顺带暴露给前端，
    二来将来给模型加字段时不会「自动泄漏」到接口上。

    `create_time` 显式指定 format：`USE_TZ=True` 之后默认会输出
    `2026-04-10T14:32:49.786000+08:00` 这种带时区的 ISO 串，
    前端如果要自己解析会很别扭。统一成 `YYYY-MM-DD`，
    与 NoticeSerializer 保持一致。
    """

    class Meta:
        model = Banner
        fields = ['id', 'img', 'order', 'create_time']
        extra_kwargs = {
            'create_time': {'format': '%Y-%m-%d'},
        }


class NoticeSerializer(serializers.ModelSerializer):
    """公告序列化类"""

    class Meta:
        model = Notice
        fields = ['id', 'title', 'img', 'create_time', 'content']
        # create_time 只想要年月日，不要时分秒
        extra_kwargs = {
            'create_time': {'format': '%Y-%m-%d'}
        }


class LearningPostSerializer(serializers.ModelSerializer):
    """首页学习动态序列化类

    **字段名故意和模型不一样，不要"修正"成 course_name / create_time。**

    前端 ``pages/index/index.js`` 里 ``daily_list`` 的假数据结构是：

        { id, date, course, theme, title, content }

    直接沿用这套键名，前端接接口时只改「数据从哪来」，卡片版式一行都不用动。
    如果这里输出 ``course_name``，前端每张卡片都要写 ``item.course_name``，
    而收藏/分享/埋点那几处又用的是 ``item.course``，改漏一处就是空白。

    ``date`` 输出 ``10月18日`` 这种中文短格式，而不是 ``2026-10-18``。
    理由同上：这是首页卡片上直接显示给人看的文案，格式对齐就不必在前端
    再写一层 formatDate。

    **跨年补年份（2026-11-27 改）**：原来注释写的是「跨年会丢年份，
    但上课日常只看近期内容」，这句在「只看 3 条」的时代成立 ——
    3 条卡最多回溯一两周，永远碰不到去年。改成无限滚动之后不成立了：
    家长一直往下滑就能翻到去年，「10月18日」到底是今年还是去年的，
    卡片上根本看不出来。
    现在按「今年省略年份、往年补全年份」输出，近期版面一个字没变，
    翻到历史内容时自动变成 ``2025年10月18日``。
    """

    course = serializers.CharField(source='course_name', read_only=True)
    date = serializers.SerializerMethodField()

    class Meta:
        model = LearningPost
        fields = ['id', 'title', 'course', 'content', 'img', 'theme', 'date']
        read_only_fields = fields

    def get_date(self, obj) -> str:
        # USE_TZ=True 时 create_time 是 UTC 的 aware datetime，
        # 直接用它的 month/day 会在凌晨 0~8 点算成前一天。
        # 必须 localtime 到 Asia/Shanghai 再取日期。
        local = timezone.localtime(obj.create_time)
        if local.year == timezone.localtime().year:
            return f'{local.month}月{local.day}日'
        return f'{local.year}年{local.month}月{local.day}日'


class SendSmsSerializer(serializers.Serializer):
    """发送验证码的入参。"""

    mobile = serializers.RegexField(
        MOBILE_REGEX,
        error_messages={'invalid': '手机号格式不正确'},
    )


class LoginSerializer(serializers.Serializer):
    """双入口登录的入参。

    两种形态共用一个接口：
    - 手机号登录：``{mobile, code}``
    - 微信静默登录：``{wx_code}``

    用 ``wx_code`` 而不是 ``code`` 命名微信凭证，是为了避免和验证码混在一起 ——
    两者都叫 code 的话，请求体里出现一个 code 根本分不清是哪种登录。
    """

    mobile = serializers.CharField(required=False, allow_blank=True, default='')
    code = serializers.CharField(required=False, allow_blank=True, default='')
    wx_code = serializers.CharField(required=False, allow_blank=True, default='')

    def validate(self, attrs):
        mobile = attrs.get('mobile', '').strip()
        code = attrs.get('code', '').strip()
        wx_code = attrs.get('wx_code', '').strip()

        if wx_code:
            # 微信登录：不需要手机号与验证码
            return {'mode': 'wechat', 'wx_code': wx_code}

        if not mobile:
            raise serializers.ValidationError({'mobile': '请输入手机号'})

        if not re.match(MOBILE_REGEX, mobile):
            raise serializers.ValidationError({'mobile': '手机号格式不正确'})

        if not code:
            raise serializers.ValidationError({'code': '请输入验证码'})

        return {'mode': 'mobile', 'mobile': mobile, 'code': code}


class BindMobileSerializer(serializers.Serializer):
    """微信登录后补手机号。"""

    mobile = serializers.RegexField(
        MOBILE_REGEX,
        error_messages={'invalid': '手机号格式不正确'},
    )


class UpdateProfileSerializer(serializers.Serializer):
    """修改昵称 / 头像。两个字段都可选，只传一个就只改一个。

    为什么不用 ModelSerializer：
    这里只允许改 nickname 和 avatar 两个字段，openid / mobile / role
    绝不能让客户端碰。Serializer 白名单比 ModelSerializer 的 exclude 更安全。

    avatar 用 ImageField 而不是 FileField —— DRF 会在这里做一次
    「是不是图片」的校验。自定义的 avatar_service 还会再验一次并压缩，
    两层都留着：DRF 这层先挡掉明显的非图片，减少 Pillow 被无效数据喂的概率。
    """

    nickname = serializers.CharField(
        required=False,
        allow_blank=True,
        max_length=32,
        error_messages={
            'max_length': '昵称最多 32 个字',
        },
    )
    avatar = serializers.ImageField(
        required=False,
        # DRF 默认报「请上传有效图片。您上传的该文件不是图片或者图片已经损坏。」
        # 这句是英文机翻，语病明显。换成正常中文。
        error_messages={
            'invalid': '这个文件不是有效的图片，请换一张',
            'no_name': '图片文件名缺失，请重新选择',
        },
    )

    def validate_nickname(self, value: str) -> str:
        value = value.strip()
        if not value:
            raise serializers.ValidationError('昵称不能为空')
        return value

    def validate(self, attrs):
        if not attrs:
            raise serializers.ValidationError('请提供要修改的昵称或头像')
        return attrs


# ============================================================
# 教务：课表 / 课时
# ============================================================


class ScheduleItemSerializer(serializers.Serializer):
    """课表的一条：某个孩子的某个班，在周几、几点上课。

    **这不是 ModelSerializer，是手写的。**
    因为一条课表要跨 ``ClassSession → CourseClass → Course`` 三张表，
    而 ``ClassSession`` 是主体，直接用它会输出一堆内部 id，
    前端还得自己再去拼课程名。手写反而更短更直白。

    一个 ``Enrollment``（报名）对应一个班，一个班可有 N 个时段，
    所以同一个孩子会按「时段数」展开成多条，``student_id`` 会重复。
    前端按 ``weekday`` 分组即可，不必自己去重。
    """

    student_id = serializers.IntegerField(source='student.id')
    student_name = serializers.CharField(source='student.name')
    class_id = serializers.IntegerField(source='course_class.id')
    class_name = serializers.CharField(source='course_class.name')
    course_name = serializers.CharField(source='course_class.course.name')
    teacher = serializers.CharField(source='course_class.teacher')
    room = serializers.CharField(source='course_class.room')

    # 时段本身的信息。session_id 前端用不到，但调试时能对上后台，
    # 且将来做「临时调课」需要它定位具体时段，先留着。
    session_id = serializers.IntegerField(source='id')
    weekday = serializers.IntegerField()
    weekday_label = serializers.CharField(source='get_weekday_display')
    start_time = serializers.TimeField(format='%H:%M')
    end_time = serializers.TimeField(format='%H:%M')
    status = serializers.CharField()
    status_label = serializers.CharField(source='get_status_display')

    # 代课教师：为空表示按正常安排上课，非空时前端要把主教师划掉显示代课人
    substitute_teacher = serializers.CharField()


class HourItemSerializer(serializers.Serializer):
    """课时的一条：某个孩子某个班的额度与消耗。

    **剩余课时不读数据库列，实时用流水汇总算。**
    ``remain`` 由 view 层算好塞进来 —— 这里用 ``SerializerMethodField``
    也行，但那样 N 个报名要发 N 次聚合查询；view 里一次 ``annotate``
    就够，这里只负责字段整形。

    ``remain_percent`` 是给前端进度条用的百分比（0~100 的整数）。
    放在后端算的理由：前端拿到 ``remain`` / ``total`` 之后还要处理
    ``total=0`` 的除零，而这是必然会出现的（报名时忘填额度）。
    """

    enrollment_id = serializers.IntegerField(source='id')
    student_id = serializers.IntegerField(source='student.id')
    student_name = serializers.CharField(source='student.name')
    class_id = serializers.IntegerField(source='course_class.id')
    class_name = serializers.CharField(source='course_class.name')
    course_name = serializers.CharField(source='course_class.course.name')

    total = serializers.IntegerField(source='total_hours')
    used = serializers.SerializerMethodField()
    remain = serializers.SerializerMethodField()
    remain_percent = serializers.SerializerMethodField()
    unit_label = serializers.CharField()
    status = serializers.CharField()
    status_label = serializers.CharField(source='get_status_display')

    def _sum(self, obj):
        # view 层已经 annotate 过 _sum_delta，避免 N+1 查询。
        # 取不到时（比如单独调用这个 serializer）退回实时聚合。
        total = getattr(obj, '_sum_delta', None)
        if total is None:
            total = obj.hour_logs.aggregate(s=Sum('delta'))['s'] or 0
        return total

    def get_used(self, obj):
        # 流水里消课是负数，展示时要翻成正数「已用 4 课时」
        return -self._sum(obj)

    def get_remain(self, obj):
        return obj.total_hours + self._sum(obj)

    def get_remain_percent(self, obj):
        remain = obj.total_hours + self._sum(obj)
        if not obj.total_hours:
            # 额度为 0 时没有「进度」可言，给 0 而不是除零崩掉
            return 0
        pct = round(remain / obj.total_hours * 100)
        # 超额消课（remain 为负）时进度条要贴底，不能出现负宽度
        return max(0, min(100, pct))


class LessonRecordSerializer(serializers.Serializer):
    """一条消课记录：什么时候、上了哪节课、扣了多少。

    **这是给家长看「签到记录」用的，不是账本全貌。**
    只暴露 ``reason='consumed'`` 的流水，充值/冲正/调整不出现在这里 ——
    家长关心「孩子上了哪些课」，不是「账怎么平的」。
    冲正造成的课时变动仍会体现，因为消课记录总额变了。

    撤销过的记录照样返回，带 ``is_revoked`` 标记。理由同课时页：
    家长如果看到「11月25日 扣了 1 课时」后来消失了，会以为系统在偷偷改账；
    保留下来并标「已撤销」，他能自己对上。
    """

    id = serializers.IntegerField()
    student_id = serializers.IntegerField(source='enrollment.student.id')
    student_name = serializers.CharField(source='enrollment.student.name')
    class_name = serializers.CharField(source='enrollment.course_class.name')
    course_name = serializers.CharField(source='enrollment.course_class.course.name')

    delta = serializers.IntegerField()
    lesson_date = serializers.DateField(format='%Y-%m-%d')
    # 上课时间只取开始时刻：家长要的是「几点上的课」，
    # 结束时间在这一页没信息量，反而让每行更长
    start_time = serializers.SerializerMethodField()
    weekday_label = serializers.SerializerMethodField()

    is_revoked = serializers.BooleanField()
    operator = serializers.CharField()
    # 记录时间 = 老师点签到的时刻，与 lesson_date（上课那天）可能不同：
    # 补签现象很常见，两个时间都给出，家长才看得出「今天补了上周的课」
    create_time = serializers.DateTimeField(format='%Y-%m-%d %H:%M')

    def get_start_time(self, obj):
        if not obj.session:
            # 手工调整的消课可能没挂时段
            return ''
        return obj.session.start_time.strftime('%H:%M')

    def get_weekday_label(self, obj):
        if not obj.session:
            return ''
        return obj.session.get_weekday_display()
