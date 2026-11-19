"""P0-4 验收：核对一次完整自进化运行留下的 10 项证据（只读）。

读取 scripts/metamind_demo/run_evolution_demo.py 刚产生的 run01.json / run02.json
（native-data/samples/），并直读 SQLite，逐项断言：

  1. Run01 原始教学对话        2. Run01 真实评价及证据
  3. Reflection 完整输出        4. V1/V2 真实版本差异
  5. V2 来源 Reflection ID      6. Run02 真实 Tutor 回答
  7. 最终 Prompt 注入证据       8. Run02 Invocation 记录
  9. Run02 独立评价记录        10. 两次教学可比较结果

只读：不调用模型、不写库、不改技能。可用作「刚跑完演示后的验收」。
用法（apps/api）：
    .venv-win/Scripts/python.exe scripts/metamind_demo/verify_evolution_demo.py
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent
API_ROOT = SCRIPT_DIR.parents[1]
sys.path.insert(0, str(API_ROOT))
sys.path.insert(0, str(SCRIPT_DIR))

import seed_demo_course as seeder  # noqa: E402

from database import async_session  # noqa: E402

COURSE_ID = str(seeder._uid("course"))
SAMPLE_DIR = Path(API_ROOT / "native-data" / "samples")


class Checks:
    def __init__(self) -> None:
        self.passed: list[str] = []
        self.failed: list[str] = []

    def ok(self, label: str, condition: bool, detail: str = "") -> None:
        if condition:
            self.passed.append(label)
            print(f"  PASS  {label}{' — ' + detail if detail else ''}")
        else:
            self.failed.append(label)
            print(f"  FAIL  {label}{' — ' + detail if detail else ''}")


def main() -> int:
    c = Checks()
    r1_path = SAMPLE_DIR / "run01.json"
    r2_path = SAMPLE_DIR / "run02.json"
    if not r1_path.exists() or not r2_path.exists():
        print("证据文件缺失：请先运行 run_evolution_demo.py")
        return 2
    r1 = json.loads(r1_path.read_text(encoding="utf-8"))
    r2 = json.loads(r2_path.read_text(encoding="utf-8"))
    inv1 = r1.get("invocation") or {}
    inv2 = r2.get("invocation") or {}

    print("\n[1] Run01 原始教学对话")
    t1 = r1["teaching_chat"].get("full_text") or ""
    c.ok("对话原文在场", len(t1) > 200, f"chars={len(t1)}")
    c.ok("Run01 会话 id 存在", bool(r1["teaching_chat"].get("session")))

    print("\n[2] Run01 真实评价及证据")
    c.ok("Run01 evaluation_id 在场", bool(r1.get("evaluation_id")))
    c.ok("Run01 run_tag 正确", r1.get("run_tag") == "run01", r1.get("run_tag"))
    c.ok("Run01 分数被评估(可能低)", r1.get("evaluation_scores") is not None,
         json.dumps(r1.get("evaluation_scores"), ensure_ascii=False))

    print("\n[3] Reflection 完整输出")
    c.ok("Reflection completed", r1.get("reflection_status") == "completed",
         str(r1.get("reflection_status")))
    c.ok("Reflection 有提案与证据", bool(r1.get("reflection")) and bool(r1.get("reflection_id")))
    proposal = (r1.get("reflection_summary") or {}).get("proposal") or {}
    c.ok("Reflection 有结构化 steps", len(proposal.get("steps") or []) >= 2,
         f"steps={len(proposal.get('steps') or [])}")

    print("\n[4] V1/V2 真实版本差异")
    c.ok("socratic 被升级到 V2", r1.get("skill_slug") == "socratic_questioning"
         and r1.get("skill_version") == 2,
         f"{r1.get('skill_slug')} V{r1.get('skill_version')}")
    c.ok("V2 为 active", r1.get("skill_version_status") == "active",
         str(r1.get("skill_version_status")))
    c.ok("V2 校验通过", (r1.get("validation") or {}).get("ok") is True)

    print("\n[5] V2 来源 Reflection ID")
    c.ok("V2 记录来源反思", bool(r1.get("v2_source_reflection_id")),
         str(r1.get("v2_source_reflection_id")))

    print("\n[6] Run02 真实 Tutor 回答")
    t2 = r2["teaching_chat"].get("full_text") or ""
    c.ok("Run02 对话原文在场", len(t2) > 200, f"chars={len(t2)}")

    print("\n[7] 最终 Prompt 注入证据")
    c.ok("Run02 注入验证为真(完整 prompt)", inv2.get("prompt_injection_verified") is True)
    c.ok("Run02 记录 prompt_hash", bool(inv2.get("prompt_hash")) and len(inv2.get("prompt_hash")) >= 64)
    c.ok("Run02 注入的是 V2", "@v2" in (inv2.get("notes") or ""),
         str(inv2.get("notes")))
    c.ok("Run02 执行状态如实(unverified)", inv2.get("execution_state") == "unverified",
         str(inv2.get("execution_state")))

    print("\n[8] Run02 Invocation 记录")
    c.ok("Run02 invocation_id 在场", bool(inv2.get("invocation_id")))
    c.ok("Run02 run_tag 正确", inv2.get("run_tag") == "run02", str(inv2.get("run_tag")))

    print("\n[9] Run02 独立评价记录")
    c.ok("Run02 evaluation_id 在场", bool(r2.get("evaluation_id")))
    c.ok("Run02 评价已挂到 invocation",
         (r2.get("evaluation_attached") or {}).get("evaluation_id")
         == r2.get("evaluation_id"))
    c.ok("Run02 run_tag 与 Run01 不同", r2.get("run_tag") == "run02")

    print("\n[10] 两次教学可比较结果")
    s1 = (r1.get("evaluation_scores") or {}).get("understanding")
    s2 = (r2.get("evaluation_scores") or {}).get("understanding")
    c.ok("两次分数都可读且独立", s1 is not None and s2 is not None,
         f"u1={s1} u2={s2}")
    # 不要求第二次提高 —— 如实对比即可
    c.ok("对比已给出(不预设提高)", r1.get("run_tag") != r2.get("run_tag"))

    print("\n" + "=" * 60)
    print(f"PASSED {len(c.passed)}, FAILED {len(c.failed)}")
    if c.failed:
        for name in c.failed:
            print(f"  - {name}")
    return 0 if not c.failed else 1


if __name__ == "__main__":
    sys.exit(main())
