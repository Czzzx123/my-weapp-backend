import re

from rest_framework import serializers

from .models import Banner, Notice

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
