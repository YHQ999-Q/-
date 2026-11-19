"""Ad-hoc debug: run one reflection and print the raw failure detail."""

from __future__ import annotations

import asyncio
import json
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
CONFUSED = (
    "自进化就是 AI 记住了我喜欢例子，下次继续给我举例，这样它就更懂我了。"
    "只要能根据我改变回答，就是自进化。"
)


def http(method: str, path: str, body: dict | None = None, timeout: int = 300):
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(
        f"{BASE}{path}", data=data, method=method,
        headers={"Content-Type": "application/json"} if data else {},
    )
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode())


async def reset():
    async with async_session() as db:
        await seeder._reset_learner(db, seeder._load_spec())
        await db.commit()


async def main():
    await reset()
    probs = {(p.get("problem_metadata") or {}).get("item_slug"): p
             for p in http("GET", f"/api/quiz/{COURSE_ID}")}
    http("POST", "/api/quiz/submit", {"problem_id": probs["a1"]["id"], "user_answer": "a"})
    ev = http("POST", "/api/teaching/evaluate", {
        "course_id": COURSE_ID, "objective_slug": "g1", "session_id": "dbg",
        "problem_ids": [probs["a1"]["id"]],
        "open_answer": CONFUSED, "open_question": "请解释什么是自进化 Agent？",
        "strategy_version": "metamind.teaching-strategy.v1",
    })
    print("eval scores:", ev["scores"])
    row = http("POST", "/api/teaching/reflect",
               {"course_id": COURSE_ID, "evaluation_id": ev["evaluation_id"]})
    print("status:", row["status"], "| err:", row["error_code"], "| attempts:", row["attempts"])
    print("msg:", (row["error_message"] or "")[:800])
    print("cited:", json.dumps((row.get("evidence") or {}).get("cited"), ensure_ascii=False)[:500])


asyncio.run(main())
