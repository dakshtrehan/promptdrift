"""
pytest plugin: assert that stored LLM outputs show no regressions, inside an
existing test suite.

    pytest --promptdrift outputs.jsonl
    pytest --promptdrift outputs.jsonl --promptdrift-max-drift 0.2 --junit-xml junit.xml

The plugin is registered through the ``pytest11`` entry point (a thin shim,
``_promptdrift_pytest``, that imports this module only when ``--promptdrift``
is on the command line), so it needs no ``-p`` flag once promptdrift is
installed, and it is inert unless ``--promptdrift`` is given: no options, no
items, no output, no files written and no import of the promptdrift package.
With the option it scores the file offline (no model is called) and adds one
test item per case, named
``promptdrift::<file>::<case_id>``, that fails when the case is a regression
(judge regressed, check pass to fail or drift level ``regression``), plus a
``summary`` item per file that fails when ``--promptdrift-max-drift`` or
``--promptdrift-max-regressions`` is exceeded. Items carry the ``promptdrift``
marker and show up in the normal report and in ``--junit-xml`` output.

The ``promptdrift_report`` fixture gives the ``DiffReport`` of the first
``--promptdrift`` file, or ``None`` when the option was not passed.
"""
from __future__ import annotations

import json
import os  # noqa: F401 - used in annotations
import warnings
from pathlib import Path
from typing import Any, Iterator, Optional, Union

import pytest

from .cli import _fraction
from .core import CaseResult, DiffReport
from .export import failure_reason
from .testing import describe_case
from .thresholds import evaluate_thresholds

__all__ = ["PromptdriftFile", "PromptdriftCaseItem", "PromptdriftSummaryItem",
           "PromptdriftRegression", "build_reports"]

_KEY = "_promptdrift_reports"


class PromptdriftRegression(AssertionError):
    """Raised by a case or summary item; the message is shown as is."""


def pytest_addoption(parser: pytest.Parser) -> None:
    group = parser.getgroup("promptdrift", "promptdrift: LLM output regression gate")
    group.addoption("--promptdrift", action="append", default=[], metavar="PATH",
                    help="JSONL/JSON outputs file to score offline (repeatable); "
                         "adds one test per case plus a summary test per file")
    group.addoption("--promptdrift-max-drift", type=_fraction, default=None, metavar="FLOAT",
                    help="fail the summary test when average drift exceeds this (0.0 to 1.0)")
    group.addoption("--promptdrift-max-regressions", type=int, default=0, metavar="INT",
                    help="fail the summary test when regressions exceed this (default 0)")
    group.addoption("--promptdrift-checks", default=None, metavar="FILE",
                    help="default deterministic checks (JSON/YAML) for cases without their own")
    group.addoption("--promptdrift-json", default=None, metavar="PATH",
                    help="save the promptdrift JSON report here")
    group.addoption("--promptdrift-html", default=None, metavar="PATH",
                    help="save the self-contained HTML report here")


def pytest_configure(config: pytest.Config) -> None:
    if config.getoption("promptdrift", default=None):
        config.addinivalue_line(
            "markers", "promptdrift: test items generated from a --promptdrift outputs file")


def _load_checks(path: Optional[str]) -> Any:
    if not path:
        return None
    from .checks import normalize_checks

    text = Path(path).read_text()
    if str(path).lower().endswith((".yaml", ".yml")):
        import yaml
        spec = yaml.safe_load(text)
    else:
        spec = json.loads(text)
    normalize_checks(spec, expected="")
    return spec


def _numbered(path: str, index: int, count: int) -> str:
    """``report.json`` stays as is for one file; ``report-2.json`` for the second of many."""
    if count == 1:
        return path
    p = Path(path)
    return str(p.with_name(f"{p.stem}-{index}{p.suffix}"))


def build_reports(config: pytest.Config) -> list[tuple[str, DiffReport]]:
    """Score every ``--promptdrift`` file once and cache the reports on ``config``."""
    cached: Optional[list[tuple[str, DiffReport]]] = getattr(config, _KEY, None)
    if cached is not None:
        return cached
    files = config.getoption("promptdrift", default=None) or []
    reports: list[tuple[str, DiffReport]] = []
    if files:
        from .offline import compare_outputs

        checks = _load_checks(config.getoption("promptdrift_checks"))
        for f in files:
            if not Path(f).exists():
                raise pytest.UsageError(f"--promptdrift: file not found: {f}")
            try:
                report = compare_outputs(f, checks=checks)
            except ValueError as e:
                raise pytest.UsageError(f"--promptdrift: {f}: {e}") from e
            reports.append((f, report))
        _save_outputs(config, reports)
    setattr(config, _KEY, reports)
    return reports


def _save_outputs(config: pytest.Config, reports: list[tuple[str, DiffReport]]) -> None:
    json_path = config.getoption("promptdrift_json")
    html_path = config.getoption("promptdrift_html")
    n = len(reports)
    for i, (_, report) in enumerate(reports, start=1):
        if json_path:
            from .reporter import save_report
            target = _numbered(json_path, i, n)
            Path(target).parent.mkdir(parents=True, exist_ok=True)
            save_report(report, target)
        if html_path:
            try:
                from .html_report import save_html
            except ImportError:
                warnings.warn(pytest.PytestWarning(
                    "--promptdrift-html ignored: promptdrift.html_report is not available"),
                    stacklevel=2)
                break
            target = _numbered(html_path, i, n)
            Path(target).parent.mkdir(parents=True, exist_ok=True)
            save_html(report, target)


class PromptdriftCaseItem(pytest.Item):
    """One test per case; fails when the case counts as a regression."""

    def __init__(self, *, case: CaseResult, report: DiffReport, **kwargs: Any) -> None:
        super().__init__(**kwargs)
        self.case = case
        self.report = report
        self.add_marker("promptdrift")

    def runtest(self) -> None:
        reason = failure_reason(self.case)
        if reason:
            raise PromptdriftRegression(
                f"promptdrift regression in {self.case.case_id} "
                f"({self.report.version_a} vs {self.report.version_b})\n"
                + describe_case(self.case))

    def repr_failure(self, excinfo: pytest.ExceptionInfo[BaseException],
                     style: Optional[str] = None) -> Any:
        if isinstance(excinfo.value, PromptdriftRegression):
            return str(excinfo.value)
        return super().repr_failure(excinfo)

    def reportinfo(self) -> tuple[Union["os.PathLike[str]", str], Optional[int], str]:
        return self.path, None, f"promptdrift case {self.case.case_id}"


class PromptdriftSummaryItem(pytest.Item):
    """One test per file; fails when the drift or regression thresholds are exceeded."""

    def __init__(self, *, report: DiffReport, max_drift: Optional[float],
                 max_regressions: Optional[int], **kwargs: Any) -> None:
        super().__init__(**kwargs)
        self.report = report
        self.max_drift = max_drift
        self.max_regressions = max_regressions
        self.add_marker("promptdrift")

    def runtest(self) -> None:
        failures = evaluate_thresholds(self.report, max_drift=self.max_drift,
                                       max_regressions=self.max_regressions)
        if failures:
            r = self.report
            lines = [f"promptdrift summary for {r.version_a} vs {r.version_b}: "
                     f"{r.total_cases} case(s), avg drift {r.avg_drift:.4f}, "
                     f"{len(r.regressions)} regression(s)"]
            lines += [f"  - {f}" for f in failures]
            raise PromptdriftRegression("\n".join(lines))

    def repr_failure(self, excinfo: pytest.ExceptionInfo[BaseException],
                     style: Optional[str] = None) -> Any:
        if isinstance(excinfo.value, PromptdriftRegression):
            return str(excinfo.value)
        return super().repr_failure(excinfo)

    def reportinfo(self) -> tuple[Union["os.PathLike[str]", str], Optional[int], str]:
        return self.path, None, "promptdrift summary"


class PromptdriftFile(pytest.File):
    """Collector for one ``--promptdrift`` outputs file."""

    def __init__(self, *, report: DiffReport, **kwargs: Any) -> None:
        super().__init__(**kwargs)
        self.report = report

    def collect(self) -> Iterator[pytest.Item]:
        config = self.config
        for case in self.report.cases:
            yield PromptdriftCaseItem.from_parent(
                self, name=case.case_id, case=case, report=self.report)
        yield PromptdriftSummaryItem.from_parent(
            self, name="summary", report=self.report,
            max_drift=config.getoption("promptdrift_max_drift"),
            max_regressions=config.getoption("promptdrift_max_regressions"))


@pytest.hookimpl(tryfirst=True)
def pytest_collection_modifyitems(session: pytest.Session, config: pytest.Config,
                                  items: list[pytest.Item]) -> None:
    # tryfirst so that -m, -k and other deselection hooks see the generated items
    if not config.getoption("promptdrift", default=None):
        return
    for f, report in build_reports(config):
        collector = PromptdriftFile.from_parent(
            session, path=Path(f).resolve(), nodeid=f"promptdrift::{f}", report=report)
        generated = list(collector.collect())
        # Report the generated items as collected so the "collected N items"
        # header, --collect-only and -k/-m "selected" counts include them.
        result: list[Union[pytest.Item, pytest.Collector]] = list(generated)
        session.ihook.pytest_collectreport(
            report=pytest.CollectReport(collector.nodeid, "passed", None, result))
        items.extend(generated)


@pytest.fixture
def promptdrift_report(request: pytest.FixtureRequest) -> Optional[DiffReport]:
    """``DiffReport`` for the first ``--promptdrift`` file, or ``None`` without the option."""
    reports = build_reports(request.config)
    return reports[0][1] if reports else None
