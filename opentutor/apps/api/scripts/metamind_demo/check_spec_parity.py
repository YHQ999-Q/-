"""Structural parity check for the two parallel test sets (A / B).

Run with the app interpreter, from ``apps/api``::

    .venv-win/Scripts/python.exe scripts/metamind_demo/check_spec_parity.py

Fails (exit 1) when the two sets stop being comparable: different item types,
option counts, number of correct options, or duplicated slugs. This keeps the
"equal difficulty" claim machine-checkable instead of aspirational.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

SPEC_PATH = Path(__file__).resolve().parent / "demo_spec.json"


def _correct_labels(problem: dict) -> list[str]:
    return [part.strip() for part in str(problem["correct_answer"]).split(",") if part.strip()]


def _shape(problem: dict) -> dict:
    return {
        "question_type": problem["question_type"],
        "difficulty_layer": problem["difficulty_layer"],
        "option_count": len(problem["options"]),
        "correct_count": len(_correct_labels(problem)),
        "goal": problem["goal"],
        "eval_dimension": problem["eval_dimension"],
    }


def main() -> int:
    spec = json.loads(SPEC_PATH.read_text(encoding="utf-8"))
    problems = spec["problem_sets"]
    if set(problems) != {"A", "B"}:
        print(f"FAIL: expected sets A and B, found {sorted(problems)}")
        return 1

    sets = {key: payload["problems"] for key, payload in problems.items()}
    if len(sets["A"]) != len(sets["B"]):
        print(f"FAIL: item counts differ — A={len(sets['A'])} B={len(sets['B'])}")
        return 1

    rows: list[tuple[str, int, dict, dict]] = []
    failures: list[str] = []
    for index, (a, b) in enumerate(zip(sets["A"], sets["B"]), start=1):
        shape_a, shape_b = _shape(a), _shape(b)
        rows.append((f"{a['slug']} / {b['slug']}", index, shape_a, shape_b))
        for field in ("question_type", "difficulty_layer", "option_count", "correct_count",
                      "goal", "eval_dimension"):
            if shape_a[field] != shape_b[field]:
                failures.append(f"item {index}: {field} differs — A={shape_a[field]} B={shape_b[field]}")

    all_slugs = [p["slug"] for items in sets.values() for p in items]
    if len(all_slugs) != len(set(all_slugs)):
        failures.append("duplicate problem slug across sets")

    for problem in sets["A"] + sets["B"]:
        labels = set(problem["options"])
        correct = set(_correct_labels(problem))
        if not correct <= labels:
            failures.append(f"{problem['slug']}: correct answer not among options {sorted(labels)}")
        if problem["question_type"] == "mc" and len(correct) != 1:
            failures.append(f"{problem['slug']}: mc item must have exactly one correct label")
        if problem["question_type"] == "select_all" and len(correct) < 2:
            failures.append(f"{problem['slug']}: select_all item should have >= 2 correct labels")

    print(f"{'pair':<16}{'type':<12}{'layer':<7}{'opts':<6}{'correct':<8}{'goal':<6}dimension")
    for name, _index, shape_a, shape_b in rows:
        print(
            f"{name:<16}{shape_a['question_type']:<12}{shape_a['difficulty_layer']:<7}"
            f"{shape_a['option_count']:<6}{shape_a['correct_count']:<8}{shape_a['goal']:<6}"
            f"{shape_a['eval_dimension']}"
        )
        assert shape_a == shape_b, (shape_a, shape_b)

    layer_mix = sorted(p["difficulty_layer"] for p in sets["A"])
    print(f"\ndifficulty layers (set A): {layer_mix}")
    print(f"open-ended prompts: A={len(spec['open_ended']['A'])} B={len(spec['open_ended']['B'])}")
    if len(spec["open_ended"]["A"]) != len(spec["open_ended"]["B"]):
        failures.append("open-ended prompt counts differ")

    if failures:
        print("\nFAIL")
        for line in failures:
            print(f" - {line}")
        return 1
    print("\nPASS: A and B are structurally parallel")
    return 0


if __name__ == "__main__":
    sys.exit(main())
