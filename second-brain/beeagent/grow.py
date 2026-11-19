"""Grow 洞察（§3.7，提示词 §5.5）：每天约 03:23（北京时间）批量；过去 72 小时；每批 6–8 条；
每条 title + description + suggested_actions（恰好 1 条）。mock 从小数据可能少于 6 条，但结构强制一致。"""
from __future__ import annotations

from collections import defaultdict
from datetime import datetime, timedelta

from .llm import LLM, load_prompt, render
from .models import Insight
from .store import Store
from .textutil import short

WINDOW_H = 72
MIN_N, MAX_N = 6, 8


def validate(items) -> list[Insight]:
    """强制结构：title/description 非空，suggested_actions 恰好 1 条（多了截断，没有就丢弃）。"""
    out = []
    for it in items or []:
        if isinstance(it, Insight):
            it = {"title": it.title, "description": it.description, "suggested_actions": it.suggested_actions,
                  "evidence": it.evidence}
        acts = it.get("suggested_actions") or ([it["suggested_action"]] if it.get("suggested_action") else [])
        if isinstance(acts, str):
            acts = [acts]
        acts = [a for a in acts if isinstance(a, str) and a.strip()]
        if not (it.get("title") and it.get("description") and acts):
            continue
        out.append(Insight(title=it["title"].strip(), description=it["description"].strip(),
                           suggested_actions=[acts[0].strip()], evidence=it.get("evidence", [])))
    return out[:MAX_N]


def _mock(convs, dailies, facts, todos, prev_titles) -> list[dict]:
    ins = []
    # 1) 跨场景反复出现的主题
    kw = defaultdict(list)
    for c in convs:
        for k in c.keywords[:6]:
            kw[k].append(c)
    rec = sorted(((k, v) for k, v in kw.items() if len({c.scene for c in v}) >= 2 or len({c.start_time[:10] for c in v}) >= 2),
                 key=lambda x: -len(x[1]))
    for k, cs in rec[:2]:
        ev = "；".join(f"{c.start_time[5:10]}「{c.short_summary}」" for c in cs[:3])
        scenes = "、".join(sorted({c.scene for c in cs}))
        ins.append({"title": f"「{k}」正在跨场景反复出现，但还没有被你整理成自己的结论。",
                    "description": f"过去 72 小时里，「{k}」出现在 {len(cs)} 段对话中（{ev}），涉及场景：{scenes}。"
                                   "同一主题在不同场合被反复提起，通常说明它正在成为你的长期关注点；"
                                   "但目前记录里只有分散的片段，没有一处是你自己的归纳。",
                    "suggested_actions": [f"今天花 20 分钟，把「{k}」在这 {len(cs)} 段对话里的说法写成 5 行自己的结论。"],
                    "evidence": [c.id for c in cs[:3]]})
    # 2) 学习与项目的交叉
    lec = [c for c in convs if c.scene == "lecture"]
    proj = [c for c in convs if c.scene in ("meeting", "one_on_one")]
    if lec and proj:
        shared = sorted({k for c in lec for k in c.keywords} & {k for c in proj for k in c.keywords})
        ins.append({"title": "课堂输入和项目讨论正在并行推进，但两边的连接还很少。",
                    "description": f"窗口内有 {len(lec)} 段课堂/讲座和 {len(proj)} 段项目或一对一交流"
                                   f"（如 {lec[0].start_time[5:10]}「{lec[0].short_summary}」与 {proj[0].start_time[5:10]}「{proj[0].short_summary}」）。"
                                   + (f"两边共同出现的关键词有：{'、'.join(shared[:4])}。" if shared else "两边的关键词几乎没有重叠。")
                                   + "课上学到的框架如果不被带进项目，很容易停留在考试层面。",
                    "suggested_actions": [f"在下一次项目讨论前，从「{lec[0].short_summary}」里挑 1 个概念，写一句它对项目的启发。"],
                    "evidence": [lec[0].id, proj[0].id]})
    # 3) 高强度的一天
    by_day = defaultdict(list)
    for c in convs:
        by_day[c.start_time[:10]].append(c)
    if by_day:
        day, cs = max(by_day.items(), key=lambda x: len(x[1]))
        if len(cs) >= 3:
            ins.append({"title": f"{day[5:]} 是窗口内最满的一天，信息密度很高，但消化时间很少。",
                        "description": f"这一天有 {len(cs)} 段录音："
                                       + "；".join(f"{c.start_time[11:16]}「{c.short_summary}」" for c in cs)
                                       + "。连续切换课堂、交流和项目，意味着每段的要点都只停留在纪要里。",
                        "suggested_actions": [f"选 {day[5:]} 中最重要的 1 段纪要，今晚用 10 分钟复述它的三条要点。"],
                        "evidence": [c.id for c in cs]})
    # 3b) 晚间时段
    eve = [c for c in convs if c.start_time[11:13] >= "18"]
    if len(eve) >= 2:
        ins.append({"title": "晚上的时间正在被课程和项目占满，留给消化和休息的空当变少。",
                    "description": "过去 72 小时里有 " + str(len(eve)) + " 段录音开始于 18 点之后："
                                   + "；".join(f"{c.start_time[5:16].replace('T', ' ')}「{c.short_summary}」" for c in eve[:3])
                                   + "。晚间输入如果没有当天收尾，第二天往往只剩模糊印象；"
                                   "而且 Bee 的每日回顾在 20:20 生成，晚间内容也最容易漏掉。",
                    "suggested_actions": ["接下来 3 天，每晚 22:00 前留 15 分钟，只做一件事：给当晚的录音写 3 行收尾。"],
                    "evidence": [c.id for c in eve[:3]]})
    # 4) 截止与"日期待通知"
    vague = [c for c in convs if "日期待通知" in c.summary]
    if vague:
        ins.append({"title": "有截止时间还悬而未决，容易在最后一刻变成压力。",
                    "description": "这些纪要里有老师说「之后再通知」的事项："
                                   + "；".join(f"{c.start_time[5:10]}「{c.short_summary}」" for c in vague[:3])
                                   + "。日期不确定的任务最容易被遗忘，也最容易和其他截止撞在一起。",
                        "suggested_actions": ["今天给这些「日期待通知」的事项各建一个无日期待办，并在下次课后 1 小时内补上日期。"],
                        "evidence": [c.id for c in vague[:3]]})
    # 5) 记忆校准
    pending = [f for f in facts if f.status == "pending"]
    if len(pending) >= 2:
        ins.append({"title": "关于你的记忆正在积累，但大部分还没有经过你的确认。",
                    "description": f"目前有 {len(pending)} 条 AI 抽取的事实处于待确认状态，例如「{short(pending[0].text, 40)}」。"
                                   "未确认的事实不会进入之后的摘要召回；如果其中有错误，后续洞察也会建立在错误前提上。",
                    "suggested_actions": [f"花 5 分钟处理这 {len(pending)} 条待确认事实：对的确认，不对的删除。"],
                    "evidence": [f"fact:{f.id}" for f in pending[:5]]})
    # 6) 待办建议堆积
    if todos:
        ins.append({"title": "对话里冒出的待办比你已经接手的多。",
                    "description": f"系统从对话中提出了 {len(todos)} 条待办建议还没有处理，例如「{todos[0].text}」。"
                                   "建议一直悬着，等于把对话里的承诺留在了录音里。",
                    "suggested_actions": ["现在用 3 分钟过一遍待办建议：要做的接受，不做的忽略。"],
                    "evidence": [f"suggestion:{t.id}" for t in todos[:5]]})
    return [i for i in ins if i["title"] not in prev_titles]


def run_grow(store: Store, llm: LLM, now: str) -> tuple[list[Insight], str]:
    since = (datetime.fromisoformat(now) - timedelta(hours=WINDOW_H)).isoformat(timespec="seconds")
    convs = store.list_conversations(since, now, state="COMPLETED")
    dailies = store.list_dailies(since[:10], now[:10] + "~")
    facts = store.list_facts()
    sugg = store.list_suggestions("open")
    prev = store.list_insights((datetime.fromisoformat(now) - timedelta(days=14)).isoformat())
    items = None
    if not llm.mock and convs:
        prompt = render(load_prompt("grow"),
                        dailies_3d=[{"date": d.date, "summary": d.summary[:1500]} for d in dailies],
                        summaries_72h=[{"id": c.id, "time": c.start_time, "title": c.short_summary, "summary": c.summary[:1200]} for c in convs],
                        facts=[f.text for f in facts[-40:]], health="（未接入）",
                        previous_insights_14d=[i.title for i in prev], corrections="（无）")
        prompt += '\n\n每个元素：{"title":"","description":"","suggested_actions":["恰好一条"],"evidence":["会话id"]}'
        data = llm.chat_json(prompt)
        items = data.get("insights") if isinstance(data, dict) else data
    insights = validate(items) if items else []
    mode = llm.mode
    if not insights:
        insights = validate(_mock(convs, dailies, facts, sugg, {i.title for i in prev}))
        mode = "mock"
    for i in insights:
        i.generated_at = now
        i.id = store.add_insight(i)
    return insights, f"窗口 {since[:16]} → {now[:16]}，会话 {len(convs)} 段，模式 {mode}"
