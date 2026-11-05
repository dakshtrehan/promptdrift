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
