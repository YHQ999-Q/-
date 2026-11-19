"""Acceptance verification for METAMIND module 5 (Tutor Integration).

Checks, against the live API + a live model:

  T1  a matching situation injects the learned skill into the Tutor prompt
      (invocation goes selected -> injected, with a real injected_at)
  T2  an unrelated message injects nothing and records no invocation
  T3  a disabled skill is not injected
  T4  injection without an evaluation leaves the invocation at `injected`
      (injected != executed)
  T5  injection + evaluation closes the loop (outcome carries the score)
  T6  prompt assembly keeps OpenTutor's native behaviour: the learned section
      appears when selected, the legacy fragments still appear when not

Run from ``apps/api`` with the API up::

    .venv-win/Scripts/python.exe scripts/metamind_demo/verify_tutor_integration.py
"""

from __future__ import annotations

import asyncio
import json
import os
import sys
import urllib.error
import urllib.request
import uuid
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent
API_ROOT = SCRIPT_DIR.parents[1]
sys.path.insert(0, str(API_ROOT))
sys.path.insert(0, str(SCRIPT_DIR))

import seed_demo_course as seeder  # noqa: E402

from database import async_session  # noqa: E402
from models.teaching_skill import SkillInvocation, TeachingSkill  # noqa: E402
from sqlalchemy import select  # noqa: E402

BASE = os.environ.get("OPENTUTOR_API", "http://localhost:8000").rstrip("/")
COURSE_ID = str(seeder._uid("course"))

CONFUSED = (
    "自进化就是 AI 记住了我喜欢例子，下次继续给我举例，这样它就更懂我了。"
    "只要能根据我改变回答，就是自进化。"
)
UNRELATED = "zzzq wwvv"


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


def http(method: str, path: str, body: dict | None = None, timeout: int = 300):
    data = json.dumps(body, ensure_ascii=False).encode("utf-8") if body is not None else None
    req = urllib.request.Request(
        f"{BASE}{path}", data=data, method=method,
        headers={"Content-Type": "application/json; charset=utf-8"} if data else {},
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return json.loads(r.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        return {"__http_error__": exc.code, "detail": exc.read().decode("utf-8", "replace")}


def chat(message: str, session_id: str | None = None, timeout: int = 300) -> dict:
    """POST /api/chat/ and consume the SSE stream. Returns text + any error."""
    payload: dict = {"course_id": COURSE_ID, "message": message}
    if session_id:
        payload["session_id"] = session_id
    body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    req = urllib.request.Request(
        f"{BASE}/api/chat/", data=body, method="POST",
        headers={"Content-Type": "application/json; charset=utf-8",
                 "Accept": "text/event-stream"},
    )
    text_parts: list[str] = []
    events: list[str] = []
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            for raw in resp:
                line = raw.decode("utf-8", "replace").strip()
                if not line:
                    continue
                if line.startswith("event:"):
                    events.append(line.split(":", 1)[1].strip())
                elif line.startswith("data:"):
                    payload = line.split(":", 1)[1].strip()
                    try:
                        parsed = json.loads(payload)
                    except json.JSONDecodeError:
                        continue
                    if isinstance(parsed, dict) and parsed.get("content"):
                        text_parts.append(str(parsed["content"]))
    except urllib.error.HTTPError as exc:
        return {"error": exc.code, "detail": exc.read().decode("utf-8", "replace")}
    return {"text": "".join(text_parts), "events": events, "error": None}


async def latest_chat_session_id() -> str | None:
    """The chat router creates the session itself, so read it back afterwards."""
    from models.chat_session import ChatSession  # local import: keeps the script light

    async with async_session() as db:
        row = (
            await db.execute(
                select(ChatSession)
                .where(ChatSession.course_id == uuid.UUID(COURSE_ID))
                .order_by(ChatSession.created_at.desc())
                .limit(1)
            )
        ).scalar_one_or_none()
        return str(row.id) if row else None


async def reset_learner() -> None:
    async with async_session() as db:
        await seeder._reset_learner(db, seeder._load_spec())
        await db.commit()


async def invocations_for(session_id: str) -> list[dict]:
    async with async_session() as db:
        rows = (
            await db.execute(
                select(SkillInvocation)
                .where(SkillInvocation.session_id == session_id)
                .order_by(SkillInvocation.created_at.desc())
            )
        ).scalars().all()
        return [
            {
                "id": str(row.id),
                "skill_id": str(row.skill_id),
                "version": row.version,
                "status": row.status,
                "match_score": row.match_score,
                "selected_at": row.selected_at.isoformat() if row.selected_at else None,
                "injected_at": row.injected_at.isoformat() if row.injected_at else None,
                "evaluated_at": row.evaluated_at.isoformat() if row.evaluated_at else None,
                "evaluation_id": str(row.evaluation_id) if row.evaluation_id else None,
                "outcome": row.outcome_json,
                "notes": row.notes,
            }
            for row in rows
        ]


async def clear_session_invocations(session_id: str) -> None:
    async with async_session() as db:
        rows = (
            await db.execute(
                select(SkillInvocation).where(SkillInvocation.session_id == session_id)
            )
        ).scalars().all()
        for row in rows:
            await db.delete(row)
        await db.commit()


async def slug_for_skill(skill_id: str) -> str | None:
    async with async_session() as db:
        row = await db.get(TeachingSkill, uuid.UUID(str(skill_id)))
        return row.slug if row else None


async def set_skill_enabled(slug: str, enabled: bool) -> None:
    async with async_session() as db:
        skill = (
            await db.execute(select(TeachingSkill).where(TeachingSkill.slug == slug))
        ).scalar_one()
        skill.enabled = enabled
        await db.commit()


async def ensure_skill_enabled(slug: str) -> int:
    async with async_session() as db:
        skill = (
            await db.execute(select(TeachingSkill).where(TeachingSkill.slug == slug))
        ).scalar_one()
        skill.enabled = True
        await db.commit()
        return skill.current_version


def prompt_assembly_checks(c: Checks) -> None:
    """T6 — prompt assembly (no network): learned section vs legacy fragments."""
    from services.agent.agents.tutor import TutorAgent
    from services.agent.state import AgentContext

    agent = TutorAgent()
    base_ctx = AgentContext(
        user_id=uuid.UUID("8f362d7b-2b52-4104-8f79-59eaedd08ab8"),
        course_id=uuid.UUID(COURSE_ID),
    )
    base_ctx.user_message = CONFUSED
    base_ctx.metadata["scene"] = "study_session"

    plain = agent.build_system_prompt(base_ctx)
    c.ok("native prompt has no learned-strategy marker",
         "<!-- skill:" not in plain, "plain prompt chars=%d" % len(plain))
    c.ok("native prompt keeps OpenTutor's own teaching strategy block",
         "## Teaching Strategies" in plain or "## Teaching Mode" in plain,
         "legacy block present")
    c.ok("native prompt does not claim an appended section",
         not base_ctx.metadata.get("_metamind_skill_in_prompt"))

    learned_ctx = AgentContext(
        user_id=base_ctx.user_id, course_id=base_ctx.course_id
    )
    learned_ctx.user_message = CONFUSED
    learned_ctx.metadata["scene"] = "study_session"
    learned_ctx.metadata["_metamind_skill_section"] = (
        "## Active Teaching Strategy (learned from a past lesson)\n"
        "<!-- skill:socratic_questioning@v2 -->\n"
        "1. Show two contrasting cases.\n"
    )
    learned = agent.build_system_prompt(learned_ctx)
    c.ok("learned section is really in the prompt",
         "<!-- skill:socratic_questioning@v2 -->" in learned,
         "learned prompt chars=%d" % len(learned))
    c.ok("prompt builder records the appended section as evidence",
         learned_ctx.metadata.get("_metamind_skill_in_prompt") is True
         and "<!-- skill:socratic_questioning@v2 -->"
         in str(learned_ctx.metadata.get("_metamind_skill_appended_section") or ""))
    c.ok("learned strategy suppresses the generic fragment block",
         "## Teaching Strategies" not in learned,
         "generic fragments skipped on purpose")


async def main() -> int:
    c = Checks()

    # ── T6 first: pure prompt assembly, no model ───────────────────────────
    print("\n[T6] Prompt 装配（不调模型）")
    prompt_assembly_checks(c)

    # ── setup: one clear teaching situation, skills enabled ────────────────
    print("\n[setup] 重置学习者状态并启用技能")
    await reset_learner()
    version = await ensure_skill_enabled("socratic_questioning")
    c.ok("target skill is enabled", True, f"socratic_questioning current=v{version}")

    # ── T1: matching message -> injected ───────────────────────────────────
    print("\n[T1] 命中场景把技能注入 Tutor prompt")
    reply = chat(CONFUSED)
    s1 = await latest_chat_session_id() or ""
    c.ok("chat answered", bool((reply.get("text") or "").strip()),
         f"chars={len(reply.get('text') or '')}, error={reply.get('error')}")
    c.ok("chat turn produced a session", bool(s1), s1)
    rows = await invocations_for(s1)
    c.ok("an invocation was recorded for this turn", len(rows) >= 1, f"{len(rows)} row(s)")
    t1_invocation: dict | None = None
    if rows:
        row = rows[0]
        c.ok("invocation reached injected", row["status"] == "injected", row["status"])
        c.ok("injected_at is set", bool(row["injected_at"]), str(row["injected_at"]))
        c.ok("injected_at is after selected_at",
             bool(row["selected_at"] and row["injected_at"]
                  and row["injected_at"] >= row["selected_at"]),
             f"{row['selected_at']} -> {row['injected_at']}")
        c.ok("notes carry the prompt marker", "<!-- skill:" in str(row["notes"] or ""),
             str(row["notes"])[:120])
        c.ok("the very marker is in the prompt evidence",
             "<!-- skill:" in str(row["notes"] or ""))
        c.ok("match score is recorded", row["match_score"] is not None,
             str(row["match_score"]))
        t1_invocation = row

    # ── T2: unrelated message -> nothing injected ──────────────────────────
    print("\n[T2] 无关消息不注入、不留调用")
    reply2 = chat(UNRELATED)
    s2 = await latest_chat_session_id() or ""
    rows2 = await invocations_for(s2) if s2 else []
    c.ok("unrelated chat still answers", bool((reply2.get("text") or "").strip())
         and reply2.get("error") is None,
         f"chars={len(reply2.get('text') or '')}, error={reply2.get('error')}")
    c.ok("no invocation recorded for the unrelated turn", len(rows2) == 0,
         f"{len(rows2)} row(s) in session {s2}")

    # ── T3: disabled skill is not injected ─────────────────────────────────
    print("\n[T3] 停用后不再注入")
    selected_slug = await slug_for_skill(t1_invocation["skill_id"]) if t1_invocation else None
    c.ok("the skill selected in T1 is identifiable", bool(selected_slug), str(selected_slug))
    await set_skill_enabled(selected_slug, False)
    chat(CONFUSED)
    s3 = await latest_chat_session_id() or ""
    rows3 = await invocations_for(s3) if s3 else []
    disabled_hits = [row for row in rows3
                     if str(row["skill_id"]) == str(t1_invocation["skill_id"])]
    c.ok("disabled skill is not selected", len(disabled_hits) == 0,
         f"{len(disabled_hits)} matching row(s) of {len(rows3)}")
    await set_skill_enabled(selected_slug, True)

    # ── T4: injected but not evaluated ─────────────────────────────────────
    print("\n[T4] 只注入不评价 → 停在 injected")
    latest = (await invocations_for(s1)) or [{}]
    c.ok("T1 invocation has no outcome yet", latest[0].get("outcome") is None,
         str(latest[0].get("outcome")))
    c.ok("T1 invocation has no evaluated_at", latest[0].get("evaluated_at") is None,
         str(latest[0].get("evaluated_at")))

    # ── T5: close the loop with a real evaluation ──────────────────────────
    print("\n[T5] 注入 + 评价 = 闭环")
    probs = {(p.get("problem_metadata") or {}).get("item_slug"): p
             for p in http("GET", f"/api/quiz/{COURSE_ID}")}
    http("POST", "/api/quiz/submit", {"problem_id": probs["a1"]["id"], "user_answer": "a"})
    evaluation = http("POST", "/api/teaching/evaluate", {
        "course_id": COURSE_ID, "objective_slug": "g1", "session_id": s1,
        "problem_ids": [probs["a1"]["id"]],
        "open_answer": CONFUSED,
        "open_question": "请解释什么是自进化 Agent？",
        "strategy_version": f"socratic_questioning@v{version}",
    })
    target_invocation = (t1_invocation or latest[0] or {}).get("id")
    if not target_invocation:
        c.ok("T5 needs an injected invocation to attach to", False, "none found")
        print("\n" + "=" * 60)
        print(f"PASSED {len(c.passed)}, FAILED {len(c.failed)}")
        return 1
    attached = http("POST", f"/api/teaching/invocations/{target_invocation}/evaluate", {
        "evaluation_id": evaluation["evaluation_id"],
    })
    c.ok("invocation reached evaluated", attached.get("status") == "evaluated",
         str(attached.get("status")))
    c.ok("outcome carries the target-dimension score",
         (attached.get("outcome") or {}).get("target_dimension_score") is not None,
         json.dumps(attached.get("outcome"), ensure_ascii=False)[:140])
    outcome = attached.get("outcome") or {}
    stored_score = outcome.get("target_dimension_score")
    expected_success = bool(stored_score is not None and stored_score >= 2)
    c.ok("success follows the stored score, not the wish",
         outcome.get("success") == expected_success,
         f"score={stored_score} success={outcome.get('success')} expected={expected_success}")
    c.ok("evaluated_at is set", bool(attached.get("evaluated_at")),
         str(attached.get("evaluated_at")))

    # ── summary ────────────────────────────────────────────────────────────
    print("\n" + "=" * 60)
    print(f"PASSED {len(c.passed)}, FAILED {len(c.failed)}")
    if c.failed:
        for name in c.failed:
            print(f"  - {name}")
    return 0 if not c.failed else 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
