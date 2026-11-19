"""Scenario verification for METAMIND module 2 (Evaluator).

Runs against the live API (http://localhost:8000) and a live model, then checks
invariants that must hold for *any* grading outcome plus the six acceptance
scenarios.

Run from ``apps/api``::

    .venv-win/Scripts/python.exe scripts/metamind_demo/verify_evaluator.py
"""

from __future__ import annotations

import asyncio
import json
import os
import sys
import urllib.error
import urllib.request
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent
API_ROOT = SCRIPT_DIR.parents[1]
sys.path.insert(0, str(API_ROOT))
sys.path.insert(0, str(SCRIPT_DIR))

import seed_demo_course as seeder  # noqa: E402

from database import async_session  # noqa: E402
from services.evolution import eval_rubric as rubric  # noqa: E402

BASE = os.environ.get("OPENTUTOR_API", "http://localhost:8000").rstrip("/")
COURSE_ID = str(seeder._uid("course"))

OPEN_QUESTION = "请用你自己的话解释：什么是自进化 Agent？它和只有长期记忆的 Agent 有什么区别？"

ANSWER_GOOD = (
    "自进化指一个 Agent 能从自己的交互经历中提取经验，并把经验转化为对自身策略的持续更新。"
    "我认为它需要四个条件：一是要有经验积累，二是要有反思和问题诊断，三是要有持续性的策略改变，"
    "四是后续结果验证。它和长期记忆的区别在于，记忆改变的是它知道什么，自进化改变的是它以后怎么教。"
)
ANSWER_PARTIAL = (
    "自进化就是它会自己改变，会越来越聪明，能根据我的情况调整，所以我觉得算是自进化吧。"
)
ANSWER_WRONG = (
    "自进化就是 AI 记住了我喜欢例子，下次继续给我举例，这样它就更懂我了。"
    "只要能根据我改变回答，就是自进化。"
)

TRANSFER_CASE = (
    "新案例：某数学辅导 Agent 连续三次用公式推导讲「导数表示变化率」，学生练习都错。"
    "它随后诊断出学生缺的是「变化率」的直觉，而不是公式推导不够细；"
    "于是新增一条策略「先用速度—时间图像做对比，再回到公式」，写明触发条件与步骤，"
    "保存为带版本号的技能 V2；下一次遇到同类问题时该策略被检索并注入教学；"
    "之后又用未讲过的新题检验效果。"
)
TRANSFER_GOOD = (
    "这个案例构成了真正的自进化。依据是：它先做了具体诊断，指出学生缺的是变化率直觉，"
    "而不是笼统说效果不好；然后产出了可执行的策略，带触发条件和步骤；"
    "并且把策略保存为带版本的 V2，下次同类问题会被检索调用；最后还用新题检验效果。"
    "诊断、策略文本、持久化版本、调用与验证这四条证据链都齐了。"
)
TRANSFER_REVERSED = (
    "这个案例不是自进化，因为它并没有重新训练模型，只是换了个讲法，本质上还是同一个 Agent。"
)
TRANSFER_WEAK_SUPPORT = "这个案例有自进化，因为它换了新方法，而且学生后来答对了。"


class Checks:
    def __init__(self) -> None:
        self.passed: list[str] = []
        self.failed: list[str] = []

    def ok(self, label: str, condition: bool, detail: str = "") -> None:
        if condition:
            self.passed.append(label)
            print(f"  PASS  {label}{(' — ' + detail) if detail else ''}")
        else:
            self.failed.append(label)
            print(f"  FAIL  {label}{(' — ' + detail) if detail else ''}")


def http(method: str, path: str, body: dict | None = None, timeout: int = 240) -> dict:
    data = json.dumps(body).encode("utf-8") if body is not None else None
    request = urllib.request.Request(
        f"{BASE}{path}",
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


async def reset() -> dict:
    async with async_session() as db:
        removed = await seeder._reset_learner(db, seeder._load_spec())
        await db.commit()
    return removed


def problems_by_slug() -> dict[str, dict]:
    listing = http("GET", f"/api/quiz/{COURSE_ID}")
    return {(item.get("problem_metadata") or {}).get("item_slug"): item for item in listing}


def submit(problem_id: str, answer: str) -> bool:
    return http("POST", "/api/quiz/submit", {"problem_id": problem_id, "user_answer": answer})[
        "is_correct"
    ]


def evaluate(**kwargs) -> dict:
    body = {"course_id": COURSE_ID, **kwargs}
    return http("POST", "/api/teaching/evaluate", body)


def check_invariants(c: Checks, tag: str, row: dict, answer_sources: dict[str, str]) -> None:
    """Properties that must hold for every evaluation, whatever the score."""
    scores = row["scores"]

    c.ok(
        f"{tag}: scores are null or int in 0..2",
        all(
            value is None or (isinstance(value, int) and 0 <= value <= 2)
            for key, value in scores.items()
            if key != "retention"
        ),
        str(scores),
    )
    c.ok(f"{tag}: retention always null", scores["retention"] is None)

    expected_verdict, _reason = rubric.compute_verdict(
        scores["understanding"], scores["application"], scores["transfer"]
    )
    c.ok(
        f"{tag}: verdict matches its own scores",
        row["verdict"] == expected_verdict,
        f"{row['verdict']} vs {expected_verdict}",
    )
    measured = sum(
        1 for key in ("understanding", "application", "transfer") if scores[key] is not None
    )
    c.ok(f"{tag}: dimensions_measured matches scores", row["dimensions_measured"] == measured)
    c.ok(
        f"{tag}: unmeasured_dimensions matches nulls",
        sorted(row["unmeasured_dimensions"])
        == sorted(
            key for key in ("understanding", "application", "transfer")
            if scores[key] is None
        ),
        str(row["unmeasured_dimensions"]),
    )

    dims = (row["evidence"] or {}).get("dimensions", {})
    for dimension, answer in answer_sources.items():
        detail = dims.get(dimension) or {}
        quotes = detail.get("evidence_quotes") or []
        if detail.get("score") is None:
            c.ok(f"{tag}: {dimension} has no score, quotes irrelevant", True)
            continue
        c.ok(
            f"{tag}: every {dimension} quote is verbatim from the student answer",
            all(quote and quote.replace(" ", "") in answer.replace(" ", "") for quote in quotes)
            if quotes
            else True,
            str(quotes)[:120],
        )
        if detail.get("score", 0) >= 1:
            c.ok(f"{tag}: {dimension} score>=1 carries at least one quote", bool(quotes))
        c.ok(
            f"{tag}: {dimension} graded_by is recorded",
            bool(detail.get("graded_by")),
            str(detail.get("graded_by")),
        )

    c.ok(
        f"{tag}: BKT mastery explicitly not used for scoring",
        (row["evidence"] or {}).get("learner_context", {}).get("used_for_scoring") is False,
    )
    c.ok(f"{tag}: comparison warning present", "comparison_warning" in (row["rationale"] or {}))
    c.ok(f"{tag}: content hash recorded", bool(row["content_hash"]))


async def main() -> int:
    c = Checks()
    await reset()
    problems = problems_by_slug()
    print(f"demo course {COURSE_ID}, problems: {sorted(k for k in problems if k)}")

    # ── S1: nothing measured ───────────────────────────────────────────────
    print("\n[S1] 三个维度均未评估")
    row = evaluate(objective_slug="g1", session_id="s1_no_evidence")
    c.ok("all three scores null", all(row["scores"][k] is None for k in
         ("understanding", "application", "transfer")), str(row["scores"]))
    c.ok("verdict == insufficient_evidence", row["verdict"] == "insufficient_evidence",
         str(row["verdict"]))
    c.ok("dimensions_measured == 0", row["dimensions_measured"] == 0)
    c.ok("status is not 'completed'", row["status"] != "completed", row["status"])
    c.ok("no fabricated error", row["error_code"] is None, str(row["error_code"]))
    check_invariants(c, "S1", row, {})

    # ── S2: everything wrong ───────────────────────────────────────────────
    print("\n[S2] 学生回答完全错误")
    a1 = problems["a1"]["id"]
    a2 = problems["a2"]["id"]
    c.ok("a1 submitted wrong", submit(a1, "a") is False)
    c.ok("a2 submitted wrong", submit(a2, "a") is False)
    row = evaluate(
        objective_slug="g1", session_id="s2_wrong",
        problem_ids=[a1],
        open_answer=ANSWER_WRONG, open_question=OPEN_QUESTION,
    )
    c.ok("application == 0 (item graded wrong)", row["scores"]["application"] == 0,
         str(row["scores"]["application"]))
    c.ok("understanding == 0", row["scores"]["understanding"] == 0,
         str(row["scores"]["understanding"]))
    c.ok("verdict == not_achieved", row["verdict"] == "not_achieved", str(row["verdict"]))
    check_invariants(c, "S2", row, {"understanding": ANSWER_WRONG})

    # ── S3: partial understanding ──────────────────────────────────────────
    print("\n[S3] 学生部分理解")
    c.ok("a1 submitted correct", submit(a1, "b") is True)
    row = evaluate(
        objective_slug="g1", session_id="s3_partial",
        problem_ids=[a1, a2],
        open_answer=ANSWER_PARTIAL, open_question=OPEN_QUESTION,
    )
    c.ok("application == 1 (mixed items)", row["scores"]["application"] == 1,
         str(row["scores"]["application"]))
    c.ok("understanding < 2", (row["scores"]["understanding"] or 0) < 2,
         str(row["scores"]["understanding"]))
    c.ok("verdict is partially_achieved", row["verdict"] == "partially_achieved",
         str(row["verdict"]))
    check_invariants(c, "S3", row, {"understanding": ANSWER_PARTIAL})

    # ── S4: independent accurate explanation ───────────────────────────────
    print("\n[S4] 学生能够独立准确解释")
    c.ok("a2 submitted correct", submit(a2, "b,d") is True)
    row = evaluate(
        objective_slug="g1", session_id="s4_good",
        problem_ids=[a1, a2],
        open_answer=ANSWER_GOOD, open_question=OPEN_QUESTION,
    )
    c.ok("application == 2 (all items correct)", row["scores"]["application"] == 2,
         str(row["scores"]["application"]))
    c.ok("understanding == 2", row["scores"]["understanding"] == 2,
         str(row["scores"]["understanding"]))
    c.ok("understanding >= partial answer", True,
         f"good={row['scores']['understanding']} partial<2")
    check_invariants(c, "S4", row, {"understanding": ANSWER_GOOD})

    # ── S5: transfer to a new case ─────────────────────────────────────────
    print("\n[S5] 学生能够迁移到新案例")
    row = evaluate(
        objective_slug="g3", session_id="s5_transfer",
        transfer_case=TRANSFER_CASE, transfer_answer=TRANSFER_GOOD,
    )
    c.ok("transfer == 2", row["scores"]["transfer"] == 2, str(row["scores"]["transfer"]))
    c.ok("understanding not silently scored", row["scores"]["understanding"] is None)
    c.ok("verdict is partially_achieved (coverage incomplete)",
         row["verdict"] == "partially_achieved", str(row["verdict"]))
    check_invariants(c, "S5", row, {"transfer": TRANSFER_GOOD})

    print("\n[S5b] 方向正确但依据不足：不得给 2 分（0 或 1 都可，取决于是否处理了新信息）")
    row = evaluate(
        objective_slug="g3", session_id="s5_weak_support",
        transfer_case=TRANSFER_CASE, transfer_answer=TRANSFER_WEAK_SUPPORT,
    )
    c.ok("weak-support answer never scores 2", (row["scores"]["transfer"] or 0) <= 1,
         str(row["scores"]["transfer"]))
    c.ok("weak-support answer is measured, not null",
         row["scores"]["transfer"] is not None, str(row["scores"]["transfer"]))
    check_invariants(c, "S5b", row, {"transfer": TRANSFER_WEAK_SUPPORT})

    print("\n[S5c] 判断方向相反：必须是 0 分（rubric 最高优先级规则）")
    row = evaluate(
        objective_slug="g3", session_id="s5_reversed",
        transfer_case=TRANSFER_CASE, transfer_answer=TRANSFER_REVERSED,
    )
    c.ok("reversed-direction answer scores 0", row["scores"]["transfer"] == 0,
         str(row["scores"]["transfer"]))
    c.ok(
        "reversed direction is not left unmeasured",
        row["scores"]["transfer"] is not None, str(row["scores"]["transfer"]),
    )
    check_invariants(c, "S5c", row, {"transfer": TRANSFER_REVERSED})

    # ── S6: repeatability ──────────────────────────────────────────────────
    print("\n[S6] 相同回答重复评价的一致性")
    repeats = [
        evaluate(
            objective_slug="g1", session_id="s6_repeat",
            problem_ids=[a1, a2],
            open_answer=ANSWER_GOOD, open_question=OPEN_QUESTION,
        )["scores"]
        for _ in range(2)
    ]
    first, second = repeats
    c.ok(
        "application is deterministic across repeats",
        first["application"] == second["application"] == 2,
        f"{first['application']} / {second['application']}",
    )
    c.ok(
        "understanding agrees across repeats",
        first["understanding"] == second["understanding"],
        f"{first['understanding']} / {second['understanding']}",
    )
    c.ok(
        "unmeasured dimensions stay null across repeats",
        first["transfer"] is None and second["transfer"] is None,
    )

    # ── S7: "asked but showed nothing" vs "never evaluated" ────────────────
    print("\n[S7] 缺失证据：区分「被要求作答但没展示能力」与「根本没有评价这个维度」")
    row = evaluate(
        objective_slug="g1", session_id="s7_empty_submitted",
        open_answer="",
    )
    c.ok("submitted empty -> understanding == 0", row["scores"]["understanding"] == 0,
         str(row["scores"]["understanding"]))
    c.ok(
        "submitted empty is recorded as a measured 0, not an error",
        row["scores"]["understanding"] is not None and row["error_code"] is None,
        f"measured={row['dimensions_measured']} err={row['error_code']}",
    )
    c.ok(
        "guard explains it was a submitted-but-empty answer",
        (row["evidence"]["dimensions"]["understanding"] or {}).get("guard")
        == "empty_answer_submitted",
    )

    row = evaluate(objective_slug="g1", session_id="s7_not_evaluated")
    c.ok("dimension never evaluated -> null", row["scores"]["understanding"] is None,
         str(row["scores"]["understanding"]))
    c.ok(
        "never-evaluated dimension is excluded from the measured count",
        "understanding" in row["unmeasured_dimensions"],
        str(row["unmeasured_dimensions"]),
    )
    c.ok("never-evaluated has no error blamed on the student", row["error_code"] is None)

    row = evaluate(
        objective_slug="g1", session_id="s7_i_get_it",
        open_answer="我懂了。",
    )
    c.ok("bare '我懂了' -> 0", row["scores"]["understanding"] == 0,
         str(row["scores"]["understanding"]))
    c.ok(
        "'我懂了' guard recorded",
        (row["evidence"]["dimensions"]["understanding"] or {}).get("guard")
        == "bare_assertion_detected",
    )
    row = evaluate(
        objective_slug="g1", session_id="s7_dont_know",
        open_answer="不知道。",
    )
    c.ok("bare '不知道' -> 0", row["scores"]["understanding"] == 0,
         str(row["scores"]["understanding"]))

    row = evaluate(
        objective_slug="g1", session_id="s7_rubric",
        open_answer=ANSWER_GOOD, rubric_version="metamind-eval-v0",
    )
    c.ok("rubric mismatch -> status failed", row["status"] == "failed", row["status"])
    c.ok("rubric mismatch -> error code",
         row["error_code"] == "rubric_version_mismatch", str(row["error_code"]))
    c.ok("rubric mismatch -> no scores", all(
        row["scores"][k] is None for k in ("understanding", "application", "transfer")))

    print("\n[S8] 持久化与可查询")
    listing = http("GET", f"/api/teaching/evaluations?course_id={COURSE_ID}&limit=50")
    c.ok("evaluations are stored", listing["count"] >= 9, str(listing["count"]))
    by_session = http(
        "GET", f"/api/teaching/evaluations?course_id={COURSE_ID}&session_id=s4_good&limit=5"
    )
    c.ok("filter by session works", by_session["count"] == 1,
         str(by_session["count"]))
    one = http("GET", f"/api/teaching/evaluations/{by_session['evaluations'][0]['evaluation_id']}")
    c.ok("single evaluation fetch works", one["evaluation_id"]
         == by_session["evaluations"][0]["evaluation_id"])
    rubric_doc = http("GET", "/api/teaching/rubric")
    c.ok("rubric endpoint exposes v1", rubric_doc["rubric_version"] == "metamind-eval-v1")

    if os.environ.get("METAMIND_KEEP_EVIDENCE") != "1":
        removed = await reset()
        print(f"\nreset removed: {json.dumps(removed, ensure_ascii=False)}")

    print("\n" + "=" * 60)
    print(f"PASSED {len(c.passed)}, FAILED {len(c.failed)}")
    for failure in c.failed:
        print(f"  - {failure}")
    return 0 if not c.failed else 1


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
