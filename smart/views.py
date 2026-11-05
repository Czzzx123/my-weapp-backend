from rest_framework.mixins import ListModelMixin
from rest_framework.response import Response
from rest_framework.viewsets import GenericViewSet

from .models import Banner, Notice
from .serializer import BannerSerializer, NoticeSerializer


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
