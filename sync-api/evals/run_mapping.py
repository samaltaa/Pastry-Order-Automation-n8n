"""Mapping eval: four POS layouts with known correct answers.

Each case checks the target for every column, the transform for the tricky columns, the key column,
the rows to exclude, and that the mapping passes the approval checks. Calls Claude, so it costs money.
Run from sync-api/:

    uv run python -m evals.run_mapping              # the pipeline production uses (optimized)
    uv run python -m evals.run_mapping --compare    # baseline vs optimized, side by side
"""

import json
import os
import sys
from pathlib import Path

import anthropic

from app.llm import propose_mapping, propose_routed
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


def run(client, pipeline: str) -> dict:
    strong = os.environ.get("ANTHROPIC_MODEL_STRONG", "claude-sonnet-5-5")
    fast = os.environ.get("ANTHROPIC_MODEL_FAST", "claude-haiku-5-5")
    totals = dict.fromkeys(["passed", "total", "input", "output", "cache_read", "cost", "ms"], 0)
    print(f"\n== {pipeline}")
    for case in json.loads(CASES.read_text()):
        if pipeline == "baseline":
            mapping, call = propose_mapping(client, strong, case["table"])
            call["route"] = strong
        else:
            mapping, call = propose_routed(client, fast, strong, case["table"])
        checks = score(case, mapping)
        case_passed = sum(ok for _, ok, _ in checks)
        for key, value in (
            ("passed", case_passed),
            ("total", len(checks)),
            ("input", call["input_tokens"]),
            ("output", call["output_tokens"]),
            ("cache_read", call.get("cache_read_tokens", 0)),
            ("cost", call["cost_usd"]),
            ("ms", call["latency_ms"]),
        ):
            totals[key] += value
        print(
            f"{case['name']}: {case_passed}/{len(checks)} | {call['input_tokens']} in,"
            f" {call['output_tokens']} out | ${call['cost_usd']:.4f} | {call['latency_ms']} ms"
            f" | {call['route']}"
        )
        for name, ok, got in checks:
            if not ok:
                print(f"  FAIL {name}: got {got}")
    totals["accuracy"] = totals["passed"] / totals["total"]
    print(
        f"{pipeline}: {totals['passed']}/{totals['total']} = {totals['accuracy']:.1%}, ${totals['cost']:.4f}"
    )
    return totals


def main() -> int:
    minimum = float(os.environ.get("EVAL_MIN_ACCURACY", "0.9"))
    client = anthropic.Anthropic(timeout=120, max_retries=2)
    if "--compare" not in sys.argv:
        return 0 if run(client, "optimized")["accuracy"] >= minimum else 1

    results = {pipeline: run(client, pipeline) for pipeline in ("baseline", "optimized")}
    base, best = results["baseline"], results["optimized"]
    print("\n| | baseline | optimized | change |\n|---|---|---|---|")
    for label, key, fmt in (
        ("Accuracy", "accuracy", "{:.1%}"),
        ("Input tokens", "input", "{:,}"),
        ("Output tokens", "output", "{:,}"),
        ("Cost (4 layouts)", "cost", "${:.4f}"),
        ("Time", "ms", "{:,} ms"),
    ):
        change = (
            "" if key == "accuracy" else f"{(best[key] - base[key]) / base[key]:+.0%}" if base[key] else ""
        )
        print(f"| {label} | {fmt.format(base[key])} | {fmt.format(best[key])} | {change} |")
    return 0 if best["accuracy"] >= minimum else 1


if __name__ == "__main__":
    sys.exit(main())
