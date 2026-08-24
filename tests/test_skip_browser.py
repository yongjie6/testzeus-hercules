"""A run driven entirely through MCP or API tools should not start a browser.

One was started per scenario regardless. Beyond the launch cost, it left empty
screenshots/ and videos/ folders in every scenario's proofs, which read as
evidence that was captured and came back empty.
"""

import asyncio

import pytest

from testzeus_hercules.config import get_global_conf
from testzeus_hercules.core import runner as runner_module
from testzeus_hercules.core.runner import SingleCommandInputRunner


@pytest.fixture
def conf():
    original = dict(get_global_conf()._config)
    yield get_global_conf()
    get_global_conf()._config = original


@pytest.fixture
def stub_agents(monkeypatch):
    """Stand in for the agent stack so only browser startup is under test."""
    built = []

    class FakeManager:
        def __init__(self, **kwargs):
            built.append(kwargs)

        async def async_initialize(self):
            return None

    class FakeHercules:
        async def shutdown(self):
            return None

    async def fake_create(*args, **kwargs):
        return FakeHercules()

    monkeypatch.setattr(runner_module, "PlaywrightManager", FakeManager)
    monkeypatch.setattr(runner_module.SimpleHercules, "create", fake_create)
    monkeypatch.setattr(
        runner_module.AgentsLLMConfigManager,
        "get_instance",
        classmethod(lambda cls: _FakeConfigManager()),
    )
    return built


class _FakeConfigManager:
    def initialize(self):
        return None

    def get_agent_config(self, name):
        return {"model_name": "x"}


def test_no_browser_is_started_when_skipped(conf, stub_agents):
    conf._config["SKIP_BROWSER"] = "true"
    runner = SingleCommandInputRunner("do something", stake_id="s")

    asyncio.run(runner.initialize())

    assert stub_agents == []
    assert runner.browser_manager is None


def test_a_browser_is_started_by_default(conf, stub_agents):
    conf._config["SKIP_BROWSER"] = "false"
    runner = SingleCommandInputRunner("do something", stake_id="s")

    asyncio.run(runner.initialize())

    assert len(stub_agents) == 1
    assert runner.browser_manager is not None


def test_cleanup_without_a_browser_does_not_raise(conf, stub_agents):
    conf._config["SKIP_BROWSER"] = "true"
    runner = SingleCommandInputRunner("do something", stake_id="s")
    asyncio.run(runner.initialize())

    asyncio.run(runner.clean_up())


@pytest.mark.parametrize("value,expected", [("true", True), ("TRUE", True), ("false", False), ("", False)])
def test_flag_parsing(conf, value, expected):
    conf._config["SKIP_BROWSER"] = value

    assert conf.should_skip_browser() is expected


def test_the_flag_defaults_to_off(conf):
    conf._config.pop("SKIP_BROWSER", None)

    assert conf.should_skip_browser() is False
