import json
from typing import Annotated, Any

from testzeus_hercules.core.tools.tool_registry import tool
from testzeus_hercules.utils.jsonpath_helper import find_json_path
from testzeus_hercules.utils.logger import logger

# Operators kept scalar-in/scalar-out (no tuples, no nested schemas) so the generated
# JSON Schema stays provider-portable — see the tool signatures below.
_OPERATORS = ("eq", "ne", "contains", "exists", "not_exists", "gt", "lt", "count_eq", "eq_path")


def _coerce(raw: str) -> Any:
    """Turn an `expected` string back into a JSON value: "true"->True, "0"->0, else the raw string."""
    try:
        return json.loads(raw)
    except (json.JSONDecodeError, TypeError):
        return raw


@tool(
    agent_names=["api_nav_agent"],
    name="assert_json_path",
    description=(
        "Make a deterministic assertion against a JSONPath in a previous tool's JSON response, instead of "
        "judging pass/fail by reading the response text. Always use this to verify a response field rather "
        "than deciding success yourself — the assertion result, not your own reading, is what should decide "
        "is_passed.\n"
        "Parameters:\n"
        "  - response_json: the JSON string returned by the API call being verified (e.g. from "
        "generic_http_api or esm_call).\n"
        "  - path: JSONPath into that JSON, for example $.body.myPage.hasScaleResult.\n"
        "  - operator: eq, ne, contains, exists, not_exists, gt, lt, count_eq, or eq_path.\n"
        '  - expected: the expected value as a string; JSON literals are honored ("true", "0", '
        '"null", \'"some string"\'), anything else is compared as a raw string. For eq_path, this is '
        "instead a second JSONPath compared against the first, for cross-field checks within the same "
        "response (e.g. a field that should mirror another field further up the document).\n"
        "Returns assertion: PASSED, FAILED, or ERROR, with the actual and expected values so a failure is "
        "diagnosable without re-reading the raw response."
    ),
)
async def assert_json_path(
    response_json: Annotated[str, "The JSON string returned by the API call being verified."],
    path: Annotated[str, "JSONPath into response_json, e.g. $.body.myPage.hasScaleResult."],
    operator: Annotated[str, "One of: eq, ne, contains, exists, not_exists, gt, lt, count_eq, eq_path."] = "eq",
    expected: Annotated[
        str,
        "Expected value as a string (JSON literals honored). For eq_path, a second JSONPath to compare against instead.",
    ] = "",
) -> Annotated[str, "Minified JSON: assertion (PASSED/FAILED/ERROR), path, operator, actual, expected."]:
    if operator not in _OPERATORS:
        result = {"assertion": "ERROR", "path": path, "operator": operator, "reason": f"unknown operator {operator!r}, expected one of {list(_OPERATORS)}"}
        logger.warning("assert_json_path: %s", result["reason"])
        return json.dumps(result, ensure_ascii=False, separators=(",", ":"))

    try:
        doc = json.loads(response_json)
    except (json.JSONDecodeError, TypeError) as e:
        result = {"assertion": "ERROR", "path": path, "operator": operator, "reason": f"response_json is not valid JSON: {e}"}
        logger.warning("assert_json_path: %s", result["reason"])
        return json.dumps(result, ensure_ascii=False, separators=(",", ":"))

    try:
        matches = find_json_path(doc, path)
    except Exception as e:
        result = {"assertion": "ERROR", "path": path, "operator": operator, "reason": f"bad JSONPath {path!r}: {e}"}
        logger.warning("assert_json_path: %s", result["reason"])
        return json.dumps(result, ensure_ascii=False, separators=(",", ":"))

    result: dict = {"path": path, "operator": operator}

    if operator == "exists":
        passed = len(matches) > 0
        result["actual"] = f"{len(matches)} match(es)"
    elif operator == "not_exists":
        passed = len(matches) == 0
        result["actual"] = f"{len(matches)} match(es)"
    elif operator == "count_eq":
        want = _coerce(expected)
        passed = len(matches) == want
        result["actual"] = len(matches)
        result["expected"] = want
    elif not matches:
        # A path matching nothing is a failure, not a silent pass, for every operator
        # except the two that explicitly test for absence above.
        result.update({"assertion": "FAILED", "reason": "path matched nothing", "expected": _coerce(expected)})
        logger.info("assert_json_path FAILED: %s", result)
        return json.dumps(result, ensure_ascii=False, separators=(",", ":"))
    else:
        actual = matches[0]
        result["actual"] = actual
        if operator == "eq_path":
            try:
                other = find_json_path(doc, expected)
            except Exception as e:
                result.update({"assertion": "ERROR", "reason": f"bad comparison JSONPath {expected!r}: {e}"})
                logger.warning("assert_json_path: %s", result["reason"])
                return json.dumps(result, ensure_ascii=False, separators=(",", ":"))
            if not other:
                result.update({"assertion": "FAILED", "reason": f"comparison path {expected!r} matched nothing"})
                logger.info("assert_json_path FAILED: %s", result)
                return json.dumps(result, ensure_ascii=False, separators=(",", ":"))
            result["expected"] = other[0]
            result["expected_from"] = expected
            passed = actual == other[0]
        else:
            want = _coerce(expected)
            result["expected"] = want
            if operator == "eq":
                passed = actual == want
            elif operator == "ne":
                passed = actual != want
            elif operator == "contains":
                passed = str(want) in str(actual)
            elif operator in ("gt", "lt"):
                try:
                    passed = actual > want if operator == "gt" else actual < want
                except TypeError as e:
                    result.update({"assertion": "ERROR", "reason": f"cannot compare {type(actual).__name__} {actual!r} with {type(want).__name__} {want!r}: {e}"})
                    logger.warning("assert_json_path: %s", result["reason"])
                    return json.dumps(result, ensure_ascii=False, separators=(",", ":"))

    result["assertion"] = "PASSED" if passed else "FAILED"
    if not passed:
        logger.info("assert_json_path FAILED: %s", result)
    return json.dumps(result, ensure_ascii=False, separators=(",", ":"))
