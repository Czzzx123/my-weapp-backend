from django.contrib import admin
from django.db.models import Count, Sum

from .models import (
    Banner, Notice,
    Parent, Student, ParentStudent,
    Course, CourseClass, ClassSession,
    Enrollment, HourLog,
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


class HourLogInline(admin.TabularInline):
    model = HourLog
    extra = 1
    fields = ('delta', 'reason', 'session', 'operator', 'remark')
    verbose_name_plural = '课时流水（只能新增，不能改旧账）'

    def get_readonly_fields(self, request, obj=None):
        # 已有流水不可改，只能新增一条来冲正
        if obj is not None:
            return ('delta', 'reason', 'session', 'operator')
        return ()


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


# 后台标题
admin.site.site_header = '未蓝科技 · 智慧教育'
admin.site.site_title = '未蓝科技后台'
admin.site.index_title = '教务管理'
