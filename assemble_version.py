#!/usr/bin/env python3
"""Assemble a runnable project base for one product version.

Cases live once, in opt/cases/<module>/<version>.feature, filed under the
version that introduced them. Running version V means running everything
introduced at or before V, so this collects the matching files, merges each
module's scenarios into the single feature file bulk mode expects, and writes
the result to opt/.run/<V>/.

    ./assemble_version.py 2.0
    uv run python -m testzeus_hercules --project-base opt/.run/2.0

Versions compare numerically, so 10.0 sorts after 9.0.

An optional opt/cases/<module>/exclude-<version>.txt drops scenarios once a
version retires them: one scenario title per line, matched as a substring.
"""

import re
import shutil
import sys
from pathlib import Path

CASES = Path("opt/cases")
BUILD = Path("opt/.run")

VERSION_FILE = re.compile(r"^v?(\d+(?:\.\d+)*)\.feature$")
SCENARIO = re.compile(r"^\s*Scenario(?:\s+Outline)?\s*:", re.IGNORECASE)
HEADER_END = re.compile(r"^\s*(Scenario(?:\s+Outline)?)\s*:", re.IGNORECASE)


def parse_version(text: str) -> tuple[int, ...]:
    return tuple(int(part) for part in text.split("."))


def version_files(module: Path, target: tuple[int, ...]) -> list[tuple[tuple[int, ...], Path]]:
    """Every version file at or below the target, oldest first."""
    found = []
    for path in module.glob("*.feature"):
        match = VERSION_FILE.match(path.name)
        if not match:
            continue
        version = parse_version(match.group(1))
        if version <= target:
            found.append((version, path))
    return sorted(found)


def split_header_and_scenarios(text: str) -> tuple[str, list[str]]:
    """Separate the Feature/Background preamble from the scenario blocks."""
    lines = text.splitlines()
    starts = [i for i, line in enumerate(lines) if HEADER_END.match(line)]
    if not starts:
        return text.strip(), []
    header = "\n".join(lines[: starts[0]]).rstrip()
    blocks = []
    for index, start in enumerate(starts):
        end = starts[index + 1] if index + 1 < len(starts) else len(lines)
        blocks.append("\n".join(lines[start:end]).rstrip())
    return header, blocks


def load_excludes(module: Path, target: tuple[int, ...]) -> list[str]:
    patterns = []
    for path in module.glob("exclude-*.txt"):
        match = re.match(r"^exclude-v?(\d+(?:\.\d+)*)\.txt$", path.name)
        if not match or parse_version(match.group(1)) > target:
            continue
        for line in path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if line and not line.startswith("#"):
                patterns.append(line)
    return patterns


def assemble(version_text: str) -> int:
    target = parse_version(version_text)
    if not CASES.is_dir():
        print(f"找不到用例库: {CASES}")
        return 1

    out_root = BUILD / version_text
    if out_root.exists():
        shutil.rmtree(out_root)

    modules = sorted(p for p in CASES.iterdir() if p.is_dir())
    assembled = 0
    total_scenarios = 0

    for module in modules:
        files = version_files(module, target)
        if not files:
            continue
        excludes = load_excludes(module, target)

        header = ""
        blocks: list[str] = []
        contributions: list[str] = []
        for version, path in files:
            file_header, file_blocks = split_header_and_scenarios(
                path.read_text(encoding="utf-8")
            )
            # The oldest file supplies Feature and Background for the module;
            # later files contribute scenarios only, so merging cannot produce
            # a second Feature line or a conflicting Background.
            if not header:
                header = file_header
            kept = [b for b in file_blocks if not any(x in b for x in excludes)]
            dropped = len(file_blocks) - len(kept)
            blocks.extend(kept)
            label = ".".join(str(p) for p in version)
            contributions.append(f"v{label}:{len(kept)}" + (f"(-{dropped})" if dropped else ""))

        if not blocks:
            continue

        suite = out_root / "tests" / module.name
        (suite / "input").mkdir(parents=True, exist_ok=True)
        (suite / "input" / f"{module.name}.feature").write_text(
            header + "\n\n" + "\n\n".join(blocks) + "\n", encoding="utf-8"
        )
        source_data = module / "test_data"
        if source_data.is_dir():
            shutil.copytree(source_data, suite / "test_data")
        else:
            (suite / "test_data").mkdir(parents=True, exist_ok=True)

        assembled += 1
        total_scenarios += len(blocks)
        print(f"  {module.name:12} {len(blocks):>3} 条  ({', '.join(contributions)})")

    if not assembled:
        print(f"版本 {version_text} 没有匹配到任何用例")
        return 1

    print(f"\n组装完成: {assembled} 个套件 / {total_scenarios} 条用例 -> {out_root}")
    print(f"运行: uv run python -m testzeus_hercules --project-base {out_root}")
    return 0


if __name__ == "__main__":
    if len(sys.argv) != 2:
        print(__doc__)
        sys.exit(2)
    sys.exit(assemble(sys.argv[1]))
