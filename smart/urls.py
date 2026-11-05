from django.urls import path
from rest_framework.routers import SimpleRouter

from .views import welcome, BannerView, NoticeView

urlpatterns = [
    path('welcome/', welcome),
]

router = SimpleRouter()
router.register('banner', BannerView, 'banner')
router.register('notice', NoticeView, 'notice')

urlpatterns += router.urls
