import json

import pytest

from promptdrift import compare_outputs, load_outputs
from promptdrift.cli import main

from .conftest import FakeProvider, verdict_json


def _write(tmp_path, rows, name="out.jsonl"):
    f = tmp_path / name
    f.write_text("\n".join(json.dumps(r) for r in rows) + "\n\n")
    return f


ROWS = [
    {"case_id": "same", "input": "q1", "output_a": "Paris is the capital.",
     "output_b": "Paris is the capital."},
    {"case_id": "diff", "input": "q2", "output_a": "apple banana", "output_b": "car train"},
]


def test_load_outputs_jsonl(tmp_path):
    recs = load_outputs(_write(tmp_path, ROWS))
    assert [r["case_id"] for r in recs] == ["same", "diff"]


def test_load_outputs_json_array_and_id_alias(tmp_path):
    f = tmp_path / "o.json"
    f.write_text(json.dumps([{"id": 7, "output_a": "x", "output_b": None}, {"output_a": "a", "output_b": "b"}]))
    recs = load_outputs(f)
    assert recs[0]["case_id"] == "7" and recs[0]["output_b"] == ""
    assert recs[1]["case_id"] == "case_2"


@pytest.mark.parametrize("bad,msg", [
    ([{"case_id": "x", "output_a": "a"}], "missing 'output_b'"),
    ([{"case_id": "x", "output_a": 3, "output_b": "b"}], "must be a string"),
    (["nope"], "must be a JSON object"),
])
def test_load_outputs_errors(tmp_path, bad, msg):
    with pytest.raises(ValueError, match=msg):
        load_outputs(_write(tmp_path, bad))


def test_load_outputs_bad_line(tmp_path):
    f = tmp_path / "o.jsonl"
    f.write_text('{"case_id": "a", "output_a": "x", "output_b": "y"}\n{oops\n')
    with pytest.raises(ValueError, match="line 2"):
        load_outputs(f)


def test_load_outputs_json_not_array(tmp_path):
    f = tmp_path / "o.json"
    f.write_text('{"case_id": "a"}')
    with pytest.raises(ValueError, match="array"):
        load_outputs(f)


def test_compare_outputs_scores_without_models(tmp_path):
    rep = compare_outputs(_write(tmp_path, ROWS), version_a="old", version_b="new")
    assert rep.mode == "offline" and rep.version_a == "old"
    same, diff = rep.cases
    assert same.drift_score == 0 and same.drift_level == "none"
    assert diff.drift_score == pytest.approx(1.0) and diff.drift_level == "major"
    assert same.judge_verdict is None


def test_compare_outputs_list_with_injected_judge(capsys):
    judge = FakeProvider([verdict_json("regressed"), verdict_json("improved")])
    rep = compare_outputs([dict(r) for r in ROWS], use_judge=True, judge_provider=judge,
                          verbose=True)
    assert rep.cases[1].judge_verdict == "regressed"
    assert rep.cases[0].judge_verdict is None  # no drift, judge skipped
    assert "[2/2]" in capsys.readouterr().out


def test_cli_diff_json_and_fail(tmp_path, capsys):
    f = _write(tmp_path, ROWS)
    out = tmp_path / "r.json"
    main(["diff", str(f), "--json", "-o", str(out)])
    data = json.loads(out.read_text())
    assert data["mode"] == "offline" and data["total_cases"] == 2
    assert '"mode": "offline"' in capsys.readouterr().out


def test_cli_diff_missing_file(tmp_path):
    with pytest.raises(SystemExit) as e:
        main(["diff", str(tmp_path / "nope.jsonl")])
    assert e.value.code == 1


def test_cli_diff_bad_input(tmp_path, capsys):
    f = _write(tmp_path, [{"case_id": "x"}])
    with pytest.raises(SystemExit):
        main(["diff", str(f)])
    assert "Input error" in capsys.readouterr().err


# ── review fixes: tags validation on both input paths ─────────────────────────
BAD_TAGS = [5, True, 1.5, {"team": "core"}, ["a", ["b"]], [None], ["a", False],
            [{"k": 1}]]


@pytest.mark.parametrize("tags", BAD_TAGS)
def test_outputs_file_with_bad_tags_is_a_clean_input_error(tmp_path, capsys, tags):
    f = _write(tmp_path, [ROWS[0], dict(ROWS[1], tags=tags)])
    with pytest.raises(ValueError, match=r"Record 2 \(diff\): 'tags' must be a string"):
        load_outputs(f)
    with pytest.raises(SystemExit) as e:
        main(["diff", str(f), "--summary"])
    assert e.value.code == 1
    err = capsys.readouterr().err
    assert "Input error" in err and "Record 2 (diff)" in err and "Traceback" not in err


@pytest.mark.parametrize("tags", BAD_TAGS)
def test_cases_file_with_bad_tags_is_rejected_the_same_way(tmp_path, tags):
    from promptdrift.compare import load_cases

    f = tmp_path / "cases.json"
    f.write_text(json.dumps([{"input": "x", "tags": tags}]))
    with pytest.raises(ValueError, match="'tags' must be a list of strings"):
        load_cases(f)


def test_good_tags_and_empty_entries():
    rows = [dict(ROWS[0], tags=["a", 2, 1.5]), dict(ROWS[1], tags=""),
            dict(ROWS[0], case_id="c3", tags=["", "  ", "b"]), dict(ROWS[1], case_id="c4",
                                                                 tags=None)]
    rep = compare_outputs(rows, bootstrap=0)
    assert [c.tags for c in rep.cases] == [["a", "2", "1.5"], None, ["b"], None]


def test_score_case_rejects_scalar_or_mapping_tags():
    from promptdrift.scoring import _case_tags

    assert _case_tags({"tags": ("x", None, "y")}) == ["x", "y"]
    assert _case_tags({"tags": []}) is None
    for bad in (5, True, {"team": "core"}):
        with pytest.raises(ValueError, match="'tags' must be a string or a list"):
            _case_tags({"id": "c", "tags": bad})
