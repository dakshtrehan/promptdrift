import pytest

from promptdrift import compare_outputs
from promptdrift.core import DiffReport
from promptdrift.reporter import print_report
from promptdrift.stats import bootstrap_ci, bootstrap_cis, percentile


def test_percentile():
    assert percentile([1, 2, 3, 4], 0.5) == 2.5
    assert percentile([5], 0.9) == 5
    with pytest.raises(ValueError):
        percentile([], 0.5)


def test_bootstrap_constant_and_reproducible():
    assert bootstrap_ci([0.3] * 10) == pytest.approx((0.3, 0.3))
    vals = [i / 20 for i in range(20)]
    a = bootstrap_ci(vals, n_boot=500, seed=1)
    assert a == bootstrap_ci(vals, n_boot=500, seed=1)
    assert a[0] < sum(vals) / len(vals) < a[1]
    assert bootstrap_ci([]) is None


def test_bootstrap_cis_paired_and_none():
    cis = bootstrap_cis({"x": [0.0, 1.0, None], "y": [None, None, None]}, n_boot=50)
    assert "y" not in cis and 0.0 <= cis["x"][0] <= cis["x"][1] <= 1.0
    with pytest.raises(ValueError, match="same length"):
        bootstrap_cis({"a": [1], "b": [1, 2]})
    with pytest.raises(ValueError, match="n_boot"):
        bootstrap_cis({"a": [1]}, n_boot=0)
    assert bootstrap_cis({}) == {}


def test_report_cis(capsys):
    rows = [{"case_id": str(i), "output_a": "alpha beta", "output_b": "alpha beta" if i % 2 else "gamma",
             "checks": {"regex": "alpha"}} for i in range(10)]
    rep = compare_outputs(rows, bootstrap=200, seed=3)
    ci = rep.confidence_intervals()
    assert ci["level"] == 0.95 and ci["n_bootstrap"] == 200
    lo, hi = ci["regression_rate"]
    assert lo <= rep.regression_rate <= hi
    assert "avg_excess_drift" not in ci
    assert rep.to_dict()["summary"]["ci"] == ci
    print_report(rep, show_cases=False)
    assert "95% CI" in capsys.readouterr().out


def test_report_cis_disabled_and_excess():
    assert compare_outputs([{"case_id": "a", "output_a": "x", "output_b": "y"}],
                           bootstrap=0).confidence_intervals() is None
    assert DiffReport("a", "b", "m", "m", 0).confidence_intervals() is None
    rep = compare_outputs([{"case_id": "a", "output_a": ["x", "x"], "output_b": ["y", "x"]}],
                          bootstrap=20)
    assert "avg_excess_drift" in rep.confidence_intervals()
