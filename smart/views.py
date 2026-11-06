import logging
import time

from rest_framework.decorators import api_view, permission_classes
from rest_framework.mixins import ListModelMixin
from rest_framework.permissions import AllowAny, IsAuthenticated
from rest_framework.response import Response
from rest_framework.viewsets import GenericViewSet
from rest_framework_simplejwt.tokens import RefreshToken

from libs.wechat import WeChatConfigError, WeChatError, code2session

from . import avatar_service, login_service, sms
from .models import Banner, Notice
from .serializer import (
    BannerSerializer,
    BindMobileSerializer,
    LoginSerializer,
    NoticeSerializer,
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
            # 配置问题不该让用户看到细节
            return fail('服务暂时不可用，请稍后再试', code=500)
        except WeChatError as exc:
            # 业务失败：前端应重新 wx.login() 换个 code
            return fail(exc.message)
        except Exception:
            # 网络超时等，可重试
            return fail('网络不太稳定，请重试')

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
    declared_size = int(request.META.get('CONTENT_LENGTH') or 0)
    if declared_size > avatar_service.MAX_UPLOAD_BYTES + 512 * 1024:
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


def _first_error(errors: dict) -> str:
    """从 DRF 的嵌套错误结构里取第一条中文提示。"""
    for value in errors.values():
        if isinstance(value, (list, tuple)) and value:
            return str(value[0])
        if isinstance(value, dict):
            return _first_error(value)
        return str(value)
    return '参数有误'
