"""In-process unit tests for the Evaluator (METAMIND module 2).

No network, no LLM: the provider is replaced with a stub so the failure paths
(invalid JSON, schema mismatch, fabricated quotes, bare assertions) are tested
deterministically.

Run from ``apps/api``::

    .venv-win/Scripts/python.exe scripts/metamind_demo/test_evaluator_unit.py
"""

from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent
API_ROOT = SCRIPT_DIR.parents[1]
sys.path.insert(0, str(API_ROOT))
sys.path.insert(0, str(SCRIPT_DIR))

import seed_demo_course as seeder  # noqa: E402  (bootstraps settings.env)

from services.evolution import eval_rubric as rubric  # noqa: E402
from services.evolution import evaluator as ev  # noqa: E402
from services.llm import router as llm_router  # noqa: E402


class StubClient:
    """Returns whatever canned text the test wants."""

    model = "stub-model"

    def __init__(self, reply: str):
        self.reply = reply
        self.calls = 0

    async def extract(self, system_prompt: str, user_message: str):
        self.calls += 1
        return self.reply, {"input_tokens": 1, "output_tokens": 1}


class ExplodingClient:
    model = "exploding"

    async def extract(self, system_prompt: str, user_message: str):
        raise RuntimeError("provider down")


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


GOOD_ANSWER = (
    "自进化是 Agent 从自己的教学经历里提取经验，把经验变成一条可复用的策略，"
    "存下来带版本号，下次遇到同类情形会被调用，再用新的结果验证它有没有用。"
    "它和记忆的区别是：记忆改变的是它知道什么，自进化改变的是它以后怎么做。"
)


def _patch(client) -> None:
    llm_router.get_llm_client = lambda *a, **k: client  # type: ignore[assignment]


async def main() -> int:
    c = Checks()

    print("\n[U1] JSON 抽取与无效 JSON 处理")
    c.ok("_extract_json parses a bare object",
         ev._extract_json('{"score": 2}') == {"score": 2})
    c.ok("_extract_json strips markdown fences",
         ev._extract_json('```json\n{"score": 1}\n```') == {"score": 1})
    try:
        ev._extract_json("抱歉，我无法评价")
        c.ok("_extract_json rejects prose", False)
    except ValueError:
        c.ok("_extract_json rejects prose", True)

    print("\n[U2] 模型返回无效 JSON -> invalid_json，不产生分数")
    _patch(StubClient("这不是 JSON"))
    outcome = await ev.grade_open_answer(rubric.DIMENSION_UNDERSTANDING, GOOD_ANSWER, "目标")
    c.ok("score is None", outcome.score is None, repr(outcome.score))
    c.ok("error_code == invalid_json", outcome.error_code == ev.ERROR_INVALID_JSON,
         str(outcome.error_code))
    c.ok("raw response kept for audit", bool(outcome.raw_response))

    print("\n[U3] 模型返回结构不符 -> schema_mismatch")
    _patch(StubClient('{"score": 5, "reason": "too high"}'))
    outcome = await ev.grade_open_answer(rubric.DIMENSION_UNDERSTANDING, GOOD_ANSWER, "目标")
    c.ok("score is None", outcome.score is None, repr(outcome.score))
    c.ok("error_code == schema_mismatch", outcome.error_code == ev.ERROR_SCHEMA_MISMATCH,
         str(outcome.error_code))

    print("\n[U4] 模型编造证据（引用不在原文）-> evidence_not_verbatim，不产生分数")
    _patch(StubClient(
        '{"score": 2, "reason": "学生准确说出定义与四个必要条件",'
        ' "evidence_quotes": ["这句话学生根本没说过"], "missing_requirements": [],'
        ' "confidence": "high"}'
    ))
    outcome = await ev.grade_open_answer(rubric.DIMENSION_UNDERSTANDING, GOOD_ANSWER, "目标")
    c.ok("score is None", outcome.score is None, repr(outcome.score))
    c.ok("error_code == evidence_not_verbatim",
         outcome.error_code == ev.ERROR_EVIDENCE_NOT_VERBATIM, str(outcome.error_code))

    print("\n[U5] 证据真实存在时得分为有效值，且证据原样保留")
    quote = "记忆改变的是它知道什么，自进化改变的是它以后怎么做"
    _patch(StubClient(
        '{"score": 2, "reason": "学生区分了记忆与策略进化",'
        f' "evidence_quotes": ["{quote}"], "missing_requirements": [],'
        ' "confidence": "high"}'
    ))
    outcome = await ev.grade_open_answer(rubric.DIMENSION_UNDERSTANDING, GOOD_ANSWER, "目标")
    c.ok("score == 2", outcome.score == 2, repr(outcome.score))
    c.ok("evidence quote preserved verbatim",
         outcome.payload["evidence_quotes"] == [quote],
         str(outcome.payload["evidence_quotes"]))
    c.ok("graded_by records the evidence check",
         outcome.payload["graded_by"] == "llm_rubric_with_verbatim_evidence_check")

    print("\n[U6] 「我懂了」式断言 -> 确定性判 0，不调用模型")
    client = StubClient('{"score": 2, "reason": "x", "evidence_quotes": ["我懂了"]}')
    _patch(client)
    outcome = await ev.grade_open_answer(rubric.DIMENSION_UNDERSTANDING, "我懂了。", "目标")
    c.ok("score == 0", outcome.score == 0, repr(outcome.score))
    c.ok("guard recorded", outcome.payload.get("guard") == "bare_assertion_detected")
    c.ok("model not called", client.calls == 0, f"calls={client.calls}")

    print("\n[U7] 空回答：「被要求作答但空着」记 0，「没有评价」保持未测量")
    outcome = await ev.grade_open_answer(rubric.DIMENSION_UNDERSTANDING, "", "目标")
    c.ok("submitted empty -> score 0", outcome.score == 0, repr(outcome.score))
    c.ok("submitted empty guarded", outcome.payload.get("guard") == "empty_answer_submitted")
    c.ok("submitted empty is not an error", outcome.error_code is None, str(outcome.error_code))
    outcome = await ev.grade_open_answer(rubric.DIMENSION_UNDERSTANDING, None, "目标")
    c.ok("never evaluated -> score None", outcome.score is None, repr(outcome.score))
    c.ok("never evaluated has no error", outcome.error_code is None, str(outcome.error_code))

    print("\n[U7b] 「不知道」也是断言，不调模型直接 0")
    client = StubClient('{"score": 2, "reason": "x", "evidence_quotes": ["不知道"]}')
    _patch(client)
    outcome = await ev.grade_open_answer(rubric.DIMENSION_UNDERSTANDING, "不知道", "目标")
    c.ok("bare '不知道' -> 0", outcome.score == 0, repr(outcome.score))
    c.ok("model not called for '不知道'", client.calls == 0, f"calls={client.calls}")

    print("\n[U8] 模型不可用 -> llm_error/unavailable，不静默成功")
    llm_router.get_llm_client = lambda *a, **k: ExplodingClient()  # type: ignore[assignment]
    outcome = await ev.grade_open_answer(rubric.DIMENSION_UNDERSTANDING, GOOD_ANSWER, "目标")
    c.ok("score is None", outcome.score is None)
    c.ok("error_code == llm_error", outcome.error_code == ev.ERROR_LLM_ERROR,
         str(outcome.error_code))

    print("\n[U9] 客观题确定性判分：无记录=未测量，不全对=1，全对=2，全错=0")
    c.ok("no attempts -> None", ev.score_objective_items([])[0] is None)
    one_right = [{"is_correct": True}, {"is_correct": False}]
    c.ok("mixed -> 1", ev.score_objective_items(one_right)[0] == 1)
    all_right = [{"is_correct": True}, {"is_correct": True}]
    score, detail = ev.score_objective_items(all_right)
    c.ok("all correct -> 2", score == 2)
    c.ok("detail keeps raw counts", detail["correct"] == 2 and detail["attempts"] == 2)
    c.ok("all wrong -> 0", ev.score_objective_items([{"is_correct": False}])[0] == 0)

    print("\n[U10] 判定规则：未测量不当作 0 分")
    c.ok("no dimensions -> insufficient_evidence",
         rubric.compute_verdict(None, None, None)[0] == "insufficient_evidence")
    c.ok("all zero -> not_achieved",
         rubric.compute_verdict(0, 0, 0)[0] == "not_achieved")
    c.ok("all two -> achieved", rubric.compute_verdict(2, 2, 2)[0] == "achieved")
    c.ok("partial coverage of 2s -> partially_achieved",
         rubric.compute_verdict(None, 2, 2)[0] == "partially_achieved")
    c.ok("one zero among twos -> partially_achieved",
         rubric.compute_verdict(2, 0, 2)[0] == "partially_achieved")
    c.ok("missing dimension is skipped, not punished",
         rubric.compute_verdict(0, 0, None)[0] == "not_achieved")

    print("\n[U11] Rubric 结构完整性")
    doc = rubric.rubric_document()
    c.ok("three dimensions", set(doc["dimensions"]) == {"understanding", "application", "transfer"})
    c.ok("each dimension has exactly 3 levels 0/1/2",
         all([lvl["score"] for lvl in doc["dimensions"][d]["levels"]] == [0, 1, 2]
             for d in doc["dimensions"]))
    c.ok("each level has non-empty behaviour",
         all(lvl["behaviour"].strip() for d in doc["dimensions"]
             for lvl in doc["dimensions"][d]["levels"]))
    c.ok("retention declared out of scope", "retention" in doc["deferred"])

    print("\n[U12] 引用校验对排版噪音稳健（全角引号/多余空格）")
    c.ok("quote with extra whitespace still matches",
         ev._quote_is_verbatim("记忆改变的是它知道什么， 自进化改变的是它以后怎么做",
                               GOOD_ANSWER))
    c.ok("fabricated quote rejected", not ev._quote_is_verbatim("自进化就是记住偏好", GOOD_ANSWER))

    print("\n[U13] 分数与理由冲突：理由说「方向相反」却给 1 分 -> 拒绝并留痕")
    reversed_quote = "这个案例不是自进化"
    _patch(StubClient(
        json.dumps({
            "score": 1,
            "reason": "学生的判断方向与正确答案相反，但表达比较完整，给 1 分",
            "evidence_quotes": [reversed_quote],
            "missing_requirements": [],
            "confidence": "medium",
        }, ensure_ascii=False)
    ))
    outcome = await ev.grade_open_answer(
        rubric.DIMENSION_TRANSFER,
        "这个案例不是自进化，因为它没有重新训练模型。",
        "目标",
        allow_repair=False,
    )
    c.ok("score rejected (None)", outcome.score is None, repr(outcome.score))
    c.ok(
        "error_code == score_reason_direction_conflict",
        outcome.error_code == ev.ERROR_SCORE_REASON_CONFLICT, str(outcome.error_code),
    )
    c.ok(
        "guard records the conflict",
        outcome.payload.get("guard") == "score_reason_direction_conflict",
    )

    print("\n[U14] 方向正确、理由充分 -> 2 分不被误杀")
    _patch(StubClient(json.dumps({
        "score": 2,
        "reason": "判断方向正确，且逐条引用了诊断、策略文本、版本与验证",
        "evidence_quotes": ["构成真正的自进化"],
        "missing_requirements": [],
        "confidence": "high",
    }, ensure_ascii=False)))
    outcome = await ev.grade_open_answer(
        rubric.DIMENSION_TRANSFER, "这个案例构成真正的自进化。", "目标", allow_repair=False
    )
    c.ok("score 2 accepted", outcome.score == 2, repr(outcome.score))

    print("\n" + "=" * 60)
    print(f"PASSED {len(c.passed)}, FAILED {len(c.failed)}")
    for failure in c.failed:
        print(f"  - {failure}")
    return 0 if not c.failed else 1


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
