"""Capture one representative learner-state response for the docs/report.

Submits one correct + one wrong answer, saves the aggregate JSON, then resets.
"""

from __future__ import annotations

import asyncio
import json
import os
import sys
import urllib.request
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPT_DIR.parents[1]))
sys.path.insert(0, str(SCRIPT_DIR))

import seed_demo_course as seeder  # noqa: E402
from database import async_session  # noqa: E402

BASE = "http://localhost:8000"
COURSE_ID = str(seeder._uid("course"))
OUT = Path(os.environ.get("METAMIND_SAMPLE_DIR")
          or (SCRIPT_DIR.parents[3] / "native-data" / "samples"))


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


def main() -> None:
    asyncio.run(reset())
    OUT.mkdir(parents=True, exist_ok=True)
    empty = http("GET", f"/api/teaching/learner-state?course_id={COURSE_ID}")
    (OUT / "learner-state-empty.json").write_text(
        json.dumps(empty, ensure_ascii=False, indent=2), encoding="utf-8"
    )

    listing = http("GET", f"/api/quiz/{COURSE_ID}")
    by_slug = {(item.get("problem_metadata") or {}).get("item_slug"): item for item in listing}
    http("POST", "/api/quiz/submit", {"problem_id": by_slug["a1"]["id"], "user_answer": "b"})
    http("POST", "/api/quiz/submit", {"problem_id": by_slug["a2"]["id"], "user_answer": "a"})
    snap = http("POST", "/api/teaching/learner-state/snapshot",
                {"course_id": COURSE_ID, "run_tag": "run01"})
    assessed = http("GET", f"/api/teaching/learner-state?course_id={COURSE_ID}")
    concept_id = assessed["concepts"][0]["concept_id"]
    history = http(
        "GET",
        f"/api/teaching/learner-state/history?course_id={COURSE_ID}&concept_id={concept_id}",
    )
    (OUT / "learner-state-assessed.json").write_text(
        json.dumps(assessed, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    (OUT / "learner-state-snapshot-write.json").write_text(
        json.dumps(snap, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    (OUT / "learner-state-history.json").write_text(
        json.dumps(history, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(json.dumps(assessed["assessment_summary"], ensure_ascii=False))
    print("snapshot written:", [item["tag"] for item in snap["written"]])
    asyncio.run(reset())
    print("reset done; files:", sorted(p.name for p in OUT.glob("learner-state-*.json")))


if __name__ == "__main__":
    main()
