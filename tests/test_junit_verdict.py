"""A scenario that asserted nothing must not be reported as a pass.

The planner narrates why it stopped in final_response -- an unreachable tool,
a provider error, a precondition it could not establish. Those runs arrive
with is_assert unset and terminate="yes", and used to come out green, which
turns an infrastructure outage into a clean report.
"""

import json
from typing import Any

import pytest
from junitparser import Failure
from testzeus_hercules.utils import junit_helper
from testzeus_hercules.utils.junit_helper import JUnitXMLGenerator


@pytest.fixture(autouse=True)
def _quiet_telemetry(monkeypatch) -> None:
    """Other tests swap out EventType, and it is read as a call argument here."""

    class _EventType:
        ASSERT = "assert"

    monkeypatch.setattr(junit_helper, "EventType", _EventType, raising=False)
    monkeypatch.setattr(junit_helper, "EventData", lambda **_k: None, raising=False)
    monkeypatch.setattr(junit_helper, "add_event", lambda *_a, **_k: None, raising=False)


def _verdict(json_data: dict[str, Any]) -> bool:
    """True when the generated case is marked failed."""
    generator = JUnitXMLGenerator("F", *["/tmp"] * 8)
    generator.add_test_case("scenario", "F", json_data, 1.0, {})
    results = [case.result for case in generator.suite]
    flattened = [r for entry in results for r in (entry if isinstance(entry, list) else [entry])]
    return any(isinstance(r, Failure) for r in flattened)


def test_a_passing_assertion_passes() -> None:
    assert not _verdict({"is_assert": True, "is_passed": True, "terminate": "yes", "assert_summary": "ok"})


def test_a_failing_assertion_fails() -> None:
    assert _verdict({"is_assert": True, "is_passed": False, "terminate": "yes", "assert_summary": "bad"})


def test_stopping_without_asserting_fails() -> None:
    """The case this exists for: terminate="yes" with nothing asserted."""
    assert _verdict(
        {
            "is_assert": False,
            "terminate": "yes",
            "final_response": "测试失败：MCP helper returned 400",
        }
    ), "a scenario that verified nothing was reported as a pass"


def test_not_terminating_still_fails() -> None:
    assert _verdict({"is_assert": False, "terminate": "no", "final_response": "aborted"})


def test_the_reason_reaches_the_report() -> None:
    generator = JUnitXMLGenerator("F", *["/tmp"] * 8)
    generator.add_test_case(
        "scenario",
        "F",
        {"is_assert": False, "terminate": "yes", "final_response": "MCP helper returned 400"},
        1.0,
        {},
    )
    messages = [r.message for case in generator.suite for r in (case.result if isinstance(case.result, list) else [case.result]) if isinstance(r, Failure)]
    assert any("400" in (m or "") for m in messages), "the cause was dropped"


# --------------------------------------------------------------- 断言台账绑定判定

PASSING = {"is_assert": True, "is_passed": True, "terminate": "yes", "assert_summary": "ok"}


def _verdict_with_ledger(tmp_path, records: list[dict]) -> bool:
    """把台账写进 proofs 目录，再看 planner 报通过时最终是红是绿。"""
    (tmp_path / "assertion_logs.log").write_text("\n".join(json.dumps(r, ensure_ascii=False) for r in records) + "\n", encoding="utf-8")
    generator = JUnitXMLGenerator("F", "/tmp", "/tmp", str(tmp_path), *["/tmp"] * 5)
    generator.add_test_case("scenario", "F", dict(PASSING), 1.0, {})
    results = [case.result for case in generator.suite]
    flattened = [r for entry in results for r in (entry if isinstance(entry, list) else [entry])]
    return any(isinstance(r, Failure) for r in flattened)


def test_a_failed_assertion_overrides_a_planner_pass(tmp_path) -> None:
    """这是整套「断言必须由工具得出」的约定唯一的强制点。

    在这之前红绿只取 planner 的 is_passed，台账只是一条 JUnit property——assert_json_path
    和 assert_ui 判否，用例照样绿，断言工具等于一种更啰嗦的日志。
    """
    assert _verdict_with_ledger(tmp_path, [{"assertion": "FAILED", "path": "$.body.chat.chatTitle", "operator": "eq", "actual": "新的对话", "expected": "怎么换工作"}])


def test_all_assertions_passing_leaves_the_pass_alone(tmp_path) -> None:
    assert not _verdict_with_ledger(tmp_path, [{"assertion": "PASSED", "path": "$.statusCode", "operator": "eq", "expected": "0"}])


def test_the_same_assertion_on_two_pages_does_not_fold(tmp_path) -> None:
    """跨页一致性检查在两个页面上用的是同一个期望值。只按路径和期望值折叠，会把 A 页
    判否的那条折进 B 页判是的那条——正好折掉 capture_ui_value 要抓的那类缺陷
    （7099316554 备注改完首页没同步、7098039604 头像传了我的页没有）。
    折叠键里带被断言内容的指纹，两页才分得开。"""
    assert _verdict_with_ledger(
        tmp_path,
        [
            {"assertion": "FAILED", "mode": "contains", "expected": "阿周", "subject": "home0000page"},
            {"assertion": "PASSED", "mode": "contains", "expected": "阿周", "subject": "mine0000page"},
        ],
    )


def test_a_corrected_assertion_is_not_held_against_the_case(tmp_path) -> None:
    """模型写错路径 -> "path matched nothing"（走的是 FAILED 分支）-> 改对再来一次。

    关键是两次的 subject **不同**：重试往往要重新发一次请求或重新抓一次快照，而响应里
    有时间戳和 id、页面也变了，指纹必然变。所以求值失败这一类必须按不带指纹的目标销账，
    否则每一次自我纠正都会留下一条永远销不掉的红。"""
    assert not _verdict_with_ledger(
        tmp_path,
        [
            {"assertion": "FAILED", "path": "$.body.chatTitle", "operator": "not_empty", "expected": "", "subject": "resp01", "reason": "path matched nothing"},
            {"assertion": "PASSED", "path": "$.body.chatTitle", "operator": "not_empty", "expected": "", "subject": "resp02"},
        ],
    )


def test_an_uncorrected_evaluation_failure_still_fails(tmp_path) -> None:
    """销账的前提是后来真的成功过一次。一直没改对的，说明这条断言从头到尾没成立过。"""
    assert _verdict_with_ledger(
        tmp_path,
        [
            {"assertion": "FAILED", "path": "$.body.nope", "operator": "not_empty", "expected": "", "subject": "resp01", "reason": "path matched nothing"},
            {"assertion": "PASSED", "path": "$.body.other", "operator": "not_empty", "expected": "", "subject": "resp02"},
        ],
    )


def test_a_value_mismatch_is_never_cleared_by_a_later_pass(tmp_path) -> None:
    """值不符是关于被测方的一条事实，后面在别的页面上断言同一个值成立，不能把它抹掉。

    这条和上面那条的区别就是全部要点：`path matched nothing` 是"没求成值"，
    `actual != expected` 是"求出来了、不对"。前者可以被自我纠正销账，后者不行。"""
    assert _verdict_with_ledger(
        tmp_path,
        [
            {"assertion": "FAILED", "mode": "contains", "expected": "阿周", "subject": "home", "actual": "小周", "occurrences": 0},
            {"assertion": "PASSED", "mode": "contains", "expected": "阿周", "subject": "mine"},
        ],
    )


def test_a_different_assertion_on_the_same_path_still_counts(tmp_path) -> None:
    """折叠的是「同一个断言重试」，不是「同一条路径上的所有断言」。
    路径相同但运算符或期望值不同，是两条断言，后一条通过不能替前一条销账。"""
    assert _verdict_with_ledger(
        tmp_path,
        [
            {"assertion": "FAILED", "path": "$.body.score", "operator": "all_lte", "expected": 3, "actual": 7.9, "subject": "resp01"},
            {"assertion": "PASSED", "path": "$.body.score", "operator": "all_gte", "expected": -3, "subject": "resp01"},
        ],
    )


def test_an_uncorrected_tool_error_also_fails(tmp_path) -> None:
    """ERROR 说的是这条断言压根没跑成。一直没改对还报通过，等于用例声称验过、实际没验。"""
    assert _verdict_with_ledger(tmp_path, [{"assertion": "ERROR", "path": "$.body.x", "operator": "eq", "expected": "not_empty", "reason": "expected 里写的是运算符名"}])


def test_a_ui_assertion_failure_also_binds(tmp_path) -> None:
    """界面侧的记录没有 path/operator，用 scope/mode 做标识，同样要能定红绿。"""
    assert _verdict_with_ledger(tmp_path, [{"assertion": "FAILED", "mode": "contains", "expected": "小周", "occurrences": 0, "preview": 'view "小量"'}])


def test_no_ledger_at_all_does_not_turn_a_pass_red(tmp_path) -> None:
    """现在还有一批用例一次都不调断言工具。把零断言直接判红，那批红说的是用例没改造完，
    不是被测方有问题——分不出来的红等于没有红。它只在报告里显形。"""
    generator = JUnitXMLGenerator("F", "/tmp", "/tmp", str(tmp_path), *["/tmp"] * 5)
    generator.add_test_case("scenario", "F", dict(PASSING), 1.0, {})
    results = [case.result for case in generator.suite]
    flattened = [r for entry in results for r in (entry if isinstance(entry, list) else [entry])]
    assert not any(isinstance(r, Failure) for r in flattened)


def test_observer_notices_do_not_masquerade_as_case_assertions(tmp_path) -> None:
    """observer 自动记的 NOTICE 是 PASSED。把它算进 total，一条 4xx 就足以让
    「本条用例没有调用确定性断言工具」这条属性消失，报告显示「断言 1 条全过」——
    而这条用例其实一次都没断言过。零断言的通过必须一直看得见。"""
    (tmp_path / "assertion_logs.log").write_text(json.dumps({"assertion": "PASSED", "source": "invariant", "mode": "http_4xx", "actual": "404 x"}) + "\n", encoding="utf-8")
    generator = JUnitXMLGenerator("F", "/tmp", "/tmp", str(tmp_path), *["/tmp"] * 5)
    ledger = generator._assertion_ledger()
    assert ledger["total"] == 0, "不变量不该冒充用例自己写的断言"
    assert ledger["invariant"] == 1
    assert ledger["unresolved"] == 0


def test_an_observer_violation_still_turns_the_case_red(tmp_path) -> None:
    """不算进 total，但照样判红——它是判定源，只是不冒充用例自己写的断言。"""
    assert _verdict_with_ledger(tmp_path, [{"assertion": "FAILED", "source": "invariant", "mode": "http_5xx", "subject": "500 https://api/x", "actual": "500 https://api/x"}])
