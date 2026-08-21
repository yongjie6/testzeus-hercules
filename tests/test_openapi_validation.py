"""Unit tests for validate_against_openapi_spec (testzeus_hercules/core/tools/openapi_validation.py).

Runs against the repo's own real spec files — tests/test_not_for_ci/ApiTesting/api_spec.json
(Swagger 2.0, the Petstore spec) and tests/test_not_for_ci/ApiTestingNew/api_spec.json
(OpenAPI 3.1.0) — rather than a hand-built toy spec, so a real-world $ref shape (definitions
vs components/schemas) is what's actually being exercised.
"""

import asyncio
import json

from testzeus_hercules.core.tools.openapi_validation import (
    validate_against_openapi_spec,
)

SWAGGER2_SPEC = "tests/test_not_for_ci/ApiTesting/api_spec.json"
OAS31_SPEC = "tests/test_not_for_ci/ApiTestingNew/api_spec.json"


def _run(**kwargs) -> dict:
    return json.loads(asyncio.run(validate_against_openapi_spec(**kwargs)))


# ---------------------------------------------------------------------------
# Swagger 2.0 (definitions + "schema", $ref resolved via evolve() against the
# whole spec since openapi_schema_validator doesn't support 2.0 at all).
# ---------------------------------------------------------------------------


def test_swagger2_passes_a_conforming_pet():
    pet = {
        "id": 1,
        "name": "Bulldog",
        "photoUrls": ["http://example.com/1.jpg"],
        "status": "available",
    }
    out = _run(response_json=json.dumps(pet), spec_path=SWAGGER2_SPEC, path="/pet/{petId}", method="GET", status_code="200")
    assert out == {"assertion": "PASSED", "path": "/pet/{petId}", "method": "GET", "status_code": "200"}


def test_swagger2_fails_when_a_required_field_is_missing():
    pet = {"id": 1, "photoUrls": []}  # "name" is required per the Petstore spec
    out = _run(response_json=json.dumps(pet), spec_path=SWAGGER2_SPEC, path="/pet/{petId}", method="GET", status_code="200")
    assert out["assertion"] == "FAILED"
    assert any("name" in v["message"] for v in out["violations"])


def test_swagger2_fails_on_wrong_type():
    pet = {"id": "not-an-int", "name": "Bulldog", "photoUrls": []}
    out = _run(response_json=json.dumps(pet), spec_path=SWAGGER2_SPEC, path="/pet/{petId}", method="GET", status_code="200")
    assert out["assertion"] == "FAILED"
    assert any("integer" in v["message"] for v in out["violations"])


def test_swagger2_can_validate_a_sub_path_of_the_response_wrapper():
    wrapped = {"httpStatus": 200, "body": {"id": 1, "name": "Bulldog", "photoUrls": []}}
    out = _run(response_json=json.dumps(wrapped), spec_path=SWAGGER2_SPEC, path="/pet/{petId}", method="GET", status_code="200", response_path="$.body")
    assert out["assertion"] == "PASSED"


# ---------------------------------------------------------------------------
# OpenAPI 3.1.0 (components/schemas + content."application/json".schema).
# ---------------------------------------------------------------------------


def test_oas31_passes_a_conforming_category():
    category = {"id": 1, "name": "Books", "description": "All books"}
    out = _run(response_json=json.dumps(category), spec_path=OAS31_SPEC, path="/categories", method="POST", status_code="200")
    assert out == {"assertion": "PASSED", "path": "/categories", "method": "POST", "status_code": "200"}


def test_oas31_fails_when_a_required_field_is_missing():
    category = {"description": "no id or name"}
    out = _run(response_json=json.dumps(category), spec_path=OAS31_SPEC, path="/categories", method="POST", status_code="200")
    assert out["assertion"] == "FAILED"
    assert len(out["violations"]) >= 1


def test_oas31_method_is_case_insensitive():
    category = {"id": 1, "name": "Books", "description": None}
    out = _run(response_json=json.dumps(category), spec_path=OAS31_SPEC, path="/categories", method="post", status_code="200")
    assert out["assertion"] == "PASSED"


# ---------------------------------------------------------------------------
# Diagnosable errors: the reason must name what the spec actually declares.
# ---------------------------------------------------------------------------


def test_unknown_path_lists_declared_paths():
    out = _run(response_json="{}", spec_path=OAS31_SPEC, path="/no-such-path", method="GET")
    assert out["assertion"] == "ERROR"
    assert "/categories" in out["reason"]


def test_unknown_method_lists_declared_methods():
    out = _run(response_json="{}", spec_path=OAS31_SPEC, path="/categories", method="DELETE")
    assert out["assertion"] == "ERROR"
    assert "GET" in out["reason"] and "POST" in out["reason"]


def test_unknown_status_lists_declared_statuses():
    out = _run(response_json="{}", spec_path=OAS31_SPEC, path="/categories", method="POST", status_code="503")
    assert out["assertion"] == "ERROR"
    assert "200" in out["reason"] and "422" in out["reason"]


def test_invalid_response_json_is_an_error():
    out = _run(response_json="not json", spec_path=OAS31_SPEC, path="/categories", method="POST")
    assert out["assertion"] == "ERROR"
    assert "not valid JSON" in out["reason"]


def test_missing_spec_file_is_an_error():
    out = _run(response_json="{}", spec_path="/no/such/spec.json", path="/x", method="GET")
    assert out["assertion"] == "ERROR"
    assert "could not read spec_path" in out["reason"]


def test_spec_with_no_recognizable_version_is_an_error(tmp_path):
    bad_spec = tmp_path / "bad_spec.json"
    bad_spec.write_text(json.dumps({"paths": {}}))
    out = _run(response_json="{}", spec_path=str(bad_spec), path="/x", method="GET")
    assert out["assertion"] == "ERROR"
    assert "unsupported or missing spec version" in out["reason"]


def test_empty_spec_file_is_an_error(tmp_path):
    # An empty file parses to None (yaml.safe_load), which used to crash with an
    # unhandled AttributeError in _validator_class's spec.get("openapi") instead of
    # this tool's usual ERROR envelope.
    empty_spec = tmp_path / "empty.yaml"
    empty_spec.write_text("")
    out = _run(response_json="{}", spec_path=str(empty_spec), path="/x", method="GET")
    assert out["assertion"] == "ERROR"
    assert "did not parse to a mapping" in out["reason"]


def test_non_mapping_spec_file_is_an_error(tmp_path):
    # A spec file whose top level is a YAML list, not an object.
    list_spec = tmp_path / "list.yaml"
    list_spec.write_text("- a\n- b\n")
    out = _run(response_json="{}", spec_path=str(list_spec), path="/x", method="GET")
    assert out["assertion"] == "ERROR"
    assert "did not parse to a mapping" in out["reason"]


def test_response_path_matching_nothing_fails_rather_than_silently_passing():
    out = _run(response_json='{"body": {}}', spec_path=OAS31_SPEC, path="/categories", method="POST", response_path="$.nope")
    assert out == {"assertion": "FAILED", "response_path": "$.nope", "reason": "path matched nothing"}


def test_bad_jsonpath_is_an_error():
    out = _run(response_json="{}", spec_path=OAS31_SPEC, path="/categories", method="POST", response_path="$[[[")
    assert out["assertion"] == "ERROR"
    assert "bad JSONPath" in out["reason"]


def test_yaml_spec_loads_the_same_way_as_json(tmp_path):
    """_load_spec always uses yaml.safe_load, so a .yaml file with the same content as
    OAS31_SPEC should validate identically — this is the .yaml/.yml half of that path."""
    spec = json.load(open(OAS31_SPEC))
    yaml_spec = tmp_path / "spec.yaml"
    import yaml as pyyaml

    yaml_spec.write_text(pyyaml.safe_dump(spec))

    category = {"id": 1, "name": "Books", "description": "All books"}
    out = _run(response_json=json.dumps(category), spec_path=str(yaml_spec), path="/categories", method="POST", status_code="200")
    assert out["assertion"] == "PASSED"
