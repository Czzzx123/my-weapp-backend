"""上课前提醒：找出「下一节课什么时候开始」，提前 N 小时通知家长。

为什么要单独一个模块
--------------------
课表里只存 ``weekday + start_time``，不存「2026-11-27 16:00 有一节课」。
这是刻意的（排课每周重复，落成具体日期就要每周批量生成一批记录），
但也意味着**没有任何一张表能直接查出「今天下午有谁的课」** ——
必须把「周几」换算成「今天的日期」才算得出来。

这段换算很容易写错方向：查「今天」要算的是
「今天星期几」对应的时段，不是「下一个 weekday 是多少」。
两者在周一早上查周三时结果一样，周五晚上查周三时差了一整周。
所以这里的函数都带日期参数（不读 ``timezone.now()``），
测试才好覆盖边界。

为什么不引 celery
-----------------
本项目的推送量是「每天几十条」级别，引一套 celery + broker
换来的是运维成本（进程守护、重启丢任务、监控），
而收益只是「不用配 cron」。所以用 ``manage.py`` 命令 + 系统 cron，
每 5 分钟跑一次，幂等由 ``NotifyLog`` 的唯一约束保证。

幂等是什么意思
--------------
同一个家长、同一个孩子、同一个时段、同一天，同一种通知只发一次。
任务跑一百遍也只发一条 —— 这条约束在数据库里，不在 Python 里，
所以并发跑两个实例也不会重复。
"""

from __future__ import annotations

import datetime
import logging

from django.db import IntegrityError, transaction
from django.utils import timezone

from .models import ClassSession, Enrollment, HourLog, NotifyLog

logger = logging.getLogger(__name__)


def _to_weekday(d: datetime.date) -> int:
    """把 date 转成本项目的 weekday 口径：1=周一 … 7=周日。

    ``date.weekday()`` 给的是 0=周一 … 6=周日，
    而 ``ClassSession.weekday`` 用的是 1~7，两者差 1，很容易漏。
    """
    return d.isoweekday()


def sessions_of_day(day: datetime.date) -> list[ClassSession]:
    """某一天有哪些时段要上课。

    排除两类：
      · ``status='suspended'``：长期停课的时段，本来就不上
      · 班级已结班（``CourseClass.status != 'active'``）：
        结班了还提醒家长去上课，比不提醒更糟 —— 家长白跑一趟

    「单次停课」目前没有对应的模型（停课是时段级的），
    将来加了临时停课表，要在派发前再过滤一次。
    """
    return list(
        ClassSession.objects
        .filter(
            weekday=_to_weekday(day),
            status='normal',
            course_class__status='active',
        )
        .select_related('course_class', 'course_class__course')
        .order_by('start_time')
    )


def due_sessions(now: datetime.datetime, lead_minutes: int,
                 window_minutes: int) -> list[tuple[ClassSession, datetime.date]]:
    """找出「该发提醒了」的时段。

    判定条件：``开课时间 - lead_minutes`` 落在
    ``(now - window_minutes, now]`` 这个区间里。

    为什么要一个窗口而不是「等于 now」
    ----------------------------------
    定时任务每 5 分钟跑一次，如果条件是「距开课正好还有 2 小时」，
    任务在 13:58 跑、开课在 15:58 时差 120 分钟不匹配，
    在 14:03 跑时已经差 115 分钟也不匹配 —— 永远命中不了。
    所以要给一个窗口，把「上一次跑到现在」这段时间内
    到期的全部捞出来。窗口取任务间隔的 2 倍，容忍跑得慢一点。

    为什么要 ``now`` 参数而不是内部读时间
    -------------------------------------
    测试要能指定「现在是几点」。内部读 ``timezone.now()`` 的写法
    只能靠 mock 测试，改起来容易误伤。

    Returns:
        ``[(session, lesson_date), ...]``，lesson_date 是这一节课的**实际日期**。
    """
    result = []
    # 提醒时刻落在 (now - window, now] 内，等价于开课时刻落在
    # (now - window + lead, now + lead] 内。跨天要一起看，所以查两天。
    for offset in (0, 1, -1):
        day = (now + datetime.timedelta(days=offset)).date()
        for s in sessions_of_day(day):
            start = datetime.datetime.combine(day, s.start_time)
            # 用带时区的时间比较：settings.USE_TZ=True 时 now 是 aware 的
            if timezone.is_naive(start):
                start = timezone.make_aware(start)
            remind_at = start - datetime.timedelta(minutes=lead_minutes)
            if now - datetime.timedelta(minutes=window_minutes) < remind_at <= now:
                result.append((s, day))
    # 同一天可能被 offset=0 和 ±1 各查一遍，去重
    seen = set()
    unique = []
    for s, day in result:
        key = (s.id, day)
        if key in seen:
            continue
        seen.add(key)
        unique.append((s, day))
    return unique


def _already_checked_in(student_id: int, session_id: int,
                        lesson_date: datetime.date) -> bool:
    """这节课这个孩子已经签到了吗。

    签过了还发「马上要上课了」，家长会以为系统坏了 ——
    老师明明已经点过名，怎么还在催。
    """
    return HourLog.objects.filter(
        enrollment__student_id=student_id,
        session_id=session_id,
        lesson_date=lesson_date,
        reason='consumed',
        is_revoked=False,
    ).exists()


def _remain(enrollment: Enrollment) -> int:
    from django.db.models import Sum
    total = enrollment.hour_logs.aggregate(s=Sum('delta'))['s'] or 0
    return enrollment.total_hours + total


def collect_recipients(session: ClassSession,
                       lesson_date: datetime.date) -> list[dict]:
    """一节课该通知哪些家长。

    返回的每一项带齐了发送需要的全部信息，调用方不必再查库：

        {'parent': Parent, 'student': Student, 'enrollment': Enrollment,
         'remain': int, 'skip_reason': str}

    ``skip_reason`` 非空表示这个人不该发（已签到 / 课时不足），
    但**照样要返回** —— 要留一条 ``skipped`` 记录，
    否则后台看不出「为什么这个家长没收到」，会以为是漏发了。
    """
    enrollments = (
        Enrollment.objects
        .filter(
            course_class_id=session.course_class_id,
            status='active',
        )
        .select_related('student')
        .prefetch_related('student__parent_students__parent')
    )

    rows = []
    for e in enrollments:
        remain = _remain(e)
        skip = ''
        if _already_checked_in(e.student_id, session.id, lesson_date):
            skip = '已签到，不再催'
        elif remain <= 0:
            # 课时为 0 还催家长来上课，来了也签不了到（会被拦），
            # 等于让人白跑。这条要先于发送判断。
            skip = f'课时不足（剩 {remain}）'

        # 一个孩子可能绑多个家长（爸妈都绑了），逐个发
        links = list(e.student.parent_students.all())
        if not links:
            rows.append({
                'parent': None, 'student': e.student, 'enrollment': e,
                'remain': remain, 'skip_reason': '未绑定家长',
            })
            continue

        for link in links:
            rows.append({
                'parent': link.parent, 'student': e.student, 'enrollment': e,
                'remain': remain, 'skip_reason': skip,
            })
    return rows


def claim(parent_id: int, student_id: int, session_id: int,
          lesson_date: datetime.date, notify_type: str) -> NotifyLog | None:
    """抢占一条通知名额。返回 None 表示已经发过了。

    这是整个幂等机制的入口：先插记录，插得进去才发。
    顺序反过来（先发再插）在任务重跑时就会重复发 ——
    而任务一定会重跑（cron 抖动、手动执行、部署重启）。

    为什么要 try/except IntegrityError 而不是先查后插：
    「查一下没有 → 发 → 插」在并发下会漏（两个进程同时查到没有）。
    唯一约束是原子的，用它判断最可靠。SQLite 与 MySQL 都支持。

    为什么里面还要套一层 ``transaction.atomic()``
    ---------------------------------------------
    **这一步不能省。** 在 PostgreSQL / SQLite 上，一条 INSERT 撞了约束
    之后，当前事务就进入 aborted 状态，后续任何 SQL 都会报
    ``TransactionManagementError: You can't execute queries until the end
    of the 'atomic' block``。套一层 atomic 等于开一个 savepoint，
    出错只回滚到这个点，外层事务照常可用。

    这个坑只在「有外层事务」时暴露 —— 而 ``manage.py`` 命令默认每条
    SQL 自动提交，所以本地手测完全正常，上线接进 atomic 的业务流程
    才会炸。测试里就是靠 TestCase（本身就是 atomic）抓出来的。
    """
    try:
        with transaction.atomic():
            return NotifyLog.objects.create(
                parent_id=parent_id,
                student_id=student_id,
                session_id=session_id,
                lesson_date=lesson_date,
                notify_type=notify_type,
                status='skipped',
                channel='',
            )
    except IntegrityError:
        return None


def mark(log: NotifyLog, status: str, channel: str = '',
         errcode: int | None = None, remark: str = '') -> None:
    """回填发送结果。

    单独一个函数是因为**发送过程可能抛异常**，
    调用方要在 finally 里把结果落下来，
    否则记录永远停在 ``skipped``，后台看起来像「没处理」。
    """
    log.status = status
    log.channel = channel
    log.errcode = errcode
    log.remark = remark[:128]
    log.save(update_fields=['status', 'channel', 'errcode', 'remark'])
