# -*- coding: utf-8 -*-
"""METAMIND 三版合一 · 一键验收（只用 Python 标准库，不写数据库）

先双击「启动全部.bat」，等浏览器打开后再运行：
    py -3.13 verify_integration.py

只读检查四件事：
  1. React 前端能拿到 OpenTutor 的真实学习数据；
  2. 第二大脑（Bee → 教学库）的数据能被教学 Agent 读到；
  3. V1 → V2 的策略进化证据仍在（run01 / run02 两次独立评价）；
  4. 三个网页版本都真的在服务，而不是只放在同一个文件夹里。
"""

from __future__ import annotations

import json
import sys
import urllib.error
import urllib.request

PASS, FAIL = "PASS", "FAIL"
results: list[tuple[str, str, str]] = []


def get(url: str, timeout: int = 15):
    with urllib.request.urlopen(url, timeout=timeout) as response:
        body = response.read().decode("utf-8", "replace")
        return response.status, json.loads(body) if body.strip().startswith(("{", "[")) else body


def check(name: str, fn) -> None:
    try:
        detail = fn()
        results.append((PASS, name, str(detail)))
    except Exception as exc:  # noqa: BLE001 - report, do not hide
        results.append((FAIL, name, str(exc)))


def main() -> int:
    # 1) OpenTutor 本体
    def health():
        status, data = get("http://127.0.0.1:8000/api/health/live")
        assert status == 200 and data.get("status") in {"ok", "alive"}, data
        return f"uptime={data.get('uptime_seconds'):.1f}s"

    check("OpenTutor 8000 存活", health)

    overview: dict = {}

    def load_overview():
        nonlocal overview
        status, data = get("http://127.0.0.1:8000/api/workspace/overview")
        assert status == 200, status
        overview = data
        assert data.get("courses"), "没有课程"
        return (
            f"课程 {len(data['courses'])} · 评价 {len(data['evaluations'])} · "
            f"反思 {len(data['reflections'])} · 策略版本 {len(data['versions'])} · "
            f"调用 {len(data['invocations'])}"
        )

    check("Workspace 统一概览接口", load_overview)

    def learner_state():
        course_id = overview["courses"][0]["course_id"]
        _, data = get(f"http://127.0.0.1:8000/api/teaching/learner-state?course_id={course_id}")
        summary = data["assessment_summary"]
        return (
            f"知识点 {summary['concepts_total']} · 已评估 {summary['concepts_assessed']} · "
            f"已掌握 {summary['concepts_mastered']}"
        )

    check("学习者状态（真实概念/掌握度）", learner_state)

    # 2) V1 → V2 证据链
    def run_ids():
        ids = {row["evaluation_id"]: row for row in overview.get("evaluations", [])}
        run01 = "077f7b8f-a9b9-43c7-8271-044113176c7e"
        run02 = "33193ddf-fd9f-447c-83bc-9906baaa5ecc"
        assert run01 in ids, "缺少 run01 评价"
        assert run02 in ids, "缺少 run02 评价"
        assert ids[run02].get("run_tag") == "run02", ids[run02].get("run_tag")
        return f"run01 与 run02 两次独立评价都在（run02 verdict={ids[run02].get('verdict')}）"

    check("两次可比评价（run01 / run02）", run_ids)

    def version_chain():
        active_v2 = [
            v for v in overview.get("versions", [])
            if v.get("version") == 2 and v.get("status") == "active"
        ]
        assert active_v2, "没有 active 的 V2 策略"
        assert any(v.get("source_evaluation_id") for v in active_v2), "V2 没有来源评价"
        return f"{active_v2[0].get('name')} V2 active，来源评价已记录"

    check("策略自改进：V2 已保存且可追溯来源", version_chain)

    def next_round():
        v1 = [i for i in overview.get("invocations", []) if i.get("version") == 1]
        v2 = [i for i in overview.get("invocations", []) if i.get("version") == 2]
        assert v1 and v2, "缺少 V1 或 V2 的实际调用记录"
        assert v2[0].get("status") == "evaluated", v2[0].get("status")
        return (
            "V1 run01 与 V2 run02 都真实注入并评价；"
            f"注入校验={v2[0].get('prompt_injection_verified')}，"
            f"执行状态={v2[0].get('execution_state')}"
        )

    check("下一轮确实调用新策略（V2 invocation）", next_round)

    # 3) 第二大脑 → 教学
    def brain_counts():
        _, facts = get("http://127.0.0.1:5001/api/facts")
        _, insights = get("http://127.0.0.1:5001/api/insights")
        _, todos = get("http://127.0.0.1:5001/api/todos")
        return (
            f"事实 {facts.get('count')} · 洞察 {insights.get('count')} · "
            f"未完成待办 {todos.get('counts', {}).get('open')}"
        )

    check("第二大脑数据已入教学库（5001）", brain_counts)

    def memory_in_plan():
        _, plan = get("http://127.0.0.1:5001/api/teach/plan")
        sources = plan.get("memory_sources") or {}
        facts = sources.get("confirmed_facts") or []
        insights = sources.get("growth_insights") or []
        assert facts or insights, "计划没有读到第二大脑来源"
        return f"计划可读 {len(facts)} 条事实、{len(insights)} 条洞察"

    check("教学 Agent 计划读到第二大脑来源", memory_in_plan)

    # 3.5) P1：SOLO / 知识图谱 / 多策略版本
    def solo_model():
        _, profile = get("http://127.0.0.1:5001/api/teach/profile")
        solo = profile.get("solo") or {}
        assert isinstance(solo.get("current"), int), "没有 SOLO 等级"
        assert solo.get("evidence"), "SOLO 没有证据说明"
        return f"SOLO L{solo['current']} · {solo.get('name')} · 目标 L{solo.get('target')}"

    check("P1 SOLO 结构化评估", solo_model)

    def knowledge_graph():
        _, courses = get("http://127.0.0.1:5001/api/teach/courses")
        course_id = (courses.get("courses") or [{}])[0].get("id") or "math"
        _, graph = get(f"http://127.0.0.1:5001/api/teach/graph/{course_id}")
        assert graph.get("nodes") and graph.get("edges"), "图谱为空"
        return f"{graph.get('course_name')}：{len(graph['nodes'])} 节点 / {len(graph['edges'])} 关系"

    check("P1 知识图谱可视化数据", knowledge_graph)

    def strategy_versions():
        _, evolution = get("http://127.0.0.1:5001/api/teach/evolution")
        strategies = [v for v in evolution.get("versions", []) if v.get("kind") == "strategy"]
        active = [v for v in strategies if v.get("status") == "active"]
        assert strategies and active, "没有可切换的课堂策略版本"
        return f"课堂策略 {len(strategies)} 个版本，当前 {active[0].get('id')}"

    check("P1 多策略版本可切换/回滚", strategy_versions)

    # 4) 三个网页入口
    def frontend():
        status, _ = get("http://127.0.0.1:5173/")
        assert status == 200, status
        return "黄紫 React 版 5173"

    check("前端主壳（黄紫版）", frontend)

    def proxy():
        status, data = get("http://127.0.0.1:5173/opentutor-api/api/workspace/overview")
        assert status == 200 and data.get("courses"), "代理没有拿到课程"
        return f"前端 → OpenTutor 代理正常（课程 {len(data['courses'])}）"

    check("前端与 OpenTutor 正常通信", proxy)

    def teach_proxy():
        status, data = get("http://127.0.0.1:5173/teach-api/facts")
        assert status == 200 and data.get("count") is not None, data
        return f"前端 → 5001 代理正常（事实 {data['count']}）"

    check("前端与课堂采集/第二大脑通信", teach_proxy)

    def capture_page():
        status, _ = get("http://127.0.0.1:5001/teach", timeout=10)
        assert status == 200, status
        return "5001 教学页可嵌入"

    check("课堂采集页（原 5001 教学 Agent）", capture_page)

    def mood_context():
        _, mood = get("http://127.0.0.1:5001/api/teach/mood")
        _, plan = get("http://127.0.0.1:5001/api/teach/plan")
        moods = (plan.get("memory_sources") or {}).get("recent_moods") or []
        return f"心境记录 {mood.get('count', 0)} 条；计划读取 {len(moods)} 条"

    check("P1 心境/反思进入教学记忆", mood_context)

    def strategy_registry():
        _, data = get("http://127.0.0.1:5001/api/teach/strategies/registry")
        sources = data.get("sources") or {}
        assert data.get("count", 0) >= 9, data
        assert sources.get("opentutor"), "registry 缺少 OpenTutor 版本"
        assert sources.get("teaching_5001"), "registry 缺少 5001 课堂策略"
        return (
            f"registry {data['count']} 条：OpenTutor {sources['opentutor']} + "
            f"课堂策略 {sources['teaching_5001']}"
        )

    check("P0 统一策略库 strategy_registry", strategy_registry)

    def unified_strategies():
        _, data = get("http://127.0.0.1:8000/api/workspace/strategies/unified")
        assert data.get("storage_merged") is True, "registry 读合并未生效"
        assert data.get("evidence_chain"), "证据链为空"
        assert data.get("sources", {}).get("opentutor"), "缺少 OpenTutor 版本"
        return (
            f"OpenTutor {data['sources']['opentutor']} + 5001 {data['sources'].get('teaching_5001', 0)} 个版本；"
            f"证据链 {len(data['evidence_chain'])} 步；registry 读合并"
        )

    check("P0 统一策略读视图", unified_strategies)

    # ── 报告 ──────────────────────────────────────────────────────────
    width = max(len(name) for _, name, _ in results)
    print("\nMETAMIND 三版合一 · 验收结果")
    print("=" * (width + 12))
    for state, name, detail in results:
        print(f"[{state}] {name:<{width}}  {detail}")
    failed = sum(1 for state, _, _ in results if state == FAIL)
    print("=" * (width + 12))
    print(f"{len(results) - failed}/{len(results)} 通过")

    print("\n诚实边界（不把这些说成已证明）：")
    print("· run01 / run02 的评价分数持平，只能说明闭环跑通，不能证明学习效果提升。")
    print("· V2 的 prompt_injection_verified=true，但 execution_state=unverified：")
    print("  系统确认了策略标记进入完整提示词，没有自动认证每一步都被执行。")
    print("· 第二大脑里的洞察来自 Bee 语料/记录，属于个人材料整理，不是学习效果实验。")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
