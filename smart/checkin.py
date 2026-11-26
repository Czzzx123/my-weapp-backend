"""签到消课：把「老师点名」变成课时流水。

为什么单独一个模块而不是写在 Admin 里
------------------------------------
一段业务逻辑塞进 Admin 的 view 里就卖不出去了 —— 小程序将来也要能签到
（教练在手机上点）、要能被测试直接调用。这里的函数只依赖 ORM，
不依赖 request，所以 Admin、API、测试三方都能用。

三条不能破的规则
----------------
1. **课时不足整批拒绝，不写一半**。
   「先上课后补费」听起来合理，但账会滚乱且无法追溯：到底是他真上了
   十节课，还是老师重复签了十次？所以宁可拦住，让老师去报名页充值。

2. **重复签到由数据库拦，不靠视图 if**。
   老师手滑连点两次时，两个请求可能同时通过 Python 层的检查。
   唯一约束 `uniq_consumed_per_lesson` 才是真正的防线；
   这里的检查只为给出好读的错误文案。

3. **撤销 = 打标 + 补一笔冲正，绝不删行**。
   删了就看不出「曾经扣过、又被冲回」这个事实，对账时无从解释。
   打标同时让唯一约束放行，签错后可以重新签到。
"""

from __future__ import annotations

import datetime
from decimal import Decimal

from django.db import IntegrityError, transaction
from django.db.models import Sum
from django.utils import timezone

from .models import ClassSession, Enrollment, HourLog, LeaveRequest


class CheckinError(Exception):
    """签到失败。``message`` 直接可以展示给老师看。"""

    def __init__(self, message: str, detail: list | None = None):
        self.message = message
        # detail 里装逐人失败原因，前端可列表展示
        self.detail = detail or []
        super().__init__(message)


def _remain(enrollment: Enrollment) -> int:
    """报名当前剩余课时。

    必须实时汇总，不能读缓存列 —— 这是全项目的账本约定。
    """
    total = enrollment.hour_logs.aggregate(s=Sum('delta'))['s'] or 0
    return enrollment.total_hours + total


def checkin(class_id: int, lesson_date: datetime.date, session_id: int,
            enrollment_ids: list[int], operator: str = '') -> dict:
    """给一批报名签到，每人扣 1 课时并写一条 consumed 流水。

    Args:
        class_id: 班级 id，用于校验这些报名确实属于该班（防止前端传错）。
        lesson_date: 上课日期，写进 ``HourLog.lesson_date``，也是防重复的键之一。
        session_id: 上课时段 id。一节课必须指明是哪个时段，
            否则约束里的 session 为空，NULL 不等于 NULL，防重复就失效了。
        enrollment_ids: 要签到的报名 id 列表。
        operator: 操作人，写进流水备注，出问题时能追到人。

    Returns:
        ``{'created': [...], 'skipped': [...]}``
        created 是新写入的流水；skipped 是因「这节课已签过」而跳过的人。

    Raises:
        CheckinError: 参数不合法、课时不足、或有任何一条不能签。
            抛错前事务已回滚，不会留下半批数据。
    """
    if not enrollment_ids:
        raise CheckinError('请至少选择一个学生')

    if not session_id:
        # 不指明时段就无法防重复：session 为空时唯一约束不生效。
        raise CheckinError('请选择上课时段，否则无法防止重复签到')

    if not lesson_date:
        raise CheckinError('请选择上课日期')

    # 校验时段属于该班。前端两个下拉是独立的，选错组合就会把 A 班的课
    # 记到 B 班学生头上，账面上完全看不出来。
    session = ClassSession.objects.filter(
        id=session_id, course_class_id=class_id,
    ).select_related('course_class').first()
    if not session:
        raise CheckinError('所选时段不属于该班级，请重新选择')

    enrollments = list(
        Enrollment.objects.filter(id__in=enrollment_ids)
        .select_related('student', 'course_class')
    )

    if len(enrollments) != len(set(enrollment_ids)):
        raise CheckinError('部分报名记录不存在，请刷新页面重试')

    wrong_class = [e for e in enrollments if e.course_class_id != class_id]
    if wrong_class:
        names = '、'.join(e.student.name for e in wrong_class)
        raise CheckinError(f'{names} 不属于该班级，请重新选择')

    # 逐人算余额。放在同一个事务外先算，是为了「一次报全所有人」，
    # 而不是签到第 3 个人时才告诉老师「第 1 个就不够」。
    shortages = []
    for e in enrollments:
        remain = _remain(e)
        if remain <= 0:
            shortages.append({
                'enrollment_id': e.id,
                'student': e.student.name,
                'remain': remain,
            })

    if shortages:
        names = '、'.join(f'{s["student"]}（剩 {s["remain"]}）' for s in shortages)
        raise CheckinError(
            f'课时不足，无法签到：{names}。请先在「报名记录」里充值。',
            detail=shortages,
        )

    created = []
    skipped = []

    try:
        with transaction.atomic():
            for e in enrollments:
                # 先查「这节课是否已有效消课」。这只是为了给好读的文案，
                # 真正的防线是下面的唯一约束 —— 并发时查不到但插得进去。
                already = HourLog.objects.filter(
                    enrollment=e, session=session, lesson_date=lesson_date,
                    reason='consumed', is_revoked=False,
                ).exists()
                if already:
                    skipped.append({
                        'enrollment_id': e.id,
                        'student': e.student.name,
                    })
                    continue

                log = HourLog.objects.create(
                    enrollment=e,
                    delta=-1,
                    reason='consumed',
                    session=session,
                    lesson_date=lesson_date,
                    operator=operator,
                    remark=f'{session.course_class.name} 签到',
                )
                created.append(log)
    except IntegrityError:
        # 并发下两人同时点「签到」：唯一约束拦住后一个。
        # 这里整批回滚，让老师刷新重看，而不是把重复扣费当成功。
        raise CheckinError(
            '这节课已经签过到了（可能有其他人同时操作）。'
            '请刷新页面确认已签到名单。'
        )

    return {'created': created, 'skipped': skipped}


def revoke(log_id: int, operator: str = '') -> HourLog:
    """撤销一条签到消课。

    做两件事：给原流水打 ``is_revoked`` 标记，并补一笔 ``+1`` 的冲正。

    为什么不是直接把 delta 改回 0 或者删行：
    账本要能回答「这 1 课时去哪了」。留着原记录 + 冲正记录，
    任何一天的余额都能顺着流水一条条算回去；删了就成了一个黑洞。

    为什么要打标而不是只看有没有冲正：
    唯一约束靠 ``is_revoked=False`` 放行重新签到。若只靠「存在冲正记录」
    来判断，约束就写不成 SQL 条件，还得在应用层算，并发时不可靠。

    Raises:
        CheckinError: 记录不存在、不是消课、或已经撤销过。
    """
    try:
        log = HourLog.objects.select_related(
            'enrollment__student', 'session__course_class',
        ).get(id=log_id)
    except HourLog.DoesNotExist:
        raise CheckinError('这条记录不存在，可能已被删除')

    if log.reason != 'consumed':
        raise CheckinError('只有「消课」记录可以撤销，充值/冲正不能撤销')

    if log.is_revoked:
        raise CheckinError('这条签到已经撤销过了，不需要重复操作')

    with transaction.atomic():
        log.is_revoked = True
        log.revoked_time = timezone.now()
        log.save(update_fields=['is_revoked', 'revoked_time'])

        HourLog.objects.create(
            enrollment=log.enrollment,
            delta=-log.delta,          # -1 → +1，把课时还回去
            reason='refund',
            session=log.session,
            lesson_date=log.lesson_date,
            operator=operator,
            remark=f'撤销签到：{log.session.course_class.name if log.session else ""}',
        )

    return log


def lesson_roster(class_id: int, lesson_date: datetime.date,
                  session_id: int) -> list[dict]:
    """列出某班某时段的在册学生，以及他们这节课的签到状态。

    给 Admin 的签到页用。**包括已签到的人**（标成 checked），
    而不是只列未签到的 —— 老师需要看到全员名单才能确认谁没来。

    已结课 / 已退费的报名不列出来：他们不该再被扣课时。
    """
    enrollments = (
        Enrollment.objects
        .filter(course_class_id=class_id, status='active')
        .select_related('student')
        .order_by('student__name')
    )

    if not session_id:
        return []

    signed = {
        h.enrollment_id: h for h in HourLog.objects.filter(
            session_id=session_id, lesson_date=lesson_date,
            reason='consumed', is_revoked=False,
        )
    }

    # 谁请了假 —— 一次查完，别在循环里逐人查。
    # 老师点名时最需要这个信息：名单上有 12 个人，实际来了 9 个，
    # 「没来的 3 个是请假了还是旷课了」必须一眼看出来，
    # 否则要么白等，要么误给请假的孩子签到扣课时。
    left_ids = set(
        LeaveRequest.objects.filter(
            session_id=session_id, lesson_date=lesson_date, status='active',
        ).values_list('student_id', flat=True)
    )

    roster = []
    for e in enrollments:
        log = signed.get(e.id)
        roster.append({
            'enrollment': e,
            'student': e.student,
            'remain': _remain(e),
            'checked': log is not None,
            'log': log,
            'on_leave': e.student_id in left_ids,
        })
    return roster
