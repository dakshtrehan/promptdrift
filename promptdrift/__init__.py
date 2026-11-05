"""
promptdrift: semantic diff for LLM prompts and outputs.
Catch regressions before they hit production.

    from promptdrift import compare
    from promptdrift.reporter import print_report

    report = compare("v1.yaml", "v2.yaml", cases="golden.json")
    print_report(report)
"""
from .compare import compare, load_cases, load_config
from .core import CaseResult, DiffReport
from .export import save_csv, save_junit, to_csv, to_junit
from .html_report import render_html, save_html
from .markdown import render_markdown
from .offline import compare_outputs, load_outputs
from .reporter import print_json, print_report, save_report
from .thresholds import evaluate_thresholds
from .version import __version__

__all__ = [
    "__version__",
    "compare", "load_config", "load_cases",
    "compare_outputs", "load_outputs",
    "DiffReport", "CaseResult",
    "print_report", "save_report", "print_json",
    "render_html", "save_html",
    "to_csv", "save_csv", "to_junit", "save_junit",
    "evaluate_thresholds", "render_markdown",
]
