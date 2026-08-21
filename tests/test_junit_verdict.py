"""A scenario that asserted nothing must not be reported as a pass.

The planner narrates why it stopped in final_response -- an unreachable tool,
a provider error, a precondition it could not establish. Those runs arrive
with is_assert unset and terminate="yes", and used to come out green, which
turns an infrastructure outage into a clean report.
"""

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
    assert not _verdict(
        {"is_assert": True, "is_passed": True, "terminate": "yes", "assert_summary": "ok"}
    )


def test_a_failing_assertion_fails() -> None:
    assert _verdict(
        {"is_assert": True, "is_passed": False, "terminate": "yes", "assert_summary": "bad"}
    )


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
    messages = [
        r.message
        for case in generator.suite
        for r in (case.result if isinstance(case.result, list) else [case.result])
        if isinstance(r, Failure)
    ]
    assert any("400" in (m or "") for m in messages), "the cause was dropped"
