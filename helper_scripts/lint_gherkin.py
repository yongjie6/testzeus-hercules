#!/usr/bin/env python
"""检查 .feature 用例的语法和**质量**，不改变 Hercules 怎么跑它们。

两件事：

1. 语法。Hercules 用正则切 .feature（utils/gherkin_helper.py），不是真正的 Gherkin
   解析器，这是有意的：非工程师写小写关键字、漏掉 Feature: 行，照样能跑。宽松是特性，
   这个脚本从不碰执行路径。但 Markdown 风格的 Examples 分隔行（`|---|---|`）会被正则
   当成一行数据，产出一条值为 "-------------" 的假用例——这类问题只有拿真解析器过一遍
   才看得见。

2. 质量。语法对的用例照样可以什么都没测。规则来自 ESM 3.0 那批缺陷的复盘：67 条缺陷
   里自动化只发现 9 条，而漏掉的那些有相当一部分不是能力不够，是用例本身的写法决定了
   它抓不到。每条规则对应一种被实测过的漏检形态，不是设想出来的风格偏好：

   pseudo-literal   「」里写的是描述不是界面文案，这条断言执行不了
   unverifiable-absent  「不应该看到红点」这类：目标是无文案元素，快照里根本没有，断言恒真
   weak-count       只数个数、且下界是 1 —— 元素在就绿，内容错了也绿
   no-assertion     整条 Scenario 没有 Then
   blocked-syntax   不可执行的用例必须用 @blocked:<kind> + `# blocked: 原因` 标注，不能是自由注释
   blocked-conflict 一条用例既 @blocked 又进了 known_failures.txt
   duplicate-title  同一文件里重复的 Scenario 标题

用法：
    python helper_scripts/lint_gherkin.py                 # 扫 tests/ 和 opt/cases/
    python helper_scripts/lint_gherkin.py path/to/dir ... # 只扫指定路径
    python helper_scripts/lint_gherkin.py --strict         # 有 error 就退 1（CI 用）
    python helper_scripts/lint_gherkin.py --max-weak 40    # 弱断言数量的棘轮，超了就退 1

--max-weak 是给存量用的。仓库里现在有一批「…个数应该不少于 1」，一次性改完不现实，
但让它继续变多就等于承认这套用例不打算真的验东西。把当天的数字设成上限，只许降不许升。

默认退出码是 0 —— 不带 --strict 时这个脚本只是报告，不是门禁。
"""

import argparse
import re
import sys
from pathlib import Path
from typing import Iterable, List, NamedTuple

from gherkin import Compiler, Parser

DEFAULT_SCAN_PATHS = ["tests", "opt/cases"]
KNOWN_FAILURES = Path("opt/cases/known_failures.txt")

_REGEX_SCENARIO = re.compile(r"^\s*scenario(?:\s+outline)?\s*:", re.IGNORECASE | re.MULTILINE)
_SCENARIO_LINE = re.compile(r"^\s*scenario(?:\s+outline)?\s*:\s*(.*)$", re.IGNORECASE)
_STEP_LINE = re.compile(r"^\s*(Given|When|Then|And|But|假如|当|那么|并且|但是)\s+(.*)$", re.IGNORECASE)
_THEN_LINE = re.compile(r"^\s*(Then|那么)\s", re.IGNORECASE)

# 与 assemble_version.py 同源，改一处要改两处的话迟早对不上，所以两边都只认这一种拼写：
#     @blocked:tooling
#     # blocked: 手机号授权是微信原生弹窗，自动化点不到
# 分两行是因为 Gherkin 的标签里不允许有空格——`@blocked(tooling: 原因)` 在 Hercules 自己
# 的正则切分器下没问题，真解析器直接拒绝，而这个脚本存在的意义之一正是让这种漂移可见。
_BLOCKED_KINDS = ("tooling", "fixture", "product", "env")
_BLOCKED_TAG = re.compile(r"^\s*@blocked:(\S*)\s*$")
# 和 assemble_version.MALFORMED_BLOCKED 同源：拼错的标记两边都必须认出来，
# 否则一条跑不了的用例会被静默当成可跑的。
_MALFORMED_BLOCKED = re.compile(r"@block(?:ed)?\b(?!:\S)|@blocked:\S+\s+\S")
_BLOCKED_REASON = re.compile(r"^\s*#\s*blocked\s*[:：]\s*(.*)$")
_MARKER_LINE = re.compile(r"^\s*@\S|^\s*#\s*blocked\s*[:：]")
_LEGACY_BLOCKED = re.compile(r"#\s*⛔")

_SEE_LITERAL = re.compile(r"(?:不应该|应该)看到「([^」]*)」")
# 「不存在」类断言的目标：快照表示得了吗？
_ABSENT_STEP = re.compile(r"不应该(?:看到|出现|再有|存在|含有)")
# 无文案的视觉可供性。mcp_servers/wxmp/wxml.mjs 的 finish() 丢弃无文案、无 aria-label、
# 非交互的节点，所以这些目标压根进不了待检文本，对它们做「不应该看到」恒真。
_UNVERIFIABLE_TARGETS = ("红点", "小红点", "角标", "徽标", "badge", "圆点", "下划线", "边框", "阴影", "高亮", "选中态", "遮罩", "分割线", "箭头")
_SENTENCE_PUNCT = re.compile(r"[。？！?!]")
_WEAK_COUNT = re.compile(r"(?:个数|条数|次数|轮次数|页面数)应该不少于\s*1\s*$")
_ANY_COUNT = re.compile(r"(?:个数|条数|次数|轮次数|页面数|距离)应该(?:不少于|不多于|为|等于)\s*\d+")


class Finding(NamedTuple):
    severity: str  # "error" | "warn"
    rule: str
    path: Path
    line: int
    message: str
    hint: str = ""


def find_feature_files(paths: List[str]) -> List[Path]:
    files: List[Path] = []
    for raw in paths:
        p = Path(raw)
        if p.is_file() and p.suffix == ".feature":
            files.append(p)
        elif p.is_dir():
            files.extend(sorted(p.rglob("*.feature")))
    return sorted(set(files))


def lint_syntax(path: Path) -> dict:
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


def looks_like_a_description(literal: str) -> bool:
    """「」里写的到底是界面上的字，还是对那个东西的描述？

    「应该看到「X」」这句话只有在 X 会原样出现在屏幕上时才可执行。实测语料里，写成描述的
    那几条长这样：

        不应该看到「被点击的那条推荐问题在推荐区里的残留」
        不应该看到「小程序本地存储中的手机号明文」
        不应该看到「授权成功后弹出的 Toast」

    而真的界面文案长这样：

        应该看到「说说你们的情况好吗？请问你们是什么关系呢？」
        应该看到「需要授权手机号才能登录，可重新点击按钮。」
        应该看到「使用 Narnia 测试账号」

    判据取三者交集：带「的」（描述性定语）、长于 8 字（短标签不会是描述）、且没有句末
    标点（真的界面长句几乎都带句读）。在 opt/cases 全量语料上跑，7 条命中全是描述、
    6 条长界面文案一条都没误伤。这不是万能规则，是一条按实际语料校准过、宁可漏不可错杀
    的规则——它一旦误报，作者就会开始无视整个 linter。
    """
    return "的" in literal and len(literal) > 8 and not _SENTENCE_PUNCT.search(literal)


def read_known_failure_patterns() -> List[str]:
    if not KNOWN_FAILURES.is_file():
        return []
    found = []
    for raw in KNOWN_FAILURES.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "::" not in line:
            continue
        found.append(line.split("::", 1)[0].strip())
    return found


def scenarios_of(lines: List[str]) -> List[tuple[int, str, List[str], List[int]]]:
    """(标题行号, 标题, 本条的所有行, 对应行号)。标签归属下方的 Scenario，和 assemble 一致。"""
    keyword_lines = [i for i, line in enumerate(lines) if _SCENARIO_LINE.match(line)]
    found = []
    for index, keyword in enumerate(keyword_lines):
        start = keyword
        while start > 0 and _MARKER_LINE.match(lines[start - 1]):
            start -= 1
        end = len(lines)
        if index + 1 < len(keyword_lines):
            nxt = keyword_lines[index + 1]
            while nxt > 0 and _MARKER_LINE.match(lines[nxt - 1]):
                nxt -= 1
            end = nxt
        title = (_SCENARIO_LINE.match(lines[keyword]) or re.match("", "")).group(1).strip()
        found.append((keyword + 1, title, lines[start:end], list(range(start + 1, end + 1))))
    return found


def lint_quality(path: Path, known_patterns: List[str]) -> List[Finding]:
    findings: List[Finding] = []
    text = path.read_text(encoding="utf-8", errors="replace")
    lines = text.splitlines()

    for number, line in enumerate(lines, 1):
        if _LEGACY_BLOCKED.search(line):
            findings.append(
                Finding(
                    "error",
                    "blocked-syntax",
                    path,
                    number,
                    "`# ⛔` 是自由注释，机器读不了：用例照样被组装、照样跑、照样红",
                    f"改成 Scenario 上方两行：@blocked:<kind> 加 `# blocked: <原因>`，kind 取 {' / '.join(_BLOCKED_KINDS)}",
                )
            )

    seen_titles: dict[str, int] = {}
    for title_line, title, block, numbers in scenarios_of(lines):
        if not title:
            continue
        if title in seen_titles:
            findings.append(
                Finding(
                    "error",
                    "duplicate-title",
                    path,
                    title_line,
                    f"Scenario 标题和第 {seen_titles[title]} 行重复：{title}",
                    "Hercules 会给后一条自动加 ' - 1' 后缀，报告里两条看起来像同一条跑了两次，红了也分不出是哪一条",
                )
            )
        else:
            seen_titles[title] = title_line

        blocked_kind = ""
        blocked_line = title_line
        blocked_reason = ""
        for offset, line in enumerate(block):
            if not _MARKER_LINE.match(line):
                break
            if not _BLOCKED_TAG.match(line) and _MALFORMED_BLOCKED.search(line):
                findings.append(
                    Finding(
                        "error",
                        "blocked-syntax",
                        path,
                        numbers[offset],
                        f"这一行像是 @blocked 但拼写不对，会被当成可跑用例：{line.strip()}",
                        "标签必须独占一行写成 @blocked:<kind>，原因写在紧跟的 `# blocked: <原因>` 注释里",
                    )
                )
            tag = _BLOCKED_TAG.match(line)
            if tag:
                blocked_kind, blocked_line = tag.group(1), numbers[offset]
                continue
            prose = _BLOCKED_REASON.match(line)
            if prose:
                blocked_reason = prose.group(1).strip()
        if blocked_kind and blocked_kind not in _BLOCKED_KINDS:
            findings.append(
                Finding(
                    "error", "blocked-syntax", path, blocked_line, f"@blocked 的 kind 是 {blocked_kind!r}，不在词表里", f"只能是 {' / '.join(_BLOCKED_KINDS)}——kind 决定这条该找谁解，所以是受控词表"
                )
            )
        if blocked_kind and not blocked_reason:
            findings.append(Finding("error", "blocked-syntax", path, blocked_line, "@blocked 没写原因", "紧跟一行 `# blocked: <原因>`。没有原因的话，没人知道解除它要做什么"))

        if blocked_kind:
            for pattern in known_patterns:
                if pattern in title:
                    findings.append(
                        Finding(
                            "error",
                            "blocked-conflict",
                            path,
                            title_line,
                            f"这条同时是 @blocked 和 known_failures.txt 里的 {pattern}",
                            "两者含义互斥：known_failures 说「跑得起来，红得有理由」，@blocked 说「压根跑不起来」。跑不起来的用例产不出红，留在名单里只会一直豁免一条根本不存在的失败",
                        )
                    )

        steps = [(numbers[i], line) for i, line in enumerate(block) if _STEP_LINE.match(line)]
        thens = [(n, line) for n, line in steps if _THEN_LINE.match(line)]
        if steps and not thens:
            findings.append(Finding("error", "no-assertion", path, title_line, "整条 Scenario 没有 Then，跑完必然是绿的", "一条不做断言的用例比没有用例更糟：它让报告显得覆盖到了"))

        for number, line in steps:
            for literal in _SEE_LITERAL.findall(line):
                if looks_like_a_description(literal):
                    findings.append(
                        Finding(
                            "error",
                            "pseudo-literal",
                            path,
                            number,
                            f"「{literal}」是对目标的描述，不是界面上会出现的字，这条断言执行不了",
                            "换成页面上真实的文案；断言的是「不存在某种形态」时改用 assert_ui 的 no_placeholder，断言存储/路由里没有敏感值时用 mp_get_storage / mp_webview_url 取回再断言",
                        )
                    )

        for number, line in steps:
            if not _ABSENT_STEP.search(line):
                continue
            hit = next((target for target in _UNVERIFIABLE_TARGETS if target in line), None)
            if hit:
                findings.append(
                    Finding(
                        "error",
                        "unverifiable-absent",
                        path,
                        number,
                        f"「{hit}」是无文案的视觉元素，快照里没有它，这条「不存在」断言不管缺陷在不在都会通过",
                        "快照只收有文案 / 有 aria-label / 可交互的节点（mcp_servers/wxmp/wxml.mjs 的 finish）。"
                        "改用 mp_visual_check 让视觉模型判读并留截图，或者先让前端给这个元素加 aria-label / testId 再断言；"
                        "两者都做不到时用 @blocked:tooling 标出来，别留一条恒真的断言冒充覆盖",
                    )
                )

        weak = [(n, line) for n, line in thens if _WEAK_COUNT.search(line.strip())]
        for number, line in weak:
            findings.append(
                Finding(
                    "warn",
                    "weak-count",
                    path,
                    number,
                    "「…个数应该不少于 1」是最弱的一种断言：只有元素完全不存在时才会红",
                    "这批用例漏掉的缺陷大多是「数对了、内容错了」。改成断言具体的值：assert_ui contains，或补一条 no_placeholder",
                )
            )

    return findings


def report(findings: Iterable[Finding]) -> tuple[int, int]:
    by_file: dict[Path, List[Finding]] = {}
    for finding in findings:
        by_file.setdefault(finding.path, []).append(finding)
    errors = warns = 0
    for path in sorted(by_file):
        print(f"\n{path}")
        for finding in sorted(by_file[path], key=lambda f: (f.line, f.rule)):
            mark = "✗" if finding.severity == "error" else "!"
            print(f"  {mark} {finding.line:>4}  [{finding.rule}] {finding.message}")
            if finding.hint:
                print(f"          → {finding.hint}")
            if finding.severity == "error":
                errors += 1
            else:
                warns += 1
    return errors, warns


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("paths", nargs="*", default=DEFAULT_SCAN_PATHS, help="Files or directories to scan (default: tests/ and opt/cases/).")
    parser.add_argument("--strict", action="store_true", help="Exit 1 if there is any error-level finding or non-standard Gherkin.")
    parser.add_argument("--max-weak", type=int, default=None, help="Ratchet: exit 1 if weak-count findings exceed this number.")
    args = parser.parse_args()

    files = find_feature_files(args.paths)
    if not files:
        print(f"no .feature files found under {args.paths}")
        return

    known_patterns = read_known_failure_patterns()

    non_standard = []
    mismatched_counts = []
    findings: List[Finding] = []
    for path in files:
        result = lint_syntax(path)
        if not result["ok"]:
            non_standard.append((path, result["error"]))
        elif result["pickles"] != result["regex_scenarios"]:
            mismatched_counts.append((path, result["regex_scenarios"], result["pickles"]))
        findings.extend(lint_quality(path, known_patterns))

    print(f"scanned {len(files)} .feature file(s)")

    if non_standard:
        print(f"\n{len(non_standard)} file(s) are not standard Gherkin (Hercules still runs them fine via its own regex splitter):")
        for path, error in non_standard:
            print(f"  ✗ {path}\n      {error}")

    if mismatched_counts:
        print(f"\n{len(mismatched_counts)} file(s) parse as standard Gherkin but a real parser would produce a different case count than Hercules' regex splitter:")
        for path, regex_count, pickle_count in mismatched_counts:
            print(f"  ! {path}: regex splitter finds {regex_count} scenario(s), full Gherkin (Examples expanded) finds {pickle_count}")

    errors, warns = report(findings)
    weak = sum(1 for f in findings if f.rule == "weak-count")

    print("\n── 用例质量 ─────────────────────────────────────────────")
    if not findings:
        print("  没有发现问题")
    else:
        counts: dict[str, int] = {}
        for finding in findings:
            counts[finding.rule] = counts.get(finding.rule, 0) + 1
        for rule, count in sorted(counts.items()):
            print(f"  {rule:18} {count:>4}")
        print(f"  {'合计':18} {errors:>4} error / {warns:>4} warn")
    if args.max_weak is not None:
        verdict = "超出" if weak > args.max_weak else "在预算内"
        print(f"  弱断言棘轮：{weak} / {args.max_weak}  {verdict}")
    print("─────────────────────────────────────────────────────────")

    over_budget = args.max_weak is not None and weak > args.max_weak
    if over_budget:
        print(f"\n弱断言比上限多了 {weak - args.max_weak} 条。这个数字只许降不许升——" "新用例请直接写值断言，不要再加「…个数应该不少于 1」。")
    if args.strict and (non_standard or mismatched_counts or errors):
        sys.exit(1)
    if over_budget:
        sys.exit(1)


if __name__ == "__main__":
    main()
