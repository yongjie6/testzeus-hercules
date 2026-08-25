#!/usr/bin/env python3
"""把一次 Hercules 运行判定成通过或失败，并写出机器可读的 e2e-summary.json。

存在的理由只有一个：`python -m testzeus_hercules` 无论用例红绿都退出 0
(`__main__.py` 的 `main()` 跑完就返回)。直接挂进 CI 的结果是一条永远绿的门禁——
比没有门禁更危险，因为它会让人相信有人在看。

判定口径：
  - JUnit 里带 <failure> / <error> 的用例算失败
  - <skipped> 也算失败。CI/CD 设计里 E2E 明确不允许 skip，一条静默跳过的用例和一条
    通过的用例在报告里长得一模一样
  - 一条用例都没扫到算失败。跑崩在第一个套件、或者选集拼错导致什么都没跑，产出都是
    “零个失败”，用计数判定的话正好是绿的
  - known_failures.txt 里登记的用例失败了不算失败，但**通过了算失败**——逼着人把它
    从名单里删掉，否则名单会变成一个只进不出的坟场

退出码：0 通过 / 1 有失败 / 2 门禁自身无法判定（目录不存在、XML 损坏）
"""

import argparse
import json
import os
import sys
import xml.etree.ElementTree as ET
from pathlib import Path
from typing import Any

SCHEMA = "hercules-e2e-summary/v1"

# 合并后的报告叫 <feature 文件名>_result.xml，单条用例的中间产物叫 *_results.xml
# （复数）。只收合并后的那份，否则每条用例会被数两遍。
MERGED_REPORT = "*.feature_result.xml"


def parse_known_failures(path: Path) -> tuple[list[dict[str, str]], list[str]]:
    """读 known_failures.txt，格式与 regression.txt 一致：<用例 ID 子串> :: <原因>。"""
    if not path.is_file():
        return [], []
    entries: list[dict[str, str]] = []
    problems: list[str] = []
    for number, raw in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        if "::" not in line:
            problems.append(f"{path}:{number} 格式不对，应为 `<用例 ID 子串> :: <原因>`：{line}")
            continue
        pattern, reason = (part.strip() for part in line.split("::", 1))
        if not pattern or not reason:
            problems.append(f"{path}:{number} 用例 ID 或原因为空：{line}")
            continue
        entries.append({"pattern": pattern, "reason": reason})
    return entries, problems


def suite_of(report: Path, run_dir: Path) -> str:
    """套件名。JUnit 里的 classname 是 Feature 的中文标题，定位不回 opt/cases/ 下的目录；
    套件名是把一条红追回 <类型>/<模块> 的唯一线索。

    报告叫 <套件>.feature_result.xml，而套件名就是 assemble_version.py 写出的
    `<类型>-<模块>`，所以文件名是最直接的来源。bulk 和单套件两种跑法产出的目录层级不同
    （前者 tests/<套件>/output/，后者 output/），文件名两种都一样，路径只做兜底。
    """
    name = report.name
    if name.endswith(".feature_result.xml"):
        return name[: -len(".feature_result.xml")]
    try:
        parts = report.relative_to(run_dir).parts
    except ValueError:
        return "unknown"
    if "tests" in parts:
        index = parts.index("tests")
        if index + 1 < len(parts):
            return parts[index + 1]
    return "unknown"


def properties_of(case: ET.Element) -> dict[str, str]:
    found = {}
    for props in case.findall("properties"):
        for prop in props.findall("property"):
            name = prop.get("name") or ""
            found[name] = prop.get("value") or ""
    return found


def prop_starting(props: dict[str, str], prefix: str) -> str:
    for name, value in props.items():
        if name.startswith(prefix):
            return value
    return ""


def read_cases(report: Path, run_dir: Path) -> list[dict[str, Any]]:
    tree = ET.parse(report)
    root = tree.getroot()
    suites = root.iter("testsuite") if root.tag == "testsuites" else [root]

    collected: list[dict[str, Any]] = []
    for suite in suites:
        for case in suite.findall("testcase"):
            failure = case.find("failure")
            error = case.find("error")
            skipped = case.find("skipped")
            if error is not None:
                status, detail = "error", error
            elif failure is not None:
                status, detail = "failed", failure
            elif skipped is not None:
                # 设计里 E2E 不允许 skip；出现即当失败，不给它躲进绿色报告的机会。
                status, detail = "skipped", skipped
            else:
                status, detail = "passed", None

            props = properties_of(case)
            collected.append(
                {
                    "suite": suite_of(report, run_dir),
                    "feature": suite.get("name") or case.get("classname") or "",
                    "name": case.get("name") or "",
                    "status": status,
                    "duration_seconds": round(float(case.get("time") or 0.0), 2),
                    "message": (detail.get("message") or "").strip() if detail is not None else None,
                    "proofs": prop_starting(props, "Proofs Base Folder"),
                    "logs": props.get("Agents Internal Logs", ""),
                    "report": str(report),
                }
            )
    return collected


def gitlab_context() -> dict[str, str]:
    """CI/CD 设计要求 E2E 交出 job URL，让 Jenkins 能从发布记录跳回原始日志。"""
    keys = {
        "project": "CI_PROJECT_PATH",
        "pipeline_id": "CI_PIPELINE_ID",
        "pipeline_url": "CI_PIPELINE_URL",
        "job_id": "CI_JOB_ID",
        "job_url": "CI_JOB_URL",
        "commit_sha": "CI_COMMIT_SHA",
        "ref": "CI_COMMIT_REF_NAME",
    }
    return {name: os.environ[var] for name, var in keys.items() if os.environ.get(var)}


def merge_junit(reports: list[Path], target: Path) -> None:
    """把各套件的报告并成一份，供 GitLab 的 artifacts:reports:junit 直接消费。"""
    merged = ET.Element("testsuites")
    for report in reports:
        root = ET.parse(report).getroot()
        for suite in root.iter("testsuite") if root.tag == "testsuites" else [root]:
            merged.append(suite)
    target.parent.mkdir(parents=True, exist_ok=True)
    ET.ElementTree(merged).write(target, encoding="UTF-8", xml_declaration=True)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--run-dir", required=True, help="opt/.run/<选集>[-<类型>]")
    parser.add_argument("--summary", default="e2e-summary.json")
    parser.add_argument("--junit", default="e2e-junit.xml")
    parser.add_argument("--known-failures", default="opt/cases/known_failures.txt")
    parser.add_argument("--selection-type", default="")
    parser.add_argument("--selection-target", default="")
    parser.add_argument("--lane", default="")
    parser.add_argument("--deploy-mode", default="unknown")
    parser.add_argument("--runner-exit-code", type=int, default=0)
    args = parser.parse_args()

    run_dir = Path(args.run_dir)
    summary_path = Path(args.summary)
    blockers: list[str] = []

    known, known_problems = parse_known_failures(Path(args.known_failures))
    # 名单自己写坏了要当门禁错误报出来，不能默默当成空名单——那会把一条本该被豁免的红
    # 变成真红，或者反过来。
    blockers.extend(known_problems)

    reports = sorted(run_dir.glob(f"**/output/*/{MERGED_REPORT}")) if run_dir.is_dir() else []
    cases: list[dict[str, Any]] = []
    for report in reports:
        try:
            cases.extend(read_cases(report, run_dir))
        except ET.ParseError as exc:
            blockers.append(f"报告解析失败 {report}: {exc}")

    matched_patterns: set[str] = set()
    for case in cases:
        hit = next((entry for entry in known if entry["pattern"] in case["name"]), None)
        case["known_failure"] = hit["reason"] if hit else None
        if hit:
            matched_patterns.add(hit["pattern"])
            if case["status"] == "passed":
                # 名单上的用例转绿是好事，但必须有人把它从名单里删掉，否则名单会一直
                # 豁免一条其实已经修好的用例，下次它再坏就没人看得见了。
                case["status"] = "unexpected_pass"
            else:
                case["status"] = "expected_failure"

    counts = {
        "cases": len(cases),
        "passed": sum(c["status"] == "passed" for c in cases),
        "failed": sum(c["status"] == "failed" for c in cases),
        "errors": sum(c["status"] == "error" for c in cases),
        "skipped": sum(c["status"] == "skipped" for c in cases),
        "expected_failures": sum(c["status"] == "expected_failure" for c in cases),
        "unexpected_passes": sum(c["status"] == "unexpected_pass" for c in cases),
    }

    if not run_dir.is_dir():
        blockers.append(f"运行目录不存在：{run_dir}（选集或类型拼错，用例根本没被组装）")
    elif not reports:
        blockers.append(f"{run_dir} 下没有找到任何 {MERGED_REPORT}，本次没有产出报告")
    elif not cases:
        blockers.append("报告里一条用例都没有")

    for count, label in ((counts["failed"], "条用例失败"), (counts["errors"], "条用例出错"), (counts["skipped"], "条用例被跳过（设计不允许 E2E skip）"), (counts["unexpected_passes"], "条已知失败用例转绿，请从 known_failures.txt 删掉")):
        if count:
            blockers.append(f"{count} {label}")

    if args.runner_exit_code != 0 and not blockers:
        # 进程崩了但已产出的用例全绿：跑到一半没跑完，剩下的用例没有结果，不能算通过。
        blockers.append(f"执行进程以 {args.runner_exit_code} 退出，本次运行未完整结束")

    # 名单里没匹配上任何用例的条目不算失败：每个 job 只跑选集的一个子集，本轮没跑到很正常。
    stale = [entry["pattern"] for entry in known if entry["pattern"] not in matched_patterns]

    summary = {
        "schema": SCHEMA,
        "runner": "testzeus-hercules",
        "status": "passed" if not blockers else "failed",
        "selection": {
            "type": args.selection_type,
            "target": args.selection_target,
            "lane": args.lane,
            "deploy_mode": args.deploy_mode,
        },
        "totals": counts,
        "duration_seconds": round(sum(c["duration_seconds"] for c in cases), 2),
        "gitlab": gitlab_context(),
        "cases": cases,
        "known_failures": {
            "file": args.known_failures,
            "entries": len(known),
            "matched": sorted(matched_patterns),
            "not_in_this_run": stale,
        },
        "artifacts": {"run_dir": str(run_dir), "junit": args.junit, "summary": str(summary_path)},
        "blockers": blockers,
    }

    summary_path.parent.mkdir(parents=True, exist_ok=True)
    summary_path.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")

    if reports:
        merge_junit(reports, Path(args.junit))

    label = {"passed": "通过", "failed": "失败", "expected_failure": "已知失败", "unexpected_pass": "已知失败转绿", "error": "出错", "skipped": "跳过"}
    print("\n── E2E 判定 ─────────────────────────────────────────────")
    for case in cases:
        mark = "✓" if case["status"] in ("passed", "expected_failure") else "✗"
        print(f"  {mark} [{case['suite']}] {case['name']}  {label.get(case['status'], case['status'])}  {case['duration_seconds']}s")
        if case["status"] in ("failed", "error", "skipped") and case["message"]:
            print(f"      {case['message'].splitlines()[0][:160]}")
    print(f"\n  共 {counts['cases']} 条：通过 {counts['passed']} / 失败 {counts['failed']} / 出错 {counts['errors']} / 已知失败 {counts['expected_failures']}")
    print(f"  summary: {summary_path}")
    if blockers:
        print("\n  判定失败：")
        for reason in blockers:
            print(f"    - {reason}")
    print("─────────────────────────────────────────────────────────\n")

    if not run_dir.is_dir() or known_problems:
        return 2
    return 1 if blockers else 0


if __name__ == "__main__":
    sys.exit(main())
