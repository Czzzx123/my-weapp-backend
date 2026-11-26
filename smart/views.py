import logging
import time

import requests
from django.db.models import Sum
from rest_framework.decorators import api_view, permission_classes
from rest_framework.mixins import ListModelMixin
from rest_framework.permissions import AllowAny, IsAuthenticated
from rest_framework.response import Response
from rest_framework.viewsets import GenericViewSet
from rest_framework_simplejwt.tokens import RefreshToken

from libs.wechat import WeChatConfigError, WeChatError, code2session

from . import avatar_service, login_service, sms
from .models import (
    Banner,
    ClassSession,
    Enrollment,
    HourLog,
    LearningPost,
    Notice,
    ParentStudent,
    Student,
)
from .serializer import (
    BannerSerializer,
    BindMobileSerializer,
    HourItemSerializer,
    LearningPostSerializer,
    LessonRecordSerializer,
    LoginSerializer,
    NoticeSerializer,
    ScheduleItemSerializer,
    SendSmsSerializer,
    UpdateProfileSerializer,
)

logger = logging.getLogger(__name__)

# 业务成功码，与前端 request.js 的约定一致
OK = 100


def ok(**payload) -> Response:
    """统一的成功响应。额外字段直接平铺进顶层，与既有 banner/notice 接口保持一致。"""
    return Response({'code': OK, 'msg': '成功', **payload})


def fail(msg: str, code: int = 400) -> Response:
    """统一的失败响应。

    code 沿用 HTTP 语义便于前端 request.js 统一处理：
    401 会让前端清理 token 并跳回登录页。
    """
    return Response({'code': code, 'msg': msg})


# ============================================================
# 基础内容（匿名可访问）
# ============================================================


class BannerView(GenericViewSet, ListModelMixin):
    """首页轮播图（附带最新一条公告）"""

    queryset = Banner.objects.all().filter(is_delete=False).order_by('order')[:3]
    serializer_class = BannerSerializer

    def list(self, request, *args, **kwargs):
        res = super().list(request, *args, **kwargs)
        # 首页展示"最新公告"，必须倒序；此前为升序，永远返回最早一条
        notice = Notice.objects.all().order_by('-create_time').first()
        # 无公告时必须返回 null，不能把 None 喂给 serializer
        # （那样会得到 {"title":"","img":null,"content":""} 的空壳对象，
        #   下游用 typeof/!==null 判断会误认为"有公告"）
        notice_data = NoticeSerializer(instance=notice).data if notice else None
        return Response({'code': 100, 'msg': '成功', 'banner': res.data, 'notice': notice_data})


class NoticeView(GenericViewSet, ListModelMixin):
    """公告列表"""

    # 公告列表倒序：最新在前（原为升序，家长看到的是最旧的通知）
    queryset = Notice.objects.all().order_by('-create_time')
    serializer_class = NoticeSerializer


class LearningPostView(GenericViewSet, ListModelMixin):
    """首页「上课日常」列表 —— 课程 / 活动的展示。

    **匿名可访问，这是刻意的。**
    这里的图片是课程活动的公开宣传照（家长、来访咨询的人都能看），
    不是孩子的隐私照片。真正需要按班级 / 学生隔离的是「电子相册」，
    那是另一个未开发的功能，届时才需要做媒体鉴权。

    ``is_delete=False`` 在这里过滤，而不是靠 queryset 的默认 manager：
    后台是软删除（``is_delete`` 打勾），硬删会让已发出的分享链接 404。
    """

    queryset = LearningPost.objects.filter(is_delete=False)
    serializer_class = LearningPostSerializer
    # 首页只展示近期几条，不分页。前端 WXML 也没有上拉加载，
    # 突然返回 20 条会让首页拉得很长，反而难扫读。
    pagination_class = None

    def list(self, request, *args, **kwargs):
        # 首页版面固定 3 张卡（与既有 mock 数据一致），
        # 多的留给「查看全部」页（未做），这里直接切掉，避免前端再截一次。
        queryset = self.filter_queryset(self.get_queryset())[:3]
        serializer = self.get_serializer(queryset, many=True)
        return ok(daily=serializer.data)


# ============================================================
# 登录（双入口）
# ============================================================


@api_view(['POST'])
@permission_classes([AllowAny])
def send_sms(request):
    """发送短信验证码。

    开发期（SMS_DEV_FIXED_CODE=True）固定返回 8888，不真的发短信。
    """
    serializer = SendSmsSerializer(data=request.data)
    if not serializer.is_valid():
        return fail(_first_error(serializer.errors))

    mobile = serializer.validated_data['mobile']
    sent, reason = sms.send_code(mobile)
    if not sent:
        return fail(reason)

    return ok(message='验证码已发送')


@api_view(['POST'])
@permission_classes([AllowAny])
def login(request):
    """双入口登录。

    - 手机号登录：``{mobile, code}``
    - 微信静默登录：``{wx_code}``

    成功后统一签发 JWT 并返回家长信息与已绑孩子。
    """
    serializer = LoginSerializer(data=request.data)
    if not serializer.is_valid():
        return fail(_first_error(serializer.errors))

    data = serializer.validated_data

    if data['mode'] == 'mobile':
        # ---- 手机号 + 验证码 ----
        mobile = data['mobile']
        passed, reason = sms.verify_code(mobile, data['code'])
        if not passed:
            return fail(reason)

        # resolve_identity 会在「该手机号已有另一条微信账号」时自动合并，
        # 返回的才是存活账号，必须用它签 JWT
        parent = login_service.resolve_identity(mobile=mobile)
        login_service.match_children(parent)

    else:
        # ---- 微信静默登录 ----
        try:
            session = code2session(data['wx_code'])
        except WeChatConfigError:
            # 配置问题不该让用户看到细节（AppID / AppSecret 没配或配错）
            logger.error('code2session 配置错误，请检查 .env 的 WECHAT_APPID / WECHAT_APPSECRET')
            return fail('服务暂时不可用，请稍后再试', code=500)
        except WeChatError as exc:
            # 业务失败：code 无效、已用过、频率超限等。
            # errcode 要记下来，否则排查时只能看到「登录凭证已失效」这种笼统提示，
            # 分不清到底是 40029（过期）还是 40163（重复使用）。
            logger.warning('code2session 业务失败 errcode=%s errmsg=%s', exc.errcode, exc.errmsg)
            return fail(exc.message)
        except requests.RequestException as exc:
            # 只捕获网络层故障。原来这里写的是 except Exception，
            # 会把 KeyError / AttributeError 这类编程错误一起吞成「网络不稳定」，
            # 真正的 bug 被藏起来，日志里也看不到。现在只挡 requests 的异常。
            logger.warning('code2session 网络故障：%s', exc)
            return fail('网络不太稳定，请重试')
        # 其余异常（KeyError、编程错误）不在这里捕获 ——
        # 让它们冒泡成 500 并被 EXCEPTION_HANDLER 记录，
        # 否则线上出了 bug 却表现为「网络不好」，永远查不到。

        parent = login_service.resolve_identity(openid=session['openid'])
        login_service.match_children(parent)

    refresh = RefreshToken.for_user(parent)
    payload = login_service.build_login_payload(parent, request=request)

    return ok(token=str(refresh.access_token), **payload)


@api_view(['GET'])
@permission_classes([IsAuthenticated])
def profile(request):
    """返回当前登录家长的完整信息与孩子列表。"""
    parent = request.user
    payload = login_service.build_login_payload(parent, request=request)
    return ok(**payload)


@api_view(['POST'])
@permission_classes([IsAuthenticated])
def bind_mobile(request):
    """微信登录的用户补手机号，补完立即匹配孩子。

    补号后孩子列表可能从空变成非空，所以直接把新的 payload 返回，
    前端不用再发一次 profile 请求。

    **补号可能触发账号合并**：如果该手机号已属于另一条账号（用户以前用
    验证码登录过），两条会合并成一条，当前 JWT 里的 user_id 就失效了。
    因此这里必须**重新签发 token** 一并返回，前端要更新本地 token，
    否则下一次请求就会 401。
    """
    serializer = BindMobileSerializer(data=request.data)
    if not serializer.is_valid():
        return fail(_first_error(serializer.errors))

    survivor = login_service.bind_mobile(request.user, serializer.validated_data['mobile'])

    # 用存活账号重签 token（可能与原 token 指向同一个 id，也可能换了）
    refresh = RefreshToken.for_user(survivor)
    payload = login_service.build_login_payload(survivor, request=request)

    return ok(token=str(refresh.access_token), **payload)


@api_view(['POST'])
@permission_classes([IsAuthenticated])
def logout(request):
    """退出登录。

    JWT 是无状态的，服务端没有 session 可清，实际动作在前端：
    删掉本地 token 即可。这里只做语义占位，方便前端统一调用。
    """
    return ok(message='已退出')


# ============================================================
# 教务：课表 / 课时（家长端只读）
# ============================================================


def _my_student_ids(parent) -> list:
    """当前家长名下**在读**的孩子 id 列表。

    这里是所有家长端教务接口的唯一入口，必须统一走它：
    漏掉 ``status='active'`` 会把已退班的孩子也带出来；
    漏掉家长维度过滤就是越权（家长 A 能看到家长 B 的孩子课表）。
    """
    return list(
        ParentStudent.objects.filter(
            parent=parent, student__status='active',
        ).values_list('student_id', flat=True)
    )


@api_view(['GET'])
@permission_classes([IsAuthenticated])
def schedule(request):
    """我的课表 —— 每周固定安排。

    **只返回「每周固定」的常规课表，不含临时调课 / 单次停课。**
    ``ClassSession.status='suspended'`` 表示这个时段长期停掉，
    这类记录照样返回，但带 ``status`` 字段让前端标灰显示，
    而不是直接过滤掉 —— 直接不返回会让家长以为「孩子这周没课」，
    实际是「课停了但没人通知」，两种情况必须让家长看得出来。

    匿名不可访问：课表含孩子姓名与班级，属隐私数据。
    """
    student_ids = _my_student_ids(request.user)
    if not student_ids:
        # 没绑孩子 / 孩子都已退班：返回空数组，不要报错。
        # 前端据此显示「还没有课表」，报错会让家长以为系统坏了。
        return ok(schedule=[])

    qs = (
        ClassSession.objects
        .filter(
            course_class__enrollments__student_id__in=student_ids,
            # 已结班的班级不该再出现在课表上，否则家长会去上课
            course_class__status='active',
            course_class__enrollments__status='active',
        )
        # 一个班有多个时段时，同一学生会被 join 出多条 —— 这正是我们要的
        .select_related('course_class', 'course_class__course')
        .distinct()
        .order_by('weekday', 'start_time')
    )

    # 上面只拿到时段，拿不到「是哪个孩子的」。所以按学生逐个查，
    # 而不是一次查完再拼 —— 孩子数量在个位数，多几次查询代价很小，
    # 换来的是代码一眼能看懂（谁的孩子对应哪些时段清清楚楚）。
    items = []
    for sid in student_ids:
        student = Student.objects.filter(id=sid).first()
        if not student:
            continue
        enrolled_class_ids = Enrollment.objects.filter(
            student_id=sid, status='active',
            course_class__status='active',
        ).values_list('course_class_id', flat=True)

        if not enrolled_class_ids:
            continue

        for s in ClassSession.objects.filter(
            course_class_id__in=list(enrolled_class_ids)
        ).select_related('course_class', 'course_class__course').order_by(
            'weekday', 'start_time'
        ):
            items.append({
                'student': {'id': student.id, 'name': student.name},
                'course_class': {
                    'id': s.course_class_id,
                    'name': s.course_class.name,
                    'course': {'name': s.course_class.course.name},
                    'teacher': s.course_class.teacher,
                    'room': s.course_class.room,
                },
                'id': s.id,
                'weekday': s.weekday,
                'get_weekday_display': s.get_weekday_display(),
                'start_time': s.start_time,
                'end_time': s.end_time,
                'status': s.status,
                'get_status_display': s.get_status_display(),
                'substitute_teacher': s.substitute_teacher,
            })

    # 孩子间顺序按 id，同一孩子内按周几 + 时间
    serializer = ScheduleItemSerializer(items, many=True)
    return ok(schedule=serializer.data)


@api_view(['GET'])
@permission_classes([IsAuthenticated])
def hours(request):
    """我的课时 —— 每个孩子每个班的额度与剩余。

    ``remain`` 实时由 ``HourLog`` 流水汇总得出，不读任何冗余列。
    已结课 / 已退费的报名照样返回，前端按 ``status`` 分组显示，
    直接过滤掉家长就查不到历史账了。
    """
    student_ids = _my_student_ids(request.user)
    if not student_ids:
        return ok(hours=[])

    qs = (
        Enrollment.objects
        .filter(student_id__in=student_ids)
        .select_related('student', 'course_class', 'course_class__course')
        # 一次 annotate 把所有流水的和算出来，避免每个报名再单独聚合一次
        .annotate(_sum_delta=Sum('hour_logs__delta'))
        .order_by('student_id', '-enroll_time')
    )

    serializer = HourItemSerializer(qs, many=True)
    return ok(hours=serializer.data)


@api_view(['GET'])
@permission_classes([IsAuthenticated])
def lesson_records(request):
    """上课记录 —— 孩子哪天上了哪节课、扣了几课时。

    **只返回消课记录（reason='consumed'），不含充值/冲正/手工调整。**
    家长打开这一页想看的是「孩子上过哪些课」，不是「账怎么平的」。
    充值记录在课时页的「共 N / 已用 M」里已经体现，重复展示只会让人算不清。

    撤销过的记录照样返回，带 ``is_revoked``。若直接过滤掉，
    家长之前看到的「11月25日 扣 1 课时」会凭空消失，他会以为系统偷偷改账。

    支持 ``?student_id=`` 只看某一个孩子；不传则返回名下全部在读孩子。
    """
    student_ids = _my_student_ids(request.user)
    if not student_ids:
        return ok(records=[])

    # 前端传的 student_id 必须在自己的范围内。不校验就是越权：
    # 改个数字就能看到别人家孩子的上课记录。
    raw_sid = request.GET.get('student_id', '')
    if raw_sid:
        if not raw_sid.isdigit() or int(raw_sid) not in student_ids:
            return fail('无权查看该学生的记录', code=403)
        student_ids = [int(raw_sid)]

    qs = (
        HourLog.objects
        .filter(
            enrollment__student_id__in=student_ids,
            reason='consumed',
        )
        .select_related(
            'enrollment__student',
            'enrollment__course_class__course',
            'session',
        )
        # 按上课日期倒序，同一天按签到时间倒序 ——
        # 家长最关心最近上的课，而不是最近被操作的课（补签会打乱后者）
        .order_by('-lesson_date', '-create_time')
    )

    serializer = LessonRecordSerializer(qs, many=True)
    return ok(records=serializer.data)


@api_view(['GET'])
@permission_classes([AllowAny])
def notify_config(request):
    """订阅消息的前端配置 —— 模板 id 从后端下发，不写死在小程序里。

    为什么不让前端硬编码 tmplId：
    模板 id 属于「微信后台配置」，跟运营动作绑定，不跟代码版本绑定。
    写死在前端的话，每次换模板（改文案、字段对不齐）都要走一遍
    小程序发版审核，而审核通常要好几天。下发就只是改个环境变量。

    匿名可访问：家长还没登录时也可能想先授权（登录页引导），
    这时没有 token。模板 id 本身不是敏感信息。

    返回 ``template_id`` 为空字符串表示「后台还没配模板」，
    前端据此隐藏授权按钮，而不是点了才发现失败。

    为什么要一次下发两个模板
    ------------------------
    微信的 ``requestSubscribeMessage`` 一次可以传多个 tmplIds，
    家长在一个弹窗里勾选，**每个模板各自获得一条额度**。
    分成两次弹窗会让家长连点两遍，第二遍的拒绝率明显更高。
    所以这里把「签到通知」和「上课前提醒」一起给前端，
    让它们合并成一次授权。
    """
    from django.conf import settings

    checkin = (getattr(settings, 'WECHAT_TEMPLATE_CHECKIN', '') or '').strip()
    before_class = (getattr(settings, 'WECHAT_TEMPLATE_BEFORE_CLASS', '') or '').strip()

    # 只把配好的塞进去：没配的模板 id 传给微信会报 20001（模板不存在），
    # 整个授权弹窗都会失败 —— 连带把另一个本来能用的模板也搞挂。
    tmpl_ids = [t for t in (checkin, before_class) if t]

    return ok(
        # 上课签到提醒（签到成功后发给家长）。保留这个字段是为了兼容
        # 已经发出去的旧版小程序 —— 它只认 template_id。
        template_id=checkin,
        # 上课前提醒（开课前 N 小时发给家长）
        template_id_before_class=before_class,
        # 一次性授权用的完整列表，前端直接传给 tmplIds
        tmpl_ids=tmpl_ids,
        # 是否可用。一个模板都没配时前端不显示「开启提醒」按钮
        enabled=bool(tmpl_ids),
    )


@api_view(['POST'])
@permission_classes([IsAuthenticated])
def update_profile(request):
    """修改昵称 / 头像。两个字段都可选，支持只传一个。

    为什么必须是 multipart：
    头像走 ``wx.uploadFile`` 传二进制，不用 base64 —— base64 会让体积膨胀
    约 33%，2MB 的图会变成 2.7MB，很容易撞到小程序的请求体上限。

    **头像的坑（务必记住）**：
    微信 ``chooseAvatar`` 返回的是 ``http://tmp/xxx`` 这样的**临时路径**，
    小程序重启后失效。绝不能把它当 URL 存进数据库，必须先上传到我们自己的
    服务器。这也是为什么这里要接收文件而不是接收一个 URL 字符串。
    """
    # 先按 Content-Length 挡一道。
    # 必须放在 serializer 之前：DRF 的 ImageField 校验要把整个文件读进内存
    # 才能判断是不是图片，一个 200MB 的上传会在这一步直接吃掉内存。
    # multipart 有边界和头部的额外开销，留 512KB 余量。
    if _declared_content_length(request) > avatar_service.MAX_UPLOAD_BYTES + 512 * 1024:
        return fail('图片太大了，请选 2MB 以内的')

    serializer = UpdateProfileSerializer(data=request.data)
    if not serializer.is_valid():
        return fail(_first_error(serializer.errors))

    parent = request.user
    data = serializer.validated_data
    changed = []

    # ---- 昵称 ----
    if 'nickname' in data:
        parent.nickname = data['nickname']
        changed.append('nickname')

    # ---- 头像 ----
    if 'avatar' in data:
        try:
            content = avatar_service.process_avatar(data['avatar'])
        except avatar_service.AvatarError as exc:
            return fail(str(exc))

        # 先**把旧文件路径存成字符串**，不能存 FieldFile 对象。
        # 这是个很隐蔽的坑：FieldFile 内部共享 name 属性，
        # 执行 parent.avatar.save(...) 之后 old_avatar.name 也会变成新文件名，
        # 于是「删旧文件」实际删的是刚存好的新文件 —— 头像传上去立刻消失。
        old_avatar_path = parent.avatar.path if parent.avatar else ''

        # 文件名用 parent.id + 时间戳。时间戳是为了绕过图片缓存：
        # 同名 URL 会让小程序继续显示旧图，用户以为没生效。
        filename = f'parent_{parent.id}_{int(time.time())}.webp'
        parent.avatar.save(filename, content, save=False)
        changed.append('avatar')

        parent.save(update_fields=changed)
        avatar_service.delete_avatar_file_by_path(old_avatar_path)
    else:
        if changed:
            parent.save(update_fields=changed)

    logger.info('家长 %s 修改资料：%s', parent.id, changed or '无')

    # 返回完整的 payload，前端直接用它刷新本地缓存，不用再发一次 profile
    payload = login_service.build_login_payload(parent, request=request)
    return ok(**payload)


def _declared_content_length(request) -> int:
    """读取请求体声明的字节数，拿不到或非法时返回 0。

    ``CONTENT_LENGTH`` 是**客户端可控的头**，直接 ``int()`` 会被玩：
    传个 ``abc`` 就是 ValueError → 500。所以这里要兜住。

    返回 0 的含义是「不知道」，不是「空请求」—— chunked 上传
    （``Transfer-Encoding: chunked``）根本不带这个头，取到的是空字符串。
    此时前置检查失效属于预期行为，大小限制交给 serializer 的
    ``ImageField`` 和 ``avatar_service`` 兜底（那两处都在）。
    """
    raw_length = request.META.get('CONTENT_LENGTH')
    if not raw_length:
        return 0
    try:
        return int(raw_length)
    except (TypeError, ValueError):
        logger.warning('CONTENT_LENGTH 不是合法数字：%r，跳过前置大小检查', raw_length)
        return 0


def _first_error(errors: dict) -> str:
    """从 DRF 的嵌套错误结构里取第一条中文提示。"""
    for value in errors.values():
        if isinstance(value, (list, tuple)) and value:
            return str(value[0])
        if isinstance(value, dict):
            return _first_error(value)
        return str(value)
    return '参数有误'
