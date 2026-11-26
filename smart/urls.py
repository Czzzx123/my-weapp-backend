from django.urls import path
from rest_framework.routers import SimpleRouter

from . import views
from .views import BannerView, LearningPostView, NoticeView

urlpatterns = [
    # 登录（双入口）
    path('user/send_sms/', views.send_sms, name='send_sms'),
    path('user/login/', views.login, name='login'),
    path('user/profile/', views.profile, name='profile'),
    path('user/bind_mobile/', views.bind_mobile, name='bind_mobile'),
    path('user/update_profile/', views.update_profile, name='update_profile'),
    path('user/logout/', views.logout, name='logout'),

    # 教务（家长端只读，必须登录）
    path('schedule/', views.schedule, name='schedule'),
    path('hours/', views.hours, name='hours'),
]

router = SimpleRouter()
router.register('banner', BannerView, 'banner')
router.register('notice', NoticeView, 'notice')
router.register('learning', LearningPostView, 'learning')

urlpatterns += router.urls
