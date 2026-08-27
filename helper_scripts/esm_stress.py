#!/usr/bin/env python3
"""ESM V3.0 答题与对话接口的并发压测：吞吐 + 并发正确性。

不走 Hercules。那套是 LLM 驱动的，单条用例约 250 秒、几十万 token，既压不出并发，
也压不起成本；而且模型延迟会混进测量结果，分不清慢在哪。这里直接打 HTTP。

    ./helper_scripts/esm_stress.py --concurrency 10
    ESM_LANE=feat-esm-v3-0 ./helper_scripts/esm_stress.py -c 50 --skip-report

测两类目标，分开报：

  吞吐   N 个用户并发各答 144 题（用户内部串行——跳答会被判题号非法，proto 里也没有
         批量提交的 RPC），看单题延迟分位数、吞吐和整套完成时间。
  正确性 同 session 并发写、同会话并发发送、同 clientRequestId 竞态、跨用户隔离。
         这四项比吞吐数字更值：吞吐在共享泳道上受邻居影响，正确性不受。

已知会在压力下出现的业务返回一律当作被测结果记录，不当脚本异常：
  BIZ_ESM_GATEWAY_DEGRADED / BIZ_ESM_*_STORAGE_UNAVAILABLE
  BIZ_ESM_CHAT_TURN_IN_PROGRESS / BIZ_ESM_CHAT_SLOT_LIMIT_REACHED

═══════════════ 基线：2026-08-27 feat-esm-v3-0 泳道实测 ═══════════════

答题接口 —— 到 50 并发都没有拐点：

    并发   吞吐(题/秒)    p50     p90     p99     max   整套144题墙钟
      2          4.4   0.39s   0.60s   0.83s   1.75s        65.1s
     10         20.8   0.39s   0.66s   1.04s   1.21s        69.3s
     30         60.7   0.39s   0.63s   1.04s   1.57s        71.2s
     50         97.2   0.42s   0.67s   1.05s   1.64s        74.1s

并发 ×25 而 p50 只涨 8%，吞吐 ×22（理想线性 ×25，损耗 12%）。50 个人同时答题，
每人的整套体感只慢 14%。那 8% 的 p50 抬升太小，分不清是服务端排队还是本机协程调度，
真正的拐点要往 100/200 并发去找。

注意 2 并发那组的 max(1.75s) 高于 10 并发(1.21s)——样本量只有 288，撞上一次长尾就
把 max 拉高了。低并发档位的 max 不要当基线用，看 p50/p90。

报告生成 —— 这里才是先到顶的一环，因为背后是真实模型：

    并发    墙钟     p50     p90     max
      2   116.1s   116s      —    116s
     10   122.5s   110s    122s   122s
     30   181.8s   106s    133s   178s
     50   181.4s   127s    165s   181s

30 和 50 的墙钟几乎相同而 p90 从 133s 涨到 165s：不是串行（串行的话 50 份要 5000s），
也不是无限并行（那样墙钟该稳定在 ~120s），是**有限并行 + 排队**，后半批在等前半批。
墙钟两次撞在 181s，像是碰到了某个并发窗口上限。

结论：答题接口的容量远超当前需求，别在那儿做优化；要提容量得看 Agent 侧的报告生成并发配置。
`esm_api.esm_wait_report` 的 300 秒超时对 50 并发下 max 181s 是够的；生产并发若超过 50 要重算。

正确性 —— 四个并发档位下结果逐字相同，总计 15781 次请求、12 次非零返回（全是设计上
该拒的 TURN_IN_PROGRESS）、零脏写、零串号、零降级：

    同 session 并发写   6 并发提交同一题 → 全成功，最终已答 1 条，answerVersion 恒为 [1]
    同会话并发发送      4 并发 → 1 成功 + 3 条 BIZ_ESM_CHAT_TURN_IN_PROGRESS
    幂等竞态            同 clientRequestId 4 并发 → 4 成功但只产生 1 个 messageId
    跨用户隔离          手机号/userId 唯一，各自读到自己的 resultId

顺带确认一件契约事实：`ErrEsmAssessmentSubmitAnswerConcurrentWrite` 到 50 并发都没被
触发过——服务端在这条路径上用幂等而不是拒绝来处理并发写，那个错误码目前走不到。

═══════════════════════════════════════════════════════════════════

跑一轮的代价：N 个用户 + N 份量表 + N 份报告留在测试环境。50 并发一轮就是 7200 条答案。
共享泳道上跑之前先跟用同一条泳道的人打个招呼，否则你测到的 p99 里混着邻居的负载。
"""

import argparse
import asyncio
import os
import statistics
import time
from collections import Counter, defaultdict
from typing import Any, Dict, List, Optional, Tuple

import httpx

BASE = os.getenv("ESM_BASE_URL", "https://zmapi-test.daqian369.com").rstrip("/")
LANE = os.getenv("ESM_LANE", "feat-esm-v3-0")
PID = os.getenv("ESM_PRODUCT_ID", "esm_miniprogram")
TOTAL_QUESTIONS = 144
AVATAR = "https://daqian-bullet-demo.tos-cn-beijing.volces.com/tests/player_01_19233_selected.jpg"

# 每次请求的观测记录：(阶段, 接口, 耗时秒, statusCode, bizCodeName)
SAMPLES: List[Tuple[str, str, float, str, str]] = []
_client: Optional[httpx.AsyncClient] = None
_concurrency = 10
_seq = 0


def client() -> httpx.AsyncClient:
    """连接池必须够并发用。

    httpx 默认 keepalive 上限 10，压测时会退化成反复建连，把客户端 TCP+TLS 握手混进
    服务端延迟——esm_api 的量表一度慢 168 秒而不是 60 秒，就是这么来的。
    """
    global _client
    if _client is None:
        _client = httpx.AsyncClient(
            timeout=90.0,
            limits=httpx.Limits(max_connections=_concurrency * 3, max_keepalive_connections=_concurrency * 2, keepalive_expiry=180.0),
        )
    return _client


def headers(token: Optional[str]) -> Dict[str, str]:
    h = {"Content-Type": "application/json", "Accept": "application/json", "X-Product-ID": PID, "lane": LANE, "x-lane": LANE}
    if token:
        h["Authorization"] = f"Bearer {token}"
    return h


def request_id(prefix: str) -> str:
    global _seq
    _seq += 1
    return f"stress-{prefix}-{_seq}"


async def call(phase: str, method: str, path: str, *, token: Optional[str] = None, body: Optional[dict] = None, query: Optional[dict] = None) -> Dict[str, Any]:
    """发一次请求并记账。业务失败也照常返回——网关对业务失败一律回 HTTP 200，成败看 statusCode。"""
    base_req = {"productId": PID, "requestId": request_id(phase)}
    params = None
    payload = None
    if method == "GET":
        params = dict(query or {})
        params.setdefault("baseReq.productId", PID)
        params.setdefault("baseReq.requestId", base_req["requestId"])
    else:
        payload = dict(body or {})
        payload.setdefault("baseReq", base_req)

    started = time.perf_counter()
    try:
        response = await client().request(method, BASE + path, headers=headers(token), params=params, json=payload)
        elapsed = time.perf_counter() - started
        data = response.json()
    except Exception as exc:  # 传输层失败也是压测结果的一部分，不能让它中断整轮
        elapsed = time.perf_counter() - started
        SAMPLES.append((phase, path, elapsed, "TRANSPORT_ERROR", type(exc).__name__))
        return {"statusCode": "TRANSPORT_ERROR", "bizCodeName": type(exc).__name__, "body": {}}

    resp = data.get("baseResp") or {}
    err = resp.get("error") or {}
    code = str(resp.get("statusCode"))
    SAMPLES.append((phase, path, elapsed, code, err.get("bizCodeName") or ""))
    return {"statusCode": code, "bizCodeName": err.get("bizCodeName") or "", "statusMsg": resp.get("statusMsg"), "body": {k: v for k, v in data.items() if k != "baseResp"}}


async def provision(idx: int, run_tag: str) -> Dict[str, Any]:
    """建一个用户并填资料。

    手机号按 worker 编号确定性分配，不能用时间戳后缀。esm_api._register_user 用的是
    time.time_ns() 的后 8 位——串行没问题，并发注册必撞；撞了是登录到别人的账号，
    数据全乱，而症状看起来像"接口返回了错的数据"，是最难查的一类。
    """
    phone = f"139{run_tag}{idx:02d}"
    response = await client().post(
        f"{BASE}/api/auth/register-by-phone",
        headers=headers(None),
        json={"phone": phone, "nickname": f"压测{idx:02d}", "baseReq": {"productId": PID, "requestId": request_id("reg")}},
    )
    data = response.json()
    if (data.get("baseResp") or {}).get("statusCode") != "0":
        raise RuntimeError(f"worker {idx} 注册失败 phone={phone}: {data.get('baseResp')}")

    token = data["accessToken"]["accessToken"]
    out = await call(
        "provision",
        "POST",
        "/api/esm/v1/user/info",
        token=token,
        body={
            "userInfo": {
                "nickname": f"压测{idx:02d}",
                "gender": "GENDER_MALE" if idx % 2 else "GENDER_FEMALE",
                "birthDate": f"199{idx % 10}-0{idx % 9 + 1}-1{idx % 9}",
                "avatarRef": AVATAR,
                "avatarSource": "AVATAR_SOURCE_WECHAT",
            }
        },
    )
    if out["statusCode"] != "0":
        raise RuntimeError(f"worker {idx} 填资料失败: {out}")
    return {"idx": idx, "user_id": data["user"]["userId"], "token": token, "phone": phone}


async def answer_all(user: Dict[str, Any]) -> Dict[str, Any]:
    """一个用户答完 144 题。用户内部必须串行：跳答会被判题号非法，也没有批量提交的 RPC。"""
    token = user["token"]
    start = await call("assess", "POST", "/api/esm/v1/answering/start-or-resume", token=token, body={})
    if start["statusCode"] != "0":
        return {**user, "ok": False, "stage": "start", "detail": start}
    session = start["body"]["session"]
    session_id, question = session["sessionId"], int(session["currentQuestionNo"])

    errors: List[str] = []
    while question <= TOTAL_QUESTIONS:
        out = await call("assess", "POST", "/api/esm/v1/answering/answer", token=token, body={"sessionId": session_id, "questionNo": question, "choiceValue": (question % 6) + 1})
        if out["statusCode"] != "0":
            errors.append(f"Q{question}:{out['bizCodeName'] or out['statusCode']}")
            if len(errors) >= 3:
                return {**user, "ok": False, "stage": f"Q{question}", "errors": errors}
            continue
        if out["body"].get("completed"):
            return {**user, "ok": True, "session_id": session_id, "result_id": out["body"].get("resultId", ""), "errors": errors}
        question = int(out["body"]["session"]["currentQuestionNo"])
    return {**user, "ok": False, "stage": "never-completed", "errors": errors}


# ─────────────────────────── 并发正确性 ───────────────────────────


async def check_concurrent_same_answer(user: Dict[str, Any], fanout: int = 6) -> Dict[str, Any]:
    """同一 session、同一题，多个请求同时提交同样的答案。

    契约里专门留了 ErrEsmAssessmentSubmitAnswerConcurrentWrite，说明服务端预期会遇到这种
    情况。要验的不是"扛不扛得住"，而是"扛不住时是否干净地拒绝"——绝不能出现 answerVersion
    被重复递增、或者已答数大于 1 的脏写。
    """
    token = user["token"]
    start = await call("correctness", "POST", "/api/esm/v1/answering/start-or-resume", token=token, body={})
    session_id = start["body"]["session"]["sessionId"]
    body = {"sessionId": session_id, "questionNo": 1, "choiceValue": 3}
    outs = await asyncio.gather(*[call("correctness", "POST", "/api/esm/v1/answering/answer", token=token, body=dict(body)) for _ in range(fanout)])

    after = await call("correctness", "POST", "/api/esm/v1/answering/start-or-resume", token=token, body={})
    session = after["body"]["session"]
    saved = after["body"].get("savedAnswers") or []
    versions = {o["body"].get("savedAnswer", {}).get("answerVersion") for o in outs if o["statusCode"] == "0"}
    return {
        "fanout": fanout,
        "成功数": sum(1 for o in outs if o["statusCode"] == "0"),
        "错误码": dict(Counter(o["bizCodeName"] or o["statusCode"] for o in outs if o["statusCode"] != "0")),
        "answerVersion 取值": sorted(v for v in versions if v is not None),
        "最终 answeredCount": session.get("answeredCount"),
        "最终 savedAnswers": len(saved),
        "判定": "干净" if session.get("answeredCount") == 1 and len(saved) == 1 else "脏写",
    }


async def _new_self_chat(user: Dict[str, Any]) -> Optional[str]:
    out = await call(
        "correctness",
        "POST",
        "/api/esm/v1/chat/create",
        token=user["token"],
        body={"chatKind": "CHAT_KIND_SELF", "sourceType": "CHAT_SOURCE_TYPE_HOME_SELF_ENTRY", "resultId": user["result_id"], "createNewChat": True},
    )
    return out["body"]["chat"]["chatId"] if out["statusCode"] == "0" else None


async def check_concurrent_chat_send(user: Dict[str, Any], fanout: int = 4) -> Dict[str, Any]:
    """同一会话并发发送不同内容。契约规定只应有一条被接受，其余拿 TURN_IN_PROGRESS。"""
    chat_id = await _new_self_chat(user)
    if not chat_id:
        return {"判定": "跳过：建会话失败"}
    outs = await asyncio.gather(
        *[
            call(
                "correctness",
                "POST",
                "/api/esm/v1/chat/message/send",
                token=user["token"],
                body={"chatId": chat_id, "content": f"并发第 {i} 条：我在关系里最在意什么？", "inputType": "CHAT_INPUT_TYPE_TEXT", "clientRequestId": f"stress-diff-{user['idx']}-{i}"},
            )
            for i in range(fanout)
        ]
    )
    read = await call("correctness", "GET", "/api/esm/v1/chat/messages-after-seq", token=user["token"], query={"chatId": chat_id, "afterSeqId": "0"})
    users = [m for m in (read["body"].get("messages") or []) if m.get("role") == "CHAT_MESSAGE_ROLE_USER"]
    accepted = sum(1 for o in outs if o["statusCode"] == "0")
    return {
        "fanout": fanout,
        "成功数": accepted,
        "错误码": dict(Counter(o["bizCodeName"] or o["statusCode"] for o in outs if o["statusCode"] != "0")),
        "落库用户消息": len(users),
        "判定": "符合契约（只接受一条）" if accepted == 1 and len(users) == 1 else f"异常：接受 {accepted} 条、落库 {len(users)} 条",
    }


async def check_idempotent_race(user: Dict[str, Any], fanout: int = 4) -> Dict[str, Any]:
    """同一 clientRequestId、同样内容并发发送。契约要求按幂等处理，应收敛到同一条消息。"""
    chat_id = await _new_self_chat(user)
    if not chat_id:
        return {"判定": "跳过：建会话失败"}
    body = {"chatId": chat_id, "content": "幂等竞态：同一个 clientRequestId 并发重发。", "inputType": "CHAT_INPUT_TYPE_TEXT", "clientRequestId": f"stress-idem-{user['idx']}"}
    outs = await asyncio.gather(*[call("correctness", "POST", "/api/esm/v1/chat/message/send", token=user["token"], body=dict(body)) for _ in range(fanout)])
    ids = {o["body"].get("userMessage", {}).get("messageId") for o in outs if o["statusCode"] == "0"}
    ids.discard(None)
    read = await call("correctness", "GET", "/api/esm/v1/chat/messages-after-seq", token=user["token"], query={"chatId": chat_id, "afterSeqId": "0"})
    users = [m for m in (read["body"].get("messages") or []) if m.get("role") == "CHAT_MESSAGE_ROLE_USER"]
    return {
        "fanout": fanout,
        "成功数": sum(1 for o in outs if o["statusCode"] == "0"),
        "错误码": dict(Counter(o["bizCodeName"] or o["statusCode"] for o in outs if o["statusCode"] != "0")),
        "不同 messageId": len(ids),
        "落库用户消息": len(users),
        "判定": "幂等成立" if len(ids) <= 1 and len(users) == 1 else f"异常：产生 {len(ids)} 个消息 id、落库 {len(users)} 条",
    }


async def check_isolation(users: List[Dict[str, Any]]) -> Dict[str, Any]:
    """并发答题之后，每个用户读到的结果必须是自己的。手机号撞号或会话串了，这里会暴露。"""
    outs = await asyncio.gather(*[call("correctness", "GET", "/api/esm/v1/result/latest", token=u["token"]) for u in users])
    mismatched = []
    for user, out in zip(users, outs):
        got = (out["body"].get("result") or {}).get("resultId", "")
        if out["statusCode"] != "0" or got != user.get("result_id"):
            mismatched.append({"worker": user["idx"], "期望": user.get("result_id", "")[:36], "实际": got[:36], "code": out["statusCode"]})
    uids = [u["user_id"] for u in users]
    phones = [u["phone"] for u in users]
    return {
        "手机号唯一": len(set(phones)) == len(phones),
        "userId 唯一": len(set(uids)) == len(uids),
        "归属不符": mismatched,
        "判定": "隔离正确" if not mismatched and len(set(uids)) == len(uids) else "存在串号",
    }


async def wait_report(user: Dict[str, Any], report_type: str, timeout: float) -> Dict[str, Any]:
    deadline = time.time() + timeout
    started = time.time()
    last: Dict[str, Any] = {}
    while time.time() < deadline:
        last = await call("report", "GET", "/api/esm/v1/report", token=user["token"], query={"reportType": report_type, "subjectUserId": user["user_id"]})
        if last["statusCode"] == "0" and (last["body"].get("report") or {}).get("status") == "REPORT_STATUS_READY":
            return {"worker": user["idx"], "found": True, "waited": round(time.time() - started, 1)}
        await asyncio.sleep(5)
    return {"worker": user["idx"], "found": False, "waited": round(time.time() - started, 1), "last": last.get("bizCodeName")}


# ─────────────────────────── 报表 ───────────────────────────


def percentile(values: List[float], fraction: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    return ordered[min(len(ordered) - 1, int(len(ordered) * fraction))]


def latency_table(title: str, phase: str) -> None:
    by_path: Dict[str, List[float]] = defaultdict(list)
    for sample_phase, path, elapsed, _code, _biz in SAMPLES:
        if sample_phase == phase:
            by_path[path].append(elapsed)
    if not by_path:
        return
    print(f"\n── {title} ──")
    print(f"{'接口':46}{'次数':>6}{'p50':>8}{'p90':>8}{'p95':>8}{'p99':>8}{'max':>8}")
    for path, values in sorted(by_path.items(), key=lambda kv: -len(kv[1])):
        print(f"{path:46}{len(values):6}{statistics.median(values):7.2f}s{percentile(values, .90):7.2f}s{percentile(values, .95):7.2f}s{percentile(values, .99):7.2f}s{max(values):7.2f}s")


def error_table() -> None:
    errors = Counter(biz or code for _p, _path, _e, code, biz in SAMPLES if code != "0")
    print(f"\n── 错误码分布（总请求 {len(SAMPLES)}）──")
    if not errors:
        print("   无非零返回")
        return
    for name, count in errors.most_common():
        print(f"   {name:46} {count:5}  {count / len(SAMPLES) * 100:5.2f}%")


async def run(concurrency: int, skip_report: bool, report_timeout: float) -> int:
    run_tag = str(int(time.time()))[-6:]
    print(f"ESM 并发压测   并发 {concurrency}   泳道 {LANE}   产品 {PID}")
    print(f"手机号段 139{run_tag}XX（按 worker 确定性分配，避免并发撞号）\n")

    started = time.perf_counter()
    users = await asyncio.gather(*[provision(i, run_tag) for i in range(concurrency)])
    print(f"[1/5] 建号+填资料  {concurrency} 个   {time.perf_counter() - started:.1f}s")

    phase_started = time.perf_counter()
    done = await asyncio.gather(*[answer_all(u) for u in users])
    span = time.perf_counter() - phase_started
    ok = [d for d in done if d.get("ok")]
    print(f"[2/5] 并发答题     {len(ok)}/{concurrency} 完成   墙钟 {span:.1f}s   吞吐 {len(ok) * TOTAL_QUESTIONS / span:.1f} 题/秒")
    for item in done:
        if not item.get("ok"):
            print(f"       ✗ worker {item['idx']} 停在 {item.get('stage')}  {item.get('errors')}")

    print("\n[3/5] 并发正确性")
    isolation = await check_isolation(ok)
    print(f"   跨用户隔离      {isolation['判定']}   手机号唯一={isolation['手机号唯一']}  userId 唯一={isolation['userId 唯一']}")
    for item in isolation["归属不符"]:
        print(f"       ✗ {item}")
    findings = [isolation["判定"] == "隔离正确"]
    if ok:
        for label, result in (
            ("同 session 并发写", await check_concurrent_same_answer(ok[0])),
            ("同会话并发发送  ", await check_concurrent_chat_send(ok[min(1, len(ok) - 1)])),
            ("幂等竞态        ", await check_idempotent_race(ok[min(2, len(ok) - 1)])),
        ):
            detail = f"成功 {result['成功数']}/{result['fanout']}  错误 {result['错误码']}" if "成功数" in result else ""
            print(f"   {label} {result['判定']}   {detail}")
            findings.append(result["判定"] in ("干净", "符合契约（只接受一条）", "幂等成立"))

    if not skip_report and ok:
        print(f"\n[4/5] 报告生成（{len(ok)} 份并发排队）")
        phase_started = time.perf_counter()
        reports = await asyncio.gather(*[wait_report(u, "personal_detail_v1", report_timeout) for u in ok])
        waited = [r["waited"] for r in reports if r["found"]]
        print(f"   {len(waited)}/{len(ok)} 就绪   墙钟 {time.perf_counter() - phase_started:.1f}s")
        if waited:
            print(f"   等待耗时  min {min(waited):.0f}s  p50 {statistics.median(waited):.0f}s  p90 {percentile(waited, .9):.0f}s  max {max(waited):.0f}s")
        for report in reports:
            if not report["found"]:
                print(f"       ✗ worker {report['worker']} 等了 {report['waited']}s 仍未就绪  {report.get('last')}")
    else:
        print("\n[4/5] 报告生成  已跳过（--skip-report）")

    print("\n[5/5] 汇总")
    latency_table("答题阶段延迟", "assess")
    latency_table("建号阶段延迟", "provision")
    error_table()
    print(f"\n总墙钟 {time.perf_counter() - started:.1f}s   总请求 {len(SAMPLES)}")
    await client().aclose()

    # 正确性任一项不成立就以非零退出，方便挂进流水线；吞吐只报数字不做判定，
    # 因为共享泳道上的吞吐会被邻居的负载影响，拿它当门禁会得到一条随机红。
    return 0 if all(findings) else 1


def main() -> int:
    global _concurrency
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("-c", "--concurrency", type=int, default=int(os.getenv("STRESS_CONCURRENCY", "10")))
    parser.add_argument("--skip-report", action="store_true", help="跳过报告生成阶段：只想量答题吞吐时用，能省几分钟并少给 Agent 侧压力")
    parser.add_argument("--report-timeout", type=float, default=600.0)
    args = parser.parse_args()
    _concurrency = args.concurrency
    return asyncio.run(run(args.concurrency, args.skip_report, args.report_timeout))


if __name__ == "__main__":
    raise SystemExit(main())
