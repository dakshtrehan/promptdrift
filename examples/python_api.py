"""Use promptdrift from Python with records built in code.

Runs with zero API keys: ``compare_outputs`` never calls a model unless you
pass ``use_judge=True``. From the repository root:

    python examples/python_api.py

The script prints the summary dict and then shows the assertion a test would
make. For a full pytest example see examples/ci/test_no_regressions.py.
"""
from __future__ import annotations

import json

from promptdrift import compare_outputs

# One record per case. ``output_a`` is the baseline, ``output_b`` the candidate.
# ``checks`` are optional and deterministic; ``tags`` are optional labels.
RECORDS = [
    {"case_id": "capital", "input": "What is the capital of France?",
     "output_a": "Paris.", "output_b": "The capital of France is Paris.",
     "checks": {"regex": "Paris"}, "tags": ["factual"]},
    {"case_id": "percent", "input": "What is 15% of 200?", "expected": "30",
     "output_a": "30", "output_b": "15% of 200 is 30.",
     "checks": {"regex": "\\b30\\b"}, "tags": ["math"]},
    {"case_id": "extract", "input": "Return Ada, 36 as JSON with name and age.",
     "output_a": "{\"name\": \"Ada\", \"age\": 36}",
     "output_b": "{\"name\": \"Ada\", \"age\": \"thirty-six\"}",
     "checks": {"json_keys": ["name", "age"]}, "tags": ["structured"]},
    # Lists give multi-sample stability: three logged outputs per side.
    {"case_id": "greeting", "input": "Say hello.",
     "output_a": ["Hello!", "Hi there!", "Hello!"],
     "output_b": ["Hello!", "Hello there.", "Hi!"], "tags": ["tone"]},
]


def main() -> None:
    report = compare_outputs(RECORDS, version_a="baseline", version_b="candidate")

    summary = report.to_dict()["summary"]
    print(json.dumps(summary, indent=2))

    # Per-case access. Every field is a plain attribute on CaseResult.
    for case in report.cases:
        print(f"{case.case_id:10} drift={case.drift_score:.3f} level={case.drift_level:8} "
              f"format_changed={case.format_changed} check_flip={case.check_flip}")

    # In a test you would assert on the report. ``regressions`` holds cases
    # where a check went from pass to fail (or a judge said regressed).
    names = [c.case_id for c in report.regressions]
    print("regressions:", names)
    assert not report.regressions, f"regressions found: {names}"
    print("no regressions, the candidate is safe on these cases")


if __name__ == "__main__":
    main()
