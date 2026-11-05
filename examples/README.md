# promptdrift examples

Every example here runs from the repository root. Unless a section says
otherwise, an example makes **no model calls, needs no API key and costs
nothing**. The test suite (`tests/test_examples.py`) runs every fenced bash
block in this file that starts with the line `# test: offline`, so the
commands below are known to work.

| Example | What it shows | Needs a key |
|---|---|---|
| [`offline/`](offline/) | End to end run on logged outputs: a support assistant prompt edit with a regression, a broken JSON, a format change, an improvement and a multi-sample noise floor. Writes JSON, HTML, CSV and JUnit reports. | No |
| [`python_api.py`](python_api.py) | `compare_outputs` on a list of dicts, the summary dict, and asserting on `report.regressions`. | No |
| [`live/mock_provider.py`](live/mock_provider.py) | The live `compare()` code path with a scripted provider and judge, so every live-mode field can be seen without a key. | No |
| [`live/`](live/) (`v1.yaml`, `v2.yaml`, `golden.json`) | Two real prompt configs and five test cases for `promptdrift compare`. | **Yes**, `OPENAI_API_KEY`, and it costs money |
| [`ci/promptdrift.yml`](ci/promptdrift.yml) | A GitHub Actions workflow that runs the offline example and fails on regressions. Not registered in this repository's CI. | No |
| [`ci/test_no_regressions.py`](ci/test_no_regressions.py) | Asserting "no regressions" inside a pytest suite with the public API. | No |
| [`outputs.jsonl`](outputs.jsonl) | The five-record file used by the README and the CI smoke test. | No |
| [`../benchmark/`](../benchmark/) | TF-IDF drift against sentence-transformer embeddings on 200 MRPC pairs, with honest results. | No (optional local model) |

## Offline: compare logged outputs

The quickest look at what promptdrift reports:

```bash
# test: offline
promptdrift diff examples/outputs.jsonl --name-a v1 --name-b v2 --summary
```

The realistic scenario, 16 cases of a support assistant with tags, checks and
a multi-sample record:

```bash
# test: offline
promptdrift diff examples/offline/outputs.jsonl --name-a v1 --name-b v2 --checks examples/offline/checks.yaml
```

The whole pipeline with every export. It exits 1 on purpose, because the data
has two regressions, which is what you want from a CI step:

```bash
# test: offline, exit 1
bash examples/offline/run.sh
```

Reports land in `examples/offline/out/` (set `PROMPTDRIFT_OUT` to change
that). `examples/offline/expected_output.txt` holds the terminal report of a
real run. See [`offline/README.md`](offline/README.md) for what each case
demonstrates.

## Python API

```bash
# test: offline
python examples/python_api.py
```

Builds records in code, prints `report.to_dict()["summary"]`, then asserts
`not report.regressions` the way a test would.

## Live mode without a key

```bash
# test: offline
python examples/live/mock_provider.py
```

Runs `compare()` with a scripted provider and a scripted judge. The judge is
injected as `judge_provider=` (any object with `complete` works). The
generation providers are swapped with `unittest.mock.patch.object` on the
`promptdrift.compare` module's `get_provider`, which is also how a test suite
would do it.

## Live mode with a real model (costs money)

```bash
export OPENAI_API_KEY=sk-...
promptdrift compare examples/live/v1.yaml examples/live/v2.yaml --cases examples/live/golden.json
```

Five cases, so at most 10 generation calls and 10 judge calls to
`gpt-4o-mini`. Add `--no-judge` to halve that, `--samples 3` to measure the
noise floor at three times the generation calls. Details in
[`live/README.md`](live/README.md). Nothing in this repository's tests runs
these configs against a real provider.

## CI

`ci/promptdrift.yml` is a complete workflow for your own repository: it
installs promptdrift from GitHub, runs the offline comparison with
`--fail-on-regression` and keeps the JSON report as an artifact. It is a
sample, not registered under `.github/workflows/` here.

`ci/test_no_regressions.py` shows the same gate as pytest tests:

```bash
# test: offline
python -m pytest examples/ci/test_no_regressions.py -q -p no:cacheprovider
```

Two of its tests are `xfail` on the example data because the data contains
deliberate regressions; in your own suite you would drop the `xfail` and let
them fail. v0.3.0 also ships a pytest plugin (`pytest --promptdrift`) that
turns each case into its own test item; the file works without it.

## Benchmark

```bash
# test: offline
python benchmark/run_benchmark.py --no-embeddings --limit 20 --no-write
```

The full run (`python benchmark/run_benchmark.py`) rewrites
`benchmark/RESULTS.md` and adds the embedding leg when sentence-transformers
and the model are available locally. See [`../benchmark/README.md`](../benchmark/README.md).
