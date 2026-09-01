#!/usr/bin/env python3
"""Assemble a runnable project base for a product version or the regression set.

Cases live once, in opt/cases/<type>/<module>/<version>.feature, filed under
the version they belong to. A version runs its own cases only.

<type> is api, web, mp (mini program) or mp-real. It is a directory rather than
a naming convention because they need different things present to run at all --
an API suite wants a lane, a mini program suite wants DevTools and the mock
gateway -- so being able to assemble and run one type on its own is what makes
a run possible at all, not a convenience.

mp and mp-real are the same surface against different backends: mp drives the
dist-scenario bundle, whose API address is compiled in as the local mock
gateway, while mp-real drives dist-integration, built against the real lane.
DevTools opens one project at a time, so the two can never share a run --
run-version.sh refuses outright rather than letting half the suites fail.

    ./assemble_version.py 3.0          every type's v3.0 cases
    ./assemble_version.py 3.0 api      just the API suites
    ./assemble_version.py regression   the cases named in opt/cases/regression.txt
    ./assemble_version.py regression mp

Because versions are self-contained, the regression set is what carries
coverage forward: it names the cases worth rerunning every release, picked by
hand from any version.

opt/cases/regression.txt holds one entry per line:

    # 核心回归
    mp/mine  :: TC-ESMV1-028-L1-3
    api/esm-gateway :: TC-ESM30-GW-01

The scenario part matches as a substring. An entry that matches nothing is an
error rather than a skip: a regression set that quietly loses a case is worse
than no regression set, because the report still comes back green.

A scenario that cannot run today is tagged at its source and excluded from the
build:

    @blocked:tooling
    # blocked: 手机号授权是微信原生弹窗，自动化点不到
    Scenario: TC-ESM-052-L1-7 ...

kind is one of tooling / fixture / product / env, because each routes to a
different owner. It rides on the tag and the prose rides on the comment because
a Gherkin tag may not contain whitespace. Excluded cases are printed and written to blocked.json so the
gap between cases written and cases actually run is a number someone can watch,
instead of a fixed block of reds in every report. --include-blocked assembles
them anyway, for checking whether one has become runnable.

Versions compare numerically, so 10.0 sorts after 9.0.
"""

import json
import re
import shutil
import sys
from pathlib import Path

CASES = Path("opt/cases")
BUILD = Path("opt/.run")
MANIFEST = CASES / "regression.txt"
TYPES = ("api", "web", "mp", "mp-real")

VERSION_FILE = re.compile(r"^v?(\d+(?:\.\d+)*)\.feature$")
SCENARIO = re.compile(r"^\s*Scenario(?:\s+Outline)?\s*:", re.IGNORECASE)
HEADER_END = re.compile(r"^\s*(Scenario(?:\s+Outline)?)\s*:", re.IGNORECASE)
TAG_LINE = re.compile(r"^\s*@\S")

# A case that cannot run today is marked at its source, in a form a machine can read:
#
#     @blocked:tooling
#     # blocked: 手机号授权是微信原生弹窗，自动化点不到
#     Scenario: TC-ESM-052-L1-7 ...
#
# Two lines rather than one because a Gherkin tag may not contain whitespace -- the
# obvious `@blocked(tooling: 原因)` spelling parses fine under Hercules' own regex
# splitter but is rejected by a real Gherkin parser, which is exactly the kind of drift
# lint_gherkin.py exists to catch. So the tag carries the machine-readable kind and the
# comment carries the prose.
#
# This replaced a plain `# ⛔ 当前不可执行：...` comment, which meant the case was still
# assembled, still run, and still failed -- so every run carried a fixed set of reds that
# nobody could tell apart from real ones, and the size of the gap between "cases written"
# and "cases actually run" was invisible. Blocked scenarios are now excluded from the build
# by default and reported as their own number, so a shrinking coverage shows up as a number
# rather than as noise in the failure list.
#
# The kind is a controlled vocabulary because each one routes to a different owner:
BLOCKED_KINDS = {
    "tooling": "自动化能力不够（工具做不到这个操作或这类判定）",
    "fixture": "造不出这个数据或状态（mock 网关 / 账号 / 环境数据）",
    "product": "被测方还没实现，用例写的是契约",
    "env": "环境不具备（设备、真机、外部依赖）",
}
BLOCKED_TAG = re.compile(r"^\s*@blocked:(\S*)\s*$")
# 「看起来像 blocked 但拼写不对」：@blocked 漏冒号、和别的 tag 挤在一行、写成 @block:xxx。
# 不认出来的话它会被静默当成可跑用例——照组装、照跑、照红，正是这个约定要消灭的状态。
MALFORMED_BLOCKED = re.compile(r"@block(?:ed)?\b(?!:\S)|@blocked:\S+\s+\S")
BLOCKED_REASON = re.compile(r"^\s*#\s*blocked\s*[:：]\s*(.*)$")
# 标记块 = Scenario 正上方那一段连续的标签行和 `# blocked:` 注释行。其余注释保持原样，
# 归上一个块——那是既有行为，改动它会打乱用例里已有的成段说明。
MARKER_LINE = re.compile(r"^\s*@\S|^\s*#\s*blocked\s*[:：]")
# 全行搜索而不是行首锚定，和 lint_gherkin 保持一致：行尾注释里的 ⛔ 同样是遗留写法。
LEGACY_BLOCKED = re.compile(r"#\s*⛔")


def parse_version(text: str) -> tuple[int, ...]:
    return tuple(int(part) for part in text.split("."))


def modules(only_type: str | None = None) -> list[Path]:
    """Every opt/cases/<type>/<module> directory, optionally one type only."""
    found = []
    for type_name in TYPES:
        if only_type and type_name != only_type:
            continue
        root = CASES / type_name
        if not root.is_dir():
            continue
        found.extend(sorted(p for p in root.iterdir() if p.is_dir()))
    return found


def suite_name(module: Path) -> str:
    """api/esm-gateway -> api-esm-gateway, so a suite says what it needs."""
    return f"{module.parent.name}-{module.name}"


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
    """Map <type>/<module> -> scenario patterns, from opt/cases/regression.txt."""
    if not MANIFEST.is_file():
        raise SystemExit(f"找不到回归清单 {MANIFEST}\n" "每行写 `<类型>/<模块> :: <Scenario 标题子串>`，例如：\n" "  mp/mine :: TC-ESMV1-028-L1-3")
    wanted: dict[str, list[str]] = {}
    for number, raw in enumerate(MANIFEST.read_text(encoding="utf-8").splitlines(), 1):
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        if "::" not in line:
            raise SystemExit(f"{MANIFEST}:{number} 格式不对，应为 `<类型>/<模块> :: <Scenario 标题子串>`：{line}")
        module, pattern = (part.strip() for part in line.split("::", 1))
        if not module or not pattern:
            raise SystemExit(f"{MANIFEST}:{number} 模块名或标题为空：{line}")
        if "/" not in module:
            raise SystemExit(f"{MANIFEST}:{number} 模块要带类型前缀，写成 `<类型>/<模块>`（类型: {', '.join(TYPES)}）：{line}")
        wanted.setdefault(module, []).append(pattern)
    if not wanted:
        raise SystemExit(f"{MANIFEST} 里没有任何条目")
    return wanted


def marker_start(lines: list[str], scenario_line: int) -> int:
    """Where a scenario really begins: at the run of marker lines above its Scenario: line.

    Gherkin puts tags above the keyword, so slicing at `Scenario:` alone drops each
    scenario's markers into the *previous* block -- which would silently attach one case's
    @blocked marker to its neighbour. Only tags and `# blocked:` comments are pulled up;
    other comments keep their current home so existing prose above a scenario is not
    reshuffled.
    """
    start = scenario_line
    while start > 0 and MARKER_LINE.match(lines[start - 1]):
        start -= 1
    return start


def split_header_and_scenarios(text: str) -> tuple[str, list[str]]:
    """Separate the Feature/Background preamble from the scenario blocks."""
    lines = text.splitlines()
    keywords = [i for i, line in enumerate(lines) if HEADER_END.match(line)]
    if not keywords:
        return text.strip(), []
    starts = [marker_start(lines, i) for i in keywords]
    header = "\n".join(lines[: starts[0]]).rstrip()
    blocks = []
    for index, start in enumerate(starts):
        end = starts[index + 1] if index + 1 < len(starts) else len(lines)
        blocks.append("\n".join(lines[start:end]).rstrip())
    return header, blocks


def blocked_reason(block: str) -> tuple[str, str] | None:
    """The (kind, reason) of a @blocked scenario, or None if it is runnable."""
    kind = None
    reason = ""
    for line in block.splitlines():
        if not MARKER_LINE.match(line):
            break
        if not BLOCKED_TAG.match(line) and MALFORMED_BLOCKED.search(line):
            raise SystemExit(f"这一行像是 @blocked 但拼写不对，会被当成可跑用例：{line.strip()}\n" "标签必须独占一行，写成 @blocked:<kind>，原因写在紧跟的 `# blocked: <原因>` 注释里")
        tag = BLOCKED_TAG.match(line)
        if tag:
            kind = tag.group(1)
            continue
        prose = BLOCKED_REASON.match(line)
        if prose:
            reason = prose.group(1).strip()
    return (kind, reason) if kind is not None else None


def strip_markers(block: str) -> str:
    """Drop the marker lines before handing a block to the runner.

    They are build metadata: Hercules' own splitter cuts at `Scenario:` too, so a marker
    left in the assembled file would reattach to the previous scenario there. They have
    done their job by assembly time.
    """
    lines = block.splitlines()
    index = 0
    while index < len(lines) and MARKER_LINE.match(lines[index]):
        index += 1
    return "\n".join(lines[index:])


def write_suite(out_root: Path, module: Path, header: str, blocks: list[str]) -> None:
    name = suite_name(module)
    suite = out_root / "tests" / name
    (suite / "input").mkdir(parents=True, exist_ok=True)
    (suite / "input" / f"{name}.feature").write_text(header + "\n\n" + "\n\n".join(strip_markers(b) for b in blocks) + "\n", encoding="utf-8")
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
        text = path.read_text(encoding="utf-8")
        legacy = [line.strip() for line in text.splitlines() if LEGACY_BLOCKED.search(line)]
        if legacy:
            # Refusing rather than warning: a `# ⛔` comment means nothing to the build, so
            # the case would be assembled and run anyway -- exactly the state this convention
            # replaced. Silently tolerating both spellings recreates it.
            raise SystemExit(
                f"{path} 里还有 {len(legacy)} 处 `# ⛔ 当前不可执行` 注释，机器读不了。\n"
                f"改成 Scenario 上方的两行，kind 取 {', '.join(sorted(BLOCKED_KINDS))}：\n"
                f"  @blocked:tooling\n"
                f"  # blocked: 手机号授权是微信原生弹窗，自动化点不到\n"
                f"首处：{legacy[0]}"
            )
        file_header, file_blocks = split_header_and_scenarios(text)
        # The first contributing file supplies Feature and Background, so a
        # merged module cannot end up with two Feature lines.
        if not header:
            header = file_header
        blocks.extend((version, block) for block in file_blocks)
    return header, blocks


def partition_blocked(module_name: str, blocks: list[str], report: list[dict[str, str]]) -> list[str]:
    """Split runnable blocks from @blocked ones, appending the blocked to ``report``."""
    runnable = []
    for block in blocks:
        marker = blocked_reason(block)
        if marker is None:
            runnable.append(block)
            continue
        kind, reason = marker
        if kind not in BLOCKED_KINDS:
            raise SystemExit(f"{module_name} :: {scenario_title(strip_markers(block))}\n" f"  @blocked 的 kind 只能是 {', '.join(sorted(BLOCKED_KINDS))}，当前写的是 {kind!r}")
        if not reason:
            raise SystemExit(f"{module_name} :: {scenario_title(strip_markers(block))}\n" "  @blocked 必须写原因：紧跟一行 `# blocked: <原因>`")
        report.append({"module": module_name, "scenario": scenario_title(strip_markers(block)), "kind": kind, "reason": reason})
    return runnable


def assemble_version(version_text: str, out_root: Path, only_type: str | None, include_blocked: bool, blocked: list[dict[str, str]]) -> tuple[int, int]:
    target = parse_version(version_text)
    suites = scenarios = 0
    for module in modules(only_type):
        header, blocks = collect(module, target)
        if not blocks:
            continue
        module_name = f"{module.parent.name}/{module.name}"
        found: list[dict[str, str]] = []
        picked = [b for _, b in blocks]
        runnable = partition_blocked(module_name, picked, found)
        blocked.extend(found)
        if not include_blocked:
            picked = runnable
        if not picked:
            print(f"  {suite_name(module):18}   -   全部 {len(found)} 条被 @blocked，本次不组装")
            continue
        write_suite(out_root, module, header, picked)
        suites += 1
        scenarios += len(picked)
        note = f"  (另有 {len(found)} 条 @blocked)" if found and not include_blocked else ""
        print(f"  {suite_name(module):18} {len(picked):>3} 条{note}")
    return suites, scenarios


def assemble_regression(out_root: Path, only_type: str | None, include_blocked: bool, blocked: list[dict[str, str]]) -> tuple[int, int]:
    wanted = read_manifest()
    known = {f"{m.parent.name}/{m.name}" for m in modules()}
    missing_modules = sorted(set(wanted) - known)
    if missing_modules:
        raise SystemExit(f"回归清单里的模块不存在: {', '.join(missing_modules)}\n" f"opt/cases/ 下现有模块: {', '.join(sorted(known)) or '(空)'}")

    suites = scenarios = 0
    unmatched: list[str] = []
    unrunnable: list[str] = []
    for module_name, patterns in wanted.items():
        if only_type and not module_name.startswith(f"{only_type}/"):
            continue
        module = CASES / module_name
        header, blocks = collect(module, None)
        picked: list[str] = []
        for pattern in patterns:
            hits = [block for _, block in blocks if pattern in scenario_title(strip_markers(block))]
            if not hits:
                unmatched.append(f"{module_name} :: {pattern}")
                continue
            marked = [h for h in hits if blocked_reason(h)]
            if marked and not include_blocked:
                # A regression entry pointing at a blocked case is a contradiction: the set
                # names what must be rerun every release, and this case cannot run at all.
                # Dropping it quietly would shrink the regression set without telling anyone.
                reason = blocked_reason(marked[0])
                unrunnable.append(f"{module_name} :: {pattern}  (@blocked {reason[0]}: {reason[1]})")
                continue
            picked.extend(hits)
        # 这里不需要再走一遍 partition_blocked：blocked 的条目上面已经进了 unrunnable 并
        # 会抛出，所以走到这一步的 picked 必然全是可跑的。回归构建的 blocked.json 因此
        # 恒为空——那是对的，回归集本来就不该含跑不了的用例。
        if picked:
            write_suite(out_root, module, header, picked)
            suites += 1
            scenarios += len(picked)
            print(f"  {module_name:18} {len(picked):>3} 条  (清单 {len(patterns)} 条)")

    if unmatched:
        # Silently skipping would still report green, which is the one outcome a
        # regression set must never produce. Entries for other types never get
        # here -- they are filtered out before matching -- so anything in this
        # list really did fail to match.
        raise SystemExit("回归清单里有条目匹配不到任何用例，可能是标题改过或用例已删除：\n  " + "\n  ".join(unmatched))
    if unrunnable:
        raise SystemExit("回归清单里有条目指向被 @blocked 的用例，跑不了：\n  " + "\n  ".join(unrunnable) + "\n从 regression.txt 里删掉，或者先把它解除 blocked。")
    return suites, scenarios


def assemble(target_text: str, only_type: str | None = None, include_blocked: bool = False) -> int:
    if not CASES.is_dir():
        print(f"找不到用例库: {CASES}")
        return 1
    if only_type and only_type not in TYPES:
        print(f"未知类型 {only_type}，可选: {', '.join(TYPES)}")
        return 1

    # A type-filtered build gets its own directory, so assembling one type does
    # not quietly replace the full build someone else is about to run.
    out_root = BUILD / (f"{target_text}-{only_type}" if only_type else target_text)
    if out_root.exists():
        shutil.rmtree(out_root)

    blocked: list[dict[str, str]] = []
    if target_text == "regression":
        suites, scenarios = assemble_regression(out_root, only_type, include_blocked, blocked)
        label = "核心回归"
    else:
        suites, scenarios = assemble_version(target_text, out_root, only_type, include_blocked, blocked)
        label = f"v{target_text}"
    if only_type:
        label += f" / {only_type}"

    if not suites:
        print(f"{label} 没有匹配到任何用例")
        return 1

    # The gate reads this to report the excluded count alongside the run. Written even when
    # empty, so a missing file means "this build predates the convention" rather than
    # "nothing was excluded" -- the two must not look alike.
    out_root.mkdir(parents=True, exist_ok=True)
    (out_root / "blocked.json").write_text(json.dumps({"include_blocked": include_blocked, "entries": blocked}, ensure_ascii=False, indent=2), encoding="utf-8")

    print(f"\n组装完成: {label} / {suites} 个套件 / {scenarios} 条用例 -> {out_root}")
    if blocked:
        by_kind: dict[str, int] = {}
        for entry in blocked:
            by_kind[entry["kind"]] = by_kind.get(entry["kind"], 0) + 1
        state = "已按 --include-blocked 一并组装" if include_blocked else "已排除"
        print(f"\n@blocked {len(blocked)} 条（{state}）：" + "  ".join(f"{kind} {count}" for kind, count in sorted(by_kind.items())))
        for entry in blocked:
            print(f"  - [{entry['kind']}] {entry['module']} :: {entry['scenario']}")
            print(f"      {entry['reason']}")
        print("\n  这个数字就是「写下来的覆盖」和「真正在跑的覆盖」之间的差。它不该长期不动。")
    print(f"\n运行: uv run python -m testzeus_hercules --project-base {out_root}")
    return 0


if __name__ == "__main__":
    argv = [a for a in sys.argv[1:] if a != "--include-blocked"]
    if len(argv) not in (1, 2):
        print(__doc__)
        sys.exit(2)
    sys.exit(assemble(argv[0], argv[1] if len(argv) == 2 else None, "--include-blocked" in sys.argv[1:]))
