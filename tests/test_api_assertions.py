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
    # subject 是被断言那份响应的指纹，判定侧靠它区分「同一条断言的重试」和「同一条断言
    # 跑在两份不同的响应上」，值本身不参与断言语义。
    assert passing.pop("subject")
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
    assert out["reason"].startswith("path matched nothing")


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
    assert out["reason"].startswith("path matched nothing")


def test_path_matching_nothing_names_the_keys_that_do_exist():
    """A path that matches nothing is usually a typo in the path, not an absent field.
    Naming the keys at the closest parent that resolved is what lets the caller tell the
    two apart instead of reporting its own typo as a defect in the service under test."""
    out = _run(response_json=GW12_RESPONSE, path="$.body.myPage.hasScaleResults", operator="eq", expected="true")
    assert out["assertion"] == "FAILED"
    assert "$.body.myPage" in out["reason"]
    assert "hasScaleResult" in out["reason"]


def test_sequence_is_compared_whole_when_expected_is_a_list():
    """`choices[*].choiceValue` matches six values; comparing only the first against a
    six-element expectation can never pass, so a list expectation compares the whole run."""
    payload = json.dumps({"choices": [{"v": 1}, {"v": 2}, {"v": 3}]})
    assert _run(response_json=payload, path="$.choices[*].v", operator="eq", expected="[1,2,3]")["assertion"] == "PASSED"
    assert _run(response_json=payload, path="$.choices[*].v", operator="eq", expected="[3,2,1]")["assertion"] == "FAILED"


def test_string_int64_matches_a_numeric_expectation():
    """protojson serialises int64 as a JSON string, so an actual of "0" has to satisfy
    expected=0 — otherwise every status-code assertion in an ESM case fails on type alone."""
    out = _run(response_json='{"statusCode":"0"}', path="$.statusCode", operator="eq", expected="0")
    assert out["assertion"] == "PASSED"
    assert "matched as text" in out["note"]


def test_count_eq_counts_array_elements_not_path_matches():
    """`$.items` matches the array itself, so counting matches reports 1 for an empty list."""
    assert _run(response_json='{"items":[]}', path="$.items", operator="count_eq", expected="0")["assertion"] == "PASSED"
    assert _run(response_json='{"items":[1,2]}', path="$.items", operator="count_eq", expected="2")["assertion"] == "PASSED"


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


def test_captured_variable_used_as_a_path_is_named_as_such():
    """The observed failure in TC-ESM30-CH-22: an id captured in an earlier step was looked
    up as $.captured.userMessageId in a LATER response, where no `captured` key exists. The
    failure has to say the value is a variable, or the caller reads it as a missing field."""
    from testzeus_hercules.core.tools.api_calls import _var_store

    _var_store()["userMessageId"] = "m_1"
    out = _run(
        response_json='{"body":{"messages":[{"messageId":"m_1"}]}}',
        path="$.body.messages[0].messageId",
        operator="eq_path",
        expected="$.captured.userMessageId",
    )
    assert out["assertion"] == "FAILED"
    assert "${userMessageId}" in out["reason"]


def test_operator_smuggled_into_expected_is_an_error_not_a_mismatch():
    """Also from CH-22: operator="eq" with expected="ne_path:$.captured.x". Compared as a
    literal it fails every time, which reads as the service being wrong rather than the call."""
    out = _run(response_json='{"a":"1"}', path="$.a", operator="eq", expected="ne_path:$.b")
    assert out["assertion"] == "ERROR"
    assert "operator" in out["reason"]


def test_operator_name_in_expected_is_auto_corrected():
    """把运算符写进期望值是模型最顽固的习惯：给出精确提示后它仍会连着重试五次同样的写法
    （TC-ESM30-PF-07 实测），然后退化成一个揉坏的字典。诊断信息是给人看的，模型需要的是
    "这个错犯不出来"的形状 —— 所以改判而不是报错，但要在 note 里留痕，好数它发生多少次。"""
    for bogus in ("not_empty", "not_empty:", "exists"):
        out = _run(response_json='{"a":"x"}', path="$.a", operator="eq", expected=bogus)
        assert out["assertion"] == "PASSED", f"{bogus!r} 应被改判，实际 {out['assertion']}"
        assert "运算符名" in out["note"]
    # 改判不等于放水：空值仍要判否
    assert _run(response_json='{"a":""}', path="$.a", operator="eq", expected="not_empty")["assertion"] == "FAILED"


def test_invented_operator_prefix_is_still_an_error():
    """自造的前缀（ne_path 根本不在运算符表里）没法改判，只能明确报错。"""
    for bogus in ("ne_path:$.captured.x", "eq:$.other"):
        assert _run(response_json='{"a":"x"}', path="$.a", operator="eq", expected=bogus)["assertion"] == "ERROR"


def test_a_colon_in_an_ordinary_expected_value_still_compares():
    """The guard keys on a JSONPath after the colon, so ordinary values keep working."""
    assert _run(response_json='{"a":"foo:bar"}', path="$.a", operator="eq", expected="foo:bar")["assertion"] == "PASSED"


def test_not_empty_closes_the_holes_that_ne_empty_leaves():
    """ "非空"写成 `ne ""` 会放过 JSON null 和未替换的 ${占位符} —— 恰好是它本该拦住的两种。"""
    payload = '{"a":"x","empty":"","nul":null,"ph":"${notReplaced}"}'
    assert _run(response_json=payload, path="$.a", operator="not_empty")["assertion"] == "PASSED"
    for field in ("empty", "nul", "ph"):
        assert _run(response_json=payload, path=f"$.{field}", operator="not_empty")["assertion"] == "FAILED"
    # ne "" 仍然可用，但结果里要说清它的边界
    out = _run(response_json=payload, path="$.nul", operator="ne", expected="")
    assert out["assertion"] == "PASSED"
    assert "not_empty" in out["note"]


def test_expected_with_an_uncaptured_variable_is_an_error_not_a_pass():
    """未捕获的 ${name} 原样留在 expected 里时，`ne` 永远通过 —— 最坏的一种假绿。"""
    out = _run(response_json='{"a":"x"}', path="$.a", operator="ne", expected="${neverCaptured}")
    assert out["assertion"] == "ERROR"
    assert "没有被捕获过" in out["reason"]


def test_every_assertion_is_recorded_not_just_the_failures():
    """通过的断言不留痕的话，一条绿色用例就无法审计：分不出它断言了十次全过，
    还是一次都没断言、由模型自己读着说通过。"""
    import json as _json
    import tempfile

    from testzeus_hercules.config import get_global_conf

    folder = tempfile.mkdtemp()
    original = get_global_conf().get_proof_path
    get_global_conf().get_proof_path = lambda *a, **k: folder
    try:
        _run(response_json='{"a":"x"}', path="$.a", operator="eq", expected="x")
        _run(response_json='{"a":"x"}', path="$.a", operator="eq", expected="WRONG")
        with open(f"{folder}/assertion_logs.log", encoding="utf-8") as handle:
            records = [_json.loads(line) for line in handle if line.strip()]
    finally:
        get_global_conf().get_proof_path = original
    assert [r["assertion"] for r in records] == ["PASSED", "FAILED"]


SCALE_RESULT_RESPONSE = json.dumps(
    {
        "statusCode": "0",
        "body": {
            "result": {
                "scoreResults": [
                    {"dimensionId": "d1", "score": 1.2},
                    {"dimensionId": "d2", "score": -0.4},
                    {"dimensionId": "d3", "score": 7.9},
                ]
            }
        },
    },
    ensure_ascii=False,
)


def test_gt_only_checks_the_first_match_and_says_so():
    """缺陷 7102943924「16 维分值超出 [-3,3]」的形状：首维在区间内，越界的排在后面。
    gt/lt 只比 matches[0]，那条越界的值根本没被看过——所以这里要留下明确的提示。"""
    out = _run(response_json=SCALE_RESULT_RESPONSE, path="$.body.result.scoreResults[*].score", operator="lt", expected="3")
    assert out["assertion"] == "PASSED"
    assert "all_gte/all_lte" in out["note"]


def test_all_lte_catches_a_later_out_of_range_value():
    out = _run(response_json=SCALE_RESULT_RESPONSE, path="$.body.result.scoreResults[*].score", operator="all_lte", expected="3")
    assert out["assertion"] == "FAILED"
    assert out["actual"] == 7.9
    assert out["first_violation_index"] == 2
    assert out["checked"] == 3


def test_all_gte_passes_when_every_value_is_in_bound():
    out = _run(response_json=SCALE_RESULT_RESPONSE, path="$.body.result.scoreResults[*].score", operator="all_gte", expected="-3")
    assert out["assertion"] == "PASSED"
    assert out["checked"] == 3


def test_all_lte_counts_array_elements_when_the_path_lands_on_the_array():
    """和 count_eq 同一口径：路径打在数组本身时约束的是它的元素。"""
    out = _run(response_json='{"body":{"scores":[1,2,9]}}', path="$.body.scores", operator="all_lte", expected="3")
    assert out["assertion"] == "FAILED"
    assert out["checked"] == 3
    assert out["first_violation_index"] == 2


def test_all_gte_on_a_path_matching_nothing_is_a_failure():
    out = _run(response_json='{"body":{}}', path="$.body.scores[*]", operator="all_gte", expected="0")
    assert out["assertion"] == "FAILED"
    assert "matched nothing" in out["reason"]


def test_all_lte_against_a_non_numeric_value_is_an_error_not_a_pass():
    out = _run(response_json='{"a":["x"]}', path="$.a", operator="all_lte", expected="3")
    assert out["assertion"] == "ERROR"
    assert "index 0" in out["reason"]
