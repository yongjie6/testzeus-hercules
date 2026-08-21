"""Validate an API response against the response schema declared in an OpenAPI/Swagger
spec file, instead of hand-copying that schema into a validate_response_schema call.

Covers Swagger 2.0 (via plain jsonschema — openapi_schema_validator doesn't accept it)
and OpenAPI 3.0/3.1/3.2 (via openapi_schema_validator's OAS3x validators, which understand
OpenAPI-specific schema keywords like `nullable`).
"""

import json
from typing import Annotated, Any, Dict

import jsonschema
import yaml
from openapi_schema_validator import OAS30Validator, OAS31Validator, OAS32Validator
from testzeus_hercules.core.tools.tool_registry import tool
from testzeus_hercules.utils.jsonpath_helper import find_json_path
from testzeus_hercules.utils.logger import logger


# YAML 1.1/1.2 parses valid JSON directly, so this one loader covers .json/.yaml/.yml
# specs without needing to branch on file extension.
def _load_spec(spec_path: str) -> Dict[str, Any]:
    with open(spec_path, "r", encoding="utf-8") as f:
        return yaml.safe_load(f)


def _validator_class(spec: Dict[str, Any]):
    """Pick the right validator for the spec's declared version, or None for Swagger 2.0
    (which openapi_schema_validator doesn't support — plain jsonschema handles it instead;
    2.0 schema objects are structurally JSON Schema Draft 4 in the cases that matter here)."""
    openapi_version = spec.get("openapi")
    if isinstance(openapi_version, str):
        if openapi_version.startswith("3.0"):
            return OAS30Validator
        if openapi_version.startswith("3.1"):
            return OAS31Validator
        if openapi_version.startswith("3.2"):
            return OAS32Validator
        return None
    if spec.get("swagger") == "2.0":
        return jsonschema.validators.Draft4Validator
    return None


def _extract_response_schema(spec: Dict[str, Any], path: str, method: str, status_code: str) -> Any:
    """Returns the response schema object, or a dict with a "reason" key explaining why
    none could be found (unknown path/method/status, or that status has no body/schema)."""
    paths = spec.get("paths", {})
    if path not in paths:
        return {"reason": f"path {path!r} is not declared in this spec. Declared paths: {sorted(paths.keys())}"}

    operations = paths[path]
    method_lower = method.lower()
    if method_lower not in operations:
        return {"reason": f"method {method!r} is not declared for {path!r}. Declared methods: {sorted(k.upper() for k in operations if k != 'parameters')}"}

    responses = operations[method_lower].get("responses", {})
    if status_code not in responses:
        return {"reason": f"status {status_code!r} is not declared for {method.upper()} {path}. Declared statuses: {sorted(responses.keys())}"}

    response = responses[status_code]

    if "schema" in response:  # Swagger 2.0
        return {"schema": response["schema"]}

    content = response.get("content", {})  # OpenAPI 3.x
    if "application/json" in content and "schema" in content["application/json"]:
        return {"schema": content["application/json"]["schema"]}

    return {"reason": f"{method.upper()} {path} -> {status_code} has no application/json schema declared in this spec (available content types: {sorted(content.keys())})"}


@tool(
    agent_names=["api_nav_agent"],
    name="validate_against_openapi_spec",
    description=(
        "Validate an API response against the response schema an OpenAPI/Swagger spec file "
        "declares for a given path, method, and status code — instead of copying that schema "
        "into validate_response_schema by hand. Use this whenever the task step says a "
        "response should conform to 'the spec' / 'the contract' rather than giving you an "
        "explicit schema.\n"
        "Parameters:\n"
        "  - response_json: the JSON string returned by the API call being verified.\n"
        "  - spec_path: path to the OpenAPI/Swagger spec file (.json or .yaml/.yml).\n"
        "  - path: the spec's path template exactly as declared, e.g. /pet/{petId} — not "
        "the concrete URL that was actually called.\n"
        "  - method: HTTP method, e.g. GET, POST.\n"
        "  - status_code: the response status as declared in the spec, e.g. 200. (Optional, "
        "default 200.)\n"
        "  - response_path: optional JSONPath into response_json to the value to validate, "
        "e.g. $.body — most call wrappers nest the actual response body there. Defaults to "
        "validating response_json's whole top level.\n"
        "Supports Swagger 2.0 and OpenAPI 3.0/3.1/3.2. Returns assertion: PASSED, FAILED with "
        "a list of violations, or ERROR when the path/method/status/schema can't be found in "
        "the spec (the error names what was declared, so a typo is easy to spot)."
    ),
)
async def validate_against_openapi_spec(
    response_json: Annotated[str, "The JSON string returned by the API call being verified."],
    spec_path: Annotated[str, "Path to the OpenAPI/Swagger spec file (.json or .yaml/.yml)."],
    path: Annotated[str, "The spec's path template exactly as declared, e.g. /pet/{petId}."],
    method: Annotated[str, "HTTP method, e.g. GET, POST."],
    status_code: Annotated[str, "The response status as declared in the spec, e.g. 200."] = "200",
    response_path: Annotated[str, "Optional JSONPath to the value to validate, e.g. $.body. Defaults to the whole response."] = "",
) -> Annotated[str, "Minified JSON: assertion (PASSED/FAILED/ERROR), and violations (path, message) on failure."]:
    try:
        instance = json.loads(response_json)
    except (json.JSONDecodeError, TypeError) as e:
        result = {"assertion": "ERROR", "reason": f"response_json is not valid JSON: {e}"}
        logger.warning("validate_against_openapi_spec: %s", result["reason"])
        return json.dumps(result, ensure_ascii=False, separators=(",", ":"))

    try:
        spec = _load_spec(spec_path)
    except OSError as e:
        result = {"assertion": "ERROR", "reason": f"could not read spec_path {spec_path!r}: {e}"}
        logger.warning("validate_against_openapi_spec: %s", result["reason"])
        return json.dumps(result, ensure_ascii=False, separators=(",", ":"))
    except yaml.YAMLError as e:
        result = {"assertion": "ERROR", "reason": f"spec_path {spec_path!r} is not valid JSON/YAML: {e}"}
        logger.warning("validate_against_openapi_spec: %s", result["reason"])
        return json.dumps(result, ensure_ascii=False, separators=(",", ":"))

    if not isinstance(spec, dict):
        result = {
            "assertion": "ERROR",
            "reason": f"spec_path {spec_path!r} did not parse to a mapping (got {type(spec).__name__}); an OpenAPI/Swagger spec must be a JSON/YAML object at the top level",
        }
        logger.warning("validate_against_openapi_spec: %s", result["reason"])
        return json.dumps(result, ensure_ascii=False, separators=(",", ":"))

    validator_cls = _validator_class(spec)
    if validator_cls is None:
        result = {
            "assertion": "ERROR",
            "reason": f'unsupported or missing spec version in {spec_path!r} (expected "swagger": "2.0" or "openapi": "3.0.x"/"3.1.x"/"3.2.x")',
        }
        logger.warning("validate_against_openapi_spec: %s", result["reason"])
        return json.dumps(result, ensure_ascii=False, separators=(",", ":"))

    if response_path:
        try:
            matches = find_json_path(instance, response_path)
        except Exception as e:
            result = {"assertion": "ERROR", "reason": f"bad JSONPath {response_path!r}: {e}"}
            logger.warning("validate_against_openapi_spec: %s", result["reason"])
            return json.dumps(result, ensure_ascii=False, separators=(",", ":"))
        if not matches:
            result = {"assertion": "FAILED", "response_path": response_path, "reason": "path matched nothing"}
            logger.info("validate_against_openapi_spec: %s", result)
            return json.dumps(result, ensure_ascii=False, separators=(",", ":"))
        instance = matches[0]

    extraction = _extract_response_schema(spec, path, method, status_code)
    if "reason" in extraction:
        result = {"assertion": "ERROR", "path": path, "method": method.upper(), "status_code": status_code, "reason": extraction["reason"]}
        logger.warning("validate_against_openapi_spec: %s", result["reason"])
        return json.dumps(result, ensure_ascii=False, separators=(",", ":"))
    schema = extraction["schema"]

    try:
        # The validator is built on the *whole* spec so "#/..." refs in the extracted
        # schema resolve against components/schemas (3.x) or definitions (2.0); evolve()
        # then swaps in the extracted schema for the actual top-level validation.
        base_validator = validator_cls(spec)
        validator = base_validator.evolve(schema=schema)
        errors = sorted(validator.iter_errors(instance), key=lambda e: list(e.absolute_path))
    except jsonschema.exceptions.SchemaError as e:
        result = {"assertion": "ERROR", "path": path, "method": method.upper(), "status_code": status_code, "reason": f"the spec's schema for this response is not valid: {e.message}"}
        logger.warning("validate_against_openapi_spec: %s", result["reason"])
        return json.dumps(result, ensure_ascii=False, separators=(",", ":"))

    if not errors:
        return json.dumps({"assertion": "PASSED", "path": path, "method": method.upper(), "status_code": status_code}, ensure_ascii=False)

    violations = [{"path": "$" + "".join(f"[{p!r}]" if isinstance(p, str) else f"[{p}]" for p in e.absolute_path), "message": e.message} for e in errors]
    result = {"assertion": "FAILED", "path": path, "method": method.upper(), "status_code": status_code, "violations": violations}
    logger.info("validate_against_openapi_spec FAILED: %s", result)
    return json.dumps(result, ensure_ascii=False, separators=(",", ":"))
