from django.shortcuts import render

# Create your views here.

from .models import Welcome, JoinRecord

from django.http import JsonResponse


def welcome(request):
    res = Welcome.objects.all().order_by('-order').first()
    img = 'http://127.0.0.1:8000/media/' + str(res.img)
    return JsonResponse({'code': 100, 'msg': "成功", 'result': img})


# 轮播图接口
from rest_framework.mixins import ListModelMixin, CreateModelMixin
from rest_framework.response import Response
from rest_framework.viewsets import GenericViewSet
from .models import Banner, Notice
from .serializer import BannerSerializer, NoticeSerializer, CollectionSerializer, CollectionSaveSerializer


class BannerView(GenericViewSet, ListModelMixin):
    queryset = Banner.objects.all().filter(is_delete=False).order_by('order')[:3]
    serializer_class = BannerSerializer

    def list(self, request, *args, **kwargs):
        res = super().list(request, *args, **kwargs)
        notice = Notice.objects.all().order_by('create_time').first()
        serializer = NoticeSerializer(instance=notice)
        return Response({'code': 100, 'msg': '成功', 'banner': res.data, 'notice': serializer.data})


from .models import Collection
from datetime import datetime
from rest_framework.mixins import DestroyModelMixin


# 信息采集接口--查询-登录用户当天采集的所有数据
class CollectionView(GenericViewSet, ListModelMixin, DestroyModelMixin, CreateModelMixin):
    queryset = Collection.objects.all().filter(create_time__gte=datetime.now().date())
    serializer_class = CollectionSerializer

    def get_serializer_class(self):
        if self.action == 'create':
            return CollectionSaveSerializer
        else:
            return CollectionSerializer

    def list(self, request, *args, **kwargs):
        res = super().list(request, *args, **kwargs)
        today_count = len(self.get_queryset())
        return Response({'code': 100, 'msg': '成功', 'result': res.data, 'today_count': today_count})

    def destroy(self, request, *args, **kwargs):
        from libs.baidu_ai import BaiDuFace
        instance = self.get_object()
        baidu = BaiDuFace()
        res = baidu.delete_user(instance.name_pinyin, face_token=instance.face_token)
        print(res)
        self.perform_destroy(instance)
        return Response()


# 查询当前用户负责的网格
from .models import Area
from .serializer import AreaSerializer


class AreaView(GenericViewSet, ListModelMixin):
    queryset = Area.objects.all()
    serializer_class = AreaSerializer


from .models import Collection
from django.db.models import Count
from django.db.models.functions import Trunc
from .serializer import StatisticsListSerializer


##统计每天采集人数接口
class StatisticsView(GenericViewSet, ListModelMixin):
    queryset = Collection.objects.annotate(date=Trunc('create_time', 'day')).values('date').annotate(
        count=Count('id')).values('date', 'count')
    serializer_class = StatisticsListSerializer


from libs.baidu_ai import BaiDuFace


# 人脸检测接口 post请求
class FaceView(GenericViewSet):
    def create(self, request, *args, **kwargs):
        # 1 取出前端传入的人脸照片
        avatar_object = request.data.get('avatar')
        if not avatar_object:
            return Response({'code': 103, 'msg': '请正常提交人脸'})
        # 使用百度
        ai = BaiDuFace()
        res = ai.search_user(avatar_object)
        if res.get("error_code") == 0:
            # 查到了，取出userid-->能匹配多个，取第一条
            user_id = res.get('result').get('user_list')[0].get('user_id')
            score = int(res.get('result').get('user_list')[0].get('score'))
            # 根据采集库内查出用户详情
            user = Collection.objects.filter(name_pinyin=user_id).first()
            return Response({'code': 100, 'msg': '匹配成功', 'name': user.name, 'score': score})
        else:
            return Response({'code': 102, 'msg': '匹配失败'})


from libs.baidu_ai import BaiDuVoice


class VoiceView(GenericViewSet):
    def create(self, request, *args, **kwargs):
        # 拿出前端传的录音
        voice_object = request.data.get('voice')
        # 提交百度识别
        ai = BaiDuVoice()
        result = ai.speech(voice_object)
        if result.get('err_no') == 0:
            return Response({'code': 100, 'msg': '识别成功', 'result': result.get('result')})
        else:
            return Response({'code': 101, 'msg': '识别失败'})


# 公告接口
from .models import Notice
from .serializer import NoticeSerializer


class NoticeView(GenericViewSet, ListModelMixin):
    queryset = Notice.objects.all().order_by('create_time')
    serializer_class = NoticeSerializer


##活动接口
from .models import Activity
from .serializer import ActivitySerializer


class ActivityView(GenericViewSet, ListModelMixin):
    queryset = Activity.objects.all().order_by('date')
    serializer_class = ActivitySerializer


# # 发送短信验证码接口 and 快速登陆 and 普通手机号登录
# # 需要安装两个模块
# pip3
# install
# djangorestframework - simplejwt
# pip3
# install
# Faker
# #### 视图类####
# from libs.send_tx_sms import get_code, send_sms_by_phone
# from django.core.cache import cache
# from rest_framework.decorators import action
# from .models import UserInfo
# from rest_framework_simplejwt.tokens import RefreshToken
# from faker import Faker
#
#
# class LoginView(GenericViewSet):
#     # 短信发送频率限制---》drf的频率限制：根据ip或手机号
#     # http://192.168.71.100:8000/smart/user/send_sms/?mobile=18923212331
#     @action(methods=['GET'], detail=False)
#     def send_sms(self, request, *args, **kwargs):
#         # 1 取出前端传入手机号--get请求传入的
#         mobile = request.query_params.get('mobile')
#         # 2 获取随机验证码
#         code = get_code()
#         print('验证码：', code)
#         # 3 验证码放到缓存-->临时存储，能存，后期可以根据key取出来--》django提供的
#         cache.set(f'sms_{mobile}', code)
#         # 4 发送短信
#         res = send_sms_by_phone(mobile, code)
#         if res:
#             return Response({'code': 100, 'msg': '短信发送成功'})
#         else:
#             return Response({'code': 101, 'msg': '短信发送失败，请稍后再试'})
#
#     # 其他手机号登录
#     @action(methods=['POST'], detail=False)
#     def login(self, request, *args, **kwargs):
#         # 1 取出手机号和验证码
#         mobile = request.data.get('mobile')
#         code = request.data.get('code')
#         # 2 校验验证码是否正确
#         # 取出当时存的验证码
#         old_code = cache.get(f'sms_{mobile}')
#         if old_code == code:
#             # 3 数据库查询用户，如果存在直接签发token登录成功
#             user = UserInfo.objects.filter(mobile=mobile).first()
#             if not user:
#                 # 4 如果用户不存在，创建用户，再签发token
#                 # pip3 install Faker  随机生成一个中文名
#
#                 fake = Faker('zh_CN')
#                 username = fake.name()
#                 user = UserInfo.objects.create(mobile=mobile, name=username)
#             # 5 能查到用户，直接签发token--》simple-jwt --》pip3 install djangorestframework-simplejwt
#             # https://www.cnblogs.com/liuqingzheng/p/17942227
#             refresh = RefreshToken.for_user(user)
#             return Response(
#                 {'code': 100, 'msg': '登录成功', 'token': str(refresh.access_token), 'name': user.name,
#                  'score': user.score, 'avatar': 'http://127.0.0.1:8000/media/' + str(user.avatar)})
#         else:
#             return Response({'code': 101, 'msg': '验证码错误'})
#
#     # 快速登录
#     @action(methods=['POST'], detail=False)
#     def quick_login(self, request, *args, **kwargs):
#         # 1 取出前端传入的code
#         code = request.data.get('code')
#         # 2 通过code，调用微信开发平台接口，换取手机号
#         # https://developers.weixin.qq.com/miniprogram/dev/OpenApiDoc/user-info/phone-number/getPhoneNumber.html
#         # 3 拿到手机号再自己库中查，能查到，签发token
#         # 4 查不到注册再签发token
#         # 假数据---》都签发成第一个用户
#         user = UserInfo.objects.filter(pk=1).first()
#         refresh = RefreshToken.for_user(user)
#         return Response(
#             {'code': 100, 'msg': '登录成功', 'token': str(refresh.access_token), 'name': user.name, 'score': user.score,
#              'avatar': 'http://127.0.0.1:8000/media/' + str(user.avatar)})
#
#
### 报名后端接口
from .auth import MyJSONWebTokenAuthentication
from rest_framework.decorators import action


class ActivityJoinView(GenericViewSet):
    authentication_classes = [MyJSONWebTokenAuthentication]

    @action(methods=['POST'], detail=False)
    def join(self, request, *args, **kwargs):
        # 1 取出要参加的活动id
        activity_id = request.data.get('id')
        # 2 取出当前登录用户
        user = request.user
        # 2 查到当前活动
        activity = Activity.objects.filter(pk=activity_id).first()
        # 3 判断时间，判断人数
        # 4 判断是否报名过
        join_record = JoinRecord.objects.filter(activity_id=activity_id, user=user).first()
        if join_record:
            return Response({'code': 101, 'msg': "已经报名过，不用重复报名"})
        else:
            # 5 包名人数+1，报名报存入
            activity.count = activity.count + 1
            activity.save()
            JoinRecord.objects.create(activity=activity, user=user)
            # 6 返回报名成功
            return Response({'code': 100, 'msg': "报名成功"})
