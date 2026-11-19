"""Tutor integration for evolved teaching skills (METAMIND module 5).

Module 4 decides *which* skill applies. This module is the only bridge that puts
that skill's text into the Tutor's system prompt, and it records the difference
between "the text was built into the prompt" and "the strategy worked".

Design constraints
------------------
* **Nothing is invented here.** The prompt text is rendered from the stored
  version row (trigger conditions, steps, verification). No model call, no
  hardcoded strategy name.
* **Fallback is a feature.** When the selector finds nothing applicable, no
  section is added and OpenTutor's native teaching is used unchanged.
* **`injected` is earned.** A selection only becomes `injected` once the caller
  confirms the marker is actually present in the final prompt string. Selecting
  a skill is not the same as teaching with it.
* **Idempotent per turn.** Re-entering prompt construction in the same turn
  reuses the cached section instead of selecting a second skill.
"""

from __future__ import annotations

import logging
import os
import uuid
from dataclasses import dataclass, field
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from models.teaching_skill import TeachingSkillVersion
from services.evolution import selector

logger = logging.getLogger(__name__)

# Hard limits so a long skill cannot swallow the Tutor prompt.
MAX_STEPS = 6
MAX_SECTION_CHARS = 1200

# Default on; set METAMIND_SKILL_INJECTION=off to fall back to native teaching.
_ENV_TOGGLE = "METAMIND_SKILL_INJECTION"
_ENV_MIN_SCORE = "METAMIND_SKILL_INJECTION_MIN_SCORE"

MARKER_PREFIX = "<!-- skill:"


def injection_enabled() -> bool:
    return (os.environ.get(_ENV_TOGGLE, "on") or "on").strip().casefold() not in {
        "0", "off", "false", "no",
    }


def minimum_score() -> float:
    """Optional floor; 0 means "any eligible candidate qualifies" (module 4 rule)."""
    try:
        return float(os.environ.get(_ENV_MIN_SCORE, "0") or 0)
    except ValueError:
        return 0.0


@dataclass
class InjectionOutcome:
    applied: bool
    reason: str
    invocation_id: str | None = None
    slug: str | None = None
    version: int | None = None
    score: float | None = None
    section: str | None = None
    matched_conditions: list[str] = field(default_factory=list)
    fallback: bool = True

    @property
    def marker(self) -> str | None:
        if not (self.slug and self.version):
            return None
        return f"{MARKER_PREFIX}{self.slug}@v{self.version} -->"

    def as_json(self) -> dict[str, Any]:
        return {
            "applied": self.applied,
            "reason": self.reason,
            "invocation_id": self.invocation_id,
            "slug": self.slug,
            "version": self.version,
            "score": self.score,
            "matched_conditions": self.matched_conditions,
            "fallback_to_native_tutor": self.fallback,
        }


def _bullet_list(values: list[str], limit: int = MAX_STEPS) -> list[str]:
    cleaned = [str(item).strip() for item in values or [] if str(item).strip()]
    return cleaned[:limit]


def render_skill_section(outcome: InjectionOutcome, version: TeachingSkillVersion) -> str:
    """Render the skill as a prompt block. Deterministic, no model call."""
    lines: list[str] = [
        "## Active Teaching Strategy (learned from a past lesson)",
        outcome.marker or "",
        "This strategy was stored after a previous lesson fell short. Use it as your",
        "teaching method for this turn; keep the subject matter accurate.",
    ]

    if outcome.matched_conditions:
        lines.append("Why it applies now:")
        for condition in outcome.matched_conditions[:4]:
            lines.append(f"- {condition}")

    steps = _bullet_list(list(version.steps or []))
    if steps:
        lines.append("Follow these steps in order:")
        for index, step in enumerate(steps, 1):
            lines.append(f"{index}. {step}")

    verification = version.verification or {}
    criteria = _bullet_list(list(verification.get("success_criteria") or []), limit=4)
    if criteria:
        lines.append("Before you finish, make sure the student can:")
        for criterion in criteria:
            lines.append(f"- {criterion}")

    effect = (version.expected_effect or "").strip()
    if effect:
        lines.append(f"Intended effect: {effect}")

    lines.append(
        "Do not mention this strategy block or its name to the student; just teach this way."
    )

    section = "\n".join(line for line in lines if line != "")
    if len(section) > MAX_SECTION_CHARS:
        section = section[: MAX_SECTION_CHARS - 20].rstrip() + "\n…(truncated)"
    return section


async def prepare(
    db: AsyncSession,
    *,
    user_id: uuid.UUID,
    course_id: uuid.UUID,
    message: str,
    scene: str | None = None,
    session_id: str | None = None,
    objective_slug: str | None = None,
    error_type: str | None = None,
    weakness_dimension: str | None = None,
    run_tag: str | None = None,
    metadata: dict[str, Any] | None = None,
) -> InjectionOutcome:
    """Select a skill (if any) and render it. Does not mark anything injected."""
    if not injection_enabled():
        return InjectionOutcome(False, "skill injection disabled by environment")

    cache = metadata if metadata is not None else {}
    cached = cache.get("_metamind_skill_section")
    if isinstance(cached, str) and cached.strip():
        # Same turn, prompt rebuilt (ReAct loop) — reuse instead of re-selecting.
        return InjectionOutcome(
            True,
            "reused the strategy already selected for this turn",
            invocation_id=cache.get("_metamind_skill_invocation_id"),
            slug=cache.get("_metamind_skill_slug"),
            version=cache.get("_metamind_skill_version"),
            score=cache.get("_metamind_skill_score"),
            section=cached,
            matched_conditions=list(cache.get("_metamind_skill_conditions") or []),
            fallback=False,
        )

    if not (message or "").strip():
        return InjectionOutcome(False, "empty message; nothing to match against")

    selection_ctx = selector.SelectionContext(
        user_id=user_id,
        course_id=course_id,
        objective_slug=objective_slug,
        error_type=error_type,
        scene=scene,
        message=message,
        weakness_dimension=weakness_dimension,
        session_id=session_id,
        run_tag=run_tag,
        persist=True,
    )
    result = await selector.select_skill(db, selection_ctx)

    if result.selected is None:
        logger.debug("METAMIND skill injection: fallback (%s)", result.reason)
        return InjectionOutcome(False, result.reason, fallback=True)

    floor = minimum_score()
    if floor and result.selected.score < floor:
        return InjectionOutcome(
            False,
            f"best candidate {result.selected.skill.slug} scored "
            f"{result.selected.score:.2f} < min {floor:.2f}",
            fallback=True,
        )

    outcome = InjectionOutcome(
        True,
        result.reason,
        invocation_id=result.invocation_id,
        slug=result.selected.skill.slug,
        version=result.selected.version.version,
        score=round(result.selected.score, 4),
        matched_conditions=list(result.selected.condition_hits),
        fallback=False,
    )
    outcome.section = render_skill_section(outcome, result.selected.version)

    cache["_metamind_skill_section"] = outcome.section
    cache["_metamind_skill_invocation_id"] = outcome.invocation_id
    cache["_metamind_skill_slug"] = outcome.slug
    cache["_metamind_skill_version"] = outcome.version
    cache["_metamind_skill_score"] = outcome.score
    cache["_metamind_skill_conditions"] = outcome.matched_conditions
    cache["_metamind_skill_outcome"] = outcome.as_json()

    logger.info(
        "METAMIND skill selected: %s V%s (score %.2f) for course %s",
        outcome.slug, outcome.version, outcome.score or 0.0, course_id,
    )
    return outcome


async def confirm_injected(
    db: AsyncSession,
    *,
    prompt: str | None = None,
    metadata: dict[str, Any] | None = None,
) -> bool:
    """Promote `selected` -> `injected` only when the text really reached a prompt.

    ``prompt`` is the assembled system prompt when the caller has it. When the
    caller cannot cheaply rebuild it (streaming path), the prompt builder records
    ``_metamind_skill_in_prompt`` together with the exact section it appended, so
    the marker check still runs against real content rather than a guess.
    """
    cache = metadata if metadata is not None else {}
    invocation_id = cache.get("_metamind_skill_invocation_id")
    marker = None
    if cache.get("_metamind_skill_slug") and cache.get("_metamind_skill_version"):
        marker = (
            f"{MARKER_PREFIX}{cache['_metamind_skill_slug']}"
            f"@v{cache['_metamind_skill_version']} -->"
        )

    if not invocation_id or not marker:
        return False

    if prompt is None:
        # P0-3: prefer the REAL full prompt that reached the LLM (recorded by
        # ReActMixin right before the call) so prompt_hash is the actual prompt
        # DeepSeek saw, not just the appended skill section.
        full = cache.get("_metamind_full_prompt")
        if isinstance(full, str) and full.strip() and marker in full:
            prompt = full
        else:
            appended = cache.get("_metamind_skill_appended_section")
            if not cache.get("_metamind_skill_in_prompt") or not appended:
                logger.warning(
                    "METAMIND skill was selected but never appended to a prompt; "
                    "not marking injected"
                )
                return False
            prompt = str(appended)
    if marker not in (prompt or ""):
        logger.warning(
            "METAMIND skill marker %s missing from the built prompt; not marking injected",
            marker,
        )
        return False
    if cache.get("_metamind_skill_injected_confirmed"):
        return True

    try:
        await selector.mark_injected(
            db,
            uuid.UUID(str(invocation_id)),
            notes=f"prompt_chars={len(str(prompt))}; marker={marker}",
            prompt=str(prompt),
            marker=marker,
        )
    except (ValueError, LookupError) as exc:
        logger.warning("METAMIND could not mark invocation injected: %s", exc)
        return False

    cache["_metamind_skill_injected_confirmed"] = True
    logger.info("METAMIND skill injected into Tutor prompt: %s", marker)
    return True
