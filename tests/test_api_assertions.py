"""Unit tests for the assert_json_path tool (testzeus_hercules/core/tools/api_assertions.py).

Covers every operator plus the two failure modes that must never be silently
treated as a pass: a path matching nothing, and malformed input.
"""

import asyncio
import json

from testzeus_hercules.core.tools.api_assertions import assert_json_path

GW12_RESPONSE = json.dumps(
    {
        "httpStatus": 200,
        "statusCode": "0",
        "success": True,
        "bizCodeName": "",
        "statusMsg": "",
        "body": {
            "entryStage": "HOME_ENTRY_STAGE_HOME",
            "latestResult": {"resultId": "scale_result_1787280661294_3d5a538192215b17"},
            "relationshipSlotQuota": {"maxCount": 20, "usedCount": 0, "remainingCount": 20},
            "myPage": {"hasScaleResult": False, "latestResult": None},
        },
    },
    ensure_ascii=False,
)


def _run(**kwargs) -> dict:
    return json.loads(asyncio.run(assert_json_path(**kwargs)))


def test_eq_pass_and_fail():
    passing = _run(response_json=GW12_RESPONSE, path="$.statusCode", operator="eq", expected='"0"')
    assert passing == {"assertion": "PASSED", "path": "$.statusCode", "operator": "eq", "actual": "0", "expected": "0"}

    failing = _run(response_json=GW12_RESPONSE, path="$.body.myPage.hasScaleResult", operator="eq", expected="true")
    assert failing["assertion"] == "FAILED"
    assert failing["actual"] is False
    assert failing["expected"] is True


def test_ne():
    out = _run(response_json=GW12_RESPONSE, path="$.statusCode", operator="ne", expected='"0"')
    assert out["assertion"] == "FAILED"


def test_contains():
    payload = json.dumps({"statusMsg": "assessment restart confirmation required"})
    out = _run(response_json=payload, path="$.statusMsg", operator="contains", expected="confirmation required")
    assert out["assertion"] == "PASSED"


def test_gt_and_lt():
    payload = json.dumps({"count": 20})
    assert _run(response_json=payload, path="$.count", operator="gt", expected="10")["assertion"] == "PASSED"
    assert _run(response_json=payload, path="$.count", operator="lt", expected="10")["assertion"] == "FAILED"


def test_gt_lt_type_mismatch_is_an_error_not_a_crash():
    # expected="high" doesn't coerce to a number, so actual (int) > want (str) used to
    # raise TypeError straight out of the tool instead of the usual ERROR envelope.
    payload = json.dumps({"count": 20})
    out = _run(response_json=payload, path="$.count", operator="gt", expected="high")
    assert out["assertion"] == "ERROR"
    assert "cannot compare" in out["reason"]

    out2 = _run(response_json=payload, path="$.count", operator="lt", expected="high")
    assert out2["assertion"] == "ERROR"


def test_exists_and_not_exists():
    assert _run(response_json=GW12_RESPONSE, path="$.body.myPage", operator="exists")["assertion"] == "PASSED"
    assert _run(response_json=GW12_RESPONSE, path="$.body.nope", operator="not_exists")["assertion"] == "PASSED"
    assert _run(response_json=GW12_RESPONSE, path="$.body.nope", operator="exists")["assertion"] == "FAILED"


def test_count_eq():
    payload = json.dumps({"items": [1, 2, 3]})
    assert _run(response_json=payload, path="$.items[*]", operator="count_eq", expected="3")["assertion"] == "PASSED"
    assert _run(response_json=payload, path="$.items[*]", operator="count_eq", expected="2")["assertion"] == "FAILED"


def test_eq_path_cross_field_comparison():
    """This is the P0-1 case from docs/esm-v3-api-findings.md: myPage.latestResult should
    mirror the top-level latestResult, but the server returns it as null."""
    out = _run(
        response_json=GW12_RESPONSE,
        path="$.body.myPage.latestResult.resultId",
        operator="eq_path",
        expected="$.body.latestResult.resultId",
    )
    assert out["assertion"] == "FAILED"
    assert out["reason"] == "path matched nothing"


def test_eq_path_bad_comparison_path_is_an_error_not_a_crash():
    # A malformed `expected` JSONPath used to raise uncaught, unlike the primary `path`
    # argument's JSONPath which was already guarded.
    out = _run(response_json='{"a":1}', path="$.a", operator="eq_path", expected="$[[[")
    assert out["assertion"] == "ERROR"
    assert "bad comparison JSONPath" in out["reason"]


def test_eq_path_passes_when_mirrored():
    payload = json.dumps({"top": {"id": "abc"}, "nested": {"id": "abc"}})
    out = _run(response_json=payload, path="$.nested.id", operator="eq_path", expected="$.top.id")
    assert out["assertion"] == "PASSED"
    assert out["expected"] == "abc"
    assert out["expected_from"] == "$.top.id"


def test_path_matching_nothing_fails_rather_than_silently_passing():
    out = _run(response_json=GW12_RESPONSE, path="$.body.doesNotExist", operator="eq", expected="1")
    assert out["assertion"] == "FAILED"
    assert out["reason"] == "path matched nothing"


def test_unknown_operator_is_an_error_not_a_silent_pass():
    out = _run(response_json=GW12_RESPONSE, path="$.statusCode", operator="matches_regex", expected="0")
    assert out["assertion"] == "ERROR"
    assert "unknown operator" in out["reason"]


def test_invalid_response_json_is_an_error():
    out = _run(response_json="not json", path="$.x", operator="eq", expected="1")
    assert out["assertion"] == "ERROR"
    assert "not valid JSON" in out["reason"]


def test_invalid_jsonpath_is_an_error():
    out = _run(response_json=GW12_RESPONSE, path="$[[[", operator="eq", expected="1")
    assert out["assertion"] == "ERROR"
    assert "bad JSONPath" in out["reason"]
