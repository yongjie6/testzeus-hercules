#!/usr/bin/env bash
# 跑某个产品版本的全部用例（累积：含该版本及之前引入的所有用例）
#   ./run-version.sh 2.0            跑 v2.0 全量回归
#   ./run-version.sh 2.0 mine       只跑 v2.0 里的 mine 套件
set -euo pipefail
V="${1:?用法: ./run-version.sh <版本> [套件名]   例如 2.0 / 2.0 mine}"
SUITE="${2:-}"

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
