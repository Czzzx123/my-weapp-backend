"""头像图片处理。

为什么不直接把用户上传的文件存起来：
1. 微信头像原图动辄几百 KB，直接存会拖慢「我的」页面加载
2. 客户端能绕过前端限制，服务端必须自己校验真伪
3. 统一转成正方形 WebP，避免各种奇怪比例的头像撑破 UI

与 Banner 那次优化的思路一致（9.4MB → 84KB），只是这里是逐张处理。
"""

from __future__ import annotations

import io
import logging
import os

from django.core.files.base import ContentFile
from PIL import Image, UnidentifiedImageError

logger = logging.getLogger(__name__)

# 头像最终边长（像素）。88rpx 的圆形头像在 3x 屏上约 132px，
# 取 200 留余量，同时兼容将来放大展示（如个人资料页）。
AVATAR_SIZE = 200

# 上传文件的字节上限。微信头像原图通常 100-500KB，2MB 已经很宽松。
MAX_UPLOAD_BYTES = 2 * 1024 * 1024

# 只接受这几种格式。Pillow 能打开的不等于业务要收的 ——
# 比如 TIFF、BMP 能开但没必要支持，反而增加攻击面。
ALLOWED_FORMATS = {'JPEG', 'PNG', 'WEBP', 'GIF'}


class AvatarError(Exception):
    """头像处理失败，message 可直接展示给用户。"""


def process_avatar(uploaded_file) -> ContentFile:
    """校验并处理上传的头像，返回可直接存盘的 WebP。

    处理步骤：
      1. 大小检查（超 2MB 直接拒，不做"压缩到能过"的妥协）
      2. 用 Pillow 真实解码，防止把 .php 改名成 .jpg 上传
      3. 居中裁成正方形（用户头像常见非正方形，拉伸会变形）
      4. 缩放到 AVATAR_SIZE
      5. 转 RGBA → WebP（带透明通道的头像不能丢透明）

    Args:
        uploaded_file: ``InMemoryUploadedFile`` 或 ``TemporaryUploadedFile``

    Returns:
        ``ContentFile``，文件名由调用方决定

    Raises:
        AvatarError: 任何校验或处理失败，message 是给用户看的中文
    """
    size = getattr(uploaded_file, 'size', 0)
    if size > MAX_UPLOAD_BYTES:
        raise AvatarError(
            f'图片太大了（{size / 1024 / 1024:.1f}MB），请选 2MB 以内的'
        )

    if size == 0:
        raise AvatarError('没有收到图片文件')

    try:
        # 必须 seek(0)，否则读出来是空的
        uploaded_file.seek(0)
        img = Image.open(uploaded_file)
        # verify() 会消耗文件指针，之后必须重新打开才能用
        img.verify()
    except (UnidentifiedImageError, OSError, ValueError):
        raise AvatarError('这个文件不是有效的图片')

    if img.format not in ALLOWED_FORMATS:
        raise AvatarError('只支持 JPG / PNG / WebP / GIF 格式的图片')

    # verify 之后原对象不可再用，重新打开
    uploaded_file.seek(0)
    try:
        img = Image.open(uploaded_file)
    except (UnidentifiedImageError, OSError):
        raise AvatarError('图片已损坏，请换一张')

    # 居中裁成正方形。用 thumbnail 之外的方式是为了精确控制输出尺寸。
    img = _crop_square(img)
    # 大图缩小时用 LANCZOS 质量最好，代价在这种尺寸下可忽略
    img = img.resize((AVATAR_SIZE, AVATAR_SIZE), Image.LANCZOS)

    # 带透明的转 RGBA 保透明；不带透明的转 RGB，避免 WebP 体积翻倍
    if img.mode in ('RGBA', 'LA') or (img.mode == 'P' and 'transparency' in img.info):
        img = img.convert('RGBA')
    else:
        img = img.convert('RGB')

    buf = io.BytesIO()
    # quality 82 是肉眼几乎无损的临界点，再往上体积增长快收益小
    img.save(buf, format='WEBP', quality=82, method=4)
    buf.seek(0)

    logger.info('头像处理完成：原始 %sB → WebP %sB', size, buf.getbuffer().nbytes)
    return ContentFile(buf.read())


def _crop_square(img: Image.Image) -> Image.Image:
    """从图片中心裁出最大的正方形。

    不用拉伸（resize 到正方形）是因为头像被压扁/拉长很难看，
    微信、QQ 这类产品都是居中裁剪。
    """
    width, height = img.size
    if width == height:
        return img

    side = min(width, height)
    left = (width - side) // 2
    top = (height - side) // 2
    return img.crop((left, top, left + side, top + side))


def delete_avatar_file_by_path(path: str) -> None:
    """按**物理路径字符串**删除旧头像。

    为什么不用 FieldFile：
    FieldFile 是绑定在模型实例上的活对象，``parent.avatar.save(new)`` 之后
    它的 ``name`` 会跟着变成新文件名。传对象进来再读 ``.name``，删掉的
    其实是刚存好的新文件，表现就是「头像上传成功但立刻变空」。
    **必须在覆盖之前先把 path 取成字符串。**

    静默失败：删不掉旧文件不该让本次请求失败，最坏结果只是多占点磁盘。
    """
    if not path or not os.path.exists(path):
        return

    try:
        os.remove(path)
        logger.info('已删除旧头像文件：%s', path)
    except OSError as exc:
        logger.warning('删除旧头像失败（不影响本次请求）：%s %s', path, exc)
