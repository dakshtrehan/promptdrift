"""Assert "no prompt regressions" inside an ordinary pytest suite.

Copy this file into your own tests/ directory and point OUTPUTS at the JSONL
your pipeline logs (one record per case with output_a from the baseline and
output_b from the candidate). It needs no API key: compare_outputs makes no
model call unless use_judge=True.

Run it from the repository root:

    pytest examples/ci/test_no_regressions.py -q

v0.3.0 also ships a pytest plugin (pytest --promptdrift) that turns each case
into its own test item. This file shows the plain public-API route that works
on every version from 0.2.0 on and depends on nothing but pytest.
"""
from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from promptdrift import compare_outputs

HERE = Path(__file__).resolve().parent
OUTPUTS = HERE.parent / "offline" / "outputs.jsonl"
CHECKS = HERE.parent / "offline" / "checks.yaml"


@pytest.fixture(scope="module")
def report():
    with open(CHECKS, encoding="utf-8") as f:
        default_checks = yaml.safe_load(f)
    return compare_outputs(OUTPUTS, version_a="v1", version_b="v2", checks=default_checks)


def test_no_check_regressions(report):
    """A deterministic check that passed in v1 must still pass in v2.

    The example data contains two deliberate regressions, so this test is
    marked xfail here. In your own suite drop the marker: an empty
    ``report.regressions`` is exactly the gate you want in CI.
    """
    names = [c.case_id for c in report.regressions]
    if names == ["refund_window", "order_status_json"]:
        pytest.xfail(f"example data contains deliberate regressions: {names}")
    assert not report.regressions, f"regressions: {names}"


def test_drift_within_budget(report):
    """Average drift stays under a budget you choose. 0.5 is generous; tighten it."""
    assert report.avg_drift < 0.5, f"avg drift {report.avg_drift:.3f} is above budget"


def test_no_json_broke(report):
    """JSON that parsed in v1 must still parse in v2.

    Also xfail on the example data, which breaks one JSON output on purpose.
    """
    broken = [c.case_id for c in report.json_broken]
    if broken == ["order_status_json"]:
        pytest.xfail(f"example data breaks JSON on purpose: {broken}")
    assert not broken, f"JSON broke in: {broken}"


def test_every_case_scored(report):
    assert report.total_cases == len(report.cases) == 16
