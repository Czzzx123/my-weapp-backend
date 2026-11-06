"""登录链路的测试。

运行方式：

    ./.venv/Scripts/python.exe manage.py test smart.tests -v 2

**测试不会真的请求微信服务器**，全部用 mock 替换 ``requests.get``。
原因：真实 code 只能用一次且 5 分钟过期，无法稳定复现；
把外部依赖挡在测试外，才能保证每次跑结果一致。
"""

from unittest.mock import patch

import requests
from django.test import TestCase, override_settings

from libs import wechat


class FakeResponse:
    """模拟 requests.Response，只实现 code2session 用到的部分。"""

    def __init__(self, payload=None, text='{}', status_code=200, json_error=False):
        self._payload = payload or {}
        self.text = text
        self.status_code = status_code
        self._json_error = json_error

    def raise_for_status(self):
        if self.status_code >= 400:
            raise requests.HTTPError(f'{self.status_code} Error')

    def json(self):
        if self._json_error:
            raise ValueError('not json')
        return self._payload


@override_settings(WECHAT_APPID='wx_test_appid', WECHAT_APPSECRET='test_secret_32chars')
class Code2SessionTest(TestCase):
    """libs.wechat.code2session 的行为约定。"""

    @patch('libs.wechat.requests.get')
    def test_success_returns_openid(self, mock_get):
        mock_get.return_value = FakeResponse({
            'openid': 'oABC1234567890',
            'session_key': 'sk_xxx',
            'unionid': 'uXYZ',
        })

        result = wechat.code2session('valid_code')

        self.assertEqual(result['openid'], 'oABC1234567890')
        self.assertEqual(result['session_key'], 'sk_xxx')
        self.assertEqual(result['unionid'], 'uXYZ')

    @patch('libs.wechat.requests.get')
    def test_unionid_absent_is_none(self, mock_get):
        """个人主体小程序拿不到 unionid，不应报错。"""
        mock_get.return_value = FakeResponse({'openid': 'oABC', 'session_key': 'sk'})

        result = wechat.code2session('code')

        self.assertIsNone(result['unionid'])

    @patch('libs.wechat.requests.get')
    def test_request_params_are_correct(self, mock_get):
        mock_get.return_value = FakeResponse({'openid': 'oABC'})

        wechat.code2session('the_code')

        _, kwargs = mock_get.call_args
        params = kwargs['params']
        self.assertEqual(params['appid'], 'wx_test_appid')
        self.assertEqual(params['secret'], 'test_secret_32chars')
        self.assertEqual(params['js_code'], 'the_code')
        self.assertEqual(params['grant_type'], 'authorization_code')
        # 必须有超时，否则微信慢响应会拖死接口
        self.assertIn('timeout', kwargs)

    @patch('libs.wechat.requests.get')
    def test_invalid_code_raises_with_chinese_hint(self, mock_get):
        mock_get.return_value = FakeResponse({'errcode': 40029, 'errmsg': 'invalid code'})

        with self.assertRaises(wechat.WeChatError) as ctx:
            wechat.code2session('bad_code')

        self.assertEqual(ctx.exception.errcode, 40029)
        # 给用户看的是中文，不是微信的英文原文
        self.assertIn('登录凭证已失效', ctx.exception.message)

    @patch('libs.wechat.requests.get')
    def test_errcode_as_string_is_handled(self, mock_get):
        """微信偶尔把 errcode 返回成字符串。"""
        mock_get.return_value = FakeResponse({'errcode': '45011', 'errmsg': 'freq limit'})

        with self.assertRaises(wechat.WeChatError) as ctx:
            wechat.code2session('code')

        self.assertEqual(ctx.exception.errcode, 45011)
        self.assertIn('太频繁', ctx.exception.message)

    @patch('libs.wechat.requests.get')
    def test_unknown_errcode_falls_back(self, mock_get):
        mock_get.return_value = FakeResponse({'errcode': 99999, 'errmsg': 'unknown'})

        with self.assertRaises(wechat.WeChatError) as ctx:
            wechat.code2session('code')

        self.assertIn('99999', ctx.exception.message)

    @patch('libs.wechat.requests.get')
    def test_missing_openid_raises(self, mock_get):
        """HTTP 200 但没有 openid，不能当成登录成功。"""
        mock_get.return_value = FakeResponse({'session_key': 'sk'})

        with self.assertRaises(wechat.WeChatError):
            wechat.code2session('code')

    @patch('libs.wechat.requests.get')
    def test_non_json_response_raises(self, mock_get):
        """微信出错时可能返回 text/plain，不能无条件 .json()。"""
        mock_get.return_value = FakeResponse(text='plain error', json_error=True)

        with self.assertRaises(wechat.WeChatError):
            wechat.code2session('code')

    @patch('libs.wechat.requests.get')
    def test_timeout_propagates(self, mock_get):
        """网络故障要让调用方看见，才能决定是否重试。"""
        mock_get.side_effect = requests.Timeout('timed out')

        with self.assertRaises(requests.RequestException):
            wechat.code2session('code')

    @patch('libs.wechat.requests.get')
    def test_http_500_propagates(self, mock_get):
        mock_get.return_value = FakeResponse(status_code=500)

        with self.assertRaises(requests.HTTPError):
            wechat.code2session('code')

    def test_empty_code_raises_without_http_call(self):
        with patch('libs.wechat.requests.get') as mock_get:
            with self.assertRaises(wechat.WeChatError):
                wechat.code2session('')
            mock_get.assert_not_called()

    @override_settings(WECHAT_APPID='', WECHAT_APPSECRET='')
    def test_missing_config_raises(self):
        with patch('libs.wechat.requests.get') as mock_get:
            with self.assertRaises(wechat.WeChatConfigError):
                wechat.code2session('code')
            mock_get.assert_not_called()

    @patch('libs.wechat.requests.get')
    def test_secret_not_leaked_in_logs(self, mock_get):
        """密钥绝不能进日志。"""
        mock_get.return_value = FakeResponse({'errcode': 40029, 'errmsg': 'invalid'})

        with self.assertLogs('libs.wechat', level='INFO') as captured:
            with self.assertRaises(wechat.WeChatError):
                wechat.code2session('code')

        joined = '\n'.join(captured.output)
        self.assertNotIn('test_secret_32chars', joined)
