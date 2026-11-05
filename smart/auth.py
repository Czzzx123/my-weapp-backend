#####认证类#####
from rest_framework.exceptions import AuthenticationFailed
from rest_framework_simplejwt.authentication import JWTAuthentication

from .models import UserInfo


class MyJSONWebTokenAuthentication(JWTAuthentication):
    """从请求头 token 字段读取 JWT 并还原用户"""

    def authenticate(self, request):
        jwt_value = request.META.get('HTTP_TOKEN')
        if not jwt_value:
            raise AuthenticationFailed('token 字段是必须的')
        validated_token = self.get_validated_token(jwt_value)
        user = UserInfo.objects.filter(pk=validated_token['user_id']).first()
        if not user:
            # token 合法但用户已被删除，不能返回 None 让下游误判为匿名
            raise AuthenticationFailed('用户不存在')
        return user, jwt_value
