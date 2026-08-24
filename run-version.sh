#!/usr/bin/env bash
# 跑一个版本的用例，或核心回归集
#   ./run-version.sh 3.0             跑 v3.0 的全部用例
#   ./run-version.sh 3.0 api         只跑 v3.0 的接口用例
#   ./run-version.sh 1.0 mp          只跑 v1.0 的小程序用例
#   ./run-version.sh 1.0 mp-nav      只跑一个套件
#   ./run-version.sh regression      跑核心回归集（按 opt/cases/regression.txt 挑选）
#   ./run-version.sh regression mp   回归集里的小程序部分
#
# 三种类型要求的环境不一样：api 要泳道，mp 要开发者工具和 mock 网关。
# 所以分类型跑不是图方便，是能不能跑起来的前提。
set -euo pipefail
V="${1:?用法: ./run-version.sh <版本|regression> [类型|套件]   类型: api / web / mp}"
SEL="${2:-}"

TYPES="api web mp"
TYPE=""
SUITE=""
if [ -n "$SEL" ]; then
  if [[ " $TYPES " == *" $SEL "* ]]; then TYPE="$SEL"; else SUITE="$SEL"; fi
fi

if [ -n "$TYPE" ]; then
  ./assemble_version.py "$V" "$TYPE"
  BASE="opt/.run/$V-$TYPE"
else
  ./assemble_version.py "$V"
  BASE="opt/.run/$V"
fi

# 只有跑到小程序套件时才需要开发者工具。曾经有一轮对着无渲染的实例跑满 24 分钟，
# 报了 7 个通过却一张截图都没有；但接口用例不碰开发者工具，不该被它挡在门外。
needs_devtools() {
  [ -n "$SUITE" ] && { [[ "$SUITE" == mp-* ]] && return 0 || return 1; }
  [ -n "$TYPE" ] && { [ "$TYPE" = mp ] && return 0 || return 1; }
  ls -d "$BASE"/tests/mp-* >/dev/null 2>&1
}
if needs_devtools; then
  node mcp_servers/wxmp/preflight.mjs
fi

if [ -n "$SUITE" ]; then
  D="$BASE/tests/$SUITE"
  if [ ! -d "$D" ]; then
    echo "版本 $V 里没有套件 $SUITE"
    echo "现有套件: $(ls "$BASE/tests" 2>/dev/null | tr '\n' ' ')"
    exit 1
  fi
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
