from rest_framework import serializers

from .models import Banner, Notice


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
