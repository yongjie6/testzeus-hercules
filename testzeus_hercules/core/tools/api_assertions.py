import json
import re
from typing import Annotated, Any

from testzeus_hercules.core.tools.api_calls import interpolate_value
from testzeus_hercules.core.tools.tool_registry import (
    assertion_logger,
    assertion_subject,
    tool,
)
from testzeus_hercules.utils.jsonpath_helper import find_json_path
from testzeus_hercules.utils.logger import logger

# Operators kept scalar-in/scalar-out (no tuples, no nested schemas) so the generated
# JSON Schema stays provider-portable — see the tool signatures below.
_OPERATORS = ("eq", "ne", "contains", "exists", "not_exists", "not_empty", "gt", "lt", "all_gte", "all_lte", "count_eq", "eq_path")


def _coerce(raw: str) -> Any:
    """Turn an `expected` string back into a JSON value: "true"->True, "0"->0, else the raw string."""
    try:
        return json.loads(raw)
    except (json.JSONDecodeError, TypeError):
        return raw


def _resolve_expected(raw: str) -> Any:
    """先替换 ``${name}``，再还原 JSON 字面量。

    顺序不能反：``${sessionId}`` 得先变成实际值，才谈得上判断它是不是 JSON 字面量。
    整体命中一个占位符时 interpolate_value 会把捕获时的类型原样还给我们（int 还是 int），
    那种情况下不能再过一遍 json.loads——捕获到的字符串 id 会被当成 JSON 解析。
    """
    value = interpolate_value(raw)
    if not isinstance(value, str):
        return value
    return _coerce(value)


def _available_keys(doc: Any, path: str) -> str:
    """路径打空时，报出最深一层还能走通的父路径下有哪些键。

    只说"path matched nothing"，调用方分不清是「字段确实不存在」还是「自己路径写错了」，
    于是把一次自己的笔误当成被测方的缺陷报上去。把那一层实际有的键列出来，写错的人
    一眼就能看出该改哪儿。
    """
    parts = [p for p in path.replace("$", "").split(".") if p]
    while parts:
        parts.pop()
        prefix = "$" + ("." + ".".join(parts) if parts else "")
        try:
            found = find_json_path(doc, prefix)
        except Exception:
            continue
        if not found:
            continue
        node = found[0]
        if isinstance(node, dict):
            return f"{prefix} 下实际有的键: {sorted(node)}"
        if isinstance(node, list):
            first = node[0] if node else None
            inner = f"，元素的键: {sorted(first)}" if isinstance(first, dict) else ""
            return f"{prefix} 是长度 {len(node)} 的数组{inner}"
    return ""


def _captured_variable_hint(path: str) -> str:
    """路径的末段正好是本 scenario 已捕获的变量名时，点破该用 ${name} 而不是路径。

    观测到的实际错法（TC-ESM30-CH-22）：把上一步捕获的 id 写成 ``$.captured.userMessageId``
    去查当前响应。``captured`` 只出现在做捕获的那一次响应里，后续调用的响应里没有这个键，
    于是断言永远打空——而失败信息只说"路径没匹配上"，看不出是取值方式用错了。
    """
    leaf = path.rstrip("]").rsplit(".", 1)[-1].split("[")[0]
    if not leaf:
        return ""
    try:
        from testzeus_hercules.core.tools.api_calls import _var_store

        store = _var_store()
    except Exception:
        return ""
    if leaf in store:
        return f"{leaf!r} 是本 scenario 已捕获的变量，不是响应里的字段：用 expected=${{{leaf}}} 比较，不要写成路径"
    return ""


_UNRESOLVED = re.compile(r"^\$\{[A-Za-z_][A-Za-z0-9_]*\}$")


def _unresolved_placeholder(value: Any) -> bool:
    """插值之后仍然是 ``${name}`` 形态，说明这个变量根本没被捕获过。

    interpolate_value 对没捕获过的名字会原样返回，好让笔误显形在请求里。但在断言里
    这会变成最坏的一种假绿：``ne`` 拿真实值和字面量 ``"${foo}"`` 比，永远不等，永远通过。
    与其静默放行，不如判 ERROR 说清楚变量没捕获到。
    """
    return isinstance(value, str) and bool(_UNRESOLVED.match(value))


def _loosely_equal(actual: Any, want: Any) -> bool:
    """按文本比较两个值。

    protojson 把所有 int64 序列化成字符串，所以响应里的 ``statusCode`` 是 ``"0"`` 而不是 ``0``；
    用例文档也要求作者按字符串传。但 ``expected`` 是个字符串参数，``"0"`` 经 json.loads 会变成
    整数 0，于是 ``"0" == 0`` 判否——工具口径和被测契约口径正好相反。

    这里只补一条按文本相等的判定，并在结果里标注是靠它过的，不静默放宽：真出现类型不符时，
    报告里看得见。
    """
    if isinstance(actual, bool) or isinstance(want, bool):
        # True/1、False/0 在 Python 里本来就相等，再按文本比会把 "True" 和 "1" 也扯进来。
        return False
    return str(actual) == str(want)


def _record(result: dict) -> str:
    """记账 + 序列化。每条断言都留痕，不管过没过。"""
    payload = json.dumps(result, ensure_ascii=False, separators=(",", ":"))
    try:
        assertion_logger(payload)
    except Exception:  # 记账失败不能把断言本身带垮
        logger.debug("assertion_logger unavailable", exc_info=True)
    outcome = result.get("assertion")
    if outcome == "PASSED":
        logger.info("assert_json_path PASSED: %s", result)
    else:
        logger.info("assert_json_path %s: %s", outcome, result)
    return payload


@tool(
    agent_names=["api_nav_agent"],
    name="assert_json_path",
    description=(
        "Make a deterministic assertion against a JSONPath in a previous tool's JSON response, instead of "
        "judging pass/fail by reading the response text. Always use this to verify a response field rather "
        "than deciding success yourself — the assertion result, not your own reading, is what should decide "
        "is_passed.\n"
        "Parameters:\n"
        "  - response_json: the JSON string returned by the API call being verified (e.g. from "
        "generic_http_api or esm_call).\n"
        "  - path: JSONPath into that JSON, for example $.body.myPage.hasScaleResult.\n"
        "  - operator: eq, ne, contains, exists, not_exists, not_empty, gt, lt, all_gte, all_lte, count_eq, or eq_path.\n"
        '  - expected: the expected value as a string; JSON literals are honored ("true", "0", '
        '"null", \'"some string"\'), anything else is compared as a raw string. For eq_path, this is '
        "instead a second JSONPath compared against the first, for cross-field checks within the same "
        "response (e.g. a field that should mirror another field further up the document).\n"
        "Both path and expected accept ${name} placeholders captured earlier in this scenario, so a "
        "cross-step check writes expected=${sessionId} directly instead of retyping the id.\n"
        "eq and ne also match across the string/number divide: an API that serialises int64 as a JSON "
        'string still passes expected=0 against an actual of "0", and the result says so in a note.\n'
        'To assert a field is non-empty use not_empty, NOT ne with an empty expected: ne "" lets a JSON '
        "null through, and lets an unreplaced ${placeholder} through too, so it passes on exactly the "
        "cases it is meant to catch. not_empty requires the path to match, the value to be non-null, and "
        "a string value to be non-blank.\n"
        "count_eq counts the ELEMENTS of the array the path lands on, so $.body.items count_eq 0 is the "
        "way to assert an empty list. Append [*] to the path to count how many times the path itself "
        "matched instead.\n"
        "gt and lt compare only the FIRST value the path matched, so a path landing on many values "
        "(e.g. every dimension score) checks one of them and silently ignores the rest. To bound every "
        "matched value use all_gte / all_lte: path=$.body.result.scoreResults[*].score with "
        "operator=all_gte expected=-3, then again with all_lte expected=3, asserts the whole set is "
        "inside [-3, 3] and names the first out-of-range value on failure.\n"
        "To assert a whole sequence, point the path at every value and give expected as a JSON array: "
        "path=$.body.questions[0].choices[*].choiceValue with expected=[1,2,3,4,5,6] compares the full "
        "ordered list in one call, instead of one assertion per element.\n"
        "When a path matches nothing the failure names the keys that DO exist at the closest parent that "
        "resolved — read it before concluding the field is missing, because the usual cause is a wrong "
        "path rather than an absent field.\n"
        "Returns assertion: PASSED, FAILED, or ERROR, with the actual and expected values so a failure is "
        "diagnosable without re-reading the raw response."
    ),
)
async def assert_json_path(
    response_json: Annotated[str, "The JSON string returned by the API call being verified."],
    path: Annotated[str, "JSONPath into response_json, e.g. $.body.myPage.hasScaleResult."],
    operator: Annotated[str, "One of: eq, ne, contains, exists, not_exists, not_empty, gt, lt, all_gte, all_lte, count_eq, eq_path."] = "eq",
    expected: Annotated[
        str,
        "Expected value as a string (JSON literals honored). For eq_path, a second JSONPath to compare against instead.",
    ] = "",
) -> Annotated[str, "Minified JSON: assertion (PASSED/FAILED/ERROR), path, operator, actual, expected."]:
    if operator not in _OPERATORS:
        result = {"assertion": "ERROR", "path": path, "operator": operator, "reason": f"unknown operator {operator!r}, expected one of {list(_OPERATORS)}"}
        return _record(result)

    try:
        doc = json.loads(response_json)
    except (json.JSONDecodeError, TypeError) as e:
        hint = "（响应过大被截断时常见）改用 esm_call 的 assert_json 参数，在产生响应的那次调用里就地断言，就不必把整份响应再传一遍" if len(response_json or "") > 2000 else ""
        result = {"assertion": "ERROR", "path": path, "operator": operator, "reason": f"response_json is not valid JSON: {e}{hint}"}
        return _record(result)

    # 把运算符写进期望值是模型最顽固的一个习惯：给出精确提示之后，实测它仍会连着重试
    # 五次同样的写法（TC-ESM30-PF-07），然后退化成一个揉坏的字典。诊断信息是给人看的，
    # 模型需要的是"这个错犯不出来"的形状。这里在 operator 还是默认值 eq、而 expected
    # 恰好是个运算符名时直接改判，并在结果里留 note——不静默，台账里数得出来它发生了多少次。
    if operator == "eq":
        candidate = expected.strip().rstrip(":")
        if candidate in _OPERATORS and candidate != "eq":
            operator, expected = candidate, ""
            _autocorrected = f"expected 里写的是运算符名，已按 operator={candidate} 执行"
        else:
            _autocorrected = ""
    else:
        _autocorrected = ""

    path = interpolate_value(path)
    if not isinstance(path, str):
        path = str(path)

    try:
        matches = find_json_path(doc, path)
    except Exception as e:
        result = {"assertion": "ERROR", "path": path, "operator": operator, "reason": f"bad JSONPath {path!r}: {e}"}
        return _record(result)

    result: dict = {"path": path, "operator": operator, "subject": assertion_subject(response_json)}
    if _autocorrected:
        result["note"] = _autocorrected

    if operator == "exists":
        passed = len(matches) > 0
        result["actual"] = f"{len(matches)} match(es)"
    elif operator == "not_exists":
        passed = len(matches) == 0
        result["actual"] = f"{len(matches)} match(es)"
    elif operator == "not_empty":
        # 「非空」写成 `ne ""` 有两个洞：JSON null 不等于空串会过，未替换的 ${占位符}
        # 也不等于空串、照样过。这个运算符把三件事一起要求：路径命中、值不是 null、
        # 去掉首尾空白后不是空串。
        if not matches:
            result.update({"assertion": "FAILED", "reason": "path matched nothing" + (lambda h: f"; {h}" if h else "")(_captured_variable_hint(path) or _available_keys(doc, path))})
            return _record(result)
        actual = matches[0]
        result["actual"] = actual
        passed = actual is not None and (not isinstance(actual, str) or actual.strip() != "")
        if passed and _unresolved_placeholder(actual):
            result.update({"assertion": "FAILED", "reason": f"值是未替换的占位符 {actual!r}，说明上一步没有捕获到这个变量"})
            return _record(result)
    elif operator in ("all_gte", "all_lte"):
        # gt / lt 只看 matches[0]。`$..score` 这种打到 16 个值的路径，用 gt 断言等于只查了
        # 第一维，其余 15 维越界照样通过——缺陷 7103182112 那一批里的「16 维分值超出
        # [-3,3]」正是这个形状：首维在区间内，越界的在后面。这两个运算符要求每个命中值
        # 都满足，失败时报出第一个越界的下标和值，而不是笼统说一句不通过。
        if not matches:
            hint = _captured_variable_hint(path) or _available_keys(doc, path)
            result.update({"assertion": "FAILED", "reason": "path matched nothing" + (f"; {hint}" if hint else ""), "expected": _resolve_expected(expected)})
            return _record(result)
        # 路径命中唯一一个数组时，作者要约束的是它的元素，不是数组本身——和 count_eq 同一口径。
        values = matches[0] if len(matches) == 1 and isinstance(matches[0], list) else matches
        if not values:
            # 空集上「每个值都满足」在逻辑上恒真，但在断言语境里它意味着「本该有一批值，
            # 结果一个都没有」——量表结果页没数据正是这个形状，而这两个运算符就是为
            # 16 维分值加的。判绿等于把最该报的那种缺陷放过去。
            # 注意同一份数据写成 `$.body.scores[*]` 时命中零次，走的是上面 `not matches`
            # 分支判 FAILED；两种写法必须给同一个结论。
            result.update({"assertion": "FAILED", "reason": "路径命中的是一个空数组：没有任何值可以检查，这通常说明本该有数据的地方是空的", "expected": _resolve_expected(expected), "actual": []})
            return _record(result)
        want = _resolve_expected(expected)
        result["expected"] = want
        result["checked"] = len(values)
        offender = None
        for index, value in enumerate(values):
            try:
                ok = value >= want if operator == "all_gte" else value <= want
            except TypeError as e:
                result.update({"assertion": "ERROR", "reason": f"cannot compare {type(value).__name__} {value!r} at index {index} with {type(want).__name__} {want!r}: {e}"})
                return _record(result)
            if not ok:
                offender = (index, value)
                break
        passed = offender is None
        if passed:
            result["actual"] = f"all {len(values)} value(s) within bound"
        else:
            result["actual"] = offender[1]
            result["first_violation_index"] = offender[0]
    elif operator == "count_eq":
        want = _resolve_expected(expected)
        # `$.body.items` 命中的是数组本身（1 个），不是它的元素。作者写 count_eq 时想数的
        # 几乎总是元素个数，于是空数组会被报成"实际 1 条"。命中唯一一个数组时改数它的长度，
        # 并在结果里说明数的是哪一种；真想数路径命中次数的写 `$.body.items[*]`。
        if len(matches) == 1 and isinstance(matches[0], list):
            count = len(matches[0])
            result["counted"] = "array elements"
        else:
            count = len(matches)
            result["counted"] = "path matches"
        passed = count == want
        result["actual"] = count
        result["expected"] = want
    elif not matches:
        # A path matching nothing is a failure, not a silent pass, for every operator
        # except the two that explicitly test for absence above.
        hint = _captured_variable_hint(path) or _available_keys(doc, path)
        result.update({"assertion": "FAILED", "reason": "path matched nothing" + (f"; {hint}" if hint else ""), "expected": _resolve_expected(expected)})
        return _record(result)
    else:
        want_preview = _resolve_expected(expected) if operator in ("eq", "ne") else None
        if isinstance(want_preview, list) and len(matches) > 1:
            # `$..choiceValue` 这类路径会命中多个值，而用例想断言的是"依次是 1 到 6"这
            # 一整个序列。只取 matches[0] 的话，拿一个标量去比一个数组，永远判否——
            # 期望值本身是数组时，就该拿整串匹配结果去比。
            actual = matches
        else:
            actual = matches[0]
        result["actual"] = actual
        if operator == "eq_path":
            # eq_path 的 expected 是第二条 JSONPath，不是值——插值可以，但不能当 JSON 字面量解析。
            expected = interpolate_value(expected)
            if not isinstance(expected, str):
                expected = str(expected)
            try:
                other = find_json_path(doc, expected)
            except Exception as e:
                result.update({"assertion": "ERROR", "reason": f"bad comparison JSONPath {expected!r}: {e}"})
                return _record(result)
            if not other:
                hint = _captured_variable_hint(expected) or _available_keys(doc, expected)
                result.update({"assertion": "FAILED", "reason": f"comparison path {expected!r} matched nothing" + (f"; {hint}" if hint else "")})
                return _record(result)
            result["expected"] = other[0]
            result["expected_from"] = expected
            passed = actual == other[0]
        else:
            prefix, sep, rest = expected.partition(":")
            # 两种错法都是把运算符写进了期望值：
            #   operator="eq", expected="not_empty"                     整个期望值就是个运算符名
            #   operator="eq", expected="ne_path:$.captured.x"          运算符前缀 + 一条路径
            # 前者尤其毒：拿真实值和字符串 "not_empty" 比，稳定判否，看着像被测方返回了错的值，
            # 实际接口完全正常（TC-ESM30-AS-14、TC-ESM30-PF-07 都是这么红的）。
            # 实际观测到的三种形态（都出自 assertion_logs.log，不是设想出来的）：
            #   expected="not_empty"                  运算符名，无冒号
            #   expected="not_empty:"                 运算符名 + 尾随冒号
            #   expected="ne_path:$.captured.x"       自造前缀 + 一条路径
            # 判据统一成「冒号前是运算符名，且冒号后为空或是一条路径」，外加自造前缀带路径的情况。
            prefix_is_operator = prefix.strip() in _OPERATORS
            rest_is_path = rest.strip().startswith("$")
            if (prefix_is_operator and (not rest.strip() or rest_is_path)) or (rest_is_path and " " not in prefix):
                # 观测到的实际错法（TC-ESM30-CH-22）：operator="eq" 配
                # expected="ne_path:$.captured.assistantMessageId"——把运算符塞进期望值，
                # 后面再跟一条路径。当字面量比会稳定判否，看着像被测方不符预期，其实是写法错了。
                # 不限定前缀必须是真运算符：模型发明的 "ne_path" 本来就不在表里，
                # 认的是"冒号后面跟了个 JSONPath"这个形态。
                result.update(
                    {
                        "assertion": "ERROR",
                        "reason": (
                            f"expected={expected!r} 是个运算符名，不是期望值。运算符只由 operator 参数指定："
                            f"想断言非空就传 operator=not_empty（expected 留空），想和响应里另一个字段比用 operator=eq_path，"
                            "想和上一步捕获的值比用 expected=${name}"
                        ),
                    }
                )
                return _record(result)
            want = _resolve_expected(expected)
            if _unresolved_placeholder(want):
                result.update(
                    {
                        "assertion": "ERROR",
                        "reason": f"expected={want!r} 里的变量没有被捕获过，拿它当字面量比是假的：ne 永远通过、eq 永远失败。先确认上一步的 extract_json 真的捕获成功",
                    }
                )
                return _record(result)
            result["expected"] = want
            if operator == "ne" and want == "":
                result["note"] = "ne '' 只排除空串：null 和未替换的 ${占位符} 都会通过。想断言「非空」用 operator=not_empty"
            if operator == "eq":
                passed = actual == want
                if not passed and _loosely_equal(actual, want):
                    passed = True
                    result["note"] = f"matched as text: actual is {type(actual).__name__}, expected literal parsed as {type(want).__name__}"
            elif operator == "ne":
                # 严格不等还不够：actual="0"、want=0 严格不等但文本相等，那不该判成"不同"。
                passed = actual != want and not _loosely_equal(actual, want)
            elif operator == "contains":
                passed = str(want) in str(actual)
            elif operator in ("gt", "lt"):
                if len(matches) > 1:
                    result["note"] = f"path matched {len(matches)} values; gt/lt only compares the first — use all_gte/all_lte to bound every value"
                try:
                    passed = actual > want if operator == "gt" else actual < want
                except TypeError as e:
                    result.update({"assertion": "ERROR", "reason": f"cannot compare {type(actual).__name__} {actual!r} with {type(want).__name__} {want!r}: {e}"})
                    return _record(result)

    result["assertion"] = "PASSED" if passed else "FAILED"
    return _record(result)
