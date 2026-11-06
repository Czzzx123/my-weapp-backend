import re

from rest_framework import serializers

from .models import Banner, Notice

# 中国大陆手机号：1 开头，第二位 3-9，共 11 位
MOBILE_REGEX = r'^1[3-9]\d{9}$'


class BannerSerializer(serializers.ModelSerializer):
    """轮播图表序列化类"""

    class Meta:
        model = Banner
        fields = '__all__'


class NoticeSerializer(serializers.ModelSerializer):
    """公告序列化类"""

    class Meta:
        model = Notice
        fields = ['id', 'title', 'img', 'create_time', 'content']
        # create_time 只想要年月日，不要时分秒
        extra_kwargs = {
            'create_time': {'format': '%Y-%m-%d'}
        }


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
