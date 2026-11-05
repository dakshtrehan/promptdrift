"""Checks on the mkdocs documentation site under docs/.

* mkdocs.yml parses and every nav entry points at an existing page,
* no page contains an em dash (U+2014) or an en dash (U+2013),
* every CLI flag printed by ``promptdrift compare --help`` and
  ``promptdrift diff --help`` is mentioned in reference/cli.md,
* every fenced bash block whose first line is ``# docs-test: run`` runs
  successfully with API keys blanked. The commands run from a temporary
  directory that mirrors the repository root through symlinks, so the
  relative example paths in the docs resolve and no output file lands in the
  repository.

No network and no model call is involved; only offline commands are marked.
"""
from __future__ import annotations

import os
import re
import shlex
import subprocess
import sys
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parent.parent
DOCS = ROOT / "docs"
MKDOCS_YML = ROOT / "mkdocs.yml"
CLI_PAGE = DOCS / "reference" / "cli.md"
RUN_MARKER = "# docs-test: run"
# Spelled as code points so this file itself passes the house rule.
EM_DASH = chr(0x2014)
EN_DASH = chr(0x2013)

_FENCE = re.compile(r"^```(?:bash|sh|console)\s*\n(.*?)^```", re.MULTILINE | re.DOTALL)
_FLAG = re.compile(r"(?<![\w-])(--[a-z][a-z0-9-]*)")
_OPTIONAL_FLAG = re.compile(r"\[(--[a-z][a-z0-9-]*)(?: [^\]]*)?\]")


def _load_mkdocs() -> dict:
    # mkdocs.yml uses python/name tags for emoji extensions in some setups;
    # the project's file uses none, but a permissive loader keeps the test
    # robust if one is added later.
    class _Loader(yaml.SafeLoader):
        pass

    def _any(loader, suffix, node):  # pragma: no cover - only for !!python tags
        return None

    _Loader.add_multi_constructor("tag:yaml.org,2002:python/", _any)
    with open(MKDOCS_YML, encoding="utf-8") as f:
        return yaml.load(f, Loader=_Loader)


def _nav_files(nav) -> list[str]:
    files: list[str] = []
    if isinstance(nav, str):
        files.append(nav)
    elif isinstance(nav, list):
        for item in nav:
            files.extend(_nav_files(item))
    elif isinstance(nav, dict):
        for value in nav.values():
            files.extend(_nav_files(value))
    return files


def _doc_pages() -> list[Path]:
    return sorted(DOCS.rglob("*.md"))


def _blank_env() -> dict:
    env = dict(os.environ)
    env["OPENAI_API_KEY"] = ""
    env["ANTHROPIC_API_KEY"] = ""
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    return env


def _runnable_blocks() -> list[tuple[str, str]]:
    """``(page, command)`` for every marked bash block in the docs."""
    found: list[tuple[str, str]] = []
    for page in _doc_pages():
        text = page.read_text(encoding="utf-8")
        for match in _FENCE.finditer(text):
            lines = match.group(1).splitlines()
            if not lines or lines[0].strip() != RUN_MARKER:
                continue
            commands = [ln for ln in lines[1:] if ln.strip() and not ln.lstrip().startswith("#")]
            assert commands, f"{page}: '{RUN_MARKER}' block has no command"
            for cmd in commands:
                found.append((str(page.relative_to(ROOT)), cmd.strip()))
    return found


# ── mkdocs.yml and nav ───────────────────────────────────────────────────────

def test_mkdocs_yml_parses_and_names_the_site():
    cfg = _load_mkdocs()
    assert cfg["site_name"] == "promptdrift"
    assert cfg["site_url"] == "https://dakshtrehan.github.io/promptdrift/"
    assert cfg["repo_url"] == "https://github.com/dakshtrehan/promptdrift"
    assert cfg["theme"]["name"] == "material"
    # The build must make no network requests: no Google Fonts, no analytics.
    assert cfg["theme"].get("font") is False
    assert "analytics" not in (cfg.get("extra") or {})


def test_every_nav_entry_exists():
    cfg = _load_mkdocs()
    files = _nav_files(cfg["nav"])
    assert files, "nav is empty"
    missing = [f for f in files if not (DOCS / f).is_file()]
    assert not missing, f"nav entries without a page: {missing}"
    # and the other way round: every page is reachable from the nav
    pages = {str(p.relative_to(DOCS)) for p in _doc_pages()}
    orphans = pages - set(files)
    assert not orphans, f"pages missing from the nav: {sorted(orphans)}"


def test_docs_requirements_and_workflow_exist():
    req = (DOCS / "requirements.txt").read_text(encoding="utf-8")
    assert "mkdocs-material" in req and "mkdocstrings" in req
    with open(ROOT / ".github" / "workflows" / "docs.yml", encoding="utf-8") as f:
        wf = yaml.safe_load(f)
    # PyYAML reads the bare "on" key as boolean True.
    triggers = wf.get("on", wf.get(True))
    assert "pull_request" in triggers
    assert triggers["push"]["branches"] == ["main"]
    assert "mkdocs build --strict" in str(wf["jobs"]["build"]["steps"])
    deploy = wf["jobs"]["deploy"]
    assert deploy["permissions"] == {"pages": "write", "id-token": "write"}
    assert "refs/heads/main" in deploy["if"]


# ── house style ──────────────────────────────────────────────────────────────

@pytest.mark.parametrize("page", [str(p.relative_to(ROOT)) for p in _doc_pages()])
def test_no_em_or_en_dashes(page):
    text = (ROOT / page).read_text(encoding="utf-8")
    for lineno, line in enumerate(text.splitlines(), 1):
        assert EM_DASH not in line, f"{page}:{lineno}: em dash"
        assert EN_DASH not in line, f"{page}:{lineno}: en dash"


def test_screenshots_referenced_by_quickstart_exist():
    text = (DOCS / "quickstart.md").read_text(encoding="utf-8")
    images = re.findall(r"!\[[^\]]*\]\(([^)\s]+)", text)
    assert images, "quickstart has no screenshots"
    for rel in images:
        assert (DOCS / rel).is_file(), f"missing image {rel}"


# ── CLI reference matches --help ─────────────────────────────────────────────

def _help_flags(*args: str) -> set[str]:
    out = subprocess.run([sys.executable, "-m", "promptdrift.cli", *args, "--help"],
                         capture_output=True, text=True, env=_blank_env(), cwd=ROOT)
    assert out.returncode == 0, out.stderr
    return set(_FLAG.findall(out.stdout))


@pytest.mark.parametrize("subcommand", ["compare", "diff"])
def test_cli_reference_lists_every_flag(subcommand):
    flags = _help_flags(subcommand)
    assert "--max-drift" in flags and "--html" in flags, flags
    page = CLI_PAGE.read_text(encoding="utf-8")
    documented = set(_FLAG.findall(page))
    missing = sorted(flags - documented)
    assert not missing, f"{subcommand} flags missing from {CLI_PAGE.name}: {missing}"


def test_cli_reference_includes_watch_and_init():
    page = CLI_PAGE.read_text(encoding="utf-8")
    for flag in ("--interval", "--clear", "--once", "--dir", "--version"):
        assert flag in page, flag
    for option in ("--promptdrift", "--promptdrift-max-drift", "--promptdrift-json"):
        assert option in page, option


# ── runnable blocks ──────────────────────────────────────────────────────────

@pytest.fixture(scope="module")
def docs_cwd(tmp_path_factory):
    """A scratch directory that looks like the repo root for the examples."""
    cwd = tmp_path_factory.mktemp("docs-run")
    for name in ("examples", "benchmark", "promptdrift", "pyproject.toml"):
        os.symlink(ROOT / name, cwd / name)
    return cwd


def test_marked_blocks_exist():
    blocks = _runnable_blocks()
    assert len(blocks) >= 5, blocks
    assert any(cmd.startswith("promptdrift diff") for _, cmd in blocks)


@pytest.mark.parametrize("page,command", _runnable_blocks())
def test_marked_block_runs(page, command, docs_cwd):
    argv = shlex.split(command)
    assert argv[0] in ("promptdrift", "python", "python3", "bash"), f"{page}: {command}"
    if argv[0] in ("python", "python3"):
        argv[0] = sys.executable
    elif argv[0] == "promptdrift":
        argv = [sys.executable, "-m", "promptdrift.cli", *argv[1:]]
    # Guard rails: documented commands must be offline, so no judge and no live compare.
    assert "--judge" not in argv, f"{page}: judge flag in a docs-test block"
    assert "compare" not in argv[1:4], f"{page}: live compare in a docs-test block"
    result = subprocess.run(argv, capture_output=True, text=True, env=_blank_env(),
                            cwd=docs_cwd, timeout=300)
    assert result.returncode == 0, (
        f"{page}: `{command}` exited {result.returncode}\n"
        f"stdout:\n{result.stdout[-2000:]}\nstderr:\n{result.stderr[-2000:]}")
