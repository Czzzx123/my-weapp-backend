from django.apps import AppConfig


class SmartConfig(AppConfig):
    name = 'smart'
    # Admin 侧边栏显示的应用名。原值「首页欢迎」是旧智慧社区项目残留。
    verbose_name = '智慧教育'