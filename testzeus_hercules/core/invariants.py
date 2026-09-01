"""全局不变量：不依赖用例作者写断言，也能发现的问题。

出发点是一次缺陷复盘的结论：漏检的缺陷里有相当一部分，用例**已经走到了那个页面**，只是
没人想到要在那里断言什么。ESM 3.0 的 67 条缺陷里，「路径走过但没有判定」是 A 类 18 条的
主体。再多写用例也补不上这一类——补它的唯一办法是让某些检查在每一步之后自动发生。

证据其实一直在采：``playwright_manager`` 每一步都往 proofs 目录写 ``console_logs.json``
和 ``network_logs.json``，两份 JSONL。但全仓没有任何一处读它们——采集了不判定，等于把
线索存进了一个没人打开的抽屉。这个模块把它们变成判定源。

## 证据从哪来：proofs 目录里的 JSONL，谁都能写

判定源是**文件**，不是某个进程里的对象。这一条是有意的：小程序套件由一个独立的 Node
进程（``mcp_servers/wxmp/server.js``）驱动开发者工具，它和本进程之间没有函数调用可言，
但它拿得到同一个 proofs 目录（启动参数里就有 ``--proof-dir``）。约定成文件之后，
「谁来采」和「谁来判」彻底解耦：新增一个采集方只要往目录里追加 JSONL，本模块一行不改。

生产者要遵守的契约只有两条：

1. 追加写 JSONL，一行一个对象，写完整行再 flush。半行是允许的（读取方会留到下一轮），
   但不要回头改已经写过的行——读取方按字节偏移增量读。
2. 行的形状按 ``EVIDENCE_SOURCES`` 里登记的那种。目前两种：

   ``{"type": "console", "level": "error", "text": ..., "location": {"url": ...}}``
   ``{"type": "response", "status": 500, "url": ...}``

**分类只有这一处。** 采集方只管把原始事件按上面的形状吐出来，什么算判红、什么只留痕
一律由本模块决定。让每个采集方各自判一遍，两边迟早会漂移，而漂移的方向通常是各自变宽。

## 什么会判红，什么只记录

这是本模块唯一需要小心的设计。控制台报错和 4xx 在真实应用里非常常见（第三方脚本、
CSP、favicon、故意走的未授权分支），不加区分地判红会让每一次运行都是红的——而分不出
真假的红，和没有红是一回事，甚至更糟，因为它会训练人忽略红色。

所以分两档：

``BINDING``   高置信度、在本项目里从不属于预期结果的信号。它进断言台账，会让用例判红。
              - HTTP 5xx：网关对**业务失败也返回 200**（见 opt/cases/api 各 test_data
                的「判定口径」），所以 5xx 从来不是任何一条用例的预期结果。
              - 前端未捕获异常：``Uncaught`` / ``TypeError`` / ``Cannot read`` 这类签名。
                缺陷 7098254066（做完评测点「查看我的结果」跳白页，前端报错）就是这个
                形状——页面白了，用例的计数断言照过。

``NOTICE``    其余的控制台报错和 4xx。只进台账留痕、报告里看得见，不改红绿。它们里面
              有真问题，但也有大量正常噪音，现在没有可靠的办法分开；先让它们可见，
              等实际运行积累出模式再决定哪些可以升级成 BINDING。

宁可少判红也不要错判红：这套系统之前的教训是假绿危险，但一个永远在红的门禁同样会被
绕过，只是坏得更快。
"""

import json
import os
import re
from typing import Any, Dict, Iterable, List, NamedTuple

BINDING = "binding"
NOTICE = "notice"

# proofs 目录里会被读的证据文件，以及各自的解析函数。新增一个采集方就在这里加一行——
# 这是本模块唯一的扩展点。
#
# console_logs.json    playwright_manager 写（浏览器套件）
# mp_console_logs.json mcp_servers/wxmp/server.js 写（小程序套件），同一种行形状，
#                      单独一个文件是为了两个进程各写各的，不去争同一个文件句柄
# network_logs.json    playwright_manager 写
EVIDENCE_SOURCES: List[tuple] = []

# 未捕获异常的签名。取的是 JS 运行时抛出错误时的固定措辞，不是业务日志里可能出现的词，
# 所以不会被应用自己 console.error 出来的中文提示误伤。
_UNCAUGHT = re.compile(
    r"Uncaught|Unhandled\s+(?:promise\s+)?rejection|TypeError|ReferenceError|SyntaxError|" r"is not a function|is not defined|Cannot read (?:property|properties)",
    re.IGNORECASE,
)


class Violation(NamedTuple):
    severity: str  # BINDING | NOTICE
    kind: str
    detail: str

    def as_record(self) -> Dict[str, Any]:
        """写进 assertion_logs.log 的形状。

        BINDING 记成 FAILED，判定侧（junit_helper）因此会让用例变红；NOTICE 记成
        PASSED 加一条 notice，只留痕。两者都带 ``source: invariant``，让事后翻账时
        分得清哪些是用例写的断言、哪些是观察者自动加的。
        """
        record: Dict[str, Any] = {
            "source": "invariant",
            "mode": self.kind,
            # subject 参与判定侧的折叠键：同一条不变量在同一处反复触发只算一条，
            # 否则一个每步都报的控制台错误会把台账刷满，失败列表里看不到别的东西。
            "subject": self.detail[:120],
            "expected": "no violation",
            "actual": self.detail,
        }
        if self.severity == BINDING:
            record["assertion"] = "FAILED"
        else:
            record["assertion"] = "PASSED"
            record["note"] = "仅记录，不改变红绿"
        return record


def _read_jsonl(path: str, offset: int) -> tuple[List[dict], int]:
    """从 ``offset`` 字节处往后读 JSONL，返回新增条目和新的偏移量。

    按偏移量增量读，而不是每次整份重读：观察者在每一步之后都跑一次，整份重读会让同一条
    报错在后续每一步里被反复报出来，台账迅速被同一条噪音淹没。

    写入方 ``_write_log_entry_to_file`` 是 ``asyncio.ensure_future`` 异步追加的，所以
    这里读到的可能是半行。半行解析失败就跳过，偏移量只推进到最后一个完整行——下一步再读
    时它已经写完了。
    """
    try:
        size = os.path.getsize(path)
    except OSError:
        return [], offset
    if size < offset:
        # 文件比上次读到的位置还短，说明这已经不是同一份了（被清空或换掉）。这里必须
        # **立刻**从头读，不能只把偏移量清零然后返回空——那样会白白跳过一轮，而观察者
        # 每步只跑一次，被跳过那一轮里的违例就此漏掉。
        offset = 0
    elif size == offset:
        return [], offset
    try:
        with open(path, "r", encoding="utf-8", errors="replace") as handle:
            handle.seek(offset)
            chunk = handle.read()
    except OSError:
        return [], offset

    entries: List[dict] = []
    consumed = offset
    for line in chunk.splitlines(keepends=True):
        if not line.endswith("\n"):
            break  # 半行，留给下一次
        consumed += len(line.encode("utf-8"))
        text = line.strip()
        if not text:
            continue
        try:
            entry = json.loads(text)
        except ValueError:
            continue
        if isinstance(entry, dict):
            entries.append(entry)
        # 非 dict 的行（数组、裸标量）直接丢掉。后面的 entry.get() 假定是 dict，
        # 而 observer 抛出的任何异常都会掀翻整条 scenario——run() 只捕 BadRequestError。
        # 观察者是加进来提高发现率的，它自己绝不能成为用例失败的原因。
    return entries, consumed


def console_violations(entries: Iterable[dict]) -> List[Violation]:
    found: List[Violation] = []
    for entry in entries:
        if entry.get("type") != "console":
            continue
        if str(entry.get("level", "")).lower() not in ("error", "assert"):
            continue
        text = str(entry.get("text", "")).strip()
        if not text:
            continue
        location = entry.get("location") or {}
        where = str(location.get("url", ""))[-80:] if isinstance(location, dict) else ""
        detail = f"{text[:240]}" + (f"  @{where}" if where else "")
        if _UNCAUGHT.search(text):
            found.append(Violation(BINDING, "console_uncaught", detail))
        else:
            found.append(Violation(NOTICE, "console_error", detail))
    return found


def network_violations(entries: Iterable[dict]) -> List[Violation]:
    for_report: List[Violation] = []
    for entry in entries:
        if entry.get("type") != "response":
            continue
        try:
            status = int(entry.get("status") or 0)
        except (TypeError, ValueError):
            continue
        url = str(entry.get("url", ""))[:200]
        if status >= 500:
            # 网关对业务失败也返回 200，所以 5xx 从来不是预期结果。
            for_report.append(Violation(BINDING, "http_5xx", f"{status} {url}"))
        elif 400 <= status < 500:
            # 4xx 里混着故意走的未授权分支和真问题，现在分不开，先只留痕。
            for_report.append(Violation(NOTICE, "http_4xx", f"{status} {url}"))
    return for_report


EVIDENCE_SOURCES.extend(
    [
        ("console_logs.json", console_violations),
        ("mp_console_logs.json", console_violations),
        ("network_logs.json", network_violations),
    ]
)


class InvariantWatcher:
    """按 proofs 目录跟踪证据文件，每次 ``poll()`` 只返回上次之后新增的违例。

    偏移量存在实例上而不是 AgentState 里：它是「这个进程读到哪儿了」，属于采集侧的细节，
    放进图状态会跟着状态一起被序列化和比较，而它对图的决策没有任何意义。

    **一个 watcher 只服务一条 scenario。** `__main__` 每条 scenario 新建一个 runner、
    进而新建一个 SimpleHercules，watcher 跟着一起新建，proofs 目录也按 scenario 分开——
    所以 `_seen` 不清空是安全的。反过来说：如果哪天改成复用同一个 SimpleHercules 连跑
    多条 scenario，这里会静默吞掉第二条起的所有相同违例。别把它提成共享单例。
    """

    def __init__(self) -> None:
        self._offsets: Dict[str, int] = {}
        self._seen: set = set()

    def poll(self, proofs_path: str) -> List[Violation]:
        if not proofs_path:
            return []
        found: List[Violation] = []
        for name, extract in EVIDENCE_SOURCES:
            path = os.path.join(proofs_path, name)
            entries, offset = _read_jsonl(path, self._offsets.get(path, 0))
            self._offsets[path] = offset
            found.extend(extract(entries))

        # 同一条违例在一次运行里往往会反复出现（同一个报错每次渲染都抛一遍）。第一次就够了：
        # 台账要能让人一眼看出「出了几种问题」，而不是「出了几次」。
        fresh = []
        for violation in found:
            key = (violation.kind, violation.detail)
            if key in self._seen:
                continue
            self._seen.add(key)
            fresh.append(violation)
        return fresh
