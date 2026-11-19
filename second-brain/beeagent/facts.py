"""事实抽取（§3.4，提示词 §5.2）：每段会话 0–3 条，默认 pending，与摘要同一时刻写入；
标签只能来自固定 11 个；用户 confirm/reject；已确认事实会被后续摘要召回（summarize.recall_rule_facts）。
相对 Bee 的收紧（§7.3#5）：敏感类目不抽取；说话人未区分时，第一人称陈述标"待核实"。"""
from __future__ import annotations

import re

from .config import FACT_TAGS
from .llm import LLM, load_prompt, render
from .models import Conversation, Fact
from .summarize import SENSITIVE_RE
from .textutil import tokenize

MAX_FACTS = 3


def _sim(a: str, b: str) -> float:
    x, y = set(tokenize(a)), set(tokenize(b))
    return len(x & y) / len(x | y) if x and y else 0.0


def clean_tags(tags) -> list[str]:
    tags = [t for t in (tags or []) if t in FACT_TAGS][:3]
    return tags or ["general"]


def _mock(conv: Conversation) -> list[dict]:
    kws = conv.keywords or ["这段对话"]
    k = "、".join(kws[:2])
    title = conv.short_summary.split("：")[0]
    out = []
    if conv.scene == "lecture" and conv.short_summary.startswith("讲座"):
        out.append({"text": f"曾听讲座「{conv.short_summary}」，涉及{k}", "tags": ["education"]})
    elif conv.scene == "lecture":
        out.append({"text": f"正在修读「{title}」，近期内容涉及{k}", "tags": ["education"]})
    elif conv.scene == "meeting":
        tech = any(re.search(r"[A-Za-z]|智能|模型|数据|开发|系统", x) for x in kws)
        out.append({"text": f"正在参与与{k}相关的项目讨论（具体分工待核实）",
                    "tags": ["technology", "general"] if tech else ["general"]})
    elif conv.scene == "one_on_one":
        out.append({"text": f"曾就{k}进行一对一交流（结论待核实）", "tags": ["education"]})
    # 说话人未区分（§6.1）：mock 不从第一人称句子抽事实，避免把别人的话记成用户的；接真实模型后由 §5.2 判断。
    if re.search(r"(纪要|总结|笔记).{0,6}(要|应该|希望|写成)", "\n".join(u.text for u in conv.utterances)):
        out.append({"text": "对录音纪要的写法有明确偏好（具体规则待确认）", "tags": ["preferences", "transcription"]})
    return out


def extract_facts(conv: Conversation, llm: LLM, existing: list[Fact], user_name: str = "用户") -> list[Fact]:
    raw = None
    if not llm.mock:
        prompt = render(load_prompt("facts"), user_name=user_name,
                        existing_facts_top_k="\n".join(f"[{f.id}] {f.text}" for f in existing[-40:]) or "（无）",
                        summary=conv.summary, transcript=conv.transcript_text(limit=300))
        data = llm.chat_json(prompt)
        raw = (data or {}).get("facts") if isinstance(data, dict) else None
    if raw is None:
        raw = _mock(conv)
    out: list[Fact] = []
    for r in raw:
        text = (r.get("text") or "").strip()
        if not text or SENSITIVE_RE.search(text):
            continue
        if any(_sim(text, f.text) > 0.6 for f in existing + out):
            continue  # 去重（update 语义留 TODO：真实模型输出 op=update 时可改写旧 fact）
        out.append(Fact(text=text[:2000], tags=clean_tags(r.get("tags")), confirmed=False,
                        status="pending", source_conversation_id=conv.id))
        if len(out) >= MAX_FACTS:
            break
    return out
