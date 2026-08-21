"""A stdio MCP server has to be told where this scenario's output goes.

It is a separate process started once per scenario with a fixed config file,
so without substitution every screenshot it writes lands in the same directory
run after run, with nothing to tell one scenario's evidence from another's.
"""

import asyncio
import os

import pytest
from testzeus_hercules.config import get_global_conf

# mcp_help and core.tools.mcp_tools import each other, so mcp_help can only be
# imported through the tool package -- reaching for it directly first raises
# ImportError on a partially initialized module. Import order here is load
# bearing, and it only shows up when another test has not already pulled these
# in, which is why this test passed alone and failed in the full suite.
from testzeus_hercules.core.tools.mcp_tools import MCPHelper
from testzeus_hercules.utils import mcp_help


@pytest.fixture
def scenario(tmp_path, monkeypatch):
    conf = get_global_conf()
    monkeypatch.setattr(conf, "_config", {**conf._config, "PROJECT_SOURCE_ROOT": str(tmp_path)}, raising=False)
    original = conf.get_default_test_id()
    conf.set_default_test_id("login_shows_the_home_page")
    yield conf
    conf.set_default_test_id(original)


def test_proof_path_lands_under_the_running_scenario(scenario):
    values = MCPHelper._run_placeholders()

    assert values["test_id"] == "login_shows_the_home_page"
    assert os.path.join("proofs", "login_shows_the_home_page") in values["proof_path"]


def test_each_scenario_gets_its_own_proof_directory(scenario):
    first = MCPHelper._run_placeholders()["proof_path"]
    scenario.set_default_test_id("logout_returns_to_guest")
    second = MCPHelper._run_placeholders()["proof_path"]

    assert first != second


def test_placeholders_are_substituted_in_args(scenario):
    values = MCPHelper._run_placeholders()

    expanded = MCPHelper._expand("--proof-dir=${proof_path}", values)

    assert expanded == f"--proof-dir={values['proof_path']}"


def test_unknown_placeholders_are_left_alone(scenario):
    # Better to hand the server a literal ${nope} it can complain about than to
    # silently substitute an empty string and have it write to the filesystem root.
    assert MCPHelper._expand("--x=${nope}", MCPHelper._run_placeholders()) == "--x=${nope}"


def test_non_string_args_survive(scenario):
    assert MCPHelper._expand(7, MCPHelper._run_placeholders()) == 7


def test_a_config_without_placeholders_is_unchanged(scenario):
    plain = "/an/absolute/path"

    assert MCPHelper._expand(plain, MCPHelper._run_placeholders()) == plain


def test_stdio_server_is_started_with_the_expanded_config(scenario, monkeypatch):
    captured = {}

    def fake_stdio_client(server_params):
        captured["params"] = server_params
        raise RuntimeError("stop here, the process must not actually start")

    monkeypatch.setattr(mcp_help, "stdio_client", fake_stdio_client)

    async def run():
        helper = MCPHelper()
        await helper.connect_mcp_server(
            "wxmp",
            {
                "transport": "stdio",
                "command": "node",
                "args": ["server.js", "--proof-dir", "${proof_path}"],
                "env": {"WXMP_RUN": "${test_id}"},
            },
        )

    asyncio.run(run())

    params = captured["params"]
    assert params.args[2] == MCPHelper._run_placeholders()["proof_path"]
    assert params.env["WXMP_RUN"] == "login_shows_the_home_page"
    # The SDK's scrubbed default environment has to survive, or the server
    # starts without PATH and cannot find the binaries it shells out to.
    assert "PATH" in params.env
