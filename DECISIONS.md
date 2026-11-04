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
