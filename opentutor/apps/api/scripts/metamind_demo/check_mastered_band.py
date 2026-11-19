"""Ad-hoc check: does the third band (mastered) actually become reachable?

Submits repeated correct answers on one concept, reads the aggregate, prints the
state transitions, then resets the demo learner so the DB is left clean.
"""

from __future__ import annotations

import asyncio
import json
import sys
import urllib.request
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent
API_ROOT = SCRIPT_DIR.parents[1]
sys.path.insert(0, str(API_ROOT))
sys.path.insert(0, str(SCRIPT_DIR))

import seed_demo_course as seeder  # noqa: E402

from database import async_session  # noqa: E402

BASE = "http://localhost:8000"
COURSE_ID = str(seeder._uid("course"))


def http(method: str, path: str, body: dict | None = None) -> dict:
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(
        f"{BASE}{path}", data=data, method=method,
        headers={"Content-Type": "application/json"} if data else {},
    )
    with urllib.request.urlopen(req, timeout=60) as resp:
        return json.loads(resp.read().decode())


async def reset() -> None:
    async with async_session() as db:
        await seeder._reset_learner(db, seeder._load_spec())
        await db.commit()


def main() -> int:
    asyncio.run(reset())
    listing = http("GET", f"/api/quiz/{COURSE_ID}")
    by_slug = {(item.get("problem_metadata") or {}).get("item_slug"): item for item in listing}
    target = by_slug["a1"]
    print(f"target problem: {target['question'][:30]}... ({target['question_type']})")

    for attempt in range(1, 5):
        result = http("POST", "/api/quiz/submit", {"problem_id": target["id"], "user_answer": "b"})
        state = http("GET", f"/api/teaching/learner-state?course_id={COURSE_ID}")
        concept = next(c for c in state["concepts"] if c["goal_slug"] == "g1")
        print(
            f"attempt {attempt}: graded={result['is_correct']} "
            f"mastery={concept['mastery_score']} attempts={concept['direct_attempts']} "
            f"state={concept['state']} reason={concept['state_reason']}"
        )
        if concept["state"] == "mastered":
            summary = state["assessment_summary"]
            print(
                f"  -> summary={summary['state']} mastered={summary['concepts_mastered']} "
                f"overall={summary['overall_mastery']}"
            )
            break

    state = http("GET", f"/api/teaching/learner-state?course_id={COURSE_ID}")
    reached = any(c["state"] == "mastered" for c in state["concepts"])
    print(f"\nmastered band reachable: {reached}")
    asyncio.run(reset())
    print("reset done")
    return 0 if reached else 1


if __name__ == "__main__":
    raise SystemExit(main())
