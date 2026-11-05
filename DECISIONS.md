# Decisions log for v0.2.0

This file records the judgement calls made while building v0.2.0 unattended.
Each entry says what was decided and why, so it can be reviewed or reversed.

## D0. Audit of v0.1.1 (task 1)

Read every module, the tests, the CI workflows and the README before changing anything.
Findings that shaped the plan:

1. `pip install promptdrift` returns 404. The README advertises a PyPI badge and
   `pip install promptdrift` even though nothing has been published.
2. The research shim `silent-regressions/src/pdx.py` imports `_judge` and
   `_tfidf_similarity` from `promptdrift.compare`. Those are private names, so
   any refactor would silently break it. Its `FUTURE_PROMPTDRIFT` list matches
   tasks 2 to 8 of this release almost one to one.
3. `AnthropicProvider.complete` reads `r.content[0].text`. When a model returns a
   thinking block first, that is not a text block and the call crashes or
   returns the wrong thing.
4. The installed `anthropic` SDK (2.4.0) has no typed `temperature` argument on
   `messages.create`, and its default `anthropic-version` header is `2026-10-21`.
   v0.1.1 forwards `temperature` as a keyword, which raises `TypeError` on SDK 2.x.
5. The judge silently maps any error or unparseable reply to `neutral`. There is
   no way to tell a real neutral from a failed call.
6. `compare()` builds an OpenAI embedder whenever `OPENAI_API_KEY` is set, so the
   same command gives different scores (and costs money) depending on the
   environment. An embedding failure mid-run silently falls back to TF-IDF for
   that case only, mixing two incomparable similarity scales in one report.
7. `prompt_template.format(input=...)` crashes on templates that contain literal
   braces, for example a JSON example in the prompt.
8. The reporter hardcodes `v0.1.1`. The CI lint step ends in `|| true`, so lint
   never fails. CI does not test Python 3.13.
9. Coverage was 51 percent; `cli.py` had none.

## D1. Backward compatibility policy

Every public name in v0.1.1 keeps working: `compare`, `load_config`, `load_cases`,
`DiffReport`, `CaseResult`, `print_report`, `save_report`, `print_json`,
`cosine_similarity`, `classify_drift`, `get_provider` and the provider classes.
New `CaseResult` and `DiffReport` fields are appended with defaults, so positional
and keyword construction from v0.1.1 code still works. The private names
`compare._tfidf_similarity`, `compare._judge`, `compare._embed_pair` and
`compare._resolve_embedder` are kept as thin aliases so `pdx.py` keeps running,
but new code (inside the package and in the research repo) should use the
public replacements listed in the README.

## D2. Offline mode (task 2)

* New public API `compare_outputs(path_or_records, ...)` and `load_outputs(path)`
  in `promptdrift.offline`, exported from the package root. New CLI subcommand
  `promptdrift diff outputs.jsonl`. A separate subcommand was chosen over a flag on
  `compare` because `compare` requires two config files that make no sense offline.
* Records need `case_id` (alias `id`), `output_a`, `output_b`; `input` and
  `expected` are optional. A JSON array is accepted as well as JSONL. A `null`
  output is read as the empty string (a model can return nothing); any other
  non-string is an error with the record number.
* The judge is off by default in offline mode, because the point of the mode is
  that no model is called. `--judge` opts in.
* Scoring moved into one shared function, `scoring.score_case`, used by both
  `compare` and `compare_outputs`, so live and offline results cannot drift apart.
* Each `CaseResult` now records `similarity_method` (`tfidf` or `embedding`) and
  `DiffReport` records `mode` (`live` or `offline`).
* `render_prompt` fills `{input}` with `str.format` as before and falls back to
  plain replacement when a template contains other braces (v0.1.1 crashed).

## D3. Structural drift (task 3)

* New module `promptdrift.structure` with `format_signature`, `signature_string`,
  `is_valid_json`, `length_ratio` and `structural_diff`. Pure Python, no model calls.
* The signature records markdown fence, JSON type, bullet/numbered list, markdown
  headings and non-blank line count. In the comparable string form the line
  count is bucketed (1, 2 to 4, 5 or more) so one extra sentence is not reported
  as a format change; the exact count stays available from `format_signature`.
* JSON validity is checked after removing one surrounding code fence, so
  "JSON gained a fence" shows up as a format change but not as a validity change.
  That matches what most production parsers that strip fences would see.
* Length ratio is `len(B) / len(A)` in characters after trimming. It is `None`
  when A is empty and B is not (undefined), and 1.0 when both are empty. The
  summary reports the median rather than the mean, because ratios are skewed and
  one huge outlier would dominate a mean.
* Summary adds `format_changes`, `format_change_rate`, `json_validity_changes`,
  `json_broken` (valid in A, invalid in B) and `median_length_ratio`. Structural
  changes are reported, not counted as regressions: a format change can be
  intended, and the user can make it a hard failure with a check (task 4).

## D4. Deterministic checks (task 4)

* New module `promptdrift.checks` with three check types: `regex` (`re.search`,
  optional flags `i`, `m`, `s`), `json_keys` (output parses as a JSON object,
  one code fence allowed, and has every listed top-level key) and `exact`
  (whitespace-trimmed by default, `ignore_case` optional). `exact` with no value
  uses the case's `expected`, so existing golden files can opt in with
  `"checks": {"exact": true}`.
* Checks are given per case under `checks` (list or shorthand dict). A default
  spec can be passed with `checks=` in Python or `--checks FILE` on the CLI and
  applies to cases that have none. Specs are validated up front; a bad regex is a
  `ValueError`, not a silent pass.
* A case passes when every check passes. A pass in A that fails in B
  (`pass_to_fail`) is counted as a regression and sets `drift_level` to
  `regression`, because a deterministic failure is the most trustworthy
  regression signal the tool has. A `fail_to_pass` counts as an improvement
  unless something else made the case a regression. With no checks declared,
  behaviour is exactly as in v0.1.1.
* `expected` alone still does not trigger exact matching, keeping v0.1.1
  semantics where `expected` is judge context only.
* Summary adds `checks`: cases with checks, pass rate A and B, `pass_to_fail` and
  `fail_to_pass` counts (or `null` when no case has checks).

## D5. Multi-sample stability (task 5)

* `compare(..., samples=n)` and `--samples N` call each version `n` times per case.
  In offline mode, `output_a` / `output_b` may be lists of samples. Sides may
  have different sample counts.
* Drift with samples is the mean drift over all A x B pairs. Within-version drift
  is the mean over unordered pairs inside one side. The noise floor is the mean
  of the two within-version drifts (or the one available when a side has a
  single sample). `excess_drift = drift - noise_floor`.
* `excess_drift` is reported unclipped, so it can be negative. Clipping at zero
  would bias the average upward; a negative value honestly says "the versions
  differ less than each one differs from itself".
* The drift level (none/minor/...) is still classified from the cross-version
  similarity, so the meaning of `drift_level` does not change with `n`. The
  summary counts `cases_above_noise` as cases with excess drift above 0.05, the
  same boundary that separates "none" from "minor".
* The per-case `output_a` / `output_b`, structural drift and the judge use the
  first sample of each side, which keeps the v0.1.1 fields as plain strings. All
  samples are kept in `samples_a` / `samples_b`.
* Checks run on every sample; a side passes when at least half its samples pass,
  and `check_pass_rate_a/b` records the exact share.
* Embeddings are cached per distinct text within a case, so n x n pairs cost at
  most 2n embedding calls. If an embedder fails for some pairs only, the case's
  `similarity_method` is `mixed`, so mixed scales are visible instead of hidden.
* Live mode reports mean latency and total tokens across samples.

## D6. Bootstrap confidence intervals (task 6)

* New module `promptdrift.stats` (pure Python, `random.Random(seed)`, no numpy).
  Percentile bootstrap of the mean, resampling cases with replacement.
* `DiffReport.confidence_intervals()` gives 95 percent CIs for `avg_drift` and
  `regression_rate`, plus `avg_excess_drift` when samples were used. All metrics
  share the same resampled case indices (paired bootstrap), so they are
  consistent with each other.
* Defaults: 1000 resamples, seed 0. Deterministic by default so the same input
  always gives the same report (important for CI diffs). Configurable with
  `bootstrap=` / `seed=` and `--bootstrap N` / `--seed`; 0 disables. The CI is
  computed on demand from the cases, so it always reflects the current cases.
* The CI is shown next to the headline numbers in the terminal report and under
  `summary.ci` in JSON. With very few cases the percentile bootstrap is known to
  be too narrow; the README says so rather than hiding it.

## D7. Provider-agnostic judge, both orders (task 7)

* The judge lives in the public module `promptdrift.judge`: `judge_pair`,
  `judge_both_orders`, `parse_verdict`, `build_judge_prompt`, `resolve_judge`,
  `default_judge_provider`. `compare`, `compare_outputs` and the CLI all go
  through `resolve_judge`, and no module imports another module's private names
  any more (`offline` used to import from `compare`; the shim aliases in
  `compare` exist only for external v0.1.1 callers).
* `judge_provider` accepts a provider name or any object with `complete`, so a
  custom or mocked provider can be injected. Its default changed from
  `"openai"` to `None` (auto): OpenAI when `OPENAI_API_KEY` is set (same as
  v0.1.1 for everyone who had a key), otherwise Anthropic when only
  `ANTHROPIC_API_KEY` is set, otherwise no judge with a clear message.
  `judge_model` default changed from `"gpt-4o-mini"` to `None`, meaning the
  per-provider default in `DEFAULT_JUDGE_MODELS` (OpenAI `gpt-4o-mini`,
  Anthropic `claude-haiku-4-5-20251001`, Ollama `llama3.2`). Haiku 4.5 was
  chosen as the Anthropic default because it is cheap and still accepts
  `temperature`, so judge calls stay as deterministic as the API allows.
* The judge runs in both orders by default (A then B, B then A). The second
  verdict is flipped back into A-vs-B terms. Agreement gives that verdict;
  disagreement gives the new verdict `inconsistent`; a failed or unparseable
  order gives `error`. Neither new value counts as a regression or an
  improvement, because the evidence is not reliable; they are counted in
  `summary.judge` (`inconsistent`, `inconsistency_rate`, `errors`) and shown in
  the report. This doubles judge calls; `judge_both_orders=False` /
  `--judge-single-order` restores the v0.1.1 single call, where failures still
  map to `neutral` for compatibility.
* `parse_verdict` now rejects verdicts outside improved/regressed/neutral
  instead of passing through whatever string the model wrote.

## D8. Current Anthropic API behaviour (task 8)

* Verified against the installed SDK, not assumed: `anthropic` 2.4.0 has no typed
  `temperature`, `top_p` or `top_k` on `messages.create`, and its default
  `anthropic-version` header is `2026-10-21`, which rejects sampling parameters.
  v0.1.1 passed `temperature` as a keyword and so failed on every call.
* `claude_version(model)` parses Claude ids (new style `claude-opus-4-7`, dated
  `claude-haiku-4-5-20251001`, `claude-sonnet-4-20250514`, old style
  `claude-3-5-sonnet-...`, and Bedrock/Vertex forms such as `anthropic.` prefixes
  or `@date`). `anthropic_supports_temperature` is true below 4.7.
* When the model supports sampling and the caller passed a value, the provider
  sends it in `extra_body` and sets `anthropic-version: 2023-06-01`. Otherwise
  sampling parameters are dropped silently, because Claude 4.7+ only accepts
  default sampling and the config default `temperature: 0.0` must not break
  those models. Unrecognised model ids are treated as not supporting sampling,
  since omitting it is accepted everywhere. `provider_options:
  {temperature_supported: true|false}` in a config overrides the detection, for
  proxies or ids the parser does not know.
* Response text is the concatenation of every `text` block, so `thinking` and
  `redacted_thinking` blocks are skipped wherever they appear. Missing `usage`
  reads as zero tokens.
* An empty system prompt is omitted rather than sent as `""`.
* A test drives the real installed SDK through a mock transport (SDK 2.x uses
  `httpx2`, 1.x uses `httpx`; the test picks whichever exists) and asserts the
  exact header and JSON body on the wire. No request leaves the machine.
* Related fix: the Ollama provider ignored `temperature` and `max_tokens`. It now
  sends them as `options.temperature` and `options.num_predict`.
* New config field `provider_options` forwards extra keyword arguments to the
  provider constructor.

## D9. TF-IDF stays the default similarity (task 9)

* Default similarity is TF-IDF for both `compare` and `compare_outputs`. In
  v0.1.1, `compare` silently used paid OpenAI embeddings whenever
  `OPENAI_API_KEY` happened to be set, so the same command gave different numbers
  (and cost money) depending on the environment. That was changed.
* Embeddings are opt-in: `similarity="embedding"` / `--similarity embedding`, or
  setting `embedding_model` in either config (that field only ever meant "use
  embeddings", so honouring it keeps v0.1.1 configs working as their authors
  intended). Offline mode takes an `embedder=` object.
* If embeddings are requested but no embedder can be built, the run uses TF-IDF
  and says so. The report records the requested backend in `similarity`, and
  each case records the method that actually ran (`tfidf`, `embedding` or
  `mixed`).
* This is a behaviour change for users who had `OPENAI_API_KEY` set and no
  `embedding_model`: their drift numbers move from the embedding scale to the
  TF-IDF scale. It is listed under "Changed" in the CHANGELOG.
* Added `--version` and a single-source `promptdrift/version.py`; the reporter no
  longer hardcodes the version and labels offline reports.

## D10. Tests, CI, docs and version (task 10)

* Coverage went from 51 to 99 percent (146 tests). Every provider is mocked;
  the Anthropic wire test uses the real SDK with a mock HTTP transport. SDK 2.x
  uses the `httpx2` package and older SDKs use `httpx`, so the test picks
  whichever exists. On Python 3.9 pip resolves an older `anthropic` (0.x) whose
  default header is already `2023-06-01`, so that test asserts "left at the
  SDK's default" rather than "not 2023-06-01".
* Verified locally on Python 3.9, 3.10, 3.11, 3.12 and 3.13 (uv-managed venvs),
  and in a core-only venv with no provider SDK installed.
* CI: test matrix for 3.9 to 3.13 with lint that can fail and
  `--cov-fail-under=90`; a core-only job proving the zero-dependency path; a
  package job that builds, runs `twine check --strict` and runs the installed
  wheel. API key env vars are blanked in CI so nothing can call out.
* Ruff rules are pinned (`E`, `F`, `W`, `I`, `B`) because newer ruff releases
  widened the default set; tests are exempt from line length only.
* JSON detection for structural drift counts only objects and arrays. Found while
  writing the README example: a bare `30` parsed as JSON, so rewording a numeric
  answer was reported as "JSON broken". Scalars are now plain text.
* The terminal verdict no longer says SAFE TO SHIP when formats changed or the
  judge was order-inconsistent, even if drift is under 5 percent.
* README: removed the PyPI badge and `pip install promptdrift` (the package is
  not published), documented install from GitHub, documented every new feature
  with its limits, and replaced the sample output with real output from
  `examples/outputs.jsonl`. Removed the comparison table against PromptFoo,
  LangSmith and Ragas because its claims were not verified and some are likely
  wrong. Every code snippet in the README was executed.
* Version is single-sourced in `promptdrift/version.py` and read by setuptools
  (`dynamic = ["version"]`). Build backend raised to setuptools>=77 to use the
  SPDX `license = "MIT"` field; the deprecated license classifier was removed
  because setuptools rejects having both.

## D11. Build and release (task 11)

* Built sdist and wheel with `python -m build`; `twine check --strict` passes for
  both. The wheel was installed into a clean venv and run outside the repo; the
  unpacked sdist's own test suite passes.
* Added `MANIFEST.in` because the sdist shipped `tests/test_*.py` without
  `tests/conftest.py` and `tests/__init__.py`, so tests from the sdist failed
  to import. It also ships `CHANGELOG.md` and `examples/`.
* `dist/` stays git-ignored; RELEASE.md rebuilds from a clean `main` so the
  uploaded files match the merged code exactly.
* Nothing was uploaded. Checked (read-only GET) that `promptdrift`,
  `prompt-drift` and `prompt_drift` are unclaimed on PyPI and TestPyPI.
* The existing `publish.yml` (trusted publishing on GitHub Release) was left in
  place. RELEASE.md explains that it conflicts with a manual token upload of the
  same version if a GitHub Release is also published, and how to choose one route.

# Decisions log for v0.3.0

v0.3.0 was built unattended on top of the v0.2.0 branch, with the work split
into six workstreams run by separate agents and then an adversarial review pass.
Each workstream appends its own section below. Entries made before the
workstreams started are numbered D12 onwards.

## D12. Branching and orchestration

* The branch starts from the open v0.2.0 PR branch (`claude/adoring-keller-fuc8cc`)
  rather than `main`, so v0.3.0 builds on the tested v0.2.0 code and the PR will
  show only the v0.3.0 delta once v0.2.0 merges.
* Workstreams were ordered by dependency: HTML report and exports (B) and
  examples/benchmark (E) first, because the GitHub Action (A) needs the HTML,
  JUnit and Markdown outputs; then watch mode and the pytest plugin (C) with the
  Action (A); then documentation (D) and hardening (F), which must describe the
  finished features. A final adversarial review fans out over the integrated result.
* The environment has no Docker daemon, so `act` cannot run. Action tests use a
  small mocked runner that executes the composite steps with a fake GitHub API.
* Hard rules carried into every workstream: every paid model call is mocked,
  nothing is published or deployed outside this repo's own CI, v0.2.0 public
  names and behaviour stay unchanged, and docs contain no em dashes.

## D13. Interface contracts agreed before implementation

So that parallel work fits together, these names were fixed in advance:

* `promptdrift.html_report`: `render_html(report) -> str`, `save_html(report, path)`.
  Self-contained file, inline CSS and JS, no external requests.
* `promptdrift.export`: `to_csv(report) -> str`, `save_csv(report, path)`,
  `to_junit(report) -> str`, `save_junit(report, path)`.
* `promptdrift.markdown`: `render_markdown(report, ...) -> str` for PR comments
  and `GITHUB_STEP_SUMMARY`.
* `promptdrift.thresholds`: `evaluate_thresholds(report, max_drift=None,
  max_regressions=None, max_format_changes=None, max_flips=None,
  fail_on_regression=False) -> list[str]` returning human readable failures.
* CLI: `--html FILE`, `--csv FILE`, `--junit FILE`, `--max-drift X`,
  `--max-regressions N`, `--max-format-changes N`, `--max-flips N` on both
  `compare` and `diff`; new `watch` subcommand that wraps `compare` or `diff`.
* `CaseResult.tags: list[str] | None` appended with a default, fed from an
  optional `tags` list on cases and offline records, for per-tag breakdowns.
* pytest plugin module `promptdrift.pytest_plugin`, registered through the
  `pytest11` entry point, inert unless `--promptdrift` is passed.
* Composite action at the repo root (`action.yml`) with helper scripts under
  `action/`, so `uses: dakshtrehan/promptdrift@<ref>` works.
* Docs site under `docs/` built by `mkdocs.yml` with mkdocs-material and
  mkdocstrings, deployed by `.github/workflows/docs.yml` to this repo's Pages.
* Version becomes 0.3.0 in `promptdrift/version.py` during hardening.

## D14. HTML report, CSV and JUnit export (workstream B)

* `promptdrift.html_report` builds the page from Python strings with
  `html.escape` on every value; no templating library and no embedded JSON, so
  the page does not depend on JavaScript. Word diffs use
  `difflib.SequenceMatcher` on whitespace-split words; each side keeps its own
  whitespace, and highlight runs hug the words (trailing gaps stay outside
  `<del>`/`<ins>`), so newlines and RTL text render as written.
* Each case is its own `<tbody>` carrying `data-*` attributes (drift, level
  rank, verdict, flip, tags as `|a|b|`, regression flag, original index). Sorting
  reorders tbodies and filtering hides them, which keeps the summary row and
  the detail row together. With JavaScript off, every detail row is visible and
  the controls are hidden (`.js` class is added by an inline head script).
* Theme: CSS variables on `:root`, dark values under `prefers-color-scheme` guarded
  by `:root:not([data-theme=light])` and again under `:root[data-theme=dark]`;
  the toggle writes `promptdrift-theme` to `localStorage` inside try/except.
* The verdict rule moved into `reporter.verdict_for(report) -> (status, reason)`
  with `VERDICT_LABEL`; `print_report` output is byte-for-byte unchanged, and the
  HTML and JUnit outputs reuse it so the three never disagree.
* `CaseResult.tags` is appended last with default `None`. A bare string tag is
  normalised to a one-item list in `scoring._case_tags`; `load_cases` rejects
  non-list, non-string tags early.
* CSV: `\r\n` line endings per RFC 4180, booleans as `true`/`false`, None as
  empty, tags joined with `;`. Outputs are opt-in via `include_outputs=True`
  because they can be large and multi-line; `expected` is not a column since
  `CaseResult` does not store it.
* JUnit: `<testsuites>` wrapping one `<testsuite name="promptdrift">`, because
  Jenkins, GitHub test reporters and GitLab all accept the wrapper and some
  require it. `classname` is the first tag when present, otherwise
  `"<version_a> vs <version_b>"`. A judge `error` on a non-regressed case is an
  `<error>` rather than a failure so CI counts it separately. `time` is the sum
  of the stored latencies (0 in offline mode).
* CLI: `--html`, `--csv`, `--junit` live in `_add_output_args` so compare and
  diff share them; `_emit_exports` uses `getattr` so older Namespace objects
  without the attributes keep working.
* Screenshots were taken with Playwright driving the system Chrome
  (`executable_path=/usr/bin/google-chrome`) because the Playwright browser
  download is blocked in this environment; the demo report is built in Python
  from `examples/outputs.jsonl` plus tags and three edge cases (RTL plus emoji,
  empty outputs, a `<script>` payload) without touching `examples/`.
* Not done: a screenshot test in CI (would need a browser in the matrix) and an
  `expected` column or field, which would widen `CaseResult` beyond the agreed
  contract.

## D-E. Workstream E: examples directory and benchmark

* `examples/` keeps `v1.yaml`, `v2.yaml`, `golden.json` and `outputs.jsonl` in
  place (the README, `ci.yml` and tests reference those paths) and adds copies
  of the live files under `examples/live/`, so a move can happen in a later
  release without breaking links. `outputs.jsonl` only gained a `tags` list per
  record; every existing record and field is unchanged.
* The offline scenario (`examples/offline/`) is a fictional support assistant
  whose prompt was edited; 16 logged cases carry tags and checks. Two cases are
  deliberate regressions (a changed refund window and a JSON that lost its
  closing brace), one is a format change, one an improvement, one a multi-sample
  record with a negative excess drift and a placeholder leak caught by the
  default check spec at a 67 percent pass rate. `tests/test_examples.py` pins
  the exact counts so the data cannot drift from its README.
* `run.sh` uses `--html`, `--csv`, `--junit` (already in the CLI when it was
  written) and adds `--max-drift 0.30 --max-regressions 0` only when
  `promptdrift diff --help` lists them, falling back to `--fail-on-regression`.
  So the script works on 0.2.0 and picks up the v0.3.0 thresholds without an
  edit. It exits 1 on purpose. `expected_output.txt` is generated by the script
  (`UPDATE_EXPECTED=1`) with colour codes stripped and the "saved" lines dropped
  because they hold absolute paths; the test compares only the key summary
  lines, so a reporter restyle or version bump does not break it, but the file
  should be refreshed after the version bump so it shows 0.3.0.
* `examples/live/mock_provider.py` patches `get_provider` on the
  `promptdrift.compare` module object with `mock.patch.object` rather than the
  dotted string, because `promptdrift.compare` is also the re-exported function
  and the string form resolves to the function on Python 3.9 and 3.10 and would
  then build a real provider. A test asserts the example never does.
  Config dicts passed to `compare()` get no defaults (only `load_config` fills
  them in), so the example sets `temperature` and `max_tokens` explicitly;
  that asymmetry is worth fixing in core at some point.
* `examples/ci/test_no_regressions.py` marks two tests `xfail` because the
  example data contains deliberate regressions; the file documents that users
  drop the marker. It uses the public API only and mentions the v0.3.0 pytest
  plugin without depending on it.
* Benchmark data: the proxy refused huggingface.co, hf-mirror.com and the
  GLUE host, so the MRPC test split was fetched from a public GitHub mirror of
  `msr_paraphrase_test.txt` (1725 pairs). 100 paraphrases and 100
  non-paraphrases were sampled with `random.Random(0)` after dropping the few
  pairs containing en or em dashes (house rule). The sample is rebalanced on
  purpose (the split is 68 percent paraphrases) so AUC and accuracy read
  cleanly; `data/SOURCE.md` says so and carries the MRPC licence note.
* The embedding leg could not run here: `sentence-transformers` 6.2.0 and
  torch 2.15.0 installed from PyPI into a throwaway venv (not the shared
  environment, and not added to `pyproject.toml`), but every route to the
  model weights (huggingface.co, hf-mirror.com, the old Qdrant GCS mirror)
  was refused by the proxy. `RESULTS.md` is written from a real run and
  records the exact failure and the fact that only the TF-IDF column is
  present; nothing is estimated. The script accepts `--model <local dir>` and
  `--note` so a later run can fill the column in with the same data.
* ROC AUC, best-threshold accuracy and Spearman are implemented in pure Python
  in the script (average ranks, Mann-Whitney U) and tested against a brute
  force pair count; a fake `sentence_transformers` module in the tests drives
  the whole embedding path offline so it is covered (98 percent) even though
  the real model never loaded.
* The honest result: TF-IDF drift gets AUC 0.79 and 73.5 percent best-threshold
  accuracy on 200 MRPC pairs, with paraphrases at 0.25 mean drift and
  non-paraphrases at 0.40. RESULTS.md lists the paraphrases it scores far
  apart and the non-paraphrases it scores close, and says plainly that TF-IDF
  is a change detector, not a paraphrase detector, which is why checks, noise
  floors and the judge exist.

## D-A. Workstream A: reusable GitHub Action, thresholds and Markdown

* `promptdrift.thresholds.evaluate_thresholds` and
  `promptdrift.markdown.render_markdown` accept either a `DiffReport` or the
  dict written by `--output` (`DiffReport.to_dict()`), so the action's
  `summarize.py` works from `report.json` without re-running anything.
  `markdown.report_from_dict` rebuilds a `DiffReport` with `n_bootstrap=0`
  (the seed is not stored) and the stored `summary.ci` is reused, which keeps
  the comment byte-identical to what the CLI computed.
* `--max-drift` is a fraction on the `avg_drift` scale (0.1 is 10 percent),
  the other limits are inclusive counts. Breaches print one sentence each to
  stderr and exit 1; `--fail-on-regression` keeps its silent exit 1 so v0.2.0
  scripts see no new output. `fail_on_regression=True` in
  `evaluate_thresholds` produces its own sentence so the action can show it.
* Markdown escaping: text goes through `html.escape` plus backslashes on
  `` \ ` * _ [ ] ~ | ``; outputs go into code spans whose fence is one
  backtick longer than the longest run inside, with newlines collapsed, `<`
  `>` `&` as entities and `|` escaped inside table cells. Case output never
  starts a line, so no HTML block (`</details>`) can open. `render_markdown`
  takes an extra trailing `failures=` list on top of the agreed signature.
  `verdict` emoji: check mark, warning sign, cross mark.
* Action design: inputs reach bash only as environment variables and are
  appended to an argv array, never eval'd; `extra-args` is split on
  whitespace (documented, no quoting). The run step always writes
  `report.json`, `report.html`, `report.junit.xml` and `report.csv` into an
  absolute `report-dir`, captures the exit code into a step output and never
  fails by itself; the last step (`if: always()`) exits with that code after
  printing `threshold-failures.txt`. `summarize.py` is stdlib only, reads
  `INPUT_*` and `GITHUB_*` from the environment, re-evaluates the thresholds
  from `report.json`, writes outputs with `GITHUB_OUTPUT` heredoc syntax for
  multi-line values, and upserts the comment by paging issue comments
  (`per_page=100`, `Link: rel="next"`) for the marker. HTTP 401/403/404 on
  the comment (fork PRs) become a `::warning::` and an empty `comment-id`;
  events without a PR number print a `::notice::` and skip the comment.
* The `verdict` output is `fail` when a threshold breached, promptdrift
  exited non zero, or the report verdict is DO NOT SHIP, even if the job
  passes (as in the dogfood workflow with `fail-on-regression: "false"`).
  `review` and `ship` follow `reporter.verdict_for`; a summary-only dict
  without `cases` uses the same rule computed from the summary numbers.
* Tests: no Docker, so `tests/test_action.py` carries a mocked runner that
  executes the composite `run` steps with bash, substitutes `inputs.*`,
  `github.action_path`, `github.token` and `steps.*.outputs.*` (recursively,
  so the `${{ github.token }}` default resolves), honours `always()` and a
  single `inputs.x == 'y'` clause, and raises on anything else so a change to
  action.yml that needs more is noticed. A fake `promptdrift` on PATH records
  argv for the live-mode command without any provider. The fake GitHub API is
  a `ThreadingHTTPServer` on 127.0.0.1 with `NO_PROXY` set.
* Found, not fixed (not my files): the `pytest11` entry point added by
  workstream C imports `promptdrift` before pytest-cov starts, so
  `pytest --cov` under-reports import-time lines of every module (total showed
  83 percent with the plugin registered, 100 percent for my modules with
  `-p no:promptdrift`). Hardening should either run coverage with
  `coverage run -m pytest`, pass `-p no:promptdrift` in CI, or make the plugin
  module import `promptdrift` lazily.

## D-C. Workstream C: watch mode, pytest plugin and assert_no_regressions

* `promptdrift watch [--interval S] [--clear] [--once] <compare|diff> <args...>`
  reuses the real `compare` and `diff` subparsers: `cmd_watch` re-parses the
  wrapped arguments with the main parser, so every flag the other workstreams
  add (thresholds, exports) works under `watch` with no extra wiring. Watched
  inputs are the config files, the cases file and the checks file for
  `compare`, and the outputs file plus the checks file for `diff`; output
  files are never watched, so a run cannot trigger itself.
* The watcher in `promptdrift.watch` polls `(mtime_ns, size)` per file
  (default 1.0 s, floored at 0.05 s) and treats missing files as "watched for
  creation". When `watchdog` is importable (optional extra `watch`, not a
  dependency) it only wakes the loop early; what changed is still decided from
  the poll snapshot, so both backends behave identically and the watchdog path
  is tested with a fake module instead of a real observer.
* Each run goes through `run_wrapped`, which turns `sys.exit` inside the
  wrapped command into a return code shown in the status line
  (`watch [HH:MM:SS] diff finished in 0.42s, exit 1`). In continuous mode the
  process never exits on regressions and Ctrl-C returns 0; with `--once` the
  wrapped command's exit code is propagated so `watch --once` can stand in
  for the plain command in scripts and tests.
* The cost warning ("watch re-runs call the model on every change; use
  --no-judge or offline diff to limit cost") is printed for every `compare`
  (generation itself calls the model even with `--no-judge`) and for
  `diff --judge`; plain `diff` prints nothing because it calls no model.
* The pytest plugin logic lives in `promptdrift.pytest_plugin` as D13 says, but
  the `pytest11` entry point points at a top-level shim module
  `_promptdrift_pytest` (shipped through `py-modules`). Pointing the entry
  point straight at the package module imported all of promptdrift at every
  pytest start-up, before pytest-cov begins measuring, and dropped this repo's
  own coverage from 99 to 81 percent because every definition line was
  executed before coverage started. The shim imports the real plugin only when
  `--promptdrift*` or `--help` is in the arguments (including `addopts` and
  `PYTEST_ADDOPTS`); pytest replays the historic `pytest_addoption` and
  `pytest_configure` hooks for the late registration, and
  `-p promptdrift.pytest_plugin` or `pytest_plugins = [...]` still work because
  `import_plugin` skips an already registered module. This follows the
  precedent of Hypothesis's `_hypothesis_pytestplugin`. The shim also defines
  the `promptdrift_report` fixture so it returns `None` instead of raising a
  missing fixture error without the option.
* Items are added in `pytest_collection_modifyitems` with `tryfirst=True`, as
  children of a `pytest.File` collector per outputs file with nodeid
  `promptdrift::<file as given>`, so `-m promptdrift`, `-m "not promptdrift"`,
  `-k` and `--junit-xml` (classname `promptdrift.<file>`) treat them like any
  other test. Regression items fail with `PromptdriftRegression`, whose
  message is printed as is (no traceback); unexpected exceptions keep the
  default pytest rendering. `--promptdrift-max-drift` and
  `--promptdrift-max-regressions` apply to the per-file `summary` item through
  `evaluate_thresholds`, so the CLI, the Action and the plugin agree on what
  "exceeded" means. With several `--promptdrift` files, `--promptdrift-json`
  and `--promptdrift-html` write numbered files (`report-1.json`,
  `report-2.json`); a single file keeps the path as given.
* `promptdrift.testing.assert_no_regressions` builds on
  `promptdrift.thresholds.evaluate_thresholds` (workstream A landed first), so
  `max_flips` counts pass-to-fail flips only, matching the CLI. It is exported
  from `promptdrift.__init__` and appended to `__all__`.
* Verified: the plugin tests pass on pytest 7.4.4, 8.3.5 and 9.2.0 (throwaway
  venvs in the scratchpad, package installed with `--no-deps`); `pytest --help`
  lists the six options through the entry point; the whole suite runs no
  model call and the two new test files take under two seconds.

## D-D. Workstream D: documentation site

* The site lives under `docs/` and is built by `mkdocs.yml` with
  mkdocs-material and mkdocstrings[python]. `font: false`, no analytics and
  no social cards, so neither the build nor the pages fetch anything from the
  network; the only external URLs in the built HTML are the repository links
  and the FontAwesome licence comment inside the inlined icons.
* Every number and output shown in the pages was produced by running the
  command with the keys blanked, then pasted with ANSI codes stripped. The
  report header line that carries the version string is trimmed from pasted
  outputs so a version bump does not make the pages stale; the JUnit excerpt
  on the formats page shows 0.3.0 because the bump had landed by then.
* `docs/reference/cli.md` is generated from the real `--help` output of every
  subcommand by a throwaway script (kept in the scratchpad, not the repo), and
  `tests/test_docs.py` asserts that every flag `compare --help` and
  `diff --help` print appears on the page, so the reference cannot fall
  behind the parser without a test failing.
* `docs/reference/api.md` uses one `:::` block per module. `promptdrift.compare`
  is both a submodule and a re-exported function, so that section lists the
  functions explicitly (`promptdrift.compare.compare`, `load_config`,
  `load_cases`, `render_prompt`) rather than relying on griffe to pick the
  module over the function. `docstring_style: null` renders the docstrings as
  plain text (the code base does not use Google or numpy sections
  consistently), `show_source: false`, `members_order: source`, and
  `show_if_no_docstring: true` so that `CaseResult`, `get_provider` and the
  module constants appear even without a docstring; `filters: ["!^_"]` hides
  private names. mkdocstrings reads the modules statically, so the build
  does not need pytest installed for `promptdrift.pytest_plugin`.
* `changelog.md` and `contributing.md` include `CHANGELOG.md` and
  `CONTRIBUTING.md` through `pymdownx.snippets` (`base_path` is the repo root
  and `docs/`, `check_paths: true`) instead of copying them, so the site and
  the repository files cannot drift apart.
* The pytest plugin is described as "auto-loaded" and the shim module is not
  named, as workstream C asked.
* `tests/test_docs.py` runs every fenced bash block that starts with
  `# docs-test: run` from a temporary directory that mirrors the repository
  root through symlinks (`examples`, `benchmark`, `promptdrift`,
  `pyproject.toml`), so the relative paths in the docs resolve and the report
  files those commands write never land in the repository. Only offline
  commands are marked; the test refuses `--judge` and live `compare` in a
  marked block. The dash check in the test spells the two characters as
  `chr(0x2014)` and `chr(0x2013)` so the test file passes the rule it
  enforces.
* `.github/workflows/docs.yml` builds with `--strict` on every pull request
  as a check and deploys with `actions/configure-pages`,
  `actions/upload-pages-artifact` and `actions/deploy-pages` only on a push to
  `main`, with `pages: write` and `id-token: write` on the deploy job alone.
  The dash check in the workflow greps for the UTF-8 byte sequences under
  `LC_ALL=C` because `grep -P "\x{2014}"` fails outside a UTF-8 locale. Pages
  must be enabled once in the repository settings with "GitHub Actions" as
  the source; the workflow has not run on a real runner yet.
* `docs/requirements.txt` pins the toolchain to major versions
  (`mkdocs>=1.6,<2`, `mkdocs-material>=9.5,<10`, `mkdocstrings[python]>=0.26,<1`,
  `pymdown-extensions>=10,<11`). `pyproject.toml` was not touched; hardening
  may add a `docs` extra pointing at the same list. Verified locally with
  mkdocs 1.6.1, mkdocs-material 9.7.7, mkdocstrings 0.30.1 and
  mkdocstrings-python 2.0.9: `mkdocs build --strict` passes with zero
  warnings. mkdocs-material prints a banner about MkDocs 2.0 on every build;
  it is informational and does not affect strict mode.
* Not done, not my files: `site/` (the default `mkdocs build` output
  directory) is not in `.gitignore`; the docs build in this session wrote to
  the scratchpad instead. README.md was being rewritten by hardening while
  the docs were written, so the README links to the site are left to that
  workstream (the URLs are listed in the structured result).

## D-F. Workstream F: hardening and release readiness

* **mypy targets 3.10, not 3.9.** mypy 2.x refuses `python_version = "3.9"`
  (the oldest it accepts is 3.10). Pinning an old mypy forever was rejected;
  the config targets 3.10 and 3.9 compatibility is enforced by the real 3.9
  interpreter in the CI matrix plus a `compileall` step. The suite was also
  run locally on a uv-installed CPython 3.9.25 (364 passed).
* **mypy runs in its own CI job on 3.13**, not across the matrix: mypy's
  version resolves differently per interpreter (2.x needs 3.10+), and a type
  gate that changes with the interpreter is noise. The strict run is
  reproducible with `python -m mypy promptdrift`; the bare `mypy` on this
  machine is a uv tool with its own interpreter and sees no pytest or yaml
  stubs, so the docs and CI use `python -m mypy`.
* **Optional SDKs are typed as `Any` everywhere** (`follow_imports = "skip"`
  plus `ignore_missing_imports` for openai, anthropic, requests, watchdog) so
  the gate gives the same answer with or without the extras installed. Here
  watchdog 7 happened to be installed and typed, which would have made a
  `type: ignore` on the watchdog handler subclass unused on this machine and
  required in CI; skipping the import removes that dependence.
* **Protocols instead of `Any` for duck-typed providers.** `SupportsComplete`
  and `SupportsEmbed` live in `promptdrift.providers` and are imported under
  `TYPE_CHECKING` by judge, scoring, similarity, compare and offline, so no
  import cycle or runtime cost. They are not `runtime_checkable`: the API
  has always accepted any object with the right method and should keep doing
  so without `isinstance` games.
* **Annotation-only edits, with three tiny runtime changes** that are
  behaviour-preserving: the CLI passes the `--max-*` limits as explicit
  keyword arguments instead of `**dict`, `judge_both_orders` tests the
  tuples (`ab is None or ba is None`) instead of their first elements, and
  `stability_summary` guards `excess_drift is not None` before comparing.
  Two real fixes rode along and are in the changelog: `compare()` with
  config dicts that omit `system`, `temperature` or `max_tokens` no longer
  raises `KeyError` (same defaults as `load_config`), and the drift of
  identical outputs is clamped at `0.0` so `-0.0` never appears in reports.
* **ruff rule set unchanged.** Trying `UP` and `SIM` produced 89 findings,
  mostly `Optional[X]` to `X | None` rewrites in runtime-evaluated positions
  and contextlib.suppress suggestions; adopting them would be churn across
  files other workstreams own and partly conflicts with the 3.9 rule. Noted
  here; a later release can adopt them in one sweep.
* **Coverage** is measured over `promptdrift` and `action` (99.25 percent,
  364 tests at the time of the final gate) with `fail_under = 90`. The
  pytest11 shim from workstream C already keeps import-time lines visible to
  pytest-cov, so no `coverage run` wrapper was needed.
* **Sdist contents.** The sdist ships `action.yml`, `action/`, `examples/`,
  `benchmark/`, `tests/` and `.github/workflows/` so `pytest` passes from an
  unpacked sdist (verified: 323 passed), and leaves out `docs/` (1.3 MB of
  screenshots) together with `tests/test_docs.py`, which only checks the
  docs site. A `docs` extra in pyproject mirrors `docs/requirements.txt`.
* **Benchmark results were not regenerated**, only the version string was
  updated. Regenerating with `--no-embeddings` would have replaced the
  honest "model download blocked by the proxy" record with "skipped by
  flag"; instead the script was re-run with `--no-write` and the numbers
  (AUC 0.792, threshold 0.315, accuracy 73.5 percent) confirmed unchanged,
  which they must be since the TF-IDF code did not change.
* **Terminal demo** is a static SVG plus a six-frame GIF, both rendered by a
  scratchpad script from the captured ANSI output of
  `promptdrift diff examples/offline/outputs.jsonl --name-a v1 --name-b v2 --checks examples/offline/checks.yaml --summary`.
  asciinema and agg were not tried: GitHub release downloads are not
  reachable through the proxy and a hand-rendered SVG needs no binary. The
  GIF uses DejaVu Sans Mono from the system; the SVG relies on the viewer's
  monospace font.
* **README snippets** were all executed in a scratch copy before going in;
  the one `evaluate_thresholds` output comment shows the real list. Snippets
  that need a key (`compare`, `--judge`) are labelled as such and not run.
* **CI plugin smoke asserts the failure**, because the offline example
  regresses on purpose: the job requires exit code 1 and a JUnit file with
  17 tests and 3 failures (two cases plus the summary), rather than a green
  run that would prove nothing.
* **Release checklist** in RELEASE.md is ordered: merge v0.2.0, merge this
  PR, enable Pages (Source: GitHub Actions) once, tag `v0.3.0`, moving
  `v0.3` tag for the action, GitHub release notes from the changelog, PyPI
  by the maintainer with their own token, post-release checks. None of
  these steps were performed.
* **Python 3.9 was executed locally** (uv-installed 3.9.25, dev extras
  installed, full suite). One run of the full suite on 3.9 failed
  `tests/test_pytest_plugin.py::test_html_skipped_when_module_missing`
  once while another workstream was committing; it passed alone, with the
  new docs tests, and in the repeated full runs afterwards, so it is treated
  as a transient of the shared tree rather than a 3.9 incompatibility.
* **Correction to the 3.9 note above.** The failure of
  `test_html_skipped_when_module_missing` was not a transient of the shared
  tree: it was order dependent and reproducible (it failed on every
  standalone run of `tests/test_pytest_plugin.py` on 3.13, before and after
  the hardening changes, and in roughly half of the full-suite runs on 3.9).
  The inner pytester run emits pytest's "module already imported so cannot
  be rewritten" warning for the plugin whenever an earlier test imported
  it, and Python shows a warning once per location per process, so the
  exact `warnings=1` count varied with test order. The count assertion was
  removed; the test still asserts the outcomes and the presence of the
  `--promptdrift-html ignored` warning. After the fix the full suite passed
  twice on 3.9 and on 3.13, and the file passes standalone on both.

## D15. CI runs on every pull request, not only those targeting main

The v0.3.0 PR is stacked on the v0.2.0 PR branch so that it shows only the
v0.3.0 delta and GitHub retargets it to `main` once v0.2.0 merges. `ci.yml`
only ran for pull requests whose base was `main`, so a stacked PR got no
checks. The trigger is now a bare `pull_request` (any base) plus
`workflow_dispatch`, matching `docs.yml` and `promptdrift-example.yml`.

## D16. First real CI run: two failures fixed

The first run of `ci.yml` on a GitHub runner failed in two places that the
local runs had not shown.

* The pytest plugin smoke step ran `pytest --promptdrift ...` from the repo
  root with no path argument, so pytest collected this repo's own 364 tests
  next to the plugin's 17 generated items and the `tests="17"` check failed.
  The step now passes an empty directory as the only collection target.
  Locally the agent had run the command from a different directory.
* `examples/offline/run.sh` piped a second `--json` run through `head -n 12`
  under `set -o pipefail`. On the runner `head` closed the pipe while Python
  was still writing, Python reported the failed flush at shutdown and exited
  120 instead of 1. The script now reads the first lines of the report.json it
  already wrote. The CLI also gained a broken pipe guard: `promptdrift ... |
  head` keeps writing its files, keeps its exit code and no longer prints a
  traceback. A test with an outputs file larger than the pipe buffer proves the
  failure on the old code and the fix on the new one.

## Review fixes (group B)

Group B covered the action, workflows, docs, examples and release notes. Every fix comes with a test in tests/test_action.py, test_docs.py or test_examples.py.

- action-1 and security-7 (comment API errors broke the step): `upsert_comment` now turns every failure into a `::warning::` and returns None. That covers any HTTPError (fork wording kept for 401/403/404), URLError, other OSErrors such as read timeouts, and a body that is not JSON. `main()` writes `$GITHUB_OUTPUT` and the `::error::` lines before it tries the comment. It then appends `comment-id` alone, and the last value for a key wins. The old test that pinned "a 500 raises" now expects None. No retry was added.
- action-2 (first docs deploy): dropped `configure-pages`, since nothing uses its output and it fails while Pages is disabled. Added `workflow_dispatch`, and the upload and deploy conditions are now `event_name != 'pull_request' && ref == main`, so a manual run on main deploys. The header comment is corrected. RELEASE.md now enables Pages in step 2, before the v0.3.0 merge in step 3, and says how to re-run if Pages was enabled late. D15's statement that docs.yml has `workflow_dispatch` is now true.
- action-3 (thresholds vanished without promptdrift importable): `summarize.py` has a stdlib copy of the threshold rules with identical sentences, including the tie-breaking decimals. A parity test runs it against `promptdrift.thresholds`. It is used with a `::warning::` when the import fails and any threshold input is set. Threshold inputs that do not parse, or that the library rejects, also become a warning instead of a traceback. With `install: skip`, the install step warns when `python` cannot import promptdrift. setup-python is still always run, so the summary step has a known Python.
- action-4 (misleading steps after a failed install or run): the upload step also requires `steps.run.outputs.report-dir != ''`. "Fail on thresholds" prints `::error::promptdrift did not run` and exits 1 when there is no exit code. The job fails in the same cases as before.
- action-5: live mode now needs `cases` too. A missing input takes the existing exit-code 2 configuration error path.
- action-6: moved to the Node.js 24 majors: checkout@v5, setup-python@v6, upload-artifact@v6 (v5 is still node20), upload-pages-artifact@v5, deploy-pages@v5. This covers action.yml, every workflow and the docs samples. The runner 2.327.1 requirement is documented in action/README.md, the action reference and the CHANGELOG.
- action-7: the artifact-name rows and the two-jobs advice now say the name must be unique per workflow run, with a matrix example. The default stays `promptdrift-report`, and no overwrite or continue-on-error was added.
- tests-5: the in-process mock_provider test blanks the keys through monkeypatch, so they are restored at teardown. The example itself is unchanged.
- docs-2, docs-4, docs-6: formats.md and the mock_provider comment now describe the compare() dict defaults. The quickstart shows the real relative `init` output. The plain workflow sample uploads both the JSON and the JUnit file. Each has a test.
- docs-3: README and RELEASE.md say the action is dogfooded on this repository's pull requests, and that the first external run is step 8. DECISIONS.md:600 is left as history.
- docs-5: the README and docs/index extras tables list the full dev extra and a new `docs` row, and Ollama's `requests` need is stated consistently. A test compares the dev and docs rows with pyproject.toml, read without tomllib so it works on 3.9. The dev rows also list markdown-it-py, which group A added to the dev extra in this round.
- docs-7: `judge` and `fail-on-regression` go through a bash 3.2 safe `is_true` (`tr`, not `${v,,}`). It accepts true/yes/on/1 in any case, the same as `summarize.truthy`. "true" and "false" behave as before. The docs are updated.
- docs-8: RELEASE.md section 6 step 3 and step 9 now say to delete the `[0.3.0]` "Not yet published" line and to mark the never-uploaded `[0.2.0]` entry as superseded, not delete it.
- security-3: the run step deletes report.json, report.html, report.junit.xml, report.csv and threshold-failures.txt. The directory prefix is on each path, and this happens before promptdrift runs. As a second guard, `build_markdown` puts "promptdrift exited with code N" right after the marker when the exit code is non-zero and no threshold failed.
- security-4: `find_existing_comment` only takes a comment whose body starts with the marker. It prefers a Bot author, which covers `github-actions[bot]` and app tokens. Otherwise it takes the token's own user, looked up with `GET /user` for PATs. A 403 there, which the default token gets, means no match, and the action posts a new comment. The login is not hard-coded, so PAT and app tokens keep working. No POST fallback was added after a PATCH 403.
