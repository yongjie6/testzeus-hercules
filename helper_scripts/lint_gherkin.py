#!/usr/bin/env python
"""Report .feature files that aren't standard Gherkin, without changing how Hercules runs
them.

Hercules splits a .feature file into per-Scenario files with a plain regex
(utils/gherkin_helper.py) rather than a real Gherkin parser, on purpose: it lets a
non-engineer write lowercase step keywords, skip the Feature: line, or otherwise bend the
grammar, and it'll still run. That leniency is a feature, not a bug — this script never
touches the execution path.

What it's for: a case authored with a Markdown-style Examples separator row
(`|---|---|`) silently gets parsed as one more data row by the regex splitter, producing
a bogus test case with values like "-------------" nobody meant to test. Full Gherkin
Scenario Outline expansion — one pickle per Examples row — also only shows up here, so
this is where to check what a stricter Gherkin toolchain (or a future generator) would
actually see.

Usage:
    python helper_scripts/lint_gherkin.py                  # scans tests/ and opt/cases/
    python helper_scripts/lint_gherkin.py path/to/dir ...   # scans specific paths instead
    python helper_scripts/lint_gherkin.py --strict          # exit 1 if anything is non-standard

Default exit code is always 0 — this is informational, not a CI gate.
"""

import argparse
import re
import sys
from pathlib import Path
from typing import List

from gherkin import Compiler, Parser

DEFAULT_SCAN_PATHS = ["tests", "opt/cases"]

_REGEX_SCENARIO = re.compile(r"^\s*scenario(?:\s+outline)?\s*:", re.IGNORECASE | re.MULTILINE)


def find_feature_files(paths: List[str]) -> List[Path]:
    files: List[Path] = []
    for raw in paths:
        p = Path(raw)
        if p.is_file() and p.suffix == ".feature":
            files.append(p)
        elif p.is_dir():
            files.extend(sorted(p.rglob("*.feature")))
    return sorted(set(files))


def lint_one(path: Path) -> dict:
    """Returns {"ok": bool, "regex_scenarios": int, "pickles": int, "error": str|None}."""
    text = path.read_text(encoding="utf-8", errors="replace")
    regex_scenarios = len(_REGEX_SCENARIO.findall(text))
    try:
        document = Parser().parse(text)
        document["uri"] = str(path)
        pickles = Compiler().compile(document)
        return {"ok": True, "regex_scenarios": regex_scenarios, "pickles": len(pickles), "error": None}
    except Exception as e:
        # The first line is just "Parser errors:" — the actual line:column and expected-
        # token diagnostic is the line after it.
        lines = [line for line in str(e).strip().splitlines() if line.strip()]
        message = lines[1] if len(lines) > 1 else (lines[0] if lines else type(e).__name__)
        return {"ok": False, "regex_scenarios": regex_scenarios, "pickles": None, "error": message}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("paths", nargs="*", default=DEFAULT_SCAN_PATHS, help="Files or directories to scan (default: tests/ and opt/cases/).")
    parser.add_argument("--strict", action="store_true", help="Exit 1 if any file is non-standard Gherkin (default: always exit 0).")
    args = parser.parse_args()

    files = find_feature_files(args.paths)
    if not files:
        print(f"no .feature files found under {args.paths}")
        return

    non_standard = []
    mismatched_counts = []
    for path in files:
        result = lint_one(path)
        if not result["ok"]:
            non_standard.append((path, result["error"]))
        elif result["pickles"] != result["regex_scenarios"]:
            mismatched_counts.append((path, result["regex_scenarios"], result["pickles"]))

    print(f"scanned {len(files)} .feature file(s)")

    if non_standard:
        print(f"\n{len(non_standard)} file(s) are not standard Gherkin (Hercules still runs them fine via its own regex splitter):")
        for path, error in non_standard:
            print(f"  ✗ {path}\n      {error}")

    if mismatched_counts:
        print(f"\n{len(mismatched_counts)} file(s) parse as standard Gherkin but a real parser would produce a different case count than Hercules' regex splitter:")
        for path, regex_count, pickle_count in mismatched_counts:
            print(f"  ! {path}: regex splitter finds {regex_count} scenario(s), full Gherkin (Examples expanded) finds {pickle_count}")

    if not non_standard and not mismatched_counts:
        print("everything scanned is standard Gherkin with matching case counts")

    if args.strict and (non_standard or mismatched_counts):
        sys.exit(1)


if __name__ == "__main__":
    main()
