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
