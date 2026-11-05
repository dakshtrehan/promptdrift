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
* ``find_existing_comment`` page through a PR's issue comments for the action's
                           own marked comment (Bot author or the token's user)
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


def _pct_pair(value: float, limit: float) -> Tuple[str, str]:
    """Percentages for a value and its limit, with more decimals when one would tie."""
    digits = 1
    a = b = ""
    for digits in range(1, 7):
        a, b = f"{value * 100:.{digits}f}%", f"{limit * 100:.{digits}f}%"
        if a != b:
            break
    if digits == 1:
        return f"{round(value * 100, 1)}%", f"{round(limit * 100, 1)}%"
    return a, b


def _fallback_thresholds(report: Mapping[str, Any], max_drift: Optional[float],
                         max_regressions: Optional[int], max_format_changes: Optional[int],
                         max_flips: Optional[int], fail_on_regression: bool) -> list[str]:
    """Stdlib copy of ``promptdrift.thresholds.evaluate_thresholds`` for a report dict.

    Used when promptdrift is not importable from the interpreter running this
    script (``install: skip`` with promptdrift in another Python, pipx or
    ``uv tool``). The sentences match the library's word for word; a test
    keeps the two in step.
    """
    s = report.get("summary") or {}
    avg = float(s.get("avg_drift") or 0.0)
    regressions = int(s.get("regressions") or 0)
    fmt = int((s.get("structural") or {}).get("format_changes") or 0)
    flips = int((s.get("checks") or {}).get("pass_to_fail") or 0)
    out: list[str] = []
    if max_drift is not None and avg > max_drift:
        measured, limit = _pct_pair(avg, max_drift)
        out.append(f"avg drift {measured} exceeds max-drift {limit}")
    if fail_on_regression and regressions > 0:
        out.append(f"{regressions} regression(s) found and fail-on-regression is set")
    if max_regressions is not None and regressions > max_regressions:
        out.append(f"{regressions} regression(s) exceed max-regressions {max_regressions}")
    if max_format_changes is not None and fmt > max_format_changes:
        out.append(f"{fmt} format change(s) exceed max-format-changes {max_format_changes}")
    if max_flips is not None and flips > max_flips:
        out.append(f"{flips} check flip(s) from pass to fail exceed max-flips {max_flips}")
    return out


def threshold_failures(report: Optional[dict], env: Mapping[str, str]) -> list[str]:
    """Re-evaluate the threshold inputs against report.json.

    Uses ``promptdrift.thresholds`` when it is importable and a stdlib copy of
    the same rules otherwise, with a warning so the fallback is visible.
    """
    if report is None:
        return []
    try:
        limits: dict[str, Any] = {
            "max_drift": _opt_float(env.get("INPUT_MAX_DRIFT")),
            "max_regressions": _opt_int(env.get("INPUT_MAX_REGRESSIONS")),
            "max_format_changes": _opt_int(env.get("INPUT_MAX_FORMAT_CHANGES")),
            "max_flips": _opt_int(env.get("INPUT_MAX_FLIPS")),
            "fail_on_regression": truthy(env.get("INPUT_FAIL_ON_REGRESSION")),
        }
    except ValueError as e:  # the CLI has already rejected the same value
        warn(f"could not re-check the threshold inputs: {e}")
        return []
    try:
        from promptdrift.thresholds import evaluate_thresholds
    except ImportError:
        if any(v not in (None, False) for v in limits.values()):
            warn(f"promptdrift is not importable from {sys.executable}; the threshold "
                 "inputs are re-checked with the action's built-in copy of the rules. "
                 "With install: skip, install promptdrift into the Python that "
                 "python-version selects to get the full report rendering.")
        return _fallback_thresholds(report, **limits)
    try:
        return list(evaluate_thresholds(report, **limits))
    except ValueError as e:
        warn(f"could not re-check the threshold inputs: {e}")
        return []


def verdict_status(report: dict) -> str:
    """``safe`` / ``review`` / ``do_not_ship`` from a report dict, matching reporter.verdict_for."""
    if "cases" in report:
        try:
            from promptdrift.markdown import report_from_dict
            from promptdrift.reporter import verdict_for
            return verdict_for(report_from_dict(report))[0]
        except ImportError:
            pass
    # summary only (or promptdrift not importable): same rule as reporter.verdict_for
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
    if exit_code != 0 and not failures:
        # a non-zero exit with no threshold breach found here: an extra-args
        # threshold, an input error or a crash. Say so above the verdict so the
        # comment never reads as a clean pass for a failed run.
        note = (f"> **promptdrift exited with code {exit_code}.** "
                "See the run log for the reason.")
        head = (marker or "").strip()
        if head and md.startswith(head):
            md = head + "\n\n" + note + md[len(head):]
        else:
            md = note + "\n\n" + md
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


def _starts_with_marker(comment: Mapping[str, Any], marker: str) -> bool:
    body = comment.get("body")
    return isinstance(body, str) and body.lstrip().startswith(marker.strip())


def token_user_id(api_url: str, token: str,
                  request: Requester = _urllib_request) -> Optional[int]:
    """Id of the user the token belongs to (a PAT), or None.

    ``GET /user`` answers 403 for the default ``GITHUB_TOKEN`` and for app
    installation tokens; their comments are written by a Bot account, which
    ``find_existing_comment`` recognises without this lookup.
    """
    if not token:
        return None
    try:
        _, _, data = request("GET", f"{api_url.rstrip('/')}/user", github_headers(token), None)
        user = json.loads(data.decode("utf-8") or "{}")
    except (OSError, ValueError):  # HTTPError and URLError are OSErrors
        return None
    uid = user.get("id") if isinstance(user, dict) else None
    return uid if isinstance(uid, int) else None


def find_existing_comment(api_url: str, repo: str, pr_number: int, marker: str, token: str,
                          request: Requester = _urllib_request) -> Optional[dict]:
    """The action's own marked comment on the PR, paging via Link, or None.

    A comment qualifies only when its body starts with ``marker`` and it was
    written by a Bot account (``github-actions[bot]`` or an app) or, failing
    that, by the user the token belongs to. A marker pasted into someone
    else's comment is ignored, so a drive-by commenter cannot make the action
    overwrite their comment or stop it posting the report.
    """
    url: Optional[str] = (f"{api_url.rstrip('/')}/repos/{repo}/issues/{pr_number}/comments"
                          f"?per_page=100")
    headers = github_headers(token)
    pages = 0
    others: list[dict] = []
    while url and pages < 50:
        status, resp_headers, data = request("GET", url, headers, None)
        pages += 1
        comments = json.loads(data.decode("utf-8") or "[]")
        for c in comments if isinstance(comments, list) else []:
            if not (isinstance(c, dict) and _starts_with_marker(c, marker)):
                continue
            user = c.get("user")
            if isinstance(user, dict) and user.get("type") == "Bot":
                return c
            others.append(c)
        link = {k.lower(): v for k, v in resp_headers.items()}.get("link")
        url = _next_link(link)
    if not others:
        return None
    me = token_user_id(api_url, token, request)
    if me is None:
        return None
    for c in others:
        user = c.get("user")
        if isinstance(user, dict) and user.get("id") == me:
            return c
    return None


def upsert_comment(api_url: str, repo: str, pr_number: int, body: str, marker: str,
                   token: str, request: Requester = _urllib_request) -> Optional[int]:
    """PATCH the marked comment if there is one, else POST; returns the comment id.

    The comment is a best-effort side effect: every failure (HTTP errors such
    as 401/403/404 on fork PRs, 422, 429 or 5xx, network errors, timeouts, a
    body that is not JSON) is logged as a warning and returns None instead of
    failing the job.
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
        comment = json.loads(data.decode("utf-8") or "{}")
    except urllib.error.HTTPError as e:  # before URLError: HTTPError subclasses it
        if e.code in (401, 403, 404):
            warn(f"could not post the PR comment (HTTP {e.code}); the token probably "
                 "lacks pull-requests: write, which is expected for pull requests from "
                 "forks. The step summary and artifact still carry the report.")
        else:
            warn(f"could not post the PR comment (HTTP {e.code} {e.reason}); the step "
                 "summary and artifact still carry the report.")
        return None
    except urllib.error.URLError as e:
        warn(f"could not reach the GitHub API to post the comment: {e.reason}")
        return None
    except (OSError, ValueError) as e:  # read timeouts, a body that is not JSON
        warn(f"could not post the PR comment ({type(e).__name__}: {e}); the step "
             "summary and artifact still carry the report.")
        return None
    cid = comment.get("id") if isinstance(comment, dict) else None
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

    # Outputs and error annotations go out before the comment, so nothing the
    # GitHub API does can leave the caller without them.
    write_github_output(outputs, env.get("GITHUB_OUTPUT"))
    for line in failures:
        print(f"::error::promptdrift: {line}")

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
            if cid is not None:
                outputs["comment-id"] = str(cid)
                # appended: for a repeated key the last line wins
                write_github_output({"comment-id": outputs["comment-id"]},
                                    env.get("GITHUB_OUTPUT"))

    log(f"verdict {outputs['verdict']}, exit code {args.exit_code}, "
        f"{len(failures)} threshold failure(s)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
