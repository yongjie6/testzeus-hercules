#!/usr/bin/env python
"""Trim an OpenAPI/Swagger spec down to the paths a test suite actually exercises.

static_data_loader.py loads every file under test_data/ wholesale and minifies it into
the LLM's prompt — a full spec costs the same tokens on every single Scenario in the
suite, even though most Scenarios in practice only touch a handful of paths. Run this
once when authoring test_data for a suite and check the trimmed spec in instead of the
full one; unreferenced $ref targets (definitions / components.schemas) are dropped too,
not just the unused paths, since those are often the bulkier part of a spec.

Usage:
    python helper_scripts/trim_openapi_spec.py api_spec.json --paths /pet,/pet/{petId} --out api_spec.trimmed.json
    python helper_scripts/trim_openapi_spec.py api_spec.json --path-prefix /pet --out api_spec.trimmed.json

--paths takes exact path template matches (comma-separated); --path-prefix keeps every
path starting with the given prefix. Combine both freely; a path is kept if it matches
either.
"""

import argparse
import json
import sys
from typing import Any, Dict, List, Set

import yaml


def load_spec(path: str) -> Dict[str, Any]:
    with open(path, "r", encoding="utf-8") as f:
        return yaml.safe_load(f)  # YAML parses valid JSON too


def definitions_key(spec: Dict[str, Any]) -> List[str]:
    """Where $ref targets live, as a JSON-pointer segment list: ["definitions"] for
    Swagger 2.0, ["components", "schemas"] for OpenAPI 3.x."""
    if "swagger" in spec:
        return ["definitions"]
    return ["components", "schemas"]


def find_refs(node: Any) -> Set[str]:
    """Every $ref target name referenced anywhere under node (recursively)."""
    refs: Set[str] = set()
    if isinstance(node, dict):
        ref = node.get("$ref")
        if isinstance(ref, str) and ref.startswith("#/"):
            refs.add(ref.rsplit("/", 1)[-1])
        for v in node.values():
            refs |= find_refs(v)
    elif isinstance(node, list):
        for item in node:
            refs |= find_refs(item)
    return refs


def trim(spec: Dict[str, Any], exact_paths: Set[str], prefixes: List[str]) -> Dict[str, Any]:
    all_paths = spec.get("paths", {})
    kept_paths = {p: v for p, v in all_paths.items() if p in exact_paths or any(p.startswith(pre) for pre in prefixes)}
    if not kept_paths:
        raise ValueError(f"no declared path matched the given selectors; declared paths are: {sorted(all_paths.keys())}")

    trimmed = dict(spec)
    trimmed["paths"] = kept_paths

    def_key = definitions_key(spec)
    defs_container = spec
    for segment in def_key:
        defs_container = defs_container.get(segment, {})
    if not defs_container:
        return trimmed

    # $ref targets can themselves reference other definitions (a Pet referencing a
    # Category, say), so this has to close over the reachable set, not just one pass.
    reachable = find_refs(kept_paths)
    frontier = set(reachable)
    while frontier:
        newly_found: Set[str] = set()
        for name in frontier:
            schema = defs_container.get(name)
            if schema is not None:
                newly_found |= find_refs(schema)
        newly_found -= reachable
        reachable |= newly_found
        frontier = newly_found

    trimmed_defs = {name: schema for name, schema in defs_container.items() if name in reachable}

    target = trimmed
    for segment in def_key[:-1]:
        target = target.setdefault(segment, {})
    target[def_key[-1]] = trimmed_defs

    return trimmed


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("spec_path", help="Path to the source OpenAPI/Swagger spec (.json or .yaml/.yml).")
    parser.add_argument("--paths", default="", help="Comma-separated exact path templates to keep, e.g. /pet,/pet/{petId}.")
    parser.add_argument("--path-prefix", action="append", default=[], help="Keep every path starting with this prefix. May be given multiple times.")
    parser.add_argument("--out", required=True, help="Where to write the trimmed spec (.json).")
    args = parser.parse_args()

    exact_paths = {p for p in args.paths.split(",") if p}
    if not exact_paths and not args.path_prefix:
        print("error: give at least one of --paths or --path-prefix", file=sys.stderr)
        raise SystemExit(2)

    spec = load_spec(args.spec_path)
    trimmed = trim(spec, exact_paths, args.path_prefix)

    with open(args.out, "w", encoding="utf-8") as f:
        json.dump(trimmed, f, indent=2)
        f.write("\n")

    before = len(json.dumps(spec, separators=(",", ":")))
    after = len(json.dumps(trimmed, separators=(",", ":")))
    kept_def_count = len(trimmed.get(definitions_key(spec)[0], {}) if len(definitions_key(spec)) == 1 else trimmed.get("components", {}).get("schemas", {}))
    print(f"kept {len(trimmed['paths'])}/{len(spec.get('paths', {}))} paths, {kept_def_count} referenced schema(s)")
    print(f"minified size: {before} -> {after} bytes ({100 - round(100 * after / before)}% smaller)")


if __name__ == "__main__":
    main()
