import pytest

from promptdrift import compare_outputs
from promptdrift.reporter import print_report
from promptdrift.structure import (format_signature, is_valid_json, length_ratio,
                                   signature_string, structural_diff)


def test_signature_json_fenced():
    sig = format_signature('```json\n{"a": 1}\n```')
    assert sig == {"fence": True, "json_type": "object", "list": False, "headings": False,
                   "line_count": 1}
    assert signature_string(sig) == "fence+json:object+lines:1"


@pytest.mark.parametrize("text,expected", [
    ("", "empty"),
    ("   \n  ", "empty"),
    ("[1, 2]", "json:array+lines:1"),
    ("null", "json:null+lines:1"),
    ('"hi"', "json:string+lines:1"),
    ("- a\n- b\n- c", "list+lines:2-4"),
    ("1. a\n2) b", "list+lines:2-4"),
    ("# Title\n\nbody text", "headings+lines:2-4"),
    ("one\ntwo\nthree\nfour\nfive", "lines:5+"),
    ("Just a sentence.", "lines:1"),
    ("```\nprint(1)\n```", "fence+lines:1"),
])
def test_signature_string(text, expected):
    assert signature_string(text) == expected


def test_is_valid_json():
    assert is_valid_json('{"x": [1, 2]}')
    assert is_valid_json('```json\n[1]\n```')
    assert not is_valid_json('{"x": ')
    assert not is_valid_json("")


def test_length_ratio():
    assert length_ratio("abcd", "ab") == 0.5
    assert length_ratio("", "") == 1.0
    assert length_ratio("", "x") is None


def test_structural_diff_fence_added():
    d = structural_diff('{"a": 1}', '```json\n{"a": 1}\n```')
    assert d["format_changed"] is True
    assert d["json_validity_changed"] is False  # still parseable after fence strip
    assert d["signature_a"] == "json:object+lines:1"


def test_report_structural_summary(capsys):
    rep = compare_outputs([
        {"case_id": "broke", "output_a": '{"a": 1}', "output_b": "Sure! a is 1."},
        {"case_id": "same", "output_a": "x y z", "output_b": "x y z"},
        {"case_id": "empty_a", "output_a": "", "output_b": "text"},
    ])
    st = rep.structural_summary()
    assert st["format_changes"] == 2
    assert st["json_validity_changes"] == 1 and st["json_broken"] == 1
    assert st["median_length_ratio"] == pytest.approx((1.0 + 13 / 8) / 2)
    assert rep.to_dict()["summary"]["structural"]["json_broken"] == 1
    print_report(rep, show_outputs=True)
    out = capsys.readouterr().out
    assert "Structure" in out and "valid → invalid" in out and "format" in out


def test_report_structural_empty_and_odd():
    rep = compare_outputs([{"case_id": "a", "output_a": "", "output_b": "x"}])
    assert rep.median_length_ratio is None
    rep = compare_outputs([{"case_id": "a", "output_a": "ab", "output_b": "abcd"}])
    assert rep.median_length_ratio == 2.0
    rep.cases.clear()
    assert rep.structural_summary()["format_change_rate"] == 0.0
