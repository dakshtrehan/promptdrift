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
from .offline import compare_outputs, load_outputs
from .reporter import print_json, print_report, save_report
from .version import __version__

__all__ = [
    "__version__",
    "compare", "load_config", "load_cases",
    "compare_outputs", "load_outputs",
    "DiffReport", "CaseResult",
    "print_report", "save_report", "print_json",
]
