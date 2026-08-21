"""Integration tests for run_hurl_suite (testzeus_hercules/core/tools/hurl_calls.py).

Runs the real `hurl` binary against a local HTTP server — these tests are skipped, not
failed, if hurl isn't installed (e.g. `brew install hurl` on macOS, see
https://hurl.dev/docs/installation.html), matching how tests should degrade around an
external dependency they don't control.
"""

import asyncio
import json
import shutil
import threading
import time
from http.server import BaseHTTPRequestHandler, HTTPServer
from typing import Any, Generator

import pytest
from junitparser import JUnitXml
from testzeus_hercules.core.tools import api_calls
from testzeus_hercules.core.tools.hurl_calls import run_hurl_suite

pytestmark = pytest.mark.skipif(shutil.which("hurl") is None, reason="hurl binary not installed")


class _EchoHandler(BaseHTTPRequestHandler):
    def do_GET(self) -> None:
        if self.path == "/slow":
            time.sleep(3)
        if self.path == "/me":
            body = json.dumps({"authorization": self.headers.get("Authorization")}).encode()
        else:
            body = json.dumps({"status": "ok"}).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

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


def _run(**kwargs) -> dict:
    return json.loads(asyncio.run(run_hurl_suite(**kwargs)))


def test_passing_suite(tmp_path, echo_server: str):
    hurl_file = tmp_path / "ok.hurl"
    hurl_file.write_text(f"""
GET {echo_server}/health
HTTP 200
[Asserts]
jsonpath "$.status" == "ok"
""")
    out = _run(hurl_path=str(hurl_file))
    assert out["assertion"] == "PASSED"
    assert out["executed"] == 1
    assert out["succeeded"] == 1
    assert out["failed"] == 0
    assert "failures" not in out


def test_failing_suite_reports_hurl_diagnostics(tmp_path, echo_server: str):
    hurl_file = tmp_path / "bad.hurl"
    hurl_file.write_text(f"""
GET {echo_server}/health
HTTP 200
[Asserts]
jsonpath "$.status" == "definitely-wrong"
""")
    out = _run(hurl_path=str(hurl_file))
    assert out["assertion"] == "FAILED"
    assert out["executed"] == 1
    assert out["failed"] == 1
    assert len(out["failures"]) == 1
    # hurl reports the testcase name as the path it was given on the command line, not a
    # bare basename — the tool passes str(hurl_path) through as-is.
    assert out["failures"][0]["testcase"] == str(hurl_file)
    assert "definitely-wrong" in out["failures"][0]["message"] or "status" in out["failures"][0]["message"]


def test_directory_runs_every_hurl_file_as_one_suite(tmp_path, echo_server: str):
    (tmp_path / "a_ok.hurl").write_text(f"""
GET {echo_server}/health
HTTP 200
""")
    (tmp_path / "b_bad.hurl").write_text(f"""
GET {echo_server}/health
HTTP 418
""")
    out = _run(hurl_path=str(tmp_path))
    assert out["executed"] == 2
    assert out["succeeded"] == 1
    assert out["failed"] == 1
    assert out["assertion"] == "FAILED"


def test_junit_report_is_saved_with_a_real_suite_name(tmp_path, echo_server: str):
    hurl_file = tmp_path / "named.hurl"
    hurl_file.write_text(f"""
GET {echo_server}/health
HTTP 200
""")
    out = _run(hurl_path=str(hurl_file))
    assert out["junit_report"] is not None
    saved = JUnitXml.fromfile(out["junit_report"])
    suite_names = [s.name for s in saved]
    assert suite_names == ["named.hurl"]


def test_missing_hurl_path_is_an_error():
    out = _run(hurl_path="/no/such/file.hurl")
    assert out["assertion"] == "ERROR"
    assert "not found" in out["reason"]


def test_hurl_binary_missing_is_a_diagnosable_error(monkeypatch, tmp_path):
    monkeypatch.setattr("testzeus_hercules.core.tools.hurl_calls.shutil.which", lambda _: None)
    out = _run(hurl_path=str(tmp_path / "whatever.hurl"))
    assert out["assertion"] == "ERROR"
    assert "not installed" in out["reason"]


def test_timeout_kills_the_process_and_reports_an_error(tmp_path, echo_server: str):
    hurl_file = tmp_path / "slow.hurl"
    hurl_file.write_text(f"""
GET {echo_server}/slow
HTTP 200
""")
    out = _run(hurl_path=str(hurl_file), timeout_seconds=0.5)
    assert out["assertion"] == "ERROR"
    assert "exceeded" in out["reason"]


def test_variables_interpolate_captured_values_into_hurl_placeholders(tmp_path, echo_server: str):
    """A value from extract_from_response feeds hurl's {{token}} the same way it would
    feed generic_http_api's ${token} — the two chaining mechanisms bridge cleanly."""
    key = api_calls.get_global_conf().get_default_test_id()
    api_calls._VARIABLES.pop(key, None)
    try:
        login_response = json.dumps({"accessToken": "tok-from-hercules"})
        asyncio.run(api_calls.extract_from_response(response_json=login_response, path="$.accessToken", as_name="captured_token"))

        hurl_file = tmp_path / "auth.hurl"
        hurl_file.write_text(f"""
GET {echo_server}/me
Authorization: Bearer {{{{token}}}}
HTTP 200
[Asserts]
jsonpath "$.authorization" == "Bearer tok-from-hercules"
""")
        out = _run(hurl_path=str(hurl_file), variables={"token": "${captured_token}"})
        assert out["assertion"] == "PASSED", out
    finally:
        api_calls._VARIABLES.pop(key, None)


def test_variables_stringify_a_captured_null(tmp_path, echo_server: str):
    """A captured JSON null used to reach the hurl CLI as a bare f-string of Python's
    interpolate_value() return, so `--variable uid=None` (Python's str(None) spelling)
    went out instead of the JSON-correct `uid=null`. Asserting on hurl's own `url`
    variable makes the resolved text observable, unlike asserting HTTP 200 (which
    passes either way since the echo server ignores the query string)."""
    key = api_calls.get_global_conf().get_default_test_id()
    api_calls._VARIABLES.pop(key, None)
    try:
        response = json.dumps({"resultId": None})
        asyncio.run(api_calls.extract_from_response(response_json=response, path="$.resultId", as_name="rid"))

        hurl_file = tmp_path / "null_var.hurl"
        hurl_file.write_text(f"""
GET {echo_server}/health?owner={{{{rid}}}}
HTTP 200
[Asserts]
url == "{echo_server}/health?owner=null"
""")
        out = _run(hurl_path=str(hurl_file), variables={"rid": "${rid}"})
        assert out["assertion"] == "PASSED", out
    finally:
        api_calls._VARIABLES.pop(key, None)
