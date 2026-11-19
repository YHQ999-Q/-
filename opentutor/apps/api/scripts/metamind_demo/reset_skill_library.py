"""Reset the skill library to the shipped seed state (rollback helper).

Deletes every version that was not produced by the markdown import, drops
reflection-created skills, resets each seed skill's pointer to V1, and re-imports
the seeds. Invocation history is cleared too, because it references versions that
no longer exist.

Run from ``apps/api``:

    .venv-win/Scripts/python.exe scripts/metamind_demo/reset_skill_library.py --yes
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPT_DIR.parents[1]))
sys.path.insert(0, str(SCRIPT_DIR))

import seed_demo_course as seeder  # noqa: E402  (bootstraps settings.env)

from sqlalchemy import delete, select, update  # noqa: E402

from database import async_session  # noqa: E402
from models.teaching_skill import (  # noqa: E402
    ORIGIN_SEED,
    SkillInvocation,
    TeachingSkill,
    TeachingSkillVersion,
)
from services.evolution import skill_store  # noqa: E402


async def reset() -> dict:
    async with async_session() as db:
        non_seed = (
            await db.execute(
                select(TeachingSkillVersion.id).where(
                    TeachingSkillVersion.created_by != "seed_import"
                )
            )
        ).scalars().all()
        if non_seed:
            await db.execute(
                delete(SkillInvocation).where(SkillInvocation.version_id.in_(non_seed))
            )
        await db.execute(delete(SkillInvocation))
        removed_versions = (
            await db.execute(
                delete(TeachingSkillVersion).where(
                    TeachingSkillVersion.created_by != "seed_import"
                )
            )
        ).rowcount or 0
        for skill in (await db.execute(select(TeachingSkill))).scalars().all():
            if skill.origin != ORIGIN_SEED:
                await db.delete(skill)
                continue
            skill.current_version = 1
            skill.version_count = 1
            skill.enabled = True
            skill.source_reflection_id = None
            skill.source_evaluation_id = None
        await db.execute(
            update(TeachingSkillVersion).values(status="active", version=1)
        )
        await db.commit()
        report = await skill_store.import_markdown_skills(db)
        await db.commit()
        remaining = (await db.execute(select(TeachingSkill))).scalars().all()
        return {
            "removed_versions": removed_versions,
            "imported": report.as_json(),
            "skills": [{"slug": s.slug, "enabled": s.enabled,
                        "v": s.current_version, "count": s.version_count}
                       for s in remaining],
        }


def main() -> None:
    parser = argparse.ArgumentParser(description="Reset teaching skills to seed state.")
    parser.add_argument("--yes", action="store_true", help="required")
    args = parser.parse_args()
    if not args.yes:
        print("refusing to reset skills without --yes")
        raise SystemExit(2)
    print(json.dumps(asyncio.run(reset()), ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
