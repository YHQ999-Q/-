"""数据模型：对齐 Bee 开发者 API 字段（§2），并加上 §7.2 建议字段（scene、rules_applied、source_conversation_id、status…）。"""
from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Optional

CAPTURING, COMPLETED = "CAPTURING", "COMPLETED"


@dataclass
class Utterance:
    text: str
    speaker: str = "Unknown"      # Bee 本账号全部是 Unknown（§3.2）
    start: float = 0.0            # 相对会话首句的秒数
    end: float = 0.0
    spoken_at: str = ""           # 绝对时间（本地 ISO，取整到秒）
    realtime: bool = True         # 实时稿 True；会话关闭后批量重转写 → False
    id: Optional[int] = None
    source: str = ""              # 来自哪个输入文件（仅调试用）


@dataclass
class Conversation:
    id: str
    start_time: str
    end_time: str = ""
    state: str = CAPTURING
    short_summary: str = ""
    summary: str = ""
    scene: str = ""
    keywords: list = field(default_factory=list)
    suggested_links: list = field(default_factory=list)   # 相关会话（关键词重叠预计算）
    rules_applied: list = field(default_factory=list)     # §7.2：本次用了哪些规则
    template_version: str = ""
    updated_at: str = ""
    utterances: list = field(default_factory=list)

    def transcript_text(self, limit: Optional[int] = None) -> str:
        us = self.utterances if limit is None else self.utterances[:limit]
        return "\n".join(f"[{u.spoken_at[11:19]}] {u.speaker}: {u.text}" for u in us)

    def to_dict(self, with_utterances: bool = False) -> dict:
        d = asdict(self)
        if not with_utterances:
            d.pop("utterances")
        return d


@dataclass
class Fact:
    text: str
    tags: list
    confirmed: bool = False
    source_conversation_id: Optional[str] = None
    status: str = "pending"        # pending / confirmed / rejected（§7.2）
    created_at: str = ""
    id: Optional[int] = None


@dataclass
class TodoSuggestion:
    emoji: str
    text: str
    details: str
    alarm_at: Optional[str] = None
    source_conversation_id: Optional[str] = None
    kind: str = "conversation"     # conversation / proactive（§3.5 A/B）
    status: str = "open"           # open / accepted / dismissed
    created_at: str = ""
    id: Optional[int] = None


@dataclass
class Todo:
    text: str
    completed: bool = False
    alarm_at: Optional[str] = None
    created_at: str = ""
    source_suggestion_id: Optional[int] = None
    id: Optional[int] = None


@dataclass
class Daily:
    date: str
    short_summary: str
    summary: str
    conversations_count: int
    conversations: list = field(default_factory=list)
    calendar_summary: str = ""
    created_at: str = ""


@dataclass
class Insight:
    title: str
    description: str
    suggested_actions: list          # 恰好 1 条（§3.7）
    generated_at: str = ""
    evidence: list = field(default_factory=list)   # 引用的会话 id / 日期
    id: Optional[int] = None


@dataclass
class Journal:
    text: str
    aiResponse: dict                 # {cleanedUpText, message, followUp, todos[]}
    created_at: str = ""
    state: str = "COMPLETED"
    id: Optional[int] = None
