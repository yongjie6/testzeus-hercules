"""The planner's routing choice must survive parsing.

A response that fails json.loads falls through to a salvage branch that used to
recover four keys, none of them target_helper. The step then went to the default
helper instead of the one the planner named, which reads as the planner routing
badly rather than as a parsing problem.
"""

from testzeus_hercules.utils.response_parser import parse_response


def test_escaped_backslash_before_n_does_not_lose_the_helper():
    # The flattening pass rewrites \\n into a bare backslash plus a space, which
    # is an invalid JSON escape. Observed live: three mini program runs failed
    # because the step went to the browser agent after the planner said mcp.
    raw = r'{"plan":"一。\\n2. 二","next_step":"x","terminate":"no","target_helper":"mcp"}'

    assert parse_response(raw)["target_helper"] == "mcp"


def test_plain_response_still_parses():
    raw = '{"plan":"p","next_step":"s","terminate":"no","target_helper":"api","is_passed":false}'

    parsed = parse_response(raw)

    assert parsed["target_helper"] == "api"
    assert parsed["is_passed"] is False


def test_newline_escapes_survive():
    parsed = parse_response('{"plan":"one\\ntwo","next_step":"s","terminate":"no","target_helper":"sql"}')

    assert parsed["target_helper"] == "sql"
    assert "one" in parsed["plan"] and "two" in parsed["plan"]


def test_json_in_a_fenced_block():
    raw = '```json\n{"plan":"p","next_step":"s","terminate":"no","target_helper":"mcp"}\n```'

    assert parse_response(raw)["target_helper"] == "mcp"


def test_the_last_object_wins_when_the_model_narrates_first():
    raw = '{"plan":"draft","target_helper":"browser"} 最终： {"plan":"final","next_step":"s","terminate":"no","target_helper":"mcp"}'

    assert parse_response(raw)["target_helper"] == "mcp"


def test_salvage_still_recovers_the_helper_from_unparseable_text():
    # Nothing here is valid JSON, so the salvage branch runs. It must not drop
    # the routing field on the floor.
    raw = 'plan: 做点什么 next_step: 下一步 terminate: no target_helper: "mcp" is_passed: false'

    parsed = parse_response(raw)

    assert parsed["target_helper"] == "mcp"
    assert parsed["is_passed"] is False


def test_a_response_with_no_json_at_all_returns_empty():
    assert parse_response("just some prose") == {}


def test_empty_input():
    assert parse_response("") == {}
