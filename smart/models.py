from django.db import models

# ============================================================
# 未蓝科技 · 智慧教育 —— 数据模型
# ============================================================


class Welcome(models.Model):
    """启动欢迎页（App 开屏广告）"""

    img = models.ImageField(upload_to='welcome', default='/welcome/slash.png')
    order = models.IntegerField()
    create_time = models.DateTimeField(auto_now_add=True)
    is_delete = models.BooleanField(default=False)

    class Meta:
        verbose_name_plural = '欢迎表'

    def __str__(self):
        return str(self.img)


class Banner(models.Model):
    """首页轮播图"""

    img = models.ImageField(upload_to='banner', default='banner1.png', verbose_name='图片')
    order = models.IntegerField(verbose_name='顺序')
    create_time = models.DateTimeField(auto_now=True, verbose_name='创建时间')
    is_delete = models.BooleanField(default=False, verbose_name='是否删除')

    class Meta:
        verbose_name_plural = '轮播图'

    def __str__(self):
        return str(self.img)


class Notice(models.Model):
    """公告 / 通知"""

    title = models.CharField(max_length=64, verbose_name='公告标题')
    content = models.TextField(verbose_name='内容')
    img = models.ImageField(upload_to='notice', default='notice.png', verbose_name='公告图片')
    create_time = models.DateTimeField(auto_now=True, verbose_name='创建时间')

    class Meta:
        verbose_name_plural = '公告表'

    def __str__(self):
        return self.title


class UserInfo(models.Model):
    """用户（登录主体）

    注意：阶段 1.2 会重新评估此模型（是否拆出 Parent / Student），
    当前 avatar 为必填且无默认值，openid 静默注册时会失败，届时一并处理。
    """

    name = models.CharField(verbose_name='姓名', max_length=32)
    avatar = models.FileField(verbose_name='头像', max_length=128, upload_to='avatar')
    create_date = models.DateTimeField(verbose_name='日期', auto_now_add=True)
    score = models.IntegerField(verbose_name='积分', default=0)

    # 用户需要用手机号登录
    mobile = models.CharField(max_length=11, null=True, verbose_name='手机号')

    class Meta:
        verbose_name_plural = '用户表'

    def __str__(self):
        return self.name
