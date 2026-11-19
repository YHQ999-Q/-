"""主链路（§1）：原始语音流 → 静音切分 → 整段重转写 → 摘要 →（同一时刻）事实 + 待办建议 → 入索引/相关会话。"""
from __future__ import annotations

import json

from .config import FIXTURES_DIR, Settings, load_json
from .facts import extract_facts
from .llm import LLM
from .search import compute_related
from .segment import build_conversations
from .store import Store, now_iso
from .summarize import summarize
from .todos import suggest_for_conversation
from .transcribe import batch_retranscribe, load_input


def load_calendar(path=None) -> list[dict]:
    data = load_json(path or (FIXTURES_DIR / "calendar.json"), {"events": []})
    return data.get("events", [])


def ingest(store: Store, path: str, date: str | None = None) -> int:
    return store.add_stream(load_input(path, date))


def seed_facts(store: Store, path=None) -> int:
    from .models import Fact
    data = load_json(path or (FIXTURES_DIR / "facts_seed.json"), {"facts": []})
    have = {f.text for f in store.list_facts(include_rejected=True)}
    n = 0
    for f in data.get("facts", []):
        if f["text"] not in have:
            store.add_fact(Fact(text=f["text"], tags=f.get("tags", ["general"]), confirmed=bool(f.get("confirmed")),
                                status="confirmed" if f.get("confirmed") else "pending"))
            n += 1
    return n


def process(store: Store, llm: LLM, s: Settings, now: str | None = None, calendar=None, log=print) -> list:
    """切分未处理的语音流；只有 COMPLETED 的会话进入后续步骤，CAPTURING 的留在流里等下次。"""
    calendar = calendar if calendar is not None else load_calendar()
    convs = build_conversations(store.unsegmented_stream(), s.silence_gap_s, s.preroll_s, now)
    done = []
    for c in convs:
        if c.state != "COMPLETED":
            log(f"[segment] {c.id} 仍在录音（CAPTURING，{len(c.utterances)} 句），等静音满 {s.silence_gap_s}s 再处理")
            continue
        ids = [u.id for u in c.utterances]
        batch_retranscribe(c.utterances)
        summarize(c, llm, store.list_facts(), calendar, s.user_name, s.timezone)
        stamp = now_iso()                    # §3.4：事实/建议与摘要同一时刻写入
        c.updated_at = stamp
        store.save_conversation(c)
        store.mark_stream(ids, c.id)
        facts = extract_facts(c, llm, store.list_facts(include_rejected=True), s.user_name)
        for f in facts:
            f.created_at = stamp
            f.id = store.add_fact(f)
        sugs = suggest_for_conversation(c, llm, store, s.timezone)
        for sg in sugs:
            sg.created_at = stamp
            sg.id = store.add_suggestion(sg)
        log(f"[process] {c.id}｜{c.start_time[5:16].replace('T', ' ')}–{c.end_time[11:16]}｜{len(c.utterances)} 句｜"
            f"场景={c.scene}｜「{c.short_summary}」｜规则 {json.dumps(c.rules_applied, ensure_ascii=False)}｜"
            f"事实 +{len(facts)}（pending）｜待办建议 +{len(sugs)}")
        done.append(c)
    if done:
        n = compute_related(store)
        log(f"[related] 关键词重叠预计算相关会话 {n} 条链接")
    return done
