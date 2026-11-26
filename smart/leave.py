"""请假：把「今天孩子不来了」变成一条可查、可撤、能抑制提醒的记录。

为什么单独一个模块
------------------
和 ``checkin.py`` 同理：业务规则不放 view、不放 Admin。
将来老师端小程序要能代填请假，直接调这里的函数即可。

三条口径（2026-11-26 用户确认）
-------------------------------
1. **提交即生效，不走审批。**
   账务上没有风险 —— 本项目「签到才扣课时」，请假、旷课都不扣。
   反而「家长请了假、老师没审、最后算旷课」才是真正会扯皮的情况。

2. **开课后不能请。**
   用开课时间当界线，不是「提前 N 小时」。理由：课都开始了还算请假，
   就等于旷课也能事后补成请假，两者的区别就彻底没了。
   「临时请假扣课时」也被否决 —— 没有补课功能时，扣了就是白花钱。

3. **撤销改状态，不删行。**
   删掉的话「这周三请过假」这个事实就消失了，名单对不上时无从解释。
   唯一约束带 ``status='active'`` 条件，撤销后放行重新提交。

与提醒的关系
------------
请了假的人不该收到「别忘了送孩子」。抑制逻辑在
``reminder.collect_recipients()`` 里，通过 ``has_active_leave()`` 判断。
**撤销请假后提醒要能恢复** —— 所以抑制是查询出来的，
不是像 ``NotifyLog`` 那样把状态固化下来。
"""

from __future__ import annotations

import datetime

from django.db import IntegrityError, transaction
from django.utils import timezone

from .models import ClassSession, LeaveRequest, ParentStudent, Student


class LeaveError(Exception):
    """请假失败。``message`` 可以直接展示给家长看。"""

    def __init__(self, message: str):
        self.message = message
        super().__init__(message)


def lesson_start(session: ClassSession, lesson_date: datetime.date) -> datetime.datetime:
    """这节课的**开始时刻**（带时区）。

    课表只存时分，日期来自请假记录，两者拼起来才是真正的时刻。
    返回 aware datetime 是为了能和 ``timezone.now()`` 直接比较 ——
    ``USE_TZ=True`` 下拿 naive 去比会抛
    ``RuntimeWarning: DateTimeField received a naive datetime``。
    """
    start = datetime.datetime.combine(lesson_date, session.start_time)
    if timezone.is_naive(start):
        start = timezone.make_aware(start)
    return start


def has_active_leave(student_id: int, session_id: int,
                     lesson_date: datetime.date) -> bool:
    """这节课这个孩子请过假了吗（且没撤销）。

    给提醒任务用。只认 ``status='active'``：
    撤销过的假不该继续抑制提醒，否则家长撤了假还是收不到催课通知，
    表现就是「我明明撤销了，怎么还是不提醒我」。
    """
    return LeaveRequest.objects.filter(
        student_id=student_id,
        session_id=session_id,
        lesson_date=lesson_date,
        status='active',
    ).exists()


def _check_owner(parent, student_id: int) -> Student:
    """确认这个孩子是当前家长名下的，并返回它。

    越权防线。漏了这一步，家长 A 传家长 B 的 student_id 就能给别人请假，
    而请求本身完全合法、日志里看不出异常。

    只认 ``status='active'`` 的孩子：已退班的孩子不该还能请假。
    """
    link = (
        ParentStudent.objects
        .filter(parent=parent, student_id=student_id, student__status='active')
        .select_related('student')
        .first()
    )
    if not link:
        raise LeaveError('找不到这个孩子，可能已退班或不是你的孩子')
    return link.student


def submit(parent, student_id: int, session_id: int,
           lesson_date: datetime.date, reason: str = '',
           now: datetime.datetime | None = None) -> LeaveRequest:
    """提交请假。

    Args:
        parent: 当前登录家长。
        student_id: 请假的孩子。
        session_id: 哪个时段的课。
        lesson_date: 哪一天。**必填** —— 课表只存周几，
            不落到日期就不知道请的是哪一节。
        reason: 家长填的原因，可不填。
        now: 当前时刻，测试注入用。内部读 ``timezone.now()``
            的写法没法测「开课后不能请」这条边界。

    Returns:
        新建的 ``LeaveRequest``。

    Raises:
        LeaveError: 参数不合法、不是自己的孩子、已开课、或这节课已请过假。
    """
    if not session_id:
        raise LeaveError('请指定要请假的课')
    if not lesson_date:
        raise LeaveError('请指定请假日期')

    session = (
        ClassSession.objects
        .filter(id=session_id)
        .select_related('course_class')
        .first()
    )
    if not session:
        raise LeaveError('这节课不存在，可能已被调整')

    student = _check_owner(parent, student_id)

    # 孩子必须真的报了这个班 —— 否则是给「别人的班」请假，
    # 名单上永远对不上，老师也看不到。
    from .models import Enrollment
    if not Enrollment.objects.filter(
        student_id=student.id,
        course_class_id=session.course_class_id,
        status='active',
    ).exists():
        raise LeaveError(f'{student.name} 没有报这个班，无法请假')

    # 「开课后不能请」的时间线。
    #
    # 为什么用 > 而不是 >=：正好在开课那一秒提交，边界上算不算？
    # 算「还没开始」，允许。这样测试和实际行为一致（13:00:00.000 通过），
    # 而且不会出现「明明显示还有 0 秒却说已开课」的怪异体验。
    if now is None:
        now = timezone.now()
    start = lesson_start(session, lesson_date)
    if now > start:
        raise LeaveError(
            f'这节课 {session.start_time:%H:%M} 已经开始了，无法再请假。'
            '如需说明情况，请联系老师'
        )

    # 同一节课重复提交：**返回已有记录，不报错**。
    #
    # 为什么不是抛「已经请过假了」：家长网络卡顿时会连点两次，
    # 报错会让他以为第一次没成功，于是反复重试。
    # 幂等返回「请好了」对用户更友好，前端也不用特殊处理。
    exist = LeaveRequest.objects.filter(
        student_id=student.id,
        session_id=session.id,
        lesson_date=lesson_date,
        status='active',
    ).first()
    if exist:
        return exist

    try:
        # savepoint：撞唯一约束后事务会进入 aborted 状态，
        # 不套 atomic 的话后续任何 SQL 都报 TransactionManagementError。
        # 这个坑只在有外层事务时暴露，详见 reminder.claim() 的注释。
        with transaction.atomic():
            return LeaveRequest.objects.create(
                student=student,
                session=session,
                lesson_date=lesson_date,
                reason=(reason or '')[:128],
                status='active',
            )
    except IntegrityError:
        # 并发下两个请求同时进来：唯一约束拦住后一个。
        # 此时那条记录已经存在，读出来返回即可 —— 结果和幂等分支一致。
        exist = LeaveRequest.objects.filter(
            student_id=student.id,
            session_id=session.id,
            lesson_date=lesson_date,
            status='active',
        ).first()
        if exist:
            return exist
        raise LeaveError('请假提交失败，请重试')


def cancel(parent, leave_id: int,
           now: datetime.datetime | None = None) -> LeaveRequest:
    """撤销请假。

    撤销后这节课的提醒会恢复 —— 这是有意的：家长撤了假说明孩子要来，
    还在抑制提醒就等于「不催他，让他忘」。
    抑制靠 ``has_active_leave()`` 实时查询，不是固化的状态，所以自动生效。

    Raises:
        LeaveError: 记录不存在 / 不是自己的孩子 / 已撤销 / 已开课。
    """
    leave = (
        LeaveRequest.objects
        .filter(id=leave_id)
        .select_related('student', 'session', 'session__course_class')
        .first()
    )
    if not leave:
        raise LeaveError('这条请假记录不存在')

    _check_owner(parent, leave.student_id)

    if leave.status != 'active':
        raise LeaveError('这条请假已经撤销过了')

    # 已开课不允许撤销：撤销会让系统重新开始催家长，
    # 而课已经上完了，催了也没用，只会让家长困惑。
    if now is None:
        now = timezone.now()
    if now > lesson_start(leave.session, leave.lesson_date):
        raise LeaveError('这节课已经开始了，无法撤销请假')

    leave.status = 'cancelled'
    leave.cancel_time = now
    leave.save(update_fields=['status', 'cancel_time'])
    return leave


def my_leaves(parent, limit: int = 50) -> list[LeaveRequest]:
    """当前家长名下孩子的请假记录，最近的在前。

    包括已撤销的 —— 家长点进去要能看到「我上周撤过一次」，
    否则会反复怀疑自己没提交成功。
    """
    student_ids = list(
        ParentStudent.objects
        .filter(parent=parent, student__status='active')
        .values_list('student_id', flat=True)
    )
    if not student_ids:
        return []
    return list(
        LeaveRequest.objects
        .filter(student_id__in=student_ids)
        .select_related('student', 'session', 'session__course_class')
        .order_by('-lesson_date', '-create_time')[:limit]
    )
