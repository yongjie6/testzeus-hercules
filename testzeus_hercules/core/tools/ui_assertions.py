"""界面侧的确定性断言。

存在的理由：接口侧有 `assert_json_path`，界面侧一条都没有。`mcp_nav_agent` 手上只有
`execute_mcp_tool` 这类执行工具，于是「页面上显示的对不对」只能由模型读一遍快照自己说，
判定和证据都落在模型的散文里——这正是断言工具要取代的东西。

后果在 ESM 3.0 那批缺陷里看得很清楚：用例断言普遍写成「…个数应该不少于 1」，而那批缺陷
里最大的一类是**数对了、内容错了**——昵称显示成默认「小量」、双人会话标题全叫「关系对话」、
候选关系卡片文案写死设计稿、测量结果表头出现未翻译的 token。这些情况下元素个数一个不少，
计数断言全绿。

本模块提供两个工具：

- ``assert_ui``       对界面负载做确定性断言，结果进 assertion_logs.log 台账
- ``capture_ui_value`` 从界面负载里抓一个值存进本 scenario 的变量域，供跨页断言引用

两者都复用接口侧那套 ``${name}`` 变量域（``api_calls._VARIABLES``，按 scenario 隔离），
所以「接口建的数据，端上断言它显示出来了」和「A 页改的值，B 页断言它同步了」都不需要
把值抄来抄去。
"""

import json
import re
from typing import Annotated, Any, List

from testzeus_hercules.core.tools.api_calls import interpolate_value, set_variable_value
from testzeus_hercules.core.tools.tool_registry import (
    assertion_logger,
    assertion_subject,
    tool,
)
from testzeus_hercules.utils.logger import logger

_MODES = ("contains", "absent", "count_eq", "no_placeholder")

# mp_snapshot 一行长这样：
#   view "候选关系小周"  [#rel-card-1 testId=rel-card src=https://cdn/x.png]
# 方括号里除了 placeholder / value 之外都是定位用的元数据，不是人看得见的字。把它们留在
# 待检文本里，`absent` 会被 class 名误伤，`no_placeholder` 会把 CDN 域名当成 i18n key。
_SNAPSHOT_META = re.compile(r"\b(?:src|testId|class|style)=\S+|(?<![\w])#[\w:-]+")

_PLACEHOLDER_PATTERNS: List[tuple[str, "re.Pattern[str]"]] = [
    # 渲染兜底失效：本该是数据的位置直接把 JS 的空值印了出来
    ("js_empty_value", re.compile(r"(?<![\w])(undefined|NaN|\[object Object\])(?![\w])")),
    # 模板没被替换：Vue/Taro 的 {{ }} 或 JS 模板串漏到了界面上
    ("unrendered_template", re.compile(r"\{\{[^}]*\}\}|\$\{[^}]*\}")),
    # 格式化占位符原样输出
    ("format_placeholder", re.compile(r"%[sd](?![\w])")),
    # 富文本没走渲染，Markdown 记号裸露（缺陷 7101347296：个人报告 Markdown 未渲染）
    ("raw_markdown", re.compile(r"\*\*[^*\n]+\*\*|^\s{0,3}#{1,6}\s+\S", re.MULTILINE)),
    # i18n key 泄漏：三段以上全小写点分 token，界面上不该出现。
    # 但域名和文件名是同一个形状：support.esm.daqian369.com、report.final.pdf 都会命中，
    # 而它们是页面上完全正常的内容。工具描述鼓励「每步都顺手加一条 no_placeholder」，
    # 误报一次这个 mode 就没人敢用了，所以末段落在已知后缀上的一律放过。
    ("i18n_key", re.compile(r"(?<![\w.])[a-z][a-z0-9_]*(?:\.[a-z][a-z0-9_]*){2,}(?![\w.])")),
]


# 域名和文件名和 i18n key 是同一个词形。用末段是不是已知后缀来区分——这不是万能的，
# 但覆盖了实际会出现在界面上的绝大多数：客服邮箱域名、下载文件名、图片名。
_BENIGN_TAIL = {
    "com",
    "cn",
    "net",
    "org",
    "io",
    "co",
    "top",
    "xyz",
    "dev",
    "app",
    "png",
    "jpg",
    "jpeg",
    "gif",
    "webp",
    "svg",
    "pdf",
    "mp3",
    "mp4",
    "json",
    "html",
    "css",
    "js",
}


def _is_benign(label: str, found: str) -> bool:
    return label == "i18n_key" and found.rsplit(".", 1)[-1].lower() in _BENIGN_TAIL


def _json_strings(node: Any, out: List[str]) -> None:
    """只收 JSON 的字符串**值**，不收键名。

    键名是契约里的字段名，不是人在界面上看见的字。把 ``latestChatSummary`` 这种键名混进
    待检文本，`absent` 断言会被一个恰好同名的字段判红，而页面上根本没显示它。
    """
    if isinstance(node, str):
        out.append(node)
    elif isinstance(node, dict):
        for value in node.values():
            _json_strings(value, out)
    elif isinstance(node, list):
        for value in node:
            _json_strings(value, out)


def visible_text(payload: str) -> str:
    """把工具返回的原始负载收敛成「人在屏幕上看得见的字」。

    三种来源一起处理，因为用例作者不该为了断言去记哪个工具返回哪种格式：
      - ``mp_page_data`` 之类的 JSON  -> 取字符串值，丢键名和 URL
      - ``mp_snapshot`` 的紧凑行       -> 丢定位元数据，留文案 / placeholder / value
      - ``mp_get_text`` / ``get_page_text`` 的纯文本 -> 原样

    「看得见」是这几个工具的共同语义，也是用例里「应该看到「X」」这句话的语义。所有断言
    都基于这一份文本，而不是原始负载——否则 class 名、data-testid、CDN 地址都会参与匹配，
    断言的红绿就和页面显示无关了。
    """
    text = payload or ""
    stripped = text.strip()
    if stripped.startswith("{") or stripped.startswith("["):
        try:
            collected: List[str] = []
            _json_strings(json.loads(stripped), collected)
            # URL 不是给人读的，而且带点分段，会被 i18n_key 规则误报
            return "\n".join(s for s in collected if not s.startswith(("http://", "https://", "data:")))
        except (json.JSONDecodeError, TypeError, RecursionError):
            pass
    return _SNAPSHOT_META.sub(" ", text)


def _record(result: dict) -> str:
    payload = json.dumps(result, ensure_ascii=False, separators=(",", ":"))
    assertion_logger(payload)
    if result.get("assertion") != "PASSED":
        logger.warning("assert_ui %s: %s", result.get("assertion"), payload)
    return payload


def _preview(text: str, limit: int = 400) -> str:
    flat = " ".join(text.split())
    return flat if len(flat) <= limit else flat[:limit] + " …"


@tool(
    agent_names=["mcp_nav_agent", "browser_nav_agent"],
    name="assert_ui",
    description=(
        "Assert deterministically on what a page is showing, instead of reading a snapshot and "
        "judging pass/fail yourself. Always use this to verify a UI expectation — the assertion "
        "result, not your own reading, is what should decide is_passed, and it is what gets "
        "written to the run's assertion ledger as evidence.\n"
        "Parameters:\n"
        "  - payload: the raw string returned by the tool that read the page (mp_snapshot, "
        "mp_page_data, mp_get_text, get_page_text — all four are accepted as-is).\n"
        "  - mode: contains, absent, count_eq, or no_placeholder.\n"
        "  - expected: the text to look for. Required for contains, absent and count_eq; leave "
        "empty for no_placeholder.\n"
        "  - count: for count_eq only, how many times expected must appear.\n"
        "  - scope: optional — narrow the check to lines containing this text first, so an "
        "assertion about the conversation list is not satisfied by the same words in the header.\n"
        "  - forbidden: for no_placeholder only, extra comma-separated strings that must not "
        "appear, e.g. a default nickname the page should have replaced with the real one.\n"
        "  - must_contain: REQUIRED for absent — text that must be present in the same payload, "
        "as a positive control. absent passes whenever the text is not found, including when the "
        "page never rendered, when you captured the wrong screen, or when the payload is stale. "
        "Naming something that should be there proves the check actually looked at the right, "
        "rendered page. Pick a stable label from that screen, not the thing you are asserting is "
        "gone.\n"
        "Everything is matched against the page's VISIBLE text: ids, testIds, class names and "
        "image URLs are stripped first, and for a JSON payload only string values are searched, "
        "never field names. So an assertion never passes or fails because of markup.\n"
        "Prefer asserting the VALUE over counting elements. 'the nickname element exists' stays "
        "green when the page renders the wrong nickname; contains with the expected nickname does "
        "not. Reach for count_eq only when the number itself is the requirement.\n"
        "no_placeholder catches a page that rendered but rendered nothing real: undefined, NaN, "
        "[object Object], an unsubstituted {{template}} or ${template}, raw Markdown that should "
        "have been formatted, and leaked i18n keys. Use it after any step that loads content, as "
        "a cheap second assertion alongside the specific one.\n"
        "expected, scope and forbidden all accept ${name} placeholders captured earlier in this "
        "scenario — by capture_ui_value on another page, or by extract_from_response from an API "
        "call — which is how a cross-page consistency check is written without retyping the value.\n"
        "A warning about absent: it can only see what the payload can represent. A mini program "
        "snapshot drops nodes that have no text, no aria-label and are not interactive, so an "
        "unread-dot badge, a coloured border or an icon-only state is simply not in it — asserting "
        "such a thing is absent passes whether or not the bug is there. For those, judge the "
        "screen with mp_visual_check, or have the page expose an aria-label first.\n"
        "Returns assertion: PASSED, FAILED, or ERROR, with the actual finding and a preview of the "
        "visible text, so a failure is diagnosable without re-reading the whole page."
    ),
)
async def assert_ui(
    payload: Annotated[str, "Raw string returned by the tool that read the page (mp_snapshot, mp_page_data, mp_get_text, get_page_text)."],
    mode: Annotated[str, "One of: contains, absent, count_eq, no_placeholder."] = "contains",
    expected: Annotated[str, "Text to look for. Required for contains, absent, count_eq; leave empty for no_placeholder."] = "",
    count: Annotated[int, "For count_eq only: how many times expected must appear."] = -1,
    scope: Annotated[str, "Optional: first narrow to lines containing this text."] = "",
    forbidden: Annotated[str, "For no_placeholder only: extra comma-separated strings that must not appear."] = "",
    must_contain: Annotated[str, "Required for absent: text that must be present in the same payload, proving the check looked at the right rendered page."] = "",
) -> Annotated[str, "Minified JSON: assertion (PASSED/FAILED/ERROR), mode, expected, actual, preview."]:
    if mode not in _MODES:
        return _record({"assertion": "ERROR", "mode": mode, "reason": f"unknown mode {mode!r}, expected one of {list(_MODES)}"})

    result: dict = {"mode": mode}

    text = visible_text(payload)
    # 指纹标的是「这次看的是哪一份内容」。同一条断言在两个页面上跑（跨页一致性检查就是
    # 这个形状），判定侧才分得开，不会把 A 页那条判否的折进 B 页那条判是的。
    result["subject"] = assertion_subject(text)
    if not text.strip():
        # 空负载对 absent 是"当然没有"，对 contains 是"当然没有"。两边都判过等于把
        # 「页面还没渲染出来」和「页面渲染了但不含这个词」混成一件事，而前者几乎总是
        # 用例自己的时序问题。统一报错，逼着作者去补等待。
        return _record({**result, "assertion": "ERROR", "reason": "payload 里没有任何可见文本，页面可能还没渲染完；先等待再断言，不要在空快照上判定"})

    if scope:
        needle = str(interpolate_value(scope))
        kept = [line for line in text.splitlines() if needle in line]
        result["scope"] = needle
        if not kept:
            return _record({**result, "assertion": "FAILED", "reason": f"scope {needle!r} 在可见文本里一行都没匹配到", "preview": _preview(text)})
        text = "\n".join(kept)

    if mode == "no_placeholder":
        extra = [item.strip() for item in str(interpolate_value(forbidden)).split(",") if item.strip()]
        hits: List[dict] = []
        for label, pattern in _PLACEHOLDER_PATTERNS:
            found = next((m for m in pattern.finditer(text) if not _is_benign(label, m.group(0))), None)
            if found:
                hits.append({"kind": label, "found": found.group(0)[:80]})
        for item in extra:
            if item in text:
                hits.append({"kind": "forbidden", "found": item})
        result["checked"] = [label for label, _ in _PLACEHOLDER_PATTERNS] + (["forbidden"] if extra else [])
        if hits:
            return _record({**result, "assertion": "FAILED", "actual": hits, "reason": "页面渲染出了占位内容或未渲染的原始标记", "preview": _preview(text)})
        return _record({**result, "assertion": "PASSED", "actual": "no placeholder pattern found"})

    want = str(interpolate_value(expected))
    if not want.strip():
        return _record({**result, "assertion": "ERROR", "reason": f"mode={mode} 需要 expected，当前为空"})
    if re.search(r"\$\{[A-Za-z_][A-Za-z0-9_]*\}", want):
        # 未被捕获的 ${name} 原样留了下来。拿它当字面量比，contains 稳定判否、absent 稳定
        # 判是——后者是一条永远绿的假断言，正是断言工具最不能产生的东西。
        # 用 search 不用 fullmatch：`昵称${neverCaptured}` 这种内嵌形式同样是假的，
        # 而 fullmatch 放它过去，absent 直接判绿。
        return _record(
            {
                **result,
                "assertion": "ERROR",
                "expected": want,
                "reason": f"expected={want!r} 里的变量没有被捕获过，拿它当字面量比是假的：absent 永远通过、contains 永远失败。先确认上一步的 capture_ui_value / extract_from_response 真的捕获成功",
            }
        )

    result["expected"] = want
    occurrences = text.count(want)
    result["occurrences"] = occurrences

    if mode == "contains":
        passed = occurrences > 0
    elif mode == "absent":
        # absent 在「文本里没有」时通过，而「没有」有太多种成因和被测方无关：页面还没渲染、
        # 抓错了屏、快照是上一页的。没有正向对照的话，这条断言在这些情况下全部判绿，
        # 而它们恰恰是它该报红的时候。所以要求作者点名一件「这一屏上本来就该有」的东西。
        #
        # 注意这条对照证明的是「看对了页面」，不证明「目标若存在则可见」。后者是另一回事：
        # 快照会丢掉无文案、无 aria-label、非交互的节点，红点/角标/纯图标这类目标压根进不了
        # 待检文本，对它们做 absent 恒真——那种情况要换 mp_visual_check，不是加对照能救的。
        control = str(interpolate_value(must_contain)).strip()
        if not control:
            return _record(
                {
                    **result,
                    "assertion": "ERROR",
                    "reason": "mode=absent 必须给 must_contain 作正向对照：页面没渲染、抓错屏、快照过期时 absent 都会判绿，正是它该报红的时候。点一件这一屏上本来就该有的稳定文案。",
                }
            )
        result["must_contain"] = control
        if control not in text:
            return _record(
                {
                    **result,
                    "assertion": "FAILED",
                    "reason": f"正向对照 {control!r} 不在可见文本里：这一屏不是预期的那一屏，或还没渲染完。此时 {want!r} 找不到不能算作它已消失。",
                    "preview": _preview(text),
                }
            )
        passed = occurrences == 0
    else:  # count_eq
        if count < 0:
            return _record({**result, "assertion": "ERROR", "reason": "mode=count_eq 需要 count（期望出现次数），当前没传"})
        result["count"] = count
        passed = occurrences == count

    result["assertion"] = "PASSED" if passed else "FAILED"
    if not passed:
        result["preview"] = _preview(text)
    return _record(result)


@tool(
    agent_names=["mcp_nav_agent", "browser_nav_agent"],
    name="capture_ui_value",
    description=(
        "Capture a value shown on the current page into a named variable, so a later step can "
        "assert on it with ${name} instead of you retyping what you read. This is how a "
        "cross-page consistency check is written: capture the nickname on the profile page, then "
        "assert_ui contains ${nickname} on the home page and in the conversation header. Reading "
        "the value yourself and typing it into the next step is exactly the step where a wrong "
        "value stops being detectable.\n"
        "Parameters:\n"
        "  - payload: the raw string returned by the tool that read the page.\n"
        "  - pattern: a Python regular expression with EXACTLY ONE capture group; the group is "
        'what gets stored. For example 昵称\\s*"([^"]+)" captures the nickname out of a snapshot '
        "line.\n"
        "  - as_name: the variable name to store it under, letters, digits and underscore only.\n"
        "The pattern runs against the page's visible text, with ids, testIds and image URLs "
        "already stripped. The captured value is scoped to this scenario and shares one namespace "
        "with API-side captures, so an API-created id and a UI-read label are referenced the same "
        "way. Matching nothing is an error, not an empty capture."
    ),
)
async def capture_ui_value(
    payload: Annotated[str, "Raw string returned by the tool that read the page."],
    pattern: Annotated[str, "Python regex with exactly one capture group; the group is what gets stored."],
    as_name: Annotated[str, "Variable name to store the captured value under."],
) -> Annotated[str, "Minified JSON: captured (name/value) or an error explaining what did not match."]:
    if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", as_name or ""):
        return _record({"assertion": "ERROR", "mode": "capture", "reason": f"as_name={as_name!r} 不是合法变量名，只能用字母数字下划线（${{...}} 的占位符正则就是这个）"})

    try:
        compiled = re.compile(pattern)
    except re.error as exc:
        return _record({"assertion": "ERROR", "mode": "capture", "expected": as_name, "reason": f"pattern 不是合法正则 {pattern!r}: {exc}"})
    if compiled.groups != 1:
        return _record(
            {"assertion": "ERROR", "mode": "capture", "expected": as_name, "reason": f"pattern 需要正好一个捕获组，当前有 {compiled.groups} 个；要捕获的那部分用 (...) 括起来，其余用 (?:...)"}
        )

    text = visible_text(payload)
    found = compiled.search(text)
    if not found:
        # 捕获不到就报错，不存空值：存了空值之后，后面那条 contains 会拿空串去匹配，
        # 空串在任何文本里都找得到，于是一条什么都没验的断言判绿。
        # mode / expected / subject 一起构成判定侧的折叠键。不写的话这条记录的键是全空，
        # 一次失败的 capture 会被后面任何一次成功的 capture 顶掉——假绿。
        return _record(
            {
                "assertion": "ERROR",
                "mode": "capture",
                "expected": as_name,
                "subject": assertion_subject(text),
                "pattern": pattern,
                "reason": "pattern 在可见文本里没有匹配到任何内容",
                "preview": _preview(text),
            }
        )

    value = found.group(1)
    set_variable_value(as_name, value)
    return _record({"assertion": "PASSED", "mode": "capture", "expected": as_name, "subject": assertion_subject(text), "captured": {"name": as_name, "value": value}})
