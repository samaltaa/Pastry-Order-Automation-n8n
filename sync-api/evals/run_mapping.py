"""Mapping eval: four POS layouts with known correct answers.

Each case checks the target for every column, the transform for the tricky columns, the key column,
the rows to exclude, and that the mapping passes the approval checks. Calls Claude, so it costs money
(about $0.05 a run). Run from sync-api/:  uv run python -m evals.run_mapping
"""

import json
import os
import sys
from pathlib import Path

import anthropic

from app.llm import propose_mapping
from app.mapping import problems

CASES = Path(__file__).with_name("mapping_cases.json")


def score(case: dict, mapping) -> list[tuple[str, bool, str]]:
    by_column = {field.source_column: field for field in mapping.fields}
    checks = []
    for column, target in case["expected"].items():
        got = by_column[column].target if column in by_column else "missing"
        checks.append((f"{column} -> {target}", got == target, got))
    for column, allowed in case["transforms"].items():
        got = by_column[column].transform if column in by_column else "missing"
        checks.append((f"{column} transform {'/'.join(allowed)}", got in allowed, got))
    checks.append(("key column", mapping.key_column in case["key"], mapping.key_column))

    excluded = sorted(row.key.strip() for row in mapping.excluded_rows)
    checks.append(("excluded rows", excluded == sorted(case["excluded"]), str(excluded)))

    columns = [column["name"] for column in case["table"]["columns"]]
    found = problems(mapping, columns)
    checks.append(("passes approval checks", not found, "; ".join(found)))
    return checks


def main() -> int:
    model = os.environ.get("ANTHROPIC_MODEL_STRONG", "claude-sonnet-5-5")
    minimum = float(os.environ.get("EVAL_MIN_ACCURACY", "0.9"))
    client = anthropic.Anthropic(timeout=120, max_retries=2)

    passed = total = 0
    cost = 0.0
    for case in json.loads(CASES.read_text()):
        mapping, call = propose_mapping(client, model, case["table"])
        cost += call["cost_usd"]
        checks = score(case, mapping)
        case_passed = sum(ok for _, ok, _ in checks)
        passed += case_passed
        total += len(checks)
        print(f"\n{case['name']}: {case_passed}/{len(checks)}  ({call['latency_ms']} ms)")
        for name, ok, got in checks:
            if not ok:
                print(f"  FAIL {name}: got {got}")

    accuracy = passed / total
    print(f"\n{model}: {passed}/{total} = {accuracy:.1%}, cost ${cost:.4f}")
    return 0 if accuracy >= minimum else 1


if __name__ == "__main__":
    sys.exit(main())
