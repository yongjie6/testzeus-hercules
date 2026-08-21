"""Unit tests for validate_response_schema (testzeus_hercules/core/tools/api_calls.py)."""

import asyncio
import json

from testzeus_hercules.core.tools.api_calls import validate_response_schema

RESPONSE = json.dumps(
    {
        "statusCode": "0",
        "body": {
            "userId": 7,
            "name": "snyder",
            "roles": ["admin", "qa"],
        },
    }
)

USER_SCHEMA = json.dumps(
    {
        "type": "object",
        "required": ["userId", "name"],
        "properties": {
            "userId": {"type": "integer"},
            "name": {"type": "string"},
            "roles": {"type": "array", "items": {"type": "string"}},
        },
    }
)


def _run(**kwargs) -> dict:
    return json.loads(asyncio.run(validate_response_schema(**kwargs)))


def test_passes_when_whole_response_matches():
    schema = json.dumps({"type": "object", "required": ["statusCode"]})
    out = _run(response_json=RESPONSE, json_schema=schema)
    assert out == {"assertion": "PASSED", "path": "$"}


def test_passes_against_a_sub_path():
    out = _run(response_json=RESPONSE, json_schema=USER_SCHEMA, path="$.body")
    assert out == {"assertion": "PASSED", "path": "$.body"}


def test_fails_on_wrong_type_with_a_diagnosable_violation():
    schema = json.dumps({"type": "object", "properties": {"userId": {"type": "string"}}})
    out = _run(response_json=RESPONSE, json_schema=schema, path="$.body")
    assert out["assertion"] == "FAILED"
    assert len(out["violations"]) == 1
    assert out["violations"][0]["path"] == "$['userId']"
    assert "not of type" in out["violations"][0]["message"] or "is not of type" in out["violations"][0]["message"]


def test_fails_on_missing_required_field():
    schema = json.dumps({"type": "object", "required": ["missingField"]})
    out = _run(response_json=RESPONSE, json_schema=schema, path="$.body")
    assert out["assertion"] == "FAILED"
    assert any("missingField" in v["message"] for v in out["violations"])


def test_reports_multiple_violations():
    schema = json.dumps(
        {
            "type": "object",
            "properties": {
                "userId": {"type": "string"},
                "name": {"type": "integer"},
            },
        }
    )
    out = _run(response_json=RESPONSE, json_schema=schema, path="$.body")
    assert out["assertion"] == "FAILED"
    assert len(out["violations"]) == 2


def test_path_matching_nothing_fails_rather_than_silently_passing():
    out = _run(response_json=RESPONSE, json_schema=USER_SCHEMA, path="$.body.doesNotExist")
    assert out == {"assertion": "FAILED", "path": "$.body.doesNotExist", "reason": "path matched nothing"}


def test_invalid_response_json_is_an_error():
    out = _run(response_json="not json", json_schema=USER_SCHEMA)
    assert out["assertion"] == "ERROR"
    assert "not valid JSON" in out["reason"]


def test_invalid_schema_json_is_an_error():
    out = _run(response_json=RESPONSE, json_schema="not json")
    assert out["assertion"] == "ERROR"
    assert "not valid JSON" in out["reason"]


def test_schema_that_is_not_a_valid_json_schema_is_an_error():
    # "type" must be a string or array of strings, not a number — this is malformed
    # as a JSON Schema, distinct from the response failing to conform to a valid one.
    out = _run(response_json=RESPONSE, json_schema=json.dumps({"type": 123}))
    assert out["assertion"] == "ERROR"
    assert "not a valid JSON Schema" in out["reason"]


def test_bad_jsonpath_is_an_error():
    out = _run(response_json=RESPONSE, json_schema=USER_SCHEMA, path="$[[[")
    assert out["assertion"] == "ERROR"
    assert "bad JSONPath" in out["reason"]
