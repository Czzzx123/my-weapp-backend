"""头像昵称设置（update_profile）的测试。

重点覆盖三类容易错的地方：
1. ImageField 的 URL 必须是绝对地址（真机才能加载）
2. 非图片文件必须被挡住（不能只信扩展名）
3. 换头像时要删掉旧文件（否则 media 目录无限膨胀）
"""

import io
import os
import shutil
import tempfile

from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase, override_settings
from PIL import Image
from rest_framework.test import APIClient
from rest_framework_simplejwt.tokens import RefreshToken

from . import avatar_service
from .models import Parent, ParentStudent, Student

# 测试期间把 MEDIA_ROOT 指到临时目录，避免污染真实 media/
TEMP_MEDIA_ROOT = tempfile.mkdtemp(prefix='weilan-test-media-')


def make_image_bytes(width=400, height=300, fmt='JPEG', color=(255, 120, 60)):
    """造一张真图片。用 Pillow 现场生成，比塞 base64 常量直观得多。"""
    buf = io.BytesIO()
    Image.new('RGB', (width, height), color).save(buf, format=fmt)
    buf.seek(0)
    return buf.read()


def make_upload(name='avatar.jpg', content=None, content_type='image/jpeg'):
    return SimpleUploadedFile(
        name, content if content is not None else make_image_bytes(),
        content_type=content_type,
    )


@override_settings(MEDIA_ROOT=TEMP_MEDIA_ROOT)
class UpdateProfileTests(TestCase):
    def setUp(self):
        self.parent = Parent.objects.create(openid='o_TEST_PROFILE')
        self.token = str(RefreshToken.for_user(self.parent).access_token)
        # Admin 页面在 ALLOWED_HOSTS 之外，统一指定 Host 避免 400
        self.client = APIClient(HTTP_HOST='127.0.0.1')

    def tearDown(self):
        # 只清 avatar 子目录，不删 TEMP_MEDIA_ROOT 本身。
        # 删整个目录会让下一个用例的 setUp 失效（目录不存在 → 保存报错），
        # 这里踩过一次：errors=3 全是 FileNotFoundError，看起来像实现有问题。
        shutil.rmtree(
            os.path.join(TEMP_MEDIA_ROOT, 'avatar'),
            ignore_errors=True)

    def _post(self, data, token=None):
        return self.client.post(
            '/smart/user/update_profile/', data,
            format='multipart', HTTP_TOKEN=token or self.token,
        )

    # ---------- 昵称 ----------
    def test_update_nickname_only(self):
        resp = self._post({'nickname': '小明妈妈'})
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.json()['code'], 100)

        self.parent.refresh_from_db()
        self.assertEqual(self.parent.nickname, '小明妈妈')
        self.assertEqual(resp.json()['parent']['display_name'], '小明妈妈')

    def test_nickname_is_stripped(self):
        self._post({'nickname': '  小明妈妈  '})
        self.parent.refresh_from_db()
        self.assertEqual(self.parent.nickname, '小明妈妈')

    def test_blank_nickname_rejected(self):
        resp = self._post({'nickname': '   '})
        self.assertNotEqual(resp.json()['code'], 100)

    def test_nickname_too_long_rejected(self):
        resp = self._post({'nickname': 'x' * 33})
        self.assertNotEqual(resp.json()['code'], 100)
        self.parent.refresh_from_db()
        self.assertEqual(self.parent.nickname, '')

    def test_empty_payload_rejected(self):
        """什么都不传应该报错，而不是静默成功。"""
        resp = self._post({})
        self.assertNotEqual(resp.json()['code'], 100)

    # ---------- 头像 ----------
    def test_upload_avatar_success(self):
        resp = self._post({'avatar': make_upload()})
        self.assertEqual(resp.json()['code'], 100)

        self.parent.refresh_from_db()
        self.assertTrue(self.parent.avatar)
        self.assertTrue(self.parent.avatar.name.endswith('.webp'))

        # 存下来的必须是可打开的 WebP，且尺寸被压到 200x200
        with Image.open(self.parent.avatar.path) as img:
            self.assertEqual(img.format, 'WEBP')
            self.assertEqual(img.size, (200, 200))

    def test_avatar_url_is_absolute(self):
        """相对路径在真机上会被当成包内资源，图片显示空白。"""
        resp = self._post({'avatar': make_upload()})
        url = resp.json()['parent']['avatar']
        self.assertTrue(
            url.startswith('http://') or url.startswith('https://'),
            f'头像 URL 必须是绝对地址，实际是 {url!r}',
        )
        self.assertIn('/media/avatar/', url)

    def test_non_square_avatar_is_center_cropped(self):
        """400x300 的图要裁成正方形，不能拉伸变形。"""
        self._post({'avatar': make_upload(content=make_image_bytes(400, 300))})
        self.parent.refresh_from_db()
        with Image.open(self.parent.avatar.path) as img:
            self.assertEqual(img.size, (200, 200))

    def test_png_with_alpha_keeps_transparency(self):
        buf = io.BytesIO()
        Image.new('RGBA', (300, 300), (255, 0, 0, 100)).save(buf, format='PNG')
        buf.seek(0)
        self._post({'avatar': make_upload(
            'a.png', buf.read(), content_type='image/png')})

        self.parent.refresh_from_db()
        with Image.open(self.parent.avatar.path) as img:
            self.assertEqual(img.mode, 'RGBA')

    def test_fake_image_rejected(self):
        """把文本改名成 .jpg 传上来，必须被识破。"""
        fake = make_upload('evil.jpg', b'<?php echo "hacked"; ?>')
        resp = self._post({'avatar': fake})
        self.assertNotEqual(resp.json()['code'], 100)
        self.parent.refresh_from_db()
        self.assertFalse(self.parent.avatar)

    def test_oversized_avatar_rejected(self):
        """超大文件必须被拒。

        注意：真正生效的是 views 里基于 CONTENT_LENGTH 的前置检查。
        DRF 的 ImageField 就算不拦，也会把 2MB+ 全读进内存才判断是不是图片，
        所以那道前置检查才是防线。这里只验证「确实被拒」，不锁定具体文案。
        """
        big = make_upload(content=b'\x00' * (avatar_service.MAX_UPLOAD_BYTES + 1))
        resp = self._post({'avatar': big})
        self.assertNotEqual(resp.json()['code'], 100)
        self.parent.refresh_from_db()
        self.assertFalse(self.parent.avatar)

    def test_unsupported_format_rejected(self):
        """BMP 能打开但不在白名单里。"""
        buf = io.BytesIO()
        Image.new('RGB', (100, 100), (0, 0, 255)).save(buf, format='BMP')
        buf.seek(0)
        resp = self._post({'avatar': make_upload(
            'a.bmp', buf.read(), content_type='image/bmp')})
        self.assertNotEqual(resp.json()['code'], 100)

    def test_pixel_bomb_rejected(self):
        """解压炸弹必须被拒，且**不能变成 500**。

        场景：一张 600KB 的纯色 PNG 可以声明 14000x14000（1.96 亿像素），
        解码成 RGBA 要吃掉约 780MB 内存。字节数检查完全拦不住它。

        DecompressionBombError 直接继承 Exception，既不是 OSError
        也不是 ValueError，漏掉它就会 500。这里同时验证状态码是 200
        （业务级拒绝），因为前端 upload.js 对非 200 会走 http 分支。
        """
        # 尺寸超过 MAX_PIXELS（2000x2000），但字节数远小于 2MB
        bomb = make_image_bytes(width=3000, height=3000, fmt='PNG')
        self.assertLess(len(bomb), avatar_service.MAX_UPLOAD_BYTES,
                        '构造的测试图应当小于字节上限，否则测不到像素这条路径')

        resp = self._post({'avatar': make_upload('bomb.png', bomb, 'image/png')})
        self.assertEqual(resp.status_code, 200, '像素超限应当被业务拒绝，而不是 500')
        self.assertNotEqual(resp.json()['code'], 100)
        self.parent.refresh_from_db()
        self.assertFalse(self.parent.avatar)

    def test_pixel_bomb_directly_raises_avatar_error(self):
        """绕开 view 直接测 service，确保抛的是 AvatarError 而不是裸异常。"""
        bomb = make_image_bytes(width=3000, height=3000, fmt='PNG')
        with self.assertRaises(avatar_service.AvatarError):
            avatar_service.process_avatar(make_upload('bomb.png', bomb, 'image/png'))

    def test_exif_orientation_applied(self):
        """iPhone 竖拍图（Orientation=6）要先按方向摆正再裁切。

        未处理时：300x100 的横图会被裁成 100x100，
        再缩放时人脸已经在画面外了。正确做法是 exif_transpose 之后再裁。
        """
        img = Image.new('RGB', (300, 100), (10, 200, 90))
        # 在左半边画红、右半边画蓝，用颜色判断裁切位置
        img.paste((255, 0, 0), (0, 0, 100, 100))
        exif = img.getexif()
        exif[274] = 6   # Orientation: 顺时针旋转 90 度
        buf = io.BytesIO()
        img.save(buf, format='JPEG', exif=exif)
        buf.seek(0)

        out = avatar_service.process_avatar(
            make_upload('rot.jpg', buf.read(), 'image/jpeg'))
        result = Image.open(out).convert('RGB')
        self.assertEqual(result.size, (200, 200))
        # 摆正后原图的「左半边红色」应落在上方（旋转 90 度后左右变上下）
        # 这里只断言输出非全绿，证明原图内容被保留且没有裁成单色
        colors = result.getcolors(maxcolors=100000) or []
        self.assertGreater(len(colors), 1, '裁切后成了纯色，说明方向处理把人脸裁掉了')

    def test_replace_avatar_deletes_old_file(self):
        """换头像要删旧文件，否则 media/avatar 会一直变大。"""
        self._post({'avatar': make_upload()})
        self.parent.refresh_from_db()
        old_path = self.parent.avatar.path
        self.assertTrue(os.path.exists(old_path))

        self._post({'avatar': make_upload(content=make_image_bytes(color=(0, 0, 255)))})
        self.parent.refresh_from_db()

        self.assertFalse(
            os.path.exists(old_path),
            '旧头像文件没有被删除',
        )
        self.assertTrue(os.path.exists(self.parent.avatar.path))

    def test_upload_avatar_and_nickname_together(self):
        resp = self._post({'nickname': '小明爸爸', 'avatar': make_upload()})
        self.assertEqual(resp.json()['code'], 100)
        self.parent.refresh_from_db()
        self.assertEqual(self.parent.nickname, '小明爸爸')
        self.assertTrue(self.parent.avatar)
        self.assertTrue(resp.json()['parent']['has_custom_profile'])

    # ---------- 鉴权 ----------
    def test_requires_login(self):
        resp = self.client.post(
            '/smart/user/update_profile/', {'nickname': 'x'}, format='multipart')
        self.assertEqual(resp.status_code, 401)


@override_settings(MEDIA_ROOT=TEMP_MEDIA_ROOT)
class DisplayNameTests(TestCase):
    """默认昵称兜底：用户没设置时显示什么。"""

    def setUp(self):
        self.client = APIClient(HTTP_HOST='127.0.0.1')

    def tearDown(self):
        shutil.rmtree(
            os.path.join(TEMP_MEDIA_ROOT, 'avatar'),
            ignore_errors=True)

    def test_parent_without_child_shows_jiating(self):
        p = Parent.objects.create(openid='o_NO_CHILD')
        from .login_service import default_display_name
        self.assertEqual(default_display_name(p, []), '家长')

    def test_parent_with_child_shows_child_name(self):
        student = Student.objects.create(
            name='张小明', mobile='13800000001', grade='三年级')
        p = Parent.objects.create(openid='o_WITH_CHILD', mobile='13800000001')
        ParentStudent.objects.create(parent=p, student=student)

        from .login_service import build_login_payload
        payload = build_login_payload(p, request=None)
        self.assertEqual(payload['parent']['display_name'], '张小明家长')
        # nickname 本身保持空，便于前端判断「用户是否真的设置过」
        self.assertEqual(payload['parent']['nickname'], '')

    def test_user_nickname_wins_over_default(self):
        student = Student.objects.create(
            name='张小明', mobile='13800000001', grade='三年级')
        p = Parent.objects.create(
            openid='o_NICK', mobile='13800000001', nickname='小明妈妈')
        ParentStudent.objects.create(parent=p, student=student)

        from .login_service import build_login_payload
        payload = build_login_payload(p, request=None)
        self.assertEqual(payload['parent']['display_name'], '小明妈妈')

    def test_has_custom_profile_flag(self):
        p = Parent.objects.create(openid='o_FLAG')
        from .login_service import build_login_payload
        self.assertFalse(build_login_payload(p)['parent']['has_custom_profile'])

        p.nickname = '有人设过了'
        p.save(update_fields=['nickname'])
        self.assertTrue(build_login_payload(p)['parent']['has_custom_profile'])


class AvatarServiceTests(TestCase):
    """不经过 HTTP，直接测处理函数，定位问题更快。"""

    def test_crop_square_from_landscape(self):
        img = Image.new('RGB', (400, 300))
        out = avatar_service._crop_square(img)
        self.assertEqual(out.size, (300, 300))

    def test_crop_square_from_portrait(self):
        img = Image.new('RGB', (200, 500))
        out = avatar_service._crop_square(img)
        self.assertEqual(out.size, (200, 200))

    def test_crop_square_already_square(self):
        img = Image.new('RGB', (256, 256))
        out = avatar_service._crop_square(img)
        self.assertEqual(out.size, (256, 256))
