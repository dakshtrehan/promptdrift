# CLI reference

Every block below is the real `--help` output of the installed command,
captured when the docs were built; `tests/test_docs.py` checks that every
flag of `compare` and `diff` appears on this page.

```text
promptdrift compare A.yaml B.yaml --cases FILE [options]
promptdrift diff OUTPUTS.jsonl [--name-a A] [--name-b B] [--judge] [options]
promptdrift watch [--interval S] [--clear] [--once] <compare|diff> [arguments...]
promptdrift init [--dir ./prompts]
promptdrift --version
```

## Exit codes

| Code | Meaning |
|---|---|
| 0 | the run finished and every gate passed (or none was set) |
| 1 | a gate failed (`--fail-on-regression`, `--max-*`), or the run raised an error such as a missing file, a bad check spec or no provider for a live run |
| 2 | argument error |

Each breached gate prints one sentence to stderr, for example
`avg drift 17.9% exceeds max-drift 10.0%`; `--fail-on-regression` alone
exits 1 silently, as in v0.2.0. See [Verdicts and exit codes](../concepts/verdicts.md).

## `promptdrift`

```text
usage: promptdrift [-h] [--version] {compare,diff,watch,init} ...

⚡ Semantic diff for LLM prompts. Catch regressions before prod.

positional arguments:
  {compare,diff,watch,init}
    compare             Compare two prompt versions
    diff                Compare stored outputs offline (no model calls)
    watch               Re-run compare or diff whenever an input file changes
    init                Scaffold example files

options:
  -h, --help            show this help message and exit
  --version             show program's version number and exit

examples:
  promptdrift compare v1.yaml v2.yaml --cases golden.json
  promptdrift compare v1.yaml v2.yaml --cases golden.json --no-judge
  promptdrift compare v1.yaml v2.yaml --cases golden.json --fail-on-regression
  promptdrift compare v1.yaml v2.yaml --cases golden.json --json -o report.json
  promptdrift diff outputs.jsonl --name-a old --name-b new
  promptdrift diff outputs.jsonl --html report.html --csv cases.csv --junit junit.xml
  promptdrift watch --clear diff outputs.jsonl --summary
  promptdrift init --dir ./prompts
```

## `promptdrift compare`

Live mode: runs both prompt configs against the cases, calling the provider
named in each config (`2 x samples x cases` generation calls) and, unless
`--no-judge` is given, the judge (up to `2 x cases` calls). Needs the
provider SDK and key. See [Quickstart](../quickstart.md#2-live-compare-with-keys).

```text
usage: promptdrift compare [-h] --cases CASES [--no-judge]
                           [--judge-provider JUDGE_PROVIDER]
                           [--judge-model JUDGE_MODEL] [--judge-single-order]
                           [--similarity {tfidf,embedding}]
                           [--samples SAMPLES] [--bootstrap N] [--seed SEED]
                           [--checks FILE] [--json] [--output FILE]
                           [--html FILE] [--csv FILE] [--junit FILE]
                           [--summary] [--show-outputs] [--fail-on-regression]
                           [--max-drift X] [--max-regressions N]
                           [--max-format-changes N] [--max-flips N] [--quiet]
                           [--verbose]
                           config_a config_b

positional arguments:
  config_a              Baseline prompt config (YAML/JSON)
  config_b              New prompt config (YAML/JSON)

options:
  -h, --help            show this help message and exit
  --cases CASES         Test cases file (JSON/JSONL/YAML)
  --no-judge            Skip LLM-as-judge (similarity, structure and checks
                        only)
  --judge-provider JUDGE_PROVIDER
                        openai | anthropic | ollama (default: from
                        OPENAI_API_KEY, else ANTHROPIC_API_KEY)
  --judge-model JUDGE_MODEL
                        Judge model (default depends on the provider)
  --judge-single-order  Judge A-vs-B only (v0.1.1 behaviour) instead of both
                        orders
  --similarity {tfidf,embedding}
                        tfidf (default, free, offline) or embedding
                        (OpenAI/Ollama, opt-in)
  --samples, -n SAMPLES
                        Samples per side per case; >1 reports noise floor and
                        excess drift
  --bootstrap N         Bootstrap resamples for 95% CIs (default 1000, 0
                        disables)
  --seed SEED           Random seed for the bootstrap
  --checks FILE         Default deterministic checks (JSON/YAML) for cases
                        without their own
  --json                Output raw JSON
  --output, -o FILE     Save report to JSON file
  --html FILE           Save a self-contained HTML report (no external
                        requests)
  --csv FILE            Save one row per case as CSV
  --junit FILE          Save JUnit XML (one testcase per case, failures =
                        regressions)
  --summary             Summary only, no per-case details
  --show-outputs        Show truncated A/B outputs per case
  --fail-on-regression  Exit 1 if regressions found (CI)
  --max-drift X         Exit 1 when average drift exceeds X (fraction, e.g.
                        0.1 for 10%)
  --max-regressions N   Exit 1 when more than N cases regressed
  --max-format-changes N
                        Exit 1 when more than N cases changed output format
  --max-flips N         Exit 1 when more than N checks flipped from pass to
                        fail
  --quiet, -q           Suppress progress output
  --verbose, -v         Verbose error traces
```

Notes:

- `--similarity embedding` uses the embedding provider of config A (OpenAI,
  or Ollama for Ollama configs) and falls back to TF-IDF with a message when
  none can be built. Setting `embedding_model` in a config has the same effect.
- `--judge-provider` and `--judge-model` default to the environment:
  OpenAI (`gpt-4o-mini`) when `OPENAI_API_KEY` is set, otherwise Anthropic
  (`claude-haiku-4-5-20251001`) when `ANTHROPIC_API_KEY` is set.
- `--bootstrap 0` turns the confidence intervals off.
- `--output`, `--html`, `--csv` and `--junit` can be combined in one run.
- `--max-drift` is a fraction (0.1 is 10 percent); the other limits are
  inclusive counts.

## `promptdrift diff`

Offline mode: scores stored outputs and never calls a model unless `--judge`
is given. See [Offline logs](../recipes/offline-logs.md).

```text
usage: promptdrift diff [-h] [--name-a NAME_A] [--name-b NAME_B] [--judge]
                        [--judge-provider JUDGE_PROVIDER]
                        [--judge-model JUDGE_MODEL] [--judge-single-order]
                        [--bootstrap N] [--seed SEED] [--checks FILE] [--json]
                        [--output FILE] [--html FILE] [--csv FILE]
                        [--junit FILE] [--summary] [--show-outputs]
                        [--fail-on-regression] [--max-drift X]
                        [--max-regressions N] [--max-format-changes N]
                        [--max-flips N] [--verbose]
                        outputs

positional arguments:
  outputs               JSONL (or JSON array) of {case_id, output_a, output_b}

options:
  -h, --help            show this help message and exit
  --name-a NAME_A       Label for side A (default: A)
  --name-b NAME_B       Label for side B (default: B)
  --judge               Also run the LLM judge (calls a model; off by default)
  --judge-provider JUDGE_PROVIDER
                        openai | anthropic | ollama (default: from
                        OPENAI_API_KEY, else ANTHROPIC_API_KEY)
  --judge-model JUDGE_MODEL
                        Judge model (default depends on the provider)
  --judge-single-order  Judge A-vs-B only (v0.1.1 behaviour) instead of both
                        orders
  --bootstrap N         Bootstrap resamples for 95% CIs (default 1000, 0
                        disables)
  --seed SEED           Random seed for the bootstrap
  --checks FILE         Default deterministic checks (JSON/YAML) for cases
                        without their own
  --json                Output raw JSON
  --output, -o FILE     Save report to JSON file
  --html FILE           Save a self-contained HTML report (no external
                        requests)
  --csv FILE            Save one row per case as CSV
  --junit FILE          Save JUnit XML (one testcase per case, failures =
                        regressions)
  --summary             Summary only, no per-case details
  --show-outputs        Show truncated A/B outputs per case
  --fail-on-regression  Exit 1 if regressions found (CI)
  --max-drift X         Exit 1 when average drift exceeds X (fraction, e.g.
                        0.1 for 10%)
  --max-regressions N   Exit 1 when more than N cases regressed
  --max-format-changes N
                        Exit 1 when more than N cases changed output format
  --max-flips N         Exit 1 when more than N checks flipped from pass to
                        fail
  --verbose, -v         Show per-case progress
```

Notes:

- `--name-a` and `--name-b` only label the sides; the default labels are
  `A` and `B`.
- `--judge` opts in to the LLM judge and is the only way this subcommand
  spends money. `--judge-provider`, `--judge-model` and
  `--judge-single-order` are then honoured as for `compare`.
- Records may carry lists in `output_a` / `output_b` for the noise floor; there
  is no `--samples` flag here because the samples are in the file.

## `promptdrift watch`

Re-runs `compare` or `diff` whenever an input file changes. The watch flags
come before the command name; everything after it is passed to that command
unchanged. See [Watch mode](../recipes/watch.md).

```text
usage: promptdrift watch [-h] [--interval SECONDS] [--clear] [--once]
                         {compare,diff} ...

positional arguments:
  {compare,diff}      Command to re-run
  args                Arguments for the wrapped command

options:
  -h, --help          show this help message and exit
  --interval SECONDS  Polling interval in seconds (default 1.0)
  --clear             Clear the terminal before each run
  --once              Run once and exit
```

Watched inputs are the config, cases and checks files for `compare` and the
outputs and checks files for `diff`; output files are never watched.
Regressions do not stop the loop, Ctrl-C exits 0, and `--once` exits with the
wrapped command's code. A cost warning is printed before the first run of any
`compare` and of `diff --judge`.

## `promptdrift init`

Scaffolds a starter project: `v1.yaml`, `v2.yaml` and `golden.json`
(four cases) in the given directory, and prints the `compare` command to run
next.

```text
usage: promptdrift init [-h] [--dir DIR]

options:
  -h, --help  show this help message and exit
  --dir DIR   Output directory (default: ./prompts)
```

## `promptdrift --version`

Prints `promptdrift <version>`; the value comes from `promptdrift.__version__`.

## pytest options

The [pytest plugin](../recipes/pytest.md) adds its own options to `pytest --help`
once promptdrift is installed:

```text
promptdrift: LLM output regression gate:
  --promptdrift=PATH    JSONL/JSON outputs file to score offline (repeatable);
                        adds one test per case plus a summary test per file
  --promptdrift-max-drift=FLOAT
                        fail the summary test when average drift exceeds this
                        (0.0 to 1.0)
  --promptdrift-max-regressions=INT
                        fail the summary test when regressions exceed this
                        (default 0)
  --promptdrift-checks=FILE
                        default deterministic checks (JSON/YAML) for cases
                        without their own
  --promptdrift-json=PATH
                        save the promptdrift JSON report here
  --promptdrift-html=PATH
                        save the self-contained HTML report here
```
