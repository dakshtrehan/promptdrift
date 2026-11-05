"""Tests for the composite GitHub Action (action.yml) and action/summarize.py.

There is no Docker daemon here, so ``act`` cannot run. Instead a tiny mocked
runner parses action.yml, substitutes the handful of expressions the action
uses and executes its ``run`` steps with bash against a fake GitHub REST API
served from a thread. ``uses:`` steps (setup-python, upload-artifact) are
skipped. No network, no API keys, no model calls.

Limits of the substitution (kept deliberately small): it understands
``${{ inputs.x }}``, ``${{ github.action_path }}``, ``${{ github.token }}`` and
``${{ steps.x.outputs.y }}``; ``if:`` conditions run the step when they contain
``always()`` and otherwise only while no earlier step has failed. Any other
expression raises so a change to action.yml that needs more is noticed.
"""
from __future__ import annotations

import importlib.util
import io
import json
import os
import re
import shutil
import stat
import subprocess
import sys
import threading
import urllib.error
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[1]
ACTION_YML = ROOT / "action.yml"
OFFLINE_OUTPUTS = ROOT / "examples" / "offline" / "outputs.jsonl"
OFFLINE_CHECKS = ROOT / "examples" / "offline" / "checks.yaml"
MARKER = "<!-- promptdrift-report -->"
REPO = "octo/prompts"
PR = 7

spec = importlib.util.spec_from_file_location("summarize", ROOT / "action" / "summarize.py")
summarize = importlib.util.module_from_spec(spec)
spec.loader.exec_module(summarize)


# ── Fake GitHub API ───────────────────────────────────────────────────────────
class ApiState:
    def __init__(self):
        self.pages: list[list[dict]] = [[]]   # comment pages served by GET
        self.post_status = 201
        self.requests: list[dict] = []
        self.lock = threading.Lock()


class Handler(BaseHTTPRequestHandler):
    state: ApiState

    def log_message(self, *a):  # keep pytest output clean
        pass

    def _record(self):
        length = int(self.headers.get("Content-Length") or 0)
        body = self.rfile.read(length).decode("utf-8") if length else ""
        rec = {"method": self.command, "path": self.path, "headers": dict(self.headers),
               "body": json.loads(body) if body else None}
        with self.state.lock:
            self.state.requests.append(rec)
        return rec

    def _send(self, status, payload, headers=None):
        data = json.dumps(payload).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        for k, v in (headers or {}).items():
            self.send_header(k, v)
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self):
        self._record()
        url = urlparse(self.path)
        m = re.fullmatch(rf"/repos/{REPO}/issues/{PR}/comments", url.path)
        if not m:
            return self._send(404, {"message": "Not Found"})
        page = int(parse_qs(url.query).get("page", ["1"])[0])
        pages = self.state.pages
        headers = {}
        if page < len(pages):
            base = f"http://{self.headers['Host']}{url.path}"
            headers["Link"] = (f'<{base}?per_page=100&page={page + 1}>; rel="next", '
                               f'<{base}?per_page=100&page={len(pages)}>; rel="last"')
        self._send(200, pages[page - 1] if page <= len(pages) else [], headers)

    def do_POST(self):
        rec = self._record()
        if self.state.post_status != 201:
            return self._send(self.state.post_status, {"message": "Resource not accessible"})
        self._send(201, {"id": 999, "body": rec["body"]["body"]})

    def do_PATCH(self):
        rec = self._record()
        cid = int(self.path.rsplit("/", 1)[1])
        self._send(200, {"id": cid, "body": rec["body"]["body"]})


@pytest.fixture
def api():
    state = ApiState()
    handler = type("H", (Handler,), {"state": state})
    server = ThreadingHTTPServer(("127.0.0.1", 0), handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    state.url = f"http://127.0.0.1:{server.server_address[1]}"
    try:
        yield state
    finally:
        server.shutdown()
        server.server_close()


# ── Mocked composite runner ───────────────────────────────────────────────────
EXPR = re.compile(r"\$\{\{\s*(.*?)\s*\}\}")


def load_action():
    return yaml.safe_load(ACTION_YML.read_text(encoding="utf-8"))


def expressions(obj):
    """Every ${{ ... }} expression found anywhere in a YAML structure."""
    found = []
    if isinstance(obj, dict):
        for v in obj.values():
            found += expressions(v)
    elif isinstance(obj, list):
        for v in obj:
            found += expressions(v)
    elif isinstance(obj, str):
        found += EXPR.findall(obj)
    return found


class Runner:
    def __init__(self, workspace: Path, inputs: dict, env: dict, action_path: Path = ROOT):
        self.action = load_action()
        self.workspace = workspace
        self.action_path = action_path
        self.inputs = {k: v.get("default", "") for k, v in self.action["inputs"].items()}
        unknown = set(inputs) - set(self.inputs)
        assert not unknown, f"unknown inputs {unknown}"
        self.inputs.update(inputs)
        self.env = env
        self.steps: dict[str, dict] = {}
        self.log: list[tuple[str, str, int]] = []
        self.failed = False
        self.summary_path = workspace / "step_summary.md"
        self.output_path = workspace / "github_output.txt"
        self.summary_path.write_text("")

    def subst(self, text: str) -> str:
        def repl(m):
            expr = m.group(1)
            if expr == "github.action_path":
                return str(self.action_path)
            if expr == "github.token":
                return "test-token"
            m2 = re.fullmatch(r"inputs\.([\w-]+)", expr)
            if m2:
                return self.subst(str(self.inputs[m2.group(1)]))  # defaults may hold expressions
            m3 = re.fullmatch(r"steps\.([\w-]+)\.outputs\.([\w-]+)", expr)
            if m3:
                return self.steps.get(m3.group(1), {}).get(m3.group(2), "")
            raise AssertionError(f"unsupported expression in action.yml: {expr}")
        return EXPR.sub(repl, text)

    def should_run(self, step) -> bool:
        cond = step.get("if")
        if cond is None:
            return not self.failed
        cond = str(cond)
        if "always()" in cond:
            rest = EXPR.sub(lambda m: m.group(1), cond).replace("always()", "").strip(" &")
            if rest:
                m = re.fullmatch(r"inputs\.([\w-]+)\s*==\s*'([^']*)'", rest)
                assert m, f"unsupported if condition: {cond}"
                return self.inputs[m.group(1)] == m.group(2)
            return True
        raise AssertionError(f"unsupported if condition: {cond}")

    def run(self):
        for step in self.action["runs"]["steps"]:
            sid = step.get("id") or step["name"]
            if not self.should_run(step):
                self.log.append((sid, "skipped", -1))
                continue
            if "uses" in step:
                self.log.append((sid, "uses:" + step["uses"], 0))
                continue
            assert step.get("shell") == "bash", f"step {sid} must declare shell: bash"
            env = dict(self.env)
            env.update({k: self.subst(str(v)) for k, v in (step.get("env") or {}).items()})
            env["GITHUB_OUTPUT"] = str(self.output_path)
            env["GITHUB_STEP_SUMMARY"] = str(self.summary_path)
            self.output_path.write_text("")
            script = self.subst(step["run"])
            proc = subprocess.run(["bash", "--noprofile", "--norc", "-eo", "pipefail", "-c",
                                   script], cwd=str(self.workspace), env=env,
                                  capture_output=True, text=True)
            self.steps[sid] = parse_outputs(self.output_path.read_text())
            self.steps[sid]["_stdout"] = proc.stdout
            self.steps[sid]["_stderr"] = proc.stderr
            self.log.append((sid, "run", proc.returncode))
            if proc.returncode != 0:
                self.failed = True
        return self


def parse_outputs(text: str) -> dict:
    out, lines, i = {}, text.splitlines(), 0
    while i < len(lines):
        line = lines[i]
        if "<<" in line and "=" not in line.split("<<")[0]:
            key, delim = line.split("<<", 1)
            buf = []
            i += 1
            while i < len(lines) and lines[i] != delim:
                buf.append(lines[i])
                i += 1
            out[key] = "\n".join(buf)
        elif "=" in line:
            key, value = line.split("=", 1)
            out[key] = value
        i += 1
    return out


def write_event(path: Path, pull_request: bool):
    event = {"pull_request": {"number": PR, "title": "evil $(rm -rf /) `x`"},
             "number": PR} if pull_request else {"ref": "refs/heads/main"}
    path.write_text(json.dumps(event))


@pytest.fixture
def workspace(tmp_path, api):
    ws = tmp_path / "ws"
    (ws / "data").mkdir(parents=True)
    shutil.copy(OFFLINE_OUTPUTS, ws / "data" / "outputs.jsonl")
    shutil.copy(OFFLINE_CHECKS, ws / "data" / "checks.yaml")
    event = tmp_path / "event.json"
    write_event(event, pull_request=True)
    env = dict(os.environ)
    env.update({
        "OPENAI_API_KEY": "", "ANTHROPIC_API_KEY": "",
        "NO_PROXY": "127.0.0.1,localhost", "no_proxy": "127.0.0.1,localhost",
        "GITHUB_WORKSPACE": str(ws), "GITHUB_EVENT_PATH": str(event),
        "GITHUB_EVENT_NAME": "pull_request", "GITHUB_REPOSITORY": REPO,
        "GITHUB_RUN_ID": "123", "GITHUB_SERVER_URL": "https://github.example",
        "GITHUB_API_URL": api.url, "GITHUB_ENV": str(tmp_path / "github_env.txt"),
        "PYTHONPATH": str(ROOT),
    })
    return ws, env, event


def offline_inputs(**extra):
    base = {"outputs": "data/outputs.jsonl", "checks": "data/checks.yaml", "install": "skip",
            "extra-args": "--name-a v1 --name-b v2 --bootstrap 50"}
    base.update(extra)
    return base


# ── action.yml static checks ──────────────────────────────────────────────────
def test_action_metadata_and_declared_inputs():
    a = load_action()
    assert a["name"] == "promptdrift" and a["branding"] == {"icon": "activity", "color": "orange"}
    assert a["runs"]["using"] == "composite"
    declared = set(a["inputs"])
    expected = {"outputs", "config-a", "config-b", "cases", "checks", "samples", "judge",
                "judge-provider", "judge-model", "similarity", "max-drift", "max-regressions",
                "max-format-changes", "max-flips", "fail-on-regression", "comment",
                "comment-marker", "step-summary", "artifact-name", "upload-artifact",
                "report-dir", "python-version", "github-token", "working-directory",
                "extra-args", "install"}
    assert declared == expected
    for name, spec_ in a["inputs"].items():
        assert "description" in spec_ and "default" in spec_, name
    used = {name for e in expressions(a["runs"]) for name in re.findall(r"inputs\.([\w-]+)", e)}
    assert used <= declared, used - declared
    assert declared - used == set(), f"declared but unused inputs: {declared - used}"
    for e in expressions(a["runs"]):
        assert re.fullmatch(r"inputs\.[\w-]+|github\.action_path|steps\.[\w-]+\.outputs\.[\w-]+"
                            r"|always\(\).*", e), e
    assert "set-output" not in ACTION_YML.read_text()
    assert chr(0x2014) not in ACTION_YML.read_text() and chr(0x2013) not in ACTION_YML.read_text()


def test_every_declared_output_is_set_by_summarize(tmp_path):
    a = load_action()
    expected = {"verdict", "avg-drift", "regression-rate", "regressions", "improvements",
                "format-changes", "json-broken", "flips", "inconsistent", "report-json",
                "report-html", "report-junit", "threshold-failures", "comment-id"}
    assert set(a["outputs"]) == expected
    written = set(summarize.build_outputs(None, 0, [], str(tmp_path)))
    for name, spec_ in a["outputs"].items():
        m = re.fullmatch(r"\$\{\{ steps\.summarize\.outputs\.([\w-]+) \}\}", spec_["value"])
        assert m, name
        assert m.group(1) == name and name in written


def test_example_workflow_is_valid_and_informational():
    wf = yaml.safe_load((ROOT / ".github/workflows/promptdrift-example.yml").read_text())
    assert set(wf[True]) == {"pull_request", "workflow_dispatch"}  # YAML parses "on" as True
    assert wf["permissions"] == {"contents": "read", "pull-requests": "write"}
    step = next(s for s in wf["jobs"]["drift"]["steps"] if s.get("uses") == "./")
    assert step["with"]["outputs"] == "examples/offline/outputs.jsonl"
    assert Path(ROOT / step["with"]["outputs"]).exists()
    assert step["with"]["fail-on-regression"] == "false" and step["with"]["max-drift"] == "0.6"
    assert step["env"] == {"OPENAI_API_KEY": "", "ANTHROPIC_API_KEY": ""}
    assert wf["jobs"]["use-outputs"]["needs"] == "drift"
    assert "needs.drift.outputs.verdict" in json.dumps(wf["jobs"]["use-outputs"])


# ── Full mocked runs ──────────────────────────────────────────────────────────
def test_run_updates_existing_comment_and_passes(workspace, api):
    ws, env, _ = workspace
    api.pages = [[{"id": 11, "body": "unrelated"}, {"id": 42, "body": f"{MARKER}\nold"}]]
    r = Runner(ws, offline_inputs(**{"fail-on-regression": "false", "max-drift": "0.9"}),
               env).run()
    assert [(s, rc) for s, _, rc in r.log] == [
        ("Set up Python", 0), ("install", 0), ("run", 0), ("summarize", 0),
        ("Upload report artifact", 0), ("Fail on thresholds", 0)]
    assert r.steps["run"]["exit-code"] == "0"
    report_dir = Path(r.steps["run"]["report-dir"])
    assert report_dir == ws / "promptdrift-report"
    for name in ("report.json", "report.html", "report.junit.xml", "report.csv"):
        assert (report_dir / name).stat().st_size > 0, name
    assert not (report_dir / "threshold-failures.txt").exists()
    out = r.steps["summarize"]
    assert set(out) - {"_stdout", "_stderr"} == set(load_action()["outputs"])
    assert out["verdict"] == "fail"  # the example data has 2 regressions, so DO NOT SHIP
    assert out["regressions"] == "2" and out["flips"] == "2" and out["threshold-failures"] == "0"
    assert out["report-html"] == str(report_dir / "report.html")
    assert out["comment-id"] == "42"
    methods = [(q["method"], q["path"].split("?")[0]) for q in api.requests]
    assert methods == [("GET", f"/repos/{REPO}/issues/{PR}/comments"),
                       ("PATCH", f"/repos/{REPO}/issues/comments/42")]
    patched = api.requests[1]
    assert patched["headers"]["Authorization"] == "Bearer test-token"
    assert patched["headers"]["Accept"] == "application/vnd.github+json"
    body = patched["body"]["body"]
    assert body.startswith(MARKER) and "DO NOT SHIP" in body
    assert f"https://github.example/{REPO}/actions/runs/123" in body
    assert "evil" not in body  # nothing from the PR payload reaches the comment
    summary = r.summary_path.read_text()
    assert "DO NOT SHIP" in summary and "| Avg drift |" in summary
    assert "all thresholds passed" in r.steps["Fail on thresholds"]["_stdout"]


def test_run_posts_new_comment_and_fails_on_threshold(workspace, api):
    ws, env, _ = workspace
    api.pages = [[{"id": 1, "body": "first page, no marker"}], []]  # Link header pagination
    r = Runner(ws, offline_inputs(**{"max-drift": "0.01", "max-flips": "0",
                                     "report-dir": "out/reports"}), env).run()
    assert r.steps["run"]["exit-code"] == "1"
    report_dir = Path(r.steps["run"]["report-dir"])
    assert report_dir == ws / "out" / "reports" and (report_dir / "report.json").exists()
    failures = (report_dir / "threshold-failures.txt").read_text().splitlines()
    assert any("exceeds max-drift 1.0%" in f for f in failures)
    assert any("exceed max-flips 0" in f for f in failures)
    assert any("fail-on-regression is set" in f for f in failures)
    out = r.steps["summarize"]
    assert out["verdict"] == "fail" and out["threshold-failures"] == str(len(failures))
    assert out["comment-id"] == "999"
    methods = [(q["method"], q["path"]) for q in api.requests]
    assert methods[0] == ("GET", f"/repos/{REPO}/issues/{PR}/comments?per_page=100")
    assert methods[1] == ("GET", f"/repos/{REPO}/issues/{PR}/comments?per_page=100&page=2")
    assert methods[2] == ("POST", f"/repos/{REPO}/issues/{PR}/comments")
    assert "**Threshold failures**" in api.requests[2]["body"]["body"]
    final = r.log[-1]
    assert final[0] == "Fail on thresholds" and final[2] == 1
    assert "exceeds max-drift" in r.steps["Fail on thresholds"]["_stdout"]
    assert "::error::" in out["_stdout"]


def test_push_event_skips_comment_and_summary_can_be_off(workspace, api):
    ws, env, event = workspace
    write_event(event, pull_request=False)
    env["GITHUB_EVENT_NAME"] = "push"
    r = Runner(ws, offline_inputs(**{"fail-on-regression": "false", "step-summary": "false"}),
               env).run()
    assert api.requests == []
    assert "skipping the PR comment" in r.steps["summarize"]["_stdout"]
    assert r.steps["summarize"]["comment-id"] == ""
    assert r.summary_path.read_text() == ""
    assert r.log[-1][2] == 0


def test_fork_pr_403_is_a_warning_not_a_failure(workspace, api):
    ws, env, _ = workspace
    api.post_status = 403
    r = Runner(ws, offline_inputs(**{"fail-on-regression": "false"}), env).run()
    assert [q["method"] for q in api.requests] == ["GET", "POST"]
    out = r.steps["summarize"]
    assert "::warning::" in out["_stdout"] and "fork" in out["_stdout"]
    assert out["comment-id"] == "" and r.log[-1][2] == 0


def test_comment_and_upload_can_be_disabled(workspace, api):
    ws, env, _ = workspace
    r = Runner(ws, offline_inputs(**{"fail-on-regression": "false", "comment": "false",
                                     "upload-artifact": "false"}), env).run()
    assert api.requests == []
    assert ("Upload report artifact", "skipped", -1) in r.log


def test_missing_inputs_fail_cleanly(workspace, api):
    ws, env, _ = workspace
    r = Runner(ws, {"install": "skip"}, env).run()
    assert r.steps["run"]["exit-code"] == "2"
    assert "::error::" in r.steps["run"]["_stdout"]
    out = r.steps["summarize"]
    assert out["verdict"] == "fail" and out["report-json"] == "" and out["regressions"] == ""
    body = api.requests[-1]["body"]["body"]
    assert "exited with code 2 and produced no report.json" in body
    assert r.log[-1][2] == 2


def test_unknown_install_mode_fails(workspace, api):
    ws, env, _ = workspace
    r = Runner(ws, offline_inputs(install="pipx"), env).run()
    assert r.log[1] == ("install", "run", 1)
    assert r.log[2] == ("run", "skipped", -1)
    assert r.log[-1] == ("Fail on thresholds", "run", 1)  # no exit code means failure


def test_live_mode_builds_the_compare_command(workspace, api, tmp_path):
    """A fake promptdrift on PATH records argv, so no provider is ever called."""
    ws, env, _ = workspace
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    fake = bin_dir / "promptdrift"
    fake.write_text("#!/usr/bin/env python3\n"
                    "import json, sys, os\n"
                    "args = sys.argv[1:]\n"
                    "if args == ['--version']:\n"
                    "    print('promptdrift fake'); sys.exit(0)\n"
                    "open(os.environ['FAKE_ARGV'], 'w').write(json.dumps(args))\n"
                    "out = args[args.index('--output') + 1]\n"
                    "json.dump({'version_a': 'v1', 'version_b': 'v2', 'model_a': 'm', 'model_b': 'm',"
                    " 'total_cases': 0, 'mode': 'live', 'similarity': 'tfidf', 'summary': {}, 'cases': []},"
                    " open(out, 'w'))\n"
                    "sys.exit(0)\n")
    fake.chmod(fake.stat().st_mode | stat.S_IXUSR)
    env["PATH"] = f"{bin_dir}{os.pathsep}{env['PATH']}"
    env["FAKE_ARGV"] = str(tmp_path / "argv.json")
    r = Runner(ws, {"install": "skip", "config-a": "p/v1.yaml", "config-b": "p/v2.yaml",
                    "cases": "p/golden.json", "samples": "3", "judge-provider": "ollama",
                    "judge-model": "llama3", "similarity": "embedding", "checks": "c.yaml",
                    "max-regressions": "1", "max-format-changes": "2", "max-flips": "3",
                    "max-drift": "0.5", "extra-args": "--seed 7 --quiet", "comment": "false"},
               env).run()
    argv = json.loads((tmp_path / "argv.json").read_text())
    rd = str(ws / "promptdrift-report")
    assert argv == ["compare", "p/v1.yaml", "p/v2.yaml", "--cases", "p/golden.json",
                    "--similarity", "embedding", "--samples", "3", "--no-judge",
                    "--judge-provider", "ollama",
                    "--judge-model", "llama3", "--checks", "c.yaml", "--max-drift", "0.5",
                    "--max-regressions", "1", "--max-format-changes", "2", "--max-flips", "3",
                    "--fail-on-regression", "--output", f"{rd}/report.json", "--html",
                    f"{rd}/report.html", "--junit", f"{rd}/report.junit.xml", "--csv",
                    f"{rd}/report.csv", "--seed", "7", "--quiet"]
    assert r.steps["summarize"]["verdict"] == "ship"
    # judge off in live mode passes --no-judge; offline judge on passes --judge
    Runner(ws, {"install": "skip", "config-a": "a", "config-b": "b", "cases": "c",
                "judge": "false", "comment": "false"}, env).run()
    assert "--no-judge" in json.loads((tmp_path / "argv.json").read_text())
    Runner(ws, {"install": "skip", "outputs": "data/outputs.jsonl", "judge": "true",
                "comment": "false"}, env).run()
    argv = json.loads((tmp_path / "argv.json").read_text())
    assert argv[:2] == ["diff", "data/outputs.jsonl"] and "--judge" in argv


def test_working_directory_input(workspace, api):
    ws, env, _ = workspace
    r = Runner(ws, offline_inputs(**{"working-directory": "data", "outputs": "outputs.jsonl",
                                     "checks": "checks.yaml", "fail-on-regression": "false",
                                     "comment": "false"}), env).run()
    assert Path(r.steps["run"]["report-dir"]) == ws / "data" / "promptdrift-report"
    assert (ws / "data" / "promptdrift-report" / "report.html").exists()


# ── summarize.py unit tests ───────────────────────────────────────────────────
def test_load_report_variants(tmp_path):
    assert summarize.load_report(str(tmp_path / "missing.json")) is None
    bad = tmp_path / "bad.json"
    bad.write_text("{not json")
    assert summarize.load_report(str(bad)) is None
    arr = tmp_path / "arr.json"
    arr.write_text("[1, 2]")
    assert summarize.load_report(str(arr)) is None
    good = tmp_path / "good.json"
    good.write_text('{"summary": {"avg_drift": 0.1}}')
    assert summarize.load_report(str(good)) == {"summary": {"avg_drift": 0.1}}


def test_build_outputs_verdicts(tmp_path):
    safe = {"summary": {"avg_drift": 0.01, "regression_rate": 0.0, "regressions": 0,
                        "improvements": 1, "structural": {"format_changes": 0, "json_broken": 0},
                        "checks": {"pass_to_fail": 0}, "judge": {"inconsistent": 0}}}
    out = summarize.build_outputs(safe, 0, [], str(tmp_path))
    assert out["verdict"] == "ship" and out["avg-drift"] == "0.01" and out["report-json"] == ""
    (tmp_path / "report.json").write_text("{}")
    assert summarize.build_outputs(safe, 0, [], str(tmp_path))["report-json"] == \
        str(tmp_path / "report.json")
    review = {"summary": {"avg_drift": 0.2, "regressions": 0,
                          "structural": {"format_changes": 0}},
              "cases": [{"case_id": "c", "input": "i", "output_a": "a", "output_b": "b",
                         "similarity": 0.8, "drift_score": 0.2, "drift_level": "moderate"}]}
    assert summarize.build_outputs(review, 0, [], str(tmp_path))["verdict"] == "review"
    assert summarize.build_outputs(review, 0, ["x"], str(tmp_path))["verdict"] == "fail"
    assert summarize.build_outputs(safe, 3, [], str(tmp_path))["verdict"] == "fail"
    assert summarize.build_outputs({"summary": {"regressions": 1}}, 0, [],
                                   str(tmp_path))["verdict"] == "fail"
    assert summarize.build_outputs(None, 0, [], str(tmp_path))["verdict"] == "fail"


def test_verdict_status_fallback_without_promptdrift(monkeypatch):
    monkeypatch.setitem(sys.modules, "promptdrift.markdown", None)
    monkeypatch.setitem(sys.modules, "promptdrift.reporter", None)
    monkeypatch.setitem(sys.modules, "promptdrift.thresholds", None)
    assert summarize.verdict_status({"summary": {"regressions": 1}}) == "do_not_ship"
    assert summarize.verdict_status({"summary": {"avg_drift": 0.05}}) == "review"
    assert summarize.verdict_status({"summary": {"structural": {"format_changes": 1}}}) == "review"
    assert summarize.verdict_status({"summary": {"judge": {"inconsistent": 1}}}) == "review"
    assert summarize.verdict_status({"summary": {"avg_drift": 0.01}}) == "safe"
    assert summarize.threshold_failures({"summary": {}}, {"INPUT_MAX_DRIFT": "0"}) == []
    md = summarize.build_markdown({"summary": {"avg_drift": 0.5, "regressions": 1}}, 0, ["boom"],
                                  MARKER, "https://run", artifact_name="art")
    assert md.startswith(MARKER) and "DO NOT SHIP" in md and "- boom" in md
    assert "| Avg drift | 50.0% |" in md and "(https://run)" in md and "`art`" in md


def test_threshold_failures_from_env():
    rep = {"summary": {"avg_drift": 0.3, "regressions": 1, "structural": {"format_changes": 2},
                       "checks": {"pass_to_fail": 1}}}
    env = {"INPUT_MAX_DRIFT": " 0.1 ", "INPUT_MAX_REGRESSIONS": "0", "INPUT_MAX_FORMAT_CHANGES": "",
           "INPUT_MAX_FLIPS": "0", "INPUT_FAIL_ON_REGRESSION": "true"}
    f = summarize.threshold_failures(rep, env)
    assert len(f) == 4 and f[0] == "avg drift 30.0% exceeds max-drift 10.0%"
    assert summarize.threshold_failures(None, env) == []
    assert summarize.threshold_failures(rep, {}) == []


def test_write_github_output_and_summary(tmp_path):
    out = tmp_path / "out.txt"
    summarize.write_github_output({"a": "1", "b": "x\ny", "c": None}, str(out))
    assert parse_outputs(out.read_text()) == {"a": "1", "b": "x\ny", "c": ""}
    assert "b<<PROMPTDRIFT_EOF\nx\ny\nPROMPTDRIFT_EOF\n" in out.read_text()
    summarize.write_github_output({"d": "PROMPTDRIFT_EOF\nz"}, str(out))
    assert "d<<PROMPTDRIFT_EOF_\n" in out.read_text()
    summarize.write_github_output({"a": "1"}, None)  # no-op without the env var
    summ = tmp_path / "summary.md"
    summarize.write_step_summary("# hi", str(summ))
    summarize.write_step_summary("more\n", str(summ))
    assert summ.read_text() == "# hi\nmore\n"
    summarize.write_step_summary("x", None)


def test_next_link_parsing():
    link = ('<https://api.github.com/x?page=2>; rel="next", '
            '<https://api.github.com/x?page=9>; rel="last"')
    assert summarize._next_link(link) == "https://api.github.com/x?page=2"
    assert summarize._next_link('<https://api.github.com/x?page=1>; rel="prev"') is None
    assert summarize._next_link(None) is None
    assert summarize._next_link("<https://h/x?page=3>; rel=next") == "https://h/x?page=3"


class FakeRequest:
    """Scripted ``request(method, url, headers, body)`` for the REST helpers."""

    def __init__(self, pages, post_error=None, post_id=5):
        self.pages = pages
        self.post_error = post_error
        self.post_id = post_id
        self.calls = []

    def __call__(self, method, url, headers, body):
        self.calls.append((method, url, dict(headers), body))
        if method == "GET":
            page = int(parse_qs(urlparse(url).query).get("page", ["1"])[0])
            headers_out = {}
            if page < len(self.pages):
                headers_out["Link"] = f'<{url.split("?")[0]}?per_page=100&page={page + 1}>; rel="next"'
            return 200, headers_out, json.dumps(self.pages[page - 1]).encode()
        if self.post_error is not None:
            raise self.post_error
        return 201 if method == "POST" else 200, {}, json.dumps({"id": self.post_id}).encode()


def http_error(code):
    return urllib.error.HTTPError("https://api", code, "nope", {}, io.BytesIO(b"{}"))


def test_find_existing_comment_pages_until_marker():
    req = FakeRequest([[{"id": 1, "body": "a"}], [{"id": 2, "body": None}, {"id": 3, "body": MARKER}]])
    found = summarize.find_existing_comment("https://api/", REPO, PR, MARKER, "tok", req)
    assert found["id"] == 3 and len(req.calls) == 2
    assert req.calls[0][1] == f"https://api/repos/{REPO}/issues/{PR}/comments?per_page=100"
    assert req.calls[0][2]["Authorization"] == "Bearer tok"
    assert "X-GitHub-Api-Version" in req.calls[0][2]
    req = FakeRequest([[{"id": 1, "body": "a"}]])
    assert summarize.find_existing_comment("https://api", REPO, PR, MARKER, "", req) is None
    assert "Authorization" not in req.calls[0][2]


def test_upsert_comment_paths():
    req = FakeRequest([[{"id": 3, "body": MARKER}]], post_id=3)
    assert summarize.upsert_comment("https://api", REPO, PR, "body", MARKER, "t", req) == 3
    assert req.calls[-1][0] == "PATCH" and req.calls[-1][1].endswith("/issues/comments/3")
    assert json.loads(req.calls[-1][3]) == {"body": "body"}
    req = FakeRequest([[]], post_id=8)
    assert summarize.upsert_comment("https://api", REPO, PR, "body", MARKER, "t", req) == 8
    assert req.calls[-1][0] == "POST"
    for code in (401, 403, 404):
        req = FakeRequest([[]], post_error=http_error(code))
        assert summarize.upsert_comment("https://api", REPO, PR, "b", MARKER, "t", req) is None
    req = FakeRequest([[]], post_error=urllib.error.URLError("down"))
    assert summarize.upsert_comment("https://api", REPO, PR, "b", MARKER, "t", req) is None
    req = FakeRequest([[]], post_error=http_error(500))
    with pytest.raises(urllib.error.HTTPError):
        summarize.upsert_comment("https://api", REPO, PR, "b", MARKER, "t", req)


def test_pr_number_from_event(tmp_path):
    p = tmp_path / "e.json"
    assert summarize.pr_number_from_event(None) is None
    assert summarize.pr_number_from_event(str(p)) is None  # missing file
    p.write_text("nope")
    assert summarize.pr_number_from_event(str(p)) is None
    p.write_text(json.dumps({"issue": {"number": 4}}))
    assert summarize.pr_number_from_event(str(p)) == 4
    p.write_text(json.dumps({"number": 9}))
    assert summarize.pr_number_from_event(str(p)) == 9
    p.write_text(json.dumps({"number": "9"}))
    assert summarize.pr_number_from_event(str(p)) is None


def test_build_markdown_without_report_and_truncation():
    md = summarize.build_markdown(None, 1, [], MARKER, "https://run")
    assert md.startswith(MARKER) and "exited with code 1" in md and "https://run" in md
    md = summarize.build_markdown(None, 1, [], None, None)
    assert md.startswith("## promptdrift report")
    big = {"version_a": "v1", "version_b": "v2", "model_a": "m", "model_b": "m",
           "total_cases": 400, "mode": "offline", "similarity": "tfidf", "summary": {},
           "cases": [{"case_id": f"c{i}", "input": "i", "output_a": "x" * 150,
                      "output_b": "y" * 150, "similarity": 0.0, "drift_score": 1.0,
                      "drift_level": "major"} for i in range(400)]}
    monkey = summarize.MAX_COMMENT_CHARS
    summarize.MAX_COMMENT_CHARS = 3000
    try:
        md = summarize.build_markdown(big, 0, [], MARKER, None)
    finally:
        summarize.MAX_COMMENT_CHARS = monkey
    assert len(md) <= 3100 and md.endswith("full report)_\n") and "<details>" not in md


def test_main_with_fake_request_and_env(tmp_path, capsys):
    report_dir = tmp_path / "rep"
    report_dir.mkdir()
    (report_dir / "report.json").write_text(json.dumps(
        {"version_a": "v1", "version_b": "v2", "model_a": "m", "model_b": "m", "total_cases": 1,
         "mode": "offline", "similarity": "tfidf",
         "summary": {"avg_drift": 0.4, "regression_rate": 0.0, "regressions": 0,
                     "improvements": 0, "neutral": 1, "structural": {"format_changes": 0,
                                                                      "json_broken": 0}},
         "cases": [{"case_id": "c", "input": "i", "output_a": "a", "output_b": "b",
                    "similarity": 0.6, "drift_score": 0.4, "drift_level": "moderate"}]}))
    event = tmp_path / "event.json"
    event.write_text(json.dumps({"pull_request": {"number": 3}}))
    env = {"GITHUB_OUTPUT": str(tmp_path / "out.txt"),
           "GITHUB_STEP_SUMMARY": str(tmp_path / "sum.md"), "GITHUB_EVENT_PATH": str(event),
           "GITHUB_REPOSITORY": "o/r", "GITHUB_RUN_ID": "5", "GITHUB_API_URL": "https://api",
           "INPUT_MAX_DRIFT": "0.1", "INPUT_GITHUB_TOKEN": "t", "INPUT_COMMENT_MARKER": "<!-- m -->"}
    req = FakeRequest([[{"id": 21, "body": "<!-- m --> old"}]], post_id=21)
    assert summarize.main(["--report-dir", str(report_dir), "--exit-code", "1"], env, req) == 0
    out = parse_outputs((tmp_path / "out.txt").read_text())
    assert out["verdict"] == "fail" and out["threshold-failures"] == "1" and out["comment-id"] == "21"
    assert (report_dir / "threshold-failures.txt").read_text() == \
        "avg drift 40.0% exceeds max-drift 10.0%\n"
    assert "<!-- m -->" in (tmp_path / "sum.md").read_text()
    assert "https://github.com/o/r/actions/runs/5" in json.loads(req.calls[-1][3])["body"]
    assert "::error::promptdrift: avg drift 40.0%" in capsys.readouterr().out

    # no token: comment skipped with a warning; no repository: notice
    env2 = dict(env, INPUT_GITHUB_TOKEN="", GITHUB_TOKEN="")
    req2 = FakeRequest([[]])
    summarize.main(["--report-dir", str(report_dir)], env2, req2)
    assert req2.calls == [] and "no github-token" in capsys.readouterr().out
    env3 = dict(env, GITHUB_REPOSITORY="")
    summarize.main(["--report-dir", str(report_dir)], env3, req2)
    assert req2.calls == [] and "GITHUB_REPOSITORY is not set" in capsys.readouterr().out
    # unwritable failures file is only logged
    env4 = dict(env)
    assert summarize.main(["--report-dir", str(tmp_path / "nope")], env4, req2) == 0


def test_main_reads_defaults_from_environment(tmp_path, monkeypatch):
    monkeypatch.setenv("INPUT_REPORT_DIR", str(tmp_path))
    monkeypatch.setenv("PROMPTDRIFT_EXIT_CODE", "0")
    monkeypatch.setenv("GITHUB_OUTPUT", str(tmp_path / "o.txt"))
    monkeypatch.setenv("INPUT_COMMENT", "false")
    monkeypatch.setenv("INPUT_STEP_SUMMARY", "false")
    monkeypatch.delenv("GITHUB_STEP_SUMMARY", raising=False)
    assert summarize.main([]) == 0
    assert parse_outputs((tmp_path / "o.txt").read_text())["verdict"] == "fail"  # no report
