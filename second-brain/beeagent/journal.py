"""语音笔记（§3.9，提示词 §5.7）：一次调用产出 cleanedUpText / message / followUp / todos[]。"""
from __future__ import annotations

import re

from .llm import LLM, load_prompt, render
from .models import Journal
from .store import Store
from .textutil import short

FILLERS = re.compile(r"(嗯+|呃+|啊+|哦+|那个|就是说|就是|然后呢|然后|对吧|这个这个|那个那个)[，,、\s]*")
TODO_RE = re.compile(r"(?:要|得|记得|别忘了|打算|准备|明天|今晚|下周)([^，。！？,.!?]{3,30})")


def process_journal(store: Store, llm: LLM, raw_text: str) -> Journal:
    resp = None
    if not llm.mock:
        data = llm.chat_json(render(load_prompt("journal"), raw_text=raw_text))
        if isinstance(data, dict) and "cleanedUpText" in data:
            resp = {k: data.get(k) for k in ("cleanedUpText", "message", "followUp", "todos")}
            resp["todos"] = (resp.get("todos") or [])[:3]
    if resp is None:
        clean = FILLERS.sub("", raw_text).strip()
        clean = re.sub(r"(.{2,8})\1+", r"\1", clean)   # 去掉口吃式重复
        todos = []
        for m in TODO_RE.finditer(clean):
            t = short(m.group(0).strip(), 30)
            if t not in todos:
                todos.append(t)
        resp = {"cleanedUpText": clean,
                "message": "记下了。" + (f"里面有 {len(todos[:3])} 件你说要做的事。" if todos else ""),
                "followUp": "这件事里，你最想先推进的是哪一步？" if len(clean) > 10 else "",
                "todos": todos[:3]}
    j = Journal(text=raw_text, aiResponse=resp)
    j.id = store.add_journal(j)
    return j
