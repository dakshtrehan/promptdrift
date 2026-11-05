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
