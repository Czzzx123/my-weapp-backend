"""把存量 naive 时间统一转成 UTC（配合 USE_TZ 从 False 改为 True）。

背景
----
`USE_TZ = False` 时 Django 存的是 naive datetime，写进去的是
**服务器本地时间**（本项目 `TIME_ZONE = 'Asia/Shanghai'`，即 UTC+8）。
实测确认：`Parent id=11` 建立于本地 15:08，数据库里存的就是 `15:08:42`。

改成 `USE_TZ = True` 后，Django 会把数据库里的值一律当作 **UTC** 解释。
不处理存量数据的话，之前存的「15:08 北京时间」会被读成「15:08 UTC」，
展示时再 +8 变成「23:08」—— 凭空多出 8 小时。

处理方式
--------
把所有 naive 的北京时间减去 8 小时，得到正确的 UTC 值。

为什么用 SQL 的 datetime 函数而不是 Python 对象
----------------------------------------------
Django 在 `USE_TZ=True` 时读 naive 存储值会自动 `make_aware(utc)`，
拿到的是「贴着 UTC 标签的北京时间」。这时再 `- timedelta(hours=8)`
虽然在数值上正确，但整个链路依赖 settings 的当前状态 ——
万一以后有人调整 `TIME_ZONE` 或迁移执行顺序，行为会静默改变。

直接用 SQLite 的 `strftime('%Y-%m-%d %H:%M:%f', col, '-8 hours')`
把「数值减 8 小时」这件事表达在最底层，与 Django 的时区解释完全无关，
可预测、可复现。`%f` 保留毫秒，避免 `datetime()` 丢精度。

反向迁移
--------
`migrations.RunPython.noop`（不可逆）。理由：写一个「加回 8 小时」的
反向操作看似对称，但反复来回迁移会重复偏移，数据彻底失真。
需要回滚就恢复 `db.sqlite3.bak-*-before-utc` 备份。
"""

from django.db import migrations

# 原 TIME_ZONE 相对 UTC 的偏移。Asia/Shanghai 全年固定 +8，无夏令时。
LOCAL_OFFSET_HOURS = 8

# (表名, 字段名)
# 只列 auto_now / auto_now_add / 手动赋值的时间字段。
TARGETS = [
    ('smart_banner', 'create_time'),
    ('smart_notice', 'create_time'),
    ('smart_parent', 'create_time'),
    ('smart_parent', 'last_login'),
    ('smart_student', 'create_time'),
    ('smart_parentstudent', 'bind_time'),
    ('smart_course', 'create_time'),
    ('smart_courseclass', 'create_time'),
    ('smart_enrollment', 'enroll_time'),
    ('smart_hourlog', 'create_time'),
]


def shift_backward(apps, schema_editor):
    """北京时间 → UTC：数值减 8 小时。"""
    connection = schema_editor.connection

    if connection.vendor != 'sqlite':
        # 本项目是 SQLite。真要换 PostgreSQL/MySQL 时，
        # 这里应改成对应的 `col - INTERVAL '8 hours'` 语法。
        raise RuntimeError(
            f'迁移 0014 目前只实现了 SQLite 版本，当前后端是 {connection.vendor}。'
            '换库时请先改写本迁移再执行。'
        )

    with connection.cursor() as cursor:
        for table, column in TARGETS:
            # 两个细节都踩过，写下来：
            #
            # 1. **不能用参数化**。`cursor.execute(sql, [hours])` 时 Django 的
            #    SQL 日志会用 `sql % params` 回填，而 strftime 的 '%Y-%m-%d'
            #    里也有 % 号，会被当成格式化占位符，报
            #    `TypeError: %d format: a real number is required, not str`。
            #    所以把小时数直接内联进 SQL（是本文件的常量，无注入风险）。
            #
            # 2. **f-string 里必须写单个 %，不是 %%**。`%%` 是 psycopg2 参数化
            #    语法的转义习惯，在 f-string 里会原样输出成两个字面的百分号，
            #    结果把所有时间字段写成字符串 '%Y-%m-%d %H:%M:%f'。
            #    （这个错误真的发生过，数据被写坏，从备份恢复才救回来。）
            sql = (
                f"UPDATE {table} "
                f"SET {column} = strftime('%Y-%m-%d %H:%M:%f', {column}, "
                f"'-{LOCAL_OFFSET_HOURS} hours') "
                f"WHERE {column} IS NOT NULL"
            )
            cursor.execute(sql)
            if cursor.rowcount:
                print(f'  0014: {table}.{column} 转换 {cursor.rowcount} 条')


class Migration(migrations.Migration):

    dependencies = [
        ('smart', '0013_alter_parent_openid'),
    ]

    operations = [
        migrations.RunPython(shift_backward, migrations.RunPython.noop),
    ]
