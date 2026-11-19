"""Check that the METAMIND 'mm:' snapshot tag cannot be mistaken for a real gap.

Three assertions:

1. ``/api/progress/courses/{id}/velocity`` is not moved by 'mm:' rows.
2. ``/api/progress/overview`` gap_type_breakdown never contains an 'mm:' key
   (that endpoint reads ``learning_progress.gap_type``, a different column).
3. ``/api/quiz/{id}/mastery-history`` is the ONE place where 'mm:' tags are
   visible, and it is visible by design (raw snapshot feed).

Run from ``apps/api``::

    .venv-win/Scripts/python.exe scripts/metamind_demo/check_gap_namespace.py
"""

from __future__ import annotations

import asyncio
import json
import os
import sys
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


def http(method: str, path: str, body: dict | None = None, timeout: int = 120) -> dict | list:
    data = json.dumps(body).encode("utf-8") if body is not None else None
    request = urllib.request.Request(
        f"{BASE}{path}", data=data, method=method,
        headers={"Content-Type": "application/json"} if data else {},
    )
    with urllib.request.urlopen(request, timeout=timeout) as response:
        return json.loads(response.read().decode("utf-8"))


async def reset() -> None:
    async with async_session() as db:
        await seeder._reset_learner(db, seeder._load_spec())
        await db.commit()


def main() -> int:
    passed: list[str] = []
    failed: list[str] = []

    def ok(label: str, condition: bool, detail: str = "") -> None:
        (passed if condition else failed).append(label)
        print(f"  {'PASS' if condition else 'FAIL'}  {label}{(' — ' + detail) if detail else ''}")

    asyncio.run(reset())
    problems = {(p.get("problem_metadata") or {}).get("item_slug"): p
                for p in http("GET", f"/api/quiz/{COURSE_ID}")}
    http("POST", "/api/quiz/submit", {"problem_id": problems["a1"]["id"], "user_answer": "b"})
    http("POST", "/api/quiz/submit", {"problem_id": problems["a2"]["id"], "user_answer": "a"})

    print("\n[G1] 'mm:' 快照不改变学习速度估计")
    before = http("GET", f"/api/progress/courses/{COURSE_ID}/velocity")
    written = http("POST", "/api/teaching/learner-state/snapshot",
                   {"course_id": COURSE_ID, "run_tag": "gaptest"})
    after = http("GET", f"/api/progress/courses/{COURSE_ID}/velocity")
    ok("snapshot rows were actually written", len(written["written"]) >= 1,
       str(len(written["written"])))
    ok("velocity identical before/after mm: rows", before == after,
       f"{before.get('concepts_per_day')} vs {after.get('concepts_per_day')}")
    ok("concepts_total unchanged", before["concepts_total"] == after["concepts_total"])

    print("\n[G2] 进度总览的 gap_type_breakdown 不含 'mm:' 键")
    overview = http("GET", f"/api/progress/overview?course_id={COURSE_ID}")
    breakdown = (overview or {}).get("gap_type_breakdown", {}) if isinstance(overview, dict) else {}
    ok("no 'mm:' key in gap_type_breakdown", not any(k.startswith("mm:") for k in breakdown),
       json.dumps(breakdown, ensure_ascii=False)[:120])

    print("\n[G3] mastery-history 是唯一会暴露 'mm:' 标签的地方（设计如此）")
    history = http("GET", f"/api/quiz/{COURSE_ID}/mastery-history?limit=50")
    tags = [row.get("gap_type") for row in history]
    ok("mm: tags visible in the raw snapshot feed",
       any(tag and tag.startswith("mm:") for tag in tags), str(tags))
    ok("tracker rows still present without tags",
       any(tag is None for tag in tags), str([t for t in tags if t is None]))

    asyncio.run(reset())
    print("\n" + "=" * 60)
    print(f"PASSED {len(passed)}, FAILED {len(failed)}")
    for failure in failed:
        print(f"  - {failure}")
    return 0 if not failed else 1


if __name__ == "__main__":
    raise SystemExit(main())
