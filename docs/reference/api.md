# Python API

Everything below is generated from the docstrings in the installed package.
The package root re-exports the names most programs need:

```python
from promptdrift import (
    compare, load_config, load_cases,                 # live mode
    compare_outputs, load_outputs,                    # offline mode
    DiffReport, CaseResult,                           # data model
    print_report, save_report, print_json,            # terminal and JSON
    render_html, save_html,                           # HTML report
    to_csv, save_csv, to_junit, save_junit,           # CSV and JUnit
    evaluate_thresholds, render_markdown,             # gates and PR comments
    assert_no_regressions,                            # test suites
    __version__,
)
```

Everything that worked in v0.1.1 and v0.2.0 keeps working: dataclass fields
are only ever appended with defaults, and the private v0.1.1 helpers
`promptdrift.compare._tfidf_similarity` and `promptdrift.compare._judge`
still exist as aliases (use `promptdrift.similarity.tfidf_similarity` and
`promptdrift.judge.judge_pair` in new code).

!!! note "`promptdrift.compare` is both a module and a function"
    `from promptdrift import compare` gives you the function; the module that
    holds it, `load_config` and `load_cases` is reached with
    `import promptdrift.compare as compare_module` or through the entries below.

## Live mode

::: promptdrift.compare.compare

::: promptdrift.compare.load_config

::: promptdrift.compare.load_cases

::: promptdrift.compare.render_prompt

## Offline mode

::: promptdrift.offline

## Data model

::: promptdrift.core

## Scoring

::: promptdrift.scoring

## Checks

::: promptdrift.checks

## Structure

::: promptdrift.structure

## Similarity

::: promptdrift.similarity

## Statistics

::: promptdrift.stats

## Judge

::: promptdrift.judge

## Providers

::: promptdrift.providers

## Terminal and verdict

::: promptdrift.reporter

## HTML report

::: promptdrift.html_report

## CSV and JUnit export

::: promptdrift.export

## Markdown

::: promptdrift.markdown

## Thresholds

::: promptdrift.thresholds

## Watch mode

::: promptdrift.watch

## Test helpers

::: promptdrift.testing

## pytest plugin

::: promptdrift.pytest_plugin
