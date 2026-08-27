"""ESM V3.0 小程序网关接口测试工具。

契约真源：
  - idl/esm/v1/esm_gateway.proto      HTTP 方法与路径
  - idl/esm/v1/esm_assessment.proto   答题、量表结果
  - idl/esm/v1/esm_profile.proto      资料、报告、分享、候选关系
  - idl/esm/v1/esm_chat.proto         对话、消息、关系对话
  - idl/error_code_esm.go             业务错误码

对外 JSON 走 grpc-gateway 默认 protojson：字段是 lowerCamelCase，int64 序列化成
字符串。失败不体现在 HTTP 状态码上——网关一律返回 200，成败看 `baseResp.statusCode`
（"0" 为成功）和 `baseResp.error.bizCodeName`。

`esm_call` 是通用调用入口，用例里的每一次接口断言都走它。`esm_new_user` 和
`esm_wait_*` 是前置条件工具：它们把「注册一个做完 144 题的用户」「等 Agent 把报告
写回」这种一步几十上百次请求的准备动作收进一次工具调用，否则 planner 光铺前置就会
耗尽 nav 轮次。
"""

import asyncio
import difflib
import json
import os
import re
import time
import uuid
from typing import Annotated, Any, Dict, Optional, Tuple

import httpx

# 复用框架自己那套跨步骤变量，而不是在这里另开一份：_var_store 按 scenario 隔离，
# 和 extract_from_response 共用同一个命名空间，所以用例里两种取值方式可以混用，
# 不会出现"同一个名字在两套存储里各有一份"。
from testzeus_hercules.core.tools.api_calls import _var_store, interpolate_value
from testzeus_hercules.core.tools.tool_registry import api_logger as file_logger
from testzeus_hercules.core.tools.tool_registry import tool
from testzeus_hercules.utils.jsonpath_helper import find_json_path
from testzeus_hercules.utils.logger import logger

DEFAULT_BASE_URL = "https://zmapi-test.daqian369.com"
DEFAULT_LANE = "feat-esm-v3-0"
# 小程序自己的产品标识。idl 的 ProductIDESMMiniprogram、四个 esm-* 服务的 defaultProductID、
# 网关中间件读 x-product-id 的单测，用的都是这个值；dev 仓的 ESM E2E 更是直接拒绝跑在别的
# 产品下。量表结果的手机号绑定按 (product_id, phone_hash) 建索引，打 narnia 等于在一个真实
# 小程序永远不会用的命名空间里造数据，测出来的东西不代表线上。
DEFAULT_PRODUCT_ID = "esm_miniprogram"

TOTAL_QUESTIONS = 144
AVATAR_REF = "https://daqian-bullet-demo.tos-cn-beijing.volces.com/tests/player_01_19233_selected.jpg"

# 答案必须按题号顺序提交（跳答会被判成题号非法，proto 里也没有批量提交的 RPC），
# 所以 144 题只能连答，没有并发的余地。
#
# 超时按实测定：432 次 SubmitAnswer 的 max 是 2.63 秒、p95 1.76 秒，超过 5 秒的零次。
# 原先的 30 秒有 11 倍余量，唯一的作用是把一次真正的挂起拖到 30 秒才报错。10 秒对
# 实测最大值仍有近 4 倍余量。
ANSWER_TIMEOUT_SECONDS = 10.0
REQUEST_TIMEOUT_SECONDS = 60.0

# 政治安全接的是外部审核系统，会稳定命中的语料只能由安全/风控团队提供、且不该进 git——
# 写死一句真会触发外部审核的文本，等于把一份"已知能骗过审核系统"的样本发布到仓库里。
# 这两个变量在跑之前由 ops 从审核团队要来的样例注入，跑之前不设就直接报错，不悄悄跳过。
_RISK_SAMPLE_ENV = {
    "political_input": "ESM_RISK_SAMPLE_POLITICAL_INPUT",
    "political_output": "ESM_RISK_SAMPLE_POLITICAL_OUTPUT",
}

# 关系类工作流现在会拿到双方的年龄和性别（idl EsmChatTurnV1Input 的 my_/peer_age_years、
# my_/peer_gender，EsmRelationBriefV1Input 的 owner_/target_*，EsmReportBundleV1Input.age_years）。
# 所有标签共用一套人口学属性的话，「对方的年龄性别有没有正确传进去、有没有和自己的搞反」
# 这类问题在用例里永远看不出来——两边一样，传错了结果也一样。所以按标签给不同的默认值。
# 标签 A 保持原来的取值，已有用例的行为不变。
_LABEL_DEMOGRAPHICS: Dict[str, Tuple[str, str]] = {
    "A": ("GENDER_FEMALE", "1995-05-05"),
    "B": ("GENDER_MALE", "1988-11-20"),
    "C": ("GENDER_FEMALE", "2001-03-08"),
}
_FALLBACK_DEMOGRAPHICS = ("GENDER_MALE", "1992-07-16")

# label -> {"user_id": str, "token": str, "phone": str, "result_id": str,
#           "gender": str, "birth_date": str}
_SESSIONS: Dict[str, Dict[str, str]] = {}

# 每次请求新建 AsyncClient 等于每题重做一次 TCP+TLS 握手。实测：一套量表 144 题，
# 每次新建连接 168 秒，复用同一个连接 60 秒——那 108 秒全是握手，服务端真实处理只有
# 0.39 秒/题。全量跑要做 59 次量表，这一项就是 1.77 小时。
#
# 按事件循环缓存：httpx 的连接池绑定创建它的 loop，Hercules 一个 scenario 一个 loop，
# 跨 loop 复用会在第二个 scenario 里炸。key 用 id(loop) 而不是 loop 本身，避免持着
# 已经关掉的 loop 不放。
_CLIENTS: Dict[int, httpx.AsyncClient] = {}

# idl/esm/v1/esm_gateway.proto 里 google.api.http 声明的全部 37 条路径。
#
# 存在的理由：模型偶尔会把路径写短一截（把 /answering/answer 写成 /answer），网关回
# 404，报告里就出现一条「服务端 Not Found」——一条本该指向用例的红被伪装成了服务端故障，
# 而这是最费时间去伪的一类误报。打不中就当场报错，比把 404 带回去强。
#
# 新增网关接口时这份名单要跟着 proto 一起更新，否则新接口的用例会被这里拦下。
_GATEWAY_PATHS = frozenset(
    {
        "/api/esm/v1/answering/answer",
        "/api/esm/v1/answering/restart",
        "/api/esm/v1/answering/start-or-resume",
        "/api/esm/v1/auth/logout",
        "/api/esm/v1/auth/token/refresh",
        "/api/esm/v1/auth/wechat-phone/login",
        "/api/esm/v1/auth/wechat/login",
        "/api/esm/v1/chat/create",
        "/api/esm/v1/chat/delete",
        "/api/esm/v1/chat/detail",
        "/api/esm/v1/chat/generation/stop",
        "/api/esm/v1/chat/message/edit-resend",
        "/api/esm/v1/chat/message/feedback",
        "/api/esm/v1/chat/message/regenerate",
        "/api/esm/v1/chat/message/send",
        "/api/esm/v1/chat/messages-after-seq",
        "/api/esm/v1/chat/recommended-question-card",
        "/api/esm/v1/chat/recommended-question/use",
        "/api/esm/v1/chat/rename",
        "/api/esm/v1/chats",
        "/api/esm/v1/home/state",
        "/api/esm/v1/relationship-chat/start",
        "/api/esm/v1/relationships/candidates",
        "/api/esm/v1/relationships/conversations",
        "/api/esm/v1/relationships/fixed-tags",
        "/api/esm/v1/relationships/setting",
        "/api/esm/v1/relationships/share/bind",
        "/api/esm/v1/relationships/share/create",
        "/api/esm/v1/relationships/type-options",
        "/api/esm/v1/report",
        "/api/esm/v1/report/content/feedback",
        "/api/esm/v1/report/viewed",
        "/api/esm/v1/result/latest",
        "/api/esm/v1/runtime/state",
        "/api/esm/v1/user/info",
        "/api/esm/v1/user/me",
        "/api/esm/v1/user/unregister",
    }
)


def _check_gateway_path(path: str) -> None:
    """路径不在契约里就直接抛，并给出最接近的那条。

    提示的排序不能只靠 difflib：典型的错法是把路径写短一截（/answering/answer 写成
    /answer），而 difflib 按整体相似度算，短字符串会把 /user/me 这种无关路径排到正确答案
    前面。所以先挑「把写错的那条当子串包含进去」的候选，再拿 difflib 兜底。
    """
    clean = (path or "").split("?", 1)[0].rstrip("/") or "/"
    if clean in _GATEWAY_PATHS:
        return
    tail = clean.rsplit("/", 1)[-1]
    contains = sorted(p for p in _GATEWAY_PATHS if tail and (f"/{tail}" in p or p.startswith(clean)))
    fuzzy = [p for p in difflib.get_close_matches(clean, sorted(_GATEWAY_PATHS), n=3, cutoff=0.5) if p not in contains]
    close = (contains + fuzzy)[:3]
    hint = "\n最接近的是：\n  " + "\n  ".join(close) if close else ""
    raise ValueError(f"{clean!r} 不是 esm_gateway.proto 里声明的网关路径。照用例里写的路径原样调用，不要自己拼。{hint}")


def _client() -> httpx.AsyncClient:
    loop = asyncio.get_running_loop()
    key = id(loop)
    client = _CLIENTS.get(key)
    if client is None or client.is_closed:
        client = httpx.AsyncClient(
            timeout=REQUEST_TIMEOUT_SECONDS,
            limits=httpx.Limits(max_keepalive_connections=8, keepalive_expiry=120.0),
        )
        _CLIENTS[key] = client
    return client


def _base_url() -> str:
    return os.getenv("ESM_BASE_URL", DEFAULT_BASE_URL).rstrip("/")


def _lane() -> str:
    return os.getenv("ESM_LANE", DEFAULT_LANE).strip()


def _product_id() -> str:
    return os.getenv("ESM_PRODUCT_ID", DEFAULT_PRODUCT_ID).strip()


def _request_id(prefix: str) -> str:
    return f"{prefix}-{uuid.uuid4().hex[:12]}"


def _headers(token: Optional[str]) -> Dict[str, str]:
    headers = {"Content-Type": "application/json", "Accept": "application/json"}
    product_id = _product_id()
    if product_id:
        headers["X-Product-ID"] = product_id
    lane = _lane()
    if lane:
        # 网关读 lane，istio 读 x-lane；两个都带才能保证请求落到泳道实例。
        headers["lane"] = lane
        headers["x-lane"] = lane
    if token:
        headers["Authorization"] = f"Bearer {token}"
    return headers


def _session(label: str) -> Dict[str, str]:
    key = (label or "").strip()
    if not key:
        return {}
    known = _SESSIONS.get(key)
    if known is None:
        raise ValueError(f"未知的用户标签 {key!r}；先用 esm_new_user 建这个用户。已有标签: {sorted(_SESSIONS) or '(空)'}")
    return known


def _publish_session_vars() -> None:
    """把每个已建用户的 userId / resultId / phone 发布成 ${A_userId} 这样的变量。

    这些值本来就在 _SESSIONS 里，用例没有理由为了引用它们再跑一次接口、再抄一遍。
    每次调用前重新发布一遍，是因为 esm_finish_assessment 会在建完用户之后回填
    result_id——只在建用户时发布一次的话，那之后拿到的会是空串。

    用下划线而不是点号，是因为框架的占位符名字只认 [A-Za-z_][A-Za-z0-9_]*
    （api_calls._VAR_PATTERN）：写成 ${A.userId} 压根匹配不上，会原样发到服务端。
    标签里的其它字符同样按这条规则替换掉。
    """
    store = _var_store()
    for label, session in _SESSIONS.items():
        safe = re.sub(r"\W", "_", label)
        if not safe or safe[0].isdigit():
            safe = f"_{safe}"
        store[f"{safe}_userId"] = session.get("user_id", "")
        store[f"{safe}_resultId"] = session.get("result_id", "")
        store[f"{safe}_phone"] = session.get("phone", "")
        store[f"{safe}_gender"] = session.get("gender", "")
        store[f"{safe}_birthDate"] = session.get("birth_date", "")


def _capture(summary: Dict[str, Any], extract_json: Any) -> Dict[str, str]:
    """按 {"名字": "JSONPath"} 把响应里的值捕获成 ${名字}。

    捕获和产生这个值的那次调用是同一次工具调用，而不是之后再补一次
    extract_from_response——ESM 用例里这种取值有上百处，每处多一轮就是上百轮，
    nav 轮次预算撑不住。
    """
    spec = _parse_json_arg(extract_json, "extract_json")
    if not spec:
        return {}
    store = _var_store()
    captured: Dict[str, str] = {}
    for name, path in spec.items():
        matches = find_json_path(summary, str(path))
        if not matches:
            raise ValueError(f"extract_json 里 {name!r} 的路径 {path!r} 在响应里匹配不到任何值。响应的顶层字段: {sorted(summary)}")
        store[name] = matches[0]
        captured[name] = matches[0]
    return captured


async def _assert_inline(summary: Dict[str, Any], assert_json: Any) -> list:
    """在产生响应的这一次调用里就地断言，不要求把响应再当参数传回来。

    `assert_json_path` 需要调用方把整份响应 JSON 当字符串参数重新提交一遍。144 题的
    start-or-resume 响应传不进去，模型只能"看着说符合"，然后诚实地把用例判成失败
    （TC-ESM30-AS-01 就是这么红的）。响应本来就在这个函数手里，让它自己断言即可。

    写法 `{"<JSONPath>": "<operator>:<expected>"}`，例如
    `{"$.body.questions": "count_eq:144", "$.statusCode": "eq:0"}`。
    只写运算符（`{"$.body.session": "exists"}`）表示不需要期望值；
    整条不带运算符前缀时按 eq 处理。

    断言实现直接复用 assert_json_path，避免同一套判定在两个地方各写一遍、然后慢慢跑偏。
    """
    spec = _parse_json_arg(assert_json, "assert_json")
    if not spec:
        return []

    from testzeus_hercules.core.tools.api_assertions import assert_json_path

    payload = json.dumps(summary, ensure_ascii=False)
    results = []
    for path, raw in spec.items():
        text = str(raw)
        operator, _, expected = text.partition(":")
        if operator not in ("eq", "ne", "contains", "exists", "not_exists", "gt", "lt", "count_eq", "eq_path"):
            operator, expected = "eq", text
        outcome = json.loads(await assert_json_path(response_json=payload, path=path, operator=operator, expected=expected))
        results.append(outcome)
    return results


def _parse_json_arg(raw: Any, field: str) -> Dict[str, Any]:
    """把工具参数里的 JSON 解析成 dict，空值当成没传。"""
    if raw is None or raw == "":
        return {}
    if isinstance(raw, dict):
        return dict(raw)
    if isinstance(raw, str):
        try:
            parsed = json.loads(raw)
        except json.JSONDecodeError as exc:
            raise ValueError(f"{field} 不是合法 JSON: {exc}") from exc
        if not isinstance(parsed, dict):
            raise ValueError(f"{field} 必须是 JSON 对象，实际是 {type(parsed).__name__}")
        return parsed
    raise ValueError(f"{field} 必须是 JSON 对象或 JSON 字符串，实际是 {type(raw).__name__}")


def _flatten_query(payload: Dict[str, Any], prefix: str = "") -> Dict[str, str]:
    """grpc-gateway 的 GET 参数用点号表达嵌套字段，例如 baseReq.productId。"""
    flat: Dict[str, str] = {}
    for key, value in payload.items():
        path = f"{prefix}{key}"
        if isinstance(value, dict):
            flat.update(_flatten_query(value, prefix=f"{path}."))
        elif isinstance(value, bool):
            flat[path] = "true" if value else "false"
        elif value is not None:
            flat[path] = str(value)
    return flat


def _summarize(response: httpx.Response) -> Dict[str, Any]:
    """把网关响应压成用例断言真正要看的那几项。"""
    try:
        payload = response.json()
    except ValueError:
        return {
            "httpStatus": response.status_code,
            "statusCode": None,
            "bizCodeName": None,
            "statusMsg": None,
            "rawBody": response.text[:2000],
        }
    base_resp = payload.get("baseResp") or {}
    error = base_resp.get("error") or {}
    body = {key: value for key, value in payload.items() if key != "baseResp"}
    return {
        "httpStatus": response.status_code,
        "statusCode": base_resp.get("statusCode"),
        "success": base_resp.get("success"),
        "bizCodeName": error.get("bizCodeName"),
        "canonicalCode": error.get("canonicalCode"),
        "statusMsg": base_resp.get("statusMsg"),
        "errorSource": error.get("source"),
        # 服务端处理这次请求时的时间，用于给用例判断"某个时间戳是不是刚刚才写入的"
        # 提供一个不依赖 harness 本地时钟的参照点——两者都来自同一台服务器。
        "serverTimeMs": base_resp.get("serverTimeMs"),
        "body": body,
    }


async def _send(
    method: str,
    path: str,
    *,
    token: Optional[str] = None,
    body: Optional[Dict[str, Any]] = None,
    query: Optional[Dict[str, Any]] = None,
    request_id_prefix: str = "esm",
    timeout: float = REQUEST_TIMEOUT_SECONDS,
) -> Dict[str, Any]:
    """发一次网关请求，并把 baseReq 补全。"""
    method = method.upper()
    base_req = {"productId": _product_id(), "requestId": _request_id(request_id_prefix)}
    _check_gateway_path(path)
    url = f"{_base_url()}{path}"

    request_query: Dict[str, str] = {}
    json_body: Optional[Dict[str, Any]] = None
    if method == "GET":
        merged = dict(query or {})
        merged.setdefault("baseReq", {})
        if isinstance(merged["baseReq"], dict):
            for key, value in base_req.items():
                merged["baseReq"].setdefault(key, value)
        request_query = _flatten_query(merged)
    else:
        json_body = dict(body or {})
        existing = json_body.get("baseReq")
        json_body["baseReq"] = {**base_req, **existing} if isinstance(existing, dict) else base_req
        request_query = _flatten_query(query or {})

    response = await _client().request(
        method,
        url,
        headers=_headers(token),
        params=request_query or None,
        json=json_body,
        timeout=timeout,
    )

    summary = _summarize(response)
    file_logger(
        json.dumps(
            {
                "esm_call": {
                    "method": method,
                    "url": str(response.request.url),
                    "requestBody": json_body,
                    "httpStatus": summary["httpStatus"],
                    "statusCode": summary.get("statusCode"),
                    "bizCodeName": summary.get("bizCodeName"),
                    "statusMsg": summary.get("statusMsg"),
                }
            },
            ensure_ascii=False,
        )
    )
    return summary


async def _register_user(nickname: str, phone_prefix: str = "139") -> Tuple[str, str, str]:
    """用 infra-auth 的手机号注册开一个全新账号，返回 (user_id, token, phone)。

    小程序真实链路走微信两段 code，自动化拿不到；测试环境的手机号注册接口签发的是
    同一套 token，ESM 网关只解析 token 里的 uid，所以对网关侧契约是等价的。
    """
    phone = f"{phone_prefix}{str(time.time_ns())[-8:]}"
    response = await _client().post(
        f"{_base_url()}/api/auth/register-by-phone",
        headers=_headers(None),
        json={
            "phone": phone,
            "nickname": nickname,
            "baseReq": {"productId": _product_id(), "requestId": _request_id("esm-register")},
        },
    )
    payload = response.json()
    status_code = (payload.get("baseResp") or {}).get("statusCode")
    if status_code != "0":
        raise RuntimeError(f"注册用户失败 phone={phone} baseResp={payload.get('baseResp')}")
    return payload["user"]["userId"], payload["accessToken"]["accessToken"], phone


async def _complete_assessment(token: str) -> Tuple[str, int]:
    """把当前 session 剩下的题答完，返回 (resultId, 起始题号)。"""
    start = await _send(
        "POST",
        "/api/esm/v1/answering/start-or-resume",
        token=token,
        body={},
        request_id_prefix="esm-fixture-start",
    )
    if start.get("statusCode") != "0":
        raise RuntimeError(f"StartOrResumeAnswering 失败: {start}")
    session_id = start["body"]["session"]["sessionId"]
    answered_from = int(start["body"]["session"].get("currentQuestionNo") or 1)

    for question_no in range(answered_from, TOTAL_QUESTIONS + 1):
        answer = await _send(
            "POST",
            "/api/esm/v1/answering/answer",
            token=token,
            body={
                "sessionId": session_id,
                "questionNo": question_no,
                "choiceValue": (question_no % 6) + 1,
            },
            request_id_prefix="esm-fixture-answer",
            timeout=ANSWER_TIMEOUT_SECONDS,
        )
        if answer.get("statusCode") != "0":
            raise RuntimeError(f"SubmitAnswer 第 {question_no} 题失败: {answer}")
        if answer["body"].get("completed"):
            return answer["body"].get("resultId") or "", answered_from
    raise RuntimeError(f"提交完 {TOTAL_QUESTIONS} 题后 completed 仍不为 true")


@tool(
    agent_names=["api_nav_agent"],
    name="esm_new_user",
    description=(
        "Create a brand-new ESM test user as a scenario precondition and cache it under a short label "
        "(for example 'A' or 'B') for later esm_call invocations. Always registers a fresh phone account. "
        "Then, depending on the flags, submits the required profile and answers all 144 assessment "
        "questions so the user owns a scale result.\n"
        "The three reachable states map to the three home entry stages:\n"
        "  - with_profile=false: raw account, entry stage HOME_ENTRY_STAGE_COMPLETE_USER_INFO.\n"
        "  - with_profile=true, with_scale_result=false: profile filled in, entry stage "
        "HOME_ENTRY_STAGE_ANSWERING.\n"
        "  - with_scale_result=true: assessment finished, entry stage HOME_ENTRY_STAGE_HOME.\n"
        "Answering all 144 questions takes roughly three minutes, so only pass with_scale_result=true when "
        "the scenario genuinely needs a scale result (relationship share, candidate relationship, report or "
        "relationship chat). Calling this again with the same label replaces that label with a new user.\n"
        "Gender and birth date default per label (A, B and C each get a different pair) so that a "
        "relationship scenario has two genuinely different people on the two sides -- the chat and report "
        "workflows receive both sides' age and gender, and identical fixtures would hide a swapped or "
        "dropped value. Pass gender/birth_date explicitly only when the scenario asserts a specific value."
    ),
)
async def esm_new_user(
    label: Annotated[str, "Short label to store this user under, for example 'A' or 'B'. Later esm_call invocations reference the user by this label."],
    nickname: Annotated[str, "Nickname submitted through UpdateUserInfo. Use plain text that passes content moderation."] = "接口用例用户",
    with_profile: Annotated[bool, "True submits the required profile through UpdateUserInfo. False leaves the account at the raw registered state, before profile completion."] = True,
    with_scale_result: Annotated[bool, "True answers all 144 assessment questions so the user owns a scale result. Takes about three minutes. Implies with_profile."] = False,
    gender: Annotated[str, "Gender enum submitted through UpdateUserInfo, for example GENDER_MALE. Leave empty to use this label's default, which differs per label on purpose."] = "",
    birth_date: Annotated[str, "Birth date submitted through UpdateUserInfo, YYYY-MM-DD. Leave empty to use this label's default, which differs per label on purpose."] = "",
) -> Annotated[str, "Minified JSON: label, userId, phone, gender, birthDate, userInfoCompleted, resultId, elapsedSeconds."]:
    label = (label or "").strip()
    if not label:
        raise ValueError("label 不能为空")

    default_gender, default_birth_date = _LABEL_DEMOGRAPHICS.get(label.upper(), _FALLBACK_DEMOGRAPHICS)
    gender = (gender or "").strip() or default_gender
    birth_date = (birth_date or "").strip() or default_birth_date

    started = time.time()
    user_id, token, phone = await _register_user(nickname)
    user_info_completed = False

    if with_profile or with_scale_result:
        profile = await _send(
            "POST",
            "/api/esm/v1/user/info",
            token=token,
            body={
                "userInfo": {
                    "nickname": nickname,
                    "gender": gender,
                    "birthDate": birth_date,
                    "avatarRef": AVATAR_REF,
                    "avatarSource": "AVATAR_SOURCE_WECHAT",
                }
            },
            request_id_prefix="esm-fixture-userinfo",
        )
        if profile.get("statusCode") != "0":
            raise RuntimeError(f"UpdateUserInfo 失败: {profile}")
        user_info_completed = profile["body"]["user"]["userInfoCompleted"]

    result_id = ""
    if with_scale_result:
        result_id, _ = await _complete_assessment(token)

    _SESSIONS[label] = {
        "user_id": user_id,
        "token": token,
        "phone": phone,
        "result_id": result_id,
        "gender": gender if user_info_completed else "",
        "birth_date": birth_date if user_info_completed else "",
    }
    logger.info("esm_new_user label=%s user_id=%s with_scale_result=%s", label, user_id, with_scale_result)
    return json.dumps(
        {
            "label": label,
            "userId": user_id,
            "phone": phone,
            "gender": gender if user_info_completed else "",
            "birthDate": birth_date if user_info_completed else "",
            "userInfoCompleted": user_info_completed,
            "resultId": result_id,
            "elapsedSeconds": round(time.time() - started, 1),
        },
        ensure_ascii=False,
    )


@tool(
    agent_names=["api_nav_agent"],
    name="esm_finish_assessment",
    description=(
        "Answer the labelled user's remaining assessment questions, from wherever the session currently "
        "stands through question 144, and return the resulting resultId. Use this in end-to-end flow "
        "scenarios that walk the real chain: start the session and submit the first few answers with "
        "esm_call so the contract stays visible in the case, then call this to grind through the rest. "
        "Answers must be submitted strictly in order and each one is a round trip, so a full run takes "
        "roughly three minutes. Fails if the user has no in-progress session."
    ),
)
async def esm_finish_assessment(
    label: Annotated[str, "User label whose in-progress answering session should be carried to completion."],
) -> Annotated[str, "Minified JSON: resultId, answeredFrom, elapsedSeconds."]:
    session = _session(label)
    started = time.time()

    result_id, answered_from = await _complete_assessment(session["token"])
    session["result_id"] = result_id
    return json.dumps(
        {"resultId": result_id, "answeredFrom": answered_from, "elapsedSeconds": round(time.time() - started, 1)},
        ensure_ascii=False,
    )


@tool(
    agent_names=["api_nav_agent"],
    name="esm_risk_sample",
    description=(
        "Fetch a chat message that reliably trips the real external content-moderation system, for risk-control "
        "scenarios. Political-safety hits depend on the real audit service's live behavior, so there is no way to "
        "encode a message that reliably triggers it without depending on that service's current rules -- the "
        "sample has to come from whoever owns the audit configuration, supplied out of band as an environment "
        "variable rather than written into the feature file. Fails with a clear message naming the missing "
        "environment variable when it has not been provided; this is intentional, not a bug to route around.\n"
        "kind is 'political_input' for a message the FIRST audit pass (before generation) should block, or "
        "'political_output' for a message that passes input audit but induces a reply the SECOND audit pass "
        "(after generation) should block and revoke. Psychological-safety cases do not need this tool -- that "
        "check is prompt-only, not routed through the external audit system, and the PRD's own self-harm-ideation "
        "example is safe to write directly into a scenario."
    ),
)
async def esm_risk_sample(
    kind: Annotated[str, "'political_input' or 'political_output'."],
    extract_json: Annotated[str, 'JSON object string mapping variable name to JSONPath into this result, e.g. {"sample": "$.content"}.'] = "",
) -> Annotated[str, "Minified JSON: kind, content, captured."]:
    env_name = _RISK_SAMPLE_ENV.get(kind)
    if env_name is None:
        raise ValueError(f"未知的 kind {kind!r}；可选: {sorted(_RISK_SAMPLE_ENV)}")
    sample = os.getenv(env_name, "").strip()
    if not sample:
        raise RuntimeError(f"{env_name} 未设置。这条用例需要外部审核系统真会命中的样例语料，不能写死在仓库里——找风控/安全团队要一条当前配置下确实会命中的样例，跑之前通过这个环境变量注入。")
    result = {"kind": kind, "content": sample}
    captured = _capture(result, extract_json)
    if captured:
        result["captured"] = captured
    return json.dumps(result, ensure_ascii=False)


@tool(
    agent_names=["api_nav_agent"],
    name="esm_call",
    description=(
        "Call one ESM miniapp gateway endpoint and return the parts a test assertion needs. Injects the "
        "Authorization bearer token of the labelled user, the lane and product headers, and baseReq "
        "(productId, requestId), so the caller only supplies the endpoint-specific fields.\n"
        "Parameters:\n"
        "  - label: user label created by esm_new_user. Pass an empty string to call without a token, "
        "which is how unauthenticated-access cases are written.\n"
        "  - method: GET or POST, exactly as the proto google.api.http annotation declares it.\n"
        "  - path: full gateway path, for example /api/esm/v1/home/state.\n"
        "  - body_json: JSON object string for POST bodies, in lowerCamelCase proto JSON. Enum fields take "
        'their full name string such as GENDER_MALE. int64 fields take strings such as "12345".\n'
        "  - query_json: JSON object string for GET query parameters; nested objects are flattened into "
        "dotted names automatically.\n"
        "  - extract_json: JSON object string mapping a variable name to a JSONPath into THIS call's "
        'response, for example {"chatId": "$.body.chat.chatId"}. Captured in the same call that produced '
        "the value, so no follow-up tool call is needed.\n"
        "NEVER retype an id from a previous response into a later call -- that is how a long random id "
        "gets garbled halfway through a scenario. Capture it with extract_json, then write ${chatId} "
        "wherever it is needed in a later path, body_json or query_json; substitution happens before the "
        "request is built. A field whose entire value is ${name} keeps the captured value's type.\n"
        "Every user created by esm_new_user also publishes ${LABEL_userId}, ${LABEL_resultId} and "
        "${LABEL_phone} automatically -- for label A that is ${A_userId} / ${A_resultId}, usable with no "
        "capture step at all. Note the underscore: variable names cannot contain a dot.\n"
        "assert_json makes the assertions right here, inside the call that produced the response, so a "
        "large payload never has to be handed back to a separate assertion tool. Write it as "
        '{"<JSONPath>": "<operator>:<expected>"} -- for example {"$.body.questions": "count_eq:144", '
        '"$.statusCode": "eq:0", "$.body.session": "exists"}. Operators are the assert_json_path ones '
        "(eq, ne, contains, exists, not_exists, gt, lt, count_eq, eq_path); count_eq counts the elements "
        "of the array the path lands on. Each entry comes back under assertions with PASSED/FAILED and the "
        "actual value. PREFER THIS over re-passing the response to assert_json_path -- for a response with "
        "144 questions, re-passing it is not possible at all.\n"
        "The returned JSON has httpStatus, statusCode, success, bizCodeName, canonicalCode, statusMsg, "
        "serverTimeMs, body, and captured when extract_json was used. The gateway answers HTTP 200 even for "
        'business failures, so judge success by statusCode == "0" and judge a specific failure by '
        "bizCodeName. serverTimeMs is when the server handled this call; use it instead of a local clock to "
        "check a timestamp field in the body was written just now -- both numbers come from the same server, "
        "so their difference is not skewed by clock drift between the harness and the backend."
    ),
)
async def esm_call(
    method: Annotated[str, "HTTP method: GET or POST."],
    path: Annotated[str, "Gateway path starting with /api/esm/v1/, for example /api/esm/v1/answering/answer. May contain ${name}."],
    label: Annotated[str, "User label created by esm_new_user. Empty string sends the request without an Authorization header."] = "",
    body_json: Annotated[str, "JSON object string for the POST body, in proto JSON lowerCamelCase. May contain ${name}. Leave empty for GET."] = "",
    query_json: Annotated[str, "JSON object string for GET query parameters, in proto JSON lowerCamelCase. May contain ${name}. Leave empty for POST."] = "",
    extract_json: Annotated[str, 'JSON object string mapping variable name to JSONPath into this response, e.g. {"chatId": "$.body.chat.chatId"}.'] = "",
    assert_json: Annotated[str, 'JSON object string mapping JSONPath to "operator:expected", e.g. {"$.body.questions": "count_eq:144", "$.statusCode": "eq:0"}.'] = "",
) -> Annotated[str, "Minified JSON: httpStatus, statusCode, success, bizCodeName, canonicalCode, statusMsg, serverTimeMs, body, captured, assertions."]:
    session = _session(label)
    _publish_session_vars()
    summary = await _send(
        method,
        interpolate_value(path),
        token=session.get("token"),
        body=interpolate_value(_parse_json_arg(body_json, "body_json")),
        query=interpolate_value(_parse_json_arg(query_json, "query_json")),
        request_id_prefix="esm-case",
    )
    captured = _capture(summary, extract_json)
    if captured:
        summary = {**summary, "captured": captured}
        # 跨步骤的 id 是后续断言的比较基准。不记下来的话，一条用例里"第二次返回的
        # sessionId 等于第一次捕获的"到底比的是什么值，事后无从查证。
        file_logger(json.dumps({"esm_capture": {"path": path, "captured": captured}}, ensure_ascii=False))
    assertions = await _assert_inline(summary, assert_json)
    if assertions:
        summary = {**summary, "assertions": assertions}
    return json.dumps(summary, ensure_ascii=False)


@tool(
    agent_names=["api_nav_agent"],
    name="esm_wait_report",
    description=(
        "Poll GetReport until the Agent has written back a report of the given type and it reaches "
        "REPORT_STATUS_READY. Report generation is asynchronous: the assessment completes, the server "
        "notifies the Agent, and the report lands seconds to a minute later, so a single GetReport right "
        "after answering legitimately returns BIZ_ESM_PROFILE_REPORT_NOT_FOUND. Use this whenever a "
        "scenario needs a readable report as a precondition; use esm_call with /api/esm/v1/report when the "
        "case is asserting the read contract itself. Known report types on this build: personal_detail_v1, "
        "personal_dialogue_v1, external_v1, relation_card_v1."
    ),
)
async def esm_wait_report(
    label: Annotated[str, "User label whose report is being waited for. The report subject is this same user."],
    report_type: Annotated[str, "Report type string written by the Agent, for example personal_detail_v1."] = "personal_detail_v1",
    # 2026-08-26 实测：一轮跑里 13 次等待，常态 85–120 秒，只有一次越过 180 秒撞上原来的超时。
    # 也就是说 180 秒对一个典型 100 秒的操作只有 1.8 倍余量，长尾必然漏出来变成假红。
    # 调到 300 秒是补余量，不是掩盖退化——分布集中在 120 秒以内，真退化会表现为整体右移，
    # 而不是单点越界；那种情况该登记缺陷，不该再调这个数。
    timeout_seconds: Annotated[float, "How long to keep polling before giving up."] = 300.0,
    extract_json: Annotated[str, 'JSON object string mapping variable name to JSONPath into this result, e.g. {"reportId": "$.reportId"}.'] = "",
) -> Annotated[str, "Minified JSON: reportId, status, reportType, reportVersion, contentLength, viewed, waitedSeconds, captured. found=false when it never became ready."]:
    session = _session(label)
    _publish_session_vars()
    deadline = time.time() + max(timeout_seconds, 1.0)
    started = time.time()
    last: Dict[str, Any] = {}

    while time.time() < deadline:
        last = await _send(
            "GET",
            "/api/esm/v1/report",
            token=session.get("token"),
            query={"reportType": report_type, "subjectUserId": session.get("user_id")},
            request_id_prefix="esm-wait-report",
        )
        report = (last.get("body") or {}).get("report") or {}
        if last.get("statusCode") == "0" and report.get("status") == "REPORT_STATUS_READY":
            result = {
                "found": True,
                "reportId": report.get("reportId"),
                "status": report.get("status"),
                "reportType": report.get("reportType"),
                "reportVersion": report.get("reportVersion"),
                "contentLength": len(report.get("reportContent") or ""),
                "viewed": report.get("viewed"),
                "waitedSeconds": round(time.time() - started, 1),
            }
            captured = _capture(result, extract_json)
            if captured:
                result["captured"] = captured
            return json.dumps(result, ensure_ascii=False)
        await asyncio.sleep(5)

    return json.dumps(
        {
            "found": False,
            "reportType": report_type,
            "waitedSeconds": round(time.time() - started, 1),
            "lastStatusCode": last.get("statusCode"),
            "lastBizCodeName": last.get("bizCodeName"),
        },
        ensure_ascii=False,
    )


@tool(
    agent_names=["api_nav_agent"],
    name="esm_wait_chat_reply",
    description=(
        "Poll GetMessagesAfterSeq until the assistant message for the current turn is committed. The chat "
        "reply is produced asynchronously through the workflow and pushed over websocket; the committed "
        "message store is the authoritative source, and this tool reads it the same way the client's "
        "compensation path does. Returns the messages committed after after_seq_id. Use it after "
        "SendChatMessage, UseRecommendedQuestion, edit-resend or regenerate whenever the assertion depends "
        "on the final assistant reply."
    ),
)
async def esm_wait_chat_reply(
    label: Annotated[str, "User label that owns the chat."],
    chat_id: Annotated[str, "Chat id returned by CreateChat or StartRelationshipChat. Accepts ${name} captured by esm_call."],
    after_seq_id: Annotated[str, 'Highest committed seq_id already seen, as a string. Pass "0" to read the whole chat.'] = "0",
    timeout_seconds: Annotated[float, "How long to keep polling before giving up."] = 120.0,
    extract_json: Annotated[str, 'JSON object string mapping variable name to JSONPath into this result, e.g. {"seq": "$.latestSeqId"}.'] = "",
) -> Annotated[str, "Minified JSON: settled, latestSeqId, turnStatus, messages (role, seqId, replySource, content preview), waitedSeconds, captured."]:
    session = _session(label)
    _publish_session_vars()
    chat_id = str(interpolate_value(chat_id))
    after_seq_id = str(interpolate_value(after_seq_id))
    deadline = time.time() + max(timeout_seconds, 1.0)
    started = time.time()
    last: Dict[str, Any] = {}

    while time.time() < deadline:
        last = await _send(
            "GET",
            "/api/esm/v1/chat/messages-after-seq",
            token=session.get("token"),
            query={"chatId": chat_id, "afterSeqId": after_seq_id or "0"},
            request_id_prefix="esm-wait-chat",
        )
        if last.get("statusCode") != "0":
            return json.dumps({"settled": False, "statusCode": last.get("statusCode"), "bizCodeName": last.get("bizCodeName")}, ensure_ascii=False)

        messages = (last.get("body") or {}).get("messages") or []
        assistant = [m for m in messages if m.get("role") == "CHAT_MESSAGE_ROLE_ASSISTANT"]
        if assistant:
            detail = await _send(
                "GET",
                "/api/esm/v1/chat/detail",
                token=session.get("token"),
                query={"chatId": chat_id},
                request_id_prefix="esm-wait-chat-detail",
            )
            turn_status = ((detail.get("body") or {}).get("chat") or {}).get("turnStatus")
            if turn_status == "CHAT_TURN_STATUS_IDLE":
                result = {
                    "settled": True,
                    "latestSeqId": (last.get("body") or {}).get("latestSeqId"),
                    "turnStatus": turn_status,
                    "messages": [
                        {
                            "messageId": m.get("messageId"),
                            "seqId": m.get("seqId"),
                            "role": m.get("role"),
                            "replySource": m.get("replySource"),
                            "contentLength": len(m.get("content") or ""),
                            "contentPreview": (m.get("content") or "")[:160],
                        }
                        for m in messages
                    ],
                    "waitedSeconds": round(time.time() - started, 1),
                }
                captured = _capture(result, extract_json)
                if captured:
                    result["captured"] = captured
                return json.dumps(result, ensure_ascii=False)
        await asyncio.sleep(3)

    return json.dumps(
        {
            "settled": False,
            "latestSeqId": (last.get("body") or {}).get("latestSeqId"),
            "waitedSeconds": round(time.time() - started, 1),
        },
        ensure_ascii=False,
    )
