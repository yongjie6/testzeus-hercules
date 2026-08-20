#!/usr/bin/env python3
"""Assemble a runnable project base for a product version or the regression set.

Cases live once, in opt/cases/<module>/<version>.feature, filed under the
version they belong to. A version runs its own cases only.

    ./assemble_version.py 2.0          just v2.0's cases
    ./assemble_version.py regression   the cases named in opt/cases/regression.txt

Because versions are self-contained, the regression set is what carries
coverage forward: it names the cases worth rerunning every release, picked by
hand from any version.

opt/cases/regression.txt holds one entry per line:

    # 核心回归
    mine :: [v1.0] 展示账号信息
    auth :: [v1.0] 未登录时展示登录入口

The scenario part matches as a substring. An entry that matches nothing is an
error rather than a skip: a regression set that quietly loses a case is worse
than no regression set, because the report still comes back green.

Versions compare numerically, so 10.0 sorts after 9.0.
"""

import re
import shutil
import sys
from pathlib import Path

CASES = Path("opt/cases")
BUILD = Path("opt/.run")
MANIFEST = CASES / "regression.txt"

VERSION_FILE = re.compile(r"^v?(\d+(?:\.\d+)*)\.feature$")
SCENARIO = re.compile(r"^\s*Scenario(?:\s+Outline)?\s*:", re.IGNORECASE)
HEADER_END = re.compile(r"^\s*(Scenario(?:\s+Outline)?)\s*:", re.IGNORECASE)


def parse_version(text: str) -> tuple[int, ...]:
    return tuple(int(part) for part in text.split("."))


def version_files(module: Path, target: tuple[int, ...] | None) -> list[tuple[tuple[int, ...], Path]]:
    """Version files for the target version, or all of them when target is None."""
    found = []
    for path in module.glob("*.feature"):
        match = VERSION_FILE.match(path.name)
        if not match:
            continue
        version = parse_version(match.group(1))
        if target is None or version == target:
            found.append((version, path))
    return sorted(found)


def scenario_title(block: str) -> str:
    return block.splitlines()[0].split(":", 1)[1].strip() if ":" in block.splitlines()[0] else ""


def read_manifest() -> dict[str, list[str]]:
    """Map module -> scenario patterns, from opt/cases/regression.txt."""
    if not MANIFEST.is_file():
        raise SystemExit(
            f"找不到回归清单 {MANIFEST}\n"
            "每行写 `<模块> :: <Scenario 标题子串>`，例如：\n"
            "  mine :: [v1.0] 展示账号信息"
        )
    wanted: dict[str, list[str]] = {}
    for number, raw in enumerate(MANIFEST.read_text(encoding="utf-8").splitlines(), 1):
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        if "::" not in line:
            raise SystemExit(f"{MANIFEST}:{number} 格式不对，应为 `<模块> :: <Scenario 标题子串>`：{line}")
        module, pattern = (part.strip() for part in line.split("::", 1))
        if not module or not pattern:
            raise SystemExit(f"{MANIFEST}:{number} 模块名或标题为空：{line}")
        wanted.setdefault(module, []).append(pattern)
    if not wanted:
        raise SystemExit(f"{MANIFEST} 里没有任何条目")
    return wanted


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


def write_suite(out_root: Path, module: Path, header: str, blocks: list[str]) -> None:
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


def collect(module: Path, target: tuple[int, ...] | None) -> tuple[str, list[tuple[tuple[int, ...], str]]]:
    """Header plus (version, scenario block) pairs for a module."""
    header = ""
    blocks: list[tuple[tuple[int, ...], str]] = []
    for version, path in version_files(module, target):
        file_header, file_blocks = split_header_and_scenarios(path.read_text(encoding="utf-8"))
        # The first contributing file supplies Feature and Background, so a
        # merged module cannot end up with two Feature lines.
        if not header:
            header = file_header
        blocks.extend((version, block) for block in file_blocks)
    return header, blocks


def assemble_version(version_text: str, out_root: Path) -> tuple[int, int]:
    target = parse_version(version_text)
    suites = scenarios = 0
    for module in sorted(p for p in CASES.iterdir() if p.is_dir()):
        header, blocks = collect(module, target)
        if not blocks:
            continue
        write_suite(out_root, module, header, [b for _, b in blocks])
        suites += 1
        scenarios += len(blocks)
        print(f"  {module.name:12} {len(blocks):>3} 条")
    return suites, scenarios


def assemble_regression(out_root: Path) -> tuple[int, int]:
    wanted = read_manifest()
    known = {p.name for p in CASES.iterdir() if p.is_dir()}
    missing_modules = sorted(set(wanted) - known)
    if missing_modules:
        raise SystemExit(
            f"回归清单里的模块不存在: {', '.join(missing_modules)}\n"
            f"opt/cases/ 下现有模块: {', '.join(sorted(known)) or '(空)'}"
        )

    suites = scenarios = 0
    unmatched: list[str] = []
    for module_name, patterns in wanted.items():
        module = CASES / module_name
        header, blocks = collect(module, None)
        picked: list[str] = []
        for pattern in patterns:
            hits = [block for _, block in blocks if pattern in scenario_title(block)]
            if not hits:
                unmatched.append(f"{module_name} :: {pattern}")
                continue
            picked.extend(hits)
        if picked:
            write_suite(out_root, module, header, picked)
            suites += 1
            scenarios += len(picked)
            print(f"  {module_name:12} {len(picked):>3} 条  (清单 {len(patterns)} 条)")

    if unmatched:
        # Silently skipping would still report green, which is the one outcome a
        # regression set must never produce.
        raise SystemExit(
            "回归清单里有条目匹配不到任何用例，可能是标题改过或用例已删除：\n  "
            + "\n  ".join(unmatched)
        )
    return suites, scenarios


def assemble(target_text: str) -> int:
    if not CASES.is_dir():
        print(f"找不到用例库: {CASES}")
        return 1

    out_root = BUILD / target_text
    if out_root.exists():
        shutil.rmtree(out_root)

    if target_text == "regression":
        suites, scenarios = assemble_regression(out_root)
        label = "核心回归"
    else:
        suites, scenarios = assemble_version(target_text, out_root)
        label = f"v{target_text}"

    if not suites:
        print(f"{label} 没有匹配到任何用例")
        return 1

    print(f"\n组装完成: {label} / {suites} 个套件 / {scenarios} 条用例 -> {out_root}")
    print(f"运行: uv run python -m testzeus_hercules --project-base {out_root}")
    return 0


if __name__ == "__main__":
    if len(sys.argv) != 2:
        print(__doc__)
        sys.exit(2)
    sys.exit(assemble(sys.argv[1]))
