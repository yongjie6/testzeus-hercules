import base64
import json
import os
import re
import time
from collections import defaultdict
from typing import Annotated, Any, Dict, Optional, Tuple

import httpx
import jsonschema
from testzeus_hercules.config import get_global_conf
from testzeus_hercules.core.tools.tool_registry import api_logger as file_logger
from testzeus_hercules.core.tools.tool_registry import tool
from testzeus_hercules.utils.jsonpath_helper import find_json_path
from testzeus_hercules.utils.logger import logger

# ------------------------------------------------------------------------------
# Logging and Utility Functions
# ------------------------------------------------------------------------------


# Keeps a multi-megabyte upload or download from flooding api_logs.log while still
# leaving ordinary JSON payloads intact in the proof trail.
MAX_LOGGED_BODY_CHARS = 65536


def truncate_for_log(body: str) -> str:
    """Cap a logged payload, marking how much was dropped."""
    if len(body) <= MAX_LOGGED_BODY_CHARS:
        return body
    return f"{body[:MAX_LOGGED_BODY_CHARS]}...[truncated, {len(body)} chars total]"


async def log_request(request: httpx.Request) -> None:
    """
    Log details of the outgoing HTTP request.
    """
    timestamp = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime())
    try:
        # Multipart bodies are streamed, so `request.content` is unavailable until the
        # stream is read. `aread()` buffers it and swaps in a replayable stream, which
        # leaves the request itself sendable.
        body_bytes = await request.aread()
        body = truncate_for_log(body_bytes.decode("utf-8", errors="ignore")) if body_bytes else None
    except Exception as e:
        import traceback

        traceback.print_exc()
        logger.exception(f"Error reading request body for logging: {e}")
        body = f"Failed to read request: {e}"
    log_data = {
        "request_data": {
            "timestamp": timestamp,
            "method": request.method,
            "url": str(request.url),
            "headers": dict(request.headers),
            "body": body,
        }
    }
    file_logger(json.dumps(log_data))


async def log_response(response: httpx.Response) -> None:
    """
    Log details of the incoming HTTP response.
    """
    timestamp = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime())
    try:
        body_bytes = await response.aread()
        body = truncate_for_log(body_bytes.decode("utf-8", errors="ignore"))
    except Exception as e:
        import traceback

        traceback.print_exc()
        body = f"Failed to read response: {e}"
    log_data = {
        "response_data": {
            "timestamp": timestamp,
            "status_code": response.status_code,
            "headers": dict(response.headers),
            "body": body,
        }
    }
    file_logger(json.dumps(log_data))


def determine_status_type(status_code: int) -> str:
    """
    Categorize the HTTP status code.
    """
    if 200 <= status_code < 300:
        return "success"
    elif 300 <= status_code < 400:
        return "redirect"
    elif 400 <= status_code < 500:
        return "client_error"
    elif 500 <= status_code < 600:
        return "server_error"
    return "unknown"


async def handle_error_response(e: httpx.HTTPStatusError) -> dict:
    """
    Extract error details from an HTTPStatusError.
    """
    try:
        error_detail = e.response.json()
    except Exception as ex:
        import traceback

        traceback.print_exc()
        logger.exception(f"Error extracting error details: {ex}")
        error_detail = e.response.text or "No details"
    return {
        "error": str(e),
        "error_detail": error_detail,
        "status_code": e.response.status_code,
        "status_type": determine_status_type(e.response.status_code),
    }


def build_multipart_fields(body: Any) -> Dict[str, Any]:
    """
    Turn a flat payload into the ``files`` mapping httpx needs for multipart/form-data.

    A value shaped like ``{"file_path": "...", "filename": ..., "content_type": ...}``
    is read from disk and sent as a file part; every other value becomes a plain text
    part. Text parts are passed through ``files`` with a ``None`` filename because httpx
    only selects multipart encoding when ``files`` is non-empty, so a payload carrying
    no actual file would otherwise be urlencoded.
    """
    if isinstance(body, str):
        try:
            body = json.loads(body)
        except json.JSONDecodeError:
            raise ValueError("multipart body must be a JSON object mapping field names to values")
    if not isinstance(body, dict):
        raise ValueError("multipart body must be a JSON object mapping field names to values")

    fields: Dict[str, Any] = {}
    for key, value in body.items():
        if isinstance(value, dict) and "file_path" in value:
            file_path = value["file_path"]
            with open(file_path, "rb") as fh:
                content = fh.read()
            filename = value.get("filename") or os.path.basename(file_path)
            content_type = value.get("content_type")
            fields[key] = (filename, content, content_type) if content_type else (filename, content)
        else:
            fields[key] = (None, stringify_interpolated_value(value))
    return fields


# ------------------------------------------------------------------------------
# Core Request Helper
# ------------------------------------------------------------------------------


async def _send_request(
    method: str,
    url: str,
    *,
    query_params: Optional[Dict[str, Any]] = None,
    body: Optional[Any] = None,
    body_mode: Optional[str] = None,
    headers: Optional[Dict[str, str]] = None,
) -> Tuple[str, float]:
    """
    Send an HTTP request using the given method and parameters.

    The 'body_mode' parameter specifies how to process the request body:
      - "multipart": Encodes a dict as multipart/form-data.
      - "urlencoded": Encodes a dict as application/x-www-form-urlencoded.
      - "raw": Sends the body as a raw string (caller must set Content-Type).
      - "binary": Sends the body as raw bytes (defaults to application/octet-stream).
      - "json": Encodes the body as JSON.
      - None: No body is sent.
    """
    query_params = query_params or {}
    headers = headers.copy() if headers else {}
    req_kwargs = {"params": query_params}

    timeout_seconds = get_global_conf().get_api_request_timeout()
    start_time = time.perf_counter()
    try:
        # Body encoding happens inside the try so a bad multipart file_path (or any
        # other encoding failure) falls through to the same graceful error handling
        # as a request failure, instead of raising past this function uncaught.
        if body_mode == "multipart" and body:
            # httpx owns the Content-Type here so the generated boundary makes it into
            # the header; a caller-supplied multipart Content-Type would lack it.
            for header_name in [name for name in headers if name.lower() == "content-type"]:
                headers.pop(header_name)
            req_kwargs["files"] = build_multipart_fields(body)

        elif body_mode == "urlencoded" and body:
            headers.setdefault("Content-Type", "application/x-www-form-urlencoded")
            req_kwargs["data"] = body

        elif body_mode == "raw" and body:
            req_kwargs["content"] = body

        elif body_mode == "binary" and body:
            headers.setdefault("Content-Type", "application/octet-stream")
            req_kwargs["content"] = body

        elif body_mode == "json" and body:
            headers.setdefault("Content-Type", "application/json")
            req_kwargs["json"] = body

        async with httpx.AsyncClient(
            event_hooks={"request": [log_request], "response": [log_response]},
            timeout=httpx.Timeout(timeout_seconds),
        ) as client:
            response = await client.request(method, url, headers=headers, **req_kwargs)
            response.raise_for_status()
            duration = time.perf_counter() - start_time

            try:
                parsed_body = response.json()
            except Exception as e:
                import traceback

                traceback.print_exc()
                logger.exception(f"Error parsing response body: {e}")
                parsed_body = response.text or ""
            result = {
                "status_code": response.status_code,
                "status_type": determine_status_type(response.status_code),
                "body": parsed_body,
            }
            # Minify the JSON response and replace double quotes with single quotes.
            result_str = json.dumps(result, separators=(",", ":")).replace('"', "'")
            return result_str, duration

    except httpx.HTTPStatusError as e:
        duration = time.perf_counter() - start_time
        logger.error(f"HTTP error: {e}")
        error_data = await handle_error_response(e)
        return json.dumps(error_data, separators=(",", ":")).replace('"', "'"), duration

    except httpx.TimeoutException as e:
        duration = time.perf_counter() - start_time
        # httpx timeout exceptions carry an empty str(), so name the cause explicitly —
        # otherwise the agent sees a blank error and cannot tell why the call failed.
        message = f"Request timed out after {timeout_seconds}s ({type(e).__name__}). Raise API_REQUEST_TIMEOUT if this endpoint is expected to be slow."
        logger.error(message)
        error_data = {"error": message, "status_code": None, "status_type": "timeout"}
        return json.dumps(error_data, separators=(",", ":")).replace('"', "'"), duration

    except Exception as e:
        import traceback

        traceback.print_exc()
        duration = time.perf_counter() - start_time
        message = str(e) or type(e).__name__
        logger.error(f"Unexpected error: {message}")
        error_data = {"error": message, "status_code": None, "status_type": "failure"}
        return json.dumps(error_data, separators=(",", ":")).replace('"', "'"), duration


# ------------------------------------------------------------------------------
# Cross-step Variables: extract a value from one response, reuse it in a later call
# ------------------------------------------------------------------------------

# Keyed by get_default_test_id(), matching the scoping state_handler.py already uses for
# store_data/store_run_data — one scenario run gets its own namespace, so a variable
# extracted in one Scenario can never leak into another.
_VARIABLES: Dict[str, Dict[str, Any]] = defaultdict(dict)

_VAR_PATTERN = re.compile(r"\$\{([A-Za-z_][A-Za-z0-9_]*)\}")


def _var_store() -> Dict[str, Any]:
    return _VARIABLES[get_global_conf().get_default_test_id()]


def set_variable_value(as_name: str, value: Any) -> None:
    """Store a value under ``${as_name}`` for the rest of this scenario.

    Named rather than letting other modules reach into ``_var_store()`` directly: the UI-side
    assertions capture into the same namespace, so an API-created id and a UI-read label are
    referenced identically, and there is exactly one place that decides what that namespace is.
    """
    _var_store()[as_name] = value


def stringify_interpolated_value(value: Any) -> str:
    """
    Render a captured value as plain text for a context that can't carry a native type
    — a CLI argument, a header, a substring of a larger string. A string passes through
    as-is; everything else renders as its JSON literal text (null/true/false/numbers),
    not Python's str() spelling (None/True) — str(None) would otherwise embed the four
    literal characters "None" into a request or a shell argument.
    """
    if isinstance(value, str):
        return value
    return json.dumps(value, separators=(",", ":"))


def interpolate_value(value: Any) -> Any:
    """
    Replace ``${name}`` placeholders with values captured by extract_from_response.

    A string that is *exactly* one placeholder (e.g. ``"${token}"``) is replaced in place
    and keeps the captured value's own type — an int stays an int, a dict stays a dict — so
    a captured object can be dropped whole into a JSON body field. A placeholder embedded in
    a larger string (e.g. ``"/pet/${petId}"``) is substituted as text. Dicts and lists are
    walked recursively. A name that was never captured is left as the literal ``${name}`` so
    a typo shows up in the request instead of raising deep inside request construction.
    """
    if isinstance(value, str):
        store = _var_store()
        full_match = _VAR_PATTERN.fullmatch(value)
        if full_match:
            name = full_match.group(1)
            return store.get(name, value)

        def _sub(m: "re.Match[str]") -> str:
            name = m.group(1)
            if name not in store:
                return m.group(0)
            return stringify_interpolated_value(store[name])

        return _VAR_PATTERN.sub(_sub, value)
    if isinstance(value, dict):
        return {k: interpolate_value(v) for k, v in value.items()}
    if isinstance(value, list):
        return [interpolate_value(v) for v in value]
    return value


@tool(
    agent_names=["api_nav_agent"],
    name="extract_from_response",
    description=(
        "Capture a value from a previous tool's JSON response so a later generic_http_api call can "
        "reuse it, instead of retyping the value yourself (which is how a login token gets garbled "
        "between steps). After calling this, reference the captured value anywhere in a later call's "
        "url, query_params, headers, auth_value, or body as ${name} — a field whose entire value is "
        "${name} keeps the captured value's original type (a captured object stays an object); ${name} "
        "embedded inside a longer string, like a URL path, is substituted as text.\n"
        "Parameters:\n"
        "  - response_json: the JSON string returned by the API call to extract from.\n"
        "  - path: JSONPath to the value, for example $.body.accessToken.\n"
        "  - as_name: the variable name to store it under, for example token.\n"
        "The captured value is scoped to this scenario only; it is never visible to other scenarios."
    ),
)
async def extract_from_response(
    response_json: Annotated[str, "The JSON string returned by the API call to extract from."],
    path: Annotated[str, "JSONPath to the value to capture, e.g. $.body.accessToken."],
    as_name: Annotated[str, "Variable name to store the value under; reference it later as ${name}."],
) -> Annotated[str, "Minified JSON: extracted (bool), name, path, and value (or reason on failure)."]:
    try:
        doc = json.loads(response_json)
    except (json.JSONDecodeError, TypeError) as e:
        result = {"extracted": False, "name": as_name, "path": path, "reason": f"response_json is not valid JSON: {e}"}
        logger.warning("extract_from_response: %s", result["reason"])
        return json.dumps(result, ensure_ascii=False, separators=(",", ":"))

    try:
        matches = find_json_path(doc, path)
    except Exception as e:
        result = {"extracted": False, "name": as_name, "path": path, "reason": f"bad JSONPath {path!r}: {e}"}
        logger.warning("extract_from_response: %s", result["reason"])
        return json.dumps(result, ensure_ascii=False, separators=(",", ":"))

    if not matches:
        result = {"extracted": False, "name": as_name, "path": path, "reason": "path matched nothing"}
        logger.info("extract_from_response: %s", result)
        return json.dumps(result, ensure_ascii=False, separators=(",", ":"))

    value = matches[0]
    set_variable_value(as_name, value)
    return json.dumps({"extracted": True, "name": as_name, "path": path, "value": value}, ensure_ascii=False, separators=(",", ":"))


# ------------------------------------------------------------------------------
# JSON Schema Response Validation
# ------------------------------------------------------------------------------


@tool(
    agent_names=["api_nav_agent"],
    name="validate_response_schema",
    description=(
        "Validate a previous tool's JSON response against a JSON Schema, instead of eyeballing whether "
        "the response looks right. Use this when a step needs to confirm a response's overall shape "
        "(required fields present, correct types) rather than one specific field's value — for a single "
        "field, assert_json_path is more direct.\n"
        "Parameters:\n"
        "  - response_json: the JSON string returned by the API call being verified.\n"
        "  - json_schema: a JSON Schema (draft-07 or later) as a JSON string.\n"
        "  - path: optional JSONPath into response_json to validate a sub-object against the schema "
        "instead of the whole response, e.g. $.body. Defaults to the whole response.\n"
        "Returns assertion: PASSED or FAILED, with the list of schema violations (path within the "
        "document and message) when it fails."
    ),
)
async def validate_response_schema(
    response_json: Annotated[str, "The JSON string returned by the API call being verified."],
    json_schema: Annotated[str, "A JSON Schema (draft-07 or later), as a JSON string."],
    path: Annotated[str, "Optional JSONPath to the sub-object to validate; defaults to the whole response."] = "",
) -> Annotated[str, "Minified JSON: assertion (PASSED/FAILED/ERROR), and violations (path, message) on failure."]:
    try:
        doc = json.loads(response_json)
    except (json.JSONDecodeError, TypeError) as e:
        result = {"assertion": "ERROR", "reason": f"response_json is not valid JSON: {e}"}
        logger.warning("validate_response_schema: %s", result["reason"])
        return json.dumps(result, ensure_ascii=False, separators=(",", ":"))

    try:
        schema = json.loads(json_schema)
    except (json.JSONDecodeError, TypeError) as e:
        result = {"assertion": "ERROR", "reason": f"json_schema is not valid JSON: {e}"}
        logger.warning("validate_response_schema: %s", result["reason"])
        return json.dumps(result, ensure_ascii=False, separators=(",", ":"))

    instance = doc
    if path:
        try:
            matches = find_json_path(doc, path)
        except Exception as e:
            result = {"assertion": "ERROR", "path": path, "reason": f"bad JSONPath {path!r}: {e}"}
            logger.warning("validate_response_schema: %s", result["reason"])
            return json.dumps(result, ensure_ascii=False, separators=(",", ":"))
        if not matches:
            result = {"assertion": "FAILED", "path": path, "reason": "path matched nothing"}
            logger.info("validate_response_schema: %s", result)
            return json.dumps(result, ensure_ascii=False, separators=(",", ":"))
        instance = matches[0]

    try:
        validator_cls = jsonschema.validators.validator_for(schema)
        validator_cls.check_schema(schema)
        validator = validator_cls(schema)
        errors = sorted(validator.iter_errors(instance), key=lambda e: list(e.absolute_path))
    except jsonschema.exceptions.SchemaError as e:
        result = {"assertion": "ERROR", "reason": f"json_schema is not a valid JSON Schema: {e.message}"}
        logger.warning("validate_response_schema: %s", result["reason"])
        return json.dumps(result, ensure_ascii=False, separators=(",", ":"))

    if not errors:
        return json.dumps({"assertion": "PASSED", "path": path or "$"}, ensure_ascii=False, separators=(",", ":"))

    violations = [{"path": "$" + "".join(f"[{p!r}]" if isinstance(p, str) else f"[{p}]" for p in e.absolute_path), "message": e.message} for e in errors]
    result = {"assertion": "FAILED", "path": path or "$", "violations": violations}
    logger.info("validate_response_schema FAILED: %s", result)
    return json.dumps(result, ensure_ascii=False, separators=(",", ":"))


# ------------------------------------------------------------------------------
# Generic HTTP API Function Covering All Combinations
# ------------------------------------------------------------------------------


@tool(
    agent_names=["api_nav_agent"],
    name="generic_http_api",
    description=(
        "Generic HTTP API call that supports any combination of HTTP method, "
        "authentication, query parameters, and request body encoding. "
        "Parameters:\n"
        "  - method: HTTP method (GET, POST, PUT, PATCH, DELETE, etc.).\n"
        "  - url: The API endpoint URL.\n"
        "  - auth_type: Authentication type. Options: basic, jwt, form_login, bearer, api_key.\n"
        "  - auth_value: For 'basic', pass [username, password]; for others, a string.\n"
        "  - query_params: URL query parameters (dict).\n"
        "  - body: Request payload.\n"
        "  - body_mode: How to encode the body. Options: multipart, urlencoded, raw, binary, json.\n"
        "    For 'multipart', body is an object of field names to values; a value shaped like\n"
        "    {'file_path': '/abs/path', 'filename': 'optional', 'content_type': 'optional'} is\n"
        "    uploaded as a file part, anything else is sent as a text part.\n"
        "  - headers: Additional HTTP headers (dict).\n"
        "This single function can generate any combination supported by _send_request.\n"
        "Any of url, query_params, headers, auth_value, or body may reference a value captured "
        "earlier by extract_from_response as ${name}."
    ),
)
async def generic_http_api(
    method: Annotated[str, "HTTP method (e.g. GET, POST, PUT, PATCH, DELETE, etc.)."],
    url: Annotated[str, "The API endpoint URL."],
    auth_type: Annotated[
        str,
        "Authentication type. Options: basic, jwt, form_login, bearer, api_key. (Optional)",
    ] = None,
    auth_value: Annotated[
        Any,
        "Authentication value: for 'basic' provide [username, password]; for others, provide a string. (Optional)",
    ] = None,
    query_params: Annotated[Optional[Dict[str, Any]], "URL query parameters."] = None,
    body: Annotated[
        Any,
        "Request payload. For multipart, an object of field names to values, where a value like {'file_path': '/abs/path'} is uploaded as a file part.",
    ] = None,
    body_mode: Annotated[
        str,
        "Body mode: multipart, urlencoded, raw, binary, or json. (Optional)",
    ] = None,
    headers: Annotated[Optional[Dict[str, str]], "Additional HTTP headers."] = None,
) -> Annotated[Tuple[str, float], "Minified JSON response and call duration (in seconds)."]:
    # Resolve any ${name} placeholders left by extract_from_response before touching auth
    # or dispatch, so an interpolated auth_value flows into the header construction below
    # exactly like a literal one would.
    url = interpolate_value(url)
    auth_value = interpolate_value(auth_value)
    query_params = interpolate_value(query_params)
    body = interpolate_value(body)
    headers = interpolate_value(headers)

    request_headers = dict(headers or {})
    request_query_params = dict(query_params or {})

    # Set authentication headers based on auth_type.
    if auth_type:
        auth_type = auth_type.lower()
        if auth_type == "basic" and isinstance(auth_value, list) and len(auth_value) == 2:
            creds = f"{auth_value[0]}:{auth_value[1]}"
            token = base64.b64encode(creds.encode()).decode()
            request_headers["Authorization"] = f"Basic {token}"
        elif auth_type == "jwt":
            request_headers["Authorization"] = f"JWT {auth_value}"
        elif auth_type == "form_login":
            request_headers["X-Form-Login"] = auth_value
        elif auth_type == "bearer":
            request_headers["Authorization"] = f"Bearer {auth_value}"
        elif auth_type == "api_key":
            request_headers["x-api-key"] = auth_value

    return await _send_request(
        method,
        url,
        query_params=request_query_params,
        body=body,
        body_mode=body_mode,
        headers=request_headers,
    )
