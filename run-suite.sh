#!/usr/bin/env bash
# 跑单个套件：./run-suite.sh wxmp
set -euo pipefail
S="${1:?用法: ./run-suite.sh <套件名>   例如 wxmp / web}"
D="opt/tests/$S"
[ -f "$D/hercules.yaml" ] || { echo "找不到 $D/hercules.yaml"; exit 1; }
rm -rf "$D/output" "$D/log_files" "$D/proofs" "$D/gherkin_files"
exec uv run python -m testzeus_hercules --config "$D/hercules.yaml"
