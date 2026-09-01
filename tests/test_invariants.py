"""observer 节点用的全局不变量（testzeus_hercules/core/invariants.py）。

这个模块的全部风险都在「什么判红、什么只记录」这条线上。判太宽，每次运行都是红的，
而分不出真假的红会训练人忽略红色——比没有门禁坏得更快；判太窄，它就白加了。
所以每条规则的两侧都要有用例钉住。
"""

import json

from testzeus_hercules.core.invariants import (
    BINDING,
    NOTICE,
    InvariantWatcher,
    console_violations,
    network_violations,
)


def _console(level: str, text: str) -> dict:
    return {"type": "console", "level": level, "text": text, "location": {"url": "https://app/x.js"}}


def _response(status: int, url: str = "https://api/x") -> dict:
    return {"type": "response", "status": status, "url": url}


# ---------------------------------------------------------------- 控制台


def test_an_uncaught_exception_binds_the_verdict():
    """缺陷 7098254066 的形状：做完评测点「查看我的结果」跳白页、前端报错，
    而用例的计数断言照过。这类签名是 JS 运行时抛出来的，不是应用自己打的日志。"""
    for text in (
        "Uncaught TypeError: Cannot read properties of undefined (reading 'resultId')",
        "Unhandled promise rejection: ReferenceError: wx is not defined",
        "TypeError: e.map is not a function",
    ):
        found = console_violations([_console("error", text)])
        assert [v.severity for v in found] == [BINDING], text


def test_an_ordinary_console_error_only_leaves_a_note():
    """真实应用里 console.error 很常见：第三方脚本、埋点上报失败、业务提示。
    把它们一律判红，第一次运行就会全红，然后这个节点会被关掉。"""
    found = console_violations([_console("error", "上报失败，已进入重试队列"), _console("error", "[analytics] beacon blocked")])
    assert [v.severity for v in found] == [NOTICE, NOTICE]


def test_console_warnings_and_logs_are_ignored_entirely():
    assert console_violations([_console("warning", "deprecated API"), _console("log", "hello")]) == []


def test_an_empty_console_message_is_ignored():
    assert console_violations([_console("error", "   ")]) == []


# ---------------------------------------------------------------- 网络


def test_a_5xx_binds_the_verdict():
    """网关对业务失败也返回 HTTP 200（见 opt/cases/api 各 test_data 的「判定口径」），
    所以 5xx 从来不是任何一条用例的预期结果。"""
    found = network_violations([_response(500), _response(503)])
    assert [v.severity for v in found] == [BINDING, BINDING]


def test_a_4xx_only_leaves_a_note():
    """4xx 里混着故意走的未授权分支——TC-ESM30-GW-02「未登录读取首页状态返回未授权」
    就是一条要求 4xx 的用例。现在分不开真问题和预期分支，先只留痕。"""
    found = network_violations([_response(401), _response(404)])
    assert [v.severity for v in found] == [NOTICE, NOTICE]


def test_success_responses_produce_nothing():
    assert network_violations([_response(200), _response(302)]) == []


# ---------------------------------------------------------------- 台账记录形状


def test_a_binding_violation_is_recorded_as_a_failed_assertion():
    """判定侧（junit_helper）认的是台账里 assertion 为 FAILED 的记录。
    不写成 FAILED，这条不变量就只是一行日志，不改变任何结果。"""
    record = console_violations([_console("error", "Uncaught TypeError: x")])[0].as_record()
    assert record["assertion"] == "FAILED"
    assert record["source"] == "invariant"
    assert record["subject"]  # 参与折叠键，同一条反复触发只算一条


def test_a_notice_is_recorded_as_passed_so_it_cannot_turn_the_run_red():
    record = network_violations([_response(404)])[0].as_record()
    assert record["assertion"] == "PASSED"
    assert "不改变红绿" in record["note"]


# ---------------------------------------------------------------- 增量读取


def test_only_new_lines_are_reported_on_each_poll(tmp_path):
    """观察者每一步之后都跑一次。整份重读的话，同一条报错会在后续每一步里被反复报出来，
    台账迅速被同一条噪音淹没，失败列表里看不到别的东西。"""
    log = tmp_path / "console_logs.json"
    watcher = InvariantWatcher()

    log.write_text(json.dumps(_console("error", "Uncaught TypeError: first")) + "\n", encoding="utf-8")
    assert len(watcher.poll(str(tmp_path))) == 1
    assert watcher.poll(str(tmp_path)) == []

    with log.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(_console("error", "Uncaught TypeError: second")) + "\n")
    found = watcher.poll(str(tmp_path))
    assert [v.detail.startswith("Uncaught TypeError: second") for v in found] == [True]


def test_the_same_violation_twice_is_reported_once(tmp_path):
    """同一个报错每次渲染都会抛一遍。台账要能让人一眼看出「出了几种问题」，
    而不是「出了几次」。"""
    log = tmp_path / "console_logs.json"
    watcher = InvariantWatcher()
    line = json.dumps(_console("error", "Uncaught TypeError: same")) + "\n"
    log.write_text(line, encoding="utf-8")
    assert len(watcher.poll(str(tmp_path))) == 1
    with log.open("a", encoding="utf-8") as handle:
        handle.write(line)
    assert watcher.poll(str(tmp_path)) == []


def test_a_half_written_line_is_left_for_the_next_poll(tmp_path):
    """写入方是 asyncio.ensure_future 异步追加的，观察者随时可能读到半行。
    把半行当成损坏数据丢掉会永久漏掉那一条；偏移量只推进到最后一个完整行。"""
    log = tmp_path / "console_logs.json"
    watcher = InvariantWatcher()
    complete = json.dumps(_console("error", "Uncaught TypeError: whole")) + "\n"
    partial = json.dumps(_console("error", "Uncaught TypeError: partial"))[:40]
    log.write_text(complete + partial, encoding="utf-8")
    assert len(watcher.poll(str(tmp_path))) == 1

    with log.open("w", encoding="utf-8") as handle:
        handle.write(complete + json.dumps(_console("error", "Uncaught TypeError: partial")) + "\n")
    found = watcher.poll(str(tmp_path))
    assert len(found) == 1
    assert "partial" in found[0].detail


def test_a_missing_proofs_folder_is_not_an_error(tmp_path):
    """浏览器没起来的运行（mp 套件走 MCP，不驱动 playwright）根本不会有这两个文件。
    观察者在那种运行里应该安静地什么都不做，而不是把每一步都变成一次报错。"""
    assert InvariantWatcher().poll(str(tmp_path / "nope")) == []
    assert InvariantWatcher().poll("") == []


def test_a_truncated_file_restarts_from_the_beginning(tmp_path):
    """换 scenario 会换 proofs 目录。文件变短说明这已经不是上一次那份，
    继续按旧偏移量读会跳过开头。"""
    log = tmp_path / "network_logs.json"
    watcher = InvariantWatcher()
    log.write_text("\n".join(json.dumps(_response(500, f"https://api/{i}")) for i in range(5)) + "\n", encoding="utf-8")
    assert len(watcher.poll(str(tmp_path))) == 5

    log.write_text(json.dumps(_response(500, "https://api/fresh")) + "\n", encoding="utf-8")
    found = watcher.poll(str(tmp_path))
    assert [v.detail for v in found] == ["500 https://api/fresh"]


# ---------------------------------------------------------------- 接进图里


def test_the_observer_sits_between_executor_and_planner():
    """节点建了但没接边，是这类改动最容易出的错——测试全绿，运行时它一次都不跑。

    看的是编译出来的图对象，不是源码字符串：按源码匹配的话，格式化工具重排一次缩进
    就会红，而且它验的是「代码长什么样」不是「图连成什么样」。
    """
    from testzeus_hercules.core import simple_hercules as sh

    hercules = sh.SimpleHercules.__new__(sh.SimpleHercules)
    graph = sh.SimpleHercules._build_graph(hercules).get_graph()
    edges = {(edge.source, edge.target) for edge in graph.edges}

    assert "observer" in graph.nodes
    assert ("executor", "observer") in edges, "executor 之后没有走到 observer"
    assert ("executor", "planner") not in edges, "有一条绕过 observer 的旁路"
    assert {target for source, target in edges if source == "observer"} == {"planner", "assertion"}, "observer 是条死路"


def test_a_binding_violation_reaches_the_ledger_and_the_planner(tmp_path, monkeypatch):
    """观察者的产出必须走两条路：写台账（这才是判定），和推回 planner 的对话（这才是它
    下一轮看得见）。只做后者的话，它就只是给模型的一句提示，不改变任何红绿。"""
    import asyncio

    from langchain_core.messages import HumanMessage
    from testzeus_hercules.core import simple_hercules as sh

    (tmp_path / "network_logs.json").write_text(json.dumps(_response(500, "https://api/report")) + "\n", encoding="utf-8")

    written: list = []
    monkeypatch.setattr(sh, "assertion_logger", written.append)

    conf = sh.get_global_conf()
    monkeypatch.setattr(conf, "get_proof_path", lambda *a, **k: str(tmp_path), raising=False)

    hercules = sh.SimpleHercules.__new__(sh.SimpleHercules)
    hercules.stake_id = "t"
    hercules._invariants = InvariantWatcher()

    out = asyncio.run(hercules._observer_node({"messages": [HumanMessage(content="task")]}))

    records = [json.loads(line) for line in written]
    assert [r["assertion"] for r in records] == ["FAILED"]
    assert records[0]["source"] == "invariant"
    assert any("observer" in str(m.content) for m in out["messages"])


def test_notices_do_not_reach_the_planner(tmp_path, monkeypatch):
    """4xx 和普通控制台报错不改红绿，把它们塞进 planner 的对话只会挤占它推进用例的注意力。
    它们只留在断言台账里，报告能看见。"""
    import asyncio

    from testzeus_hercules.core import simple_hercules as sh

    (tmp_path / "network_logs.json").write_text(json.dumps(_response(404, "https://api/favicon")) + "\n", encoding="utf-8")

    written: list = []
    monkeypatch.setattr(sh, "assertion_logger", written.append)
    monkeypatch.setattr(sh.get_global_conf(), "get_proof_path", lambda *a, **k: str(tmp_path), raising=False)

    hercules = sh.SimpleHercules.__new__(sh.SimpleHercules)
    hercules.stake_id = "t"
    hercules._invariants = InvariantWatcher()
    out = asyncio.run(hercules._observer_node({"messages": []}))

    assert [json.loads(line)["assertion"] for line in written] == ["PASSED"]  # 留痕了
    assert out == {}  # 但没有推回对话，也不往图状态里塞只写不读的东西


def test_a_non_dict_line_does_not_take_down_the_scenario(tmp_path):
    """observer 抛出的任何异常都会掀翻整条 scenario（run() 只捕 BadRequestError）。
    它是为提高发现率加进来的，绝不能自己成为用例失败的原因。"""
    log = tmp_path / "console_logs.json"
    log.write_text('"裸字符串"\n[1,2,3]\n' + json.dumps(_console("error", "Uncaught TypeError: real")) + "\n", encoding="utf-8")
    found = InvariantWatcher().poll(str(tmp_path))
    assert [v.severity for v in found] == [BINDING]


def test_the_observer_node_swallows_collection_errors(tmp_path, monkeypatch):
    import asyncio

    from testzeus_hercules.core import simple_hercules as sh

    hercules = sh.SimpleHercules.__new__(sh.SimpleHercules)
    hercules.stake_id = "t"

    class Exploding:
        def poll(self, _path):
            raise RuntimeError("证据文件格式没见过")

    hercules._invariants = Exploding()
    assert asyncio.run(hercules._observer_node({"messages": []})) == {}


# ------------------------------------------------- 跨进程证据契约（小程序套件）


def test_the_mini_program_console_file_is_read_too(tmp_path):
    """小程序套件由独立的 Node 进程驱动开发者工具，和本进程之间没有函数调用可言，
    但它拿得到同一个 proofs 目录。约定成文件之后，「谁来采」和「谁来判」就解耦了。

    在这条链路之前，observer 读的两份文件只有 playwright 会写，所以它在小程序套件上
    什么也看不到——而 67 条缺陷里有 60 条在小程序端。
    """
    # 这三行是 mcp_servers/wxmp/mp_events.mjs 实际写出来的形状（node --test 覆盖了它，
    # 这里复制过来是为了钉住跨进程的那一半：JS 改了形状，这条会红）。
    (tmp_path / "mp_console_logs.json").write_text(
        "\n".join(
            [
                json.dumps(
                    {"type": "console", "level": "error", "text": 'Uncaught TypeError: Cannot read properties of undefined (reading "resultId")', "location": {"url": "miniprogram"}},
                    ensure_ascii=False,
                ),
                json.dumps({"type": "console", "level": "error", "text": "上报失败，已进入重试队列", "location": {"url": "miniprogram"}}, ensure_ascii=False),
                json.dumps({"type": "console", "level": "log", "text": "一切正常", "location": {"url": "miniprogram"}}, ensure_ascii=False),
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    found = InvariantWatcher().poll(str(tmp_path))
    assert [v.severity for v in found] == [BINDING, NOTICE], "未捕获异常判红、普通报错只留痕"


def test_adding_an_evidence_producer_is_one_table_entry():
    """扩展点只有这一处。新增一个采集方 = 往表里加一行，判定逻辑一行不改——
    分类只在 invariants.py 里做，让每个采集方各自判一遍迟早会漂移。"""
    from testzeus_hercules.core.invariants import EVIDENCE_SOURCES

    names = [name for name, _ in EVIDENCE_SOURCES]
    assert "console_logs.json" in names  # playwright 写
    assert "mp_console_logs.json" in names  # wxmp server.js 写
    assert "network_logs.json" in names
    # 同一种行形状复用同一个解析函数，不为新采集方复制一份
    extractors = {name: extract for name, extract in EVIDENCE_SOURCES}
    assert extractors["console_logs.json"] is extractors["mp_console_logs.json"]
