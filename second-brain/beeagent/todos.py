"""待办建议（§3.5，提示词 §5.3）：
A. 会话级：与摘要同时生成，1–3 条，emoji + 祈使句 text(≤40 字) + details + 可选 alarm_at；
B. 主动式：结合近 48 小时摘要 + facts + 现有待办，不与会话同步。
接受 → 正式 todo（保留 emoji 前缀，created_at = 接受时间）；dismiss → 不再出现。"""
from __future__ import annotations

import re
from datetime import datetime, timedelta

from .llm import LLM, load_prompt, render
from .models import Conversation, Fact, Todo, TodoSuggestion
from .store import Store
from .summarize import ACTION_RE
from .textutil import VAGUE_TIME, parse_when, short, tokenize

MAX_SUG = 3
EMOJI = [(r"作业|提交|交上|截止|论文|DDL|ddl", "📝"), (r"考试|期末|复习|自测", "📚"), (r"录课|迟到|上课", "⏰"),
         (r"上传|课件|PPT|ppt", "📂"), (r"报名|讲座|活动", "🎟️"), (r"申请|雅思|选校|简历", "🎓"),
         (r"项目|开发|Demo|demo|Agent|agent|黑客松", "💻"), (r"联系|发给|邮件|微信", "📨")]
USER_TASK = re.compile(r"(作业|截止|提交(?!了)|交上|不要迟到|别迟到|录课|记得|别忘|(?<!我)报名(?!了)|"
                       r"下周.{0,12}(交|讲|汇报|上传|展示)|大家.{0,6}(要|得(?!知|到)|需要)|你(要|得|需要)(?!的|不要)|"
                       r"我(会|来|要)(做|负责|把|写|整理))")


def _emoji(s: str) -> str:
    return next((e for p, e in EMOJI if re.search(p, s)), "✅")


def _sim(a: str, b: str) -> float:
    x, y = set(tokenize(a)), set(tokenize(b))
    return len(x & y) / len(x | y) if x and y else 0.0


def _imperative(s: str) -> str:
    s = re.sub(r"^(嗯|呃|啊|那|然后|就是|所以|好|对)[，,、\s]*", "", s.strip())
    if VAGUE_TIME.search(s):
        return short("留意老师后续通知（日期待通知）：" + s, 40)
    if re.search(r"录课|迟到", s):
        return "准时参加录课，提前到场"
    if re.search(r"作业|提交|交上|截止", s):
        return short("按要求提交：" + s, 40)
    if re.search(r"复习|考试|期末", s):
        return short("复习准备：" + s, 40)
    if re.search(r"报名", s):
        return short("完成报名：" + s, 40)
    return short("跟进：" + s, 40)


def _alarm(s: str, ref: datetime) -> tuple[str | None, str]:
    when = parse_when(s, ref)
    if not when:
        return None, "时间待确认。" if VAGUE_TIME.search(s) or ACTION_RE.search(s) else ""
    # 截止类：提醒设在截止前 2 小时（§5.3"截止前合理时间"，§3.5 实测）
    alarm = when - timedelta(hours=2) if re.search(r"截止|之前|前交|前提交|deadline", s) else when
    return alarm.isoformat(timespec="minutes"), f"对话里的时间：{when.strftime('%m-%d %H:%M')}。"


def _dedupe(new: list[TodoSuggestion], store: Store) -> list[TodoSuggestion]:
    todos = [t.text for t in store.list_todos(open_only=True)]
    seen = [s.text for s in store.list_suggestions(None)]
    out = []
    for s in new:
        pool = [o for o in seen if o.startswith("给「")] if s.text.startswith("给「") else seen + todos  # 定提醒类本来就引用待办原文
        if any(_sim(s.text, o) > 0.5 for o in pool):
            continue
        seen.append(s.text)
        out.append(s)
        if len(out) >= MAX_SUG:
            break
    return out


def _from_llm(data, cid, kind) -> list[TodoSuggestion] | None:
    if not isinstance(data, dict) or not isinstance(data.get("suggestions"), list):
        return None
    return [TodoSuggestion(emoji=(x.get("emoji") or "✅")[:4], text=short(x.get("text", ""), 40),
                           details=x.get("details", ""), alarm_at=x.get("alarm_at"),
                           source_conversation_id=cid, kind=kind)
            for x in data["suggestions"] if x.get("text")]


def suggest_for_conversation(conv: Conversation, llm: LLM, store: Store,
                             timezone: str = "Asia/Shanghai") -> list[TodoSuggestion]:
    ref = datetime.fromisoformat(conv.start_time)
    sug = None
    if not llm.mock:
        prompt = render(load_prompt("todos"), now_local=conv.end_time or conv.start_time,
                        summary=conv.summary, transcript_excerpt=conv.transcript_text(limit=300),
                        recent_summaries_48h="（会话级模式，不提供）", facts_top_k="（会话级模式，不提供）",
                        open_todos=[t.text for t in store.list_todos(open_only=True)], timezone=timezone)
        sug = _from_llm(llm.chat_json(prompt), conv.id, "conversation")
    if sug is None:
        sug = []
        for u in conv.utterances:
            s = u.text.strip()
            if not 8 <= len(s) <= 90 or s.endswith(("？", "?")) or not USER_TASK.search(s):
                continue
            alarm, note = _alarm(s, ref)
            sug.append(TodoSuggestion(
                emoji=_emoji(s), text=_imperative(s),
                details=f"{u.spoken_at[5:16].replace('T', ' ')} 的「{conv.short_summary}」里提到：「{short(s, 50)}」。{note}".strip(),
                alarm_at=alarm, source_conversation_id=conv.id, kind="conversation"))
    return _dedupe(sug, store)


def suggest_proactive(store: Store, llm: LLM, now: str, timezone: str = "Asia/Shanghai") -> list[TodoSuggestion]:
    """主动式：近 48 小时会话 + facts + 现有待办。"""
    since = (datetime.fromisoformat(now) - timedelta(hours=48)).isoformat()
    recent = store.list_conversations(since, now, state="COMPLETED")
    facts = store.list_facts()
    open_todos = store.list_todos(open_only=True)
    sug = None
    if not llm.mock:
        prompt = render(load_prompt("todos"), now_local=now, summary="（主动模式）", transcript_excerpt="（主动模式）",
                        recent_summaries_48h=[{"time": c.start_time, "title": c.short_summary, "summary": c.summary[:800]} for c in recent],
                        facts_top_k=[f.text for f in facts[-30:]], open_todos=[t.text for t in open_todos],
                        timezone=timezone)
        sug = _from_llm(llm.chat_json(prompt), None, "proactive")
    if sug is None:
        sug = []
        for t in open_todos:
            if not t.alarm_at:
                name = re.sub(r"^\W+\s*", "", t.text)   # 去掉 emoji 前缀
                sug.append(TodoSuggestion(emoji="⏰", text=short(f"给「{name}」定一个提醒时间", 40),
                                          details=f"你有一个未设闹钟的提醒（创建于 {t.created_at[:16]}），截止时间待确认。",
                                          kind="proactive"))
        kw_days: dict[str, set] = {}
        for c in recent:
            for k in c.keywords[:6]:
                kw_days.setdefault(k, set()).add((c.start_time[:10], c.short_summary))
        for k, items in sorted(kw_days.items(), key=lambda x: -len(x[1])):
            days = {d for d, _ in items}
            if len(items) >= 2 and len(days) >= 2:
                src = "；".join(f"{d}「{t}」" for d, t in sorted(items)[:3])
                sug.append(TodoSuggestion(emoji="🗂️", text=short(f"把「{k}」在 {len(items)} 段对话里的要点整理成一页", 40),
                                          details=f"你之前多次提到「{k}」：{src}。", kind="proactive"))
                break
        pending = [f for f in facts if f.status == "pending"]
        if len(pending) >= 3:
            sug.append(TodoSuggestion(emoji="🧠", text=f"花 5 分钟确认或删除 {len(pending)} 条待确认事实",
                                      details="待确认事实不会被摘要召回；确认是唯一的人工校准入口。",
                                      kind="proactive"))
    return _dedupe(sug, store)


def accept(store: Store, sid: int) -> int | None:
    s = store.get_suggestion(sid)
    if not s or s.status != "open":
        return None
    store.set_suggestion_status(sid, "accepted")
    return store.add_todo(Todo(text=f"{s.emoji} {s.text}", alarm_at=s.alarm_at, source_suggestion_id=sid))


def dismiss(store: Store, sid: int) -> bool:
    s = store.get_suggestion(sid)
    if not s or s.status != "open":
        return False
    store.set_suggestion_status(sid, "dismissed")
    return True
