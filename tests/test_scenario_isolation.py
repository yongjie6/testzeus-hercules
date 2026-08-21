"""A scenario that blows up must not cancel the scenarios after it.

Anything raised by a runner used to reach a_main and end the process, so one
transient provider error discarded every scenario that had not run yet --
including ones that would have passed.
"""

import asyncio
from typing import Any

import pytest

import testzeus_hercules.__main__ as main_module


class _FakeRunner:
    """Runner that fails for one scenario and succeeds for the rest."""

    started: list[str] = []

    def __init__(self, stake_id: str, command: str, **_: Any) -> None:
        self.stake_id = stake_id
        self.command = command
        self.result = None
        self.execution_time = 0.1
        self.browser_manager = _FakeBrowserManager()

    async def start(self) -> None:
        _FakeRunner.started.append(self.stake_id)
        if "boom" in self.stake_id:
            raise RuntimeError("Error code: 502 - upstream_error")
        self.result = _FakeResult()

    async def shutdown(self) -> None:
        return None


class _FakeResult:
    summary = '{"terminate": "yes", "is_passed": true, "final_response": "ok"}'
    cost = None
    terminate = "yes"


class _FakeBrowserManager:
    stake_id = "x"
    _screenshots_dir = "/tmp"
    request_response_log_file = "/tmp/net.json"

    def get_latest_video_path(self) -> None:
        return None


@pytest.fixture
def captured(monkeypatch) -> list[dict]:
    """Run sequential_process over three scenarios, the middle one failing."""
    _FakeRunner.started = []
    built: list[dict] = []

    feats = [
        {"output_file": "/tmp/a.feature", "feature": "F", "scenario": "first"},
        {"output_file": "/tmp/b.feature", "feature": "F", "scenario": "boom"},
        {"output_file": "/tmp/c.feature", "feature": "F", "scenario": "third"},
    ]

    async def fake_process_feature_file(**_: Any) -> list[dict]:
        return feats

    async def fake_serialize(_path: str) -> str:
        return "Scenario: x"

    async def fake_build_junit_xml(*args: Any, **_kw: Any) -> str:
        # build_junit_xml(result, execution_time, cost, feature_name, scenario, ...)
        built.append({"scenario": args[4], "result": args[0]})
        return "/tmp/out.xml"

    async def fake_merge(*_a: Any, **_k: Any) -> None:
        return None

    monkeypatch.setattr(main_module, "process_feature_file", fake_process_feature_file)
    monkeypatch.setattr(main_module, "serialize_feature_file", fake_serialize)
    monkeypatch.setattr(main_module, "SingleCommandInputRunner", _FakeRunner)
    monkeypatch.setattr(main_module, "build_junit_xml", fake_build_junit_xml)
    monkeypatch.setattr(main_module.JUnitXMLGenerator, "merge_junit_xml", fake_merge)
    monkeypatch.setattr(main_module, "prepare_html", lambda *_a, **_k: None)
    monkeypatch.setattr(main_module, "add_event", lambda *_a, **_k: None)

    asyncio.run(main_module.sequential_process())
    return built


def test_scenarios_after_a_failure_still_run(captured) -> None:
    assert _FakeRunner.started == ["first", "boom", "third"], (
        "the scenario after the failing one never started"
    )


def test_every_scenario_is_reported(captured) -> None:
    assert [entry["scenario"] for entry in captured] == ["first", "boom", "third"]


def test_the_failing_scenario_is_reported_as_failed(captured) -> None:
    failed = next(e for e in captured if e["scenario"] == "boom")["result"]
    assert failed["is_passed"] is False
    assert "502" in failed["final_response"], "the cause should survive into the report"


def test_healthy_scenarios_keep_their_own_result(captured) -> None:
    ok = next(e for e in captured if e["scenario"] == "third")["result"]
    assert ok.get("is_passed") is True
