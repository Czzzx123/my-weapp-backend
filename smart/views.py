from rest_framework.decorators import api_view, permission_classes
from rest_framework.mixins import ListModelMixin
from rest_framework.permissions import AllowAny, IsAuthenticated
from rest_framework.response import Response
from rest_framework.viewsets import GenericViewSet
from rest_framework_simplejwt.tokens import RefreshToken

from libs.wechat import WeChatConfigError, WeChatError, code2session

from . import login_service, sms
from .models import Banner, Notice
from .serializer import (
    BannerSerializer,
    BindMobileSerializer,
    LoginSerializer,
    NoticeSerializer,
    SendSmsSerializer,
)

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

        parent, _ = login_service.get_or_create_parent(mobile=mobile)
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

        parent, _ = login_service.get_or_create_parent(openid=session['openid'])
        login_service.match_children(parent)

    refresh = RefreshToken.for_user(parent)
    payload = login_service.build_login_payload(parent)

    return ok(token=str(refresh.access_token), **payload)


@api_view(['GET'])
@permission_classes([IsAuthenticated])
def profile(request):
    """返回当前登录家长的完整信息与孩子列表。"""
    parent = request.user
    payload = login_service.build_login_payload(parent)
    return ok(**payload)


@api_view(['POST'])
@permission_classes([IsAuthenticated])
def bind_mobile(request):
    """微信登录的用户补手机号，补完立即匹配孩子。

    补号后孩子列表可能从空变成非空，所以直接把新的 payload 返回，
    前端不用再发一次 profile 请求。
    """
    serializer = BindMobileSerializer(data=request.data)
    if not serializer.is_valid():
        return fail(_first_error(serializer.errors))

    parent = request.user
    login_service.bind_mobile(parent, serializer.validated_data['mobile'])

    # 补号后孩子列表可能从空变成非空，直接把新的 payload 返回，
    # 前端不用再发一次 profile 请求
    payload = login_service.build_login_payload(parent)

    return ok(**payload)


@api_view(['POST'])
@permission_classes([IsAuthenticated])
def logout(request):
    """退出登录。

    JWT 是无状态的，服务端没有 session 可清，实际动作在前端：
    删掉本地 token 即可。这里只做语义占位，方便前端统一调用。
    """
    return ok(message='已退出')


def _first_error(errors: dict) -> str:
    """从 DRF 的嵌套错误结构里取第一条中文提示。"""
    for value in errors.values():
        if isinstance(value, (list, tuple)) and value:
            return str(value[0])
        if isinstance(value, dict):
            return _first_error(value)
        return str(value)
    return '参数有误'
