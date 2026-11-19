"""命令行入口：python -m beeagent <命令>。"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime
from pathlib import Path

from . import chat as chat_mod
from . import todos as todos_mod
from .config import DATA_DIR, FIXTURES_DIR, get_settings
from .daily import build_daily
from .grow import run_grow
from .journal import process_journal
from .llm import LLM
from .pipeline import ingest, load_calendar, process, seed_facts
from .scheduler import default_daily_date
from .search import search
from .store import Store

OUT_DIR = DATA_DIR / "outputs"
DEMO_NOW = "2026-11-14T03:23:00"      # Bee Grow 批量时刻（§3.7）


def _ctx(db: str | None = None):
    s = get_settings()
    if db:
        s.db_path = Path(db)
    return s, LLM(s), Store(s.db_path)


def _hr(title: str):
    print(f"\n── {title} " + "─" * max(4, 40 - len(title) * 2))


def cmd_status(store: Store, llm: LLM):
    c = store.counts()
    facts = store.list_facts()
    sug = store.list_suggestions(None)
    print(f"模式：{llm.mode}｜数据库：{store.path}")
    print(f"会话 {c['conversations']}｜发言 {c['utterances']}（未切分流 {len(store.unsegmented_stream())}）｜"
          f"事实 {len(facts)}（已确认 {sum(f.confirmed for f in facts)} / 待确认 {sum(f.status == 'pending' for f in facts)}）")
    print(f"待办建议 {len(sug)}（open {sum(s.status == 'open' for s in sug)} / accepted {sum(s.status == 'accepted' for s in sug)}"
          f" / dismissed {sum(s.status == 'dismissed' for s in sug)}）｜待办 {c['todos']}｜每日回顾 {c['dailies']}｜"
          f"洞察 {c['insights']}｜语音笔记 {c['journals']}")
    for cv in store.list_conversations():
        print(f"  {cv.start_time[:16].replace('T', ' ')}  {cv.state:<9} {cv.scene:<10} {cv.short_summary}")


def print_facts(store: Store):
    for f in store.list_facts(include_rejected=True):
        mark = {"confirmed": "✓", "pending": "?", "rejected": "✗"}[f.status]
        print(f"  #{f.id} {mark} [{','.join(f.tags)}] {f.text[:70]}{'…' if len(f.text) > 70 else ''}"
              + (f"  ← {f.source_conversation_id}" if f.source_conversation_id else ""))


def print_suggestions(store: Store, status="open"):
    for s in store.list_suggestions(status):
        print(f"  #{s.id} {s.emoji} {s.text}  [{s.kind}]" + (f"  ⏰{s.alarm_at}" if s.alarm_at else ""))
        print(f"       {s.details}")


def print_daily(d):
    print(f"{d.date}｜{d.short_summary}｜会话 {d.conversations_count} 段")
    print(d.summary)


def print_insights(ins):
    for i in ins:
        print(f"  ◆ {i.title}\n    {i.description}\n    → {i.suggested_actions[0]}")


def run_demo(args):
    db = DATA_DIR / "demo.db"
    s, llm, store = _ctx(str(db))
    store.reset()
    print(f"Bee agent 复刻 demo｜模式：{llm.mode}｜静音阈值 {s.silence_gap_s}s，预录回填 {s.preroll_s}s｜数据库 {db}")
    _hr("1 ingest：读入脱敏转写，作为一条连续语音流")
    n = ingest(store, str(FIXTURES_DIR / "raw"))
    print(f"[ingest] {n} 句 utterance（realtime=true，speaker 全为 Unknown，与 Bee 一致）")
    print(f"[seed] 预置已确认事实 {seed_facts(store)} 条（含 1 条用户写进 Bee 的课堂纪要规则）")
    _hr("2 segment → summarize → facts → todo suggestions")
    convs = process(store, llm, s, now=DEMO_NOW)
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    for c in convs:
        (OUT_DIR / f"{c.id}.md").write_text(c.summary, encoding="utf-8")
    lec = next((c for c in convs if c.scene == "lecture" and "日期待通知" in c.summary), convs[0])
    print(f"\n[示例摘要] {lec.id}（全部摘要已写到 {OUT_DIR}/）")
    print("\n".join(lec.summary.splitlines()[:14]))
    _hr("3 facts（每段 ≤3，pending）与待办建议")
    print_facts(store)
    pend = [f for f in store.list_facts() if f.status == "pending"]
    if pend:
        store.set_fact_status(pend[0].id, "confirmed")
        print(f"[facts confirm] #{pend[0].id} → confirmed（之后的摘要会召回它）")
    print_suggestions(store)
    open_s = store.list_suggestions("open")
    if open_s:
        tid = todos_mod.accept(store, open_s[0].id)
        print(f"[todos accept] 建议 #{open_s[0].id} → 待办 #{tid}（保留 emoji 前缀）")
    pro = todos_mod.suggest_proactive(store, llm, DEMO_NOW, s.timezone)
    for sg in pro:
        sg.created_at = DEMO_NOW
        store.add_suggestion(sg)
    print(f"[todos proactive] 主动式建议 +{len(pro)}：" + "；".join(f"{x.emoji} {x.text}" for x in pro))
    _hr("4 run-daily（Bee ≈20:20）")
    cal = load_calendar()
    for date in sorted({c.start_time[:10] for c in convs}):
        d, _ = build_daily(store, llm, date, cal, s.user_name)
        if d:
            print(f"[daily] {d.date}｜{d.short_summary}｜{d.conversations_count} 段")
    _hr("5 run-grow（Bee ≈03:23，72h 窗口）")
    ins, info = run_grow(store, llm, DEMO_NOW)
    print(f"[grow] {info}｜生成 {len(ins)} 条，每条恰好 1 个行动")
    for i in ins:
        print(f"  ◆ {i.title}\n    → {i.suggested_actions[0]}")
    _hr("6 search（BM25）")
    q = "多尔四R"
    for h in search(store, q, limit=3):
        print(f"  [{h.kind}] {h.date[:16]}「{h.title}」score={h.score} 覆盖={h.coverage}\n     {h.snippet[:80]}")
    _hr("7 chat（工具调用；找不到不编造）")
    for question in ["录课那天有什么要求？", "我上次说的火星移民计划是什么？", "11月12日我做了什么？"]:
        ans, trace = chat_mod.ask(store, llm, question, now=DEMO_NOW, user_name=s.user_name)
        print(f"Q：{question}\n  工具：{' → '.join(t['tool'] for t in trace) or '（无）'}")
        print("  A：" + ans.replace("\n", "\n     "))
    _hr("8 journal（语音笔记）")
    j = process_journal(store, llm, "嗯那个，明天要把黑客松的演示视频剪完，然后然后记得周五前交比较教育的小组汇报。")
    print("  " + json.dumps(j.aiResponse, ensure_ascii=False))
    _hr("9 status")
    cmd_status(store, llm)
    store.close()


def main(argv=None):
    p = argparse.ArgumentParser(prog="beeagent", description="Bee second-brain agent 复刻（最小可跑骨架）")
    p.add_argument("--db", help="SQLite 路径（默认 data/beeagent.db）")
    sub = p.add_subparsers(dest="cmd", required=True)
    sub.add_parser("demo", help="一键端到端演示（离线可跑）")
    a = sub.add_parser("ingest", help="读入转写（txt/JSON 文件或目录）到原始语音流")
    a.add_argument("path")
    a.add_argument("--date", help="txt 里只有 HH:MM:SS 时指定日期 YYYY-MM-DD")
    a.add_argument("--seed-facts", action="store_true", help="同时导入 fixtures/facts_seed.json")
    a = sub.add_parser("process", help="切分 + 摘要 + 事实 + 待办建议")
    a.add_argument("--now", help="当前时间 ISO（判断会话是否已静音结束），默认不判断，全部视为 COMPLETED")
    a = sub.add_parser("facts", help="事实：list / confirm ID / reject ID")
    a.add_argument("action", choices=["list", "confirm", "reject"])
    a.add_argument("id", nargs="?", type=int)
    a = sub.add_parser("todos", help="待办：list / suggest [--proactive] / accept ID / dismiss ID")
    a.add_argument("action", choices=["list", "suggest", "accept", "dismiss"])
    a.add_argument("id", nargs="?", type=int)
    a.add_argument("--proactive", action="store_true")
    a.add_argument("--now")
    for name in ("daily", "run-daily"):
        a = sub.add_parser(name, help="生成并显示每日回顾（Bee ≈20:20）")
        a.add_argument("--date")
        a.add_argument("--cutoff", help="例如 20:20：复现 Bee，之后结束的会话不进当天回顾")
    for name in ("grow", "run-grow"):
        a = sub.add_parser(name, help="生成 Grow 洞察（Bee ≈03:23，72h 窗口）")
        a.add_argument("--now")
        a.add_argument("--list", action="store_true", help="只列出已有洞察，不生成")
    a = sub.add_parser("search", help="BM25 检索")
    a.add_argument("query")
    a.add_argument("--filter", default="all", choices=["all", "conversations", "daily", "facts", "journals"])
    a.add_argument("--mode", default="keyword", choices=["keyword", "semantic"])
    a.add_argument("--limit", type=int, default=5)
    a = sub.add_parser("chat", help="Chat agent：交互或 --ask")
    a.add_argument("--ask")
    a.add_argument("--now")
    a = sub.add_parser("journal", help="处理一条语音笔记文本")
    a.add_argument("text")
    sub.add_parser("status")
    sub.add_parser("reset", help="清空数据库")
    a = sub.add_parser("show", help="显示一段会话摘要")
    a.add_argument("id")
    args = p.parse_args(argv)

    if args.cmd == "demo":
        return run_demo(args)
    s, llm, store = _ctx(args.db)
    if args.cmd == "ingest":
        n = ingest(store, args.path, args.date)
        print(f"[ingest] {n} 句进入原始语音流")
        if args.seed_facts:
            print(f"[seed] 已确认事实 +{seed_facts(store)}")
    elif args.cmd == "process":
        done = process(store, llm, s, now=args.now)
        print(f"完成 {len(done)} 段会话")
    elif args.cmd == "facts":
        if args.action == "list":
            print_facts(store)
        else:
            if args.id is None:
                sys.exit("需要事实 id")
            ok = store.set_fact_status(args.id, "confirmed" if args.action == "confirm" else "rejected")
            print(("已" + ("确认" if args.action == "confirm" else "拒绝") + f" #{args.id}") if ok else f"没有 #{args.id}")
    elif args.cmd == "todos":
        if args.action == "list":
            for t in store.list_todos():
                print(f"  #{t.id} {'[x]' if t.completed else '[ ]'} {t.text}" + (f"  ⏰{t.alarm_at}" if t.alarm_at else ""))
            print("待办建议：")
            print_suggestions(store)
        elif args.action == "suggest":
            if args.proactive:
                now = args.now or datetime.now().isoformat(timespec="seconds")
                new = todos_mod.suggest_proactive(store, llm, now, s.timezone)
                for sg in new:
                    store.add_suggestion(sg)
                print(f"主动式建议 +{len(new)}")
            print_suggestions(store)
        elif args.action == "accept":
            tid = todos_mod.accept(store, args.id)
            print(f"已接受 → 待办 #{tid}" if tid else "没有这条 open 建议")
        else:
            print("已忽略" if todos_mod.dismiss(store, args.id) else "没有这条 open 建议")
    elif args.cmd in ("daily", "run-daily"):
        date = args.date or default_daily_date()
        d, excluded = build_daily(store, llm, date, load_calendar(), s.user_name, args.cutoff)
        for c in excluded:
            print(f"[cutoff {args.cutoff}] 「{c.short_summary}」结束于 {c.end_time[11:16]}，不进当天回顾（复现 Bee §6.4）")
        print_daily(d) if d else print(f"{date} 没有已完成的会话")
    elif args.cmd in ("grow", "run-grow"):
        if args.list:
            print_insights(store.list_insights())
        else:
            ins, info = run_grow(store, llm, args.now or datetime.now().isoformat(timespec="seconds"))
            print(f"[grow] {info}｜{len(ins)} 条")
            print_insights(ins)
    elif args.cmd == "search":
        hits = search(store, args.query, args.filter, args.limit, mode=args.mode)
        if not hits:
            print("没找到")
        for h in hits:
            note = f"（{h.extra['note']}）" if h.extra.get("note") else ""
            print(f"[{h.kind}] {h.id}｜{h.date[:16]}｜{h.title}｜score={h.score} 覆盖={h.coverage}{note}\n    {h.snippet[:100]}")
    elif args.cmd == "chat":
        if args.ask:
            ans, trace = chat_mod.ask(store, llm, args.ask, now=args.now, user_name=s.user_name)
            print(f"[工具] {' → '.join(t['tool'] for t in trace) or '（无）'}\n{ans}")
        else:
            print(f"Chat（{llm.mode}）。输入问题，空行或 Ctrl-D 退出。")
            hist = []
            while True:
                try:
                    q = input("你> ").strip()
                except EOFError:
                    break
                if not q:
                    break
                ans, trace = chat_mod.ask(store, llm, q, now=args.now, user_name=s.user_name, history=hist)
                hist += [{"role": "user", "content": q}, {"role": "assistant", "content": ans}]
                print(f"[工具] {' → '.join(t['tool'] for t in trace) or '（无）'}\nBee> {ans}")
    elif args.cmd == "journal":
        print(json.dumps(process_journal(store, llm, args.text).aiResponse, ensure_ascii=False, indent=1))
    elif args.cmd == "status":
        cmd_status(store, llm)
    elif args.cmd == "reset":
        store.reset()
        print("已清空")
    elif args.cmd == "show":
        c = store.get_conversation(args.id)
        print(c.summary if c else "没有这段会话")
        if c and c.suggested_links:
            print("\n相关会话：" + "；".join(f"{x['short_summary']}({x['score']})" for x in c.suggested_links))
    store.close()
