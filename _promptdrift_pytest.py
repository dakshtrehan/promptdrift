"""
Entry point shim for the promptdrift pytest plugin.

pytest imports every ``pytest11`` entry point at start-up, before conftests
and before coverage tools begin measuring. Pointing the entry point straight
at ``promptdrift.pytest_plugin`` would import the whole ``promptdrift`` package
into every pytest run on a machine where it is installed, and would hide the
package's own definition lines from coverage. This module imports nothing from
promptdrift until ``--promptdrift`` (or ``--help``) is on the command line, in
``addopts`` or in ``PYTEST_ADDOPTS``; then it registers the real plugin, whose
``pytest_addoption`` is replayed by pytest so the options parse normally.

``-p promptdrift.pytest_plugin`` and ``pytest_plugins = ["promptdrift.pytest_plugin"]``
keep working: pytest skips a plugin that is already registered.
"""
from __future__ import annotations

import pytest

PLUGIN = "promptdrift.pytest_plugin"


def _wants_plugin(args: list[str]) -> bool:
    for a in args:
        if a in ("-h", "--help") or a.startswith("--promptdrift"):
            return True
    return False


def pytest_load_initial_conftests(early_config, parser, args):
    """Load the real plugin only when its options are in use (or for --help)."""
    if _wants_plugin(list(args)):
        early_config.pluginmanager.import_plugin(PLUGIN)


@pytest.fixture
def promptdrift_report(request):
    """``DiffReport`` for the first ``--promptdrift`` file, or ``None`` without the option.

    The real plugin defines the same fixture; this one exists so that a
    test asking for ``promptdrift_report`` gets ``None`` instead of a missing
    fixture error when ``--promptdrift`` was not passed. pytest uses whichever
    fixture was registered last: normally the real plugin's, but with
    ``-p promptdrift.pytest_plugin --promptdrift FILE`` the real plugin is
    registered first and this one wins, so it must build the report too.
    """
    if request.config.getoption("promptdrift", default=None):
        from promptdrift.pytest_plugin import build_reports

        reports = build_reports(request.config)
        return reports[0][1] if reports else None
    return None
