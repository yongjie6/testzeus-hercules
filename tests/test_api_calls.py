"""Regression tests for the API nav agent's HTTP tool.

These cover the two paths that were broken: multipart encoding (which used a
non-existent ``httpx.FormData``) and the request timeout (which was hardcoded at
five seconds with no way to configure it).
"""

import asyncio
import json
import threading
import time
from http.server import BaseHTTPRequestHandler, HTTPServer
from typing import Any, Generator

import pytest
from testzeus_hercules.config import get_global_conf
from testzeus_hercules.core.tools.api_calls import (
    build_multipart_fields,
    generic_http_api,
)


class _EchoHandler(BaseHTTPRequestHandler):
    """Echoes the request's Content-Type and body; ``/slow`` stalls before replying."""

    def do_POST(self) -> None:
        if self.path == "/slow":
            time.sleep(2)
        length = int(self.headers.get("Content-Length") or 0)
        raw = self.rfile.read(length)
        payload = json.dumps(
            {
                "content_type": self.headers.get("Content-Type"),
                "body": raw.decode("utf-8", errors="replace"),
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


@pytest.fixture
def restore_timeout() -> Generator[None, None, None]:
    conf = get_global_conf()
    original = conf._config.get("API_REQUEST_TIMEOUT")
    try:
        yield
    finally:
        if original is None:
            conf._config.pop("API_REQUEST_TIMEOUT", None)
        else:
            conf._config["API_REQUEST_TIMEOUT"] = original


def test_multipart_text_fields_are_encoded_as_form_data(echo_server: str) -> None:
    """A payload with no file at all still has to go out as multipart, not urlencoded."""
    response, _ = asyncio.run(
        generic_http_api(
            method="POST",
            url=f"{echo_server}/echo",
            body={"name": "bulldog", "count": 2},
            body_mode="multipart",
        )
    )

    assert "'status_code':200" in response
    assert "multipart/form-data; boundary=" in response
    assert "Content-Disposition: form-data" in response
    assert "bulldog" in response
    assert "count" in response


def test_multipart_uploads_a_file_and_owns_the_content_type(echo_server: str, tmp_path: Any) -> None:
    """The caller's boundary-less Content-Type must be dropped so httpx can set its own."""
    fixture = tmp_path / "note.txt"
    fixture.write_text("hello-from-file")

    response, _ = asyncio.run(
        generic_http_api(
            method="POST",
            url=f"{echo_server}/echo",
            body={
                "note": "with file",
                "doc": {"file_path": str(fixture), "content_type": "text/plain"},
            },
            body_mode="multipart",
            headers={"Content-Type": "multipart/form-data"},
        )
    )

    assert "'status_code':200" in response
    assert "multipart/form-data; boundary=" in response
    assert "filename=" in response
    assert "note.txt" in response
    assert "Content-Type: text/plain" in response
    assert "hello-from-file" in response


def test_multipart_bad_file_path_returns_graceful_error_not_a_crash(echo_server: str) -> None:
    # build_multipart_fields used to run before _send_request's try/except, so a bad
    # file_path raised FileNotFoundError straight out of generic_http_api instead of
    # the module's usual {"status_type":"failure",...} JSON.
    response, _ = asyncio.run(
        generic_http_api(
            method="POST",
            url=f"{echo_server}/echo",
            body={"doc": {"file_path": "/no/such/file.png"}},
            body_mode="multipart",
        )
    )
    assert "'status_type':'failure'" in response
    assert "No such file or directory" in response


def test_build_multipart_fields_shapes_values_for_httpx(tmp_path: Any) -> None:
    fixture = tmp_path / "payload.bin"
    fixture.write_bytes(b"binary-content")

    fields = build_multipart_fields(
        {
            "plain": "text",
            "number": 7,
            "nested": {"a": 1},
            "upload": {"file_path": str(fixture), "filename": "renamed.bin", "content_type": "application/octet-stream"},
        }
    )

    # Text parts carry a None filename so httpx still picks multipart encoding.
    assert fields["plain"] == (None, "text")
    assert fields["number"] == (None, "7")
    assert fields["nested"] == (None, '{"a":1}')
    assert fields["upload"] == ("renamed.bin", b"binary-content", "application/octet-stream")


def test_build_multipart_fields_rejects_non_object_payloads() -> None:
    with pytest.raises(ValueError):
        build_multipart_fields("not-an-object")


def test_build_multipart_fields_accepts_a_json_string() -> None:
    assert build_multipart_fields('{"a": "b"}') == {"a": (None, "b")}


def test_request_timeout_is_configurable(echo_server: str, restore_timeout: None) -> None:
    """The timeout used to be pinned at 5s; the endpoint below stalls for 2s."""
    conf = get_global_conf()

    conf._config["API_REQUEST_TIMEOUT"] = "0.5"
    response, _ = asyncio.run(generic_http_api(method="POST", url=f"{echo_server}/slow", body={"x": 1}, body_mode="json"))
    assert "'status_type':'timeout'" in response
    assert "timed out after 0.5s" in response

    conf._config["API_REQUEST_TIMEOUT"] = "10"
    response, _ = asyncio.run(generic_http_api(method="POST", url=f"{echo_server}/slow", body={"x": 1}, body_mode="json"))
    assert "'status_code':200" in response


@pytest.mark.parametrize("bad_value", ["abc", "-5", "0", ""])
def test_invalid_timeout_falls_back_to_default(bad_value: str, restore_timeout: None) -> None:
    conf = get_global_conf()
    conf._config["API_REQUEST_TIMEOUT"] = bad_value
    assert conf.get_api_request_timeout() == 30.0
