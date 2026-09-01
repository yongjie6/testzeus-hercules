"""Unit tests for the UI-side assertion tools (testzeus_hercules/core/tools/ui_assertions.py).

Every case here is shaped after a real ESM 3.0 defect that the counting-style assertions in
opt/cases/mp/ could not have caught, so the tests double as the record of what each mode is for.
"""

import asyncio
import json
from types import SimpleNamespace

from testzeus_hercules.core.tools.api_calls import set_variable_value
from testzeus_hercules.core.tools.ui_assertions import (
    assert_ui,
    capture_ui_value,
    visible_text,
)

# mp_snapshot 的真实形态：一行一个节点，方括号里是定位元数据。
# 缺陷 7099285452：对方昵称本该是备注名，页面渲染成了默认的「小量」——元素个数完全正常。
CHAT_HEADER_SNAPSHOT = """
view "小量"  [#chat-title testId=chat-title]
image (icon)  [src=https://cdn.daqian369.com/avatar/default.png]
button "对方报告"  [#peer-report]
textarea  [placeholder="请输入消息..." ]
""".strip()

# mp_page_data 的真实形态：JSON，键名是契约字段，不是屏幕上的字。
CHAT_PAGE_DATA = json.dumps(
    {
        "chat": {"chatTitle": "关系对话", "latestChatSummary": "压力有点大"},
        "peer": {"nickname": "小周", "avatar": "https://cdn.daqian369.com/a/b.png"},
    },
    ensure_ascii=False,
)


def _run(fn, **kwargs) -> dict:
    return json.loads(asyncio.run(fn(**kwargs)))


# ---------------------------------------------------------------- visible_text


def test_snapshot_locator_metadata_is_not_matchable():
    """testId / id / src 是定位用的，不是人看得见的字。留着它们，一条 absent 断言会被
    class 名或 CDN 域名误伤，红绿就和页面显示无关了。"""
    text = visible_text(CHAT_HEADER_SNAPSHOT)
    assert "小量" in text
    assert "请输入消息..." in text
    assert "chat-title" not in text
    assert "cdn.daqian369.com" not in text


def test_json_field_names_are_not_matchable():
    """键名 chatTitle 不出现在屏幕上。把它算进可见文本，断言就会因为一个同名字段而
    误判成「页面上有这个词」。"""
    text = visible_text(CHAT_PAGE_DATA)
    assert "关系对话" in text
    assert "chatTitle" not in text
    assert "nickname" not in text


# ---------------------------------------------------------------- contains / absent


def test_contains_catches_the_wrong_value_where_counting_could_not():
    """缺陷 7099285452 的形状：昵称元素存在（计数断言绿），但显示的是默认值。"""
    wrong = _run(assert_ui, payload=CHAT_HEADER_SNAPSHOT, mode="contains", expected="小周")
    assert wrong["assertion"] == "FAILED"
    assert wrong["occurrences"] == 0
    assert "小量" in wrong["preview"]

    right = _run(assert_ui, payload=CHAT_HEADER_SNAPSHOT, mode="contains", expected="小量")
    assert right["assertion"] == "PASSED"


def test_absent_passes_only_when_the_text_really_is_gone():
    gone = _run(assert_ui, payload=CHAT_HEADER_SNAPSHOT, mode="absent", expected="内测受邀用户", must_contain="对方报告")
    assert gone["assertion"] == "PASSED"

    still_there = _run(assert_ui, payload=CHAT_HEADER_SNAPSHOT, mode="absent", expected="对方报告", must_contain="小量")
    assert still_there["assertion"] == "FAILED"


def test_absent_without_a_positive_control_is_refused():
    """absent 在「文本里没有」时通过，而「没有」的成因大多和被测方无关：页面没渲染、
    抓错屏、快照过期。没有对照的话，这条断言在这些情况下全部判绿。"""
    out = _run(assert_ui, payload=CHAT_HEADER_SNAPSHOT, mode="absent", expected="内测受邀用户")
    assert out["assertion"] == "ERROR"
    assert "must_contain" in out["reason"]


def test_absent_fails_when_the_positive_control_is_missing():
    """对照不在，说明这一屏不是预期的那一屏。此时目标找不到，不能算作它已消失。"""
    out = _run(assert_ui, payload='view "正在加载..."', mode="absent", expected="未读红点", must_contain="我的报告")
    assert out["assertion"] == "FAILED"
    assert "正向对照" in out["reason"]


def test_absent_cannot_see_what_the_snapshot_drops():
    """这条钉的是能力边界，不是行为缺陷。

    mcp_servers/wxmp/wxml.mjs 的 finish() 会丢弃无文案、无 aria-label、非交互的节点，
    所以红点这类目标压根进不了待检文本——对它做 absent 恒真，加正向对照也救不了。
    这类判定要走 mp_visual_check；lint_gherkin 的 unverifiable-absent 规则负责在用例层
    把它们拦下来。
    """
    with_dot = 'view "我的报告"\nview "未读红点这种节点在真实快照里根本不会出现"'
    out = _run(assert_ui, payload='view "我的报告"', mode="absent", expected="红点", must_contain="我的报告")
    assert out["assertion"] == "PASSED"  # 红点在与不在，这条都通过 —— 所以不能这么写
    assert "红点" in with_dot  # 只是为了说明：真实快照里这一行不存在


def test_scope_stops_a_header_match_from_satisfying_a_list_assertion():
    payload = 'view "关系对话"  [#header]\nview "今天"  [#group-title]\nview "小周"  [#item-1]'
    out = _run(assert_ui, payload=payload, mode="contains", expected="关系对话", scope="小周")
    assert out["assertion"] == "FAILED"
    assert out["scope"] == "小周"


def test_scope_matching_nothing_fails_instead_of_checking_the_whole_page():
    out = _run(assert_ui, payload=CHAT_HEADER_SNAPSHOT, mode="contains", expected="小量", scope="根本没有这一段")
    assert out["assertion"] == "FAILED"
    assert "一行都没匹配到" in out["reason"]


# ---------------------------------------------------------------- count_eq


def test_count_eq_is_for_when_the_number_itself_is_the_requirement():
    payload = 'view "对话列表"\nview "怎么判断适不适合换工作"  [#c1]\nview "我最近总是拖延"  [#c2]'
    exactly_two = _run(assert_ui, payload=payload, mode="count_eq", expected="view", count=3)
    assert exactly_two["assertion"] == "PASSED"

    wrong = _run(assert_ui, payload=payload, mode="count_eq", expected="view", count=2)
    assert wrong["assertion"] == "FAILED"
    assert wrong["occurrences"] == 3


def test_count_eq_without_a_count_is_an_error_not_a_pass():
    out = _run(assert_ui, payload=CHAT_HEADER_SNAPSHOT, mode="count_eq", expected="view")
    assert out["assertion"] == "ERROR"
    assert "需要 count" in out["reason"]


# ---------------------------------------------------------------- no_placeholder


def test_no_placeholder_catches_raw_markdown():
    """缺陷 7101347296：个人报告的 Markdown 没走渲染，记号裸露在页面上。"""
    out = _run(assert_ui, payload='view "**核心特质**\n## 你的关系模式"', mode="no_placeholder")
    assert out["assertion"] == "FAILED"
    assert {h["kind"] for h in out["actual"]} == {"raw_markdown"}


def test_no_placeholder_catches_unrendered_data():
    out = _run(assert_ui, payload='view "你好，undefined"\nview "{{user.name}}"', mode="no_placeholder")
    assert out["assertion"] == "FAILED"
    kinds = {h["kind"] for h in out["actual"]}
    assert "js_empty_value" in kinds
    assert "unrendered_template" in kinds


def test_no_placeholder_does_not_fire_on_image_urls():
    """CDN 域名带点分段，形状和 i18n key 一样。误报一次，这个 mode 就没人敢用了。"""
    out = _run(assert_ui, payload=CHAT_HEADER_SNAPSHOT, mode="no_placeholder")
    assert out["assertion"] == "PASSED"


def test_no_placeholder_takes_case_specific_forbidden_values():
    """缺陷 7099827528「内测受邀用户」标签该去掉——通用规则认不出来，用例自己给。"""
    out = _run(assert_ui, payload='view "内测受邀用户"', mode="no_placeholder", forbidden="内测受邀用户, 小量")
    assert out["assertion"] == "FAILED"
    assert out["actual"] == [{"kind": "forbidden", "found": "内测受邀用户"}]


# ---------------------------------------------------------------- 假绿防护


def test_asserting_on_an_empty_payload_is_an_error_not_a_pass():
    """空快照上 absent 恒真。让它判绿，等于把「页面还没渲染」写成了一条通过的用例。"""
    out = _run(assert_ui, payload="", mode="absent", expected="错误提示")
    assert out["assertion"] == "ERROR"
    assert "没有任何可见文本" in out["reason"]


def test_an_uncaptured_variable_is_an_error_not_a_silently_literal_compare():
    out = _run(assert_ui, payload=CHAT_HEADER_SNAPSHOT, mode="absent", expected="${neverCaptured}")
    assert out["assertion"] == "ERROR"
    assert "没有被捕获过" in out["reason"]


def test_unknown_mode_is_rejected():
    out = _run(assert_ui, payload=CHAT_HEADER_SNAPSHOT, mode="equals", expected="x")
    assert out["assertion"] == "ERROR"
    assert "unknown mode" in out["reason"]


# ---------------------------------------------------------------- capture / 跨页一致性


def test_capture_then_assert_on_another_page_is_the_cross_page_check():
    """缺陷 7099316554 / 7098039604 的形状：A 页改了值，B 页没同步。
    只有「A 页抓值 -> B 页用同一个值断言」这条链路抓得到，模型自己读一遍再手抄抓不到。"""
    profile = 'view "备注名"\nview "阿周"  [#remark-value]'
    captured = _run(capture_ui_value, payload=profile, pattern=r'"(阿周)"', as_name="remark")
    assert captured["assertion"] == "PASSED"
    assert captured["captured"] == {"name": "remark", "value": "阿周"}

    home_not_synced = 'view "小周"  [#rel-card-1]'
    out = _run(assert_ui, payload=home_not_synced, mode="contains", expected="${remark}")
    assert out["assertion"] == "FAILED"
    assert out["expected"] == "阿周"


def test_capture_matching_nothing_is_an_error_not_an_empty_capture():
    """存了空串，后面那条 contains 会拿空串去匹配——空串在任何文本里都找得到，
    一条什么都没验的断言就此判绿。"""
    out = _run(capture_ui_value, payload=CHAT_HEADER_SNAPSHOT, pattern=r'昵称是"([^"]+)"', as_name="nickname")
    assert out["assertion"] == "ERROR"
    assert "没有匹配到" in out["reason"]


def test_capture_requires_exactly_one_group():
    out = _run(capture_ui_value, payload=CHAT_HEADER_SNAPSHOT, pattern=r'"([^"]+)"\s*(\[.*\])?', as_name="x")
    assert out["assertion"] == "ERROR"
    assert "正好一个捕获组" in out["reason"]


def test_capture_rejects_a_name_that_placeholders_cannot_reference():
    out = _run(capture_ui_value, payload=CHAT_HEADER_SNAPSHOT, pattern=r'"([^"]+)"', as_name="peer.nickname")
    assert out["assertion"] == "ERROR"
    assert "不是合法变量名" in out["reason"]


def test_api_captured_values_are_visible_to_ui_assertions():
    """接口侧建的数据和界面侧读的值共用一个变量域——「接口建的会话，端上断言它显示出来了」
    因此不需要把 id 抄一遍。"""
    set_variable_value("chatTitle", "怎么判断适不适合换工作")
    out = _run(assert_ui, payload='view "怎么判断适不适合换工作"  [#c1]', mode="contains", expected="${chatTitle}")
    assert out["assertion"] == "PASSED"


def test_every_ui_assertion_is_recorded_in_the_ledger(monkeypatch):
    """通过的断言不留痕，一条绿色用例就分不出「断言了十次全过」和「一次都没断言」。

    打桩直接落在 `assert_ui.__globals__` 上——那就是它查找 assertion_logger 的那本字典，
    按模块名再 import 一次拿到的不一定是同一个对象（见 conftest 的 _restore_module_registry）。
    这里要证的也正是这件事：assert_ui 每次调用都往台账里写一条，通过的也写。
    """
    written: list[str] = []
    monkeypatch.setitem(assert_ui.__globals__, "assertion_logger", written.append)

    _run(assert_ui, payload='view "小量"', mode="contains", expected="小量")
    _run(assert_ui, payload='view "小量"', mode="contains", expected="小周")

    assert [json.loads(line)["assertion"] for line in written] == ["PASSED", "FAILED"]
