from django.http import JsonResponse
from rest_framework.mixins import ListModelMixin
from rest_framework.response import Response
from rest_framework.viewsets import GenericViewSet

from .models import Welcome, Banner, Notice
from .serializer import BannerSerializer, NoticeSerializer


# ============================================================
# 基础接口
# ============================================================

def welcome(request):
    """启动页图片"""
    res = Welcome.objects.all().order_by('-order').first()
    if not res:
        return JsonResponse({'code': 102, 'msg': '暂无启动页', 'result': ''})
    img = 'http://127.0.0.1:8000/media/' + str(res.img)
    return JsonResponse({'code': 100, 'msg': '成功', 'result': img})


class BannerView(GenericViewSet, ListModelMixin):
    """首页轮播图（附带最新一条公告）"""

    queryset = Banner.objects.all().filter(is_delete=False).order_by('order')[:3]
    serializer_class = BannerSerializer

    def list(self, request, *args, **kwargs):
        res = super().list(request, *args, **kwargs)
        # 首页展示"最新公告"，必须倒序；此前为升序，永远返回最早一条
        notice = Notice.objects.all().order_by('-create_time').first()
        serializer = NoticeSerializer(instance=notice)
        return Response({'code': 100, 'msg': '成功', 'banner': res.data, 'notice': serializer.data})


class NoticeView(GenericViewSet, ListModelMixin):
    """公告列表"""

    # 公告列表倒序：最新在前（原为升序，家长看到的是最旧的通知）
    queryset = Notice.objects.all().order_by('-create_time')
    serializer_class = NoticeSerializer
