"""METAMIND demo course seeder — idempotent, tagged, non-destructive.

Stage 0.5 deliverable. Creates (or refreshes) a clearly-tagged synthetic demo
course with 3 concepts, 3 study goals, and two parallel deterministic test
sets (A / B) used by the Evolution Engine demonstration.

Design rules
------------
* Every row this script owns is deterministic (uuid5) and tagged with
  ``metamind_demo`` + ``demo_key`` in its metadata column, so it never mixes
  with a real learner's data.
* Re-running is safe: rows are upserted by deterministic id. Practice problems
  that already have answer history are never rewritten (that would corrupt
  recorded evidence); they are reported as ``skipped_has_results``.
* Nothing outside the demo scope is touched. The pre-existing course
  ``自进化`` (created by the user) is left untouched.
* No secrets are read, printed, or written. ``settings.env`` is loaded into
  the process environment only.

Usage (from ``apps/api`` with the app's own interpreter)::

    .venv-win/Scripts/python.exe scripts/metamind_demo/seed_demo_course.py --seed
    .venv-win/Scripts/python.exe scripts/metamind_demo/seed_demo_course.py --verify
    .venv-win/Scripts/python.exe scripts/metamind_demo/seed_demo_course.py --reset-learner --yes
    .venv-win/Scripts/python.exe scripts/metamind_demo/seed_demo_course.py --purge --yes
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
import uuid
from pathlib import Path
from typing import Any

SCRIPT_PATH = Path(__file__).resolve()
API_ROOT = SCRIPT_PATH.parents[2]
REPO_ROOT = SCRIPT_PATH.parents[4]
SPEC_PATH = SCRIPT_PATH.parent / "demo_spec.json"
SETTINGS_ENV = REPO_ROOT / "native-data" / "settings.env"

# Deterministic id namespace — never change, or re-running creates duplicates.
NAMESPACE = uuid.UUID("6f1c4d1a-8f3e-5c2b-9a7d-2b6e0c5d8a41")

DEMO_TAG = "metamind_demo"


def _bootstrap_env() -> None:
    """Load native-data/settings.env before importing config/database."""
    if str(API_ROOT) not in sys.path:
        sys.path.insert(0, str(API_ROOT))
    if not SETTINGS_ENV.exists():
        raise SystemExit(f"settings file not found: {SETTINGS_ENV}")
    for raw_line in SETTINGS_ENV.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        key = key.strip()
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
            value = value[1:-1]
        os.environ.setdefault(key, value)


_bootstrap_env()

from sqlalchemy import delete, select, func  # noqa: E402

from database import async_session  # noqa: E402
from models.agent_kv import AgentKV  # noqa: E402
from models.agent_task import AgentTask  # noqa: E402
from models.chat_message import ChatMessageLog  # noqa: E402
from models.chat_session import ChatSession  # noqa: E402
from models.content import CourseContentTree  # noqa: E402
from models.course import Course  # noqa: E402
from models.ingestion import WrongAnswer  # noqa: E402
from models.intervention_outcome import InterventionOutcome  # noqa: E402
from models.knowledge_graph import ConceptMastery, KnowledgeEdge, KnowledgeNode  # noqa: E402
from models.mastery_snapshot import MasterySnapshot  # noqa: E402
from models.memory import ConversationMemory  # noqa: E402
from models.practice import PracticeProblem, PracticeResult  # noqa: E402
from models.progress import LearningProgress  # noqa: E402
from models.study_goal import StudyGoal  # noqa: E402
from models.usage_event import UsageEvent  # noqa: E402
from models.user import User  # noqa: E402
from services.practice.answers import grade_select_all_answer, grade_text_answer  # noqa: E402


def _uid(*parts: str) -> uuid.UUID:
    return uuid.uuid5(NAMESPACE, ":".join((DEMO_KEY, *parts)))


def _load_spec() -> dict[str, Any]:
    return json.loads(SPEC_PATH.read_text(encoding="utf-8"))


DEMO_KEY = _load_spec()["demo_key"] if SPEC_PATH.exists() else "metamind_self_evolution_v1"


# ── helpers ────────────────────────────────────────────────────────────────


async def _single_user(db) -> User:
    result = await db.execute(select(User).order_by(User.created_at).limit(1))
    user = result.scalar_one_or_none()
    if user is None:
        raise SystemExit("no user row found; start OpenTutor once so the local owner exists")
    return user


def _merge_tags(existing: dict | None, spec: dict[str, Any]) -> dict:
    merged = dict(existing or {})
    merged[DEMO_TAG] = True
    merged["demo_key"] = DEMO_KEY
    merged["data_kind"] = spec.get("data_kind", "synthetic_demo")
    merged["demo_spec_version"] = spec.get("spec_version", 1)
    return merged


async def _seed_course(db, spec: dict[str, Any], user: User) -> tuple[Course, str]:
    course_id = _uid("course")
    course = await db.get(Course, course_id)
    action = "updated"
    if course is None:
        course = Course(id=course_id, user_id=user.id)
        db.add(course)
        action = "created"
    course.name = spec["course"]["name"]
    course.description = spec["course"]["description"]
    course.active_scene = spec["course"].get("active_scene", "study_session")
    course.metadata_ = _merge_tags(
        course.metadata_ if isinstance(course.metadata_, dict) else None, spec
    )
    course.metadata_["disclaimer"] = spec.get("disclaimer", "")
    course.metadata_["user_course"] = False
    return course, action


async def _seed_content_nodes(db, spec: dict[str, Any], course_id: uuid.UUID):
    ids: dict[str, uuid.UUID] = {}
    actions: dict[str, int] = {"created": 0, "updated": 0}
    for node in spec["content_nodes"]:
        node_id = _uid("content", node["slug"])
        ids[node["slug"]] = node_id
        row = await db.get(CourseContentTree, node_id)
        if row is None:
            row = CourseContentTree(id=node_id, course_id=course_id)
            db.add(row)
            actions["created"] += 1
        else:
            actions["updated"] += 1
        row.parent_id = None if node["level"] == 0 else ids.get("root")
        row.title = node["title"]
        row.content = node["content"]
        row.level = node["level"]
        row.order_index = node["order_index"]
        row.source_type = "manual"
        row.source_file = "metamind_demo/demo_spec.json"
        row.content_category = "concept" if node["level"] > 0 else "overview"
        row.metadata_ = _merge_tags(row.metadata_ if isinstance(row.metadata_, dict) else None, spec)
    return ids, actions


async def _seed_concepts(db, spec, course_id, content_ids) -> dict[str, uuid.UUID]:
    ids: dict[str, uuid.UUID] = {}
    actions = {"created": 0, "updated": 0}
    for concept in spec["concepts"]:
        node_id = _uid("concept", concept["slug"])
        ids[concept["slug"]] = node_id
        row = await db.get(KnowledgeNode, node_id)
        if row is None:
            # Respect the (course_id, name) unique constraint if an earlier
            # partial run already inserted this concept under another id.
            existing = await db.execute(
                select(KnowledgeNode).where(
                    KnowledgeNode.course_id == course_id,
                    KnowledgeNode.name == concept["name"],
                )
            )
            row = existing.scalar_one_or_none()
        if row is None:
            row = KnowledgeNode(id=node_id, course_id=course_id)
            db.add(row)
            actions["created"] += 1
        else:
            ids[concept["slug"]] = row.id
            actions["updated"] += 1
        row.name = concept["name"]
        row.description = concept["description"]
        row.content_node_id = content_ids.get(concept["content_node_slug"])
        meta = _merge_tags(row.metadata_ if isinstance(row.metadata_, dict) else None, spec)
        meta["goal_slug"] = concept["goal_slug"]
        meta["bloom_level"] = 2
        row.metadata_ = meta
    return ids, actions


async def _seed_edges(db, spec, concept_ids) -> dict[str, int]:
    actions = {"created": 0, "updated": 0}
    for edge in spec["edges"]:
        edge_id = _uid("edge", edge["source"], edge["target"], edge["relation_type"])
        row = await db.get(KnowledgeEdge, edge_id)
        if row is None:
            row = KnowledgeEdge(id=edge_id)
            db.add(row)
            actions["created"] += 1
        else:
            actions["updated"] += 1
        row.source_id = concept_ids[edge["source"]]
        row.target_id = concept_ids[edge["target"]]
        row.relation_type = edge["relation_type"]
        row.weight = float(edge["weight"])
    return actions


async def _seed_goals(db, spec, course_id, user) -> dict[str, int]:
    actions = {"created": 0, "updated": 0}
    for goal in spec["goals"]:
        goal_id = _uid("goal", goal["slug"])
        row = await db.get(StudyGoal, goal_id)
        if row is None:
            row = StudyGoal(id=goal_id, user_id=user.id, course_id=course_id)
            db.add(row)
            actions["created"] += 1
        else:
            actions["updated"] += 1
        row.title = goal["title"]
        row.objective = goal["objective"]
        row.success_metric = goal["success_metric"]
        row.status = "active"
        row.metadata_json = _merge_tags(
            row.metadata_json if isinstance(row.metadata_json, dict) else None, spec
        )
        row.metadata_json["goal_slug"] = goal["slug"]
    return actions


def _problem_metadata(spec, set_key, problem) -> dict:
    meta = _merge_tags(None, spec)
    meta.update(
        {
            "test_set": set_key,
            "test_set_label": spec["problem_sets"][set_key]["label"],
            "goal": problem["goal"],
            "eval_dimension": problem["eval_dimension"],
            "auto_grade": "deterministic",
            "item_slug": problem["slug"],
            "core_concept": spec["concepts"],
        }
    )
    # Keep metadata compact — the concept list is only needed by the UI card.
    meta["core_concept"] = next(
        (c["name"] for c in spec["concepts"] if c["goal_slug"] == problem["goal"]), ""
    )
    return meta


async def _seed_problems(db, spec, course_id, content_ids, concept_ids) -> dict[str, Any]:
    actions: dict[str, Any] = {"created": 0, "updated": 0, "skipped_has_results": []}
    concept_name_by_goal = {c["goal_slug"]: c["name"] for c in spec["concepts"]}
    goal_to_concept = {c["goal_slug"]: c["slug"] for c in spec["concepts"]}
    goal_to_content = {
        c["goal_slug"]: content_ids.get(c["content_node_slug"]) for c in spec["concepts"]
    }
    order = {"A": 0, "B": 100}
    for set_key, set_payload in spec["problem_sets"].items():
        for idx, problem in enumerate(set_payload["problems"]):
            problem_id = _uid("problem", set_key, problem["slug"])
            row = await db.get(PracticeProblem, problem_id)
            if row is not None:
                answered = await db.execute(
                    select(func.count(PracticeResult.id)).where(
                        PracticeResult.problem_id == problem_id
                    )
                )
                if (answered.scalar() or 0) > 0:
                    actions["skipped_has_results"].append(f"{set_key}:{problem['slug']}")
                    continue
                actions["updated"] += 1
            else:
                row = PracticeProblem(id=problem_id, course_id=course_id)
                db.add(row)
                actions["created"] += 1
            row.question_type = problem["question_type"]
            row.question = problem["question"]
            row.options = problem["options"]
            row.correct_answer = problem["correct_answer"]
            row.explanation = problem["explanation"]
            row.order_index = order[set_key] + idx
            row.knowledge_points = [concept_name_by_goal[problem["goal"]]]
            row.source = "ai_generated"
            row.source_owner = "ai"
            row.source_version = 1
            row.is_diagnostic = False
            row.is_archived = False
            row.locked = False
            row.difficulty_layer = problem["difficulty_layer"]
            row.content_node_id = goal_to_content.get(problem["goal"])
            meta = _problem_metadata(spec, set_key, problem)
            meta["concept_slug"] = goal_to_concept[problem["goal"]]
            meta["concept_id"] = str(concept_ids[goal_to_concept[problem["goal"]]])
            row.problem_metadata = meta
    return actions


# ── verification ───────────────────────────────────────────────────────────


async def _verify(db, spec) -> dict[str, Any]:
    course_id = _uid("course")
    report: dict[str, Any] = {"demo_key": DEMO_KEY, "course_id": str(course_id)}
    course = await db.get(Course, course_id)
    report["course_present"] = course is not None
    if course is None:
        report["ok"] = False
        return report
    report["course_name"] = course.name
    report["tagged"] = bool((course.metadata_ or {}).get(DEMO_TAG))

    nodes = (
        await db.execute(
            select(CourseContentTree)
            .where(CourseContentTree.course_id == course_id)
            .order_by(CourseContentTree.order_index)
        )
    ).scalars().all()
    report["content_nodes"] = [
        {"slug_title": n.title, "level": n.level, "chars": len(n.content or "")} for n in nodes
    ]

    concepts = (
        await db.execute(select(KnowledgeNode).where(KnowledgeNode.course_id == course_id))
    ).scalars().all()
    report["concepts"] = [{"name": c.name, "id": str(c.id)} for c in concepts]

    edge_count = await db.execute(
        select(func.count(KnowledgeEdge.id)).where(
            KnowledgeEdge.source_id.in_([c.id for c in concepts])
        )
    )
    report["edges"] = edge_count.scalar() or 0

    goals = (
        await db.execute(select(StudyGoal).where(StudyGoal.course_id == course_id))
    ).scalars().all()
    report["goals"] = [{"title": g.title, "status": g.status} for g in goals]

    problems = (
        await db.execute(
            select(PracticeProblem)
            .where(PracticeProblem.course_id == course_id)
            .where(PracticeProblem.is_diagnostic == False)  # noqa: E712
            .order_by(PracticeProblem.order_index)
        )
    ).scalars().all()
    derived = await db.execute(
        select(func.count(PracticeProblem.id)).where(
            PracticeProblem.course_id == course_id,
            PracticeProblem.is_diagnostic == True,  # noqa: E712
        )
    )
    report["derived_diagnostic_problems"] = derived.scalar() or 0
    per_set: dict[str, int] = {}
    grading_ok = True
    grading_detail: list[dict] = []
    for p in problems:
        meta = p.problem_metadata or {}
        set_key = str(meta.get("test_set", "?"))
        per_set[set_key] = per_set.get(set_key, 0) + 1
        if p.question_type == "mc":
            correct = grade_text_answer(p.correct_answer or "", p.correct_answer or "")
        else:
            correct = grade_select_all_answer(
                p.correct_answer or "", p.correct_answer or "", p.options
            )
        grading_ok = grading_ok and correct
        grading_detail.append(
            {"set": set_key, "slug": meta.get("item_slug"), "type": p.question_type,
             "reference_grades_correct": correct}
        )
    report["problems_per_set"] = per_set
    report["problems_total"] = len(problems)
    report["deterministic_grading_selfcheck"] = grading_ok
    report["grading_detail"] = grading_detail

    expected_per_set = len(spec["problem_sets"]["A"]["problems"])
    report["structure_ok"] = (
        len(nodes) == len(spec["content_nodes"])
        and len(concepts) == len(spec["concepts"])
        and len(goals) == len(spec["goals"])
        and all(per_set.get(k, 0) == expected_per_set for k in spec["problem_sets"])
        and grading_ok
    )

    # Retrieval check — the real Tutor context builder uses this exact path.
    from services.search.hybrid import hybrid_search

    hits = await hybrid_search(db, course_id, "自进化 定义 四个必要条件 记忆 区别", limit=5)
    report["retrieval_hits"] = [
        {"title": h.get("title"), "score": round(float(h.get("score") or 0), 3), "source": h.get("source")}
        for h in hits
    ]
    concept1_id = str(_uid("content", "concept-1"))
    report["retrieval_finds_concept1"] = any(h.get("id") == concept1_id for h in hits)

    report["ok"] = bool(
        report["tagged"] and report["structure_ok"] and report["retrieval_finds_concept1"]
    )
    return report


# ── destructive operations (explicit flags only) ───────────────────────────


async def _reset_learner(db, spec) -> dict[str, int]:
    """Reset demo-scope learning state so Run 01 and Run 02 start equal."""
    course_id = _uid("course")
    user = await _single_user(db)
    problem_ids = [
        _uid("problem", set_key, p["slug"])
        for set_key, payload in spec["problem_sets"].items()
        for p in payload["problems"]
    ]
    concept_ids = [_uid("concept", c["slug"]) for c in spec["concepts"]]
    removed: dict[str, int] = {}

    # Derived diagnostic children (created by OpenTutor when a wrong answer is
    # submitted) belong to the demo scope too — drop them so a run always
    # starts from the curated 8-item bank.
    child_rows = await db.execute(
        select(PracticeProblem.id).where(
            PracticeProblem.parent_problem_id.in_(problem_ids),
            PracticeProblem.is_diagnostic == True,  # noqa: E712
        )
    )
    child_ids = [row[0] for row in child_rows.all()]
    all_problem_ids = problem_ids + child_ids

    async def _del(name: str, stmt) -> None:
        result = await db.execute(stmt)
        removed[name] = result.rowcount or 0

    await _del(
        "practice_results",
        delete(PracticeResult).where(
            PracticeResult.user_id == user.id,
            PracticeResult.problem_id.in_(all_problem_ids),
        ),
    )
    if child_ids:
        await _del(
            "derived_diagnostic_problems",
            delete(PracticeProblem).where(PracticeProblem.id.in_(child_ids)),
        )
    else:
        removed["derived_diagnostic_problems"] = 0
    await _del(
        "wrong_answers",
        delete(WrongAnswer).where(
            WrongAnswer.user_id == user.id, WrongAnswer.course_id == course_id
        ),
    )
    await _del(
        "concept_mastery",
        delete(ConceptMastery).where(
            ConceptMastery.user_id == user.id,
            ConceptMastery.knowledge_node_id.in_(concept_ids),
        ),
    )
    await _del(
        "learning_progress",
        delete(LearningProgress).where(
            LearningProgress.user_id == user.id, LearningProgress.course_id == course_id
        ),
    )
    await _del(
        "mastery_snapshots",
        delete(MasterySnapshot).where(
            MasterySnapshot.user_id == user.id, MasterySnapshot.course_id == course_id
        ),
    )
    await _del(
        "intervention_outcomes",
        delete(InterventionOutcome).where(
            InterventionOutcome.user_id == user.id,
            InterventionOutcome.course_id == course_id,
        ),
    )
    # ── Evidence is preserved on purpose (P0-1) ─────────────────────────────
    # teaching_evaluations, teaching_reflections, skill_versions, skill_invocations
    # and chat transcripts are the *audit evidence* of a run (P0-4 item 1/2/6/8/9).
    # They are NOT deleted here. A run reset only resets the learner *state*
    # (mastery / practice results / snapshots) so Run01 and Run02 start from the
    # same initial learner, while every evaluation/reflection/answer stays to be
    # compared. Run02 writes a fresh run_tag, so the two runs never mix.
    # (Reflection rows are additionally protected from FK cascade: their
    # evaluation_id is now nullable SET NULL, so deleting an evaluation would
    # only null the link, not erase the reflection.)

    # Runtime state that leaks *pedagogical* context (tutor notes / socratic
    # engine state / extracted strategies) is still reset so Run02 does not
    # inherit Run01's internal run state. Chat transcripts are evidence and are
    # NOT deleted; chat scoping means Run02 gets its own session.
    await _del(
        "agent_kv",
        delete(AgentKV).where(
            AgentKV.user_id == user.id, AgentKV.course_id == course_id
        ),
    )
    await _del("agent_tasks", delete(AgentTask).where(AgentTask.course_id == course_id))
    await _del(
        "conversation_memories",
        delete(ConversationMemory).where(
            ConversationMemory.user_id == user.id,
            ConversationMemory.course_id == course_id,
        ),
    )
    await _del("usage_events", delete(UsageEvent).where(UsageEvent.course_id == course_id))
    return removed


async def _purge(db, spec) -> dict[str, int]:
    """Remove demo-tagged rows. Never touches non-demo courses."""
    course_id = _uid("course")
    course = await db.get(Course, course_id)
    removed: dict[str, int] = {}
    if course is None or not (course.metadata_ or {}).get(DEMO_TAG):
        removed["course"] = 0
        removed["note"] = 0
        return removed

    problem_ids = [
        _uid("problem", set_key, p["slug"])
        for set_key, payload in spec["problem_sets"].items()
        for p in payload["problems"]
    ]
    concept_ids = [_uid("concept", c["slug"]) for c in spec["concepts"]]
    session_ids = [
        row[0]
        for row in (
            await db.execute(select(ChatSession.id).where(ChatSession.course_id == course_id))
        ).all()
    ]
    if session_ids:
        result = await db.execute(
            delete(ChatMessageLog).where(ChatMessageLog.session_id.in_(session_ids))
        )
        removed["chat_message_logs"] = result.rowcount or 0

    for name, stmt in (
        ("practice_results", delete(PracticeResult).where(PracticeResult.problem_id.in_(problem_ids))),
        ("wrong_answers", delete(WrongAnswer).where(WrongAnswer.course_id == course_id)),
        ("concept_mastery", delete(ConceptMastery).where(ConceptMastery.knowledge_node_id.in_(concept_ids))),
        ("knowledge_edges", delete(KnowledgeEdge).where(KnowledgeEdge.source_id.in_(concept_ids))),
        ("learning_progress", delete(LearningProgress).where(LearningProgress.course_id == course_id)),
        ("mastery_snapshots", delete(MasterySnapshot).where(MasterySnapshot.course_id == course_id)),
        ("intervention_outcomes", delete(InterventionOutcome).where(InterventionOutcome.course_id == course_id)),
        ("practice_problems", delete(PracticeProblem).where(PracticeProblem.course_id == course_id)),
        ("content_nodes", delete(CourseContentTree).where(CourseContentTree.course_id == course_id)),
        ("knowledge_nodes", delete(KnowledgeNode).where(KnowledgeNode.course_id == course_id)),
        ("study_goals", delete(StudyGoal).where(StudyGoal.course_id == course_id)),
        ("chat_sessions", delete(ChatSession).where(ChatSession.course_id == course_id)),
        ("agent_kv", delete(AgentKV).where(AgentKV.course_id == course_id)),
        ("agent_tasks", delete(AgentTask).where(AgentTask.course_id == course_id)),
        ("conversation_memories", delete(ConversationMemory).where(ConversationMemory.course_id == course_id)),
        ("usage_events", delete(UsageEvent).where(UsageEvent.course_id == course_id)),
    ):
        result = await db.execute(stmt)
        removed[name] = result.rowcount or 0
    await db.delete(course)
    removed["course"] = 1
    return removed


# ── entrypoint ─────────────────────────────────────────────────────────────


async def _run(args) -> int:
    spec = _load_spec()
    async with async_session() as db:
        user = await _single_user(db)
        out: dict[str, Any] = {"demo_key": DEMO_KEY, "user_id": str(user.id)}

        if args.purge:
            if not args.yes:
                print("refusing to purge without --yes")
                return 2
            removed = await _purge(db, spec)
            await db.commit()
            print(json.dumps({"purged": removed}, ensure_ascii=False, indent=2))
            return 0

        if args.reset_learner:
            if not args.yes:
                print("refusing to reset learner data without --yes")
                return 2
            removed = await _reset_learner(db, spec)
            await db.commit()
            print(json.dumps({"reset": removed}, ensure_ascii=False, indent=2))
            return 0

        if args.verify:
            report = await _verify(db, spec)
            print(json.dumps(report, ensure_ascii=False, indent=2))
            return 0 if report.get("ok") else 1

        # seed (default) — dry run still computes actions but rolls back
        course, course_action = await _seed_course(db, spec, user)
        out["course"] = course_action
        await db.flush()
        content_ids, content_actions = await _seed_content_nodes(db, spec, course.id)
        await db.flush()
        concept_ids, concept_actions = await _seed_concepts(db, spec, course.id, content_ids)
        await db.flush()
        edge_actions = await _seed_edges(db, spec, concept_ids)
        await db.flush()
        goal_actions = await _seed_goals(db, spec, course.id, user)
        await db.flush()
        problem_actions = await _seed_problems(db, spec, course.id, content_ids, concept_ids)
        await db.flush()

        out.update(
            {
                "content_nodes": content_actions,
                "concepts": concept_actions,
                "edges": edge_actions,
                "goals": goal_actions,
                "problems": problem_actions,
            }
        )

        if args.dry_run:
            await db.rollback()
            out["dry_run"] = True
            print(json.dumps(out, ensure_ascii=False, indent=2))
            return 0

        await db.commit()
        out["dry_run"] = False
        print(json.dumps(out, ensure_ascii=False, indent=2))

    # verify in a fresh session so we read committed state
    async with async_session() as db:
        report = await _verify(db, spec)
    print(json.dumps({"verify": report}, ensure_ascii=False, indent=2))
    return 0 if report.get("ok") else 1


def main() -> None:
    parser = argparse.ArgumentParser(description="Seed the METAMIND demo course (idempotent).")
    parser.add_argument("--seed", action="store_true", help="insert/update demo content (default)")
    parser.add_argument("--verify", action="store_true", help="read-only verification report")
    parser.add_argument("--dry-run", action="store_true", help="run inserts then roll back")
    parser.add_argument("--reset-learner", action="store_true", help="clear demo-scope learning state")
    parser.add_argument("--purge", action="store_true", help="delete all demo-tagged rows")
    parser.add_argument("--yes", action="store_true", help="required for destructive flags")
    args = parser.parse_args()
    raise SystemExit(asyncio.run(_run(args)))


if __name__ == "__main__":
    main()
