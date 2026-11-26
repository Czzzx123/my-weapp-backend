"""发上课前提醒。设计给 cron 每 5 分钟跑一次。

    */5 * * * * cd /path/to/backend && .venv/bin/python manage.py send_class_reminders

为什么是命令而不是视图
----------------------
视图要有人访问才会跑。提醒的价值在于「家长还没想起来的时候主动送到」，
靠访问触发等于没提醒。定时任务必须由机器驱动。

``--dry-run`` 是给上线前验的
---------------------------
第一次配 cron 时谁都不敢直接开。``--dry-run`` 会走完整流程
（查该发谁、每人什么状态、跳过原因），只是不真的调微信接口。
先看一遍输出，确认「今天该发 12 条、3 个已签到、1 个课时不足」，
再关掉 dry-run 上真发。

``--date`` 是给补发用的
-----------------------
某个时段漏发了（服务挂了、token 过期没重试成功），
可以 ``--date 2026-11-27 --force`` 补一次。
``--force`` 会跳过「该发时刻是否落在窗口内」的判断，
但仍然受 ``NotifyLog`` 唯一约束保护 —— 发过的不重发。
"""

from __future__ import annotations

import datetime

from django.conf import settings
from django.core.management.base import BaseCommand
from django.utils import timezone

from smart import reminder
from smart.models import ClassSession


class Command(BaseCommand):
    help = '发送上课前提醒（订阅消息）'

    def add_arguments(self, parser):
        parser.add_argument(
            '--dry-run', action='store_true',
            help='只打印该发给谁，不真正发送')
        parser.add_argument(
            '--date', type=str, default='',
            help='指定日期（YYYY-MM-DD），配合 --force 补发用')
        parser.add_argument(
            '--force', action='store_true',
            help='忽略时间窗口，强制处理指定日期的全部时段')
        parser.add_argument(
            '--lead', type=int, default=0,
            help='提前多少分钟提醒，默认读 settings.WECHAT_REMIND_LEAD_MINUTES')

    def handle(self, *args, **options):
        from libs.wechat_notify import notify_before_class

        lead = options['lead'] or getattr(
            settings, 'WECHAT_REMIND_LEAD_MINUTES', 120)
        dry = options['dry_run']
        now = timezone.localtime() if settings.USE_TZ else timezone.now()

        # ---- 找出该处理的时段 ----
        if options['date']:
            try:
                day = datetime.datetime.strptime(options['date'], '%Y-%m-%d').date()
            except ValueError:
                self.stderr.write(f'日期格式不对：{options["date"]}，应为 YYYY-MM-DD')
                return
            pairs = [(s, day) for s in reminder.sessions_of_day(day)]
            if not options['force']:
                # 指定了日期却没给 --force，仍然按窗口过滤，
                # 避免「--date 写错成明天」把明天的提醒提前发掉
                in_window = {
                    (s.id, d) for s, d in
                    reminder.due_sessions(now, lead, max(5, lead))
                }
                pairs = [p for p in pairs if (p[0].id, p[1]) in in_window]
        else:
            # 窗口取任务间隔的 2 倍。cron 跑 5 分钟一次，窗口给 10 分钟，
            # 容忍偶尔跑慢一拍；再大就会和「提前 N 分钟」的语义打架
            pairs = reminder.due_sessions(now, lead, window_minutes=10)

        if not pairs:
            self.stdout.write('现在没有需要提醒的课')
            return

        self.stdout.write(
            f'{"[预演] " if dry else ""}共 {len(pairs)} 个时段待处理，'
            f'提前 {lead} 分钟')

        stats = {'sent': 0, 'failed': 0, 'skipped': 0, 'muted': 0}

        for session, day in pairs:
            recipients = reminder.collect_recipients(session, day)
            self.stdout.write(
                f'\n· {day} {session.start_time.strftime("%H:%M")} '
                f'{session.course_class.name}（{len(recipients)} 人）')

            for row in recipients:
                parent = row['parent']
                student = row['student']
                who = (parent.nickname or parent.mobile or parent.id) if parent else ''

                # 结构性跳过：这些情况**不占 NotifyLog 名额**。
                #
                # 为什么不占：占了这个名额，将来条件变好了也不会补发 ——
                #   · 已签到 → 老师撤销后本该提醒，但记录还挡着
                #   · 已请假 → 家长撤销请假后本该提醒，同样被挡
                #   · 课时不足 → 家长充值后本该提醒，同样被挡
                #   · 家长没 openid → 后来绑定了微信，还是收不到
                # 也就是说「占名额」会把一次性的临时状态变成永久结论。
                # 只在**真的要发**的时候才抢占，这一条能挡掉一整类难查的 bug。
                if parent is None or not parent.openid:
                    stats['skipped'] += 1
                    self.stdout.write(f'    - {student.name}：家长未绑定微信')
                    continue

                if row['skip_reason']:
                    stats['skipped'] += 1
                    self.stdout.write(
                        f'    - {student.name}（{who}）：{row["skip_reason"]}')
                    continue

                if dry:
                    self.stdout.write(
                        f'    - {student.name}（{who}）：将发送，'
                        f'剩 {row["remain"]} 课时')
                    stats['sent'] += 1
                    continue

                # ---- 真正发送 ----
                log = reminder.claim(
                    parent.id, student.id, session.id, day, 'before_class')
                if log is None:
                    stats['muted'] += 1
                    self.stdout.write(
                        f'    - {student.name}（{parent.nickname or parent.mobile or parent.id}）：已发过，跳过')
                    continue

                ok, errcode, msg = notify_before_class(
                    parent, student, session, day, row['remain'])
                if ok:
                    reminder.mark(log, 'sent', channel='subscribe')
                    stats['sent'] += 1
                    self.stdout.write(
                        f'    - {student.name}（{parent.nickname or parent.mobile or parent.id}）：已发送')
                elif errcode == 43101:
                    # 未授权不属于故障：家长没在课表页点过「开启提醒」，
                    # 或者点过但额度已用掉。记 skipped，别污染失败统计。
                    reminder.mark(log, 'skipped', channel='subscribe',
                                  errcode=errcode, remark='未订阅/额度用完')
                    stats['skipped'] += 1
                    self.stdout.write(
                        f'    - {student.name}（{parent.nickname or parent.mobile or parent.id}）：未订阅，跳过')
                else:
                    reminder.mark(log, 'failed', channel='subscribe',
                                  errcode=errcode, remark=msg)
                    stats['failed'] += 1
                    self.stdout.write(
                        f'    - {student.name}（{parent.nickname or parent.mobile or parent.id}）：失败 {errcode} {msg}')

        self.stdout.write(
            f'\n{"[预演] " if dry else ""}完成：'
            f'发送 {stats["sent"]}，跳过 {stats["skipped"]}，'
            f'失败 {stats["failed"]}，已发过 {stats["muted"]}')
        if stats['failed']:
            # 有失败时返回非零退出码，cron 会把输出邮件给运维
            raise SystemExit(1)
