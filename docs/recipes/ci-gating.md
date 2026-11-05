# Recipe: CI gating

Turn the comparison into a pull request check: one comment that updates in
place, the HTML report as an artifact, regressions shown as native test
failures, and a red check when a threshold is breached.

## The GitHub Action

The repository root is a composite action, referenced as
`uses: dakshtrehan/promptdrift@<ref>`. Pin it to `@main`, to a tag such as
`@v0.3.0` once it exists, or to a commit SHA for a fully immutable pin.

### Offline on logged outputs (no key, no cost)

```yaml
name: promptdrift

on:
  pull_request:

permissions:
  contents: read
  pull-requests: write   # only needed for the PR comment

jobs:
  drift:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - uses: dakshtrehan/promptdrift@main
        with:
          outputs: prompts/outputs.jsonl
          checks: prompts/checks.yaml
          max-drift: "0.15"
          max-flips: "0"
```

### Live mode (calls the provider, costs money)

```yaml
      - uses: dakshtrehan/promptdrift@main
        env:
          OPENAI_API_KEY: ${{ secrets.OPENAI_API_KEY }}
        with:
          config-a: prompts/v1.yaml
          config-b: prompts/v2.yaml
          cases: prompts/golden.json
          samples: "3"
          judge: "true"
          max-regressions: "0"
```

`judge` defaults to `"false"`, so no judge call happens unless you ask; in
live mode that means `--no-judge` is passed. All inputs are strings. An empty
threshold input disables that gate; `fail-on-regression` defaults to
`"true"`.

### What the action does

1. Installs promptdrift from its own checkout (`install: action`; `pypi` and
   `skip` are the alternatives) on the Python version in `python-version`.
2. Runs `promptdrift diff` (when `outputs` is set) or `promptdrift compare`,
   always writing `report.json`, `report.html`, `report.junit.xml` and
   `report.csv` into `report-dir`.
3. Posts or updates **one** comment on the pull request. The comment carries
   a hidden marker (`comment-marker`, default `<!-- promptdrift-report -->`)
   so a re-run edits the same comment instead of adding another; the body is
   identical for identical reports. Change the marker to get separate
   comments from two jobs.
4. Writes the same Markdown to the job summary.
5. Uploads the report directory as an artifact (`artifact-name`, default
   `promptdrift-report`), with `threshold-failures.txt` added on a breach.
   The comment links to the run so the HTML report is one click away.
6. Fails the job when a threshold was breached or, with the default
   `fail-on-regression: "true"`, when any case regressed.

The comment contains the verdict line, any threshold failures, a summary
table with the 95 percent confidence intervals, a per-tag table when cases
carry tags, and a collapsible list of regressions and notable cases with
truncated A and B outputs. Model outputs are escaped before they reach the
comment, so an output cannot inject Markdown, close the collapsible block or
break a table.

### Outputs

The action exposes `verdict` (`ship`, `review`, `fail`), `avg-drift`,
`regression-rate`, `regressions`, `improvements`, `format-changes`,
`json-broken`, `flips`, `inconsistent`, `threshold-failures`, `comment-id`
and the absolute paths `report-json`, `report-html`, `report-junit`. Use them
in later steps:

```yaml
      - uses: dakshtrehan/promptdrift@main
        id: drift
        with:
          outputs: prompts/outputs.jsonl
          fail-on-regression: "false"
      - run: echo "verdict ${{ steps.drift.outputs.verdict }}, avg drift ${{ steps.drift.outputs.avg-drift }}"
```

The full input and output tables are in the [Action reference](../reference/action.md).

### Fork pull requests and other events

On pull requests from forks the default token is read only. The action logs
a warning, skips the comment and still writes the job summary and the
artifact; the job outcome is unaffected. If you need comments on fork PRs,
run the action from a `pull_request_target` workflow that checks out the base
branch, and never run untrusted code there.

On `push`, `schedule` and `workflow_dispatch` there is no pull request number,
so the comment step prints a notice and is skipped.

## Native failures with JUnit

`report.junit.xml` has one `<testcase>` per promptdrift case, a `<failure>`
for each regression and an `<error>` for a judge error on a non-regressed
case. Feed it to a test reporter to get failures annotated on the PR:

```yaml
      - uses: dakshtrehan/promptdrift@main
        id: drift
        with:
          outputs: prompts/outputs.jsonl
      - uses: dorny/test-reporter@v1
        if: always()
        with:
          name: promptdrift
          path: ${{ steps.drift.outputs.report-junit }}
          reporter: java-junit
```

The suite carries properties CI systems can read: `promptdrift_version`,
`version_a`, `version_b`, `model_a`, `model_b`, `mode`, `similarity`,
`avg_drift`, `regression_rate`, `regressions`, `improvements`, `neutral`,
`format_changes`, `verdict`, `verdict_status`, `verdict_reason`, and with
the bootstrap on, `ci_level` and the `_ci_low` / `_ci_high` bounds. The root
is `<testsuites>` wrapping one `<testsuite name="promptdrift">`, which
Jenkins, GitHub reporters and GitLab accept.

## Without the action

A plain workflow needs nothing but the CLI. `examples/ci/promptdrift.yml`
in the repository is a complete sample (it is not registered under
`.github/workflows/` there):

```yaml
      - name: Install promptdrift
        run: pip install "git+https://github.com/dakshtrehan/promptdrift.git"

      - name: Compare logged outputs (offline, no model calls)
        env:
          OPENAI_API_KEY: ""
          ANTHROPIC_API_KEY: ""
        run: |
          promptdrift diff prompts/outputs.jsonl \
            --name-a v1 --name-b v2 \
            --checks prompts/checks.yaml \
            --output promptdrift-report.json \
            --junit promptdrift-junit.xml \
            --max-regressions 0 --max-drift 0.3

      - name: Keep the report
        if: always()
        uses: actions/upload-artifact@v4
        with:
          name: promptdrift-report
          path: promptdrift-*.json
```

Blanking the keys in the step environment is a cheap guarantee that an
accidental `--judge` cannot spend money.

Inside an existing test job, the [pytest plugin](pytest.md) does the same
gate with `pytest --promptdrift outputs.jsonl --junit-xml junit.xml`.

## Thresholds in CI

See [Verdicts and exit codes](../concepts/verdicts.md) for the full table.
Short version:

| Gate | Flag | Action input |
|---|---|---|
| any regression | `--fail-on-regression` or `--max-regressions 0` | `fail-on-regression: "true"` (default) or `max-regressions: "0"` |
| average drift above 15 percent | `--max-drift 0.15` | `max-drift: "0.15"` |
| any check flip from pass to fail | `--max-flips 0` | `max-flips: "0"` |
| any format change | `--max-format-changes 0` | `max-format-changes: "0"` |

Every breach is one sentence on stderr (and in the comment), all gates are
evaluated in one run, and `report.json` can be re-gated later with
`evaluate_thresholds()` without re-running anything.

## Dogfood

This repository runs the action on itself on every pull request
(`.github/workflows/promptdrift-example.yml`) against the offline example
with blanked keys, `fail-on-regression: "false"` and a high `max-drift`,
because the example data regresses on purpose. It publishes nothing.
