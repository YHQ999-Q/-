"""会话切分（§3.1）：静音超时关闭会话 + 预录回填 + 状态流转。

实测依据：会话内两句最大间隔 ≤ 约 333 秒；最后一句到 end_time 的尾巴 380–393 秒 → 静音 ≈360 秒关会话；
首句 spoken_at 比 start_time 早 25–30 秒 → 连续语音约 30 秒才"立会话"，并回填之前的缓冲。
未实现：Bee 的语义/话题切分与单段最长时长（§3.1 第 3 条，§8 未确认），留 TODO。
"""
from __future__ import annotations

import hashlib
from datetime import datetime
from typing import Optional

from .models import CAPTURING, COMPLETED, Conversation, Utterance
from .transcribe import add_seconds


def _cid(first: Utterance) -> str:
    h = hashlib.sha1(f"{first.spoken_at}|{first.text}".encode()).hexdigest()[:6]
    return f"c{first.spoken_at[:10].replace('-', '')}-{first.spoken_at[11:16].replace(':', '')}-{h}"


def split_by_silence(utts: list[Utterance], gap_s: int = 360) -> list[list[Utterance]]:
    """相邻两句（上一句结束 → 下一句开始）静音超过 gap_s 就切开。"""
    groups: list[list[Utterance]] = []
    last_end: Optional[datetime] = None
    for u in sorted(utts, key=lambda x: x.spoken_at):
        t = datetime.fromisoformat(u.spoken_at)
        if last_end is None or (t - last_end).total_seconds() > gap_s:
            groups.append([])
        groups[-1].append(u)
        dur = max(u.end - u.start, 0.0)
        end = datetime.fromisoformat(add_seconds(u.spoken_at, dur))
        last_end = max(end, last_end) if last_end and groups[-1][:-1] else end
    return groups


def build_conversations(utts: list[Utterance], gap_s: int = 360, preroll_s: int = 30,
                        now: Optional[str] = None) -> list[Conversation]:
    """切分后生成会话：
    - start_time = 首句 + preroll_s（"立会话"时刻；之前 preroll_s 秒的缓冲回填进来，所以首句早于 start_time）
    - 最后一句之后静音 ≥ gap_s（相对 now）→ COMPLETED，end_time = 最后一句结束 + gap_s（Bee 的"尾巴"）
    - 否则仍是 CAPTURING（录音中；Bee 此时只有滚动临时标题，summary 为空）
    """
    now_dt = datetime.fromisoformat(now) if now else None
    convs = []
    for g in split_by_silence(utts, gap_s):
        first, last = g[0], g[-1]
        last_end = add_seconds(last.spoken_at, max(last.end - last.start, 0.0))
        c = Conversation(id=_cid(first), start_time=add_seconds(first.spoken_at, preroll_s),
                         utterances=g)
        closed = now_dt is None or (now_dt - datetime.fromisoformat(last_end)).total_seconds() >= gap_s
        if closed:
            c.state, c.end_time = COMPLETED, add_seconds(last_end, gap_s)
        else:
            c.state, c.end_time = CAPTURING, ""
        convs.append(c)
    return convs
