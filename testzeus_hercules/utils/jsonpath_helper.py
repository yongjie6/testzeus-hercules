from typing import Any, List

from jsonpath_ng.ext import parse


def find_json_path(doc: Any, path: str) -> List[Any]:
    """Evaluate a JSONPath against a parsed JSON document and return every match's value.

    Shared by assert_json_path and extract_from_response so both tools parse JSONPath the
    same way and a syntax error is diagnosed identically wherever it surfaces.
    """
    return [m.value for m in parse(path).find(doc)]
