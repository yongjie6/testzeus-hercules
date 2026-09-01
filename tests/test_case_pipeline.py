"""用例库 -> 可执行套件 这条流水线的单测。

覆盖两个脚本：

- assemble_version.py   @blocked 标签的解析、排除、上报
- helper_scripts/lint_gherkin.py  用例质量规则

两者都不进 testzeus_hercules 包，用 importlib 从文件加载。
"""

import importlib.util
import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]


def _load(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


assemble = _load("_assemble_version", ROOT / "assemble_version.py")
linter = _load("_lint_gherkin", ROOT / "helper_scripts" / "lint_gherkin.py")


FEATURE = """Feature: 示例

  Scenario: TC-A 能跑的
    Given 以 logged-in 状态进入小程序
    Then 应该看到「首页」

  @blocked:tooling
  # blocked: 手机号授权是微信原生弹窗，自动化点不到
  Scenario: TC-B 跑不了的
    Given 以 guest 状态进入小程序
    Then 应该看到「填写信息」

  Scenario: TC-C 也能跑的
    Given 以 logged-in 状态进入小程序
    Then 应该看到「我的」
"""


# ------------------------------------------------------------------ 标签归属


def test_a_marker_belongs_to_the_scenario_below_it_not_the_one_above():
    """按 `Scenario:` 行切的话，TC-B 的 @blocked 会落到 TC-A 的块尾——
    于是排除掉的是 TC-A，而真正跑不了的 TC-B 照跑照红。"""
    _, blocks = assemble.split_header_and_scenarios(FEATURE)
    assert len(blocks) == 3
    assert assemble.blocked_reason(blocks[0]) is None
    assert assemble.blocked_reason(blocks[1]) == ("tooling", "手机号授权是微信原生弹窗，自动化点不到")
    assert assemble.blocked_reason(blocks[2]) is None


def test_markers_are_stripped_before_the_runner_sees_them():
    """Hercules 自己的切分器也在 `Scenario:` 上切，标签留在组装产物里会再次跑到
    上一条用例名下。标签是构建期元数据，到这一步已经用完了。"""
    _, blocks = assemble.split_header_and_scenarios(FEATURE)
    assert "@blocked" not in assemble.strip_markers(blocks[1])
    assert assemble.strip_markers(blocks[1]).lstrip().startswith("Scenario:")


# ------------------------------------------------------------------ 排除与上报


def test_blocked_scenarios_are_partitioned_out_and_reported():
    _, blocks = assemble.split_header_and_scenarios(FEATURE)
    report: list[dict] = []
    runnable = assemble.partition_blocked("mp/demo", blocks, report)
    assert len(runnable) == 2
    assert report == [
        {
            "module": "mp/demo",
            "scenario": "TC-B 跑不了的",
            "kind": "tooling",
            "reason": "手机号授权是微信原生弹窗，自动化点不到",
        }
    ]


def test_an_unknown_blocked_kind_is_refused():
    """kind 决定这条该找谁解，所以是受控词表。放开成自由文本，它就退化成第二种注释。"""
    block = "@blocked:later\n  # blocked: 以后再说\n  Scenario: TC-X\n    Then 应该看到「x」"
    with pytest.raises(SystemExit) as caught:
        assemble.partition_blocked("mp/demo", [block], [])
    assert "kind" in str(caught.value)


def test_a_blocked_tag_without_a_reason_is_refused():
    block = "@blocked:tooling\n  Scenario: TC-X\n    Then 应该看到「x」"
    with pytest.raises(SystemExit) as caught:
        assemble.partition_blocked("mp/demo", [block], [])
    assert "必须写原因" in str(caught.value)


def test_the_legacy_comment_spelling_is_refused_not_tolerated():
    """`# ⛔` 对构建没有任何意义，用例照样被组装、照样跑、照样红。同时容忍两种拼写，
    等于把这个约定退回它取代的那个状态。"""
    module = ROOT / "opt" / "cases" / "mp" / "nav"
    text = (module / "v3.0.feature").read_text(encoding="utf-8")
    assert "⛔" not in text, "仓库里不该再有遗留写法"


def test_real_repo_assembles_with_blocked_excluded(tmp_path, monkeypatch):
    """跑真实用例库：默认排除 @blocked，--include-blocked 时一条不少。"""
    monkeypatch.chdir(ROOT)
    monkeypatch.setattr(assemble, "BUILD", Path(tmp_path))

    assert assemble.assemble("3.0", "mp", include_blocked=False) == 0
    out = Path(tmp_path) / "3.0-mp"
    excluded = json.loads((out / "blocked.json").read_text(encoding="utf-8"))
    assert excluded["include_blocked"] is False
    assert len(excluded["entries"]) == 21
    assert {e["kind"] for e in excluded["entries"]} == {"tooling", "fixture", "product"}
    runnable = sum(f.read_text(encoding="utf-8").count("Scenario:") for f in out.rglob("input/*.feature"))

    assert assemble.assemble("3.0", "mp", include_blocked=True) == 0
    everything = sum(f.read_text(encoding="utf-8").count("Scenario:") for f in out.rglob("input/*.feature"))
    assert everything - runnable == 21


def test_a_regression_entry_pointing_at_a_blocked_case_is_refused(tmp_path, monkeypatch):
    """回归集说的是「每次发版都要重跑」，指向一条跑不起来的用例是自相矛盾。
    静默丢掉它会让回归集悄悄少一条，而报告照样是绿的。"""
    monkeypatch.chdir(ROOT)
    monkeypatch.setattr(assemble, "BUILD", Path(tmp_path))
    manifest = Path(tmp_path) / "regression.txt"
    manifest.write_text("mp/nav :: TC-ESM-052-L1-7\n", encoding="utf-8")
    monkeypatch.setattr(assemble, "MANIFEST", manifest)

    with pytest.raises(SystemExit) as caught:
        assemble.assemble("regression", "mp")
    assert "@blocked" in str(caught.value)


# ------------------------------------------------------------------ 质量规则


def _lint(tmp_path, text: str, known: list[str] | None = None):
    path = tmp_path / "case.feature"
    path.write_text(text, encoding="utf-8")
    return linter.lint_quality(path, known or [])


def test_a_literal_that_is_a_description_is_flagged():
    """实测语料校准过：这三条是描述，agent 拿它没法做任何事。"""
    assert linter.looks_like_a_description("被点击的那条推荐问题在推荐区里的残留")
    assert linter.looks_like_a_description("小程序本地存储中的手机号明文")
    assert linter.looks_like_a_description("授权成功后弹出的 Toast")


def test_real_ui_strings_are_not_flagged():
    """误报一次，作者就会开始无视整个 linter。这几条都是页面上真实出现的字。"""
    assert not linter.looks_like_a_description("说说你们的情况好吗？请问你们是什么关系呢？")
    assert not linter.looks_like_a_description("需要授权手机号才能登录，可重新点击按钮。")
    assert not linter.looks_like_a_description("内容因安全原因已被拦截")
    assert not linter.looks_like_a_description("使用 Narnia 测试账号")
    assert not linter.looks_like_a_description("首页")


def test_pseudo_literal_is_an_error(tmp_path):
    findings = _lint(tmp_path, "Feature: x\n\n  Scenario: TC-A\n    Then 不应该看到「小程序本地存储中的手机号明文」\n")
    assert [(f.severity, f.rule) for f in findings] == [("error", "pseudo-literal")]


def test_a_scenario_without_a_then_is_an_error(tmp_path):
    findings = _lint(tmp_path, "Feature: x\n\n  Scenario: TC-A\n    Given 以 logged-in 状态进入小程序\n    When 点击「发送」\n")
    assert [f.rule for f in findings] == ["no-assertion"]


def test_a_weakest_count_assertion_is_a_warning(tmp_path):
    findings = _lint(tmp_path, "Feature: x\n\n  Scenario: TC-A\n    Then 报告入口个数应该不少于 1\n")
    assert [(f.severity, f.rule) for f in findings] == [("warn", "weak-count")]


def test_a_bounded_count_assertion_is_not_flagged(tmp_path):
    """「不多于 2」「为 0」都是能证伪的；只有下界 1 才是「元素在就绿」。"""
    findings = _lint(tmp_path, "Feature: x\n\n  Scenario: TC-A\n    Then tab 个数应该不多于 2\n    And 历史消息条数应该为 0\n")
    assert findings == []


def test_duplicate_titles_are_an_error(tmp_path):
    """Hercules 给后一条自动加 ' - 1' 后缀，报告里两条看起来像同一条跑了两次。"""
    findings = _lint(tmp_path, "Feature: x\n\n  Scenario: TC-A 一样的标题\n    Then 应该看到「首页」\n\n  Scenario: TC-A 一样的标题\n    Then 应该看到「我的」\n")
    assert [f.rule for f in findings] == ["duplicate-title"]


def test_blocked_and_known_failure_at_once_is_an_error(tmp_path):
    """两者含义互斥：known_failures 说「跑得起来，红得有理由」，@blocked 说「压根跑不起来」。
    跑不起来的用例产不出红，留在名单里只会一直豁免一条不存在的失败。"""
    text = "Feature: x\n\n  @blocked:tooling\n  # blocked: 点不到\n  Scenario: TC-ESM30-GW-05 冲突的\n    Then 应该看到「首页」\n"
    findings = _lint(tmp_path, text, known=["TC-ESM30-GW-05"])
    assert [f.rule for f in findings] == ["blocked-conflict"]


def test_legacy_blocked_comment_is_an_error(tmp_path):
    findings = _lint(tmp_path, "Feature: x\n\n  Scenario: TC-A\n    # ⛔ 当前不可执行：随便什么理由\n    Then 应该看到「首页」\n")
    assert [f.rule for f in findings] == ["blocked-syntax"]


def test_the_repo_itself_has_no_error_level_findings():
    """存量 warn 允许留着（有棘轮兜底），但 error 级说的是「这条用例执行不了」——
    仓库里不该有。这条测试和 CI 的 --strict 是同一个判定。"""
    known = linter.read_known_failure_patterns()
    files = linter.find_feature_files([str(ROOT / "opt" / "cases")])
    # 换个工作目录跑时相对路径会扫出空列表，下面那个 `errors == []` 于是恒真——
    # 一条什么都没检查的绿。
    assert files, "没有扫到任何用例文件，这条测试什么也没验"
    findings = []
    for path in files:
        findings.extend(linter.lint_quality(path, known))
    errors = [f for f in findings if f.severity == "error"]
    assert errors == [], "\n".join(f"{f.path}:{f.line} [{f.rule}] {f.message}" for f in errors)


def test_the_blocked_marker_is_valid_gherkin():
    """约定本身必须过真解析器。

    第一版写成 `@blocked(tooling: 原因)`——Hercules 自己的正则切分器完全接受，assemble
    也解析得出来，但 Gherkin 规定标签里不能有空格，真解析器直接报
    "A tag may not contain whitespace"。也就是说那一版凭空给用例库引入了 2 个非标准
    文件，而这个脚本存在的意义之一正是让这种漂移可见。所以约定改成了「标签只带 kind，
    原因写在紧邻的注释里」，并且用这条测试钉住。
    """
    from gherkin import Parser

    text = "Feature: x\n\n  @blocked:tooling\n  # blocked: 手机号授权是微信原生弹窗，自动化点不到\n  Scenario: TC-A\n    Then 应该看到「首页」\n"
    Parser().parse(text)  # 抛异常即失败


def test_the_repo_is_all_standard_gherkin():
    """用例库整体过真解析器。新约定引入非标准语法时，这里先红。"""
    files = linter.find_feature_files([str(ROOT / "opt" / "cases")])
    assert files, "没有扫到任何用例文件，这条测试什么也没验"
    broken = [str(path) for path in files if not linter.lint_syntax(path)["ok"]]
    assert broken == []


def test_an_absent_assertion_on_a_textless_affordance_is_an_error(tmp_path):
    """快照会丢掉无文案、无 aria-label、非交互的节点（mcp_servers/wxmp/wxml.mjs 的 finish），
    所以「不应该看到红点」不管缺陷在不在都会通过——一条恒真断言冒充了覆盖。"""
    findings = _lint(tmp_path, "Feature: x\n\n  Scenario: TC-A\n    Then 不应该看到小红点\n")
    assert [(f.severity, f.rule) for f in findings] == [("error", "unverifiable-absent")]


def test_an_absent_assertion_on_real_text_is_fine(tmp_path):
    findings = _lint(tmp_path, "Feature: x\n\n  Scenario: TC-A\n    Then 不应该看到「内容因安全原因已被拦截」\n")
    assert findings == []


def test_routing_a_textless_affordance_to_visual_check_clears_the_rule(tmp_path):
    """规则针对的是 absent 这种写法，不是「红点」这个词。改成视觉判读就不该再报。"""
    findings = _lint(tmp_path, "Feature: x\n\n  Scenario: TC-A\n    Then 用 mp_visual_check 确认入口上已经没有未读红点\n")
    assert findings == []
