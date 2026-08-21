"""Unit tests for cross-step variable capture and ${name} interpolation
(testzeus_hercules/core/tools/api_calls.py: extract_from_response, interpolate_value).

Covers extraction in isolation, interpolation's type-preservation vs text-substitution
split, and an end-to-end chain through generic_http_api against a real HTTP server —
the shape a login-then-authenticated-call scenario actually needs.
"""

import asyncio
import json
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from typing import Any, Generator

import pytest
from testzeus_hercules.config import get_global_conf
from testzeus_hercules.core.tools import api_calls
from testzeus_hercules.core.tools.api_calls import (
    extract_from_response,
    generic_http_api,
    interpolate_value,
)


@pytest.fixture(autouse=True)
def clear_variables() -> Generator[None, None, None]:
    """Variables are scoped by get_default_test_id(); wipe that scenario's namespace
    before and after each test so tests can't leak captures into one another."""
    key = get_global_conf().get_default_test_id()
    api_calls._VARIABLES.pop(key, None)
    yield
    api_calls._VARIABLES.pop(key, None)


def _extract(**kwargs) -> dict:
    return json.loads(asyncio.run(extract_from_response(**kwargs)))


LOGIN_RESPONSE = json.dumps(
    {
        "accessToken": "tok-abc123",
        "user": {"id": 7, "roles": ["admin", "qa"]},
        "expiresIn": 3600,
    }
)


def test_extract_from_response_stores_value():
    out = _extract(response_json=LOGIN_RESPONSE, path="$.accessToken", as_name="token")
    assert out == {"extracted": True, "name": "token", "path": "$.accessToken", "value": "tok-abc123"}


def test_extract_from_response_can_capture_a_whole_object():
    out = _extract(response_json=LOGIN_RESPONSE, path="$.user", as_name="user")
    assert out["extracted"] is True
    assert out["value"] == {"id": 7, "roles": ["admin", "qa"]}


def test_extract_from_response_path_not_found():
    out = _extract(response_json=LOGIN_RESPONSE, path="$.doesNotExist", as_name="x")
    assert out == {"extracted": False, "name": "x", "path": "$.doesNotExist", "reason": "path matched nothing"}


def test_extract_from_response_invalid_json():
    out = _extract(response_json="not json", path="$.x", as_name="x")
    assert out["extracted"] is False
    assert "not valid JSON" in out["reason"]


def test_extract_from_response_bad_jsonpath():
    out = _extract(response_json=LOGIN_RESPONSE, path="$[[[", as_name="x")
    assert out["extracted"] is False
    assert "bad JSONPath" in out["reason"]


def test_interpolate_full_match_preserves_type():
    _extract(response_json=LOGIN_RESPONSE, path="$.expiresIn", as_name="ttl")
    _extract(response_json=LOGIN_RESPONSE, path="$.user", as_name="user")

    # A field whose entire value is the placeholder keeps the captured value's own type.
    assert interpolate_value("${ttl}") == 3600
    assert interpolate_value("${user}") == {"id": 7, "roles": ["admin", "qa"]}


def test_interpolate_embedded_placeholder_substitutes_as_text():
    _extract(response_json=LOGIN_RESPONSE, path="$.user.id", as_name="uid")
    assert interpolate_value("/users/${uid}/profile") == "/users/7/profile"


def test_interpolate_embedded_null_renders_as_json_null_not_python_none():
    # A captured JSON null used to embed as the four characters "None" (str(None)),
    # producing a syntactically valid but semantically wrong URL/body with no warning.
    key = "resultId"
    api_calls._var_store()[key] = None
    assert interpolate_value(f"/orders/${{{key}}}/confirm") == "/orders/null/confirm"


def test_interpolate_embedded_bool_renders_as_json_literal():
    key = "active"
    api_calls._var_store()[key] = True
    assert interpolate_value(f"flag=${{{key}}}") == "flag=true"


def test_interpolate_embedded_object_is_json_encoded_as_text():
    _extract(response_json=LOGIN_RESPONSE, path="$.user", as_name="user")
    assert interpolate_value("note: ${user}") == 'note: {"id":7,"roles":["admin","qa"]}'


def test_interpolate_unresolved_placeholder_is_left_literal():
    # No prior extract_from_response call captured "missing" — a typo must surface in the
    # request, not silently vanish or raise deep inside request construction.
    assert interpolate_value("${missing}") == "${missing}"
    assert interpolate_value("prefix-${missing}-suffix") == "prefix-${missing}-suffix"


def test_interpolate_walks_nested_dicts_and_lists():
    _extract(response_json=LOGIN_RESPONSE, path="$.accessToken", as_name="token")
    payload = {
        "headers": {"Authorization": "Bearer ${token}"},
        "items": ["${token}", {"nested": "${token}"}],
    }
    out = interpolate_value(payload)
    assert out == {
        "headers": {"Authorization": "Bearer tok-abc123"},
        "items": ["tok-abc123", {"nested": "tok-abc123"}],
    }


def test_interpolate_leaves_non_string_scalars_untouched():
    assert interpolate_value(42) == 42
    assert interpolate_value(True) is True
    assert interpolate_value(None) is None


def test_interpolate_string_with_no_placeholder_is_returned_unchanged():
    assert interpolate_value("no placeholders here") == "no placeholders here"


def test_interpolate_multiple_distinct_placeholders_in_one_string():
    _extract(response_json=LOGIN_RESPONSE, path="$.user.id", as_name="uid")
    _extract(response_json=LOGIN_RESPONSE, path="$.accessToken", as_name="token")
    assert interpolate_value("/users/${uid}?auth=${token}") == "/users/7?auth=tok-abc123"


class _EchoHandler(BaseHTTPRequestHandler):
    """Echoes the path (with query string), request headers, and posted body (parsed if
    it was JSON, otherwise the raw text — multipart bodies fall through to raw text)."""

    def do_POST(self) -> None:
        length = int(self.headers.get("Content-Length") or 0)
        raw = self.rfile.read(length)
        try:
            received_body: Any = json.loads(raw)
        except json.JSONDecodeError:
            received_body = raw.decode("utf-8", errors="replace")
        payload = json.dumps(
            {
                "path": self.path,
                "authorization": self.headers.get("Authorization"),
                "headers": {k: v for k, v in self.headers.items()},
                "receivedBody": received_body,
            }
        ).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    def log_message(self, *args: Any) -> None:
        pass


@pytest.fixture(scope="module")
def echo_server() -> Generator[str, None, None]:
    server = HTTPServer(("127.0.0.1", 0), _EchoHandler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    try:
        yield f"http://127.0.0.1:{server.server_port}"
    finally:
        server.shutdown()
        server.server_close()


def test_end_to_end_login_then_authenticated_call_chain(echo_server: str):
    """The pattern this exists for: capture a token from one call, spend it on the next
    call's URL, Authorization header, and body without the model retyping the value."""
    login_response, _ = asyncio.run(
        generic_http_api(
            method="POST",
            url=f"{echo_server}/login",
            body={"accessToken": "tok-xyz789", "userId": "u-42"},
            body_mode="json",
        )
    )
    # generic_http_api's return string swaps double quotes for single quotes, so turn it
    # back into strict JSON before feeding it to extract_from_response.
    login_json = login_response.replace("'", '"')

    extraction = _extract(response_json=login_json, path="$.body.receivedBody.accessToken", as_name="token")
    assert extraction["extracted"] is True
    _extract(response_json=login_json, path="$.body.receivedBody.userId", as_name="uid")

    response, _ = asyncio.run(
        generic_http_api(
            method="POST",
            url=f"{echo_server}/users/${{uid}}/orders",
            auth_type="bearer",
            auth_value="${token}",
            body={"note": "auth is ${token}"},
            body_mode="json",
        )
    )

    assert "'path':'/users/u-42/orders'" in response
    assert "'authorization':'Bearer tok-xyz789'" in response
    assert "'note':'auth is tok-xyz789'" in response


def test_headers_and_query_params_are_interpolated(echo_server: str):
    """Interpolation isn't limited to url/body/auth_value — a custom header or a query
    parameter built from a captured value must resolve the same way."""
    _extract(response_json=LOGIN_RESPONSE, path="$.user.id", as_name="uid")
    _extract(response_json=LOGIN_RESPONSE, path="$.accessToken", as_name="token")

    response, _ = asyncio.run(
        generic_http_api(
            method="POST",
            url=f"{echo_server}/search",
            query_params={"ownerId": "${uid}"},
            headers={"X-Trace-Token": "${token}"},
            body={"q": "anything"},
            body_mode="json",
        )
    )

    assert "'path':'/search?ownerId=7'" in response
    assert "'X-Trace-Token':'tok-abc123'" in response


def test_multipart_body_values_are_interpolated(echo_server: str):
    """A captured value dropped into a multipart field must resolve before encoding,
    not get sent through as the literal ${name} placeholder."""
    _extract(response_json=LOGIN_RESPONSE, path="$.accessToken", as_name="token")

    response, _ = asyncio.run(
        generic_http_api(
            method="POST",
            url=f"{echo_server}/upload",
            body={"note": "token is ${token}"},
            body_mode="multipart",
        )
    )

    assert "'status_code':200" in response
    assert "token is tok-abc123" in response
    assert "${token}" not in response
