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
export ESM_PRODUCT_ID="${ESM_PRODUCT_ID:-narnia}"

# esm_call 这些工具住在 additional_tools/，由 BaseNavAgent.load_tools 按这个变量加载。
# 不设它，api_nav_agent 只有 generic_http_api，用例会全线失败。
export ADDITIONAL_TOOL_DIRS="${ADDITIONAL_TOOL_DIRS:-$(pwd)/additional_tools}"

set -euo pipefail
exec ./run-version.sh 3.0 "$@"
