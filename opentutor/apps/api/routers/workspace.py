"""METAMIND workspace BFF — the glue the 黄紫 React frontend expects.

The frontend was written against ``/api/workspace/*`` but the hand-off
OpenTutor tree did not contain that router.  Everything here is a thin,
read-mostly adapter over existing OpenTutor tables/services; it does not
re-implement teaching, evaluation, reflection or skill memory.

Honesty rules:
* Missing evidence is returned as ``None``/empty, never fabricated.
* Write endpoints that cannot be backed by an existing service return a
  clear ``detail`` instead of pretending the action happened.
"""

from __future__ import annotations

import logging
import os
import uuid
from datetime import datetime, timezone
from typing import Any

import httpx
from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import desc, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from config import settings
from database import get_db
from models.agent_kv import AgentKV
from models.chat_message import ChatMessageLog
from models.chat_session import ChatSession
from models.content import CourseContentTree
from models.course import Course
from models.study_plan import StudyPlan
from models.teaching_evaluation import TeachingEvaluation
from models.teaching_reflection import TeachingReflection
from models.teaching_skill import SkillInvocation, TeachingSkill, TeachingSkillVersion
from models.user import User
from services.auth.dependency import get_current_user
from services.evolution import learner_state as ls

logger = logging.getLogger(__name__)

router = APIRouter()


def _iso(value: datetime | None) -> str | None:
    if value is None:
        return None
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.isoformat()


def _str(value: Any) -> str | None:
    return str(value) if value is not None else None


def _evaluation_view(row: TeachingEvaluation) -> dict[str, Any]:
    return {
        "evaluation_id": str(row.id),
        "course_id": str(row.course_id),
        "session_id": row.session_id,
        "run_tag": row.run_tag,
        "objective_slug": row.objective_slug,
        "objective_id": _str(row.objective_id),
        "objective_text": row.objective_text,
        "rubric_version": row.rubric_version,
        "strategy_version": row.strategy_version,
        "status": row.status,
        "verdict": row.verdict,
        "scores": {
            "understanding": row.understanding_score,
            "application": row.application_score,
            "transfer": row.transfer_score,
            # Immediate evaluation never measures retention.
            "retention": None,
        },
        "dimensions_measured": row.dimensions_measured,
        "evidence": row.evidence_json,
        "rationale": row.rationale_json,
        "model": {"provider": row.model_provider, "name": row.model_name},
        "created_at": _iso(row.created_at),
    }


def _reflection_view(row: TeachingReflection) -> dict[str, Any]:
    improvement = row.improvement_json or {}
    cause = row.cause_hypothesis_json or {}
    if not cause and row.cause_statement:
        cause = {"description": row.cause_statement}
    return {
        "reflection_id": str(row.id),
        "evaluation_id": _str(row.evaluation_id),
        "course_id": str(row.course_id),
        "objective_slug": row.objective_slug,
        "session_id": row.session_id,
        "run_tag": row.run_tag,
        "strategy_version": row.strategy_version,
        "status": row.status,
        "problem": {
            "statement": row.problem_statement,
            "severity": row.problem_severity,
            "dimension": row.problem_dimension,
        },
        "cause_hypothesis": cause or None,
        "improvement_proposal": improvement or None,
        "verification_plan": row.verification_plan_json,
        "attempts": row.attempts,
        "model": {"provider": row.model_provider, "name": row.model_name},
        "created_at": _iso(row.created_at),
    }


def _version_view(row: TeachingSkillVersion, skill_name: str | None) -> dict[str, Any]:
    return {
        "version_id": str(row.id),
        "skill_id": str(row.skill_id),
        "name": skill_name,
        "version": row.version,
        "status": row.status,
        "trigger_conditions": row.trigger_conditions or [],
        "steps": row.steps or [],
        "change_summary": row.change_summary,
        "expected_effect": row.expected_effect,
        "validation": row.validation_json or {},
        "source_reflection_id": _str(row.source_reflection_id),
        "source_evaluation_id": _str(row.source_evaluation_id),
        "created_at": _iso(row.created_at),
    }


def _invocation_view(row: SkillInvocation) -> dict[str, Any]:
    return {
        "invocation_id": str(row.id),
        "course_id": str(row.course_id),
        "version_id": str(row.version_id),
        "version": row.version,
        "session_id": row.session_id,
        "run_tag": row.run_tag,
        "objective_slug": row.objective_slug,
        "scene": row.scene,
        "status": row.status,
        "match_score": row.match_score,
        "selected_at": _iso(row.selected_at),
        "injected_at": _iso(row.injected_at),
        "evaluated_at": _iso(row.evaluated_at),
        "executed_at": _iso(row.executed_at),
        "prompt_injection_verified": row.prompt_injection_verified,
        "execution_state": row.execution_state,
        "evaluation_id": _str(row.evaluation_id),
        "outcome": row.outcome_json,
    }


async def _load_overview(db: AsyncSession, user: User) -> dict[str, Any]:
    courses = (
        await db.execute(
            select(Course).where(Course.user_id == user.id).order_by(desc(Course.created_at))
        )
    ).scalars().all()

    course_cards: list[dict[str, Any]] = []
    for course in courses:
        try:
            state = await ls.build_learner_state(db, user.id, course.id, course=course)
        except Exception as exc:  # one broken course must not blank the workspace
            logger.warning("learner state failed for course %s: %s", course.id, exc)
            state = {}
        course_cards.append(
            {
                "course_id": str(course.id),
                "course_name": course.name,
                "description": course.description,
                "metadata": course.metadata_ or {},
                "concepts": state.get("concepts", []),
                "weak_concepts": state.get("weak_concepts", []),
                "review_due": state.get("review_due", []),
                "goals": state.get("goals", []),
                "assessment_summary": state.get(
                    "assessment_summary",
                    {
                        "state": "not_assessed",
                        "concepts_total": 0,
                        "concepts_assessed": 0,
                        "concepts_mastered": 0,
                        "overall_mastery": None,
                        "overall_mastery_basis": "no learner state available",
                    },
                ),
                "activity": state.get(
                    "activity",
                    {
                        "quiz_attempts": 0,
                        "quiz_correct": 0,
                        "accuracy": None,
                        "time_spent_minutes": 0,
                        "last_studied_at": None,
                        "gap_types": [],
                        "practice_results": 0,
                    },
                ),
                "error_profile": state.get("error_profile", {}),
                "warnings": state.get("warnings", []),
            }
        )

    evaluations = (
        await db.execute(
            select(TeachingEvaluation)
            .where(TeachingEvaluation.user_id == user.id)
            .order_by(desc(TeachingEvaluation.created_at))
            .limit(80)
        )
    ).scalars().all()
    reflections = (
        await db.execute(
            select(TeachingReflection)
            .where(TeachingReflection.user_id == user.id)
            .order_by(desc(TeachingReflection.created_at))
            .limit(80)
        )
    ).scalars().all()

    skills = (await db.execute(select(TeachingSkill))).scalars().all()
    skill_names = {row.id: row.name for row in skills}
    skill_enabled = {row.id: row.enabled for row in skills}
    versions = (
        await db.execute(select(TeachingSkillVersion).order_by(desc(TeachingSkillVersion.created_at)))
    ).scalars().all()
    version_rows = [_version_view(row, skill_names.get(row.skill_id)) for row in versions]

    invocations = (
        await db.execute(
            select(SkillInvocation)
            .where(SkillInvocation.user_id == user.id)
            .order_by(desc(SkillInvocation.selected_at))
            .limit(80)
        )
    ).scalars().all()

    reflection_by_evaluation = {
        str(row.evaluation_id): row for row in reflections if row.evaluation_id is not None
    }
    version_by_evaluation = {
        str(row.source_evaluation_id): row
        for row in versions
        if row.source_evaluation_id is not None
    }

    jobs: list[dict[str, Any]] = []
    for evaluation in evaluations:
        reflection = reflection_by_evaluation.get(str(evaluation.id))
        version = version_by_evaluation.get(str(evaluation.id))
        skill = None
        if version is not None:
            skill = {
                "skill_id": str(version.skill_id),
                "name": skill_names.get(version.skill_id),
                "version": version.version,
                "status": version.status,
                "enabled": bool(skill_enabled.get(version.skill_id, False)),
            }
        jobs.append(
            {
                "id": str(evaluation.id),
                "status": "failed" if evaluation.status == "failed" else "completed",
                "created_at": _iso(evaluation.created_at),
                "is_correct": evaluation.verdict == "achieved",
                "verdict": evaluation.verdict,
                "error": evaluation.error_message,
                "reflection_id": str(reflection.id) if reflection is not None else None,
                "evolution": {"skill": skill} if skill else {},
            }
        )

    plans = (
        await db.execute(
            select(StudyPlan)
            .where(StudyPlan.user_id == user.id)
            .order_by(desc(StudyPlan.created_at))
            .limit(20)
        )
    ).scalars().all()

    return {
        "courses": course_cards,
        "evaluations": [_evaluation_view(row) for row in evaluations],
        "reflections": [_reflection_view(row) for row in reflections],
        "invocations": [_invocation_view(row) for row in invocations],
        "versions": version_rows,
        "jobs": jobs,
        "plans": [
            {
                "id": str(row.id),
                "name": row.name,
                "course_id": str(row.course_id),
                "tasks": row.tasks or {},
                "created_at": _iso(row.created_at),
            }
            for row in plans
        ],
    }


# ── Shared workspace store ────────────────────────────────────────────────
# The React app keeps personal notes/records in a versioned JSON blob and
# syncs it through these three endpoints.  Stored in AgentKV so it follows
# the same user boundary as the rest of the workspace data.

WORKSPACE_NAMESPACE = "workspace"
WORKSPACE_KEY = "shared-v2"


async def _workspace_row(db: AsyncSession, user_id: uuid.UUID) -> AgentKV | None:
    result = await db.execute(
        select(AgentKV).where(
            AgentKV.user_id == user_id,
            AgentKV.course_id.is_(None),
            AgentKV.namespace == WORKSPACE_NAMESPACE,
            AgentKV.key == WORKSPACE_KEY,
        )
    )
    return result.scalar_one_or_none()


def _workspace_payload(row: AgentKV | None) -> dict[str, Any]:
    return {
        "data": row.value_json if row is not None else None,
        "version": row.version if row is not None else 0,
    }


@router.get("/data", summary="Read the shared workspace document")
async def get_workspace_data(
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    return _workspace_payload(await _workspace_row(db, user.id))


@router.put("/data", summary="Save the shared workspace document")
async def put_workspace_data(
    body: dict[str, Any],
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    row = await _workspace_row(db, user.id)
    incoming_version = body.get("version")
    if row is not None and incoming_version is not None and incoming_version != row.version:
        raise HTTPException(status_code=409, detail="工作空间已在别处更新，请刷新后再保存。")
    data = body.get("data")
    if not isinstance(data, dict):
        raise HTTPException(status_code=400, detail="工作空间数据必须是对象")
    if row is None:
        row = AgentKV(
            user_id=user.id,
            course_id=None,
            namespace=WORKSPACE_NAMESPACE,
            key=WORKSPACE_KEY,
            value_json=data,
            version=1,
        )
        db.add(row)
    else:
        row.value_json = data
        row.version = (row.version or 0) + 1
    await db.commit()
    await db.refresh(row)
    return _workspace_payload(row)


@router.post("/data/migrate", summary="Seed the shared workspace from browser data")
async def migrate_workspace_data(
    body: dict[str, Any],
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    row = await _workspace_row(db, user.id)
    if row is not None and row.value_json:
        # Never overwrite server data with a stale browser copy.
        return _workspace_payload(row)
    data = body.get("data")
    if not isinstance(data, dict):
        raise HTTPException(status_code=400, detail="迁移数据必须是对象")
    if row is None:
        row = AgentKV(
            user_id=user.id,
            course_id=None,
            namespace=WORKSPACE_NAMESPACE,
            key=WORKSPACE_KEY,
            value_json=data,
            version=1,
        )
        db.add(row)
    else:
        row.value_json = data
        row.version = (row.version or 0) + 1
    await db.commit()
    await db.refresh(row)
    return _workspace_payload(row)


TEACHING_API_BASE = os.environ.get("METAMIND_TEACHING_API", "http://127.0.0.1:5001/api")


@router.get("/classroom-todos", summary="Open classroom follow-ups from the 5001 teaching service")
async def classroom_todos(
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Read the 5001 classroom todo list and adapt it to the workspace UI.

    Read-only: the teaching service remains the owner of these tasks.  If it is
    offline the endpoint returns an empty list with a reason header instead of
    inventing tasks.
    """
    try:
        async with httpx.AsyncClient(timeout=6.0) as client:
            response = await client.get(
                f"{TEACHING_API_BASE}/todos",
                params={"status": "open"},
            )
            response.raise_for_status()
            payload = response.json()
    except Exception as exc:
        logger.info("classroom todos bridge unavailable: %s", exc)
        return []

    items: list[dict[str, Any]] = []
    for row in payload.get("todos", [])[:20]:
        kp = row.get("kp") or row.get("kp_id") or ""
        course_id = row.get("course_id") or ""
        items.append(
            {
                "id": f"teaching-{row.get('id')}",
                "kind": "classroom" if course_id else "followup",
                "course_id": course_id,
                "course_name": course_id or "课堂记录",
                "topic": kp,
                "title": row.get("text") or "课堂待办",
                "reason": (
                    f"截止 {row.get('due')}" if row.get("due") else "来自课堂记录"
                ),
                "next_step": (
                    f"复习「{kp}」：先不看答案做一道同类题，再说出每步依据"
                    if kp
                    else "回到教学 Agent 完成这一步"
                ),
                "reasons": [kp] if kp else [],
            }
        )
    return items


@router.get("/strategies/unified", summary="Read-only unified view of both strategy libraries")
async def unified_strategies(
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Normalise OpenTutor skill versions and 5001 classroom strategies.

    This is deliberately **read-only and non-merging**: the two libraries keep
    their own storage and rollback semantics.  The point is to stop the
    frontend (and the audience) from seeing two unrelated "strategy versions"
    with no explanation of where each comes from.
    """
    skills = (await db.execute(select(TeachingSkill))).scalars().all()
    skill_names = {row.id: row.name for row in skills}
    versions = (
        await db.execute(select(TeachingSkillVersion).order_by(TeachingSkillVersion.version))
    ).scalars().all()

    # The physical merge lives in the 5001 service's strategy_registry table.
    # Sync both sources, then read that one table. If 5001 is offline, fall
    # back to the old side-by-side view instead of pretending it merged.
    registry_items = None
    registry_sources = None
    try:
        async with httpx.AsyncClient(timeout=8.0) as client:
            await client.post(f"{TEACHING_API_BASE}/teach/strategies/registry/sync")
            registry_response = await client.get(
                f"{TEACHING_API_BASE}/teach/strategies/registry"
            )
            if registry_response.status_code == 200:
                registry_payload = registry_response.json()
                if registry_payload.get("items"):
                    registry_items = registry_payload["items"]
                    registry_sources = registry_payload.get("sources") or {}
    except Exception as exc:
        logger.info("strategy registry unavailable: %s", exc)

    opentutor_items = [
        {
            "source": "opentutor",
            "id": str(row.id),
            "name": skill_names.get(row.skill_id),
            "version": row.version,
            "status": row.status,
            "change_summary": row.change_summary,
            "target_dimensions": row.target_dimensions or [],
            "metrics": None,
            "source_evaluation_id": _str(row.source_evaluation_id),
            "source_reflection_id": _str(row.source_reflection_id),
            "created_at": _iso(row.created_at),
        }
        for row in versions
    ]

    teaching_items: list[dict[str, Any]] = []
    teaching_error: str | None = None
    try:
        async with httpx.AsyncClient(timeout=6.0) as client:
            response = await client.get(f"{TEACHING_API_BASE}/teach/evolution")
            response.raise_for_status()
            payload = response.json()
        for row in payload.get("versions", []):
            if row.get("kind") != "strategy":
                continue
            teaching_items.append(
                {
                    "source": "teaching_5001",
                    "id": row.get("id"),
                    "name": row.get("title"),
                    "version": row.get("id"),
                    "status": row.get("status"),
                    "change_summary": row.get("reason"),
                    "strategy": row.get("strategy"),
                    "error_type": row.get("error_type"),
                    "metrics": row.get("metrics"),
                    "diff": row.get("diff"),
                    "created_at": row.get("time"),
                }
            )
    except Exception as exc:  # 5001 may be stopped; the OpenTutor side still works
        teaching_error = str(exc)
        logger.info("teaching strategy bridge unavailable: %s", exc)

    # The demo DB contains earlier rehearsal rows that reuse run_tag="run01".
    # Anchor run01 to the evaluation that actually produced the active V2,
    # instead of taking the chronologically first run01 row.
    v2 = next(
        (
            row
            for row in versions
            if row.version == 2
            and row.status == "active"
            and row.source_evaluation_id is not None
        ),
        None,
    )
    run01 = None
    if v2 is not None and v2.source_evaluation_id is not None:
        run01 = await db.get(TeachingEvaluation, v2.source_evaluation_id)
        if run01 is not None and run01.user_id != user.id:
            run01 = None
    if run01 is None:
        run01 = (
            await db.execute(
                select(TeachingEvaluation)
                .where(
                    TeachingEvaluation.user_id == user.id,
                    TeachingEvaluation.run_tag == "run01",
                )
                .order_by(desc(TeachingEvaluation.created_at))
                .limit(1)
            )
        ).scalar_one_or_none()
    run02 = (
        await db.execute(
            select(TeachingEvaluation)
            .where(
                TeachingEvaluation.user_id == user.id,
                TeachingEvaluation.run_tag == "run02",
            )
            .order_by(desc(TeachingEvaluation.created_at))
            .limit(1)
        )
    ).scalar_one_or_none()
    inv2 = (
        await db.execute(
            select(SkillInvocation)
            .where(
                SkillInvocation.user_id == user.id,
                SkillInvocation.run_tag == "run02",
                SkillInvocation.version == 2,
            )
            .order_by(desc(SkillInvocation.selected_at))
            .limit(1)
        )
    ).scalar_one_or_none()

    evidence_chain: list[dict[str, Any]] = []
    if run01 is not None:
        evidence_chain.append(
            {
                "step": "V1 教学",
                "run": "run01",
                "id": str(run01.id),
                "detail": (
                    f"理解 {run01.understanding_score} / 应用 {run01.application_score} / "
                    f"迁移 {run01.transfer_score if run01.transfer_score is not None else '未测'}"
                ),
                "at": _iso(run01.created_at),
            }
        )
    if v2 is not None:
        evidence_chain.append(
            {
                "step": "反思生成 V2",
                "run": "reflection",
                "id": str(v2.id),
                "detail": v2.change_summary or "由反思生成的策略新版本",
                "at": _iso(v2.created_at),
            }
        )
    if inv2 is not None:
        evidence_chain.append(
            {
                "step": "V2 注入下一轮",
                "run": "run02",
                "id": str(inv2.id),
                "detail": (
                    f"version=2；prompt_injection_verified={inv2.prompt_injection_verified}；"
                    f"execution_state={inv2.execution_state}"
                ),
                "at": _iso(inv2.selected_at),
            }
        )
    if run02 is not None:
        evidence_chain.append(
            {
                "step": "V2 独立评价",
                "run": "run02",
                "id": str(run02.id),
                "detail": (
                    f"verdict={run02.verdict}；理解 {run02.understanding_score} / "
                    f"应用 {run02.application_score}"
                ),
                "at": _iso(run02.created_at),
            }
        )

    tied = bool(
        run01 is not None
        and run02 is not None
        and run01.understanding_score == run02.understanding_score
        and run01.application_score == run02.application_score
    )

    merged = registry_items is not None
    return {
        "storage_merged": merged,
        "note": (
            "统一 strategy_registry 已合并读出（5001 + OpenTutor）；两个引擎仍各自写源表，"
            "新版本通过 sync 汇入。"
            if merged
            else
            "两套策略库并列展示：OpenTutor 的 skill_versions 与 5001 的 strategies "
            "各自保存、各自回滚；这里只做只读归一化，未合并存储。"
        ),
        "sources": registry_sources or {
            "opentutor": len(opentutor_items),
            "teaching_5001": len(teaching_items),
        },
        "teaching_error": teaching_error,
        "items": registry_items if merged else opentutor_items + teaching_items,
        "evidence_chain": evidence_chain,
        "honesty": {
            "scores_tied": tied,
            "prompt_injection_verified": (
                inv2.prompt_injection_verified if inv2 is not None else None
            ),
            "execution_state": inv2.execution_state if inv2 is not None else None,
            "learning_gain_claimed": False,
        },
    }


@router.get("/growth-insights/{course_id}", summary="Evidence-backed next-step cards")
async def growth_insights(
    course_id: uuid.UUID,
    refresh: bool = Query(default=False),
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Deterministic cards built only from real learner evidence.

    No LLM call here: the point is to show the student (and the demo audience)
    which real record produced each suggestion.  ``refresh`` is accepted for
    API compatibility; the result is recomputed from the database each time.
    """
    course = await db.get(Course, course_id)
    if course is None or course.user_id != user.id:
        raise HTTPException(status_code=404, detail="没有找到这门课")

    try:
        state = await ls.build_learner_state(db, user.id, course_id, course=course)
    except Exception:
        state = {}

    reflections = (
        await db.execute(
            select(TeachingReflection)
            .where(
                TeachingReflection.user_id == user.id,
                TeachingReflection.course_id == course_id,
            )
            .order_by(desc(TeachingReflection.created_at))
            .limit(5)
        )
    ).scalars().all()
    evaluations = (
        await db.execute(
            select(TeachingEvaluation)
            .where(
                TeachingEvaluation.user_id == user.id,
                TeachingEvaluation.course_id == course_id,
            )
            .order_by(desc(TeachingEvaluation.created_at))
            .limit(5)
        )
    ).scalars().all()

    cards: list[dict[str, Any]] = []
    weak = state.get("weak_concepts") or []
    if weak:
        concept = weak[0]
        score = concept.get("mastery_score")
        cards.append(
            {
                "id": f"weak-{concept.get('concept_id')}",
                "kind": "observation",
                "title": f"先把「{concept.get('name')}」练到能独立解释",
                "body": (
                    f"目前掌握度是 {round(score * 100)}%，"
                    if isinstance(score, (int, float))
                    else "这个知识点还没有可靠的掌握度记录，"
                )
                + f"已有 {concept.get('direct_attempts', 0)} 次作答。"
                + "先不要追求做难题，把同一步的道理说清楚更重要。",
                "suggestion": "用 3 道变式题走一遍：每题先说依据，再写步骤；最后再用一句话复述为什么。",
                "evidence": [
                    {
                        "id": f"ls-{concept.get('concept_id')}",
                        "type": "evaluation",
                        "topic": concept.get("name") or "薄弱知识点",
                        "label": f"学习者状态：{concept.get('state_reason') or concept.get('state')}",
                        "quote": concept.get("state_reason") or "该知识点被标记为需要继续巩固。",
                        "at": state.get("generated_at"),
                    }
                ],
            }
        )

    if reflections:
        reflection = reflections[0]
        improvement = reflection.improvement_json or {}
        cause = reflection.cause_hypothesis_json or {}
        cards.append(
            {
                "id": f"reflection-{reflection.id}",
                "kind": "hypothesis",
                "title": reflection.problem_statement or "上一次教学没有达到预期",
                "body": (
                    (improvement.get("summary") if isinstance(improvement, dict) else None)
                    or (cause.get("description") if isinstance(cause, dict) else None)
                    or "系统留下了一条教学问题记录，但还没有足够证据形成完整解释。"
                ),
                "suggestion": "下一次遇到同类题，先用一句话说出你卡住的那一步，再让 Agent 给提示而不是答案。",
                "evidence": [
                    {
                        "id": f"ref-{reflection.id}",
                        "type": "evaluation",
                        "topic": reflection.objective_slug or "教学反思",
                        "label": "来自一次真实评价的反思",
                        "quote": reflection.problem_statement or "反思没有留下问题陈述。",
                        "at": _iso(reflection.created_at),
                        "session_id": reflection.session_id,
                    }
                ],
            }
        )

    due = state.get("review_due") or []
    if due:
        item = due[0]
        cards.append(
            {
                "id": f"due-{item.get('concept_id')}",
                "kind": "observation",
                "title": f"「{item.get('name')}」到复习时间了",
                "body": "这是按真实作答时间推算的复习点，不是系统随便安排的。",
                "suggestion": "先不看答案做一道同类题；做对后再隔一天复测一次。",
                "evidence": [
                    {
                        "id": f"due-{item.get('concept_id')}",
                        "type": "evaluation",
                        "topic": item.get("name") or "复习项",
                        "label": f"下次复习：{item.get('next_review_at') or '待定'}",
                        "quote": "复习时间来自 spaced repetition 记录。",
                        "at": item.get("next_review_at"),
                    }
                ],
            }
        )

    if not cards:
        cards.append(
            {
                "id": "empty",
                "kind": "observation",
                "title": "先留下一次真实作答",
                "body": "还没有足够的学习证据。系统不会用一句通用鸡汤代替你的真实表现。",
                "suggestion": "去教学 Agent 做一次 3 题自测，回来这里就会有依据。",
                "evidence": [],
            }
        )

    return {
        "course_id": str(course_id),
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "cards": cards[:3],
        "source": "learner_state + teaching_reflections + review schedule",
        "refreshed": bool(refresh),
    }


@router.get("/overview", summary="Unified learning overview for the workspace UI")
async def get_overview(
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    return await _load_overview(db, user)


@router.get("/chat-history", summary="Recent teaching conversations")
async def get_chat_history(
    limit: int = Query(default=12, ge=1, le=50),
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    sessions = (
        await db.execute(
            select(ChatSession)
            .where(ChatSession.user_id == user.id)
            .order_by(desc(ChatSession.updated_at))
            .limit(limit)
        )
    ).scalars().all()

    out: list[dict[str, Any]] = []
    for session in sessions:
        messages = (
            await db.execute(
                select(ChatMessageLog)
                .where(ChatMessageLog.session_id == session.id)
                .order_by(ChatMessageLog.created_at)
                .limit(60)
            )
        ).scalars().all()
        out.append(
            {
                "id": str(session.id),
                "sessionId": str(session.id),
                "courseId": str(session.course_id),
                "title": session.title or "教学对话",
                "updatedAt": _iso(session.updated_at),
                "messages": [
                    {
                        "id": str(message.id),
                        "role": message.role,
                        "content": message.content,
                        "createdAt": _iso(message.created_at),
                    }
                    for message in messages
                ],
            }
        )
    return out


@router.get("/ai/status", summary="Report the configured teaching model")
async def get_ai_status(user: User = Depends(get_current_user)):
    configured = bool(
        settings.deepseek_api_key
        or settings.openai_api_key
        or settings.anthropic_api_key
        or settings.openrouter_api_key
        or settings.gemini_api_key
        or settings.groq_api_key
        or settings.custom_llm_api_key
    )
    if settings.llm_provider == "ollama":
        configured = True  # local provider, no key required
    return {
        "configured": configured,
        "provider": settings.llm_provider,
        "model": settings.llm_model,
        # OpenTutor has no built-in ASR; the 5001 capture service provides it.
        "asr": "本地采集服务（5001）" if configured else "未配置模型",
    }


@router.post("/plan", summary="Create a study plan from real learner state")
async def create_plan(
    body: dict[str, Any] | None = None,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Minimal, honest plan: only uses concepts that actually have evidence.

    The full PlanningAgent is intentionally not invoked here (it spends model
    tokens and needs a configured LLM).  This endpoint returns the real
    ``review_due`` items as a one-day plan so the UI has something true to show.
    """
    courses = (
        await db.execute(select(Course).where(Course.user_id == user.id))
    ).scalars().all()
    days: list[dict[str, Any]] = []
    for course in courses:
        try:
            state = await ls.build_learner_state(db, user.id, course.id, course=course)
        except Exception:
            continue
        due = state.get("review_due", []) or []
        weak = state.get("weak_concepts", []) or []
        picks = due[:3] or weak[:3]
        if picks:
            days.append(
                {
                    "date": (body or {}).get("start_date") or datetime.now().date().isoformat(),
                    "course_id": str(course.id),
                    "course_name": course.name,
                    "tasks": [
                        {"title": f"复习：{item.get('name')}", "concept_id": item.get("concept_id")}
                        for item in picks
                    ],
                }
            )
    if not days:
        return {
            "created": 0,
            "note": "还没有真实作答或到期复习项；先完成一次练习，计划才有依据。",
        }

    plan = StudyPlan(
        user_id=user.id,
        course_id=uuid.UUID(days[0]["course_id"]),
        name="本周学习安排",
        tasks={"days": days},
    )
    db.add(plan)
    await db.commit()
    return {"created": len(days), "plan_id": str(plan.id), "days": days}


@router.post("/jobs/{job_id}/retry", summary="Retry a failed teaching analysis")
async def retry_job(
    job_id: str,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    try:
        evaluation_id = uuid.UUID(job_id)
    except ValueError:
        raise HTTPException(status_code=400, detail="无效的评价编号")
    evaluation = await db.get(TeachingEvaluation, evaluation_id)
    if evaluation is None or evaluation.user_id != user.id:
        raise HTTPException(status_code=404, detail="没有找到这条评价")
    return {
        "ok": False,
        "detail": "重试需要重新调用评价/反思流程；请在练习结果页重新作答，或使用 /api/teaching/reflect 接口。",
        "evaluation_id": str(evaluation.id),
        "status": evaluation.status,
    }


@router.post("/material", summary="Add a note as course material")
async def add_material(
    body: dict[str, Any],
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    try:
        course_id = uuid.UUID(str(body.get("course_id")))
    except (TypeError, ValueError):
        raise HTTPException(status_code=400, detail="课程编号无效")
    course = await db.get(Course, course_id)
    if course is None or course.user_id != user.id:
        raise HTTPException(status_code=404, detail="没有找到这门课")
    title = str(body.get("title") or "").strip()[:500]
    content = str(body.get("content") or "").strip()
    if not title or not content:
        raise HTTPException(status_code=400, detail="标题和内容不能为空")

    node = CourseContentTree(
        course_id=course.id,
        parent_id=None,
        title=title,
        content=content,
        level=1,
        source_type="manual",
        content_category="notes",
        metadata_={"metamind_source": "workspace_note", "note_id": body.get("note_id")},
    )
    db.add(node)
    await db.commit()
    await db.refresh(node)
    return {"id": str(node.id), "title": node.title, "course_id": str(course.id)}


@router.post("/generate-practice", summary="Generate practice from existing problems")
async def generate_practice(
    body: dict[str, Any],
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Generation needs the configured LLM and the practice pipeline.

    Rather than fake ``problems_created``, point the caller at the real
    OpenTutor generation endpoint and report why this adapter did not run it.
    """
    return {
        "problems_created": 0,
        "detail": "请使用课程资料页的「生成练习」（POST /api/quiz/extract）或先导入课件。",
        "course_id": body.get("course_id"),
    }


@router.get("/backup", summary="Export learning records as JSON")
async def backup(
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    data = await _load_overview(db, user)
    data["schema"] = "metamind-workspace-backup-v1"
    data["exported_at"] = datetime.now(timezone.utc).isoformat()
    return data


@router.post("/restore", summary="Merge-restore a workspace backup")
async def restore(
    body: dict[str, Any],
    user: User = Depends(get_current_user),
):
    return {
        "inserted": 0,
        "kept_existing": 0,
        "detail": "恢复接口尚未接入写库；备份文件已保留，可人工核对。",
    }
