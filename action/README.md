# promptdrift GitHub Action

A composite action that runs promptdrift on a pull request, posts (or updates)
one summary comment with drift, check flips, format changes and confidence
intervals, writes the same summary to the job summary, uploads the HTML, JSON,
JUnit and CSV reports as an artifact, and fails the job on configurable
thresholds.

The action lives at the root of this repository (`action.yml`) with its helper
script in `action/summarize.py`, so it is referenced as
`uses: dakshtrehan/promptdrift@<ref>`.

## Minimal usage

Offline mode on logged outputs. No API key, no model call, no cost.

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

Live mode, comparing two prompt configs against golden cases (this calls the
model provider named in the configs and costs money):

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

## Pinning

* `uses: dakshtrehan/promptdrift@v0.3.0` once that tag exists (recommended for
  reproducible runs),
* `uses: dakshtrehan/promptdrift@main` to follow the default branch,
* `uses: dakshtrehan/promptdrift@<commit sha>` for a fully immutable pin.

Inside this repository the dogfood workflow uses `uses: ./`.

## Inputs

All inputs are strings. Unset threshold inputs disable that gate.

| Input | Default | Meaning |
| --- | --- | --- |
| `outputs` | `""` | JSONL (or JSON array) of logged outputs with `case_id`, `output_a`, `output_b`. When set the action runs `promptdrift diff` offline and calls no model. |
| `config-a` | `""` | Baseline prompt config (live mode, used only when `outputs` is empty). |
| `config-b` | `""` | New prompt config (live mode). |
| `cases` | `""` | Test cases file (live mode). |
| `checks` | `""` | Default deterministic checks file (JSON/YAML). |
| `samples` | `"1"` | Samples per side per case in live mode. |
| `judge` | `"false"` | Run the LLM judge. Offline mode never calls a model unless this is `"true"`; live mode passes `--no-judge` when it is `"false"`. |
| `judge-provider` | `""` | `openai`, `anthropic` or `ollama`. |
| `judge-model` | `""` | Judge model name. |
| `similarity` | `"tfidf"` | `tfidf` or `embedding` (live mode). |
| `max-drift` | `""` | Fail when average drift exceeds this fraction (`0.1` is 10 percent). |
| `max-regressions` | `""` | Fail when more than this many cases regressed. |
| `max-format-changes` | `""` | Fail when more than this many cases changed output format. |
| `max-flips` | `""` | Fail when more than this many checks flipped from pass to fail. |
| `fail-on-regression` | `"true"` | Fail the job when any case regressed. |
| `comment` | `"true"` | Post or update one PR comment. |
| `comment-marker` | `<!-- promptdrift-report -->` | Hidden marker used to find the action's own comment. Change it to post separate comments from two jobs. |
| `step-summary` | `"true"` | Write the Markdown to the job summary. |
| `artifact-name` | `"promptdrift-report"` | Name of the uploaded artifact. |
| `upload-artifact` | `"true"` | Upload the report directory. |
| `report-dir` | `"promptdrift-report"` | Directory (relative to `working-directory`) for `report.json`, `report.html`, `report.junit.xml`, `report.csv` and, on a breach, `threshold-failures.txt`. |
| `python-version` | `"3.12"` | Python for `actions/setup-python`. |
| `github-token` | `${{ github.token }}` | Token for the comment. Needs `pull-requests: write`. |
| `working-directory` | `"."` | Directory to run in. |
| `extra-args` | `""` | Extra CLI arguments, split on whitespace (no quoting). For example `--name-a v1 --name-b v2` or `--bootstrap 2000`. |
| `install` | `"action"` | `action` installs promptdrift from the action's own checkout, `pypi` runs `pip install promptdrift`, `skip` uses an existing install. |

## Outputs

| Output | Meaning |
| --- | --- |
| `verdict` | `ship`, `review` or `fail`. `fail` whenever a threshold was breached, promptdrift exited non zero, or the report verdict is DO NOT SHIP (any regression). |
| `avg-drift` | Average drift as a fraction. |
| `regression-rate` | Share of cases that regressed. |
| `regressions`, `improvements` | Case counts. |
| `format-changes`, `json-broken` | Structural counts. |
| `flips` | Checks that flipped from pass to fail. |
| `inconsistent` | Order-inconsistent judge verdicts. |
| `report-json`, `report-html`, `report-junit` | Absolute paths of the report files (empty when missing). |
| `threshold-failures` | Number of breached thresholds. |
| `comment-id` | Id of the created or updated PR comment, empty when none was posted. |

## Permissions

```yaml
permissions:
  contents: read
  pull-requests: write
```

`pull-requests: write` is only needed for the comment. On pull requests from
forks the default token is read only; the action then logs a warning, skips
the comment and still writes the job summary and the artifact. The job
outcome is unaffected. If you need comments on fork PRs, run the action from
a `pull_request_target` workflow that checks out the base branch, and never
run untrusted code there.

On `push`, `schedule` or `workflow_dispatch` events there is no pull request
number, so the comment step prints a notice and is skipped.

## What the comment contains

A verdict line (SAFE TO SHIP, REVIEW or DO NOT SHIP, the same rule as the
terminal report), any threshold failures, a summary table (average drift and
regression rate with their 95 percent bootstrap confidence intervals,
regressions, improvements, neutral, format changes, JSON broken, check flips,
judge inconsistency and stability when present), a per tag table when cases
carry tags, a collapsible list of regressions and notable cases with
truncated A and B outputs, and a link to the workflow run where the artifact
with the full HTML report can be downloaded.

Model outputs are escaped before they reach the comment, so an output cannot
inject Markdown or HTML, close the collapsible block or break a table. The
comment body is identical for identical reports, so re-runs do not churn the
comment history.

## JUnit for native CI failures

`report.junit.xml` has one test case per promptdrift case; regressions are
failures. Feed it to a test reporter to get failures shown natively, for
example with `dorny/test-reporter` or `mikepenz/action-junit-report`, using
the `report-junit` output as the path.

## Testing the action locally

There is no Docker daemon in the development environment, so `act` cannot be
used. `tests/test_action.py` holds a small mocked runner: it parses
`action.yml`, substitutes `${{ inputs.* }}`, `${{ github.action_path }}` and
`${{ steps.*.outputs.* }}`, executes the `run` steps with bash against a fake
GitHub API served from a thread, and skips the `uses` steps. Run it with
`python -m pytest tests/test_action.py`.
