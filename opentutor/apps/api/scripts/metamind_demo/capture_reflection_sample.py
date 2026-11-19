"""Capture representative reflection responses for the docs/report.

Saves to D:\\Hermes\\archive\\metamind-20261114\\ :
  reflection-completed.json    (concept confusion -> evidence-backed diagnosis)
  reflection-achieved.json     (objective met -> no_improvement_needed)
  reflection-insufficient.json (no measured dimension -> insufficient_evidence)
Resets the demo learner afterwards unless METAMIND_KEEP_EVIDENCE=1.
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

CONFUSED = (
    "自进化就是 AI 记住了我喜欢例子，下次继续给我举例，这样它就更懂我了。"
    "只要能根据我改变回答，就是自进化。"
)
GOOD = (
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


def http(method: str, path: str, body: dict | None = None, timeout: int = 300):
    data = json.dumps(body).encode("utf-8") if body is not None else None
    request = urllib.request.Request(
        f"{BASE}{path}", data=data, method=method,
        headers={"Content-Type": "application/json; charset=utf-8"} if data else {},
    )
    with urllib.request.urlopen(request, timeout=timeout) as response:
        return json.loads(response.read().decode("utf-8"))


async def reset() -> None:
    async with async_session() as db:
        await seeder._reset_learner(db, seeder._load_spec())
        await db.commit()


def dump(name: str, payload) -> None:
    (OUT / name).write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def problems() -> dict[str, dict]:
    return {(p.get("problem_metadata") or {}).get("item_slug"): p
            for p in http("GET", f"/api/quiz/{COURSE_ID}")}


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)

    # 1) concept confusion -> completed reflection
    asyncio.run(reset())
    p = problems()
    http("POST", "/api/quiz/submit", {"problem_id": p["a1"]["id"], "user_answer": "a"})
    http("POST", "/api/quiz/submit", {"problem_id": p["a2"]["id"], "user_answer": "a"})
    ev = http("POST", "/api/teaching/evaluate", {
        "course_id": COURSE_ID, "objective_slug": "g1", "session_id": "run01",
        "strategy_version": "metamind.teaching-strategy.v1",
        "problem_ids": [p["a1"]["id"], p["a2"]["id"]],
        "open_answer": CONFUSED,
        "open_question": "请解释什么是自进化 Agent？它和只有长期记忆的 Agent 有什么区别？",
    })
    dump("evaluation-run01.json", ev)
    row = http("POST", "/api/teaching/reflect", {
        "course_id": COURSE_ID, "evaluation_id": ev["evaluation_id"],
        "subjective_feedback": "我觉得我懂了，但是题目一做就错",
    })
    dump("reflection-completed.json", row)
    print("completed:", row["status"], "| attempts:", row["attempts"],
          "| dim:", row["problem"]["dimension"])

    # 2) achieved -> no improvement needed
    asyncio.run(reset())
    p = problems()
    http("POST", "/api/quiz/submit", {"problem_id": p["a1"]["id"], "user_answer": "b"})
    http("POST", "/api/quiz/submit", {"problem_id": p["a2"]["id"], "user_answer": "b,d"})
    ev2 = http("POST", "/api/teaching/evaluate", {
        "course_id": COURSE_ID, "objective_slug": "g1", "session_id": "run_achieved",
        "problem_ids": [p["a1"]["id"], p["a2"]["id"]],
        "open_answer": GOOD,
        "transfer_case": TRANSFER_CASE, "transfer_answer": TRANSFER_GOOD,
    })
    row2 = http("POST", "/api/teaching/reflect", {
        "course_id": COURSE_ID, "evaluation_id": ev2["evaluation_id"],
    })
    dump("reflection-achieved.json", row2)
    print("achieved case:", ev2["verdict"], "->", row2["status"])

    # 3) no evidence -> insufficient
    asyncio.run(reset())
    ev3 = http("POST", "/api/teaching/evaluate", {
        "course_id": COURSE_ID, "objective_slug": "g1", "session_id": "run_empty",
    })
    row3 = http("POST", "/api/teaching/reflect", {
        "course_id": COURSE_ID, "evaluation_id": ev3["evaluation_id"],
    })
    dump("reflection-insufficient.json", row3)
    print("empty case:", row3["status"], "|", (row3["problem"]["statement"] or "")[:40])

    if os.environ.get("METAMIND_KEEP_EVIDENCE") != "1":
        asyncio.run(reset())
    print("files:", sorted(p.name for p in OUT.glob("reflection-*.json")))


if __name__ == "__main__":
    main()
