"""Deterministic HTTP regression via Hurl (https://hurl.dev).

Hurl runs plain-text .hurl files — requests and assertions fixed ahead of time — and is
the "confirmed regression" half of the API testing story: generic_http_api/assert_json_path
are for the LLM deciding what to check as it goes, run_hurl_suite is for checks that are
already known and just need to run the same way every time.

Hurl is an external binary, not a Python dependency; `uv sync` does not install it. See
https://hurl.dev/docs/installation.html (macOS: `brew install hurl`).
"""

import asyncio
import glob
import json
import os
import shutil
import tempfile
import time
from typing import Annotated, Dict, List, Optional

from junitparser import JUnitXml
from testzeus_hercules.config import get_global_conf
from testzeus_hercules.core.tools.api_calls import (
    interpolate_value,
    stringify_interpolated_value,
)
from testzeus_hercules.core.tools.tool_registry import tool
from testzeus_hercules.utils.logger import logger

HURL_BINARY = "hurl"


def _hurl_files(path: str) -> List[str]:
    """A directory runs every *.hurl file in it (non-recursive) as one suite; a file runs alone."""
    if os.path.isdir(path):
        return sorted(glob.glob(os.path.join(path, "*.hurl")))
    return [path]


def _write_hurl_log(text: str) -> None:
    """Append to proofs/hurl_logs.log, the same convention api_logs.log/security_logs.log follow."""
    proof_path = get_global_conf().get_proof_path()
    log_path = os.path.join(proof_path, "hurl_logs.log")
    with open(log_path, "a", encoding="utf-8") as f:
        f.write(text + "\n")


def _save_junit_copy(tmp_junit_path: str, hurl_path: str) -> Optional[str]:
    """
    Name the report's testsuite (hurl leaves it blank, which the HTML report renders as
    "suite-N") and save a copy under proofs/hurl_results/ so it survives independently of
    the temp file — this tool never hands the temp path itself to anything that might
    delete it, such as Hercules' own JUnit merge step.
    """
    try:
        xml = JUnitXml.fromfile(tmp_junit_path)
    except Exception as e:
        logger.warning("run_hurl_suite: could not parse JUnit report %s: %s", tmp_junit_path, e)
        return None

    suite_name = os.path.basename(hurl_path.rstrip("/")) or "hurl"
    for suite in xml:
        if not suite.name:
            suite.name = suite_name

    proof_path = get_global_conf().get_proof_path()
    results_dir = os.path.join(proof_path, "hurl_results")
    os.makedirs(results_dir, exist_ok=True)
    dest = os.path.join(results_dir, f"{suite_name}-{int(time.time() * 1000)}.xml")
    xml.write(dest)
    return dest


@tool(
    agent_names=["api_nav_agent"],
    name="run_hurl_suite",
    description=(
        "Run a deterministic HTTP regression suite written in Hurl's plain-text format "
        "(https://hurl.dev) — one or more .hurl files whose requests and assertions are "
        "fixed ahead of time, not decided by you at run time. Use this for a stable, "
        "already-written regression check; keep exploratory or context-dependent testing "
        "on generic_http_api/assert_json_path instead.\n"
        "Parameters:\n"
        "  - hurl_path: path to a single .hurl file, or a directory of *.hurl files that "
        "are all run together as one suite.\n"
        "  - variables: values for the suite's {{name}} placeholders. A value may itself "
        "be ${name} to reuse something captured earlier by extract_from_response.\n"
        "  - timeout_seconds: maximum time to let the suite run before it is killed.\n"
        "Requires the hurl binary on PATH; if it is missing this returns assertion: ERROR "
        "with an installation hint rather than failing obscurely.\n"
        "Returns assertion: PASSED only if every request in the suite passed, otherwise "
        "FAILED with each failing request's name and hurl's own diagnostic message. The "
        "full JUnit report is saved under this scenario's proofs folder."
    ),
)
async def run_hurl_suite(
    hurl_path: Annotated[str, "Path to a .hurl file, or a directory of *.hurl files to run together."],
    variables: Annotated[
        Optional[Dict[str, str]],
        "Values for the suite's {{name}} placeholders; a value may be ${name} to reuse a captured extract_from_response value.",
    ] = None,
    timeout_seconds: Annotated[float, "Maximum time to let the suite run, in seconds, before it is killed."] = 60.0,
) -> Annotated[str, "Minified JSON: assertion (PASSED/FAILED/ERROR), executed/succeeded/failed counts, failures[], and the saved JUnit report path."]:
    if shutil.which(HURL_BINARY) is None:
        result = {
            "assertion": "ERROR",
            "reason": "hurl is not installed or not on PATH. Install it (e.g. `brew install hurl` on macOS) — see https://hurl.dev/docs/installation.html",
        }
        logger.warning("run_hurl_suite: %s", result["reason"])
        return json.dumps(result, ensure_ascii=False, separators=(",", ":"))

    if not os.path.exists(hurl_path):
        result = {"assertion": "ERROR", "hurl_path": hurl_path, "reason": f"hurl_path not found: {hurl_path}"}
        logger.warning("run_hurl_suite: %s", result["reason"])
        return json.dumps(result, ensure_ascii=False, separators=(",", ":"))

    files = _hurl_files(hurl_path)
    if not files:
        result = {"assertion": "ERROR", "hurl_path": hurl_path, "reason": f"no .hurl files found under {hurl_path}"}
        logger.warning("run_hurl_suite: %s", result["reason"])
        return json.dumps(result, ensure_ascii=False, separators=(",", ":"))

    # Values may reference something extract_from_response captured earlier, bridging the
    # LLM-driven setup steps into a suite whose own execution is otherwise fully deterministic.
    # interpolate_value can return a non-str (a captured object/None/bool) when the whole
    # value is a placeholder; stringify_interpolated_value renders that as JSON literal
    # text so it's a valid --variable value instead of Python's repr()/str() spelling.
    resolved_vars = {k: stringify_interpolated_value(interpolate_value(v)) for k, v in (variables or {}).items()}

    # hurl 8.0.1 treats an existing --report-junit path as a report to merge into, and
    # panics if that file is empty — mkdtemp() reserves a fresh, race-free directory
    # without touching the report file itself, unlike mkstemp() which pre-creates it.
    junit_dir = tempfile.mkdtemp(prefix="hurl-junit-")
    junit_path = os.path.join(junit_dir, "report.xml")

    command = [HURL_BINARY, "--test", "--report-junit", junit_path]
    for k, v in resolved_vars.items():
        command += ["--variable", f"{k}={v}"]
    command += files

    start = time.perf_counter()
    process = await asyncio.create_subprocess_exec(*command, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
    try:
        stdout, stderr = await asyncio.wait_for(process.communicate(), timeout=timeout_seconds)
    except asyncio.TimeoutError:
        process.kill()
        await process.wait()
        shutil.rmtree(junit_dir, ignore_errors=True)
        result = {"assertion": "ERROR", "hurl_path": hurl_path, "reason": f"hurl run exceeded {timeout_seconds}s and was killed"}
        logger.warning("run_hurl_suite: %s", result["reason"])
        return json.dumps(result, ensure_ascii=False, separators=(",", ":"))
    duration = time.perf_counter() - start

    stdout_text = stdout.decode("utf-8", errors="replace")
    stderr_text = stderr.decode("utf-8", errors="replace")
    _write_hurl_log(f"=== run_hurl_suite {hurl_path} (exit {process.returncode}) ===\n{stdout_text}\n{stderr_text}")

    if not os.path.exists(junit_path):
        shutil.rmtree(junit_dir, ignore_errors=True)
        result = {
            "assertion": "ERROR",
            "hurl_path": hurl_path,
            "reason": f"hurl exited {process.returncode} without producing a JUnit report",
            "stderr": stderr_text[-2000:],
        }
        logger.warning("run_hurl_suite: %s", result["reason"])
        return json.dumps(result, ensure_ascii=False, separators=(",", ":"))

    try:
        xml = JUnitXml.fromfile(junit_path)
    except Exception as e:
        result = {"assertion": "ERROR", "hurl_path": hurl_path, "reason": f"could not parse hurl's JUnit report: {e}"}
        logger.warning("run_hurl_suite: %s", result["reason"])
        shutil.rmtree(junit_dir, ignore_errors=True)
        return json.dumps(result, ensure_ascii=False, separators=(",", ":"))

    executed = succeeded = failed = 0
    failures: List[Dict[str, str]] = []
    for suite in xml:
        for case in suite:
            executed += 1
            if case.result:
                failed += 1
                outcome = case.result[0]
                message = (getattr(outcome, "text", None) or getattr(outcome, "message", None) or "").strip()
                failures.append({"testcase": case.name, "message": message})
            else:
                succeeded += 1

    saved_report = _save_junit_copy(junit_path, hurl_path)
    shutil.rmtree(junit_dir, ignore_errors=True)

    result = {
        "assertion": "PASSED" if failed == 0 else "FAILED",
        "hurl_path": hurl_path,
        "executed": executed,
        "succeeded": succeeded,
        "failed": failed,
        "duration_seconds": round(duration, 2),
        "junit_report": saved_report,
    }
    if failures:
        result["failures"] = failures
    if failed:
        logger.info("run_hurl_suite FAILED: %s", result)
    return json.dumps(result, ensure_ascii=False, separators=(",", ":"))
