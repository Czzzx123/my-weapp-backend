from django.contrib import admin, messages
from django.db.models import Count, Sum
from django.forms.models import BaseInlineFormSet
from django.shortcuts import redirect, render
from django.urls import path
from django.utils import timezone
from django.utils.html import format_html

from . import checkin as checkin_service
from .models import (
    Banner, LearningPost, Notice,
    Parent, Student, ParentStudent,
    Course, CourseClass, ClassSession,
    Enrollment, HourLog, NotifyLog,
)


# ============================================================
# 基础内容
# ============================================================

@admin.register(Banner)
class BannerAdmin(admin.ModelAdmin):
    list_display = ('id', 'img', 'order', 'create_time', 'is_delete')
    list_editable = ('order', 'is_delete')
    list_filter = ('is_delete',)


@admin.register(Notice)
class NoticeAdmin(admin.ModelAdmin):
    list_display = ('id', 'title', 'create_time')
    search_fields = ('title', 'content')


@admin.register(LearningPost)
class LearningPostAdmin(admin.ModelAdmin):
    list_display = ('id', 'thumb', 'title', 'course_name', 'theme',
                    'create_time', 'is_delete')
    list_filter = ('is_delete', 'theme', 'course_name')
    search_fields = ('title', 'course_name', 'content')
    list_editable = ('theme', 'is_delete')
    readonly_fields = ('create_time', 'preview')
    date_hierarchy = 'create_time'
    fieldsets = (
        ('卡片内容', {
            'fields': ('title', 'course_name', 'content'),
            'description': '标题显示在卡片顶部，课程名是卡片上的彩色标签，'
                           '正文在首页截断为 2 行，写长一点没关系。'
        }),
        ('图片', {
            'fields': ('img', 'preview'),
            'description': '这是课程 / 活动的展示照，所有访客都能看到，'
                           '不要放孩子的单人照片或含姓名、联系方式的内容。'
                           '建议 16:9（如 750x420），卡片上会裁成通栏。'
        }),
        ('展示', {
            'fields': ('theme', 'is_delete', 'create_time'),
            'description': '配色对应前端标签颜色：蓝色=编程类，薄荷绿=创意类，紫色=硬件类。'
        }),
    )

    def get_queryset(self, request):
        # 默认隐藏软删除的记录：后台列表不该被历史数据淹没。
        # 需要找回时用列表页右侧的筛选器切到「全部」。
        qs = super().get_queryset(request)
        if request.GET.get('is_delete__exact') in ('0', '1'):
            return qs
        return qs.filter(is_delete=False)

    @admin.display(description='配图')
    def thumb(self, obj):
        if not obj.img:
            return '（无图）'
        # mark_safe 是必要的：format_html 不能用在 list_display 的返回里拼多段，
        # 这里只是一段固定结构的 img 标签，src 已由 Django 做了转义。
        return format_html(
            '<img src="{}" style="height:44px;width:78px;object-fit:cover;'
            'border-radius:4px;" />', obj.img.url)

    @admin.display(description='效果预览')
    def preview(self, obj):
        if not obj or not obj.img:
            return '保存后这里会显示图片预览'
        return format_html(
            '<img src="{}" style="max-width:420px;width:100%;border-radius:8px;" />',
            obj.img.url)


# ============================================================
# A 组 · 家长体系
# ============================================================

class ParentStudentInline(admin.TabularInline):
    model = ParentStudent
    fk_name = 'parent'
    extra = 0
    autocomplete_fields = ('student',)
    verbose_name_plural = '已绑定的孩子'


@admin.register(Parent)
class ParentAdmin(admin.ModelAdmin):
    list_display = ('id', 'nickname', 'mobile', 'role', 'child_count',
                    'create_time', 'last_login')
    list_filter = ('role', 'create_time')
    search_fields = ('nickname', 'mobile', 'openid')
    readonly_fields = ('create_time', 'last_login')
    inlines = [ParentStudentInline]
    fieldsets = (
        ('身份', {'fields': ('openid', 'unionid', 'role')}),
        ('资料', {'fields': ('nickname', 'avatar', 'mobile')}),
        ('时间', {'fields': ('create_time', 'last_login')}),
    )

    def get_queryset(self, request):
        qs = super().get_queryset(request)
        return qs.annotate(_child_count=Count('parent_students'))

    @admin.display(description='孩子数', ordering='_child_count')
    def child_count(self, obj):
        return obj._child_count


@admin.register(Student)
class StudentAdmin(admin.ModelAdmin):
    list_display = ('id', 'name', 'mobile', 'grade', 'status',
                    'class_count', 'create_time')
    list_filter = ('status', 'grade')
    search_fields = ('name', 'mobile')
    list_editable = ('status',)
    fieldsets = (
        ('学生', {'fields': ('name', 'grade', 'status')}),
        ('家长手机号（登录匹配依据）', {
            'fields': ('mobile',),
            'description': '家长报名时提供的手机号。家长登录后系统按此号码自动绑定，'
                           '不唯一（二孩家庭同号）。没填也不影响建档，后续补上即可。'
        }),
        ('其他', {'fields': ('remark',)}),
    )

    def get_queryset(self, request):
        qs = super().get_queryset(request)
        return qs.annotate(_class_count=Count('enrollments'))

    @admin.display(description='已报班数', ordering='_class_count')
    def class_count(self, obj):
        return obj._class_count


@admin.register(ParentStudent)
class ParentStudentAdmin(admin.ModelAdmin):
    list_display = ('id', 'parent', 'student', 'relation', 'bind_time')
    list_filter = ('relation', 'bind_time')
    search_fields = ('parent__nickname', 'parent__mobile', 'student__name')
    autocomplete_fields = ('parent', 'student')
    readonly_fields = ('bind_time',)


# ============================================================
# B 组 · 教学体系
# ============================================================

@admin.register(Course)
class CourseAdmin(admin.ModelAdmin):
    list_display = ('id', 'name', 'category', 'class_count', 'create_time')
    list_filter = ('category',)
    search_fields = ('name',)

    def get_queryset(self, request):
        qs = super().get_queryset(request)
        return qs.annotate(_class_count=Count('classes'))

    @admin.display(description='班级数', ordering='_class_count')
    def class_count(self, obj):
        return obj._class_count


class ClassSessionInline(admin.TabularInline):
    """一个班一周可多次，必须在班级详情页直接增删"""

    model = ClassSession
    extra = 1
    fields = ('weekday', 'start_time', 'end_time',
              'substitute_teacher', 'status', 'remark')
    ordering = ('weekday', 'start_time')
    verbose_name_plural = '上课时段（一周可多条）'


class EnrollmentInlineForClass(admin.TabularInline):
    model = Enrollment
    fk_name = 'course_class'
    extra = 0
    autocomplete_fields = ('student',)
    fields = ('student', 'total_hours', 'unit_label', 'amount', 'status')
    verbose_name_plural = '报名学员'


@admin.register(CourseClass)
class CourseClassAdmin(admin.ModelAdmin):
    list_display = ('id', 'name', 'course', 'level', 'teacher',
                    'session_count', 'student_count', 'status')
    list_filter = ('status', 'course', 'level')
    search_fields = ('name', 'teacher')
    autocomplete_fields = ('course',)
    list_select_related = ('course',)
    inlines = [ClassSessionInline, EnrollmentInlineForClass]
    fieldsets = (
        ('班级', {'fields': ('course', 'name', 'level', 'status')}),
        ('师资与场地', {'fields': ('teacher', 'room')}),
        ('其他', {'fields': ('remark',)}),
    )

    def get_queryset(self, request):
        qs = super().get_queryset(request)
        return qs.annotate(
            _session_count=Count('sessions', distinct=True),
            _student_count=Count('enrollments', distinct=True),
        )

    @admin.display(description='周课时', ordering='_session_count')
    def session_count(self, obj):
        return obj._session_count

    @admin.display(description='学员数', ordering='_student_count')
    def student_count(self, obj):
        return obj._student_count


@admin.register(ClassSession)
class ClassSessionAdmin(admin.ModelAdmin):
    list_display = ('id', 'course_class', 'weekday', 'start_time', 'end_time',
                    'substitute_teacher', 'status')
    list_filter = ('weekday', 'status')
    search_fields = ('course_class__name', 'substitute_teacher')
    autocomplete_fields = ('course_class',)
    list_select_related = ('course_class',)
    ordering = ('weekday', 'start_time')


class HourLogFormSet(BaseInlineFormSet):
    """课时流水的账本保护

    规则：**旧账只能看，不能改；新账只能追加。**

    为什么不用 `readonly_fields`：内联的 `get_readonly_fields(request, obj)` 里
    `obj` 是**父对象**（报名记录），它在编辑页永远不为 None，
    于是连新增的空行也会被一起锁死 —— 结果是全系统没有任何入口能写流水。
    正确做法是按**每条已存在的记录**（`form.instance.pk`）逐条禁用字段：
    已保存的流水字段 disabled（浏览器不提交 + 服务端忽略，保留原值），
    新增空行仍可编辑。
    """

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        for form in self.forms:
            if form.instance.pk:
                for name in ('delta', 'reason', 'session', 'operator', 'remark'):
                    if name in form.fields:
                        form.fields[name].disabled = True


class HourLogInline(admin.TabularInline):
    model = HourLog
    formset = HourLogFormSet
    extra = 1
    can_delete = False          # 账本不允许删行，冲正请追加一条
    fields = ('delta', 'reason', 'session', 'operator', 'remark')
    verbose_name_plural = '课时流水（旧账只读，只能追加新记录）'


@admin.register(Enrollment)
class EnrollmentAdmin(admin.ModelAdmin):
    list_display = ('id', 'student', 'course_class', 'total_hours',
                    'used_hours_display', 'remain_hours_display',
                    'unit_label', 'amount', 'status', 'enroll_time')
    list_filter = ('status', 'course_class')
    search_fields = ('student__name', 'student__mobile', 'course_class__name')
    autocomplete_fields = ('student', 'course_class')
    list_select_related = ('student', 'course_class')
    readonly_fields = ('used_hours_display', 'remain_hours_display', 'enroll_time')
    inlines = [HourLogInline]
    fieldsets = (
        ('报名', {'fields': ('student', 'course_class', 'status')}),
        ('课时额度（报名时约定）', {
            'fields': ('total_hours', 'unit_label',
                       'used_hours_display', 'remain_hours_display'),
            'description': '剩余课时 = 购买课时 + 所有流水变动。'
                           '余额由流水汇总得出，不单独存储，避免算错。'
        }),
        ('费用', {'fields': ('amount', 'enroll_time', 'remark')}),
    )

    def get_queryset(self, request):
        qs = super().get_queryset(request)
        return qs.annotate(_sum_delta=Sum('hour_logs__delta'))

    @admin.display(description='已用')
    def used_hours_display(self, obj):
        total = getattr(obj, '_sum_delta', None)
        if total is None:
            total = obj.hour_logs.aggregate(s=Sum('delta'))['s'] or 0
        return -total

    @admin.display(description='剩余')
    def remain_hours_display(self, obj):
        total = getattr(obj, '_sum_delta', None)
        if total is None:
            total = obj.hour_logs.aggregate(s=Sum('delta'))['s'] or 0
        return obj.total_hours + total


@admin.register(HourLog)
class HourLogAdmin(admin.ModelAdmin):
    list_display = ('id', 'enrollment', 'delta', 'reason',
                    'session', 'operator', 'create_time')
    list_filter = ('reason', 'create_time')
    search_fields = ('enrollment__student__name', 'enrollment__course_class__name')
    autocomplete_fields = ('enrollment', 'session')
    list_select_related = ('enrollment__student', 'enrollment__course_class', 'session')
    readonly_fields = ('enrollment', 'delta', 'reason', 'session', 'operator')
    date_hierarchy = 'create_time'

    def has_change_permission(self, request, obj=None):
        # 流水只允许新增（冲正），不允许事后改账
        return False

    def has_add_permission(self, request):
        # 新增统一从「报名记录」页的内联进来，避免选错报名
        return False


# ============================================================
# 签到消课（独立页面，不是模型 Admin）
# ============================================================
#
# 为什么不做成 HourLog 的「批量新增」
# ----------------------------------
# 老师的心智是「今天这个班谁来了」，不是「往流水表里插几条记录」。
# 从流水表出发，他得自己算时间、自己找报名、自己填 -1，
# 每一步都能填错，而且看不出「这个人这节课已经签过了」。
#
# 所以单独做一个页面：选班 → 选时段 → 选日期 → 出现全员名单 →
# 勾选到场的人。业务规则全在 smart/checkin.py，这里只管界面与消息。


def checkin_view(request):
    """签到消课页。"""
    classes = CourseClass.objects.filter(status='active').order_by('name')

    raw_class = request.GET.get('class_id') or request.POST.get('class_id') or ''
    raw_session = request.GET.get('session_id') or request.POST.get('session_id') or ''
    raw_date = request.GET.get('lesson_date') or request.POST.get('lesson_date') or ''

    selected_class_id = int(raw_class) if raw_class.isdigit() else None
    selected_session_id = int(raw_session) if raw_session.isdigit() else None

    lesson_date = timezone.localdate()
    if raw_date:
        try:
            lesson_date = timezone.datetime.strptime(raw_date, '%Y-%m-%d').date()
        except ValueError:
            messages.error(request, '日期格式不正确，已重置为今天')
            lesson_date = timezone.localdate()

    sessions = []
    if selected_class_id:
        sessions = ClassSession.objects.filter(
            course_class_id=selected_class_id,
        ).order_by('weekday', 'start_time')

    # 没选全三样东西时 roster 为 None → 模板不渲染名单区，
    # 而不是渲染一个空列表让老师以为「这个班没人」。
    roster = None
    if selected_class_id and selected_session_id:
        roster = checkin_service.lesson_roster(
            class_id=selected_class_id,
            lesson_date=lesson_date,
            session_id=selected_session_id,
        )

    if request.method == 'POST':
        action = request.POST.get('action', 'checkin')

        if action == 'checkin':
            enrollment_ids = [
                int(x) for x in request.POST.getlist('enrollment_ids') if x.isdigit()
            ]
            try:
                result = checkin_service.checkin(
                    class_id=selected_class_id,
                    lesson_date=lesson_date,
                    session_id=selected_session_id,
                    enrollment_ids=enrollment_ids,
                    operator=request.user.get_username(),
                )
            except checkin_service.CheckinError as exc:
                messages.error(request, exc.message)
            else:
                created = result['created']
                skipped = result['skipped']
                if created:
                    names = '、'.join(
                        log.enrollment.student.name for log in created
                    )
                    messages.success(
                        request,
                        f'签到成功：{names}，共 {len(created)} 人，每人扣 1 课时。'
                    )
                    _notify_parents(created)
                if skipped:
                    names = '、'.join(s['student'] for s in skipped)
                    messages.warning(
                        request,
                        f'{names} 这节课已经签过，已自动跳过（不会重复扣课时）。'
                    )
                if not created and not skipped:
                    messages.warning(request, '没有可签到的学生')

        elif action == 'revoke':
            log_id = request.POST.get('log_id', '')
            if log_id.isdigit():
                try:
                    log = checkin_service.revoke(
                        int(log_id), operator=request.user.get_username(),
                    )
                except checkin_service.CheckinError as exc:
                    messages.error(request, exc.message)
                else:
                    messages.success(
                        request,
                        f'已撤销 {log.enrollment.student.name} 的签到，1 课时已退回。'
                        '现在可以重新签到。'
                    )

        # 提交后重定向，避免刷新页面重复提交
        query = (
            f'?class_id={selected_class_id or ""}'
            f'&session_id={selected_session_id or ""}'
            f'&lesson_date={lesson_date:%Y-%m-%d}'
        )
        return redirect(f'/wl-manage/checkin/{query}')

    signed_logs = []
    if selected_class_id and selected_session_id:
        signed_logs = list(
            HourLog.objects.filter(
                session_id=selected_session_id,
                lesson_date=lesson_date,
                reason='consumed',
            )
            .select_related('enrollment__student')
            .order_by('-create_time')
        )

    context = {
        **admin.site.each_context(request),
        'title': '签到消课',
        'classes': classes,
        'sessions': sessions,
        'selected_class_id': selected_class_id,
        'selected_session_id': selected_session_id,
        'lesson_date': lesson_date,
        'roster': roster,
        'signed_logs': signed_logs,
    }
    return render(request, 'admin/smart/checkin.html', context)


def _notify_parents(created_logs):
    """签到成功后通知家长。**任何异常都不能影响签到结果。**

    签到已经写进数据库了，通知只是附加动作。家长没订阅、token 过期、
    模板没配，老师都不该看到「签到失败」。
    """
    from libs.wechat_notify import notify_checkin

    for log in created_logs:
        try:
            enrollment = log.enrollment
            remain = enrollment.remain_hours
            notify_checkin(
                enrollment=enrollment,
                lesson_date=log.lesson_date,
                session=log.session,
                remain=remain,
            )
        except Exception:  # noqa: BLE001 —— 通知失败绝不能冒泡
            import logging
            logging.getLogger(__name__).exception(
                '签到通知异常 log_id=%s', log.id
            )


# 把签到页挂到 admin 的 URL 上，路径为 /wl-manage/checkin/
_original_get_urls = admin.site.get_urls


def _get_urls_with_checkin():
    custom = [
        path('checkin/', admin.site.admin_view(checkin_view), name='checkin'),
    ]
    return custom + _original_get_urls()


admin.site.get_urls = _get_urls_with_checkin


# 后台标题
admin.site.site_header = '未蓝科技 · 智慧教育'
admin.site.site_title = '未蓝科技后台'
admin.site.index_title = '教务管理'

@admin.register(NotifyLog)
class NotifyLogAdmin(admin.ModelAdmin):
    """通知记录：只读，用来回答「这个家长到底收到没有」。

    为什么全只读
    ------------
    这张表是判断「要不要发」的依据（唯一约束）。允许手工改状态，
    就等于允许把「没发出去」改成「已发送」—— 那之后所有的排查
    都建立在假数据上。要重发就删记录（见 allow_delete），
    而不是改状态。

    为什么允许删除
    --------------
    这是唯一的补发手段：删掉某条 ``failed`` 记录，下一次定时任务
    就会重新尝试。比做一个「重发」按钮简单，也更符合这张表的语义
    （记录 = 发过没）。
    """

    list_display = (
        'id', 'lesson_date', 'notify_type', 'status',
        'student', 'parent', 'errcode', 'create_time',
    )
    list_filter = ('notify_type', 'status', 'lesson_date')
    search_fields = ('student__name', 'parent__nickname', 'parent__mobile')
    date_hierarchy = 'lesson_date'
    ordering = ('-create_time',)

    # 全字段只读：见类注释
    readonly_fields = [f.name for f in NotifyLog._meta.fields]

    def has_add_permission(self, request):
        # 手建记录没有意义 —— 记录是「发送动作的副产品」，
        # 手建的记录会让定时任务误以为「已经发过了」而跳过真实发送。
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        # 删记录 = 允许重发一次。这是有意的补发入口，见类注释
        return True
