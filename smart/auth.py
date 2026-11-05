#####认证类#####
from .models import UserInfo
from rest_framework.authentication import BaseAuthentication
from rest_framework.exceptions import AuthenticationFailed
from rest_framework_simplejwt.authentication import JWTAuthentication

class MyJSONWebTokenAuthentication(JWTAuthentication):
    def authenticate(self, request):
        jwt_value = request.META.get("HTTP_TOKEN")
        if not jwt_value:
            raise AuthenticationFailed('token 字段是必须的')
        validated_token = self.get_validated_token(jwt_value)
        print(validated_token['user_id'])
        user = UserInfo.objects.filter(pk=validated_token['user_id']).first()
        return user, jwt_value