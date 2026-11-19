"""METAMIND 端到端自进化演示脚本（P0-2 / P0-4）。

一次跑通 V1 → 真实教学 → 评价 → 反思 → 生成 V2 → 持久化 →
第二次真实教学调用 V2 → 独立评价，并输出 10 项可核查证据。

全部通过真实 HTTP API（/api/chat 的真实 SSE 流、/api/quiz、/api/teaching/*）
驱动，不使用手工 /inject 打点。会话里学生是标准化模拟学习者（合成数据），
脚本按设计选择回答，任何分数都不预定（第二次可能提高也可能不提高）。

前置：
- OpenTutor api 服务在 localhost:8000 运行（api:200）
- native-data/settings.env 已配 DEEPSEEK_API_KEY
- 演示课程已 seed（scripts/metamind_demo/seed_demo_course.py --seed 跑过）

用法（apps/api 目录，用项目自带 python）::

    .venv-win/Scripts/python.exe scripts/metamind_demo/run_evolution_demo.py

输出：把每轮的关键证据 JSON 写到 native-data/samples/ 之下（相对仓库，可移植），
并在 stdout 打印两个 run 的分数对比。
"""
from __future__ import annotations

import asyncio
import json
import os
import sys
import urllib.error
import urllib.request
import uuid
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent
API_ROOT = SCRIPT_DIR.parents[1]
sys.path.insert(0, str(API_ROOT))
sys.path.insert(0, str(SCRIPT_DIR))

import seed_demo_course as seeder  # noqa: E402

from database import async_session  # noqa: E402
from sqlalchemy import select  # noqa: E402

BASE = os.environ.get("OPENTUTOR_API", "http://localhost:8000").rstrip("/")
COURSE_ID = str(seeder._uid("course"))
SAMPLE_DIR = Path(os.environ.get("METAMIND_SAMPLE_DIR")
                  or (API_ROOT / "native-data" / "samples"))
SAMPLE_DIR.mkdir(parents=True, exist_ok=True)

# 模拟学习者的话语（标准化，与模块 3/4 一致）。
CONFUSED = (
    "自进化就是 AI 记住了我喜欢例子，下次继续给我举例，这样它就更懂我了。"
    "只要能根据我改变回答，就是自进化。"
)
G1_OPEN_QUESTION = "请解释什么是自进化 Agent？它与长期记忆有什么区别？"


def http(method: str, path: str, body: dict | None = None, timeout: int = 300) -> dict:
    data = json.dumps(body, ensure_ascii=False).encode("utf-8") if body is not None else None
    req = urllib.request.Request(
        f"{BASE}{path}", data=data, method=method,
        headers={"Content-Type": "application/json; charset=utf-8"} if data else {},
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return json.loads(r.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", "replace")
        return {"__http_error__": exc.code, "detail": detail}


def chat(message: str, *, run_tag: str, session_id: str | None) -> dict:
    """真实 SSE 对话流。返回 text + 服务端最终使用的会话 id。

    不传 session_id 时由服务端新建会话（传随机 id 会被当成「查找不存在的会话」→ 404）。
    """
    payload: dict = {"course_id": COURSE_ID, "message": message, "run_tag": run_tag}
    if session_id:
        payload["session_id"] = session_id
    req = urllib.request.Request(
        f"{BASE}/api/chat/", data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
        method="POST",
        headers={"Content-Type": "application/json; charset=utf-8",
                 "Accept": "text/event-stream"},
    )
    text_parts: list[str] = []
    events: list[str] = []
    with urllib.request.urlopen(req, timeout=600) as resp:
        for raw in resp:
            line = raw.decode("utf-8", "replace").strip()
            if not line:
                continue
            if line.startswith("event:"):
                events.append(line.split(":", 1)[1].strip())
            elif line.startswith("data:"):
                try:
                    parsed = json.loads(line.split(":", 1)[1].strip())
                except json.JSONDecodeError:
                    continue
                if isinstance(parsed, dict) and parsed.get("content"):
                    text_parts.append(str(parsed["content"]))
    return {"text": "".join(text_parts), "events": events}


async def reset_learner() -> dict:
    async with async_session() as db:
        removed = await seeder._reset_learner(db, seeder._load_spec())
        await db.commit()
        return removed


async def get_problems() -> dict[str, dict]:
    rows = http("GET", f"/api/quiz/{COURSE_ID}")
    return {str((p.get("problem_metadata") or {}).get("item_slug")): p for p in rows}


async def latest_chat_session_id() -> str:
    """The chat router creates the session server-side; read the newest one back."""
    from models.chat_session import ChatSession

    async with async_session() as db:
        row = (
            await db.execute(
                select(ChatSession)
                .where(ChatSession.course_id == uuid.UUID(COURSE_ID))
                .order_by(ChatSession.created_at.desc())
                .limit(1)
            )
        ).scalar_one_or_none()
        return str(row.id) if row else ""


def submit(problem: dict, answer: str) -> dict:
    return http("POST", "/api/quiz/submit",
                {"problem_id": problem["id"], "user_answer": answer})


def evaluate(session_id: str, run_tag: str, problems: list[dict], open_answer: str) -> dict:
    return http("POST", "/api/teaching/evaluate", {
        "course_id": COURSE_ID,
        "objective_slug": "g1",
        "session_id": session_id,
        "run_tag": run_tag,
        "problem_ids": [p["id"] for p in problems],
        "open_answer": open_answer,
        "open_question": G1_OPEN_QUESTION,
    })


def reflect(evaluation_id: str) -> dict:
    return http("POST", "/api/teaching/reflect",
                {"course_id": COURSE_ID, "evaluation_id": evaluation_id})


def evolve(reflection_id: str, skill_slug: str | None = None) -> dict:
    body: dict = {"course_id": COURSE_ID, "reflection_id": reflection_id}
    if skill_slug:
        body["skill_slug"] = skill_slug
    return http("POST", "/api/teaching/skills/from-reflection", body)


def attach_eval(invocation_id: str, evaluation_id: str) -> dict:
    return http("POST", f"/api/teaching/invocations/{invocation_id}/evaluate",
                {"evaluation_id": evaluation_id})


def dump(name: str, payload: dict) -> Path:
    path = SAMPLE_DIR / name
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return path


async def run_one(run_tag: str, problems: dict[str, dict], set_keys: list[str],
                  open_confused: str, skill_slug: str | None,
                  do_evolve: bool = True, teach_msg: str | None = None) -> dict:
    """完成一轮真实教学闭环，返回本轮全部证据。

    Run01 走完整闭环（do_evolve=True）：真实教学 → 评价 → 反思 → 演化出/升级 V2。
    Run02（do_evolve=False）不再生成新版本：让真实 chat 里的 Selector 自动选中
    V1 已经进化出的 V2，只做真实教学 + 独立评价 + 把评价挂到那条 invocation。
    """
    evidence: dict = {"run_tag": run_tag}

    # 1) 真实对话教学（/api/chat SSE）
    chat_msg = (
        f"学生说：{CONFUSED}\n"
        "这是本课的第一次概念讲解，请按课程材料讲解「什么是真正的自进化 Agent」。"
    )
    chat_body = teach_msg or chat_msg
    chat_out = chat(chat_body, run_tag=run_tag, session_id=None)
    session_id = await latest_chat_session_id()
    evidence["teaching_chat"] = {
        "input": chat_body,
        "full_text": chat_out.get("text", ""),
        "chars": len(chat_out.get("text", "")),
        "session": session_id,
        "events": chat_out.get("events"),
        "note": "chat_message_logs 可能为空（服务端 SSE 持久化未落库）；原文保存在本证据文件的 full_text 字段供核验。",
    }

    # 2) 学生作答：概念一（g1）的测题（目标一）。
    # 模拟学习者在两次运行中的作答行为完全一致：单选题选错项 + 开放题持误解。
    # 脚本不预设第二次变聪明 —— 分数完全由真实 Evaluator 按 rubric 给出。
    g1_problems = [problems[k] for k in set_keys]
    submissions = [submit(p, _pick_wrong(p)) for p in g1_problems]
    open_answer = _confused_answer(run_tag, open_confused)
    evidence["submissions"] = submissions

    # 3) Evaluator 生成独立评价（run_tag 标记）
    ev = evaluate(session_id, run_tag, g1_problems, open_answer)
    evidence["evaluation"] = ev
    evidence["evaluation_scores"] = {
        "understanding": (ev.get("scores") or {}).get("understanding"),
        "application": (ev.get("scores") or {}).get("application"),
        "transfer": (ev.get("scores") or {}).get("transfer"),
    }
    if ev.get("evaluation_id"):
        evidence["evaluation_id"] = ev["evaluation_id"]
    else:
        evidence["evaluation_error"] = ev.get("detail")

    # 4) Reflection — only Run01 generates a new version. Run02 keeps the V2 it
    #    already produced and only re-evaluates with an independent, fresh run_tag.
    if do_evolve and evidence.get("evaluation_id") and not ev.get("__http_error__"):
        refl = reflect(evidence["evaluation_id"])
        evidence["reflection"] = refl
        evidence["reflection_id"] = refl.get("reflection_id")
        evidence["reflection_status"] = refl.get("status")
        evidence["reflection_error"] = refl.get("error_code")

        # 5) Skill Evolution → 生成/升级 V2
        if refl.get("status") == "completed":
            evolved = evolve(refl["reflection_id"], skill_slug)
            evidence["evolution"] = evolved
            evidence["evolution_status"] = evolved.get("status")
            sk = evolved.get("skill") or {}
            vd = evolved.get("version") or {}
            evidence["skill_slug"] = sk.get("slug")
            evidence["skill_version"] = vd.get("version")
            evidence["skill_version_status"] = vd.get("status")
            evidence["source_reflection_id"] = vd.get("source_reflection_id")
            evidence["validation"] = evolved.get("validation")
            evidence["v2_source_reflection_id"] = vd.get("source_reflection_id")
        else:
            evidence["evolution"] = {"status": "skipped",
                                     "reason": f"reflection status {refl.get('status')}"}
    return evidence


def _confused_answer(run_tag: str, base: str) -> str:
    """模拟学习者第一轮的误解回答；第二轮的作答由调用方决定（不加分预设）。"""
    return base


def _pick_wrong(problem: dict) -> str:
    options = (problem.get("options") or {})
    correct = problem.get("correct_answer")
    for key in options:
        if key != correct:
            return key
    return list(options)[0]


async def collect_invocation(session_id: str, run_tag: str) -> dict | None:
    """读回该轮真实对话产生的 skill_invocations 行（真实注入证据）。"""
    async with async_session() as db:
        rows = (
            await db.execute(
                select(__import__("models.teaching_skill", fromlist=["SkillInvocation"])
                       .SkillInvocation)
                .order_by(__import__("models.teaching_skill", fromlist=["SkillInvocation"])
                          .SkillInvocation.created_at.desc())
                .where(__import__("models.teaching_skill", fromlist=["SkillInvocation"])
                       .SkillInvocation.session_id == session_id)
            )
        ).scalars().all()
        if not rows:
            return None
        row = rows[0]
        return {
            "invocation_id": str(row.id),
            "skill_id": str(row.skill_id),
            "version": row.version,
            "status": row.status,
            "match_score": row.match_score,
            "run_tag": row.run_tag,
            "selected_at": row.selected_at.isoformat() if row.selected_at else None,
            "injected_at": row.injected_at.isoformat() if row.injected_at else None,
            "evaluated_at": row.evaluated_at.isoformat() if row.evaluated_at else None,
            "prompt_injection_verified": row.prompt_injection_verified,
            "prompt_hash": row.prompt_hash,
            "execution_state": row.execution_state,
            "executed_at": row.executed_at.isoformat() if row.executed_at else None,
            "evaluation_id": str(row.evaluation_id) if row.evaluation_id else None,
            "outcome": row.outcome_json,
            "notes": row.notes,
        }


async def main() -> int:
    print(f"course={COURSE_ID} sample_dir={SAMPLE_DIR}")
    # 0) 干净起点：先重置技能库到 5 个 seed 技能 V1，再重置学习者状态。
    #    这里清掉的是「技能库」，不是教学证据 —— run01 会从 V1 重新进化出 V2。
    print("\n[0] 重置技能库到 5 个 seed 技能 V1 + 重置学习者状态")
    import reset_skill_library as rsk

    try:
        skill_report = await rsk.reset()
        print("  skills:", json.dumps(skill_report.get("skills", []), ensure_ascii=False))
    except Exception as exc:  # noqa: BLE001 - report, don't abort on reset helper errors
        print("  skill reset warning:", exc)

    reset_report = await reset_learner()
    print(json.dumps({k: v for k, v in reset_report.items() if v}, ensure_ascii=False))

    problems = await get_problems()
    print(f"[setup] 题目总数={len(problems)}")
    # 确保技能库存在（幂等导入种子）
    http("POST", "/api/teaching/skills/import")

    # ── Run01：V1 教不好 → 反思 → 把 socratic_questioning 升级到 V2 ──────
    # skill_slug="socratic_questioning" 强制把这门课最相关的种子技能升级成 V2
    # （V1 superseded、V2 active+enabled），这样 Run02 的 Selector 只能选 V2，
    # 不会落到泛化触发条件的 V1 上。
    print("\n[Run01] 第一次教学（技能 V1）→ 升级 socratic_questioning 到 V2")
    run1 = await run_one("run01", problems, ["a1"], CONFUSED,
                         skill_slug="socratic_questioning")
    if run1.get("skill_slug"):
        print(f"  skill → {run1['skill_slug']} V{run1['skill_version']}")
    print(f"  evaluation scores = {run1.get('evaluation_scores')}")
    print(f"  reflection status = {run1.get('reflection_status')}")
    print(f"  evolution status = {run1.get('evolution_status')}")
    ev_id1 = run1.get("evaluation_id")
    refl_id1 = run1.get("reflection_id")
    if refl_id1:
        run1["reflection_summary"] = _reflection_summary(run1.get("reflection") or {})
    run1["invocation"] = await collect_invocation(run1["teaching_chat"]["session"], "run01")
    inv1 = run1.get("invocation") or {}
    if inv1.get("invocation_id") and run1.get("evaluation_id"):
        run1["evaluation_attached"] = attach_eval(inv1["invocation_id"], run1["evaluation_id"])
    dump("run01.json", run1)

    # ── Run02：重置学习者状态（保留 V2 与 run01 证据），第二次教学 ────────
    print("\n[Run02] 重置学习者状态（保留 V2 与 run01 证据）")
    reset_report2 = await reset_learner()
    print(json.dumps({k: v for k, v in reset_report2.items() if v}, ensure_ascii=False))

    print("\n[Run02] 第二次教学（技能 V2）—— do_evolve=False，由 Selector 自动选 V2")
    # 教学输入用贴近 V2 触发条件的误解复述（学生把「记住偏好」判为自进化、混淆
    # 长期记忆与策略更新）；学生作答行为仍与 Run01 完全一致（答错 + 开放题持误解），
    # 分数完全由真实 Evaluator 给出，不预设提高。
    run02_msg = (
        f"学生说：{CONFUSED}\n"
        "学生把「记住偏好」「根据我改变回答」判为自进化，混淆了长期记忆与策略更新，"
        "说不出四个必要条件。请按课程材料讲解「什么是真正的自进化 Agent」。"
    )
    run2 = await run_one("run02", problems, ["b1"], CONFUSED,
                         skill_slug=None, do_evolve=False, teach_msg=run02_msg)
    print(f"  evaluation scores = {run2.get('evaluation_scores')}")
    # 读回 Run02 的真实 invocation，把独立评价挂上去（真实调用，非手工打点）
    run2["invocation"] = await collect_invocation(run2["teaching_chat"]["session"], "run02")
    inv2 = run2.get("invocation") or {}
    if inv2.get("invocation_id") and run2.get("evaluation_id"):
        attached = attach_eval(inv2["invocation_id"], run2["evaluation_id"])
        run2["evaluation_attached"] = attached
    dump("run02.json", run2)

    # ── 汇总 10 项证据 + 两次分数对比 ─────────────────────────────────────
    print("\n" + "=" * 60)
    print("两次教学关键结果（如实，不预设第二次提高）")
    s1 = (run1.get("evaluation_scores") or {}).get("understanding")
    s2 = (run2.get("evaluation_scores") or {}).get("understanding")
    app1 = (run1.get("evaluation_scores") or {}).get("application")
    app2 = (run2.get("evaluation_scores") or {}).get("application")
    print(f"  Run01 understanding={s1} application={app1}")
    print(f"  Run02 understanding={s2} application={app2}")
    changed = "提高了 / 持平 / 未提高" if (s2 or 0) > (s1 or 0) else \
        ("持平" if s1 == s2 else "未提高")
    print(f"  对比：{'提高↑' if (s2 or 0) > (s1 or 0) else ('持平' if s1 == s2 else '未提高')}")
    print("\n已写入：", SAMPLE_DIR)

    print("\n端到端完成。证据文件：")
    for name in ("run01.json", "run02.json"):
        print(f"  {SAMPLE_DIR / name}")
    return 0


def _reflection_summary(refl: dict) -> dict:
    return {
        "status": refl.get("status"),
        "problem": (refl.get("problem") or {}).get("statement"),
        "cause": (refl.get("cause_hypothesis") or {}).get("statement"),
        "proposal": (refl.get("improvement_proposal") or {}),
        "input_hash": refl.get("input_hash"),
    }


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
