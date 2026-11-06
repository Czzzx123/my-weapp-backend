"""视图层的异常分支测试。

运行方式：

    ./.venv/Scripts/python.exe manage.py test smart.tests_views -v 2

为什么单独开一个文件
--------------------
``tests.py`` 测的是 ``libs.wechat.code2session`` 本身「怎么报错」，
这里测的是 ``views.login`` 拿到异常后「怎么回应用户」。两者关注点不同：
底层可以把异常抛得很干净，上层照样可能把它吞错。

**本文件守的是两条底线**：

1. 网络故障 → 给用户「网络不太稳定，请重试」（可重试）
2. 编程错误 → **不能被当成网络故障**，必须冒泡成 500

第 2 条是批次 B 修掉的一个真实隐患：原来 ``except Exception`` 把
``KeyError`` / ``AttributeError`` 一起吞成「网络不稳定」，线上出 bug
时用户看到的是网络提示，日志里连堆栈都没有，永远查不到根因。
"""

from unittest.mock import patch

import tempfile

import requests
from django.test import TestCase, override_settings
from rest_framework.test import APIClient

from libs.wechat import WeChatConfigError, WeChatError
from smart import views
from smart.models import Parent

# 测试期间把 MEDIA_ROOT 指到临时目录，避免污染真实 media/
TEMP_MEDIA_ROOT = tempfile.mkdtemp(prefix='weilan-test-views-')

# 登录接口路径（见 smart/urls.py，前缀 smart/ 挂载于 smart_backend/urls.py）
LOGIN_URL = '/smart/user/login/'
PROFILE_URL = '/smart/user/update_profile/'


@override_settings(
    WECHAT_APPID='wx_test_appid',
    WECHAT_APPSECRET='test_secret_32chars',
    # 关掉开发期宽松模式，避免验证码相关逻辑干扰
    SMS_DEV_FIXED_CODE=False,
    SMS_DEV_RELAX=False,
)
class LoginViewExceptionTests(TestCase):
    """微信登录分支的异常分类。"""

    def setUp(self):
        # Admin 页面不在 ALLOWED_HOSTS 内，统一指定 Host 避免 400
        self.client = APIClient(HTTP_HOST='127.0.0.1')

    def _login(self, wx_code='fake_code'):
        return self.client.post(LOGIN_URL, {'wx_code': wx_code}, format='json')

    # ---------- 正常路径（对照组，证明 mock 生效） ----------

    @patch('smart.views.code2session')
    def test_wechat_login_success(self, mock_c2s):
        mock_c2s.return_value = {'openid': 'o_HAPPY', 'session_key': 'sk', 'unionid': None}

        resp = self._login()

        self.assertEqual(resp.status_code, 200)
        body = resp.json()
        self.assertEqual(body['code'], 100)
        self.assertTrue(body['token'])
        # 新用户没手机号 → 前端据此弹「补手机号」引导
        self.assertEqual(body['parent']['mobile'], '')
        self.assertEqual(body['parent']['has_custom_profile'], False)
        self.assertEqual(body['children'], [])
        self.assertTrue(Parent.objects.filter(openid='o_HAPPY').exists())

    # ---------- 网络故障：可重试 ----------

    @patch('smart.views.code2session')
    def test_timeout_returns_retryable_message(self, mock_c2s):
        """超时属于网络故障，提示用户重试，不是「凭证失效」。"""
        mock_c2s.side_effect = requests.Timeout('timed out')

        resp = self._login()

        # 关键：HTTP 200 + 业务 code 400，前端按业务码处理
        self.assertEqual(resp.status_code, 200)
        body = resp.json()
        self.assertEqual(body['code'], 400)
        self.assertIn('网络', body['msg'])
        self.assertNotIn('token', body)

    @patch('smart.views.code2session')
    def test_connection_error_returns_retryable_message(self, mock_c2s):
        """连接失败同样是网络层，走同一个分支。"""
        mock_c2s.side_effect = requests.ConnectionError('dns failed')

        body = self._login().json()

        self.assertEqual(body['code'], 400)
        self.assertIn('网络', body['msg'])

    # ---------- 业务失败：换 code 重进 ----------

    @patch('smart.views.code2session')
    def test_wechat_business_error_returns_chinese_hint(self, mock_c2s):
        mock_c2s.side_effect = WeChatError(40029, 'invalid code')

        body = self._login().json()

        self.assertEqual(body['code'], 400)
        self.assertIn('登录凭证已失效', body['msg'])

    @patch('smart.views.code2session')
    def test_wechat_config_error_returns_500_without_leaking_details(self, mock_c2s):
        """配置错误是部署问题，不能把「AppSecret 没配」告诉用户。"""
        mock_c2s.side_effect = WeChatConfigError('secret missing')

        body = self._login().json()

        self.assertEqual(body['code'], 500)
        self.assertIn('服务暂时不可用', body['msg'])
        # 不得泄露内部配置细节
        self.assertNotIn('secret', body['msg'])
        self.assertNotIn('AppID', body['msg'])

    # ---------- 【本文件核心】编程错误必须冒泡，不能被吞 ----------

    @patch('smart.views.code2session')
    def test_keyerror_is_not_swallowed_as_network_error(self, mock_c2s):
        """KeyError 是编程错误，必须冒泡成 500 并留下堆栈。

        这条是批次 B 的回归测试。若有人把 except 改回 ``except Exception``，
        这里会失败，从而挡住那次提交。
        """
        mock_c2s.side_effect = KeyError('openid')

        # assertLogs 顺带断言「错误被记进日志了」—— 这恰恰是原来
        # except Exception 丢掉的东西（日志里只有一句「网络不稳定」）
        with self.assertLogs('django.request', level='ERROR'):
            with self.assertRaises(KeyError):
                self._login()

    @patch('smart.views.code2session')
    def test_attribute_error_is_not_swallowed(self, mock_c2s):
        """同上，AttributeError 也不该变成「网络不稳定」。"""
        mock_c2s.side_effect = AttributeError('no such attr')

        with self.assertLogs('django.request', level='ERROR'):
            with self.assertRaises(AttributeError):
                self._login()

    @patch('smart.views.code2session')
    def test_missing_openid_key_in_session_bubbles_up(self, mock_c2s):
        """code2session 返回了但缺 openid —— 是上游契约被破坏，必须暴露。"""
        mock_c2s.return_value = {'session_key': 'sk'}

        with self.assertLogs('django.request', level='ERROR'):
            with self.assertRaises(KeyError):
                self._login()

    # ---------- 失败不该留下脏数据 ----------

    @patch('smart.views.code2session')
    def test_network_failure_creates_no_parent(self, mock_c2s):
        mock_c2s.side_effect = requests.Timeout('timed out')

        before = Parent.objects.count()
        self._login()
        self.assertEqual(Parent.objects.count(), before)


@override_settings(MEDIA_ROOT=TEMP_MEDIA_ROOT)
class ContentLengthParsingTests(TestCase):
    """``_declared_content_length`` 的边界值。

    单独测这个纯函数而不是打接口，是因为**非法 CONTENT_LENGTH 根本到不了视图**：
    Django 的 multipart 解析器会先读它，读到非法值直接抛
    ``MultiPartParserError: Invalid Content-Length header``，返回 400。
    也就是说视图里那段 try/except 是为「解析器没拦住」的路径兜底的，
    端到端没法稳定复现，只能测函数本身。
    """

    def test_normal_value(self):
        class Req:
            META = {'CONTENT_LENGTH': '1024'}
        self.assertEqual(views._declared_content_length(Req), 1024)

    def test_missing_header_returns_zero(self):
        """chunked 上传不带这个头。"""
        class Req:
            META = {}
        self.assertEqual(views._declared_content_length(Req), 0)

    def test_empty_string_returns_zero(self):
        class Req:
            META = {'CONTENT_LENGTH': ''}
        self.assertEqual(views._declared_content_length(Req), 0)

    def test_non_numeric_returns_zero_not_raise(self):
        """传 'abc' 不能 500 —— 这是客户端可控的头。"""
        class Req:
            META = {'CONTENT_LENGTH': 'abc'}
        self.assertEqual(views._declared_content_length(Req), 0)

    def test_whitespace_returns_zero(self):
        class Req:
            META = {'CONTENT_LENGTH': '   '}
        self.assertEqual(views._declared_content_length(Req), 0)

    def test_negative_returns_value(self):
        """负数 int() 能过，值本身没意义，只要不抛。"""
        class Req:
            META = {'CONTENT_LENGTH': '-1'}
        self.assertEqual(views._declared_content_length(Req), -1)

    def test_float_string_returns_zero(self):
        class Req:
            META = {'CONTENT_LENGTH': '1e999'}
        self.assertEqual(views._declared_content_length(Req), 0)

    def test_none_returns_zero(self):
        class Req:
            META = {'CONTENT_LENGTH': None}
        self.assertEqual(views._declared_content_length(Req), 0)


@override_settings(MEDIA_ROOT=TEMP_MEDIA_ROOT)
class OversizedUploadTests(TestCase):
    """前置大小检查真的会在 serializer 之前拦住大文件。"""

    def setUp(self):
        self.parent = Parent.objects.create(openid='o_CL_TEST')
        from rest_framework_simplejwt.tokens import RefreshToken
        self.token = str(RefreshToken.for_user(self.parent).access_token)
        self.client = APIClient(HTTP_HOST='127.0.0.1')

    def test_oversized_content_length_rejected_before_parsing(self):
        """声明 10MB 时直接返回友好提示，不进 serializer。

        这里只声明头、不带真实文件体：如果前置检查没生效，
        请求会因为缺少 multipart body 报别的错，测试就会失败 ——
        正好能证明拦截发生在解析之前。
        """
        resp = self.client.generic(
            'POST', PROFILE_URL, data=b'',
            content_type='multipart/form-data; boundary=x',
            CONTENT_LENGTH=str(10 * 1024 * 1024),
            HTTP_TOKEN=self.token,
        )

        self.assertEqual(resp.status_code, 200)
        body = resp.json()
        self.assertEqual(body['code'], 400)
        self.assertIn('图片太大了', body['msg'])
