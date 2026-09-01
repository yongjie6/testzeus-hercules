# tool_registry.py
import csv
import hashlib
import os
from collections import defaultdict
from collections.abc import Callable
from typing import Any

from testzeus_hercules.config import get_global_conf
from testzeus_hercules.utils.logger import logger

# Define the type of the functions that will be registered as tools
toolType = Callable[..., Any]

# Global registry to store private tool functions and their metadata
#
tool_registry: dict[list[dict[str, Any]]] = defaultdict(list)


def accessibility_logger(identity: str, violations_json: dict) -> None:
    """
    Converts Axe-core accessibility violations JSON to CSV.

    Parameters:
    - violations_json (dict): The JSON object containing accessibility violations.

    Returns:
    None
    """
    og_identity = f"{identity}"

    # Extract violations from the JSON
    if isinstance(violations_json, list):
        violations = violations_json
    else:
        violations = violations_json.get("violations", [])

    if not violations:
        logger.info("No violations found in the provided JSON.")
        return

    # Define CSV header
    csv_header = [
        "Violation ID",
        "Impact",
        "Description",
        "Help URL",
        "Affected Element",
        "Fix Recommendation",
    ]

    # Use CONF for path management
    proof_path = get_global_conf().get_proof_path()
    accessibility_logs_path = os.path.join(proof_path, "accessibility_logs.csv")
    with open(accessibility_logs_path, mode="w", newline="", encoding="utf-8") as csv_file:
        writer = csv.writer(csv_file)

        # Write header row
        writer.writerow(csv_header)

        # Write each violation and affected nodes to the CSV
        for violation in violations:
            identity_page = og_identity
            violation_id = violation["id"]
            impact = violation.get("impact", "N/A")
            description = violation["description"]
            help_url = violation["helpUrl"]
            nodes = violation.get("nodes", [])

            for node in nodes:
                affected_element = node["html"]
                fix_recommendation = node["failureSummary"]

                writer.writerow(
                    [
                        identity_page,
                        violation_id,
                        impact,
                        description,
                        help_url,
                        affected_element,
                        fix_recommendation,
                    ]
                )

    logger.info(f"Accessibility violations successfully saved to {proof_path}.")


def accessibility_logger_json(identity: str, logging_string: str) -> None:
    """
    Function to log to a file.

    Parameters:
    - logging_string (str): The string to log.
    """
    # clean identity str
    identity = identity.replace("/", "").replace(":", "").lower().replace("#", "")
    proof_path = get_global_conf().get_proof_path()
    json_accessibility_dom_path = os.path.join(proof_path, "json_accessibility_dom.json")
    with open(json_accessibility_dom_path, "a", encoding="utf-8") as file:
        file.write(logging_string + "\n")


def api_logger(logging_string: str) -> None:
    """
    Function to log to a file.

    Parameters:
    - logging_string (str): The string to log.
    """
    proof_path = get_global_conf().get_proof_path()
    api_logs_path = os.path.join(proof_path, "api_logs.log")
    with open(api_logs_path, "a", encoding="utf-8") as file:
        file.write(logging_string + "\n")


def assertion_logger(logging_string: str) -> None:
    """把一条断言结果追加到本 scenario 的 proofs/assertion_logs.log。

    通过的断言原先什么都不留：日志只在 FAILED 时写，JUnit 里也没有断言的位置。
    结果是一条绿色用例完全不可审计——没有任何记录能回答"它到底断言了什么"，
    连"它一次断言都没做"和"它断言了十次全过"都分不出来。审计只能靠事后翻模型
    自述的散文，而那正是断言工具存在的意义所要取代的东西。
    """
    proof_path = get_global_conf().get_proof_path()
    assertion_logs_path = os.path.join(proof_path, "assertion_logs.log")
    with open(assertion_logs_path, "a", encoding="utf-8") as file:
        file.write(logging_string + "\n")


def assertion_subject(examined: str) -> str:
    """被断言的那份内容的短指纹，随每条断言记录一起落台账。

    判定侧要把「同一条断言的重试」折叠成一条，否则模型写错一次再改对会被当成被测方的
    缺陷。而只按 (路径, 运算符, 期望值) 折叠会撞车：跨页一致性检查在两个页面上用的正是
    同一个期望值，A 页判否、B 页判是，折叠之后只剩下 B 页那条通过的——恰好把这类缺陷
    折没了。指纹进折叠键，两页就分得开。

    只取前 12 位十六进制：它的用途是区分，不是校验完整性。
    """
    return hashlib.sha1((examined or "").encode("utf-8")).hexdigest()[:12]


def sec_logger(logging_string: str) -> None:
    """
    Function to log to a file.

    Parameters:
    - logging_string (str): The string to log.
    """
    proof_path = get_global_conf().get_proof_path()
    security_logs_path = os.path.join(proof_path, "security_logs.log")
    with open(security_logs_path, "a", encoding="utf-8") as file:
        file.write(logging_string + "\n")


def tool(agent_names: list[str], description: str, name: str | None = None) -> Callable[[toolType], toolType]:
    """
    Decorator for registering private tools.

    Parameters:
    - description: A string describing the tool's function.
    - name: Optional name to register the tool with. If not provided, the function's name will be used.

    Returns:
    - A decorator function that registers the tool in the global registry.
    """

    def decorator(func: toolType) -> toolType:
        for agent_name in agent_names:
            tool_registry[agent_name].append(
                {
                    "name": (name if name else func.__name__),  # Use provided name or fallback to function name
                    "func": func,
                    "description": description,
                }
            )
        return func

    return decorator
