"""Capture representative evaluator responses for the docs/report.

Saves to D:\\Hermes\\archive\\metamind-20261114\\ : rubric.json,
evaluation-good.json, evaluation-no-evidence.json, evaluation-transfer.json.
Resets the demo learner afterwards.
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

ANSWER_GOOD = (
    "自进化指一个 Agent 能从自己的交互经历中提取经验，并把经验转化为对自身策略的持续更新。"
    "我认为它需要四个条件：一是要有经验积累，二是要有反思和问题诊断，三是要有持续性的策略改变，"
    "四是后续结果验证。它和长期记忆的区别在于，记忆改变的是它知道什么，自进化改变的是它以后怎么教。"
)
TRANSFER_CASE = (
    "新案例：某数学辅导 Agent 连续三次用公式推导讲「导数表示变化率」，学生练习都错。"
    "它诊断出学生缺的是「变化率」的直觉；新增策略「先用速度—时间图像做对比，再回到公式」，"
    "保存为带版本号的技能 V2；下一次同类问题该策略被检索调用；之后用未讲过的新题检验效果。"
)
TRANSFER_ANSWER = (
    "这个案例构成了真正的自进化。依据是：它先做了具体诊断，指出学生缺的是变化率直觉；"
    "然后产出了可执行的策略，带触发条件和步骤；并且把策略保存为带版本的 V2，"
    "下次同类问题会被检索调用；最后还用新题检验效果。四条证据链都齐了。"
)


def http(method: str, path: str, body: dict | None = None, timeout: int = 240):
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


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    asyncio.run(reset())
    dump("eval-rubric.json", http("GET", "/api/teaching/rubric"))

    problems = {(p.get("problem_metadata") or {}).get("item_slug"): p
                for p in http("GET", f"/api/quiz/{COURSE_ID}")}

    dump("evaluation-no-evidence.json", http(
        "POST", "/api/teaching/evaluate",
        {"course_id": COURSE_ID, "objective_slug": "g1", "session_id": "sample_empty"},
    ))

    http("POST", "/api/quiz/submit", {"problem_id": problems["a1"]["id"], "user_answer": "b"})
    http("POST", "/api/quiz/submit", {"problem_id": problems["a2"]["id"], "user_answer": "b,d"})
    good = http("POST", "/api/teaching/evaluate", {
        "course_id": COURSE_ID, "objective_slug": "g1", "session_id": "sample_good",
        "strategy_version": "metamind.teaching-strategy.v1",
        "problem_ids": [problems["a1"]["id"], problems["a2"]["id"]],
        "open_answer": ANSWER_GOOD,
        "open_question": "请解释什么是自进化 Agent？它和只有长期记忆的 Agent 有什么区别？",
    })
    dump("evaluation-good.json", good)

    transfer = http("POST", "/api/teaching/evaluate", {
        "course_id": COURSE_ID, "objective_slug": "g3", "session_id": "sample_transfer",
        "transfer_case": TRANSFER_CASE, "transfer_answer": TRANSFER_ANSWER,
    })
    dump("evaluation-transfer.json", transfer)

    print("good:", json.dumps(good["scores"], ensure_ascii=False), good["verdict"])
    print("transfer:", json.dumps(transfer["scores"], ensure_ascii=False), transfer["verdict"])
    asyncio.run(reset())
    print("files:", sorted(p.name for p in OUT.glob("eval*.json")))


if __name__ == "__main__":
    main()
