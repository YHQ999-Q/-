"""每日回顾（§3.6，提示词 §5.4）。Bee 每天约 20:20（北京时间）定时生成；20:20 之后的会话进不了当天回顾。
本复刻默认收录当天全部已完成会话，`--cutoff 20:20` 可复现 Bee 的行为。邮件/位置/照片输入在本骨架里为空。"""
from __future__ import annotations

from collections import Counter

from .llm import LLM, load_prompt, render
from .models import Daily
from .store import Store
from .textutil import short

SCENE_CN = {"lecture": "课堂/讲座", "meeting": "项目讨论", "one_on_one": "一对一交流", "casual": "日常对话",
            "media": "媒体背景音", "mixed": "混合场景"}


def _takeaways(md: str, n: int = 2) -> list[str]:
    out = []
    for line in md.splitlines():
        if line.startswith("- ") and "mock 模式" not in line and not line.startswith("- 围绕"):
            out.append(line[2:].replace("**", "").strip())
        if len(out) >= n:
            break
    return out


def build_daily(store: Store, llm: LLM, date: str, calendar: list[dict], user_name: str = "用户",
                cutoff: str | None = None) -> tuple[Daily | None, list]:
    convs = store.list_conversations(f"{date}T00:00", f"{date}T23:59:59", with_utts=True, state="COMPLETED")
    excluded = []
    if cutoff:
        lim = f"{date}T{cutoff}"
        excluded = [c for c in convs if c.end_time > lim]
        convs = [c for c in convs if c.end_time <= lim]
    if not convs:
        return None, excluded
    events = [e for e in calendar if e["date"] == date]
    clist = [{"id": c.id, "time": c.start_time[11:16], "title": c.short_summary, "scene": c.scene} for c in convs]
    data = None
    if not llm.mock:
        prompt = render(load_prompt("daily"), user_name=user_name, date=date,
                        conversations=[{**x, "summary": c.summary[:1500]} for x, c in zip(clist, convs)],
                        calendar=events or "（无）", emails="（未接入）", locations="（未接入）", photos="（未接入）")
        prompt += "\n\n输出 JSON 字段：short_summary, summary, email_summary, calendar_summary。"
        data = llm.chat_json(prompt)
    if isinstance(data, dict) and data.get("summary"):
        d = Daily(date=date, short_summary=data.get("short_summary", ""), summary=data["summary"],
                  conversations_count=len(convs), conversations=clist,
                  calendar_summary=data.get("calendar_summary", ""))
    else:
        scenes = Counter(c.scene for c in convs)
        kw = Counter(k for c in convs for k in c.keywords[:5])
        top = [k for k, _ in kw.most_common(3)]
        parts = "、".join(f"{n} 段{SCENE_CN.get(s, s)}" for s, n in scenes.most_common())
        short_s = short(f"{parts}，围绕{'、'.join(top[:2]) or '日常'}推进的一天。", 40)
        body = ["# Summary"]
        for s, _ in scenes.most_common():
            items = [c for c in convs if c.scene == s]
            body.append(f"{SCENE_CN.get(s, s)}：" + "；".join(
                f"{c.start_time[11:16]}「{c.short_summary}」（{'、'.join(c.keywords[:3])}）" for c in items) + "。")
            body.append("")
        n_utt = sum(len(c.utterances) for c in convs)
        body += ["# Atmosphere", f"共 {len(convs)} 段对话、{n_utt} 句发言；以{SCENE_CN.get(scenes.most_common(1)[0][0])}为主。", "",
                 "# Key Takeaways"]
        for c in convs:
            body += [f"- {short(t, 80)}（{c.short_summary}）" for t in _takeaways(c.summary)]
        body += ["", "> mock 模式：由当天各段摘要拼接，不含输入以外的事实。"]
        cal = "；".join(f"{e['start']}–{e['end']} {e['title']}" for e in events)
        d = Daily(date=date, short_summary=short_s, summary="\n".join(body), conversations_count=len(convs),
                  conversations=clist, calendar_summary=f"日程：{cal}。均有对应录音。" if cal else "")
    store.save_daily(d)
    return d, excluded
