"""会话摘要 + 场景模板（§3.3，提示词 §5.1）。

两条规则注入路径并存：
1. Bee 原机制：按 transcript 用 BM25 召回 top-k 已确认 facts（tag 含 transcription 的规则类优先）→ 是否生效靠碰运气；
2. 本复刻改进（§7.3#1）：场景分类后，把 config/scene_templates.json 的模板 + config/user_rules.json 中该场景的规则直接注入，100% 生效。
每次都把用到的规则记进 conversation.rules_applied，方便追踪"为什么这次没生效"（§7.2）。"""
from __future__ import annotations

import json
import re
from datetime import datetime

from .config import scene_templates, user_rules
from .llm import LLM, load_prompt, render
from .models import Conversation, Fact
from .search import BM25
from .textutil import VAGUE_TIME, keywords, short, tokenize

LECTURE_W = ["老师", "同学们", "这节课", "上课", "下课", "课件", "作业", "考试", "期末", "理论", "讲座",
             "汇报", "教材", "概念", "学派", "主义", "原理", "PPT", "today we", "lecture"]
MEETING_W = ["项目", "分工", "需求", "方案", "产品", "上线", "demo", "Demo", "黑客松", "我们做", "接口",
             "开发", "Agent", "agent", "原型", "功能", "进度"]
ONE_W = ["咨询", "申请", "背景", "推荐信", "选校", "你可以", "建议你", "导师", "简历", "面试", "硕士", "雅思",
         "绩点", "你的", "你这个", "你要"]
CASUAL_W = ["吃饭", "好吃", "电影", "哈哈", "逛", "衣服", "外卖", "宿舍", "周末"]

ACTION_RE = re.compile(r"(作业|截止|提交|交上|deadline|DDL|ddl|考试|考核|期末|上传|下周|下次课|记得|别忘|"
                       r"不要迟到|别迟到|录课|报名|准备好|我会|我来|你来|负责|安排|之前要|前要|完成)")
SENSITIVE_RE = re.compile(r"(月经|经期|生病|医院|吃药|抑郁|恋爱|分手|男朋友|女朋友|宗教|政党|性生活)")


def detect_scene(conv: Conversation, calendar_event: dict | None = None) -> str:
    if calendar_event and calendar_event.get("kind") in ("course", "lecture"):
        return "lecture"
    text = "\n".join(u.text for u in conv.utterances)
    score = {s: sum(text.count(w) for w in ws) for s, ws in
             [("lecture", LECTURE_W), ("meeting", MEETING_W), ("one_on_one", ONE_W), ("casual", CASUAL_W)]}
    best = max(score, key=lambda k: score[k])
    return best if score[best] >= 3 else "casual"


def find_calendar_event(conv: Conversation, calendar: list[dict]) -> dict | None:
    """课表/日历对齐（§7.1 "按课表自动对齐"）：会话与日历事件时间重叠最多的一条。"""
    s, e = conv.start_time, conv.end_time or conv.start_time
    best, overlap = None, 0.0
    for ev in calendar:
        es, ee = f"{ev['date']}T{ev['start']}:00", f"{ev['date']}T{ev['end']}:00"
        lo, hi = max(s, es), min(e, ee)
        if lo < hi:
            ov = (datetime.fromisoformat(hi) - datetime.fromisoformat(lo)).total_seconds()
            if ov > overlap:
                best, overlap = ev, ov
    return best


def recall_rule_facts(conv: Conversation, facts: list[Fact], k: int = 3) -> list[Fact]:
    """Bee 机制复现：用转写做查询，BM25 召回已确认 facts 的 top-k（§3.3 推断机制）。"""
    conf = [f for f in facts if f.confirmed]
    if not conf:
        return []
    bm = BM25([tokenize(f.text) for f in conf])
    q = tokenize(" ".join(u.text for u in conv.utterances[:200]))
    hits = bm.top(q, k=k)
    if not hits:
        return []
    top = hits[0][1]
    return [conf[i] for i, s in hits if s >= 0.25 * top]


def _template(scene: str) -> tuple[dict, str]:
    tpls = scene_templates()
    t = tpls.get(scene) or tpls.get("default", {})
    if "inherit" in t:
        t = tpls.get(t["inherit"], {})
    return t, tpls.get("version", "tpl-none")


def _sentences(conv: Conversation) -> list[tuple[str, str]]:
    return [(u.spoken_at[11:16], u.text.strip()) for u in conv.utterances if len(u.text.strip()) >= 6]


def _bold(s: str, kws: list[str]) -> str:
    for k in kws[:6]:
        if k in s and f"**{k}**" not in s:
            s = s.replace(k, f"**{k}**", 1)
    return s


def _top_sentences(sents, kws, n):
    scored = []
    for i, (t, s) in enumerate(sents):
        if len(s) < 15:
            continue
        hit = sum(1 for k in kws if k in s)
        if hit:
            scored.append((hit + min(len(s), 80) / 80, i))
    picked = sorted(i for _, i in sorted(scored, reverse=True)[:n])
    seen, out = set(), []
    for i in picked:
        key = sents[i][1][:12]
        if key not in seen:
            seen.add(key)
            out.append(sents[i])
    return out


def mock_summary(conv: Conversation, scene: str, event: dict | None, rules: list[str],
                 user_name: str) -> dict:
    """确定性规则实现：抽取式，只用转写里出现过的句子，不编造。"""
    sents = _sentences(conv)
    kws = keywords([s for _, s in sents], k=10)
    course = (event or {}).get("title") if scene == "lecture" else None
    k1, k2 = (kws + ["记录", "讨论"])[:2]
    if course:
        rest = [k for k in kws if k not in course and course not in k] + ["本节内容"]
        title = course if "：" in course else f"{course}：{rest[0]}" + (f"与{rest[1]}" if len(rest) > 2 else "")
    else:
        title = {"meeting": f"{k1}与{k2}项目讨论", "one_on_one": f"关于{k1}的一对一交流"}.get(
            scene, f"关于{k1}与{k2}的对话")
    title = short(title, 24)
    take = _top_sentences(sents, kws, 8)
    actions = [(t, s) for t, s in sents if ACTION_RE.search(s)][:6]
    mins = int((datetime.fromisoformat(conv.utterances[-1].spoken_at)
                - datetime.fromisoformat(conv.utterances[0].spoken_at)).total_seconds() // 60)
    tpl, _ = _template(scene)
    titles = [s["title"] for s in tpl.get("sections", [])]
    out = [f"# {title}", ""]

    def act_line(t, s):
        tag = "（日期待通知）" if VAGUE_TIME.search(s) else ""
        return f"- [{t}] {short(s, 70)}{tag}"

    if scene == "lecture":
        out += ["## 要记住的"] + ([act_line(t, s) for t, s in actions[:4]] or ["- 本节未提到作业、考核或截止。"]) + [""]
        out += ["## 本节课回答的问题", f"- 围绕 **{k1}**、**{k2}**" + (f"、**{kws[2]}**" if len(kws) > 2 else "") + " 展开。", ""]
        for n, k in enumerate(kws[:3], 1):
            lines = [s for _, s in take if k in s][:2] or [s for _, s in sents if k in s and len(s) > 12][:2]
            if lines:
                out += [f"### {n}. {k}"] + [f"- {_bold(short(s, 80), kws)}" for s in lines] + [""]
        if "复习自测" in titles:
            q_src = next((s for _, s in sents if k1 in s and len(s) > 15), "")
            out += ["## 复习自测", f"1. 用自己的话解释「{k1}」，它和「{k2}」是什么关系？",
                    f"   答案线索（原句）：{short(q_src, 60)}", ""]
    else:
        who = {"meeting": "项目讨论", "one_on_one": "一对一交流", "casual": "日常对话"}.get(scene, "对话")
        summary_p = (f"{user_name}参与了一段约 {mins} 分钟的{who}，主要围绕 {k1}、{k2}"
                     + (f" 和 {kws[2]}" if len(kws) > 2 else "") + "。")
        for sec in tpl.get("sections", []):
            st = sec["title"]
            if st == "Summary":
                out += ["## Summary", summary_p, ""]
            elif st == "Atmosphere":
                out += ["## Atmosphere", f"{len(conv.utterances)} 句发言、约 {mins} 分钟，"
                        + ("讨论密集。" if len(conv.utterances) / max(mins, 1) > 5 else "节奏平稳。"), ""]
            elif st == "Key Takeaways":
                out += ["## Key Takeaways"] + [f"- {_bold(short(s, 80), kws)}" for _, s in take] + [""]
            elif st == "Division of Work":
                div = [(t, s) for t, s in sents if re.search(r"(我来|你来|负责|分工|我做|你做|交给)", s)][:4]
                if div:
                    out += ["## Division of Work"] + [f"- [{t}] {short(s, 70)}" for t, s in div] + [""]
            elif st == "Action Items" and actions:
                out += ["## Action Items"] + [act_line(t, s) for t, s in actions[:5]] + [""]
            elif st == "Still worth clarifying":
                qs = [(t, s) for t, s in sents if s.endswith(("？", "?")) and any(k in s for k in kws)][:3]
                if qs:
                    out += ["## Still worth clarifying"] + [f"- {short(s, 60)}" for _, s in qs] + [""]
    out.append("> mock 模式：抽取式摘要，只用转写原句，用于验证流程；接真实模型后按 §5.1 生成。")
    return {"scene": scene, "short_summary": title, "summary_markdown": "\n".join(out).strip(),
            "keywords": kws[:10]}


def summarize(conv: Conversation, llm: LLM, facts: list[Fact], calendar: list[dict],
              user_name: str = "用户", timezone: str = "Asia/Shanghai") -> Conversation:
    event = find_calendar_event(conv, calendar)
    recalled = recall_rule_facts(conv, facts)
    scene = detect_scene(conv, event)
    cfg_rules = [r for r in user_rules().get("rules", []) if r.get("scene") == scene]
    tpl, tpl_ver = _template(scene)
    result = None
    if not llm.mock:
        prompt = render(load_prompt("summarize"), user_name=user_name,
                        start_local=conv.start_time.replace("T", " "),
                        end_local=(conv.end_time or "录音中").replace("T", " "), timezone=timezone,
                        location="（未提供）",
                        top_k_confirmed_facts_relevant_to_transcript="\n".join(f"- {f.text}" for f in recalled) or "（无）")
        prompt += ("\n\n【本复刻追加：场景模板与用户规则（配置注入，必须遵守）】\n"
                   f"日历/课表：{json.dumps(event, ensure_ascii=False) if event else '（无匹配）'}\n"
                   f"场景模板（按识别出的场景选用）：{json.dumps(scene_templates(), ensure_ascii=False)}\n"
                   f"用户规则（按场景）：{json.dumps(user_rules().get('rules', []), ensure_ascii=False)}\n"
                   f"\n【对话转写】\n{conv.transcript_text(limit=600)}")
        result = llm.chat_json(prompt)
        if result and result.get("scene") in scene_templates():
            scene = result["scene"]
            cfg_rules = [r for r in user_rules().get("rules", []) if r.get("scene") == scene]
    if not result or "summary_markdown" not in result:
        result = mock_summary(conv, scene, event, [r["text"] for r in cfg_rules], user_name)
    conv.scene = result.get("scene", scene)
    conv.short_summary = result["short_summary"]
    conv.summary = result["summary_markdown"]
    conv.keywords = [k for k in result.get("keywords", []) if k][:10]
    conv.template_version = f"{tpl_ver}/{conv.scene}"
    conv.rules_applied = ([f"config:{r['id']}" for r in cfg_rules]
                          + [f"recalled_fact:{f.id}" for f in recalled if "transcription" in f.tags])
    return conv
