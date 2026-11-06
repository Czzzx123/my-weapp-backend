#####认证类#####
from rest_framework.exceptions import AuthenticationFailed
from rest_framework_simplejwt.authentication import JWTAuthentication

from .models import Parent


class MyJSONWebTokenAuthentication(JWTAuthentication):
    """从请求头 token 字段读取 JWT 并还原家长身份。

    登录主体是 Parent（家长 / 老师 / 管理员统一身份表）。

    重要：**请求头没有 token 时必须返回 None，不能抛异常**。
    认证类（authenticate）的职责只是「如果有凭证，就把它翻译成用户」；
    「要不要登录才能访问」由 permission_classes 决定。
    在这里抛 AuthenticationFailed 会让 AllowAny 的接口（发验证码、登录本身）
    也返回 401，等于登录入口把自己锁死了。
    """

    def authenticate(self, request):
        jwt_value = request.META.get('HTTP_TOKEN')

        # 没有 token → 匿名访问。是否需要登录交给权限类判断。
        # 但兼容旧行为：如果显式传了空字符串，视为前端 bug，提示一下。
        if not jwt_value:
            return None

        validated_token = self.get_validated_token(jwt_value)
        user = Parent.objects.filter(pk=validated_token['user_id']).first()
        if not user:
            # token 合法但用户已被删除，不能返回 None 让下游误判为匿名
            raise AuthenticationFailed('用户不存在')
        return user, jwt_value
