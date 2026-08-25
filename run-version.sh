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

TYPES="api web mp mp-real"
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
#
# 小程序有两种跑法，用的是两个不同的包：mp 走 dist-scenario，API 地址编译期写死成本地
# mock 网关；mp-real 走 dist-integration，编译进真实泳道地址。开发者工具一次只能打开
# 一个项目，所以这两类凑在同一个 run 里必然有一半跑不起来——与其让它半绿，不如直接拒绝。
#
# 注意 mp-real-* 同时匹配 mp-*，所有分支一律用 case 且把 mp-real-* 放前面。写成一串
# if 的话顺序一旦被人调换就会静默走错分支。
MOCK_MP=0
REAL_MP=0
if [ -n "$SUITE" ]; then
  case "$SUITE" in
    mp-real-*) REAL_MP=1 ;;
    mp-*)      MOCK_MP=1 ;;
  esac
elif [ -n "$TYPE" ]; then
  case "$TYPE" in
    mp-real) REAL_MP=1 ;;
    mp)      MOCK_MP=1 ;;
  esac
else
  for suite_dir in "$BASE"/tests/*/; do
    [ -d "$suite_dir" ] || continue
    case "$(basename "$suite_dir")" in
      mp-real-*) REAL_MP=1 ;;
      mp-*)      MOCK_MP=1 ;;
    esac
  done
fi

if [ "$MOCK_MP" = 1 ] && [ "$REAL_MP" = 1 ]; then
  echo "mp 和 mp-real 不能在同一个 run 里：前者要 dist-scenario + mock 网关，"
  echo "后者要 dist-integration + 真实泳道，开发者工具一次只能开一个项目。"
  echo "分开跑：./run-version.sh $V mp   和   ./run-version.sh $V mp-real"
  exit 1
fi

if [ "$REAL_MP" = 1 ]; then
  node mcp_servers/wxmp/preflight.mjs --mode real
elif [ "$MOCK_MP" = 1 ]; then
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
