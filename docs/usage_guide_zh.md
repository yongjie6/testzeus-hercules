# TestZeus Hercules 使用说明

Hercules 的对外契约只有一条：**Gherkin 进，报告出**。你写一个 `.feature` 文件描述测试意图，它自己规划步骤、驱动浏览器 / API / SQL 完成操作，最后吐出 JUnit XML、HTML 报告，以及截图、录屏、网络日志等证据。

| | |
| --- | --- |
| 输入 | Gherkin `.feature` 文件 |
| 输出 | JUnit XML + HTML 报告 + 执行证据 |
| Python | 3.11 – 3.13 |
| 浏览器 | Playwright（chromium / firefox / webkit） |
| 必备 | 一个 LLM API Key |

## 目录

1. [安装](#1-安装)
2. [配置模型](#2-配置模型)
3. [项目目录](#3-项目目录)
4. [写测试](#4-写测试)
5. [运行](#5-运行)
6. [看结果](#6-看结果)
- [常用环境变量](#常用环境变量)
- [进阶能力](#进阶能力)
- [排查清单](#排查清单)

---

## 1. 安装

三种方式，按用途选一种即可。日常跑测试用 A，要改代码 / 加工具用 B，进 CI 用 C。

### A. pip 安装（最快）

```bash
pip install testzeus-hercules
playwright install --with-deps
```

### B. 源码安装（要改代码时）

```bash
git clone git@github.com:test-zeus-ai/testzeus-hercules.git
cd testzeus-hercules
make setup-uv     # pip install uv
make install      # uv sync
uv run playwright install --with-deps
```

> **必看 · 已知坑**
>
> `make install` 的实际内容是 `uv sync && exit && uv run playwright install --with-deps`，在 `.ONESHELL:` 下那个 `exit` 会直接终止 shell —— **Playwright 浏览器永远不会被装上**。装完依赖后一定要单独再跑一次 `uv run playwright install --with-deps`，否则第一次运行会以"找不到浏览器"失败。

### C. Docker（CI / 批量跑）

```bash
docker pull testzeus/hercules:latest

docker run --env-file=.env \
  -v ./agents_llm_config.json:/testzeus-hercules/agents_llm_config.json \
  -v ./opt:/testzeus-hercules/opt \
  --rm -it testzeus/hercules:latest
```

Docker 模式下不需要传 `--input-file` / `--output-path` 这些参数，挂载 `opt` 目录就够了。容器内只有无头浏览器；要连可见浏览器或远程网格，用 `CDP_ENDPOINT_URL`（见[进阶能力](#进阶能力)）。

---

## 2. 配置模型

两条路，二选一。跑通第一个用例用路线 A 就够；要省钱或做精细调优再上路线 B。

### A. 单模型直连

```bash
export LLM_MODEL_NAME=gpt-4o
export LLM_MODEL_API_KEY=sk-xxxxxx
export LLM_MODEL_BASE_URL=https://api.openai.com/v1
```

仓库根目录有 `.env-example`，`cp .env-example .env` 后改即可。Hercules 启动时会自动加载当前目录的 `.env`。

### B. 分角色配置（推荐长期使用）

Hercules 内部只取三份模型配置：`planner_agent`（规划）、`nav_agent`（所有执行类 Agent 共用）、`helper_agent`（视觉比对）。把强模型给 planner、便宜快的给 nav，是最直接的降本手段。

```json
{
  "my_profile": {
    "planner_agent": {
      "model_name": "claude-sonnet-5",
      "model_api_key": "sk-xxx",
      "model_base_url": "http://localhost:4000",
      "llm_config_params": { "temperature": 0.0, "seed": 12345 }
    },
    "nav_agent":    { "model_name": "gpt-5-mini", "model_api_key": "sk-xxx" },
    "helper_agent": { "model_name": "gpt-4o",     "model_api_key": "sk-xxx" }
  }
}
```

```bash
export AGENTS_LLM_CONFIG_FILE=agents_llm_config.json
export AGENTS_LLM_CONFIG_FILE_REF_KEY=my_profile
```

仓库里的 `agents_llm_config-example.json.txt` 已经写好了 `litellm`、`openai_gpt`、`azure`、`anthropic`、`anthropic_bedrock`、`gemini`、`ollama`、`groq`、`deepseek`、`mistral` 等多套模板，直接抄。

### 配置优先级

由低到高，后者覆盖前者：

```
内置默认  →  .env 文件  →  环境变量  →  --config 文件  →  命令行参数
```

---

## 3. 项目目录

Hercules 默认以 `./opt` 作为项目根（`PROJECT_SOURCE_ROOT`）。你只需要准备 `input/` 和 `test_data/`，其余目录运行时自动创建。

```
opt/
├── input/              ← 你放 .feature 文件的地方
│   └── test.feature
├── test_data/          ← 你放测试数据的地方（账号、URL、期望值…）
│   └── test_data.txt
├── gherkin_files/      自动：按 Scenario 拆分后的单条用例
├── output/             自动：run_<时间戳>/ 下的 XML 与 HTML 报告
├── log_files/          自动：每条用例的 Agent 思考链路
└── proofs/             自动：截图 / 录屏 / 网络日志
```

想换位置，用 `--project-base <dir>` 指定根目录，或用 `--input-file`、`--output-path`、`--test-data-path` 三个参数分别指定。

> **关于 test_data**
>
> `test_data/` 下的内容会被读进 Agent 的长期记忆，作为系统提示的一部分注入。账号密码、目标 URL、期望文案这类东西放这里，用例正文里就可以只写"用测试账号登录"，不必把数据硬编码进 Gherkin。

---

## 4. 写测试

标准 Gherkin 语法，写给人看的粒度就行——不需要写选择器，不需要写等待。

```gherkin
Feature: Product Search on Demo Store

  Scenario: Successfully find a product

    Given I am on the demo store home page
    When I enter "wireless headphones" in the search field
    And I click on the "Search" button
    And I open the first matching product result
    Then I should see the product details page
    And I should see "wireless headphones" in the product title or description
```

> **最重要的一条原则：用例必须原子**
>
> 一个 Scenario 只验证一件事。Hercules 会把 feature 文件**按 Scenario 拆成独立用例逐条执行**，每条都是独立的 LLM 会话和浏览器上下文。一个塞了登录 + 下单 + 退款的巨型 Scenario，既会让 Agent 迷失方向，也让你无法从报告里定位到底哪步坏了。

### 写作要点

- 用**用户看得见的东西**指代元素："点击『搜索』按钮"，而不是 CSS 选择器。
- 断言写进 `Then`：Agent 会据此判定通过与否，并把结论写进 `assert_summary`。
- 要断言视觉效果（截图比对、组件外观）直接用自然语言描述，会路由到视觉助手。
- 不要让 Agent "自己去搜一下"——没有明确 URL 时它会直接停下报错，这是刻意设计，避免乱逛。
- 支持中文等多语言用例正文。

### 不会写？让它替你生成

```bash
# 只生成 Gherkin，打印出来不执行
testzeus-hercules --test "打开 example.com 并搜索无线耳机" --dry-run

# 生成之后直接执行
testzeus-hercules --test "打开 example.com 并搜索无线耳机"
```

---

## 5. 运行

### 四种运行模式

| 模式 | 命令 | 用途 |
| --- | --- | --- |
| **单文件** | `testzeus-hercules --input-file …` | 默认模式，跑一个 feature 文件里的所有 Scenario |
| **批量** | `--bulk` 或 `EXECUTE_BULK=true` | 把 `<根>/tests/` 下每个子目录当成独立项目依次跑完 |
| **引导** | `--guided` / `--test "…"` | 自然语言 → Gherkin → 执行；加 `--dry-run` 只生成不执行 |
| **交互** | `make run-interactive` | 聊天式逐条下指令，调试工具行为和新环境时最好用 |

### 最常用的一行

```bash
testzeus-hercules \
  --input-file opt/input/test.feature \
  --output-path opt/output \
  --test-data-path opt/test_data
```

源码方式下等价于 `make run`（直接读 `opt/` 目录）。

### 命令行参数

| 参数 | 说明 |
| --- | --- |
| `--project-base` | 项目根目录；给了它就不必再给下面三个路径参数 |
| `--input-file` | 输入的 .feature 文件 |
| `--output-path` | 报告输出目录 |
| `--test-data-path` | 测试数据目录 |
| `--config` | YAML / JSON 配置文件 |
| `--llm-model` | 模型名，等价于 `LLM_MODEL_NAME` |
| `--llm-model-api-key` | API Key |
| `--llm-model-base-url` | OpenAI 兼容的 base URL |
| `--llm-model-api-type` | API 类型（openai / anthropic / azure 等） |
| `--llm-temperature` | 采样温度，测试场景建议 0 |
| `--agents-llm-config-file` | 分角色模型配置文件路径 |
| `--agents-llm-config-file-ref-key` | 激活配置文件里的哪个 profile |
| `--bulk` | 批量模式 |
| `--guided` / `--test` / `--dry-run` | 引导模式相关 |
| `--browser-channel` | 如 `chrome-beta`、`msedge` |
| `--browser-path` / `--browser-version` | 指定浏览器可执行文件或版本 |
| `--enable-ublock` / `--disable-ublock` | uBlock Origin 广告拦截扩展 |
| `--auto-accept-screen-sharing` | 自动接受屏幕共享弹窗 |
| `--sandbox-tenant-id` | Python 沙箱权限档位 |
| `--sandbox-custom-injections` | 沙箱自定义注入（JSON） |
| `--enable-portkey` / `--portkey-api-key` / `--portkey-strategy` | Portkey 网关路由（fallback / loadbalance） |

---

## 6. 看结果

跑完之后按这个顺序看：

| 看什么 | 在哪 | 什么时候看 |
| --- | --- | --- |
| **HTML 报告** | `output/run_<ts>/*.html` | 日常第一眼：通过 / 失败、耗时、token 成本 |
| **JUnit XML** | `output/run_<ts>/*.xml` | 给 CI 吃的，接 Jenkins / GitLab 等 |
| **思考链路** | `log_files/<用例>/run_<ts>/agent_inner_thoughts.json` | 用例挂了但看不懂为什么——**从这里开始查** |
| **录屏 / 截图** | `proofs/<用例>/run_<ts>/videos\|screenshots/` | 要肉眼确认页面当时长什么样 |
| **网络日志** | `proofs/…/network_logs.json` | 怀疑是接口问题而不是前端问题 |

> **agent_inner_thoughts.json 里有什么**
>
> 完整的 planner 决策记录：每一步的 `plan`、`next_step`、派给了哪个执行 Agent（`target_helper`）、助手回了什么、以及最终的 `is_passed` 与 `assert_summary`。绝大多数"为什么它没点那个按钮"的问题，答案都在这个文件里。

另外 `proofs/` 下还会按需生成 `api_logs.log`、`security_logs.log`、`accessibility_logs.csv`，分别对应接口测试、安全扫描和无障碍检查的产出。接口请求的超时用 `API_REQUEST_TIMEOUT` 控制，默认 30 秒。

---

## 常用环境变量

完整清单见 [environment_variables.md](environment_variables.md)，下面是实际最常改的几个。

| 变量 | 默认 | 说明 |
| --- | --- | --- |
| `HEADLESS` | `true` | 设 `false` 可以看着它操作，调试必备 |
| `BROWSER_TYPE` | `chromium` | 也支持 `firefox`、`webkit` |
| `BROWSER_RESOLUTION` | — | 如 `1920,1080` |
| `BROWSER_COOKIES` | — | JSON 数组，用来跳过登录直接带态运行 |
| `DONT_CLOSE_BROWSER` | `false` | 跑完不关浏览器，方便手工接着看 |
| `RECORD_VIDEO` | `true` | 录屏，CI 里可关掉省磁盘 |
| `TAKE_SCREENSHOTS` | `true` | 每步截图 |
| `CAPTURE_NETWORK` | `true` | 抓网络请求 |
| `MODE` | `prod` | 设 `debug` 出更详细日志 |
| `AUTO_MODE` | `0` | 设 `1` 后首次运行不再交互式索要邮箱，CI 必设 |
| `ENABLE_TELEMETRY` | `1` | 遥测默认开启，设 `0` 关闭 |
| `RUN_DEVICE` | — | 移动端模拟，如 `"iPhone 15 Pro Max"` |
| `LOAD_EXTRA_TOOLS` | `false` | 开启地理位置、剪贴板、拖拽、PDF 等扩展工具 |
| `CDP_ENDPOINT_URL` | — | 连接远程浏览器 |
| `REACTION_DELAY_TIME` | `0.1` | 动作之间的间隔秒数，页面慢时可调大 |
| `EXECUTE_BULK` | `false` | 批量模式 |

> **隐私提示**
>
> 遥测默认**开启**，会上报匿名安装 ID、配置快照，以及本次运行用到的目录路径——**路径里可能带你的系统用户名**。介意的话在 `.env` 里加 `ENABLE_TELEMETRY=0`。

---

## 进阶能力

### Python 沙箱：自动化搞不定时人工接管

遇到诡异控件、复杂条件逻辑、需要数据处理的场景，可以在 Gherkin 里直接调用你自己写的 Python 函数，脚本里能拿到完整的 Playwright `page` 对象。

```gherkin
And execute the apply_filter function from script at "scripts/apply_filter.py" with filter_type as "Turtle Neck"
```

```python
# opt/scripts/apply_filter.py
async def apply_filter(filter_type: str) -> dict:
    """page、logger、asyncio、json 等已自动注入，无需 import。"""
    await page.wait_for_selector('[data-filter-section]')
    for selector in [f'input[value="{filter_type}"]',
                     f'label:has-text("{filter_type}") input']:
        if await page.locator(selector).count() > 0:
            await page.locator(selector).click()
            break
    return {"status": "success", "filter": filter_type}
```

用 `SANDBOX_TENANT_ID` 控制脚本能 import 什么，档位有 `executor_agent`（最宽，含 requests / pandas / numpy / BeautifulSoup）、`data_agent`、`api_agent`、`restricted_agent`。另有 `SANDBOX_PACKAGES` 和 `SANDBOX_CUSTOM_INJECTIONS` 做细粒度注入。详见 [python_sandbox_execution.md](python_sandbox_execution.md)。

### MCP：把外部系统接进测试

支持 `stdio`、`sse`、`streamable-http` 三种传输。典型用途是从 Gmail 取验证码、查数据库、调内部服务。

`mcp_servers.json`（放在仓库根目录）：

```json
{
  "mcpServers": {
    "server_name": {
      "transport": "streamable-http",
      "url": "<session.mcp.url>",
      "headers": { "Authorization": "Bearer <token>" }
    }
  }
}
```

`.env`：

```bash
MCP_ENABLED=true
MCP_SERVERS=mcp_servers.json
MCP_TIMEOUT=30
```

配好之后照常运行即可，MCP 服务端的工具会被自动发现并挂到 MCP 执行 Agent 上，输入 schema 原样保留。详见 [MCP_Usage.md](MCP_Usage.md)。

### 远程浏览器

```bash
# BrowserStack
export CDP_ENDPOINT_URL=$(python helper_scripts/browser_stack_generate_cdp_url.py)

# LambdaTest
export CDP_ENDPOINT_URL=$(python helper_scripts/lambda_test_generate_cdp_url.py)

# BrowserBase / AnchorBrowser
export CDP_ENDPOINT_URL=wss://connect.browserbase.com?apiKey=xxx
```

注意：只有走 `connect_over_cdp` 的平台（BrowserBase、AnchorBrowser）支持录屏；BrowserStack 和 LambdaTest 走 connect API，**拿不到录屏**。

### 移动端与地理位置

```bash
# .env
RUN_DEVICE="iPhone 15 Pro Max"    # Playwright 设备描述符名称
LOAD_EXTRA_TOOLS=true
GEO_PROVIDER=maps_co              # 或 google
GEO_API_KEY=your_key
```

设备名取自 [Playwright 设备描述符列表](https://github.com/microsoft/playwright/blob/main/packages/playwright-core/src/server/deviceDescriptorsSource.json)。

### API 测试

接口测试走两条路：`.feature` 里用自然语言描述、由 `api_nav_agent` 现场调用断言工具判定，适合探索性用例；契约已经稳定的回归用例写成 `.hurl`，断言写死，跑起来不需要 LLM key。断言/契约校验/取值/回归执行的完整工具清单和取舍判据见 [api_testing.md](api_testing.md)，Hurl 那条路的细节见 [api_testing_hurl.md](api_testing_hurl.md)。

### 安全与无障碍

安全测试走 Nuclei 集成（OWASP Top 10、配置错误、API 缺陷），无障碍支持 WCAG 2.0 / 2.1 / 2.2 的 A / AA / AAA 级别，产出分别落在 `proofs/` 下的 `security_logs.log` 与 `accessibility_logs.csv`。用例正文里用自然语言提出要求即可，planner 会路由到对应的执行 Agent。

---

## 排查清单

| 现象 | 先查这个 |
| --- | --- |
| 启动就报找不到浏览器 | 单独跑 `playwright install --with-deps`——`make install` 不会帮你装 |
| CI 里卡住不动 | 设 `AUTO_MODE=1`，否则首次运行会停下来交互式索要邮箱 |
| 没有任何用例被执行 | feature 文件里至少要有一个 `Scenario:` 块，否则直接退出 |
| Agent 点错元素 / 原地打转 | 看 `agent_inner_thoughts.json`；多半是 Scenario 不够原子，或步骤描述里的元素名和页面上看到的文案对不上 |
| 它跑去搜索引擎乱逛 | 用例或 test_data 里没给明确 URL。给了它就不会猜——这是刻意设计 |
| 模型报 INVALID_ARGUMENT / 超 token | 页面 DOM 过大。Hercules 已做压缩，但超复杂企业应用仍可能触顶，考虑换上下文更大的模型或拆细用例 |
| 成本超预期 | 报告里有每条用例的 token 与费用。把 `nav_agent` 换成便宜模型，只给 `planner_agent` 用强模型 |
| 结果不稳定 | 把 `temperature` 设为 `0`，并在 `llm_config_params` 里固定 `seed` |

> **成本参考**
>
> 按官方实测，一个包含搜索、进商品、加购物车、核对结算摘要的复杂用例，用 OpenAI gpt-4o 大约花费 **$0.20**。每次运行的实际 token 与费用都会打进测试报告的属性里，可以据此估算自己的用例。

---

## 延伸阅读

- [README.md](../README.md) —— 完整功能介绍
- [run_guide.md](run_guide.md) —— 项目目录结构详解
- [environment_variables.md](environment_variables.md) —— 全部环境变量
- [MCP_Usage.md](MCP_Usage.md) —— MCP 接入
- [python_sandbox_execution.md](python_sandbox_execution.md) —— Python 沙箱
- [Migration/ARCHITECTURE.md](Migration/ARCHITECTURE.md) —— planner / executor / assertion 图的运行时契约
