"""首页「学习动态」（LearningPost）接口测试。

运行方式：

    ./.venv/Scripts/python.exe manage.py test smart.tests_learning -v 2

**本文件守的是三条线**：

1. 接口必须**匿名可访问** —— 这是课程活动的公开宣传内容，未登录的新访客
   也要能看到。若有人给这个视图加上 ``IsAuthenticated``，这里会失败。
2. 输出字段名是 ``course`` / ``date``，**不是** ``course_name`` / ``create_time``。
   这是为对齐前端 ``daily_list`` 的既有结构，改错前端就是一片空白。
3. 软删除（``is_delete``）和「只取 3 条」必须生效。
"""

import io
import tempfile

from PIL import Image
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase, override_settings
from rest_framework.test import APIClient

from smart.models import LearningPost

# 测试期间把 MEDIA_ROOT 指到临时目录，避免往真实 media/ 塞垃圾文件
TEMP_MEDIA_ROOT = tempfile.mkdtemp(prefix='weilan-test-learning-')

LEARNING_URL = '/smart/learning/'


def make_image(name='pic.jpg'):
    """生成一张可上传的图片。"""
    buf = io.BytesIO()
    Image.new('RGB', (750, 420), (47, 107, 237)).save(buf, format='JPEG')
    return SimpleUploadedFile(name, buf.getvalue(), content_type='image/jpeg')


def make_post(title='猜数字小游戏完成', **kwargs):
    defaults = {
        'course_name': 'Python课堂',
        'content': '今天的Python课堂，同学们完成了猜数字小游戏的编写。',
        'img': make_image(),
        'theme': 'blue',
    }
    defaults.update(kwargs)
    return LearningPost.objects.create(title=title, **defaults)


@override_settings(MEDIA_ROOT=TEMP_MEDIA_ROOT)
class LearningPostApiTests(TestCase):
    def setUp(self):
        self.client = APIClient(HTTP_HOST='127.0.0.1')

    # ---------- 匿名可访问 ----------

    def test_anonymous_can_read(self):
        """未登录必须能读到 —— 这是公开宣传内容，不是家长专属。

        若有人误加 IsAuthenticated，这条会失败。
        """
        make_post()

        resp = self.client.get(LEARNING_URL)

        self.assertEqual(resp.status_code, 200)
        body = resp.json()
        self.assertEqual(body['code'], 100)
        self.assertEqual(len(body['daily']), 1)

    def test_empty_list_returns_empty_array_not_null(self):
        """没有数据时返回 []，不是 null。

        前端 ``daily_list`` 直接 wx:for，null 会让页面渲染报错。
        """
        body = self.client.get(LEARNING_URL).json()
        self.assertEqual(body['daily'], [])

    # ---------- 字段契约 ----------

    def test_field_names_match_frontend_contract(self):
        """键名必须是 course / date，不是 course_name / create_time。

        前端 index.wxml 里写的是 {{item.course}} 和 {{item.date}}，
        改键名等于把首页卡片变成空白。这条测试是那份契约的锁。
        """
        make_post()

        item = self.client.get(LEARNING_URL).json()['daily'][0]

        self.assertIn('course', item)
        self.assertIn('date', item)
        self.assertNotIn('course_name', item)
        self.assertNotIn('create_time', item)
        self.assertEqual(item['course'], 'Python课堂')

    def test_date_is_chinese_short_format(self):
        """date 输出 ``11月23日`` 形态，前端直接显示，不再二次格式化。"""
        make_post()

        date = self.client.get(LEARNING_URL).json()['daily'][0]['date']

        self.assertRegex(date, r'^\d{1,2}月\d{1,2}日$')

    def test_img_is_absolute_url(self):
        """img 必须是带域名的绝对地址。

        小程序 <image> 不认相对路径 /media/xxx.jpg，
        前端也没做拼接，所以后端要给全。
        """
        make_post()

        img = self.client.get(LEARNING_URL).json()['daily'][0]['img']

        self.assertTrue(img.startswith('http'), f'img 不是绝对地址：{img}')

    def test_no_internal_fields_leaked(self):
        """is_delete 这类内部字段不该出现在接口上。"""
        make_post()

        item = self.client.get(LEARNING_URL).json()['daily'][0]

        self.assertNotIn('is_delete', item)

    # ---------- 过滤与排序 ----------

    def test_soft_deleted_posts_are_hidden(self):
        """软删除的记录不能出现在首页。"""
        make_post(title='正常一条')
        make_post(title='已删除一条', is_delete=True)

        daily = self.client.get(LEARNING_URL).json()['daily']

        self.assertEqual(len(daily), 1)
        self.assertEqual(daily[0]['title'], '正常一条')

    def test_only_latest_three_returned(self):
        """首页固定 3 张卡，多发的数据在这里切掉。"""
        for i in range(5):
            make_post(title=f'第{i}条')

        daily = self.client.get(LEARNING_URL).json()['daily']

        self.assertEqual(len(daily), 3)

    def test_newest_first(self):
        """最新的在最前，否则家长要先划过时的内容。"""
        make_post(title='旧的一条')
        make_post(title='新的一条')

        daily = self.client.get(LEARNING_URL).json()['daily']

        self.assertEqual(daily[0]['title'], '新的一条')


@override_settings(MEDIA_ROOT=TEMP_MEDIA_ROOT)
class LearningPostAdminTests(TestCase):
    """后台录入入口。运营只用后台，这里坏了等于功能没法用。"""

    def setUp(self):
        from django.contrib.auth import get_user_model
        User = get_user_model()
        User.objects.create_superuser('root_test', 'r@example.com', 'pwd')
        self.client = APIClient(HTTP_HOST='127.0.0.1')
        self.client.login(username='root_test', password='pwd')

    def test_admin_list_hides_deleted_by_default(self):
        """列表默认不显示软删除记录，否则历史数据会淹没在列表里。"""
        make_post(title='正常的')
        make_post(title='删掉的', is_delete=True)

        from django.urls import reverse
        resp = self.client.get(reverse('admin:smart_learningpost_changelist'))

        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, '正常的')
        self.assertNotContains(resp, '删掉的')

    def test_admin_list_can_show_deleted_when_filtered(self):
        """筛选器切到「是」时必须能找回被删的记录。"""
        make_post(title='删掉的', is_delete=True)

        from django.urls import reverse
        resp = self.client.get(
            reverse('admin:smart_learningpost_changelist'), {'is_delete__exact': '1'})

        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, '删掉的')

    def test_admin_add_page_renders(self):
        """新增页能打开，说明 fieldsets 里的字段名没有拼错。"""
        from django.urls import reverse
        resp = self.client.get(reverse('admin:smart_learningpost_add'))
        self.assertEqual(resp.status_code, 200)
