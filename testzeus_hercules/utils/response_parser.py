import json
import re
from typing import Any

from testzeus_hercules.utils.logger import logger


def _load_planner_json(message: str) -> dict[str, Any] | None:
    """Parse the last complete JSON object that looks like a planner response."""
    candidates = []
    for match in re.finditer(r"\{", message):
        start = match.start()
        depth = 0
        for index, char in enumerate(message[start:]):
            if char == "{":
                depth += 1
            elif char == "}":
                depth -= 1
                if depth == 0:
                    candidates.append(message[start : start + index + 1])
                    break
    for candidate in reversed(candidates):
        try:
            parsed = json.loads(candidate)
        except Exception:
            continue
        if isinstance(parsed, dict) and any(key in parsed for key in ("plan", "next_step", "terminate", "final_response", "target_helper")):
            return parsed
    return None


def parse_response(message: str) -> dict[str, Any]:
    """
    Parse the response from the browser agent and return the response as a dictionary.
    """
    if not message or not str(message).strip():
        return {}

    json_response: dict[str, Any] = {}

    # Check if message is wrapped in ```json ``` blocks
    if "```json" in message:
        start_idx = message.find("```json") + 7
        end_idx = message.find("```", start_idx + 7)
        message = message[start_idx:end_idx]
    else:
        # Original handling for ``` blocks
        if message.startswith("```"):
            message = message[3:]
        if message.endswith("```"):
            message = message[:-3]
        if message.startswith("json"):
            message = message[4:]

    message = message.strip()

    # Try the response exactly as it came first. The flattening below rewrites
    # the text before json.loads ever sees it, and it cannot tell a newline
    # escape from one that is itself escaped: given a valid `\\n` it consumes
    # the second backslash and leaves `\` plus a space, which is an invalid
    # escape. The response then falls through to the salvage branch, which only
    # recovers four keys -- target_helper is not one of them, so the step gets
    # routed to the default helper instead of the one the planner chose. That
    # failure looks like the planner picking the wrong helper, which is a long
    # way from where the problem actually is.
    intact = _load_planner_json(message)
    if intact is not None:
        return intact

    message = message.replace("\\n", "\n")
    message = message.replace("\n", " ")  # type: ignore

    # Find all top-level JSON objects and take the last one (most recent planner response)
    json_candidates = []
    for m in re.finditer(r"\{", message):
        start = m.start()
        depth = 0
        for i, ch in enumerate(message[start:]):
            if ch == "{":
                depth += 1
            elif ch == "}":
                depth -= 1
                if depth == 0:
                    json_candidates.append(message[start : start + i + 1])
                    break
    if json_candidates:
        for candidate in reversed(json_candidates):
            try:
                parsed_candidate = json.loads(candidate)
            except Exception:
                continue
            if any(key in parsed_candidate for key in ("plan", "next_step", "terminate", "final_response", "target_helper")):
                message = candidate
                break
        else:
            message = json_candidates[-1]  # take the last complete JSON object
    elif not any(key in message for key in ("plan", "next_step", "terminate", "final_response")):
        logger.debug("Skipping non-JSON response parsing for message: %s", message[:200])
        return {}

    try:
        json_response: dict[str, Any] = json.loads(message)
    except Exception as e:
        logger.warning(
            "LLM response was not properly formed JSON. Will try to use it as is. " 'LLM response: "%s". Error: %s',
            message[:200],
            e,
        )

        if "plan" in message and "next_step" in message:
            start = message.index("plan") + len("plan")
            end = message.index("next_step")
            json_response["plan"] = message[start:end].replace('"', "").strip()
        if "next_step" in message and "terminate" in message:
            start = message.index("next_step") + len("next_step")
            end = message.index("terminate")
            json_response["next_step"] = message[start:end].replace('"', "").strip()
        if "terminate" in message and "final_response" in message:
            start = message.index("terminate") + len("terminate")
            end = message.index("final_response")
            matched_string = message[start:end].replace('"', "").strip()
            if "yes" in matched_string:
                json_response["terminate"] = "yes"
            else:
                json_response["terminate"] = "no"

            start = message.index("final_response") + len("final_response")
            end = len(message) - 1
            json_response["final_response"] = message[start:end].replace('"', "").strip()

        elif "terminate" in message:
            start = message.index("terminate") + len("terminate")
            end = len(message) - 1
            matched_string = message[start:end].replace('"', "").strip()
            if "yes" in matched_string:
                json_response["terminate"] = "yes"
            else:
                json_response["terminate"] = "no"

        # Salvage the routing and verdict fields too. Losing target_helper sends
        # the step to the default helper, and losing is_passed turns a stated
        # failure into a scenario that never asserted -- both silent.
        helper_match = re.search(r'"?target_helper"?\s*[:=]\s*"?([A-Za-z_]+)"?', message)
        if helper_match:
            json_response["target_helper"] = helper_match.group(1)
        for flag in ("is_assert", "is_passed"):
            flag_match = re.search(rf'"?{flag}"?\s*[:=]\s*"?(true|false)"?', message, re.IGNORECASE)
            if flag_match:
                json_response[flag] = flag_match.group(1).lower() == "true"
        summary_match = re.search(r'"?assert_summary"?\s*[:=]\s*"([^"]*)"', message)
        if summary_match:
            json_response["assert_summary"] = summary_match.group(1)

    return json_response
