import datetime
import json
import os
import tempfile
from typing import Any, Dict, List

import aiofiles
from junitparser import Failure, JUnitXml, Property, TestCase, TestSuite
from junitparser.junitparser import Properties, SystemOut
from testzeus_hercules.config import get_global_conf
from testzeus_hercules.telemetry import EventData, EventType, add_event


def flatten_dict(d: Dict[str, Any], parent_key: str = "", sep: str = ".") -> Dict[str, Any]:
    """
    Flatten a nested dictionary.

    Args:
        d (Dict[str, Any]): The dictionary to flatten.
        parent_key (str): The base key to use for the flattened keys.
        sep (str): The separator to use between keys.

    Returns:
        Dict[str, Any]: The flattened dictionary.
    """
    items: List[tuple[str, Any]] = []
    for key, value in d.items():
        new_key = f"{parent_key}{sep}{key}" if parent_key else key
        if isinstance(value, dict):
            items.extend(flatten_dict(value, new_key, sep=sep).items())
        else:
            items.append((new_key, value))
    return dict(items)


class JUnitXMLGenerator:
    def __init__(
        self,
        suite_name: str,
        feature_file_path: str,
        output_file_path: str,
        proofs_path: str,
        proofs_video_path: str,
        proofs_screenshot_path: str,
        network_logs_path: str,
        logs_path: str,
        planner_thoughts_path: str,
    ) -> None:
        """
        Initialize the JUnitXMLGenerator.

        Args:
            suite_name (str): The name of the test suite.
        """
        self.suite_name = suite_name
        self.suite = TestSuite(suite_name)
        self.total_execution_cost = 0.0
        self.total_token_used = 0
        self.total_time = 0.0
        self.feature_file_path = feature_file_path
        self.output_file_path = output_file_path
        self.proofs_path = proofs_path
        self.proofs_video_path = proofs_video_path
        self.proofs_screenshot_path = proofs_screenshot_path
        self.logs_path = logs_path
        self.planner_thoughts_path = planner_thoughts_path
        self.network_logs_path = network_logs_path

    def _evidence_gaps(self) -> list[str]:
        """Lines from EVIDENCE_MISSING.txt in this scenario's proofs folder.

        Any tool that fails to capture evidence appends a line there; the file
        is absent on a run where nothing failed.
        """
        marker = os.path.join(str(self.proofs_path), "EVIDENCE_MISSING.txt")
        try:
            with open(marker, encoding="utf-8") as handle:
                return [line.strip() for line in handle if line.strip()]
        except OSError:
            return []

    # 「这条断言根本没求值成功」的判据。措辞取自 api_assertions / ui_assertions 里实际
    # 会写出的 reason，不是猜的。
    _UNEVALUABLE = ("matched nothing", "一行都没匹配到", "没有任何可见文本", "正向对照", "没有被捕获过", "不是合法")

    @classmethod
    def _unevaluable(cls, record: dict) -> bool:
        """这条记录是「工具没用对／条件不具备，断言压根没跑成」，还是「跑成了、值不符」？

        两者要区别对待，因为它们的重试语义相反：
        - 没跑成的，模型会改对再来一次，那次才是真正的判定，前一次不该算数；
        - 跑成了但值不符的，那是关于被测方的一条事实，后面再断言别的东西不能把它抹掉。
        """
        outcome = str(record.get("assertion", "")).lower()
        if outcome == "error":
            return True
        reason = str(record.get("reason", ""))
        return outcome == "failed" and any(marker in reason for marker in cls._UNEVALUABLE)

    @staticmethod
    def _assertion_target(record: dict, *, with_subject: bool) -> tuple:
        """一条断言记录「断的是什么」。

        `with_subject` 决定要不要把被断言内容的指纹算进去，这是两种折叠语义的分界：

        - 求值失败的记录按 **不带 subject** 折叠。模型写错 JSONPath 会得到 FAILED
          （`path matched nothing` 走的就是 FAILED 分支，不是 ERROR），改对之后重发一次
          请求再断言——响应里有时间戳和 id，指纹必然不同。带上 subject 就永远销不掉账，
          一次自我纠正变成一条红。
        - 值不符的记录按 **带 subject** 计数，且永不销账。跨页一致性检查在两个页面上用的
          正是同一个期望值（capture_ui_value 就是为这类缺陷做的）：A 页判否、B 页判是，
          不带 subject 会把 A 页那条折进 B 页，正好折掉这类缺陷。
        """
        base = (
            str(record.get("path") or record.get("scope") or ""),
            str(record.get("operator") or record.get("mode") or ""),
            str(record.get("expected", "")),
            str(record.get("must_contain", "")),
        )
        return (str(record.get("subject", "")),) + base if with_subject else base

    def _assertion_ledger(self) -> dict:
        """本 scenario 的断言台账，来自 proofs/assertion_logs.log。

        没有这一项的话，一条绿色用例是不可审计的：报告里看不出它断言了十次全过，
        还是一次都没断言、由模型自己读着说通过——而后者恰恰是这套用例明令禁止的判定方式。
        零断言的通过尤其要在报告里显形，否则它和真正验过的通过长得一模一样。

        `unresolved` 是给判定用的，由两部分组成：
        - 值不符的 FAILED，按 (指纹, 目标) 去重，一条不销；
        - 求值失败的记录里，同一目标后来始终没有成功过的那些。
        """
        path = os.path.join(str(self.proofs_path), "assertion_logs.log")
        counts = {"total": 0, "passed": 0, "failed": 0, "error": 0, "invariant": 0}
        mismatched: dict[tuple, dict] = {}
        unevaluable: dict[tuple, dict] = {}
        evaluated_ok: set = set()
        try:
            with open(path, encoding="utf-8") as handle:
                for line in handle:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        record = json.loads(line)
                    except ValueError:
                        continue
                    if not isinstance(record, dict):
                        continue
                    if record.get("source") == "invariant":
                        # 不变量是 observer 自动加的，不是用例写的断言。把它算进 total，
                        # 一条 4xx 就足以让「本条用例没有调用确定性断言工具」这条属性消失，
                        # 报告于是显示"断言 N 条全过"——而用例其实一次都没断言过。
                        counts["invariant"] += 1
                        if str(record.get("assertion", "")).lower() == "failed":
                            mismatched[self._assertion_target(record, with_subject=True)] = record
                        continue
                    counts["total"] += 1
                    outcome = str(record.get("assertion", "")).lower()
                    if outcome in counts:
                        counts[outcome] += 1
                    loose = self._assertion_target(record, with_subject=False)
                    if outcome == "passed":
                        evaluated_ok.add(loose)
                    elif self._unevaluable(record):
                        unevaluable[loose] = record
                    elif outcome in ("failed", "error"):
                        mismatched[self._assertion_target(record, with_subject=True)] = record

        except OSError:
            pass

        failures: list[str] = []
        for record in list(mismatched.values()) + [r for key, r in unevaluable.items() if key not in evaluated_ok]:
            subject = record.get("path") or record.get("scope") or record.get("mode") or "?"
            operator = record.get("operator") or record.get("mode") or ""
            detail = record.get("reason") or f"{record.get('actual')!r} (期望 {record.get('expected')!r})"
            failures.append(f"{subject} {operator} -> {detail}")
        return counts | {"failures": failures, "unresolved": len(failures)}

    def add_test_case(
        self,
        scenario: str,
        feature: str,
        json_data: Dict[str, Any],
        execution_time: float,
        cost_metric: Dict[str, Any],
    ) -> None:
        """
        Add a test case to the test suite.

        Args:
            scenario (str): The scenario name.
            feature (str): The feature name.
            json_data (Dict[str, Any]): The JSON data containing test details.
            execution_time (float): The execution time of the test case.
            cost_metric (Dict[str, Any]): The cost metrics associated with the test case.
        """
        test_case = TestCase(name=scenario, classname=feature, time=execution_time)

        final_response = json_data.get("final_response")
        terminate = json_data.get("terminate", "yes")
        assert_summary = json_data.get("assert_summary", "Runtime Failure")
        is_assert = json_data.get("is_assert", False)
        is_passed = json_data.get("is_passed", False)
        ledger = self._assertion_ledger()

        if is_assert:
            add_event(
                EventType.ASSERT,
                EventData(detail=f"Assertion with result: {is_passed}"),
            )
            if not is_passed:
                test_case.result = Failure(message=assert_summary)
            elif ledger["unresolved"]:
                # planner 说通过，但确定性断言工具说没通过。以工具为准。
                #
                # 之前这里只看 planner 的 is_passed，台账只是一条 JUnit property——
                # 于是 assert_json_path / assert_ui 全都是建议性的：断言判否，用例照样绿。
                # 那让整套「断言必须由工具得出」的约定失去了强制力，也让断言工具沦为
                # 一种更啰嗦的日志。判定必须落在工具上，否则它就不是判定。
                #
                # 只看每个断言目标的**最后**一次结果（见 _assertion_target）：模型写错
                # 路径再改对属于自我纠正，不该判红；从头到尾没成立过的才算。
                detail = "; ".join(ledger["failures"][:3])
                test_case.result = Failure(message=f"planner 报告通过，但有 {ledger['unresolved']} 条断言最终仍未通过：{detail}")
            else:
                test_case.system_out = assert_summary
        else:
            # A scenario that stopped without asserting anything verified nothing,
            # so it cannot be reported as a pass. The planner narrates the reason
            # in final_response -- an unreachable tool, a provider error, a
            # precondition it could not establish -- but those runs used to land
            # here with terminate="yes" and come out green, which turns an
            # infrastructure outage into a clean report.
            # Read assert_summary off json_data rather than the local: it is
            # defaulted to "Runtime Failure" above, and that placeholder is
            # truthy, so it would mask the reason the planner actually wrote.
            test_case.result = Failure(message=str(json_data.get("assert_summary") or final_response or "The scenario ended without asserting anything."))

        # A tool that could not capture what it was asked to capture leaves a
        # marker in the proofs folder. Without this the scenario reports as a
        # clean pass whose proofs folder is empty, and nothing says which of the
        # two it is -- nothing to capture, or capture that failed.
        evidence_gaps = self._evidence_gaps()

        opt_list = []
        test_props = Properties()
        test_case.append(test_props)
        test_props.add_property(Property(name="Terminate", value=str(terminate)))
        test_props.add_property(Property(name="Feature File", value=str(self.feature_file_path)))
        test_props.add_property(Property(name="Output File", value=str(self.output_file_path)))
        test_props.add_property(Property(name="Proofs Video", value=str(self.proofs_video_path)))
        test_props.add_property(
            Property(
                name="Proofs Base Folder, includes screenshots, recording, netwrok logs, api logs, sec logs, accessibility logs",
                value=str(self.proofs_path),
            )
        )
        test_props.add_property(Property(name="Proofs Screenshot", value=str(self.proofs_screenshot_path)))
        if evidence_gaps:
            test_props.add_property(Property(name="Evidence Missing", value=f"{len(evidence_gaps)} 次采集失败: " + "; ".join(evidence_gaps[:3])))
        test_props.add_property(Property(name="Network Logs", value=str(self.network_logs_path)))
        test_props.add_property(Property(name="Agents Internal Logs", value=str(self.logs_path)))
        test_props.add_property(Property(name="Planner Thoughts", value=str(self.planner_thoughts_path)))

        test_props.add_property(Property(name="Assertions Total", value=str(ledger["total"])))
        test_props.add_property(Property(name="Assertions Passed", value=str(ledger["passed"])))
        test_props.add_property(Property(name="Assertions Failed", value=str(ledger["failed"] + ledger["error"])))
        test_props.add_property(Property(name="Assertions Unresolved", value=str(ledger["unresolved"])))
        if ledger["total"] == 0:
            # 判定是模型读出来的，不是断言工具得出的。它**暂时**不改变本条的红绿：现在还有
            # 一批用例一次都不调断言工具，把零断言直接判红会让它们集体变红，而那批红说的是
            # 用例没改造完，不是被测方有问题——分不出来的红等于没有红。等它们改造完之后，
            # 这里应该收紧成失败。在那之前，报告里必须看得见。
            test_props.add_property(Property(name="Assertions Missing", value="本条用例没有调用确定性断言工具，结果由模型自述"))
            opt_list.append("Assertions: 0 — 结果由模型自述，未经确定性断言工具验证")
        else:
            opt_list.append(f"Assertions: {ledger['total']} 条，通过 {ledger['passed']}，失败 {ledger['failed'] + ledger['error']}，最终未通过 {ledger['unresolved']}")
        for detail in ledger["failures"][:5]:
            opt_list.append(f"Assertion failed: {detail}")

        opt_list.append(f"Final Response: {final_response}")
        if evidence_gaps:
            opt_list.append(f"Evidence Missing: {len(evidence_gaps)} capture(s) failed, proofs are incomplete")

        for key, value in json_data.items():
            if key not in [
                "is_assert",
                "is_passed",
                "final_response",
                "assert_summary",
            ]:
                prop = Property(name=key, value=str(value))
                test_props.add_property(prop)

        res_d = flatten_dict(cost_metric)
        res = [f"{k}: {v}" for k, v in res_d.items()]
        opt_list.append(", ".join(res))

        for opt_item in opt_list:
            test_case.append(SystemOut(opt_item))

        flat_cost_metric = flatten_dict(cost_metric)
        for key, value in flat_cost_metric.items():
            test_props.add_property(Property(name=key, value=str(value)))

        total_cost_value = flat_cost_metric.get("total_cost")
        if total_cost_value is None:
            total_cost_value = flat_cost_metric.get("usage_including_cached_inference.total_cost")
        if total_cost_value is not None:
            self.total_execution_cost += float(total_cost_value)

        token_keys = [
            key
            for key in flat_cost_metric
            if key.endswith(".total_tokens") and (key.startswith("usage_including_cached_inference.") or not any(k.startswith("usage_including_cached_inference.") for k in flat_cost_metric))
        ]
        for key in token_keys:
            self.total_token_used += int(flat_cost_metric[key])

        self.total_time += float(execution_time)
        self.suite.add_testcase(test_case)

    async def write_xml(self, output_file: str) -> None:
        """
        Write the test suite to a JUnit XML file asynchronously.

        Args:
            output_file (str): The path to the output XML file.
        """
        self.suite.add_property("total_execution_cost", str(self.total_execution_cost))
        self.suite.add_property("total_token_used", str(self.total_token_used))
        self.suite.time = self.total_time
        self.suite.timestamp = datetime.datetime.now().isoformat()
        self.suite.update_statistics()

        xml = JUnitXml()
        xml.add_testsuite(self.suite)

        # Write to temp file first
        with tempfile.NamedTemporaryFile(mode="w", delete=False) as tmp:
            xml.write(tmp.name)
            tmp_path = tmp.name

        # Read from temp and write to final destination asynchronously
        async with aiofiles.open(tmp_path, "r") as src, aiofiles.open(output_file, "w") as dest:
            content = await src.read()
            await dest.write(content)

        # Clean up temp file
        os.unlink(tmp_path)

    @staticmethod
    async def merge_junit_xml(files: List[str], output_file: str) -> None:
        """
        Merge multiple JUnit XML files into one asynchronously.

        Args:
            files (List[str]): List of file paths to JUnit XML files.
            output_file (str): Path to the output merged JUnit XML file.
        """
        merged_xml = JUnitXml()
        suite_dict: Dict[str, TestSuite] = {}

        for file in files:
            xml = JUnitXml.fromfile(file)
            for suite in xml:
                suite_name = suite.name
                if suite_name in suite_dict:
                    existing_suite = suite_dict[suite_name]
                    for testcase in suite:
                        existing_suite.add_testcase(testcase)
                    existing_suite.time = float(existing_suite.time or 0.0) + float(suite.time or 0.0)

                    for prop in suite.properties():
                        is_existing_prop = False
                        for existing_prop in existing_suite.properties():
                            if existing_prop.name.strip() == prop.name.strip():
                                try:
                                    existing_value = float(existing_prop.value)
                                    new_value = float(prop.value)
                                    existing_prop.value = existing_value + new_value
                                except ValueError:
                                    pass
                                is_existing_prop = True

                        existing_suite.update_statistics()

                        if not is_existing_prop:
                            existing_suite.add_property(name=prop.name, value=prop.value)
                else:
                    suite_dict[suite_name] = suite

            # delete the files of individual test cases
            if os.path.exists(file) and get_global_conf().get_mode() not in ["debug"]:
                os.remove(file)

        for suite in suite_dict.values():
            merged_xml.add_testsuite(suite)

        # Write merged XML to temp file first
        with tempfile.NamedTemporaryFile(mode="w", delete=False) as tmp:
            merged_xml.write(tmp.name)
            tmp_path = tmp.name

        # Read from temp and write to final destination asynchronously
        async with aiofiles.open(tmp_path, "r") as src, aiofiles.open(output_file, "w") as dest:
            content = await src.read()
            await dest.write(content)

        # Clean up temp file
        os.unlink(tmp_path)

        # Delete individual test files if not in debug mode
        if get_global_conf().get_mode() not in ["debug"]:
            for file in files:
                if os.path.exists(file):
                    os.remove(file)


async def build_junit_xml(
    json_data: Dict[str, Any],
    execution_time: float,
    cost_metric: Dict[str, Any],
    feature: str,
    scenario: str,
    feature_file_path: str = None,
    output_file_path: str = None,
    proofs_path: str = None,
    proofs_video_path: str = None,
    proofs_screenshot_path: str = None,
    logs_path: str = None,
    network_logs_path: str = None,
    planner_thoughts_path: str = None,
) -> str:
    """
    Build a JUnit XML file from test data.

    Args:
        json_data (Dict[str, Any]): The JSON data containing test details.
        execution_time (float): The execution time of the test case.
        cost_metric (Dict[str, Any]): The cost metrics associated with the test case.
        feature (str): The feature name.
        scenario (str): The scenario name.

    Returns:
        str: The path to the generated JUnit XML file.
    """
    feature_r = feature.replace(" ", "_").replace(":", "")
    scenario_r = scenario.replace(" ", "_").replace(":", "")

    file_path = f"{get_global_conf().get_junit_xml_base_path()}/{feature_r}_{scenario_r}_results.xml"

    generator = JUnitXMLGenerator(
        feature,
        feature_file_path,
        file_path or output_file_path,
        proofs_path,
        proofs_video_path,
        proofs_screenshot_path,
        network_logs_path,
        logs_path,
        planner_thoughts_path,
    )
    generator.add_test_case(scenario, feature, json_data, execution_time, cost_metric)
    await generator.write_xml(file_path)
    return file_path


def run_test() -> None:
    """
    Run a test and generate JUnit XML files.
    """
    json_data = {
        "terminate": "yes",
        "final_response": "The number of bikes displayed when the Max price is set to 1600 is 2, which matches the expected result.",
        "is_assert": False,
    }

    execution_time = 30.04
    cost_metric = {
        "usage_including_cached_inference": {
            "total_cost": 0.12923500000000002,
            "gpt-4o-2024-08-06": {
                "cost": 0.12923500000000002,
                "prompt_tokens": 45362,
                "completion_tokens": 1583,
                "total_tokens": 46945,
            },
        },
        "usage_excluding_cached_inference": {
            "total_cost": 0.11612000000000003,
            "gpt-4o-2024-08-06": {
                "cost": 0.11612000000000003,
                "prompt_tokens": 41220,
                "completion_tokens": 1307,
                "total_tokens": 42527,
            },
        },
    }

    feature = "Feature: Filter Bikes by Max Price"
    scenario = "Scenario: User filters bikes with Max Price of 1600"

    f1_path = build_junit_xml(
        json_data,
        execution_time,
        cost_metric,
        feature,
        scenario,
    )

    json_data_fail = {
        "terminate": "yes",
        "final_response": "No bikes are displayed when the Max price is set to 1600, which does not match the expected result.",
        "is_assert": True,
    }
    f2_path = build_junit_xml(
        json_data_fail,
        execution_time,
        cost_metric,
        feature + "1",
        scenario + "1",
    )

    f2_path = build_junit_xml(json_data_fail, execution_time, cost_metric, feature + "1", scenario + "1")

    JUnitXMLGenerator.merge_junit_xml(
        [f1_path, f2_path],
        f"{get_global_conf().get_junit_xml_base_path()}/final_results.xml",
    )


# # Example usage
# run_test()
