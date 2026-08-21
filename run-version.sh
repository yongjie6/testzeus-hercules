#!/usr/bin/env bash
# 跑一个版本的用例，或核心回归集
#   ./run-version.sh 2.0            只跑 v2.0 自己的用例
#   ./run-version.sh 2.0 mine       只跑 v2.0 里的 mine 套件
#   ./run-version.sh regression     跑核心回归集（按 opt/cases/regression.txt 挑选）
set -euo pipefail
V="${1:?用法: ./run-version.sh <版本|regression> [套件名]   例如 2.0 / 2.0 mine / regression}"
SUITE="${2:-}"

# 环境自检先跑：曾经有一轮对着无渲染的 --cli 实例跑满 24 分钟，
# 报了 7 个通过却一张截图都没有，而且判定是否可信无从确认。
node mcp_servers/wxmp/preflight.mjs

./assemble_version.py "$V"
BASE="opt/.run/$V"

if [ -n "$SUITE" ]; then
  D="$BASE/tests/$SUITE"
  [ -d "$D" ] || { echo "版本 $V 里没有套件 $SUITE"; exit 1; }
  # bulk 会遍历整个 tests/，所以指定单套件时改用显式路径并关掉 bulk
  cat > "$BASE/hercules.yaml" <<YAML
bulk: false
project_base: $(pwd)/$D
input_file: $(pwd)/$D/input/$SUITE.feature
test_data_path: $(pwd)/$D/test_data
output_path: $(pwd)/$D/output
YAML
  exec uv run python -m testzeus_hercules --config "$BASE/hercules.yaml"
fi

exec uv run python -m testzeus_hercules --project-base "$(pwd)/$BASE"
