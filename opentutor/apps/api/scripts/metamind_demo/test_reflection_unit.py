"""In-process unit tests for the Reflection Engine (METAMIND module 3).

No network: the LLM is stubbed so validation, gating and retry behaviour are
deterministic. The evidence package is built from the real demo course data.

Run from ``apps/api``::

    .venv-win/Scripts/python.exe scripts/metamind_demo/test_reflection_unit.py
"""

from __future__ import annotations

import asyncio
import json
import sys
import uuid
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent
API_ROOT = SCRIPT_DIR.parents[1]
sys.path.insert(0, str(API_ROOT))
sys.path.insert(0, str(SCRIPT_DIR))

import seed_demo_course as seeder  # noqa: E402

from sqlalchemy import select  # noqa: E402

from database import async_session  # noqa: E402
from models.practice import PracticeProblem, PracticeResult  # noqa: E402
from models.teaching_evaluation import (  # noqa: E402
    EVAL_STATUS_COMPLETED,
    EVAL_STATUS_FAILED,
    TeachingEvaluation,
)
from models.teaching_reflection import TeachingReflection  # noqa: E402
from services.evolution import evaluator as ev  # noqa: E402
from services.evolution import reflection_engine as refl  # noqa: E402
from services.llm import router as llm_router  # noqa: E402

COURSE_ID = seeder._uid("course")


class StubClient:
    model = "stub-model"

    def __init__(self, replies: list[str]):
        self.replies = replies
        self.calls = 0
        self.prompts: list[str] = []

    async def extract(self, system_prompt: str, user_message: str):
        self.calls += 1
        self.prompts.append(system_prompt)
        index = min(self.calls - 1, len(self.replies) - 1)
        return self.replies[index], {"input_tokens": 1, "output_tokens": 1}


def _patch_client(client) -> None:
    llm_router.get_llm_client = lambda *a, **k: client  # type: ignore[assignment]


def _ids_from_package(package, evaluation_id: uuid.UUID) -> dict[str, str]:
    """Evidence ids/quotes that really exist in this specific package."""
    attempt = next(
        (item for item in package.attempts if item["question_type"] == "open_answer"), None
    )
    answer = (attempt or {}).get("user_answer") or ""
    objective = package.objective_text or ""
    return {
        "evaluation_id": str(evaluation_id),
        "student_answer_id": str((attempt or {}).get("practice_result_id") or ""),
        "student_quote": answer[:14],
        "evaluation_quote": objective[:14],
    }


def _valid_output(package_ids: dict[str, str], skills: list[str]) -> dict:
    """A schema-valid reflection whose quotes are taken from the real package."""
    return {
        "problem": {
            "statement": "学生把『记住偏好』当成自进化，未建立『策略被保存并被验证』这一边界",
            "severity": "high",
            "dimension": "understanding",
            "affected_objective": "准确解释自进化 Agent 的定义",
        },
        "evidence": [
            {
                "source_type": "student_answer",
                "source_id": package_ids["student_answer_id"],
                "quote": package_ids["student_quote"],
                "note": "学生直接把记忆与自进化等同",
            },
            {
                "source_type": "evaluation",
                "source_id": package_ids["evaluation_id"],
                "quote": package_ids["evaluation_quote"],
                "note": "评价给出的判定理由",
            },
        ],
        "cause_hypothesis": {
            "statement": "本轮只给了抽象定义，缺少让学生比较两种机制的对照材料",
            "confidence": "medium",
            "is_verified": False,
            "rationale": "同一段作答同时出现定义缺失与概念混同，指向讲解方式而非学生能力",
            "alternatives": ["学生只是没读完题", "评价题目本身表述不清"],
        },
        "improvement_proposal": {
            "summary": "改用对照案例 + 边界追问 + 新情境检验的组合",
            "target_dimension": "understanding",
            "reuses_skills": skills[:2],
            "steps": [
                "先给出两个结构相同但只有一个具备策略保存与验证的案例",
                "让学生逐条指出两个案例的差异，并写下判断依据",
                "用第三个未见过的案例让学生独立判断并说明缺哪条证据",
            ],
            "trigger_conditions": ["学生把记忆/个性化与策略进化混同"],
            "expected_effect": "学生能独立说出保存、调用、验证三个环节",
            "is_verified": False,
        },
        "verification_plan": {
            "metric": "同目标下的 understanding 维度得分与引文完整度",
            "comparison": "与本次评价对比，使用平行新题，不重复原题",
            "next_run_procedure": ["重置学习者状态", "用新题教学并评价", "对比两次评价证据"],
            "success_criteria": ["understanding 提升且能引用三条证据链"],
            "failure_criteria": ["仍无法区分记忆与策略进化", "无法给出任何引文"],
            "uses_parallel_items": True,
        },
    }


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


async def _reset() -> None:
    async with async_session() as db:
        await seeder._reset_learner(db, seeder._load_spec())
        await db.commit()


async def _make_evaluation(
    *, open_answer: str, submit_wrong: bool, verdict_override: str | None = None
) -> uuid.UUID:
    """Create a real evaluation row (deterministic parts only) for reflection tests."""
    async with async_session() as db:
        user = await seeder._single_user(db)
        problem = (
            await db.execute(
                select(PracticeProblem).where(PracticeProblem.course_id == COURSE_ID)
            )
        ).scalars().first()
        if submit_wrong:
            db.add(
                PracticeResult(
                    problem_id=problem.id,
                    user_id=user.id,
                    user_answer="a",
                    is_correct=False,
                    error_category="conceptual",
                )
            )
            await db.flush()
        row = await ev.evaluate_objective(
            db,
            user_id=user.id,
            course_id=COURSE_ID,
            session_id="unit_reflection",
            objective_slug="g1",
            open_answer=open_answer,
            open_question="请解释什么是自进化 Agent？",
            persist=True,
        )
        if verdict_override == "achieved":
            row.verdict = "achieved"
        await db.commit()
        return row.id


async def _package_for(evaluation_id: uuid.UUID):
    async with async_session() as db:
        user = await seeder._single_user(db)
        evaluation = await db.get(TeachingEvaluation, evaluation_id)
        return await refl.build_evidence_package(db, user.id, COURSE_ID, evaluation)


async def main() -> int:
    c = Checks()
    await _reset()

    MIXED_ANSWER = (
        "自进化就是 AI 记住了我喜欢例子，下次继续给我举例，这样它就更懂我了。"
        "只要能根据我改变回答，就是自进化。"
    )
    evaluation_id = await _make_evaluation(open_answer=MIXED_ANSWER, submit_wrong=True)
    package = await _package_for(evaluation_id)
    student_attempt = next(
        item for item in package.attempts if item["question_type"] == "open_answer"
    )
    ids = {
        "evaluation_id": str(evaluation_id),
        "student_answer_id": student_attempt["practice_result_id"],
        "student_quote": (student_attempt["user_answer"] or "")[:14],
        "evaluation_quote": (package.objective_text or "")[:14],
    }
    skills = [skill["id"] for skill in package.skills]
    print(f"skill library: {skills}")

    # ── R1: baseline package shape ─────────────────────────────────────────
    print("\n[R1] 证据包完整性")
    c.ok("package carries the evaluation", package.evaluation["evaluation_id"] == str(evaluation_id))
    c.ok("package carries learner state",
         isinstance(package.learner_state.get("concepts"), list)
         and len(package.learner_state["concepts"]) == 3)
    c.ok("package carries the student's own words",
         any(item["question_type"] == "open_answer" for item in package.attempts))
    c.ok("package exposes the existing skill library", len(skills) >= 3, str(len(skills)))
    c.ok("strategy version may be null", "strategy_version" in package.evaluation)

    # ── R2: happy path validation ──────────────────────────────────────────
    print("\n[R2] 合法输出通过校验")
    output = refl.ReflectionOutput.model_validate(_valid_output(ids, skills))
    failures = refl.validate_output(output, package)
    c.ok("no validation failures", failures == [], str([f.code for f in failures]))
    c.ok("is_verified stays false on both blocks",
         output.cause_hypothesis.is_verified is False
         and output.improvement_proposal.is_verified is False)

    # ── R3: unknown evidence id ────────────────────────────────────────────
    print("\n[R3] 引用不存在的证据 id -> 校验失败")
    bad = _valid_output(ids, skills)
    bad["evidence"][0]["source_id"] = "does-not-exist"
    failures = refl.validate_output(refl.ReflectionOutput.model_validate(bad), package)
    c.ok("unknown_evidence_id reported",
         any(item.code == refl.ERROR_UNKNOWN_EVIDENCE_ID for item in failures),
         str([f.code for f in failures]))

    # ── R4: fabricated student quote ───────────────────────────────────────
    print("\n[R4] 编造学生原话 -> 校验失败")
    bad = _valid_output(ids, skills)
    bad["evidence"][0]["quote"] = "学生说他已经完全理解了自进化"
    failures = refl.validate_output(refl.ReflectionOutput.model_validate(bad), package)
    c.ok("evidence_not_verbatim reported",
         any(item.code == refl.ERROR_EVIDENCE_NOT_VERBATIM for item in failures),
         str([f.code for f in failures]))

    # ── R5: claiming verified effectiveness ────────────────────────────────
    print("\n[R5] 把未验证的建议标成已验证 -> 校验失败")
    bad = _valid_output(ids, skills)
    bad["improvement_proposal"]["is_verified"] = True
    failures = refl.validate_output(refl.ReflectionOutput.model_validate(bad), package)
    c.ok("unverified_claim reported",
         any(item.code == refl.ERROR_UNVERIFIED_CLAIM for item in failures),
         str([f.code for f in failures]))
    bad = _valid_output(ids, skills)
    bad["cause_hypothesis"]["is_verified"] = True
    failures = refl.validate_output(refl.ReflectionOutput.model_validate(bad), package)
    c.ok("verified cause hypothesis rejected",
         any(item.code == refl.ERROR_UNVERIFIED_CLAIM for item in failures))

    # ── R6: invented skill ─────────────────────────────────────────────────
    print("\n[R6] 使用不存在的教学技能 -> 校验失败")
    bad = _valid_output(ids, skills)
    bad["improvement_proposal"]["reuses_skills"] = ["contrastive_teaching"]
    failures = refl.validate_output(refl.ReflectionOutput.model_validate(bad), package)
    c.ok("unknown_skill reported",
         any(item.code == refl.ERROR_UNKNOWN_SKILL for item in failures),
         str([f.code for f in failures]))

    print("\n[R6b] 证据 id 多写了 practice_result: 前缀 -> 容错通过（不是编造）")
    prefixed = _valid_output(ids, skills)
    prefixed["evidence"][0]["source_id"] = f"practice_result:{ids['student_answer_id']}"
    failures = refl.validate_output(refl.ReflectionOutput.model_validate(prefixed), package)
    c.ok("redundant prefix tolerated", failures == [], str([f.code for f in failures]))
    prefixed["evidence"][0]["source_id"] = "practice_result:not-a-real-id"
    failures = refl.validate_output(refl.ReflectionOutput.model_validate(prefixed), package)
    c.ok("prefix does not rescue an unknown id",
         any(item.code == refl.ERROR_UNKNOWN_EVIDENCE_ID for item in failures))

    # ── R7: vague proposal ─────────────────────────────────────────────────
    print("\n[R7] 空泛建议 -> 校验失败")
    bad = _valid_output(ids, skills)
    bad["improvement_proposal"]["steps"] = ["多练习", "下次更努力"]
    failures = refl.validate_output(refl.ReflectionOutput.model_validate(bad), package)
    c.ok("vague_proposal reported",
         any(item.code == refl.ERROR_VAGUE_PROPOSAL for item in failures),
         str([f.code for f in failures]))

    # ── R8: gate — achieved objective ──────────────────────────────────────
    print("\n[R8] 目标已达成 -> no_improvement_needed，且不调用模型")
    achieved_id = await _make_evaluation(
        open_answer=MIXED_ANSWER, submit_wrong=False, verdict_override="achieved"
    )
    client = StubClient([json.dumps(_valid_output(ids, skills), ensure_ascii=False)])
    _patch_client(client)
    async with async_session() as db:
        user = await seeder._single_user(db)
        row = await refl.reflect_on_evaluation(
            db, user_id=user.id, course_id=COURSE_ID, evaluation_id=achieved_id
        )
        await db.commit()
        c.ok("status == no_improvement_needed",
             row.status == refl.REFLECTION_STATUS_NO_IMPROVEMENT, row.status)
        c.ok("model was never called", client.calls == 0, f"calls={client.calls}")
        c.ok("no fabricated problem statement", row.problem_statement is None)
        c.ok("evidence records the gate reason",
             (row.evidence_json or {}).get("gate") == refl.REFLECTION_STATUS_NO_IMPROVEMENT)

    # ── R9: gate — evaluation with no measured dimension ────────────────────
    print("\n[R9] 评价没有任何维度测得 -> insufficient_evidence")
    await _reset()
    empty_id = await _make_evaluation(open_answer="", submit_wrong=False)
    async with async_session() as db:
        row = await db.get(TeachingEvaluation, empty_id)
        row.understanding_score = None
        row.application_score = None
        row.transfer_score = None
        row.dimensions_measured = 0
        await db.commit()
    client = StubClient([json.dumps(_valid_output(ids, skills), ensure_ascii=False)])
    _patch_client(client)
    async with async_session() as db:
        user = await seeder._single_user(db)
        row = await refl.reflect_on_evaluation(
            db, user_id=user.id, course_id=COURSE_ID, evaluation_id=empty_id
        )
        await db.commit()
        c.ok("status == insufficient_evidence",
             row.status == refl.REFLECTION_STATUS_INSUFFICIENT, row.status)
        c.ok("model not called", client.calls == 0, f"calls={client.calls}")
        c.ok("gate reason stored", "证据不足" in (row.problem_statement or ""))

    # ── R10: failed evaluation gate ────────────────────────────────────────
    print("\n[R10] 评价本身 failed -> insufficient_evidence，不编原因")
    async with async_session() as db:
        row = await db.get(TeachingEvaluation, empty_id)
        row.status = EVAL_STATUS_FAILED
        row.error_code = "rubric_version_mismatch"
        await db.commit()
    async with async_session() as db:
        user = await seeder._single_user(db)
        row = await refl.reflect_on_evaluation(
            db, user_id=user.id, course_id=COURSE_ID, evaluation_id=empty_id
        )
        await db.commit()
        c.ok("failed evaluation -> insufficient",
             row.status == refl.REFLECTION_STATUS_INSUFFICIENT, row.status)

    # ── R11: invalid JSON then repaired ────────────────────────────────────
    print("\n[R11] 首次无效 JSON -> 重试一次后成功，数据库只留一条记录")
    await _reset()
    eval_id = await _make_evaluation(open_answer=MIXED_ANSWER, submit_wrong=True)
    fresh_package = await _package_for(eval_id)
    fresh_ids = _ids_from_package(fresh_package, eval_id)
    good = json.dumps(_valid_output(fresh_ids, skills), ensure_ascii=False)
    async with async_session() as db:
        snapshot_row = await db.get(TeachingEvaluation, eval_id)
        scores_before = (
            snapshot_row.understanding_score,
            snapshot_row.application_score,
            snapshot_row.transfer_score,
            snapshot_row.verdict,
            snapshot_row.status,
        )
    client = StubClient(["这不是 JSON", good])
    _patch_client(client)
    async with async_session() as db:
        user = await seeder._single_user(db)
        before = (await db.execute(select(TeachingEvaluation))).scalars().all()
        row = await refl.reflect_on_evaluation(
            db, user_id=user.id, course_id=COURSE_ID, evaluation_id=eval_id
        )
        await db.commit()
        c.ok("retried once", row.attempts == 2, str(row.attempts))
        c.ok("status completed after repair",
             row.status == refl.REFLECTION_STATUS_COMPLETED, row.status)
        c.ok("raw responses keep both attempts",
             (row.raw_llm_response or "").count("--- attempt ---") == 1,
             str(row.raw_llm_response and len(row.raw_llm_response)))

    # ── R12: persistent failure is not disguised ───────────────────────────
    print("\n[R12] 两次都失败 -> failed + 错误码，不留成功伪装")
    client = StubClient(["还是不是 JSON", "仍然不是 JSON"])
    _patch_client(client)
    async with async_session() as db:
        user = await seeder._single_user(db)
        row = await refl.reflect_on_evaluation(
            db, user_id=user.id, course_id=COURSE_ID, evaluation_id=eval_id
        )
        await db.commit()
        c.ok("status == failed", row.status == refl.REFLECTION_STATUS_FAILED, row.status)
        c.ok("error_code recorded", row.error_code == refl.ERROR_INVALID_JSON, str(row.error_code))
        c.ok("no proposal stored for a failed run", row.improvement_json is None)
        c.ok("attempts capped by max_attempts", row.attempts <= 3, str(row.attempts))
        stored = (
            await db.execute(
                select(TeachingReflection).where(
                    TeachingReflection.evaluation_id == eval_id
                )
            )
        ).scalars().all()
        c.ok(
            "retries do not create extra rows (2 calls -> 2 rows)",
            len(stored) == 2, f"{len(stored)} row(s)",
        )

    # ── R13: unknown evaluation id ─────────────────────────────────────────
    print("\n[R13] 评价不存在 -> 明确报错，不落一条假记录")
    async with async_session() as db:
        user = await seeder._single_user(db)
        try:
            await refl.reflect_on_evaluation(
                db, user_id=user.id, course_id=COURSE_ID, evaluation_id=uuid.uuid4()
            )
            c.ok("raises for unknown evaluation", False)
        except ValueError as exc:
            c.ok("raises evaluation_not_found",
                 str(exc) == refl.ERROR_EVALUATION_NOT_FOUND, str(exc))
        await db.rollback()

    # ── R14: reflection never touches scores ───────────────────────────────
    print("\n[R14] 反思不修改评价分数与学习进度")
    async with async_session() as db:
        evaluation = await db.get(TeachingEvaluation, eval_id)
        after = (
            evaluation.understanding_score,
            evaluation.application_score,
            evaluation.transfer_score,
            evaluation.verdict,
            evaluation.status,
        )
        c.ok("evaluation row untouched by reflection", after == scores_before,
             f"{scores_before} -> {after}")

    await _reset()
    print("\n" + "=" * 60)
    print(f"PASSED {len(c.passed)}, FAILED {len(c.failed)}")
    for failure in c.failed:
        print(f"  - {failure}")
    return 0 if not c.failed else 1


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
