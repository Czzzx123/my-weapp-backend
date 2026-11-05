from django.urls import path
from rest_framework.routers import SimpleRouter

from .views import BannerView, NoticeView

urlpatterns = []

router = SimpleRouter()
router.register('banner', BannerView, 'banner')
router.register('notice', NoticeView, 'notice')

urlpatterns += router.urls
