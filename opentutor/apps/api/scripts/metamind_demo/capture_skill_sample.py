"""Capture module-4 artifacts for the report (skill versions, diff, selector, invocation)."""

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
CONFUSED = (
    "自进化就是 AI 记住了我喜欢例子，下次继续给我举例，这样它就更懂我了。"
    "只要能根据我改变回答，就是自进化。"
)


def http(method: str, path: str, body: dict | None = None, timeout: int = 300):
    data = json.dumps(body, ensure_ascii=False).encode() if body is not None else None
    req = urllib.request.Request(
        f"{BASE}{path}", data=data, method=method,
        headers={"Content-Type": "application/json; charset=utf-8"} if data else {},
    )
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode())


async def reset():
    async with async_session() as db:
        await seeder._reset_learner(db, seeder._load_spec())
        await db.commit()


def dump(name, payload):
    (OUT / name).write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


async def main():
    OUT.mkdir(parents=True, exist_ok=True)
    http("POST", "/api/teaching/skills/import")
    await reset()
    probs = {(p.get("problem_metadata") or {}).get("item_slug"): p
             for p in http("GET", f"/api/quiz/{COURSE_ID}")}
    http("POST", "/api/quiz/submit", {"problem_id": probs["a1"]["id"], "user_answer": "a"})
    ev = http("POST", "/api/teaching/evaluate", {
        "course_id": COURSE_ID, "objective_slug": "g1", "session_id": "run01",
        "problem_ids": [probs["a1"]["id"]], "open_answer": CONFUSED,
        "open_question": "请解释什么是自进化 Agent？",
        "strategy_version": "socratic_questioning@v1",
    })
    refl = http("POST", "/api/teaching/reflect", {
        "course_id": COURSE_ID, "evaluation_id": ev["evaluation_id"],
    })
    dump("reflection-run01.json", refl)

    new_skill = http("POST", "/api/teaching/skills/from-reflection", {
        "course_id": COURSE_ID, "reflection_id": refl["reflection_id"],
    })
    dump("skill-from-reflection.json", new_skill)

    update = http("POST", "/api/teaching/skills/from-reflection", {
        "course_id": COURSE_ID, "reflection_id": refl["reflection_id"],
        "skill_slug": "socratic_questioning",
    })
    dump("skill-socratic-upgrade.json", update)

    versions = http("GET", "/api/teaching/skills/socratic_questioning/versions")
    dump("skill-socratic-versions.json", versions)
    to_v = versions["current_version"]
    dump("skill-socratic-diff-v1-v2.json",
         http("GET", f"/api/teaching/skills/socratic_questioning/diff?to_version={to_v}"))

    selection = http("POST", "/api/teaching/selector/select", {
        "course_id": COURSE_ID, "objective_slug": "g1", "error_type": "conceptual",
        "scene": "study_session",
        "message": "学生把记住偏好当成自进化，混淆了两个概念，不知道为什么错了",
        "weakness_dimension": "understanding", "session_id": "run01",
    })
    dump("selector-run01.json", selection)

    inv = selection["invocation_id"]
    dump("invocation-injected.json",
         http("POST", f"/api/teaching/invocations/{inv}/inject"))
    dump("invocation-evaluated.json", http(
        "POST", f"/api/teaching/invocations/{inv}/evaluate",
        {"evaluation_id": ev["evaluation_id"]}))

    print("skill:", new_skill["skill"]["slug"], new_skill["status"])
    print("upgrade:", update["status"], (update.get("version") or {}).get("version"))
    print("selector:", selection["selected"]["slug"] if selection["selected"] else None,
          "| fallback:", selection["fallback_to_native_tutor"])
    await reset()


asyncio.run(main())
