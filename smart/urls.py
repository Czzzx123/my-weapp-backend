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
    # 上课记录：孩子哪天上了哪节课、扣了几课时（只含消课）
    path('lesson_records/', views.lesson_records, name='lesson_records'),
    # 订阅消息的模板 id 下发（匿名可读，模板没配时 enabled=false）
    path('notify_config/', views.notify_config, name='notify_config'),

    # 请假（家长端写操作，必须登录）
    # 口径：提交即生效（不走审批）、开课后不能请、不扣课时
    path('leave/submit/', views.leave_submit, name='leave_submit'),
    path('leave/cancel/', views.leave_cancel, name='leave_cancel'),
    path('leave/list/', views.leave_list, name='leave_list'),
]

router = SimpleRouter()
router.register('banner', BannerView, 'banner')
router.register('notice', NoticeView, 'notice')
router.register('learning', LearningPostView, 'learning')

urlpatterns += router.urls
