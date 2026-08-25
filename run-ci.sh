#!/usr/bin/env bash
# CI 入口：把 Jenkins / GitLab 传进来的变量翻成一次可判定的运行。
#
# 和 run-version.sh 的分工：run-version.sh 是给人用的，参数是位置参数，跑完就完了；
# run-ci.sh 是给机器用的，参数从环境变量来，跑完必须给出退出码和 e2e-summary.json。
#
#   E2E_TYPE         api | web                      必填
#   E2E_TARGET       regression | 3.0 | 1.0         默认 regression
#   E2E_LANE         泳道名；baseline 部署传空串    api 必填（含空串）
#   E2E_DEPLOY_MODE  lane | baseline                默认按 E2E_LANE 是否为空推导
#   E2E_SUITE        只跑一个套件，如 esm-gateway   可选
#
# 用例类型和 runner 是绑死的：api 是纯 HTTP，web 要 Playwright，两者都能进 Linux 容器；
# mp / mp-real 要微信开发者工具，只能上 macOS runner，这个脚本直接拒。
set -euo pipefail
cd "$(dirname "$0")"

# 退出码在这个脚本里有约定：2 = 调用方给的参数不对（门禁没法判定），1 = 用例真的红了。
# ${VAR:?} 会返回 1，正好和「用例失败」撞上，所以这里手写而不是用参数展开。
TYPE="${E2E_TYPE:-}"
if [ -z "$TYPE" ]; then
  echo "✗ E2E_TYPE 必填：api 或 web"
  exit 2
fi
TARGET="${E2E_TARGET:-regression}"
SUITE="${E2E_SUITE:-}"

case "$TYPE" in
  api|web) ;;
  mp|mp-real)
    echo "✗ $TYPE 需要微信开发者工具（GUI，一次只能开一个项目），跑不进 Linux 容器。"
    echo "  接入需要一台常驻开发者工具的自建 macOS runner，且 mp 与 mp-real 不能同一个 job。"
    exit 2 ;;
  *)
    echo "✗ 未知的 E2E_TYPE=$TYPE，可选：api / web"
    exit 2 ;;
esac

# --- 环境判定 -----------------------------------------------------------------
# E2E_LANE 未定义和定义为空是两回事：前者是调用方忘了传，后者是明确说“打 baseline”。
# esm_api.py 里 DEFAULT_LANE 兜底成 feat-esm-v3-0，不显式 export 的话一次 baseline 的
# E2E 会静默打到那条泳道上然后报绿——测的根本不是刚部署的镜像。所以这里必须区分。
if [ -n "${E2E_DEPLOY_MODE:-}" ]; then
  MODE="$E2E_DEPLOY_MODE"
elif [ "${E2E_LANE+defined}" = "defined" ]; then
  MODE=$([ -n "$E2E_LANE" ] && echo lane || echo baseline)
else
  MODE=""
fi

if [ "$TYPE" = "api" ]; then
  if [ "${E2E_LANE+defined}" != "defined" ]; then
    echo "✗ api 用例必须显式传 E2E_LANE（baseline 部署传空串 E2E_LANE=）。"
    echo "  不传的话 esm_api.py 会退回 DEFAULT_LANE=feat-esm-v3-0，测到的不是本次部署。"
    exit 2
  fi
  # ESM v3.0 当前只部署在泳道上，baseline 没有；不带泳道头打过去是 istio 的 503，
  # 会得到一屏与本次部署无关的红。与其让人去分辨哪条红是真的，不如直接不跑。
  if [ "$MODE" = "baseline" ] && [ "${ESM_ALLOW_BASELINE:-0}" != "1" ]; then
    echo "✗ api 套件目前不能跑 baseline：ESM v3.0 服务只部署在泳道上，baseline 上没有，"
    echo "  不带泳道头会拿到 istio 503。v3.0 合入 baseline 后设 ESM_ALLOW_BASELINE=1 放开。"
    exit 2
  fi
  export ESM_BASE_URL="${ESM_BASE_URL:-https://zmapi-test.daqian369.com}"
  export ESM_LANE="$E2E_LANE"
  export ESM_PRODUCT_ID="${ESM_PRODUCT_ID:-narnia}"
  # esm_call 这些工具住在 additional_tools/，靠这个变量被 BaseNavAgent.load_tools 加载。
  # 不设它 api_nav_agent 只有 generic_http_api，用例会全线失败。
  export ADDITIONAL_TOOL_DIRS="${ADDITIONAL_TOOL_DIRS:-$(pwd)/additional_tools}"
fi

# CI 里没有开发者工具、没有 mock 网关，MCP 一律关掉；HEADLESS 默认就是 true，显式写死
# 是为了不被继承来的 .env 或 runner 上的残留变量改掉。
export MCP_ENABLED=false
export HEADLESS=true
export AUTO_MODE=1
export ENABLE_TELEMETRY="${ENABLE_TELEMETRY:-0}"

# run-version.sh 只在第二个参数是类型时才给构建目录加 -<类型> 后缀；传套件名时它落在
# opt/.run/<选集> 下（assemble_version.py 的 out_root 规则）。这里必须跟着走，否则 gate
# 会去扫一个不存在的目录，然后报“没有产出任何用例”——把配置错误伪装成执行失败。
if [ -n "$SUITE" ]; then
  BASE="opt/.run/$TARGET"
else
  BASE="opt/.run/$TARGET-$TYPE"
fi
SUMMARY="${E2E_SUMMARY_PATH:-e2e-summary.json}"
JUNIT="${E2E_JUNIT_PATH:-e2e-junit.xml}"

echo "── Hercules E2E ─────────────────────────────────────────"
printf '  %-14s %s\n' 类型 "$TYPE" 选集 "$TARGET" 套件 "${SUITE:-全部}" \
                      环境 "${MODE:-n/a}" 泳道 "${E2E_LANE:-（baseline，不带泳道头）}"
echo "─────────────────────────────────────────────────────────"

# run-version.sh 无论用例红绿都退出 0（__main__.py 的 main() 跑完就返回），所以这里
# 不能靠它的退出码判断结果，只用来区分“跑崩了”和“跑完了”。判定一律交给 e2e_gate.py：
# 中途崩掉时已经产出的 XML 仍然要进 summary，否则一次 provider 抖动会让报告整个消失。
set +e
if [ -n "$SUITE" ]; then
  ./run-version.sh "$TARGET" "$TYPE-$SUITE"
else
  ./run-version.sh "$TARGET" "$TYPE"
fi
RUN_RC=$?
set -e
[ "$RUN_RC" -ne 0 ] && echo "⚠ 执行进程以 $RUN_RC 退出，仍按已产出的报告判定"

exec uv run python helper_scripts/e2e_gate.py \
  --run-dir "$BASE" \
  --summary "$SUMMARY" \
  --junit "$JUNIT" \
  --known-failures opt/cases/known_failures.txt \
  --selection-type "$TYPE" \
  --selection-target "$TARGET" \
  --lane "${E2E_LANE:-}" \
  --deploy-mode "${MODE:-unknown}" \
  --runner-exit-code "$RUN_RC"
