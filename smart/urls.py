"""
URL configuration for smart_backend project.

The `urlpatterns` list routes URLs to views. For more information please see:
    https://docs.djangoproject.com/en/6.0/topics/http/urls/
Examples:
Function views
    1. Add an import:  from my_app import views
    2. Add a URL to urlpatterns:  path('', views.home, name='home')
Class-based views
    1. Add an import:  from other_app.views import Home
    2. Add a URL to urlpatterns:  path('', Home.as_view(), name='home')
Including another URLconf
    1. Import the include() function: from django.urls import include, path
    2. Add a URL to urlpatterns:  path('blog/', include('blog.urls'))
"""
from django.contrib import admin
from django.contrib.auth.views import LoginView
from django.urls import path
from .views import welcome, BannerView, CollectionView, AreaView, StatisticsView, FaceView, VoiceView, NoticeView, \
    ActivityView,ActivityJoinView

urlpatterns = [
    path('welcome/', welcome),

]
from rest_framework.routers import SimpleRouter

router = SimpleRouter()
router.register('banner', BannerView, 'banner')
router.register('collection', CollectionView, 'collection')
router.register('area', AreaView, 'area')
router.register('statistics', StatisticsView, 'statistics')
router.register('face', FaceView, 'face')
router.register('voice', VoiceView, 'voice')
router.register('notice', NoticeView, 'notice')
router.register('activity', ActivityView, 'activity')
# router.register('user',LoginView,'user')
router.register('join',ActivityJoinView,'join')
# ###路由####
# router.register('user', LoginView, 'user')
# ### 封装的发送短信包--》直接复制到libs目录下即可##

urlpatterns += router.urls
