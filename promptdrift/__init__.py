"""
promptdrift — semantic diff for LLM prompts and outputs.
Catch regressions before they hit production.

    from promptdrift import compare
    from promptdrift.reporter import print_report

    report = compare("v1.yaml", "v2.yaml", cases="golden.json")
    print_report(report)
"""
from .compare import compare, load_config, load_cases
from .offline import compare_outputs, load_outputs
from .core import DiffReport, CaseResult
from .reporter import print_report, save_report, print_json

__version__ = "0.1.1"
__all__ = [
    "compare", "load_config", "load_cases",
    "compare_outputs", "load_outputs",
    "DiffReport", "CaseResult",
    "print_report", "save_report", "print_json",
]
