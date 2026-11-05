"""A closed stdout pipe (``promptdrift ... | head``) must not change the exit code.

The outputs file is made larger than a pipe buffer so the CLI is still writing
when ``head`` exits; without the guard Python raises BrokenPipeError and the
command exits 1 (or 120 from the shutdown flush) regardless of the result.
"""
import json
import subprocess
import sys

import pytest


@pytest.fixture
def big_outputs(tmp_path):
    filler = "word " * 400
    records = [{"case_id": f"c{i}", "output_a": filler, "output_b": filler} for i in range(300)]
    # one deliberate regression: a regex check that passes in A and fails in B
    records.append({"case_id": "flip", "output_a": "answer 30", "output_b": "answer 3",
                    "checks": {"regex": r"\b30\b"}})
    path = tmp_path / "outputs.jsonl"
    path.write_text("\n".join(json.dumps(r) for r in records))
    return path


def _run_piped(path, extra):
    cmd = (f"{sys.executable} -m promptdrift.cli diff {path} {extra} --json "
           f"--output {path.parent}/report.json | head -c 20 >/dev/null")
    return subprocess.run(
        ["bash", "-o", "pipefail", "-c", cmd], capture_output=True, text=True,
        env={"PATH": "/usr/bin:/bin", "OPENAI_API_KEY": "", "ANTHROPIC_API_KEY": ""},
    )


def test_broken_pipe_keeps_exit_code_zero(big_outputs):
    proc = _run_piped(big_outputs, "--bootstrap 0")
    assert proc.returncode == 0, proc.stderr
    assert "BrokenPipeError" not in proc.stderr
    # the report file is still written after the pipe closed
    assert json.loads((big_outputs.parent / "report.json").read_text())["total_cases"] == 301


def test_broken_pipe_keeps_threshold_exit_code(big_outputs):
    proc = _run_piped(big_outputs, "--bootstrap 0 --max-regressions 0")
    assert proc.returncode == 1, proc.stderr
    assert "exceed max-regressions" in proc.stderr
    assert "BrokenPipeError" not in proc.stderr


# ── closed stdout (``cmd >&-``, cron wrappers, pythonw) ────────────────────────
def _run_closed_stdout(args):
    from pathlib import Path

    root = Path(__file__).resolve().parents[1]
    cmd = f"{sys.executable} -m promptdrift.cli {args} >&-"
    return subprocess.run(
        ["bash", "-c", cmd], capture_output=False, stderr=subprocess.PIPE, text=True,
        cwd=root, env={"PATH": "/usr/bin:/bin", "OPENAI_API_KEY": "", "ANTHROPIC_API_KEY": ""},
    )


def test_closed_stdout_keeps_exit_code_zero(tmp_path):
    out = tmp_path / "report.json"
    proc = _run_closed_stdout(f"diff examples/outputs.jsonl --bootstrap 0 --output {out}")
    assert proc.returncode == 0, proc.stderr
    assert "AttributeError" not in proc.stderr and "Traceback" not in proc.stderr
    assert json.loads(out.read_text())["total_cases"] >= 1


def test_closed_stdout_keeps_an_intended_exit_1(tmp_path):
    out = tmp_path / "report.json"
    proc = _run_closed_stdout(
        f"diff examples/outputs.jsonl --bootstrap 0 --output {out} --fail-on-regression")
    assert proc.returncode == 1 and "Traceback" not in proc.stderr
    assert out.exists()


def test_flush_guard_handles_none_and_closed_stdout(monkeypatch):
    import io

    from promptdrift import cli

    monkeypatch.setattr(sys, "stdout", None)
    cli._flush_stdout_quietly()  # no AttributeError

    class Closed(io.StringIO):
        def flush(self):
            raise ValueError("I/O operation on closed file.")

        def fileno(self):
            raise ValueError("I/O operation on closed file.")

    monkeypatch.setattr(sys, "stdout", Closed())
    cli._flush_stdout_quietly()  # ValueError from flushing a closed file is swallowed


# ── --json keeps stdout one JSON document ─────────────────────────────────────
def test_json_mode_sends_saved_lines_to_stderr(tmp_path, capsys):
    from pathlib import Path

    from promptdrift.cli import main

    example = Path(__file__).resolve().parents[1] / "examples" / "outputs.jsonl"
    main(["diff", str(example), "--json", "--bootstrap", "0",
          "-o", str(tmp_path / "r.json"), "--html", str(tmp_path / "r.html"),
          "--csv", str(tmp_path / "r.csv"), "--junit", str(tmp_path / "r.xml")])
    captured = capsys.readouterr()
    assert json.loads(captured.out)["total_cases"] >= 1  # nothing after the document
    for line in ("Report saved", "HTML report saved", "CSV saved", "JUnit XML saved"):
        assert line in captured.err and line not in captured.out
