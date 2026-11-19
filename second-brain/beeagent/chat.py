"""Chat Agent（§3.8，提示词 §5.6）：工具调用式 RAG。

真实模型：OpenAI function-calling 循环（最多 6 步）。
mock：按关键词把问题路由到工具，回答必须带日期和出处；什么都没找到就说没找到，不编造。
对外动作（发邮件/建日程/下单）本骨架不提供；create_todo / create_fact 只写本地库。"""
from __future__ import annotations

import json
import re
from datetime import datetime, timedelta

from .llm import LLM, load_prompt, render
from .models import Fact, Todo
from .search import clean_query, search
from .store import Store
from .textutil import short

TOOLS_SPEC = [
    ("search", "BM25 检索会话/每日回顾/事实/语音笔记。mode=semantic 目前退化为 BM25。",
     {"query": "string", "mode": "string", "since": "string", "until": "string", "filter": "string"}, ["query"]),
    ("get_conversation", "读取一段会话的摘要；include_transcript=true 时附转写（ASR，可能有识别错误）。",
     {"id": "string", "include_transcript": "boolean"}, ["id"]),
    ("list_facts", "列出关于用户的事实（confirmed / pending）。", {"confirmed": "boolean"}, []),
    ("create_fact", "用户说「记住……」或纠正你时，创建一条已确认事实。", {"text": "string", "tags": "array"}, ["text"]),
    ("list_todos", "列出待办与待办建议。", {}, []),
    ("create_todo", "为用户创建本地待办（不对外发送）。", {"text": "string", "alarm_at": "string"}, ["text"]),
    ("get_daily", "读取某天（YYYY-MM-DD）的每日回顾。", {"date": "string"}, ["date"]),
    ("get_insights", "读取最近的 Grow 洞察。", {}, []),
]


def openai_tools() -> list:
    out = []
    for name, desc, props, req in TOOLS_SPEC:
        p = {k: ({"type": "array", "items": {"type": "string"}} if v == "array" else {"type": v}) for k, v in props.items()}
        out.append({"type": "function", "function": {"name": name, "description": desc,
                                                     "parameters": {"type": "object", "properties": p, "required": req}}})
    return out


class Tools:
    def __init__(self, store: Store):
        self.store = store

    def search(self, query, mode="keyword", since="", until="9999", filter="all", limit=5):
        return [h.__dict__ for h in search(self.store, query, filter, limit, since or "", until or "9999", mode)]

    def get_conversation(self, id, include_transcript=False):
        c = self.store.get_conversation(id, with_utts=include_transcript)
        if not c:
            return {"error": f"没有会话 {id}"}
        d = c.to_dict()
        if include_transcript:
            d["transcript"] = c.transcript_text(limit=400)
            d["note"] = "转写是 ASR 输出，可能有识别错误；摘要是 AI 生成，utterances 才是权威。"
        return d

    def list_facts(self, confirmed=None):
        return [f.__dict__ for f in self.store.list_facts(confirmed)]

    def create_fact(self, text, tags=None):
        fid = self.store.add_fact(Fact(text=text, tags=tags or ["general"], confirmed=True, status="confirmed"))
        return {"created_fact_id": fid}

    def list_todos(self):
        return {"todos": [t.__dict__ for t in self.store.list_todos()],
                "suggestions": [s.__dict__ for s in self.store.list_suggestions("open")]}

    def create_todo(self, text, alarm_at=None):
        return {"created_todo_id": self.store.add_todo(Todo(text=text, alarm_at=alarm_at))}

    def get_daily(self, date):
        d = self.store.get_daily(date)
        return d.__dict__ if d else {"error": f"{date} 没有每日回顾"}

    def get_insights(self):
        return [i.__dict__ for i in self.store.list_insights()][-8:]

    def call(self, name, args: dict):
        fn = getattr(self, name, None)
        if not fn or name.startswith("_") or name == "call":
            return {"error": f"未知工具 {name}"}
        try:
            return fn(**args)
        except TypeError as e:
            return {"error": f"参数错误：{e}"}


# ---------------- 时间表达 → 日期范围 ----------------
def resolve_dates(q: str, now: datetime) -> tuple[str, str] | None:
    if m := re.search(r"(\d{4})-(\d{2})-(\d{2})", q):
        d = datetime(int(m[1]), int(m[2]), int(m[3]))
    elif m := re.search(r"(\d{1,2})月(\d{1,2})[日号]?", q):
        d = datetime(now.year, int(m[1]), int(m[2]))
    elif "前天" in q:
        d = now - timedelta(days=2)
    elif "昨天" in q:
        d = now - timedelta(days=1)
    elif "今天" in q:
        d = now
    else:
        return None
    return d.strftime("%Y-%m-%d"), (d + timedelta(days=1)).strftime("%Y-%m-%d")


NOT_FOUND = "没找到相关记录。"


def _strip_dates(q: str) -> str:
    return re.sub(r"\d{4}-\d{2}-\d{2}|\d{1,2}月\d{1,2}[日号]?|前天|昨天|今天", " ", q)


def mock_answer(tools: Tools, q: str, now: datetime) -> tuple[str, list]:
    trace = []

    def call(name, **a):
        r = tools.call(name, a)
        trace.append({"tool": name, "args": a})
        return r

    rng = resolve_dates(q, now)
    if m := re.search(r"(?:记住|帮我记一下|请记住)[：:，,\s]*(.+)", q):
        r = call("create_fact", text=m.group(1).strip("。 "), tags=["general"])
        return f"好，记住了（事实 #{r['created_fact_id']}，已确认）：{m.group(1).strip('。 ')}", trace
    if m := re.search(r"(?:提醒我|帮我建(?:个|一个)?待办|加(?:个|一个)?待办)[：:，,\s]*(.+)", q):
        r = call("create_todo", text=m.group(1).strip("。 "))
        return f"已在本地建好待办 #{r['created_todo_id']}：{m.group(1).strip('。 ')}（时间未指定）。", trace
    if re.search(r"待办|todo|要做什么|该做什么", q, re.I):
        r = call("list_todos")
        lines = [f"- {t['text']}" + (f"（提醒 {t['alarm_at']}）" if t["alarm_at"] else "") for t in r["todos"] if not t["completed"]]
        lines += [f"- [建议] {s['emoji']} {s['text']}" for s in r["suggestions"][:5]]
        return ("你的待办：\n" + "\n".join(lines)) if lines else "现在没有待办，也没有待处理的建议。", trace
    if re.search(r"洞察|成长|grow|模式", q, re.I):
        r = call("get_insights")
        if not r:
            return "还没有生成 Grow 洞察（先运行 run-grow）。", trace
        return "最近的洞察：\n" + "\n".join(f"- {i['title']}（{i['generated_at'][:10]}）→ {i['suggested_actions'][0]}" for i in r[-5:]), trace
    if re.search(r"关于我|我的事实|你知道我|记得我", q):
        r = call("list_facts")
        if not r:
            return "关于你的事实库目前是空的。", trace
        return "你的事实（✓ 已确认 / ? 待确认）：\n" + "\n".join(
            f"- {'✓' if f['confirmed'] else '?'} {short(f['text'], 60)}" for f in r[-10:]), trace
    rest = clean_query(_strip_dates(q))
    if rng and (not rest.strip() or re.search(r"做了什么|干了什么|回顾|总结|怎么样", q)):
        r = call("get_daily", date=rng[0])
        if "error" in r:
            return f"{rng[0]} 没有每日回顾（可能那天没有录音，或还没运行 run-daily）。", trace
        return f"{r['date']} 的每日回顾：{r['short_summary']}\n" + "\n".join(
            f"- {c['time']}「{c['title']}」" for c in r["conversations"]) + f"\n（出处：每日回顾 {r['date']}）", trace
    since, until = rng if rng else ("", "9999")
    hits = call("search", query=_strip_dates(q), since=since, until=until)
    if not hits:
        return NOT_FOUND + f"（检索词：{rest.strip() or q}" + (f"，日期 {since}" if since else "") + "）我不会编造没有记录的内容。", trace
    h = hits[0]
    if h["kind"] == "conversation":
        call("get_conversation", id=h["id"], include_transcript=True)   # 按 §5.6 第 3 条读转写核对
        ans = (f"在 {h['date'][:10]} {h['date'][11:16]} 的「{h['title']}」里找到：\n> {short(h['snippet'], 120)}\n"
               f"（出处：会话 {h['id']}；转写来自语音识别，可能有同音字错误）")
    else:
        kind_cn = {"daily": "每日回顾", "fact": "事实", "journal": "语音笔记"}[h["kind"]]
        ans = f"在{kind_cn} {h['date']} 找到：\n> {short(h['snippet'], 120)}\n（出处：{h['kind']} {h['id']}）"
    others = [f"{x['date'][:10]}「{x['title']}」" for x in hits[1:3]]
    if others:
        ans += "\n其他相关：" + "；".join(others)
    return ans, trace


def ask(store: Store, llm: LLM, question: str, now: str | None = None, user_name: str = "用户",
        timezone: str = "Asia/Shanghai", history: list | None = None) -> tuple[str, list]:
    now_dt = datetime.fromisoformat(now) if now else datetime.now()
    tools = Tools(store)
    if not llm.mock:
        system = render(load_prompt("chat"), user_name=user_name, now_local=now_dt.isoformat(timespec="minutes"),
                        timezone=timezone, location="（未提供）")
        system += "\n\n【本复刻实际提供的工具】" + "、".join(t[0] for t in TOOLS_SPEC) + "；其余工具尚未实现，不要调用。"
        msgs = [{"role": "system", "content": system}, *(history or []), {"role": "user", "content": question}]
        trace = []
        for _ in range(6):
            m = llm.chat_tools(msgs, openai_tools())
            if m is None:
                break
            msgs.append({k: v for k, v in m.items() if k in ("role", "content", "tool_calls")})
            calls = m.get("tool_calls") or []
            if not calls:
                return m.get("content") or NOT_FOUND, trace
            for tc in calls:
                name = tc["function"]["name"]
                try:
                    args = json.loads(tc["function"].get("arguments") or "{}")
                except json.JSONDecodeError:
                    args = {}
                res = tools.call(name, args)
                trace.append({"tool": name, "args": args})
                msgs.append({"role": "tool", "tool_call_id": tc["id"],
                             "content": json.dumps(res, ensure_ascii=False, default=str)[:6000]})
    return mock_answer(tools, question, now_dt)
