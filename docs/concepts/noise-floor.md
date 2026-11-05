# Noise floor

Ask a model the same question twice and you get two different answers. If you
compare one answer from version A with one answer from version B, part of the
drift you measure is that run to run variation, not the change you made.
Multi-sample stability measures the variation so you can subtract it.

## Getting several samples

=== "Live"

    ```bash
    promptdrift compare v1.yaml v2.yaml --cases golden.json --samples 3
    ```

    Each version is called three times per case. Generation calls go from
    `2 x cases` to `2 x 3 x cases`; the judge and structure still look at the
    first sample of each side only, so judge calls do not multiply.

=== "Offline"

    Give lists instead of strings. The sides may have different counts.

    ```json
    {"case_id": "subject", "output_a": ["Re: your refund", "Refund request received", "About your refund"],
                            "output_b": ["Your refund is on its way", "Refund update", "Re: your refund"]}
    ```

=== "Python"

    ```python
    report = compare("v1.yaml", "v2.yaml", cases="golden.json", samples=3)
    ```

## What is computed

For a case with samples `a_1..a_n` and `b_1..b_m`:

| Field | Definition |
|---|---|
| `drift_score` | mean drift over all `n x m` cross-version pairs |
| `within_drift_a` | mean drift over the unordered pairs inside side A (`None` with one sample) |
| `within_drift_b` | same for side B |
| `noise_floor` | mean of the two within-version drifts (or the one available when a side has a single sample) |
| `excess_drift` | `drift_score - noise_floor` |
| `n_samples` | the larger of `n` and `m` |
| `samples_a`, `samples_b` | all samples; `output_a` / `output_b` stay the first sample so v0.1.1 fields keep their type |

`excess_drift` is the number to look at. It answers "how much more do the
versions differ from each other than each one differs from itself?"

It is **left unclipped**, so it can be negative. A negative excess drift says
the two versions are closer to each other than samples of the same version
are to each other: whatever changed is inside the noise. Clipping at zero
would bias the average upward, so promptdrift does not.

The summary reports:

```json
"stability": {"cases": 1, "samples_per_side": 3,
              "avg_within_drift_a": 0.645, "avg_within_drift_b": 0.766,
              "avg_noise_floor": 0.706, "avg_excess_drift": -0.127,
              "cases_above_noise": 0}
```

`cases_above_noise` counts cases whose excess drift exceeds 0.05, the same
boundary that separates `none` from `minor` drift. The terminal line reads
`Stability  n=3/side   noise floor 70.6% (A 64.5%, B 76.6%)   excess drift -12.7%   above noise 0/1`.

## When drift is just sampling noise

A worked example from the offline data: the case `refund_subject_line` has
three subject lines per side. Across versions the mean drift is 58 percent,
which looks alarming. Within v1 alone the samples differ by 64 percent and
within v2 by 77 percent, so the noise floor is 71 percent and the excess
drift is minus 13 percent. The prompt edit did not change this case; the
model simply never writes the same subject line twice.

Rules of thumb:

- `drift_level` is still classified from the cross-version similarity, so
  its meaning does not change with `n`. Use `excess_drift` and
  `cases_above_noise` to interpret it.
- At temperature 0 the noise floor is usually small but rarely zero;
  measure rather than assume. Models that ignore temperature (Claude 4.7 and
  later only accept default sampling, see [Provider switch](../recipes/provider-switch.md))
  have a noise floor whether you asked for one or not.
- Three samples per side is enough to see whether a case is noisy; it is not
  enough to estimate the floor precisely. The
  [confidence interval](confidence.md) on `avg_excess_drift` says how much
  to trust the average.
- Checks run on every sample and a side passes when at least half pass, so a
  flaky instruction (a placeholder leaking one time in three) shows up in
  `check_pass_rate_b` without flipping the case. See
  [Checks and flips](flips.md).

## Cost and caching

Embeddings are cached per distinct text within a case, so `n x m` pairs cost
at most `n + m` embedding calls. If the embedder fails for some pairs only,
the case's `similarity_method` becomes `mixed` so a mixed scale is visible
rather than hidden. Live mode reports the mean latency and the total tokens
across samples.
