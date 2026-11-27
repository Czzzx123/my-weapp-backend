"""首页「学习动态」（LearningPost）接口测试。

运行方式：

    ./.venv/Scripts/python.exe manage.py test smart.tests_learning -v 2

**本文件守的是四条线**：

1. 接口必须**匿名可访问** —— 这是课程活动的公开宣传内容，未登录的新访客
   也要能看到。若有人给这个视图加上 ``IsAuthenticated``，这里会失败。
2. 输出字段名是 ``course`` / ``date``，**不是** ``course_name`` / ``create_time``。
   这是为对齐前端 ``daily_list`` 的既有结构，改错前端就是一片空白。
3. 软删除（``is_delete``）必须生效。
4. **游标分页必须不重不漏** —— 这是 2026-11-27 从「固定 3 条」改成
   「无限滚动」之后新增的约束。翻页期间运营插入新内容、以及非法的
   ``before`` 参数，都在下面有专门用例。
"""

import io
import tempfile
from datetime import datetime
from datetime import timezone as dt_timezone

from PIL import Image
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase, override_settings
from django.utils import timezone
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
        """今年创建的记录，date 输出 ``11月23日`` 形态，不带年份。

        今年不带年份是刻意的：首页卡片位置窄，多四个字会把标题挤掉一行，
        而且「上课日常」的绝大多数内容都是今年的，写上年份纯属冗余。
        """
        make_post()

        date = self.client.get(LEARNING_URL).json()['daily'][0]['date']

        self.assertRegex(date, r'^\d{1,2}月\d{1,2}日$')

    def test_date_carries_year_for_old_posts(self):
        """往年内容必须补上年份，否则「10月18日」分不清是哪一年。

        改无限滚动之前只看 3 条，最多回溯一两周，碰不到去年，
        所以那时丢年份没关系。现在家长能一直滑到去年，必须补。
        """
        post = make_post()
        # auto_now_add 会覆盖 create_time，所以先建再 UPDATE。
        # 用 5 月 6 日这种「离现在够远、又不在年初」的日期，
        # 避免 1 月 1 日那类跨年边界的干扰。
        LearningPost.objects.filter(pk=post.pk).update(
            create_time=timezone.make_aware(datetime(2025, 5, 6, 10, 0)))

        date = self.client.get(LEARNING_URL).json()['daily'][0]['date']

        self.assertEqual(date, '2025年5月6日')

    def test_date_uses_shanghai_time_not_utc(self):
        """凌晨的记录要按北京时间算日期，不能按 UTC。

        坑：USE_TZ=True 时 create_time 是 UTC。北京时间 2026-11-27 00:30
        在 UTC 是 11-26 16:30，直接取 create_time.day 会显示成 26 号。
        家长半夜看不到「今天」的动态，这种错很难被发现。
        """
        post = make_post()
        # 北京时间 00:30 == UTC 前一天 16:30
        LearningPost.objects.filter(pk=post.pk).update(
            create_time=datetime(2026, 11, 26, 16, 30, tzinfo=dt_timezone.utc))

        date = self.client.get(LEARNING_URL).json()['daily'][0]['date']

        self.assertEqual(date, '11月27日')

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

    def test_default_page_size_is_six(self):
        """不传参数时一页 6 条。

        这个数字和前端体验绑着：一屏大概看得到 2~3 张卡，
        6 条能保证滑到底部时下一批已经在路上，不会一顿一顿地等。
        改动它等于改首屏观感，所以锁在这里。
        """
        for i in range(10):
            make_post(title=f'第{i}条')

        body = self.client.get(LEARNING_URL).json()

        self.assertEqual(len(body['daily']), 6)
        self.assertTrue(body['has_more'])

    def test_newest_first(self):
        """最新的在最前，否则家长要先划过时的内容。"""
        make_post(title='旧的一条')
        make_post(title='新的一条')

        daily = self.client.get(LEARNING_URL).json()['daily']

        self.assertEqual(daily[0]['title'], '新的一条')

    def test_backdated_post_stays_in_its_time_position(self):
        """补录旧内容不能窜到最顶端。

        这个 bug 2026-11-27 真出过：当时为了「游标严密」把排序从
        ``-create_time`` 改成 ``-id``，结果运营补录一条去年的活动，
        它 id 最大却时间最旧，直接排到第一 —— 家长看到的最新动态
        其实是一年前的。

        **id 是录入顺序，create_time 才是发布时间**，两者不是一回事。
        """
        LearningPost.objects.create(
            title='今年最新的', course_name='Python课堂', theme='blue')
        old = LearningPost.objects.create(
            title='去年补录的', course_name='Python课堂', theme='blue')
        # 补录：id 最大，但发布时间是去年的
        LearningPost.objects.filter(pk=old.pk).update(
            create_time=timezone.make_aware(datetime(2025, 3, 8, 10, 0)))

        titles = [x['title'] for x in self.client.get(LEARNING_URL).json()['daily']]

        self.assertEqual(titles[0], '今年最新的')
        self.assertEqual(titles[-1], '去年补录的')

    # ---------- 游标分页 ----------

    def test_cursor_walks_all_pages_without_dup_or_gap(self):
        """一路翻到底，每条记录必须恰好出现一次，且顺序与列表一致。

        这是无限滚动最要紧的约束。少一条 = 家长永远看不到那条动态；
        多一条 = 同一张卡连着出现两次，看起来像重复发文。

        期望顺序必须和视图的 ``order_by('-create_time', '-id')`` 一致，
        不能再用 ``-id`` —— 那正是 2026-11-27 那个「补录旧内容窜到顶部」
        bug 的来源，用它当期望等于把错误写成契约。
        """
        for i in range(14):
            make_post(title=f'第{i}条')

        expected = list(
            LearningPost.objects.order_by('-create_time', '-id')
            .values_list('id', flat=True))

        seen = self._walk_all_pages()

        self.assertEqual(seen, expected)

    def test_cursor_paging_across_mixed_dates(self):
        """时间乱序的数据也要能翻完，不重不漏。

        专门造「id 顺序与时间顺序不一致」的数据：先建一条很早的，
        再建几条正常的，再建一条更早的。用裸 id 当游标会在这里翻车
        （跳过或重复），复合游标才过得去。
        """
        early = make_post(title='很早的一条')
        LearningPost.objects.filter(pk=early.pk).update(
            create_time=timezone.make_aware(datetime(2025, 1, 5, 9, 0)))
        for i in range(8):
            make_post(title=f'正常{i}')
        early2 = make_post(title='也很早的一条')
        LearningPost.objects.filter(pk=early2.pk).update(
            create_time=timezone.make_aware(datetime(2025, 2, 6, 9, 0)))

        expected = list(
            LearningPost.objects.order_by('-create_time', '-id')
            .values_list('id', flat=True))
        seen = self._walk_all_pages(page_size=3)

        self.assertEqual(seen, expected)
        self.assertEqual(len(seen), len(set(seen)), '翻页出现了重复')
        self.assertEqual(len(seen), 10)

    def test_cursor_does_not_repeat_records_with_identical_timestamp(self):
        """同一时刻的多条记录不能被重复发送。

        批量导入的数据 create_time 常常一模一样。若游标只比 create_time
        （``create_time__lte``），这几条会在下一页再来一遍。
        必须用 ``(create_time, id)`` 复合比较。
        """
        same = timezone.make_aware(datetime(2026, 10, 1, 12, 0))
        for i in range(7):
            p = make_post(title=f'同一秒{i}')
            LearningPost.objects.filter(pk=p.pk).update(create_time=same)

        seen = self._walk_all_pages(page_size=2)

        self.assertEqual(len(seen), 7)
        self.assertEqual(len(seen), len(set(seen)), '同一时刻的记录被重复发送')

    def _walk_all_pages(self, page_size=None):
        """一路翻到底，返回按顺序收集到的 id 列表。

        抽成方法是因为有 3 个用例要走完整轮询，各写一遍容易漂移。
        """
        seen, cursor, guard = [], None, 0
        while True:
            guard += 1
            self.assertLess(guard, 40, '翻页没有终止，has_more/next_cursor 逻辑有问题')
            params = []
            if cursor is not None:
                params.append(f'before={cursor}')
            if page_size:
                params.append(f'page_size={page_size}')
            url = LEARNING_URL + ('?' + '&'.join(params) if params else '')
            body = self.client.get(url).json()
            seen.extend(item['id'] for item in body['daily'])
            if not body['has_more']:
                return seen
            cursor = body['next_cursor']
            self.assertIsNotNone(cursor, 'has_more=true 时必须给 next_cursor')

    def test_last_page_sets_has_more_false_and_null_cursor(self):
        """最后一页 has_more=false 且 next_cursor=null。

        前端靠 has_more 决定显示「已经到底啦」还是继续加载；
        next_cursor 给 null 是为了让前端不必猜 —— 它只要原样回传就行。
        """
        for i in range(3):
            make_post(title=f'第{i}条')

        body = self.client.get(LEARNING_URL).json()

        self.assertEqual(len(body['daily']), 3)
        self.assertFalse(body['has_more'])
        self.assertIsNone(body['next_cursor'])

    def test_exactly_page_size_has_no_more(self):
        """刚好一整页时 has_more=false，不能多显示一个「加载中」。

        「多取一条」判断法的边界：6 条数据取 7 条拿到 6 条，
        len(6) > 6 为假，正确。若代码写成 ``>=`` 就会显示一个
        永远点不出来的「上拉加载更多」。
        """
        for i in range(6):
            make_post(title=f'第{i}条')

        body = self.client.get(LEARNING_URL).json()

        self.assertEqual(len(body['daily']), 6)
        self.assertFalse(body['has_more'])

    def test_new_post_during_paging_does_not_duplicate(self):
        """翻页中途有新动态插入，已翻过的位置不能被打乱。

        这正是选游标而非 ``?page=`` 的理由。用页码分页时，
        插一条新记录会让所有内容后移一格，第 2 页会把第 1 页
        最后一条再发一遍。这里显式模拟这个场景。
        """
        make_post(title='第一条')
        make_post(title='第二条')
        make_post(title='第三条')
        post_first_id = LearningPost.objects.get(title='第一条').id

        page1 = self.client.get(f'{LEARNING_URL}?page_size=2').json()
        first_ids = [x['id'] for x in page1['daily']]

        # 家长正看着，运营发了一条新的
        make_post(title='刚发的')

        page2 = self.client.get(
            f'{LEARNING_URL}?page_size=2&before={page1["next_cursor"]}').json()
        second_ids = [x['id'] for x in page2['daily']]

        self.assertEqual(
            set(first_ids) & set(second_ids), set(),
            '两页之间出现了重复卡片，游标分页失效')
        # 第 2 页应该拿到最早的那条（id 最小，也就是「第一条」）
        self.assertEqual(len(second_ids), 1)
        self.assertEqual(second_ids[0], post_first_id)
        # 新发的那条不该出现在第 2 页（它比游标新，属于下一次刷新才会看到的内容）
        self.assertNotIn(
            LearningPost.objects.get(title='刚发的').id, second_ids)

    def test_invalid_cursor_falls_back_to_first_page(self):
        """非法 before 不报错，退回第一页。

        游标是 base64 串，用户完全可能手改或复制残缺。
        解析失败就当「没传游标」，比 500 友好。

        这里覆盖三种残缺形态：明文乱码、空串、能 base64 解码但内容不是
        ``时间|id`` 的串。最后一种最容易漏 —— 它过了第一层解码。
        """
        make_post(title='正常一条')

        for bad in ('abc', 'null-x', 'MTIzNDU', '%20', '-----'):
            with self.subTest(before=bad):
                resp = self.client.get(f'{LEARNING_URL}?before={bad}')
                self.assertEqual(resp.status_code, 200)
                self.assertEqual(len(resp.json()['daily']), 1,
                                 f'before={bad!r} 没有退回第一页')

    def test_bare_numeric_cursor_still_supported_for_debugging(self):
        """裸数字游标（老格式）仍然可用，方便 curl 手敲验证。

        生产前端只透传 ``next_cursor``，走不到这条分支。
        但调试时手输 ``?before=8`` 比手写一长串 base64 现实得多，
        所以留了这个兼容。负数/0 仍当非法。
        """
        for i in range(5):
            make_post(title=f'第{i}条')

        resp = self.client.get(f'{LEARNING_URL}?before=3')

        self.assertEqual(resp.status_code, 200)
        ids = [x['id'] for x in resp.json()['daily']]
        self.assertTrue(all(i < 3 for i in ids), f'裸数字游标没生效：{ids}')
        self.assertEqual(len(ids), 2)     # id 1、2

    def test_huge_bare_numeric_cursor_does_not_500(self):
        """裸数字游标超出 int64 时不能 500，按「全都要」处理。

        这条是 SQLite 抓出来的静默坑：SQLite 会把超范围整数转成浮点比较，
        ``id < 1e22`` 恒真，所以在 SQLite 上压根看不出问题；
        同一段代码换到 PostgreSQL 就是 ``DataError: bigint out of range``，
        首页 500。视图里夹到 ``2**63-1`` 后，两边的行为才一致。
        """
        make_post(title='正常一条')

        resp = self.client.get(f'{LEARNING_URL}?before=9999999999999999999999')

        self.assertEqual(resp.status_code, 200)
        body = resp.json()
        self.assertEqual(len(body['daily']), 1)
        self.assertFalse(body['has_more'])

    def test_page_size_is_clamped(self):
        """page_size 被夹在 [1, 20]，且非法值退回默认 6。

        上限是为了防止手写 ``?page_size=9999`` 把数据库拖死；
        下限 1 是为了防止 ``?page_size=0`` 让前端永远拿不到数据、
        无限触发「上拉加载」。
        """
        for i in range(30):
            make_post(title=f'第{i}条')

        self.assertEqual(
            len(self.client.get(f'{LEARNING_URL}?page_size=9999').json()['daily']), 20)
        self.assertEqual(
            len(self.client.get(f'{LEARNING_URL}?page_size=0').json()['daily']), 1)
        self.assertEqual(
            len(self.client.get(f'{LEARNING_URL}?page_size=abc').json()['daily']), 6)
        self.assertEqual(
            len(self.client.get(f'{LEARNING_URL}?page_size=-5').json()['daily']), 1)

    def test_soft_deleted_post_does_not_break_cursor(self):
        """软删除的记录被跳过，但不影响游标推进。

        坑：如果把过滤放在 Python 层（先切片再 filter），
        删掉一条会让这一页只剩 5 条、has_more 判断失准；
        SQL 层过滤则天然正确。这条测试锁住「过滤必须在数据库里做」。
        """
        posts = [make_post(title=f'第{i}条') for i in range(8)]
        posts[1].is_delete = True
        posts[1].save(update_fields=['is_delete'])

        page1 = self.client.get(f'{LEARNING_URL}?page_size=4').json()
        page2 = self.client.get(
            f'{LEARNING_URL}?page_size=4&before={page1["next_cursor"]}').json()

        ids = [x['id'] for x in page1['daily']] + [x['id'] for x in page2['daily']]
        self.assertEqual(len(ids), 7)         # 8 条删 1 条
        self.assertEqual(len(ids), len(set(ids)))   # 无重复
        self.assertNotIn(posts[1].id, ids)    # 删掉的不出现


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
