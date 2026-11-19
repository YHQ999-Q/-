"""Fixed evaluation rubric (METAMIND module 2).

Two rules make scores defensible:

1. **Objective items are scored deterministically** by the existing grading code
   in ``services.practice.answers`` — no model involvement, no interpretation.
2. **Open answers are scored against verbatim anchors** that the model must
   quote from the student's own words. A quote that cannot be found in the
   answer is rejected and the dimension becomes ``not_measured`` rather than
   silently scoring whatever the model felt like.

Every rubric change must bump ``RUBRIC_VERSION``; evaluation rows store the
version they were produced with so old evidence stays interpretable.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

RUBRIC_VERSION = "metamind-eval-v1"

DIMENSION_UNDERSTANDING = "understanding"
DIMENSION_APPLICATION = "application"
DIMENSION_TRANSFER = "transfer"

# What each dimension measures, in one line. Used in prompts and in the API.
DIMENSION_INTENT: dict[str, str] = {
    DIMENSION_UNDERSTANDING: "能否准确解释概念：说出定义与关键构成，而不是复述题面。",
    DIMENSION_APPLICATION: "无提示时能否独立运用：独立作答、不依赖提示或重述题干。",
    DIMENSION_TRANSFER: "能否迁移到新情境：在未出现过的新案例上做判断并给出依据。",
}

# Retention is deliberately excluded from immediate scoring.
DEFERRED_DIMENSION_NOTE = (
    "本次不评价 Retention（延迟保持）：即时评价无法测保持，需要后续延迟测验。"
)


@dataclass(frozen=True)
class LevelAnchor:
    """One score level with concrete, checkable behaviour."""

    score: int
    label: str
    behaviour: str


@dataclass(frozen=True)
class DimensionRubric:
    dimension: str
    intent: str
    levels: tuple[LevelAnchor, ...]
    # Evidence the grader must be able to point at.
    required_evidence: str
    # How this dimension is normally measured in our instruments.
    instruments: tuple[str, ...]


UNDERSTANDING_LEVELS = (
    LevelAnchor(
        score=0,
        label="未体现理解",
        behaviour=(
            "没有给出定义或关键构成；或只重复题面用词；或把两个不同概念混为一谈"
            "（例如把「记住偏好」直接说成「自进化」）并给出错误理由。"
        ),
    ),
    LevelAnchor(
        score=1,
        label="部分理解",
        behaviour=(
            "说出了定义中的一部分（例如只说「会改变自己」），但缺少必要条件中的"
            "至少两个（经验积累 / 反思诊断 / 策略持续改变 / 结果验证），"
            "或方向正确但表述含糊、无法据以判断。"
        ),
    ),
    LevelAnchor(
        score=2,
        label="准确理解",
        behaviour=(
            "用学生自己的话准确解释定义，并至少指出三个必要条件；"
            "能说明「改变的是以后怎么做，而不是知道什么」这一关键区分。"
        ),
    ),
)

APPLICATION_LEVELS = (
    LevelAnchor(
        score=0,
        label="未独立完成",
        behaviour=(
            "客观题判分错误；或开放作答明显依赖提示、重复题干、没有给出自己的判断。"
        ),
    ),
    LevelAnchor(
        score=1,
        label="部分独立",
        behaviour=(
            "客观题判分正确但只覆盖部分目标；或开放作答给出判断但理由不完整"
            "（有结论、无依据，或依据与结论不对应）。"
        ),
    ),
    LevelAnchor(
        score=2,
        label="独立准确",
        behaviour=(
            "客观题在无提示条件下全部判分正确，且开放作答给出判断并附与结论一致的依据。"
        ),
    ),
)

TRANSFER_LEVELS = (
    LevelAnchor(
        score=0,
        label="未迁移",
        behaviour=(
            "面对新案例给出的判断与正确判断相反；或直接照搬原案例的措辞而没有处理新信息。"
        ),
    ),
    LevelAnchor(
        score=1,
        label="部分迁移",
        behaviour=(
            "对新案例的判断方向正确，但依据不完整，或漏掉案例中给出的关键条件"
            "（例如只说「有策略」而没检查是否被调用与验证）。"
        ),
    ),
    LevelAnchor(
        score=2,
        label="成功迁移",
        behaviour=(
            "对新案例判断正确，并明确引用案例中的具体条件作为依据；"
            "能指出案例缺少哪一条证据链（诊断 / 策略文本 / 持久化版本 / 调用与验证）。"
        ),
    ),
)

RUBRIC: dict[str, DimensionRubric] = {
    DIMENSION_UNDERSTANDING: DimensionRubric(
        dimension=DIMENSION_UNDERSTANDING,
        intent=DIMENSION_INTENT[DIMENSION_UNDERSTANDING],
        levels=UNDERSTANDING_LEVELS,
        required_evidence="学生原话中解释定义与必要条件的片段（逐字）",
        instruments=("open_answer",),
    ),
    DIMENSION_APPLICATION: DimensionRubric(
        dimension=DIMENSION_APPLICATION,
        intent=DIMENSION_INTENT[DIMENSION_APPLICATION],
        levels=APPLICATION_LEVELS,
        required_evidence="客观题判分结果（题目 id + 是否判对），或开放作答中的判断与依据",
        instruments=("objective_items", "open_answer"),
    ),
    DIMENSION_TRANSFER: DimensionRubric(
        dimension=DIMENSION_TRANSFER,
        intent=DIMENSION_INTENT[DIMENSION_TRANSFER],
        levels=TRANSFER_LEVELS,
        required_evidence="学生对新案例的判断原文，以及引用的案例条件（逐字）",
        instruments=("transfer_items",),
    ),
}


def level_table(dimension: str) -> list[dict[str, Any]]:
    rubric = RUBRIC[dimension]
    return [
        {"score": level.score, "label": level.label, "behaviour": level.behaviour}
        for level in rubric.levels
    ]


def rubric_document() -> dict[str, Any]:
    """Machine-readable full rubric — served by the API and stored on rows."""
    return {
        "rubric_version": RUBRIC_VERSION,
        "scale": {"min": 0, "max": 2, "type": "ordinal"},
        "deferred": {"retention": DEFERRED_DIMENSION_NOTE},
        "dimensions": {
            dimension: {
                "intent": rubric.intent,
                "required_evidence": rubric.required_evidence,
                "instruments": list(rubric.instruments),
                "levels": level_table(dimension),
            }
            for dimension, rubric in RUBRIC.items()
        },
        "verdict_rules": verdict_rules_document(),
    }


# ── Verdict: rule-based, never model-decided ───────────────────────────────


@dataclass
class VerdictInput:
    understanding: int | None
    application: int | None
    transfer: int | None
    measured: int = field(default=0)


def compute_verdict(
    understanding: int | None,
    application: int | None,
    transfer: int | None,
) -> tuple[str, str]:
    """Return ``(verdict, reason)`` from measured scores only.

    Rules (checked in order):

    * nothing measured -> ``insufficient_evidence``
    * every measured dimension == 0 -> ``not_achieved``
    * every dimension measured AND all == 2 -> ``achieved``
    * otherwise -> ``partially_achieved``

    A missing dimension never counts as a zero: it is skipped, not punished.
    ``achieved`` requires full coverage on purpose — claiming an objective is
    met while one dimension was never measured would be exactly the kind of
    overstatement this module exists to prevent.
    """
    scores = {
        "understanding": understanding,
        "application": application,
        "transfer": transfer,
    }
    measured = {k: v for k, v in scores.items() if v is not None}

    if not measured:
        return "insufficient_evidence", "no dimension produced usable evidence"
    if all(value == 0 for value in measured.values()):
        return "not_achieved", f"all measured dimensions scored 0 ({sorted(measured)})"
    if len(measured) == 3 and all(value == 2 for value in measured.values()):
        return "achieved", "all three dimensions measured and scored 2"
    if len(measured) < 3 and all(value == 2 for value in measured.values()):
        return (
            "partially_achieved",
            f"all measured dimensions scored 2 but only {len(measured)}/3 were measured "
            f"(missing: {sorted(set(scores) - set(measured))})",
        )
    return "partially_achieved", "some dimensions measured below 2 or coverage incomplete"


def verdict_rules_document() -> list[str]:
    return [
        "没有任何维度被测得 -> insufficient_evidence（不当作 0 分）",
        "所有测得的维度都为 0 -> not_achieved",
        "三个维度都测得且都为 2 -> achieved",
        "其余情况（含只测得部分维度）-> partially_achieved",
        "未测得的维度不参与判定，也不会被算作 0 分",
    ]
