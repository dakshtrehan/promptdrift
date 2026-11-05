# GitHub Action

A composite action at the root of the repository (`action.yml`, helper script
in `action/summarize.py`), referenced as `uses: dakshtrehan/promptdrift@<ref>`.
It runs promptdrift on a pull request, posts or updates one summary comment,
writes the job summary, uploads the reports as an artifact and fails on
configurable thresholds. The recipe is in [CI gating](../recipes/ci-gating.md).

## Example workflow

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
      - uses: actions/checkout@v5

      - uses: dakshtrehan/promptdrift@main
        id: drift
        # Offline mode: no key, no model call. For live mode set config-a,
        # config-b and cases, and pass the provider key through env.
        with:
          outputs: prompts/outputs.jsonl
          checks: prompts/checks.yaml
          max-drift: "0.15"
          max-regressions: "0"
          max-flips: "0"
          extra-args: "--name-a main --name-b pr"

      - name: Show regressions as native test failures
        if: always()
        uses: dorny/test-reporter@v1
        with:
          name: promptdrift
          path: ${{ steps.drift.outputs.report-junit }}
          reporter: java-junit
```

## Pinning

| Reference | Use |
|---|---|
| `dakshtrehan/promptdrift@v0.3.0` | once that tag exists; recommended for reproducible runs |
| `dakshtrehan/promptdrift@main` | follow the default branch |
| `dakshtrehan/promptdrift@<commit sha>` | fully immutable |
| `./` | inside this repository (the dogfood workflow) |

## Inputs

All inputs are strings. An empty threshold input disables that gate.

| Input | Default | Meaning |
|---|---|---|
| `outputs` | `""` | JSONL (or JSON array) of logged outputs with `case_id`, `output_a`, `output_b`. When set the action runs `promptdrift diff` offline and calls no model |
| `config-a` | `""` | Baseline prompt config (live mode, used only when `outputs` is empty) |
| `config-b` | `""` | New prompt config (live mode) |
| `cases` | `""` | Test cases file (live mode) |
| `checks` | `""` | Default deterministic checks file (JSON or YAML) for cases without their own |
| `samples` | `"1"` | Samples per side per case in live mode; more than 1 reports a noise floor |
| `judge` | `"false"` | Run the LLM judge. `"true"`, `"yes"`, `"on"` or `"1"` (any case) turn it on; any other value, including the default `"false"`, keeps it off: offline mode then calls no model and live mode passes `--no-judge` |
| `judge-provider` | `""` | `openai`, `anthropic` or `ollama` (default from the environment) |
| `judge-model` | `""` | Judge model name (default depends on the provider) |
| `similarity` | `"tfidf"` | `tfidf` or `embedding` (live mode) |
| `max-drift` | `""` | Fail when average drift exceeds this fraction (`0.1` is 10 percent) |
| `max-regressions` | `""` | Fail when more than this many cases regressed |
| `max-format-changes` | `""` | Fail when more than this many cases changed output format |
| `max-flips` | `""` | Fail when more than this many checks flipped from pass to fail |
| `fail-on-regression` | `"true"` | Fail the job when any case regressed. Accepts `true`, `yes`, `on` or `1` in any case; anything else is off |
| `comment` | `"true"` | Post or update one PR comment |
| `comment-marker` | `<!-- promptdrift-report -->` | Hidden marker used to find the action's own comment. Change it to post separate comments from two jobs (and change `artifact-name` too). Only a comment that starts with the marker and was written by a Bot account or by the token's own user is updated |
| `step-summary` | `"true"` | Write the Markdown to the job summary |
| `artifact-name` | `"promptdrift-report"` | Name of the uploaded artifact. Must be unique within a workflow run: in a matrix or a second job set it per job, for example `promptdrift-report-${{ matrix.python-version }}`, or the second upload fails |
| `upload-artifact` | `"true"` | Upload the report directory |
| `report-dir` | `"promptdrift-report"` | Directory (relative to `working-directory`) for `report.json`, `report.html`, `report.junit.xml`, `report.csv` and, on a breach, `threshold-failures.txt` |
| `python-version` | `"3.12"` | Python version for `actions/setup-python` |
| `github-token` | `${{ github.token }}` | Token for the comment. Needs `pull-requests: write` |
| `working-directory` | `"."` | Directory to run in |
| `extra-args` | `""` | Extra CLI arguments, split on whitespace (no quoting). For example `--name-a v1 --name-b v2` or `--bootstrap 2000` |
| `install` | `"action"` | `action` installs promptdrift from the action's own checkout, `pypi` runs `pip install promptdrift` (not available until the first PyPI release), `skip` uses an existing install |

Inputs reach the shell only as environment variables and are appended to an
argument array; nothing is evaluated.

## Outputs

| Output | Meaning |
|---|---|
| `verdict` | `ship`, `review` or `fail`. `fail` whenever a threshold was breached, promptdrift exited non zero, or the report verdict is DO NOT SHIP (any regression), even when the job passes because `fail-on-regression` is `"false"` |
| `avg-drift` | Average drift as a fraction |
| `regression-rate` | Share of cases that regressed |
| `regressions`, `improvements` | Case counts |
| `format-changes`, `json-broken` | Structural counts |
| `flips` | Checks that flipped from pass to fail |
| `inconsistent` | Order-inconsistent judge verdicts |
| `report-json`, `report-html`, `report-junit` | Absolute paths of the report files (empty when missing) |
| `threshold-failures` | Number of breached thresholds |
| `comment-id` | Id of the created or updated PR comment, empty when none was posted |

## Permissions

```yaml
permissions:
  contents: read
  pull-requests: write
```

`pull-requests: write` is only needed for the comment. On pull requests from
forks the default token is read only: the action logs a warning, skips the
comment, and still writes the job summary and the artifact; the job outcome
is unaffected. Any other GitHub API failure while posting the comment (a 5xx,
a 429 rate limit, a timeout) is also only a warning, and every output is still
set. On `push`, `schedule` and `workflow_dispatch` there is no pull
request number, so the comment is skipped with a notice.

## What the comment contains

A verdict line (SAFE TO SHIP, REVIEW or DO NOT SHIP, the same rule as the
terminal report), any threshold failures, a summary table (average drift and
regression rate with their 95 percent bootstrap confidence intervals,
regressions, improvements, neutral, format changes, JSON broken, check flips,
judge inconsistency and stability when present), a per-tag table when cases
carry tags, a collapsible list of regressions and notable cases with
truncated A and B outputs, and a link to the workflow run where the artifact
with the full HTML report can be downloaded.

Model outputs are escaped before they reach the comment. The body is
identical for identical reports, so re-runs do not churn the comment history.

## Steps, in order

1. `actions/setup-python` with `python-version`.
2. Install promptdrift per `install`, then `promptdrift --version`.
3. Run `promptdrift diff` or `promptdrift compare` with the mapped flags and
   `--output`, `--html`, `--junit`, `--csv` into `report-dir`. Report files
   already in `report-dir` (from an earlier run or committed to the branch)
   are deleted first, so they can never be shown as this run's report. The
   promptdrift exit code is captured rather than failing the step.
4. `action/summarize.py` (standard library only) re-evaluates the thresholds
   from `report.json` (with a built-in copy of the rules and a warning when
   promptdrift is not importable from that Python), renders the Markdown,
   writes the job summary, sets the outputs and then upserts the comment
   through the REST API. A comment failure is only a warning.
5. `actions/upload-artifact` of `report-dir` (`if: always()`, skipped when
   the run step never got as far as creating `report-dir`).
6. Fail on thresholds: exits with promptdrift's code after printing
   `threshold-failures.txt`, or says that promptdrift did not run when the
   install or run step failed first.

The `uses:` steps target Node.js 24 (`actions/setup-python@v6`,
`actions/upload-artifact@v6`), which needs GitHub Actions runner 2.327.1 or
newer; GitHub-hosted runners qualify, old self-hosted runners must be
updated.

## Testing the action locally

There is no Docker daemon in the development environment, so `act` cannot be
used there. `tests/test_action.py` holds a small mocked runner that parses
`action.yml`, substitutes the expressions it uses, executes the `run` steps
with bash against a fake GitHub API served from a thread, and skips the
`uses` steps:

```bash
python -m pytest tests/test_action.py
```

The dogfood workflow `.github/workflows/promptdrift-example.yml` runs the
real action on the offline example in this repository's own CI.
