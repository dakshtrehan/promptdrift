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

import fnmatch
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
    # an accidental live call fails locally instead of reaching any API
    env["OPENAI_BASE_URL"] = "http://127.0.0.1:9"
    env["ANTHROPIC_BASE_URL"] = "http://127.0.0.1:9"
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    return env


def _offline_guard(argv: list) -> None:
    """Documented commands must be offline: no judge and no live compare anywhere in argv."""
    assert "--judge" not in argv, "judge flag in a docs-test block"
    # the whole argv, so "promptdrift watch --once compare ..." is caught too
    assert "compare" not in argv, "live compare in a docs-test block"


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
    # a manual run on main can deploy (Pages enabled after the first push)
    assert "workflow_dispatch" in triggers
    assert "github.event_name != 'pull_request'" in deploy["if"]
    upload = next(s for s in wf["jobs"]["build"]["steps"]
                  if "upload-pages-artifact" in s.get("uses", ""))
    assert upload["if"] == deploy["if"]
    # configure-pages fails the build job while Pages is disabled and is not needed
    assert "configure-pages" not in str(wf["jobs"])


def _optional_dependencies() -> dict[str, list[str]]:
    """Extras from pyproject.toml without tomllib (absent on Python 3.9 and 3.10)."""
    text = (ROOT / "pyproject.toml").read_text(encoding="utf-8")
    section = text.split("[project.optional-dependencies]", 1)[1].split("\n[", 1)[0]
    extras: dict[str, list[str]] = {}
    for m in re.finditer(r"^(\w+)\s*=\s*\[(.*?)\]\s*$", section, re.MULTILINE | re.DOTALL):
        names = re.findall(r'"([A-Za-z0-9_.-]+)', m.group(2))
        extras[m.group(1)] = names
    return extras


@pytest.mark.parametrize("page,prefix", [("README.md", "`[{}]`"), ("docs/index.md", "`{}`")])
def test_extras_tables_match_pyproject(page, prefix):
    extras = _optional_dependencies()
    assert {"openai", "anthropic", "watch", "docs", "all", "dev"} <= set(extras)
    rows = {line.split("|")[1].strip(): line for line in
            (ROOT / page).read_text(encoding="utf-8").splitlines()
            if line.startswith("| `")}
    sdk = {"openai", "anthropic"}
    for extra in ("docs", "dev"):
        row = rows.get(prefix.format(extra))
        assert row, f"{page}: no row for the {extra} extra"
        for name in extras[extra]:
            if name in sdk:
                assert "both SDKs" in row, f"{page}: {extra} row omits the SDKs"
            else:
                assert name in row, f"{page}: {extra} row omits {name}"


def test_quickstart_init_output_matches_the_cli(tmp_path):
    proc = subprocess.run([sys.executable, "-m", "promptdrift.cli", "init", "--dir", "./prompts"],
                          cwd=str(tmp_path), env=_blank_env(), capture_output=True,
                          text=True, encoding="utf-8", timeout=60)
    assert proc.returncode == 0, proc.stderr
    text = (DOCS / "quickstart.md").read_text(encoding="utf-8")
    for line in proc.stdout.splitlines():
        if line.strip():
            assert line.rstrip() in text, f"quickstart does not show {line!r}"


def test_formats_page_matches_compare_dict_defaults():
    text = (DOCS / "reference" / "formats.md").read_text(encoding="utf-8")
    assert "must set `temperature` and `max_tokens` explicitly" not in text
    assert "same defaults as `load_config()`" in text


def test_ci_gating_plain_workflow_uploads_the_junit_file():
    text = (DOCS / "recipes" / "ci-gating.md").read_text(encoding="utf-8")
    block = text.split("## Without the action", 1)[1].split("```yaml", 1)[1].split("```", 1)[0]
    wf = yaml.safe_load(block)
    run = next(s for s in wf if "promptdrift diff" in s.get("run", ""))
    upload = next(s for s in wf if "upload-artifact" in s.get("uses", ""))
    written = re.findall(r"--(?:output|junit) (\S+)", run["run"])
    assert len(written) == 2
    patterns = upload["with"]["path"].split()
    for name in written:
        assert any(fnmatch.fnmatch(name, pat) for pat in patterns), name


@pytest.mark.parametrize("page", ["action/README.md", "docs/reference/action.md",
                                  "docs/recipes/ci-gating.md"])
def test_action_docs_say_artifact_names_must_be_unique(page):
    text = " ".join((ROOT / page).read_text(encoding="utf-8").split())
    assert "unique within a workflow run" in text


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
    try:
        _offline_guard(argv)
    except AssertionError as e:
        raise AssertionError(f"{page}: {e}") from None
    result = subprocess.run(argv, capture_output=True, text=True, env=_blank_env(),
                            cwd=docs_cwd, timeout=300)
    assert result.returncode == 0, (
        f"{page}: `{command}` exited {result.returncode}\n"
        f"stdout:\n{result.stdout[-2000:]}\nstderr:\n{result.stderr[-2000:]}")


@pytest.mark.parametrize("command", [
    "promptdrift compare a.yaml b.yaml --cases c.json",
    "promptdrift watch --once compare examples/live/v1.yaml examples/live/v2.yaml --cases x",
    "promptdrift watch compare a.yaml b.yaml --cases c.json",
    "promptdrift diff out.jsonl --judge",
])
def test_offline_guard_rejects_live_commands(command):
    with pytest.raises(AssertionError):
        _offline_guard(shlex.split(command))


def test_offline_guard_accepts_offline_commands():
    _offline_guard(shlex.split("promptdrift watch --once diff examples/outputs.jsonl --summary"))
