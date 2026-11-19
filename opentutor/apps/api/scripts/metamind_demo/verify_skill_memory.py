"""Acceptance verification for METAMIND module 4 (Skill Memory + Selector).

Twelve required checks, run against the live API and a live model:

 1. shipped markdown skills import idempotently
 2. a new skill can be created and persisted
 3. V1 -> V2 upgrade on an existing skill
 4. version diff query
 5. V2 is still readable after a service restart
 6. selector matches by error type / weakness
 7. selector falls back to None when nothing fits
 8. a disabled skill is no longer selected
 9. an invalid skill version cannot be enabled
10. invocation tracks the exact version
11. a failed update never overwrites the previous version
12. modules 1-3 regressions still pass (run separately; this script only checks
    that learner-state / evaluations / reflections endpoints still answer)

Run from ``apps/api`` with the API up::

    .venv-win/Scripts/python.exe scripts/metamind_demo/verify_skill_memory.py
"""

from __future__ import annotations

import asyncio
import json
import os
import subprocess
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
from models.teaching_skill import (  # noqa: E402
    SkillInvocation,
    TeachingSkill,
    TeachingSkillVersion,
)
from sqlalchemy import delete, select  # noqa: E402

BASE = os.environ.get("OPENTUTOR_API", "http://localhost:8000").rstrip("/")
COURSE_ID = str(seeder._uid("course"))

CONFUSED = (
    "自进化就是 AI 记住了我喜欢例子，下次继续给我举例，这样它就更懂我了。"
    "只要能根据我改变回答，就是自进化。"
)

SITUATION = {
    "course_id": COURSE_ID,
    "objective_slug": "g1",
    "error_type": "conceptual",
    "scene": "study_session",
    "message": "学生把记住偏好当成自进化，混淆了两个概念，说不知道为什么错了",
    "weakness_dimension": "understanding",
}


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
        detail = exc.read().decode("utf-8", "replace")
        return {"__http_error__": exc.code, "detail": detail}


async def reset_learner() -> None:
    async with async_session() as db:
        await seeder._reset_learner(db, seeder._load_spec())
        await db.commit()


async def purge_skills(slugs: list[str]) -> int:
    async with async_session() as db:
        rows = (
            await db.execute(select(TeachingSkill).where(TeachingSkill.slug.in_(slugs)))
        ).scalars().all()
        for row in rows:
            await db.execute(delete(SkillInvocation).where(SkillInvocation.skill_id == row.id))
            await db.execute(
                delete(TeachingSkillVersion).where(TeachingSkillVersion.skill_id == row.id)
            )
            await db.delete(row)
        await db.commit()
        return len(rows)


def make_reflection() -> dict:
    """evaluate -> reflect; returns the reflection payload."""
    probs = {(p.get("problem_metadata") or {}).get("item_slug"): p
             for p in http("GET", f"/api/quiz/{COURSE_ID}")}
    http("POST", "/api/quiz/submit", {"problem_id": probs["a1"]["id"], "user_answer": "a"})
    evaluation = http("POST", "/api/teaching/evaluate", {
        "course_id": COURSE_ID, "objective_slug": "g1", "session_id": "m4",
        "problem_ids": [probs["a1"]["id"]],
        "open_answer": CONFUSED,
        "open_question": "请解释什么是自进化 Agent？",
        "strategy_version": "socratic_questioning@v1",
    })
    reflection = http("POST", "/api/teaching/reflect", {
        "course_id": COURSE_ID, "evaluation_id": evaluation["evaluation_id"],
    })
    return {"evaluation": evaluation, "reflection": reflection}


async def main() -> int:
    c = Checks()

    # ── 1. idempotent import ───────────────────────────────────────────────
    print("\n[1] 原有 Markdown Skills 幂等导入")
    first = http("POST", "/api/teaching/skills/import")
    second = http("POST", "/api/teaching/skills/import")
    listing = http("GET", "/api/teaching/skills?limit=200")
    slugs = {item["slug"] for item in listing["skills"]}
    expected = {"analogies", "error_analysis", "socratic_questioning",
                "spaced_retrieval", "worked_examples"}
    c.ok("all five shipped skills present", expected <= slugs, str(sorted(slugs)))
    c.ok("second import creates nothing", second["created"] == [], str(second["created"]))
    c.ok("second import leaves all unchanged",
         len(second["unchanged"]) == len(expected), str(second["unchanged"]))
    c.ok("total stays five", listing["count"] >= 5, str(listing["count"]))
    c.ok("seed skills carry provenance",
         all(item["origin"] == "seed" and item["seed_path"] for item in listing["skills"]
             if item["slug"] in expected))

    # ── 2. create + persist a new skill from a reflection ──────────────────
    print("\n[2] 由 Reflection 创建新技能并持久化")
    await reset_learner()
    made = make_reflection()
    c.ok("reflection completed", made["reflection"]["status"] == "completed",
         made["reflection"]["status"])
    proposal = made["reflection"].get("improvement_proposal") or {}
    c.ok("proposal declares an origin",
         proposal.get("strategy_origin") in {"reuse_existing", "compose_existing", "new_skill"},
         str(proposal.get("strategy_origin")))

    created = http("POST", "/api/teaching/skills/from-reflection", {
        "course_id": COURSE_ID, "reflection_id": made["reflection"]["reflection_id"],
    })
    c.ok("skill created from reflection",
         created["status"] in {"created", "version_added"}, created["status"])
    new_slug = (created.get("skill") or {}).get("slug")
    c.ok("new skill has a slug", bool(new_slug), str(new_slug))
    c.ok("new skill records its reflection source",
         (created.get("skill") or {}).get("source_reflection_id")
         == made["reflection"]["reflection_id"])
    c.ok("new skill validation passed", (created.get("validation") or {}).get("ok") is True,
         json.dumps(created.get("validation"), ensure_ascii=False)[:160])
    detail = http("GET", f"/api/teaching/skills/{new_slug}")
    c.ok("created skill is readable", detail["skill"]["slug"] == new_slug)
    c.ok("its current version carries steps",
         len((detail["current_version_detail"] or {}).get("steps") or []) >= 2)

    dup = http("POST", "/api/teaching/skills/from-reflection", {
        "course_id": COURSE_ID, "reflection_id": made["reflection"]["reflection_id"],
    })
    c.ok("re-running the same reflection does not add a version",
         dup["status"] == "duplicate", dup["status"])

    # ── 3. V1 -> V2 on an existing skill ───────────────────────────────────
    print("\n[3] 既有技能 V1 → V2 升级")
    before = http("GET", "/api/teaching/skills/socratic_questioning")
    v_before = before["skill"]["current_version"]
    upgraded = http("POST", "/api/teaching/skills/from-reflection", {
        "course_id": COURSE_ID,
        "reflection_id": made["reflection"]["reflection_id"],
        "skill_slug": "socratic_questioning",
    })
    c.ok("version appended to existing skill",
         upgraded["status"] in {"version_added", "created"}, upgraded["status"])
    after = http("GET", "/api/teaching/skills/socratic_questioning")
    v_after = after["skill"]["current_version"]
    target_v = (upgraded.get("version") or {}).get("version")
    if target_v == v_before:
        # same content as current version -> intentionally skipped
        c.ok("V1 unchanged because proposal equals current content",
             upgraded["status"] in {"skipped", "duplicate"}, upgraded["status"])
        v2 = v_before
    else:
        c.ok("current version advanced", v_after > v_before, f"{v_before} -> {v_after}")
        v2 = v_after
    versions = http("GET", "/api/teaching/skills/socratic_questioning/versions")
    c.ok("both versions are retained", versions["count"] >= 2, str(versions["count"]))
    c.ok("V1 is not mutated by the upgrade",
         next(v for v in versions["versions"] if v["version"] == 1)["created_by"]
         == "seed_import")
    c.ok("V2 keeps reflection lineage",
         next(v for v in versions["versions"] if v["version"] == v2)["source_reflection_id"]
         == made["reflection"]["reflection_id"])

    # ── 4. diff ────────────────────────────────────────────────────────────
    print("\n[4] 版本差异查询")
    diff = http("GET", f"/api/teaching/skills/socratic_questioning/diff"
                     f"?from_version=1&to_version={v2}")
    c.ok("diff names both versions", diff["from_version"] == 1 and diff["to_version"] == v2,
         f"{diff.get('from_version')} -> {diff.get('to_version')}")
    c.ok("diff lists changed fields", bool(diff["changed_fields"]),
         str(diff["changed_fields"]))
    c.ok("diff shows steps added", bool((diff["changes"].get("steps") or {}).get("added")),
         json.dumps(diff["changes"].get("steps"), ensure_ascii=False)[:120])

    # ── 5. survives restart ────────────────────────────────────────────────
    print("\n[5] 重启服务后仍能读到 V2")
    restart_script = API_ROOT.parent.parent / "scripts" / "native-control.ps1"
    pid = json.loads((API_ROOT.parent.parent / "native-data" / "runtime.json")
                     .read_text(encoding="utf-8-sig"))["api"]["pid"]
    subprocess.run(["taskkill", "/PID", str(pid), "/F"], capture_output=True, shell=True)
    await asyncio.sleep(3)
    subprocess.run(["powershell.exe", "-NoProfile", "-ExecutionPolicy", "Bypass",
                    "-File", str(restart_script), "-Action", "Start", "-NoBrowser"],
                   capture_output=True, shell=True)
    for _ in range(20):
        await asyncio.sleep(2)
        try:
            if http("GET", "/api/health/live", timeout=10).get("status") in {"ok", "healthy"}:
                break
        except Exception:  # noqa: BLE001
            continue
    after_restart = http("GET", "/api/teaching/skills/socratic_questioning")
    c.ok("service is back", after_restart["skill"]["slug"] == "socratic_questioning")
    c.ok("persisted version survives restart",
         after_restart["skill"]["current_version"] == v2,
         str(after_restart["skill"]["current_version"]))
    c.ok("created skill also survives",
         http("GET", f"/api/teaching/skills/{new_slug}")["skill"]["slug"] == new_slug)

    # ── 6. selector matches by situation ───────────────────────────────────
    print("\n[6] Selector 根据错误类型与薄弱点匹配")
    picked = http("POST", "/api/teaching/selector/select", {**SITUATION, "session_id": "m4-sel"})
    c.ok("selector returns a skill", picked["selected"] is not None,
         picked.get("reason"))
    c.ok("selected candidate explains its match",
         bool((picked["selected"] or {}).get("matched_conditions")),
         str((picked["selected"] or {}).get("matched_conditions")))
    c.ok("invocation recorded", bool(picked.get("invocation_id")))
    c.ok("ranking includes runner-ups", len(picked["candidates"]) >= 1,
         str([item["slug"] for item in picked["candidates"]]))

    # ── 7. fallback ────────────────────────────────────────────────────────
    print("\n[7] 没有适用技能时回落原生教学")
    nothing = http("POST", "/api/teaching/selector/select", {
        "course_id": COURSE_ID, "message": "zzz nothing here", "persist": False,
    })
    c.ok("fallback flag set", nothing["fallback_to_native_tutor"] is True)
    c.ok("no skill returned", nothing["selected"] is None)
    c.ok("fallback reason recorded", "fall back" in nothing["reason"], nothing["reason"])

    # ── 8. disabled skill is not selected ──────────────────────────────────
    print("\n[8] 停用后不再被选择")
    off = http("POST", "/api/teaching/skills/socratic_questioning/toggle", {"enabled": False})
    c.ok("skill disabled", off["action"] == "disabled")
    after_off = http("POST", "/api/teaching/selector/select", {**SITUATION, "persist": False})
    c.ok("disabled skill is not selected",
         (after_off["selected"] or {}).get("slug") != "socratic_questioning",
         str((after_off["selected"] or {}).get("slug")))
    back = http("POST", "/api/teaching/skills/socratic_questioning/toggle", {"enabled": True})
    c.ok("skill re-enabled", back["action"] == "enabled")

    # ── 9. invalid version cannot be enabled ───────────────────────────────
    print("\n[9] 不合格的技能配置无法启用")
    async with async_session() as db:
        skill = (
            await db.execute(
                select(TeachingSkill).where(TeachingSkill.slug == "socratic_questioning")
            )
        ).scalar_one()
        previous_version = skill.current_version
        next_v = skill.version_count + 1
        bad = TeachingSkillVersion(
            skill_id=skill.id,
            version=next_v,
            status="draft",
            trigger_conditions=[],
            target_dimensions=[],
            steps=["多练习"],
            expected_effect="",
            verification={},
            reused_skills=[],
            origin_kind="new_skill",
            created_by="verification_test",
        )
        db.add(bad)
        # Simulate a broken evolution: the pointer moved to the invalid version.
        skill.version_count = next_v
        skill.current_version = next_v
        await db.commit()
        bad_id = bad.id
        bad_version = next_v
    invalid = http("POST", "/api/teaching/skills/socratic_questioning/toggle", {"enabled": True})
    c.ok("enable attempt reports validation failure",
         (invalid.get("validation") or {}).get("ok") is False,
         json.dumps(invalid.get("validation"), ensure_ascii=False)[:200])
    c.ok("skill stays disabled after failed enable",
         invalid["skill"]["enabled"] is False, str(invalid["skill"]["enabled"]))
    # clean up the deliberately broken version and restore the pointer
    async with async_session() as db:
        skill = (
            await db.execute(
                select(TeachingSkill).where(TeachingSkill.slug == "socratic_questioning")
            )
        ).scalar_one()
        row = await db.get(TeachingSkillVersion, bad_id)
        if row is not None:
            await db.delete(row)
        skill.version_count = bad_version - 1
        skill.current_version = previous_version
        skill.enabled = True
        await db.commit()
    restored = http("POST", "/api/teaching/skills/socratic_questioning/toggle", {"enabled": True})
    c.ok("restored skill can be enabled again", restored["skill"]["enabled"] is True,
         json.dumps(restored.get("validation"), ensure_ascii=False)[:120])

    # ── 10. invocation tracks the version ──────────────────────────────────
    print("\n[10] Invocation 追踪到具体版本")
    inv_list = http("GET", f"/api/teaching/invocations?course_id={COURSE_ID}&limit=20")
    c.ok("invocations are recorded", inv_list["count"] >= 1, str(inv_list["count"]))
    first_inv = inv_list["invocations"][0]
    c.ok("invocation carries a version id", bool(first_inv["version_id"]))
    c.ok("invocation starts as selected", first_inv["status"] == "selected",
         first_inv["status"])
    c.ok("selected != injected (timestamps differ in meaning)",
         first_inv["injected_at"] is None, str(first_inv["injected_at"]))
    # P0-3: an injection is only confirmed when the caller supplies the FULL
    # prompt that reached the LLM and the marker really appears in it. A bare
    # `POST /inject` without a prompt is honestly left unverified — this mirrors
    # module 5, which never marks injected just because a selection exists.
    marker = f"<!-- skill:{first_inv.get('slug') or 'skill'}@v{first_inv['version']} -->"
    fake_full_prompt = "".join([
        "## System prompt (assembled by the Tutor builder)\n",
        "Teach the current lesson accurately.\n",
        marker,
        "\nFollow the strategy steps above.",
    ])
    injected = http("POST", f"/api/teaching/invocations/{first_inv['invocation_id']}/inject", {
        "prompt": fake_full_prompt,
        "marker": marker,
    })
    c.ok("injection is recorded separately (marker verified in full prompt)",
         injected["status"] == "injected", injected["status"])
    c.ok("injected_at is set", bool(injected["injected_at"]))
    c.ok("prompt_hash recorded from the full prompt",
         bool(injected.get("prompt_hash")) and len(injected.get("prompt_hash")) >= 64)
    c.ok("prompt_injection_verified is true", injected.get("prompt_injection_verified") is True)
    attached = http("POST", f"/api/teaching/invocations/{first_inv['invocation_id']}/evaluate", {
        "evaluation_id": made["evaluation"]["evaluation_id"],
    })
    c.ok("evaluation link is stored", attached["evaluation_id"]
         == made["evaluation"]["evaluation_id"])
    c.ok("outcome keeps the target-dimension score",
         (attached.get("outcome") or {}).get("target_dimension_score") is not None,
         json.dumps(attached.get("outcome"), ensure_ascii=False)[:160])

    # ── 11. invalid update does not overwrite ──────────────────────────────
    print("\n[11] 失败的更新不会覆盖原版本")
    async with async_session() as db:
        skill = (
            await db.execute(
                select(TeachingSkill).where(TeachingSkill.slug == "socratic_questioning")
            )
        ).scalar_one()
        keep_version = skill.current_version
        keep_count = skill.version_count

    # Build a deliberately invalid proposal: too-short step, no triggers, no
    # verification — exactly the kind of output that must never go live.
    # The reflection is only a fixture here: its proposal is overwritten below,
    # so the row is forced to "completed" to make the validation path run
    # deterministically (the reflection engine may legitimately gate this case).
    await reset_learner()
    broken = make_reflection()
    async with async_session() as db:
        row = await db.get(
            __import__("models.teaching_reflection", fromlist=["TeachingReflection"])
            .TeachingReflection,
            uuid.UUID(broken["reflection"]["reflection_id"]),
        )
        payload = dict(row.improvement_json or {})
        payload.update({
            "steps": ["多练习"],
            "trigger_conditions": [],
            "expected_effect": "",
            "reuses_skills": [],
            "strategy_origin": "reuse_existing",
            "target_dimension": "understanding",
        })
        row.improvement_json = payload
        row.verification_plan_json = {}
        row.status = "completed"
        row.objective_slug = "g1"
        await db.commit()

    failed_update = http("POST", "/api/teaching/skills/from-reflection", {
        "course_id": COURSE_ID,
        "reflection_id": broken["reflection"]["reflection_id"],
        "skill_slug": "socratic_questioning",
    })
    c.ok("invalid update reports validation failure",
         (failed_update.get("validation") or {}).get("ok") is False,
         json.dumps(failed_update.get("validation"), ensure_ascii=False)[:200])
    c.ok("invalid version is kept as a draft, not activated",
         (failed_update.get("version") or {}).get("status") == "draft",
         str((failed_update.get("version") or {}).get("status")))

    async with async_session() as db:
        skill = (
            await db.execute(
                select(TeachingSkill).where(TeachingSkill.slug == "socratic_questioning")
            )
        ).scalar_one()
        c.ok("current version pointer untouched", skill.current_version == keep_version,
             f"{keep_version} -> {skill.current_version}")
        c.ok("history keeps the failed attempt", skill.version_count == keep_count + 1,
             f"{keep_count} -> {skill.version_count}")
        active = (
            await db.execute(
                select(TeachingSkillVersion).where(
                    TeachingSkillVersion.skill_id == skill.id,
                    TeachingSkillVersion.version == keep_version,
                )
            )
        ).scalar_one()
        c.ok("previously active version stays active", active.status == "active", active.status)

    # ── 12. modules 1-3 still answer ───────────────────────────────────────
    print("\n[12] 模块 1–3 端点回归")
    state = http("GET", f"/api/teaching/learner-state?course_id={COURSE_ID}")
    c.ok("learner-state still works", state["assessment_summary"]["state"] in
         {"not_assessed", "partially_assessed", "all_mastered"},
         state["assessment_summary"]["state"])
    evals = http("GET", f"/api/teaching/evaluations?course_id={COURSE_ID}&limit=5")
    c.ok("evaluations still queryable", evals["count"] >= 1, str(evals["count"]))
    refls = http("GET", f"/api/teaching/reflections?course_id={COURSE_ID}&limit=5")
    c.ok("reflections still queryable", refls["count"] >= 1, str(refls["count"]))
    rubric = http("GET", "/api/teaching/rubric")
    c.ok("rubric still served", rubric["rubric_version"] == "metamind-eval-v1")

    await reset_learner()
    print("\n" + "=" * 60)
    print(f"PASSED {len(c.passed)}, FAILED {len(c.failed)}")
    for failure in c.failed:
        print(f"  - {failure}")
    return 0 if not c.failed else 1


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
