#!/usr/bin/env python3
"""
Summarise a promptdrift run for GitHub Actions.

Run by the composite action after ``promptdrift`` has written ``report.json``
into the report directory. Pure standard library, Python 3.9+, and importable
so the pieces can be unit tested:

* ``load_report``          read report.json (None when it is missing or broken)
* ``build_outputs``        the action's outputs as a flat ``{name: str}`` dict
* ``write_github_output``  append ``name=value`` lines to ``$GITHUB_OUTPUT``
* ``write_step_summary``   append Markdown to ``$GITHUB_STEP_SUMMARY``
* ``find_existing_comment`` page through a PR's issue comments for the marker
* ``upsert_comment``       PATCH the marked comment or POST a new one
* ``main(argv)``           glue, driven by ``INPUT_*`` and ``GITHUB_*`` env vars

Everything that reaches the comment body comes from report.json and from the
``GITHUB_*`` variables read here in Python; nothing from the pull request
(title, branch name, body) is interpolated anywhere. The Markdown comes from
``promptdrift.markdown.render_markdown`` when promptdrift is importable
(the action installs it) and from a minimal table otherwise.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
import urllib.error
import urllib.parse
import urllib.request
from typing import Any, Callable, Mapping, Optional, Tuple

API_VERSION = "2022-11-28"
USER_AGENT = "promptdrift-action"
MAX_COMMENT_CHARS = 65000  # GitHub rejects bodies over 65536 characters
DEFAULT_MARKER = "<!-- promptdrift-report -->"
REPORT_FILES = {
    "report-json": "report.json",
    "report-html": "report.html",
    "report-junit": "report.junit.xml",
    "report-csv": "report.csv",
}
FAILURES_FILE = "threshold-failures.txt"

Requester = Callable[[str, str, Mapping[str, str], Optional[bytes]],
                     Tuple[int, Mapping[str, str], bytes]]


def log(msg: str) -> None:
    print(f"[promptdrift] {msg}", file=sys.stderr)


def warn(msg: str) -> None:
    print(f"::warning::promptdrift: {msg}")


def notice(msg: str) -> None:
    print(f"::notice::promptdrift: {msg}")


def truthy(value: Optional[str]) -> bool:
    return str(value or "").strip().lower() in ("1", "true", "yes", "on")


def _opt_float(value: Optional[str]) -> Optional[float]:
    value = (value or "").strip()
    return float(value) if value else None


def _opt_int(value: Optional[str]) -> Optional[int]:
    value = (value or "").strip()
    return int(value) if value else None


# ── Report ────────────────────────────────────────────────────────────────────
def load_report(path: str) -> Optional[dict]:
    """Read report.json. Returns None (and logs) when it is missing or not an object."""
    try:
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
    except FileNotFoundError:
        log(f"no report at {path}")
        return None
    except (OSError, ValueError) as e:
        log(f"could not read {path}: {e}")
        return None
    if not isinstance(data, dict):
        log(f"{path} does not hold a report object")
        return None
    return data


def threshold_failures(report: Optional[dict], env: Mapping[str, str]) -> list[str]:
    """Re-evaluate the threshold inputs against report.json (empty without promptdrift)."""
    if report is None:
        return []
    try:
        from promptdrift.thresholds import evaluate_thresholds
    except ImportError:
        return []
    return evaluate_thresholds(
        report,
        max_drift=_opt_float(env.get("INPUT_MAX_DRIFT")),
        max_regressions=_opt_int(env.get("INPUT_MAX_REGRESSIONS")),
        max_format_changes=_opt_int(env.get("INPUT_MAX_FORMAT_CHANGES")),
        max_flips=_opt_int(env.get("INPUT_MAX_FLIPS")),
        fail_on_regression=truthy(env.get("INPUT_FAIL_ON_REGRESSION")),
    )


def verdict_status(report: dict) -> str:
    """``safe`` / ``review`` / ``do_not_ship`` from a report dict, matching reporter.verdict_for."""
    try:
        from promptdrift.markdown import report_from_dict
        from promptdrift.reporter import verdict_for
        return verdict_for(report_from_dict(report))[0]
    except ImportError:
        s = report.get("summary") or {}
        if int(s.get("regressions") or 0) > 0:
            return "do_not_ship"
        structural = s.get("structural") or {}
        judge = s.get("judge") or {}
        if (round(float(s.get("avg_drift") or 0.0) * 100, 1) >= 5
                or int(structural.get("format_changes") or 0) > 0
                or int(judge.get("inconsistent") or 0) > 0):
            return "review"
        return "safe"


def build_outputs(report: Optional[dict], exit_code: int, failures: list[str],
                  report_dir: str) -> dict[str, str]:
    """The action's outputs. ``verdict`` is ship, review or fail."""
    s = (report or {}).get("summary") or {}
    structural = s.get("structural") or {}
    checks = s.get("checks") or {}
    judge = s.get("judge") or {}
    if report is None:
        verdict = "fail"
    else:
        verdict = {"safe": "ship", "review": "review",
                   "do_not_ship": "fail"}[verdict_status(report)]
    if exit_code != 0 or failures:
        verdict = "fail"
    out = {
        "verdict": verdict,
        "avg-drift": str(s.get("avg_drift", "")),
        "regression-rate": str(s.get("regression_rate", "")),
        "regressions": str(s.get("regressions", "")),
        "improvements": str(s.get("improvements", "")),
        "format-changes": str(structural.get("format_changes", "")),
        "json-broken": str(structural.get("json_broken", "")),
        "flips": str(checks.get("pass_to_fail", 0)),
        "inconsistent": str(judge.get("inconsistent", 0)),
        "threshold-failures": str(len(failures)),
        "comment-id": "",
    }
    for key, name in REPORT_FILES.items():
        if key == "report-csv":
            continue
        path = os.path.join(report_dir, name)
        out[key] = path if os.path.exists(path) else ""
    return out


# ── GitHub files ──────────────────────────────────────────────────────────────
def write_github_output(outputs: Mapping[str, str], path: Optional[str]) -> None:
    """Append outputs to ``$GITHUB_OUTPUT`` (multi-line values use the heredoc form)."""
    if not path:
        log("GITHUB_OUTPUT is not set; outputs not written")
        return
    with open(path, "a", encoding="utf-8") as f:
        for key, value in outputs.items():
            value = "" if value is None else str(value)
            if "\n" in value:
                delim = "PROMPTDRIFT_EOF"
                while delim in value:
                    delim += "_"
                f.write(f"{key}<<{delim}\n{value}\n{delim}\n")
            else:
                f.write(f"{key}={value}\n")


def write_step_summary(markdown: str, path: Optional[str]) -> None:
    """Append Markdown to ``$GITHUB_STEP_SUMMARY``."""
    if not path:
        log("GITHUB_STEP_SUMMARY is not set; summary not written")
        return
    with open(path, "a", encoding="utf-8") as f:
        f.write(markdown)
        if not markdown.endswith("\n"):
            f.write("\n")


# ── Markdown ──────────────────────────────────────────────────────────────────
def _minimal_markdown(report: dict, marker: Optional[str], run_url: Optional[str],
                      failures: list[str]) -> str:
    s = report.get("summary") or {}
    structural = s.get("structural") or {}
    checks = s.get("checks") or {}
    judge = s.get("judge") or {}

    def pct(v: Any) -> str:
        return "n/a" if v in (None, "") else f"{round(float(v) * 100, 1)}%"

    label = {"safe": "SAFE TO SHIP", "review": "REVIEW", "do_not_ship": "DO NOT SHIP"}
    status = verdict_status(report)
    lines = [marker or "", "## promptdrift report", "", f"**{label[status]}**", ""]
    if failures:
        lines += ["**Threshold failures**", ""] + [f"- {f}" for f in failures] + [""]
    lines += ["| Metric | Value |", "| --- | --- |",
              f"| Avg drift | {pct(s.get('avg_drift'))} |",
              f"| Regression rate | {pct(s.get('regression_rate'))} |",
              f"| Regressions | {s.get('regressions', 0)} |",
              f"| Improvements | {s.get('improvements', 0)} |",
              f"| Format changes | {structural.get('format_changes', 0)} |",
              f"| JSON broken | {structural.get('json_broken', 0)} |",
              f"| Check flips | {checks.get('pass_to_fail', 0)} |",
              f"| Judge inconsistent | {judge.get('inconsistent', 0)} |", ""]
    if run_url:
        lines.append(f"[Full HTML report in the run artifacts]({run_url})")
    return "\n".join(ln for ln in lines if ln is not None) + "\n"


def build_markdown(report: Optional[dict], exit_code: int, failures: list[str],
                   marker: Optional[str], run_url: Optional[str],
                   artifact_name: str = "") -> str:
    """Markdown for the comment and step summary."""
    if report is None:
        body = (f"{marker}\n\n" if marker else "") + "## promptdrift report\n\n"
        body += (f"❌ promptdrift exited with code {exit_code} and produced no "
                 "report.json. Check the run log.")
        if run_url:
            body += f"\n\n[Workflow run]({run_url})"
        return body + "\n"
    try:
        from promptdrift.markdown import render_markdown
    except ImportError:
        md = _minimal_markdown(report, marker, run_url, failures)
    else:
        md = render_markdown(report, artifact_url=run_url, marker=marker, failures=failures)
    if artifact_name:
        md = md.rstrip("\n") + f"\n\n<sub>artifact: `{artifact_name}`</sub>\n"
    if len(md) > MAX_COMMENT_CHARS:
        cut = md[:MAX_COMMENT_CHARS]
        # never cut inside the details block: drop it and say so
        if "<details>" in cut:
            cut = md[:md.index("<details>")]
        md = cut.rstrip() + "\n\n_(truncated; see the run artifacts for the full report)_\n"
    return md


# ── GitHub REST ───────────────────────────────────────────────────────────────
def _urllib_request(method: str, url: str, headers: Mapping[str, str],
                    body: Optional[bytes]) -> Tuple[int, Mapping[str, str], bytes]:
    req = urllib.request.Request(url, data=body, method=method, headers=dict(headers))
    with urllib.request.urlopen(req, timeout=30) as resp:
        return resp.status, dict(resp.headers.items()), resp.read()


def github_headers(token: str) -> dict[str, str]:
    h = {"Accept": "application/vnd.github+json", "X-GitHub-Api-Version": API_VERSION,
         "User-Agent": USER_AGENT}
    if token:
        h["Authorization"] = f"Bearer {token}"
    return h


def _next_link(link_header: Optional[str]) -> Optional[str]:
    """The ``rel="next"`` URL from a Link header, or None."""
    for part in (link_header or "").split(","):
        m = re.match(r'\s*<([^>]+)>\s*;\s*rel="?next"?', part)
        if m:
            return m.group(1)
    return None


def find_existing_comment(api_url: str, repo: str, pr_number: int, marker: str, token: str,
                          request: Requester = _urllib_request) -> Optional[dict]:
    """First issue comment on the PR whose body contains ``marker``, paging via Link."""
    url: Optional[str] = (f"{api_url.rstrip('/')}/repos/{repo}/issues/{pr_number}/comments"
                          f"?per_page=100")
    headers = github_headers(token)
    pages = 0
    while url and pages < 50:
        status, resp_headers, data = request("GET", url, headers, None)
        pages += 1
        comments = json.loads(data.decode("utf-8") or "[]")
        for c in comments:
            if isinstance(c, dict) and marker in (c.get("body") or ""):
                return c
        link = {k.lower(): v for k, v in resp_headers.items()}.get("link")
        url = _next_link(link)
    return None


def upsert_comment(api_url: str, repo: str, pr_number: int, body: str, marker: str,
                   token: str, request: Requester = _urllib_request) -> Optional[int]:
    """PATCH the marked comment if there is one, else POST; returns the comment id.

    A 401, 403 or 404 (typically a fork PR whose token cannot write) is logged
    as a warning and returns None instead of failing the job.
    """
    base = api_url.rstrip("/")
    headers = dict(github_headers(token))
    headers["Content-Type"] = "application/json"
    payload = json.dumps({"body": body}).encode("utf-8")
    try:
        existing = find_existing_comment(api_url, repo, pr_number, marker, token, request)
        if existing is not None:
            url = f"{base}/repos/{repo}/issues/comments/{existing['id']}"
            status, _, data = request("PATCH", url, headers, payload)
            action = "updated"
        else:
            url = f"{base}/repos/{repo}/issues/{pr_number}/comments"
            status, _, data = request("POST", url, headers, payload)
            action = "created"
    except urllib.error.HTTPError as e:
        if e.code in (401, 403, 404):
            warn(f"could not post the PR comment (HTTP {e.code}); the token probably "
                 "lacks pull-requests: write, which is expected for pull requests from "
                 "forks. The step summary and artifact still carry the report.")
            return None
        raise
    except urllib.error.URLError as e:
        warn(f"could not reach the GitHub API to post the comment: {e.reason}")
        return None
    comment = json.loads(data.decode("utf-8") or "{}")
    cid = comment.get("id")
    log(f"{action} PR comment {cid} on {repo}#{pr_number} (HTTP {status})")
    return cid


def pr_number_from_event(event_path: Optional[str]) -> Optional[int]:
    """The pull request number from the event payload, or None (push, schedule, ...)."""
    if not event_path:
        return None
    try:
        with open(event_path, encoding="utf-8") as f:
            event = json.load(f)
    except (OSError, ValueError):
        return None
    for key in ("pull_request", "issue"):
        obj = event.get(key)
        if isinstance(obj, dict) and isinstance(obj.get("number"), int):
            return obj["number"]
    number = event.get("number")
    return number if isinstance(number, int) else None


# ── Main ──────────────────────────────────────────────────────────────────────
def main(argv: Optional[list[str]] = None, env: Optional[Mapping[str, str]] = None,
         request: Requester = _urllib_request) -> int:
    env = os.environ if env is None else env
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--report-dir", default=env.get("INPUT_REPORT_DIR") or "promptdrift-report")
    ap.add_argument("--exit-code", type=int,
                    default=int(env.get("PROMPTDRIFT_EXIT_CODE") or 0))
    args = ap.parse_args(argv)

    report = load_report(os.path.join(args.report_dir, REPORT_FILES["report-json"]))
    failures = threshold_failures(report, env)
    if failures:
        try:
            with open(os.path.join(args.report_dir, FAILURES_FILE), "w", encoding="utf-8") as f:
                f.write("\n".join(failures) + "\n")
        except OSError as e:
            log(f"could not write {FAILURES_FILE}: {e}")

    outputs = build_outputs(report, args.exit_code, failures, args.report_dir)

    server = env.get("GITHUB_SERVER_URL") or "https://github.com"
    repo = env.get("GITHUB_REPOSITORY") or ""
    run_id = env.get("GITHUB_RUN_ID") or ""
    run_url = f"{server}/{repo}/actions/runs/{run_id}" if repo and run_id else None
    marker = env.get("INPUT_COMMENT_MARKER") or DEFAULT_MARKER
    markdown = build_markdown(report, args.exit_code, failures, marker, run_url,
                              artifact_name=env.get("INPUT_ARTIFACT_NAME") or "")

    if truthy(env.get("INPUT_STEP_SUMMARY", "true")):
        write_step_summary(markdown, env.get("GITHUB_STEP_SUMMARY"))

    if truthy(env.get("INPUT_COMMENT", "true")):
        pr = pr_number_from_event(env.get("GITHUB_EVENT_PATH"))
        token = env.get("INPUT_GITHUB_TOKEN") or env.get("GITHUB_TOKEN") or ""
        if pr is None:
            notice(f"no pull request number in the {env.get('GITHUB_EVENT_NAME') or 'current'} "
                   "event; skipping the PR comment")
        elif not repo:
            notice("GITHUB_REPOSITORY is not set; skipping the PR comment")
        elif not token:
            warn("no github-token given; skipping the PR comment")
        else:
            api = env.get("GITHUB_API_URL") or "https://api.github.com"
            cid = upsert_comment(api, repo, pr, markdown, marker, token, request)
            outputs["comment-id"] = "" if cid is None else str(cid)

    write_github_output(outputs, env.get("GITHUB_OUTPUT"))
    for line in failures:
        print(f"::error::promptdrift: {line}")
    log(f"verdict {outputs['verdict']}, exit code {args.exit_code}, "
        f"{len(failures)} threshold failure(s)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
