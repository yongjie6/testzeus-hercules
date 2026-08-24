"""A scenario that passes with no evidence must say so.

A tool that cannot capture what it was asked to capture appends a line to
EVIDENCE_MISSING.txt in the scenario's proofs folder. Without surfacing it, the
run reads as a clean pass whose proofs folder happens to be empty, and nothing
distinguishes "nothing to capture" from "capture failed" -- the same shape as
the false greens this report has already had to be taught to refuse.
"""

import asyncio
import xml.etree.ElementTree as ET
from pathlib import Path

from junitparser import Failure

from testzeus_hercules.utils.junit_helper import JUnitXMLGenerator

PASSING = {"is_assert": True, "is_passed": True, "terminate": "yes", "assert_summary": "ok"}


def report(tmp_path: Path, gaps: list[str] | None = None) -> dict[str, str]:
    """Build a one-scenario report and return its properties."""
    proofs = tmp_path / "proofs"
    proofs.mkdir(exist_ok=True)
    if gaps:
        (proofs / "EVIDENCE_MISSING.txt").write_text("\n".join(gaps) + "\n", encoding="utf-8")

    generator = JUnitXMLGenerator("F", "f.feature", "", str(proofs), "", "", "", "", "")
    generator.add_test_case("scenario", "F", PASSING, 1.0, {})
    out = tmp_path / "result.xml"
    asyncio.run(generator.write_xml(str(out)))
    root = ET.parse(out).getroot()
    return {p.get("name"): p.get("value") for p in root.iter("property")}


def test_a_pass_with_failed_captures_is_flagged(tmp_path):
    props = report(tmp_path, ["2026-01-01T00:00:00Z mp_login_as: fail to capture screenshot"])

    assert "1 次采集失败" in props["Evidence Missing"]


def test_a_clean_run_says_nothing(tmp_path):
    assert "Evidence Missing" not in report(tmp_path)


def test_every_failed_capture_is_counted(tmp_path):
    props = report(tmp_path, [f"tool_{i}: boom" for i in range(5)])

    assert "5 次采集失败" in props["Evidence Missing"]


def test_the_scenario_still_passes(tmp_path):
    # A missing screenshot is not itself a test failure. Flagging it must not
    # turn a real pass into a failure.
    proofs = tmp_path / "proofs"
    proofs.mkdir()
    (proofs / "EVIDENCE_MISSING.txt").write_text("x: boom\n", encoding="utf-8")

    generator = JUnitXMLGenerator("F", "f.feature", "", str(proofs), "", "", "", "", "")
    generator.add_test_case("scenario", "F", PASSING, 1.0, {})

    results = [case.result for case in generator.suite]
    flattened = [r for entry in results for r in (entry if isinstance(entry, list) else [entry])]
    assert not any(isinstance(r, Failure) for r in flattened)


def test_a_missing_proofs_folder_is_not_an_error(tmp_path):
    generator = JUnitXMLGenerator("F", "f.feature", "", str(tmp_path / "nope"), "", "", "", "", "")

    assert generator._evidence_gaps() == []
