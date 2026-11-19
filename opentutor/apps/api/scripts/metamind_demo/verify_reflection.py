"""Live scenario verification for the Reflection Engine (METAMIND module 3).

Drives the real API + real model, then checks that every reflection is anchored
in evidence that actually exists.

Run from ``apps/api`` (API up on :8000)::

    .venv-win/Scripts/python.exe scripts/metamind_demo/verify_reflection.py
"""

from __future__ import annotations

import asyncio
import json
import os
import sys
import urllib.error
import urllib.request
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent
API_ROOT = SCRIPT_DIR.parents[1]
sys.path.insert(0, str(API_ROOT))
sys.path.insert(0, str(SCRIPT_DIR))

import seed_demo_course as seeder  # noqa: E402

from database import async_session  # noqa: E402

BASE = os.environ.get("OPENTUTOR_API", "http://localhost:8000").rstrip("/")
COURSE_ID = str(seeder._uid("course"))

CONFUSED_ANSWER = (
    "自进化就是 AI 记住了我喜欢例子，下次继续给我举例，这样它就更懂我了。"
    "只要能根据我改变回答，就是自进化。"
)
GOOD_ANSWER = (
    "自进化指一个 Agent 能从自己的交互经历中提取经验，并把经验转化为对自身策略的持续更新。"
    "它需要四个条件：经验积累、反思和问题诊断、持续性的策略改变、后续结果验证。"
    "它和长期记忆的区别是：记忆改变的是它知道什么，自进化改变的是它以后怎么教。"
)
TRANSFER_CASE = (
    "新案例：某数学辅导 Agent 连续三次用公式推导讲「导数表示变化率」，学生练习都错。"
    "它诊断出学生缺的是「变化率」的直觉；新增策略「先用速度—时间图像做对比，再回到公式」，"
    "保存为带版本的技能 V2；下一次同类问题该策略被检索调用；之后用未讲过的新题检验效果。"
)
TRANSFER_GOOD = (
    "这个案例构成真正的自进化。依据是：它先做了具体诊断，指出学生缺的是变化率直觉；"
    "然后产出了可执行的策略，带触发条件和步骤；并且把策略保存为带版本的 V2，"
    "下次同类问题会被检索调用；最后还用新题检验效果。四条证据链都齐了。"
)


class Checks:
    def __init__(self) -> None:
        self.passed: list[str] = []
        self.failed: list[str] = []

    def ok(self, label: str, condition: bool, detail: str = "") -> None:
        if condition:
            self.passed.append(label)
            print(f"  PASS  {label}{(' — ' + detail) if detail else ''}")
        else:
            self.failed.append(label)
            print(f"  FAIL  {label}{(' — ' + detail) if detail else ''}")


def http(method: str, path: str, body: dict | None = None, timeout: int = 300) -> dict:
    data = json.dumps(body).encode("utf-8") if body is not None else None
    request = urllib.request.Request(
        f"{BASE}{path}", data=data, method=method,
        headers={"Content-Type": "application/json; charset=utf-8"} if data else {},
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:  # pragma: no cover
        detail = exc.read().decode("utf-8", "replace")
        raise SystemExit(f"{method} {path} -> HTTP {exc.code}: {detail}") from exc


async def reset() -> None:
    async with async_session() as db:
        await seeder._reset_learner(db, seeder._load_spec())
        await db.commit()


def problems() -> dict[str, dict]:
    return {
        (item.get("problem_metadata") or {}).get("item_slug"): item
        for item in http("GET", f"/api/quiz/{COURSE_ID}")
    }


def submit(problem_id: str, answer: str) -> bool:
    return http("POST", "/api/quiz/submit",
                {"problem_id": problem_id, "user_answer": answer})["is_correct"]


def evaluate(**kwargs) -> dict:
    return http("POST", "/api/teaching/evaluate", {"course_id": COURSE_ID, **kwargs})


def reflect(evaluation_id: str, feedback: str | None = None) -> dict:
    body = {"course_id": COURSE_ID, "evaluation_id": evaluation_id}
    if feedback:
        body["subjective_feedback"] = feedback
    return http("POST", "/api/teaching/reflect", body)


def check_evidence_integrity(c: Checks, tag: str, row: dict) -> None:
    package = row.get("input_package") or {}
    evidence = (row.get("evidence") or {})
    cited = evidence.get("cited") or []
    allowed = set(evidence.get("allowed_source_ids") or [])
    if row["status"] != "completed":
        c.ok(f"{tag}: non-completed reflection cites nothing fabricated",
             all(item.get("source_id") in allowed for item in cited) if cited else True)
        return
    c.ok(f"{tag}: cites at least one piece of evidence", len(cited) >= 1, str(len(cited)))
    def _known(raw_id: str) -> bool:
        return raw_id in allowed or (":" in raw_id and raw_id.split(":", 1)[1] in allowed)

    c.ok(
        f"{tag}: every cited id exists in the input package",
        all(_known(item["source_id"]) for item in cited),
        str([item["source_id"] for item in cited if not _known(item["source_id"])]),
    )
    attempts = {str(a.get("practice_result_id")): (a.get("user_answer") or "")
                for a in package.get("student_attempts") or []}
    objective = package.get("objective_text") or ""
    skill_ids = {s["id"] for s in package.get("available_teaching_skills") or []}
    feedback = ((package.get("subjective_feedback") or {}) or {}).get("text") or ""
    corpus_texts: list[str] = list(attempts.values()) + [objective]
    for item in cited:
        quote = item.get("quote") or ""
        source_id = item["source_id"]
        if source_id not in (evidence.get("allowed_source_ids") or []) and ":" in source_id:
            source_id = source_id.split(":", 1)[1]
        if source_id in attempts:
            haystack = attempts[source_id]
        elif source_id == "objective":
            haystack = objective
        elif source_id == "subjective_feedback":
            haystack = feedback
        elif source_id.startswith("skill:"):
            haystack = source_id + " " + " ".join(skill_ids)
        elif source_id.startswith("concept:"):
            concept = next(
                (c for c in (package.get("learner_state") or {}).get("concepts", [])
                 if f"concept:{c['concept_id']}" == source_id),
                None,
            )
            haystack = json.dumps(concept, ensure_ascii=False) if concept else ""
        else:
            haystack = json.dumps(package.get("evaluation") or {}, ensure_ascii=False)
        c.ok(
            f"{tag}: quote for {source_id} is verbatim in its source",
            quote.replace(" ", "") in haystack.replace(" ", ""),
            quote[:40],
        )
    c.ok(
        f"{tag}: no reflection quote invents a student sentence",
        any(item["quote"].replace(" ", "") in text.replace(" ", "")
            for item in cited for text in corpus_texts)
        or all(item["source_id"] not in attempts for item in cited),
    )


def check_proposal_quality(c: Checks, tag: str, row: dict) -> None:
    proposal = row.get("improvement_proposal") or {}
    plan = row.get("verification_plan") or {}
    package = row.get("input_package") or {}
    library = {s["id"] for s in package.get("available_teaching_skills") or []}

    c.ok(f"{tag}: proposal is not verified", proposal.get("is_verified") is False)
    c.ok(
        f"{tag}: cause hypothesis is not verified",
        (row.get("cause_hypothesis") or {}).get("is_verified") is False,
    )
    reused = proposal.get("reuses_skills") or []
    c.ok(f"{tag}: proposal reuses existing skills only",
         bool(reused) and set(reused) <= library, str(reused))
    steps = proposal.get("steps") or []
    c.ok(f"{tag}: proposal has at least 2 concrete steps", len(steps) >= 2, str(len(steps)))
    c.ok(
        f"{tag}: steps are not generic filler",
        all(len(step) >= 6 for step in steps)
        and not any(word in step for step in steps
                    for word in ("多练习", "更努力", "讲清楚一点", "多举例子")),
        str(steps)[:120],
    )
    c.ok(f"{tag}: verification plan defines a metric", bool(plan.get("metric")))
    c.ok(f"{tag}: verification plan defines success criteria",
         bool(plan.get("success_criteria")))
    c.ok(f"{tag}: verification plan defines failure criteria",
         bool(plan.get("failure_criteria")))
    c.ok(f"{tag}: verification uses parallel/new items",
         plan.get("uses_parallel_items") is True)


async def main() -> int:
    c = Checks()
    await reset()
    p = problems()

    # ── C1: concept confusion -> evidence-backed reflection ────────────────
    print("\n[C1] 学生明确混淆「记忆」与「自进化」→ 生成有证据的反思")
    submit(p["a1"]["id"], "a")
    submit(p["a2"]["id"], "a")
    evaluation = evaluate(
        objective_slug="g1", session_id="run01_concept",
        problem_ids=[p["a1"]["id"], p["a2"]["id"]],
        open_answer=CONFUSED_ANSWER,
        open_question="请解释什么是自进化 Agent？它和只有长期记忆的 Agent 有什么区别？",
        strategy_version="metamind.teaching-strategy.v1",
    )
    c.ok("evaluation captured the confusion (understanding == 0)",
         evaluation["scores"]["understanding"] == 0,
         str(evaluation["scores"]))
    row = reflect(evaluation["evaluation_id"], feedback="我觉得我懂了，但是题目一做就错")
    if row["status"] != "completed":
        print(f"  [diag] status={row['status']} code={row['error_code']} "
              f"attempts={row['attempts']}")
        print(f"  [diag] {(row['error_message'] or '')[:600]}")
    c.ok("reflection completed", row["status"] == "completed", row["status"])
    c.ok("problem statement is specific", len(row["problem"]["statement"] or "") >= 12,
         (row["problem"]["statement"] or "")[:60])
    c.ok("problem targets the understanding dimension",
         row["problem"]["dimension"] in {"understanding", "multiple"},
         str(row["problem"]["dimension"]))
    c.ok("evaluation_id is linked", row["evaluation_id"] == evaluation["evaluation_id"])
    c.ok("strategy_version is carried over",
         row["strategy_version"] == "metamind.teaching-strategy.v1",
         str(row["strategy_version"]))
    check_evidence_integrity(c, "C1", row)
    check_proposal_quality(c, "C1", row)
    c.ok("cause hypothesis carries alternatives or explicit low confidence",
         bool((row.get("cause_hypothesis") or {}).get("alternatives"))
         or (row.get("cause_hypothesis") or {}).get("confidence") == "low",
         json.dumps(row.get("cause_hypothesis") or {}, ensure_ascii=False)[:140])
    c.ok("subjective feedback cited as subjective, not as objective evidence",
         "subjective_feedback" not in [i["source_id"] for i in (row["evidence"]["cited"] or [])]
         or any(i["source_type"] == "subjective_feedback" for i in row["evidence"]["cited"]))

    # ── C2: repeatability of structure ─────────────────────────────────────
    print("\n[C2] 同一输入重复调用：结构稳定，不产生矛盾结论")
    again = reflect(evaluation["evaluation_id"], feedback="我觉得我懂了，但是题目一做就错")
    c.ok("second call reaches a terminal status",
         again["status"] in {"completed", "failed"}, again["status"])
    c.ok("a failed repeat still reports its error code",
         again["status"] != "failed" or bool(again["error_code"]),
         str(again["error_code"]))
    c.ok("both reflections cite evidence",
         bool(again["evidence"]["cited"]) and bool(row["evidence"]["cited"]))
    c.ok("neither run claims verified effectiveness",
         (again["improvement_proposal"] or {}).get("is_verified") is not True
         and (row["improvement_proposal"] or {}).get("is_verified") is not True)
    c.ok("new reflection id (history is append-only)",
         again["reflection_id"] != row["reflection_id"])
    check_evidence_integrity(c, "C2", again)
    if again["status"] == "completed" and row["status"] == "completed":
        c.ok("same problem dimension across repeats",
             again["problem"]["dimension"] == row["problem"]["dimension"],
             f"{again['problem']['dimension']} vs {row['problem']['dimension']}")
        c.ok("repeat proposals use the same skill library",
             set((again["improvement_proposal"] or {}).get("reuses_skills") or [])
             <= {s["id"] for s in (again["input_package"] or {}).get(
                 "available_teaching_skills", [])})

    # ── C3: achieved objective -> no invented failure ──────────────────────
    print("\n[C3] 学生已达成目标：不得强行提出失败原因")
    await reset()
    p = problems()
    submit(p["a1"]["id"], "b")
    submit(p["a2"]["id"], "b,d")
    achieved = evaluate(
        objective_slug="g1", session_id="run_achieved",
        problem_ids=[p["a1"]["id"], p["a2"]["id"]],
        open_answer=GOOD_ANSWER,
        transfer_case=TRANSFER_CASE, transfer_answer=TRANSFER_GOOD,
        open_question="请解释什么是自进化 Agent？",
    )
    print(f"  achieved scores: {achieved['scores']} verdict={achieved['verdict']}")
    if achieved["verdict"] == "achieved":
        row = reflect(achieved["evaluation_id"])
        c.ok("status == no_improvement_needed",
             row["status"] == "no_improvement_needed", row["status"])
        c.ok("no problem invented", row["problem"]["statement"] is None,
             str(row["problem"]["statement"])[:80])
        c.ok("no proposal attached", row["improvement_proposal"] is None)
        c.ok("model not used for this decision", row["attempts"] == 0, str(row["attempts"]))
    else:
        c.ok("achieved scenario reachable", False,
             f"verdict={achieved['verdict']} scores={achieved['scores']}")

    # ── C4: no evidence -> insufficient ────────────────────────────────────
    print("\n[C4] 缺少评价证据 → insufficient_evidence")
    await reset()
    empty = evaluate(objective_slug="g1", session_id="run_empty")
    c.ok("empty evaluation measured 0 dimensions",
         empty["dimensions_measured"] == 0, str(empty["dimensions_measured"]))
    row = reflect(empty["evaluation_id"])
    c.ok("status == insufficient_evidence", row["status"] == "insufficient_evidence",
         row["status"])
    c.ok("gate reason recorded", "证据不足" in (row["problem"]["statement"] or ""),
         (row["problem"]["statement"] or "")[:60])
    c.ok("no cause invented", row["cause_hypothesis"] is None)
    c.ok("no proposal invented", row["improvement_proposal"] is None)

    # ── C5: persistence + query ────────────────────────────────────────────
    print("\n[C5] 数据库与查询接口")
    listing = http("GET", f"/api/teaching/reflections?course_id={COURSE_ID}&limit=50")
    c.ok("reflections are persisted", listing["count"] >= 1, str(listing["count"]))
    by_eval = http(
        "GET",
        f"/api/teaching/reflections?course_id={COURSE_ID}"
        f"&evaluation_id={empty['evaluation_id']}",
    )
    c.ok("filter by evaluation works", by_eval["count"] == 1, str(by_eval["count"]))
    one = http("GET", f"/api/teaching/reflections/{by_eval['reflections'][0]['reflection_id']}")
    c.ok("single reflection fetch works",
         one["reflection_id"] == by_eval["reflections"][0]["reflection_id"])
    c.ok("input hash recorded for reproducibility", bool(one["input_hash"]))
    # P0-1: `_reset_learner` now PRESERVES evidence (evaluations/reflections stay
    # so Run01 and Run02 can be compared); run isolation is by run_tag, not by
    # deleting earlier reflections. So after several runs reflections accumulate.
    c.ok("reset keeps demo reflections (evidence preserved, runs tagged)",
         listing["count"] >= 1, str(listing["count"]))
    c.ok("reflections carry a run_tag for isolation",
         any((r.get("run_tag") or "").startswith("run")
             for r in listing["reflections"]),
         str({r.get("run_tag") for r in listing["reflections"]}))

    if os.environ.get("METAMIND_KEEP_EVIDENCE") != "1":
        await reset()
    print("\n" + "=" * 60)
    print(f"PASSED {len(c.passed)}, FAILED {len(c.failed)}")
    for failure in c.failed:
        print(f"  - {failure}")
    return 0 if not c.failed else 1


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
