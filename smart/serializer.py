from rest_framework import serializers
from rest_framework.exceptions import APIException

from .models import Banner, Notice, Collection, Area, Activity
from libs.baidu_ai import BaiDuFace


# 轮播图表序列化类
class BannerSerializer(serializers.ModelSerializer):
    class Meta:
        model = Banner
        fields = '__all__'


# 社区通知序列化类
class NoticeSerializer(serializers.ModelSerializer):
    class Meta:
        model = Notice
        fields = ['id', 'title', 'img', 'create_time', 'content']
        # create_time 只想要年月日，不要时分秒
        extra_kwargs = {
            'create_time': {'format': '%Y-%m-%d'}
        }


class CollectionSerializer(serializers.ModelSerializer):
    class Meta:
        model = Collection
        fields = ['id', 'name', 'avatar', 'area']
        depth = 1  # area 外键关联详情拿到


class CollectionSaveSerializer(serializers.ModelSerializer):
    class Meta:
        model = Collection
        fields = ['name', 'avatar', 'area']

    def create(self, validated_data):
        # 完成保存
        # 把图片保存到自己本地
        # 上传到百度人脸库，再保存到本地，返回前端，完成采集
        ai = BaiDuFace()
        # 1 取出前端传入的人脸图片
        file_obj = validated_data.get('avatar')
        # 2 取出前端传入的人名
        name = validated_data.get('name')
        # 3 把人名转成拼音
        name_pinyin = ai.name_to_pinyin(name)
        # 上传到百度ai库
        res = ai.add_user(file_obj, name_pinyin)
        # 上传成功，取出faceToken保存到本地表中。
        if res.get('error_code') == 0:
            validated_data['face_token'] = res.get('result').get('face_token')
            validated_data['name_pinyin'] = name_pinyin
            # 保存到数据库
            instance = super().create(validated_data)
            return instance
        else:
            return APIException('采集信息失败')


##网格序列化类
class AreaSerializer(serializers.ModelSerializer):
    class Meta:
        model = Area
        fields = ['id', 'name', 'desc']


# 采集统计接口 get请求
class StatisticsListSerializer(serializers.Serializer):
    date = serializers.DateTimeField(format='%Y年%m月%d日')
    count = serializers.IntegerField()


# 活动序列化类
class ActivitySerializer(serializers.ModelSerializer):
    class Meta:
        model = Activity
        fields = ['id', 'title', 'text', 'date', 'count', 'score', 'total_count']
        extra_kwargs = {
            'date': {'format': '%Y-%m-%d'}
        }
