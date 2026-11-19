# -*- coding: utf-8 -*-
"""Bee 第二大脑 → 教学库同步桥（只读 Bee，只写教学库自己的表）

为什么需要它：
  * Bee（second-brain/）负责「采集—整理—记忆—检索」；
  * 教学 Agent（teaching/）负责计划、诊断、自测与策略升版；
  * 两个库的职责不同，不互相改 schema，只通过本桥同步**已确认**的条目。

幂等：Bee 条目用 ``bee:<id>`` 作为 fact_id / insight_id，重复执行不会翻倍。
不搬运：pending 事实、未接受的待办建议、引擎内部状态都不进来。

用法：
    python bridge/bee_sync.py
    python bridge/bee_sync.py --bee-db second-brain/data/demo.db
"""

from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
TEACHING = ROOT / "teaching"
sys.path.insert(0, str(TEACHING))

import teach_db as db  # noqa: E402  (teaching service's own data layer)


def _rows(conn: sqlite3.Connection, sql: str) -> list[sqlite3.Row]:
    return conn.execute(sql).fetchall()


def sync(bee_db: Path) -> dict[str, int]:
    if not bee_db.is_file():
        raise SystemExit(f"Bee 数据库不存在：{bee_db}\n先在 second-brain/ 里运行：py -3.13 -m beeagent demo")

    conn = sqlite3.connect(f"file:{bee_db.as_posix()}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    counts = {"facts": 0, "insights": 0, "todos": 0, "skipped": 0}
    existing_facts = {row.get("fact_id") for row in db.list_facts(1000)}
    existing_insights = {row.get("insight_id") for row in db.list_insights(limit=1000)}
    existing_todos = {row["text"] for row in db.list_todos("open", limit=1000)}

    try:
        # 1) 只同步已确认事实（AI 抽出的 pending 不进入教学判断）
        for row in _rows(
            conn,
            "SELECT id, text, tags, created_at FROM facts "
            "WHERE status='confirmed' OR confirmed=1 ORDER BY id",
        ):
            tags = []
            try:
                tags = json.loads(row["tags"] or "[]")
            except Exception:
                pass
            created = (row["created_at"] or "")[:10]
            fact_id = f"bee:{row['id']}"
            db.add_fact(
                row["text"],
                fact_id=fact_id,
                source="bee:second_brain",
                date=created,
                tags=["bee", "second_brain", *tags][:6],
                meta={"bee_fact_id": row["id"]},
            )
            if fact_id in existing_facts:
                counts["skipped"] += 1
            else:
                counts["facts"] += 1
                existing_facts.add(fact_id)

        # 2) Growth 洞察：保留原条目与日期，来源标成 bee
        for row in _rows(
            conn,
            "SELECT id, title, description, suggested_actions, generated_at, evidence "
            "FROM insights ORDER BY id",
        ):
            suggested = []
            try:
                suggested = json.loads(row["suggested_actions"] or "[]")
            except Exception:
                pass
            created = (row["generated_at"] or "")[:10]
            insight_id = f"bee:{row['id']}"
            db.add_insight(
                row["title"],
                description=row["description"] or "",
                kind="insight",
                insight_id=insight_id,
                suggested=suggested,
                date=created,
                source="bee",
                meta={"bee_insight_id": row["id"]},
            )
            if insight_id in existing_insights:
                counts["skipped"] += 1
            else:
                counts["insights"] += 1
                existing_insights.add(insight_id)

        # 3) 已接受的待办：只同步真正落成任务的那部分
        for row in _rows(
            conn,
            "SELECT id, text, alarm_at FROM todos ORDER BY id",
        ):
            if row["text"] in existing_todos:
                counts["skipped"] += 1
                continue
            db.add_todo(
                row["text"],
                due=(row["alarm_at"] or "")[:10],
                source="bee",
                priority=1,
                meta={"bee_todo_id": row["id"]},
                dedupe=True,
            )
            counts["todos"] += 1
            existing_todos.add(row["text"])
    finally:
        conn.close()

    return counts


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--bee-db",
        type=Path,
        default=ROOT / "second-brain" / "data" / "demo.db",
        help="Bee SQLite database (default: second-brain/data/demo.db)",
    )
    args = parser.parse_args()
    counts = sync(args.bee_db)
    print(
        "同步完成："
        f"新增事实 {counts['facts']}，洞察 {counts['insights']}，待办 {counts['todos']}；"
        f"重复/未接受 {counts['skipped']}。"
    )


if __name__ == "__main__":
    main()
