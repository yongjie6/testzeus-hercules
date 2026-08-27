#!/usr/bin/env bash
# 跑 ESM V3.0 服务端接口用例
#   ./run-esm-api.sh                 跑 v3.0 的全部 ESM 接口套件
#   ./run-esm-api.sh esm-gateway     只跑网关套件
#   ./run-esm-api.sh esm-assessment  只跑答题套件
#
# 套件名：esm-gateway / esm-assessment / esm-profile / esm-chat / esm-flow
#
# 被测环境靠这三个变量切换，默认打测试环境的 feat-esm-v3-0 泳道。
# ESM v3.0 这套服务当前只部署在泳道上，baseline 上没有，不带泳道头会 503。
export ESM_BASE_URL="${ESM_BASE_URL:-https://zmapi-test.daqian369.com}"
export ESM_LANE="${ESM_LANE:-feat-esm-v3-0}"
export ESM_PRODUCT_ID="${ESM_PRODUCT_ID:-esm_miniprogram}"

# esm_call 这些工具住在 additional_tools/，由 BaseNavAgent.load_tools 按这个变量加载。
# 不设它，api_nav_agent 只有 generic_http_api，用例会全线失败。
export ADDITIONAL_TOOL_DIRS="${ADDITIONAL_TOOL_DIRS:-$(pwd)/additional_tools}"

set -euo pipefail

# 不带参数要显式说明是 api 这一类。run-version.sh 收不到类型就把小程序套件一起装进来，
# 而 mp 和 mp-real 互斥，整个 run 会直接退出——「跑全部 ESM 接口套件」反而一条都跑不了。
#
# 传套件名时补上 api- 前缀：assemble_version.py 把 api/esm-gateway 铺成 api-esm-gateway，
# run-version.sh 找的是铺完之后的目录名，直接传 esm-gateway 会说套件不存在。
if [ $# -eq 0 ]; then
  exec ./run-version.sh 3.0 api
fi

case "$1" in
  api-*) exec ./run-version.sh 3.0 "$1" ;;
  *)     exec ./run-version.sh 3.0 "api-$1" ;;
esac
