"""Scenario verification for METAMIND module 1 (Learner Model).

Exercises the real endpoint ``/api/teaching/learner-state`` against the running
OpenTutor instance, and proves the four acceptance scenarios:

1. fresh demo course  -> every concept is ``not_assessed``, overall mastery null
2. correct + wrong answers actually move the aggregate the right way
3. snapshots are append-only and never touch ConceptMastery / LearningProgress
4. reset restores ``not_assessed``

Run from ``apps/api`` with the API up (http://localhost:8000)::

    .venv-win/Scripts/python.exe scripts/metamind_demo/verify_learner_state.py

Exit code 0 = all scenarios passed.
"""

from __future__ import annotations

import argparse
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

import seed_demo_course as seeder  # noqa: E402  (also bootstraps settings.env)

from sqlalchemy import select  # noqa: E402

from database import async_session  # noqa: E402
from models.knowledge_graph import ConceptMastery  # noqa: E402
from models.practice import PracticeProblem  # noqa: E402
from models.progress import LearningProgress  # noqa: E402


def _http(method: str, path: str, body: dict | None = None, timeout: int = 60) -> dict:
    url = f"{BASE_URL}{path}"
    data = json.dumps(body).encode("utf-8") if body is not None else None
    request = urllib.request.Request(
        url,
        data=data,
        method=method,
        headers={"Content-Type": "application/json; charset=utf-8"} if data else {},
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:  # pragma: no cover - diagnostic path
        detail = exc.read().decode("utf-8", "replace")
        raise SystemExit(f"{method} {path} -> HTTP {exc.code}: {detail}") from exc


class Checker:
    def __init__(self) -> None:
        self.failures: list[str] = []
        self.passes: list[str] = []

    def check(self, label: str, condition: bool, detail: str = "") -> None:
        if condition:
            self.passes.append(label)
            print(f"  PASS  {label}{(' — ' + detail) if detail else ''}")
        else:
            self.failures.append(f"{label}{(' — ' + detail) if detail else ''}")
            print(f"  FAIL  {label}{(' — ' + detail) if detail else ''}")


def _concept(state: dict, goal_slug: str) -> dict:
    for concept in state["concepts"]:
        if concept["goal_slug"] == goal_slug:
            return concept
    raise SystemExit(f"concept with goal_slug={goal_slug!r} not found in state")


def _problems_by_slug() -> dict[str, dict]:
    listing = _http("GET", f"/api/quiz/{COURSE_ID}")
    return {(item.get("problem_metadata") or {}).get("item_slug"): item for item in listing}


def _submit(problem_id: str, answer: str) -> dict:
    return _http(
        "POST", "/api/quiz/submit",
        {"problem_id": problem_id, "user_answer": answer},
    )


async def _reset(spec: dict) -> dict:
    async with async_session() as db:
        removed = await seeder._reset_learner(db, spec)
        await db.commit()
    return removed


async def _mastery_snapshot_of_learner_state() -> list[dict]:
    """Raw pointers of the upstream mastery rows, for the no-mutation check."""
    async with async_session() as db:
        concepts = (
            await db.execute(
                select(ConceptMastery.knowledge_node_id, ConceptMastery.mastery_score,
                       ConceptMastery.practice_count, ConceptMastery.updated_at)
            )
        ).all()
        progress = (
            await db.execute(
                select(LearningProgress.content_node_id, LearningProgress.mastery_score,
                       LearningProgress.quiz_attempts, LearningProgress.fsrs_stability)
                .where(LearningProgress.course_id == uuid.UUID(COURSE_ID))
            )
        ).all()
    return [
        {"kind": "concept_mastery", "rows": [tuple(map(str, row)) for row in concepts]},
        {"kind": "learning_progress", "rows": [tuple(map(str, row)) for row in progress]},
    ]


async def main() -> int:
    spec = seeder._load_spec()
    checker = Checker()

    print("\n[scenario 1] 全新演示课程：应全部为 not_assessed，不得虚构掌握度")
    removed = await _reset(spec)
    print(f"  reset removed: {json.dumps(removed, ensure_ascii=False)}")
    state = _http("GET", f"/api/teaching/learner-state?course_id={COURSE_ID}")
    summary = state["assessment_summary"]
    checker.check("concepts_total == 3", summary["concepts_total"] == 3, str(summary["concepts_total"]))
    checker.check(
        "summary.state == not_assessed",
        summary["state"] == "not_assessed", summary["state"],
    )
    checker.check(
        "overall_mastery is null",
        summary["overall_mastery"] is None, repr(summary["overall_mastery"]),
    )
    checker.check(
        "every concept mastery_score is null",
        all(c["mastery_score"] is None for c in state["concepts"]),
    )
    checker.check(
        "every concept state == not_assessed",
        all(c["state"] == "not_assessed" for c in state["concepts"]),
    )
    checker.check(
        "weak_concepts empty (nothing assessed yet)",
        state["weak_concepts"] == [],
    )
    checker.check("activity.quiz_attempts == 0", state["activity"]["quiz_attempts"] == 0)

    print("\n[scenario 1b] 未评估时写快照：应全部 skipped，不写 0.0")
    snap = _http(
        "POST", "/api/teaching/learner-state/snapshot",
        {"course_id": COURSE_ID, "run_tag": "run01"},
    )
    checker.check("snapshot written == 0", len(snap["written"]) == 0, str(len(snap["written"])))
    checker.check(
        "snapshot skipped == 3 with reason not_assessed",
        len(snap["skipped"]) == 3
        and all(item["reason"] == "not_assessed" for item in snap["skipped"]),
    )

    print("\n[scenario 2] 提交 1 对 1 错：聚合状态应反映真实变化")
    problems = _problems_by_slug()
    checker.check("test set A present", "a1" in problems and "a2" in problems)

    base_state = await _mastery_snapshot_of_learner_state()

    submit_ok = _submit(problems["a1"]["id"], "b")
    checker.check("a1 graded correct", submit_ok["is_correct"] is True)
    submit_bad = _submit(problems["a2"]["id"], "a")
    checker.check("a2 graded wrong", submit_bad["is_correct"] is False)

    state2 = _http("GET", f"/api/teaching/learner-state?course_id={COURSE_ID}")
    summary2 = state2["assessment_summary"]
    g1 = _concept(state2, "g1")
    g2 = _concept(state2, "g2")
    checker.check(
        "summary.state no longer not_assessed",
        summary2["state"] == "partially_assessed", summary2["state"],
    )
    checker.check(
        "concepts_assessed == 2", summary2["concepts_assessed"] == 2,
        str(summary2["concepts_assessed"]),
    )
    checker.check(
        "overall_mastery now numeric",
        isinstance(summary2["overall_mastery"], (int, float)),
        repr(summary2["overall_mastery"]),
    )
    checker.check(
        "g1 (answered correctly) is assessed with a score",
        g1["state"] != "not_assessed" and g1["mastery_score"] is not None,
        f"state={g1['state']} score={g1['mastery_score']}",
    )
    checker.check(
        "g1 mastery_source == concept_mastery", g1["mastery_source"] == "concept_mastery",
        g1["mastery_source"],
    )
    checker.check(
        "g2 (answered wrong) records a wrong answer",
        g2["wrong"] >= 1, str(g2["wrong"]),
    )
    checker.check(
        "g2 has error_categories from the real classifier",
        bool(g2["error_categories"]), json.dumps(g2["error_categories"], ensure_ascii=False),
    )
    checker.check(
        "g1 accuracy reflects the single correct answer",
        g1["accuracy"] is not None and g1["accuracy"] == 1.0, repr(g1["accuracy"]),
    )
    checker.check(
        "error_profile counts the wrong answer",
        state2["error_profile"]["total_wrong_answers"] >= 1,
        str(state2["error_profile"]["total_wrong_answers"]),
    )
    checker.check(
        "evidence.practice_results == 2", state2["evidence"]["practice_results"] == 2,
        str(state2["evidence"]["practice_results"]),
    )

    print("\n[scenario 3] 快照为 append-only，且不得修改掌握度/BKT/FSRS 状态")
    before = await _mastery_snapshot_of_learner_state()
    snap2 = _http(
        "POST", "/api/teaching/learner-state/snapshot",
        {"course_id": COURSE_ID, "run_tag": "run01"},
    )
    after = await _mastery_snapshot_of_learner_state()
    checker.check(
        "snapshot written for the 2 assessed concepts",
        len(snap2["written"]) == 2, str(len(snap2["written"])),
    )
    checker.check(
        "snapshot tags are namespaced mm:run01:*",
        all(item["tag"].startswith("mm:run01:") for item in snap2["written"]),
        str([item["tag"] for item in snap2["written"]]),
    )
    checker.check(
        "ConceptMastery rows unchanged by snapshot write",
        before[0]["rows"] == after[0]["rows"],
    )
    checker.check(
        "LearningProgress rows unchanged by snapshot write",
        before[1]["rows"] == after[1]["rows"],
    )
    print(f"  note: base_state captured before answers had "
          f"{len(base_state[0]['rows'])} concept_mastery rows")

    print("\n[scenario 3b] 按 concept 追踪快照历史")
    history = _http(
        "GET",
        f"/api/teaching/learner-state/history?course_id={COURSE_ID}"
        f"&concept_id={g1['concept_id']}",
    )
    checker.check(
        "history returns mm: points for that concept",
        len(history["points"]) >= 1
        and all(point["tag"].startswith("mm:") for point in history["points"]),
        f"{len(history['points'])} point(s)",
    )
    checker.check(
        "history points carry run_tag=run01",
        all(point["run_tag"] == "run01" for point in history["points"]),
    )
    upstream = _http(
        "GET",
        f"/api/teaching/learner-state/history?course_id={COURSE_ID}"
        f"&concept_id={g1['concept_id']}&include_upstream=true",
    )
    checker.check(
        "include_upstream=true also shows tracker snapshots",
        len(upstream["points"]) >= len(history["points"]),
        f"{len(upstream['points'])} vs {len(history['points'])}",
    )

    print("\n[scenario 4] 重置学习者状态：应回到 not_assessed")
    removed2 = await _reset(spec)
    print(f"  reset removed: {json.dumps(removed2, ensure_ascii=False)}")
    state3 = _http("GET", f"/api/teaching/learner-state?course_id={COURSE_ID}")
    checker.check(
        "summary.state back to not_assessed",
        state3["assessment_summary"]["state"] == "not_assessed",
        state3["assessment_summary"]["state"],
    )
    checker.check(
        "overall_mastery back to null",
        state3["assessment_summary"]["overall_mastery"] is None,
    )
    checker.check(
        "all concept mastery back to null",
        all(c["mastery_score"] is None for c in state3["concepts"]),
    )
    history_after = _http(
        "GET",
        f"/api/teaching/learner-state/history?course_id={COURSE_ID}"
        f"&concept_id={g1['concept_id']}",
    )
    checker.check(
        "snapshot history cleared by reset",
        history_after["points"] == [],
        f"{len(history_after['points'])} point(s)",
    )
    checker.check(
        "practice problems untouched by reset",
        len(_problems_by_slug()) >= 8,
    )

    print("\n" + "=" * 60)
    print(f"PASSED {len(checker.passes)} checks, FAILED {len(checker.failures)}")
    for failure in checker.failures:
        print(f"  - {failure}")
    return 0 if not checker.failures else 1


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Verify METAMIND learner-state behaviour.")
    parser.add_argument("--base-url", default=os.environ.get("OPENTUTOR_API", "http://localhost:8000"))
    parser.add_argument("--course-id", default=None)
    args = parser.parse_args()
    BASE_URL = args.base_url.rstrip("/")
    COURSE_ID = args.course_id or str(seeder._uid("course"))
    raise SystemExit(asyncio.run(main()))
