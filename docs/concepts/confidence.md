# Confidence intervals

A regression rate of 20 percent measured on five cases means one case. The
report says so with a confidence interval next to every headline rate.

## What is reported

```text
  Avg drift         ████░░░░░░░░░░░░░░░░░░  17.9%  95% CI 7.8 to 30.4%
  Regression rate   ███░░░░░░░░░░░░░░░░░░░  12.5%  95% CI 0.0 to 31.2%
```

```json
"ci": {"level": 0.95, "n_bootstrap": 1000,
       "avg_drift": [0.078, 0.304], "regression_rate": [0.0, 0.3125],
       "avg_excess_drift": [-0.127, -0.127]}
```

`avg_excess_drift` appears only when cases carry several samples. The HTML
report draws the intervals as range markers on the bars, the Markdown PR
comment prints them in the summary table, and the JUnit file carries them as
suite properties (`avg_drift_ci_low`, `avg_drift_ci_high`, and so on).

## How it is computed

A seeded percentile bootstrap, in pure Python:

1. resample the cases with replacement, 1000 times,
2. compute the mean drift, the regression rate and the mean excess drift on
   each resample,
3. take the 2.5th and 97.5th percentiles of each.

All three metrics share the same resampled case indices (a paired bootstrap),
so they are consistent with each other. The random generator is seeded (0 by
default), so the same input always produces the same interval, which matters
when a CI job compares two reports.

| Control | CLI | Python |
|---|---|---|
| Resamples | `--bootstrap N` (default 1000, `0` disables) | `bootstrap=N` |
| Seed | `--seed S` | `seed=S` |
| Level | | `report.confidence_intervals(level=0.9)` |

The interval is computed on demand from the cases, so it always reflects the
report as it stands.

## Limits with few cases

Be honest with yourself about what the interval can say:

- **The percentile bootstrap is too narrow for very small samples.** With
  five or ten cases the true uncertainty is larger than the printed interval.
  Treat a wide interval as a reason to add cases, not as noise to ignore.
- **It assumes cases are independent and representative.** Ten variations of
  the same question are not ten cases. The interval says nothing about inputs
  you did not include.
- **Rates near 0 or 1 give lopsided intervals.** A regression rate of 0.0
  with a 95 percent interval of 0.0 to 0.0 on five cases does not mean there
  are no regressions in the world, only that none of these five regressed.
- **A single multi-sample case gives a degenerate interval.** The example
  above has one such case, so `avg_excess_drift` is `[-0.127, -0.127]`.

Thresholds (`--max-drift`, `--max-regressions`) gate on the point estimate,
not on the interval. If you want a conservative gate, use the upper bound
from `report.confidence_intervals()` in your own script:

```python
from promptdrift import compare_outputs

report = compare_outputs("outputs.jsonl")
ci = report.confidence_intervals()
assert ci["avg_drift"][1] < 0.25, f"upper bound of avg drift is {ci['avg_drift'][1]:.3f}"
```

## Python

```python
from promptdrift.stats import bootstrap_ci, bootstrap_cis

lo, hi = bootstrap_ci([0.1, 0.4, 0.2, 0.9], n_boot=2000, seed=1)
```

See [`promptdrift.stats`](../reference/api.md#promptdrift.stats).
