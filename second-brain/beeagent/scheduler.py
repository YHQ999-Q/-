"""定时任务（§3.6 / §3.7）：Bee 每日回顾约 20:20、Grow 洞察约 03:23（北京时间）。
本骨架不常驻进程；用 cron 调 `python -m beeagent run-daily` / `run-grow` 即可，见 scripts/crontab.example。"""
from __future__ import annotations

from datetime import datetime, timedelta

BEE_DAILY_AT = "20:20"
BEE_GROW_AT = "03:23"


def default_daily_date(now: datetime | None = None) -> str:
    """20:20 之前运行就回顾昨天，之后回顾今天。"""
    now = now or datetime.now()
    if now.strftime("%H:%M") < BEE_DAILY_AT:
        now -= timedelta(days=1)
    return now.strftime("%Y-%m-%d")
