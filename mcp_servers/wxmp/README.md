# wxmp — 微信小程序 MCP Server

把微信小程序自动化能力接给 Hercules。基于官方 `miniprogram-automator`，通过 MCP stdio 暴露给 `mcp_nav_agent`。

**这是骨架版**，能跑通完整链路，但工具集只覆盖了最常用的路径。

## 设计约束（改之前先看）

**① 只 connect，不 launch。** Hercules 每跑完一条 Scenario 就调 `MCPHelper.destroy()`（见 `simple_hercules.py:1099`），本进程会被反复 spawn / kill —— 一条用例一轮。所以这里绝不能 `automator.launch()` 去拉起 IDE，否则每条用例都要重启一次开发者工具。

**微信开发者工具需要你自己常驻**，本 server 只是个薄客户端。

**② stdout 属于 MCP 协议。** 所有日志走 stderr。这个文件里不要出现 `console.log`。

**③ token 不进 LLM。** `mp_login_as(alias)` 只让模型传别名，真实凭据在本进程内查表解析。原因见下方「安全」。

## 环境（每次跑之前）

小程序不像浏览器，跑一条用例需要三样东西同时在位。缺任何一个的表现都是"页面加载失败"，但原因完全不同。

**1. mock 网关**

```bash
cd <miniapp>/packages/e2e && ./node_modules/.bin/tsx <此目录>/gateway.mts
```

端口 39100/39101 是**编译进小程序包里的**，不能改。`gateway.mts` 用的是 e2e 那套 `startGateway` + fixture 加载器，只是常驻运行——他们的 `test-env.ts` 每次 vitest 跑完就拆掉网关，那个生命周期和 Hercules 对不上。

**读哪个 checkout 由 `MINIAPP_REPO` 决定**，默认 `../../../mini/esm_fe_app`。这里踩过一次：它原先硬编码指向 `mini/esm_mini_app/fe_app`，那是**同一个仓库的另一个 checkout**，停在 v2 时代的分支上。开发者工具换成 v3.0 的包之后，就变成 v3.0 的 app 配 v1 的 fixture，页面照常渲染、数据全错、没有任何地方报错。启动日志第一行现在会打印实际读的路径，跑之前扫一眼。

常驻也意味着它得能换数据：vitest 那边每条 TC 起一个新网关来加载自己的 fixture，这里换不了进程，所以 `gateway.mts` 另开一个控制端口（默认 39102，`GATEWAY_CTRL_PORT`）就地换：

```bash
curl -s http://127.0.0.1:39102/fixture
```

```bash
curl -sX POST http://127.0.0.1:39102/fixture -d '{"scenes":["state-scale-done","state-report-ready"]}'
```

名字按顺序**深合并**上去，见 [fixtures/README.md](fixtures/README.md)。`{"scene":"TC-XXX"}` 的旧写法仍然接受。`mp_use_fixture` 调的就是它。名字写错会直接报错并列出可选值，不会静默退回 base。控制端口只监听 127.0.0.1。

控制端口还有三条给用例用的路由：

| 路由 | 用途 |
| --- | --- |
| `GET /calls` | 应用实际请求过哪些接口，以及**哪些没有 fixture 覆盖** |
| `POST /ws/push` | 往应用推一条 WS 事件，`mp_ws_push` 用 |
| `POST /ws/disconnect` | 断掉应用的 WS 连接，`mp_ws_disconnect` 用 |

`GET /calls` 是排查空白页的第一站。`base/proto/defaults.json` 给**每一个** proto 路由都种了一个只有 `baseResp` 的空响应，所以没被 fixture 覆盖的接口不会 404，而是返回成功加一个空 body——页面转圈或提示重试，但没有任何地方指出是哪个调用回来是空的。`uncovered` 字段直接给出这份名单。

WS 那两条是转发：网关自己的 `/__push` `/__disconnect` 挂在自签 HTTPS 上，转到明文控制端口，MCP server 就不用处理证书（它由 Hercules 跨 stdio 边界拉起，几乎拿不到环境变量）。

**2. 开发者工具打开 `dist-scenario`**

必须是指向 `https://localhost:39100` 的包。`dist` 指向真实后端，跑不通。

`dist-scenario` 在任何仓库里都不是现成的构建脚本，按 e2e 的 `buildEnvVars` 注入变量自己构建：

```bash
cd <miniapp>/packages/app && TARO_OUTPUT_ROOT="$(pwd)/dist-scenario" TARO_APP_API_BASE_URL="https://localhost:39100" TARO_APP_WSS_BASE_URL="wss://localhost:39101" TARO_APP_MONITOR_ID="local-e2e" TARO_APP_MONITOR_ENDPOINT="http://localhost:39200/log" TARO_APP_MONITOR_ENABLE_ENDPOINT="false" TARO_APP_USER_AGREEMENT_URL="https://example.com/agreement" TARO_APP_PRIVACY_URL="https://example.com/privacy" TARO_APP_CONTACT_EMAIL="support@example.com" pnpm run build:weapp
```

v3.0 的产物 appid 是真实的，`cli open` 可以直接打开。**只有 appid 为 `touristappid` 的旧产物**才必须走界面导入——CLI 对它会报 `不存在此 AppID (code 10)`。

**新项目第一次打开后，必须手工勾一次「不校验合法域名」**：详情 → 本地设置 → 勾上「不校验合法域名、web-view（业务域名）、TLS 版本以及 HTTPS 证书」。

这一步没有命令行替代。构建产出的 `project.config.json` 里 `setting.urlCheck: false` 不够，往产物里补一个

```json
{ "setting": { "urlCheck": false } }
```

的 `project.private.config.json` 也不够——开发者工具按项目路径记住这个本地设置，一个从没被勾过的新路径照样拦。漏了这一步的现象很有迷惑性：**开发者工具在渲染、自动化连得上、截图正常，但小程序发出的每个请求都被拦在本地**，网关一条请求都收不到。页面于是停在「正在加载…」「当前无法进入，请稍后重试」，「我的」页显示「登录/注册」而不是昵称——看起来像 fixture 不对，实际一个字节都没出去。

确认它真的通了（应当返回 `statusCode: 200`，而不是 `request:fail url not in domain list`）：

```bash
curl -s http://127.0.0.1:39102/calls
```

跳几个页面之后这里应该列出应用请求过的接口；一直是空的就说明域名校验还开着。

**3. 自动化会话**

```bash
/Applications/wechatwebdevtools.app/Contents/MacOS/cli auto --project <...>/dist-scenario --auto-port 9420
```

别重复起——多个会话抢同一端口会让行为变得不确定。

**换项目时要先关掉旧的。** `cli open` 只是在界面上打开新项目，**不会重绑已有的自动化会话**：9420 会继续连着上一个项目，对它重跑 `cli auto` 照样报成功但不生效，`mp_navigate` 于是在旧包的路由表上跑。先释放端口再重绑：

```bash
/Applications/wechatwebdevtools.app/Contents/MacOS/cli close --project <旧的 dist-scenario>
```

### 验证

```bash
npm run smoke   # 只验 MCP 协议层，不需要开发者工具
npm run live    # 验真实链路：连接、当前页、storage、web-view
```

## 接进 Hercules

仓库根目录 `mcp_servers.json`：

```json
{
  "mcpServers": {
    "wxmp": {
      "transport": "stdio",
      "command": "node",
      "args": ["mcp_servers/wxmp/server.js"]
    }
  }
}
```

`.env`：

```bash
MCP_ENABLED=true
MCP_SERVERS=mcp_servers.json
MCP_TIMEOUT=30

WXMP_WS_ENDPOINT=ws://localhost:9420
WXMP_FIXTURE_DEFAULTS=<miniapp>/packages/e2e/tests/fixtures/base/defaults/defaults.json
WXMP_GATEWAY_CONTROL=http://127.0.0.1:39102
WXMP_PROOF_DIR=opt/proofs
```

> server name 起短点。Hercules 会把工具重命名成 `mmcp_{server}_{tool}`（`mcp_help.py:127`），叫 `wxmp` 得到 `mmcp_wxmp_mp_tap`，够清楚也不啰嗦。

## 环境变量

| 变量 | 默认 | 说明 |
| --- | --- | --- |
| `WXMP_WS_ENDPOINT` | `ws://localhost:9420` | 开发者工具自动化端口 |
| `WXMP_FIXTURE_DEFAULTS` | — | fixture defaults：JSON 文件路径，或内联 JSON。`mp_login_as` 照它种登录态 |
| `WXMP_GATEWAY_CONTROL` | `http://127.0.0.1:39102` | mock 网关的控制端口，`mp_use_fixture` 用 |
| `WXMP_PROOF_DIR` | cwd | 截图落盘目录，指到 Hercules 的 proofs 下 |
| `WXMP_CONNECT_TIMEOUT` | `30000` | 连接超时（毫秒） |
| `WXMP_AUTO_SCREENSHOT` | `on` | 每个改变画面的动作后自动截图，`off` 关掉 |

对应的 CLI flag 同名去掉前缀：`--fixture` / `--gateway-control` / `--endpoint` / `--proof-dir` / `--auto-screenshot`。**flag 优先**——MCP 的 stdio 边界不传环境变量，接进 Hercules 时只能用 flag。

## 登录

用 `mp_login_as('logged-in')`，不要去驱动登录界面——微信授权弹窗是原生 UI，自动化点不到。

登录态直接复用 e2e 的 fixture：

```bash
WXMP_FIXTURE_DEFAULTS=<miniapp>/packages/e2e/tests/fixtures/base/defaults/defaults.json
```

`mp_login_as` 照搬 `tests/utils/storage.ts` 的 `primeStorageFromFixture`：清空 storage → 写 `esm_tokens` 和 `esm_user`（两个都是**对象**，不是字符串）→ reLaunch 到首页。

注意登录态**同时依赖 mock 网关**：只注入 storage 而网关没起，应用拿 token 去校验失败，照样回落到未登录。实测过，现象是「我的」页显示「登录/注册」而不是用户昵称。

fixture 里都是 mock 假值（`access-token-001`），没有真实凭据。

## 工具

| 工具 | 用途 |
| --- | --- |
| `mp_snapshot` | **看见页面**：可见文案 + 控件 + 可操作的 id |
| `mp_navigate` | 命名路由跳转（启动页/首页/对话/我的/量表/报告/…），自动选 switchTab 还是 reLaunch |
| `mp_tap_text` | 按可见文案点击，对齐 getByText |
| `mp_login_as` | `logged-in` / `guest` 前置态 |
| `mp_use_fixture` | 设后端数据状态，多个名字可组合（见 [fixtures/README.md](fixtures/README.md)），**要在 `mp_login_as` 之前调** |
| `mp_swipe` | 手势，带 `until` 会重试到目标文案出现 |
| `mp_long_press` | 按住不放，`duration` 控制时长（微信 350ms 起算长按），`until` 同上 |
| `mp_trigger` | 发组件事件；输入框用 `into`（placeholder）定位，它自己没有文案 |
| `mp_current_page` | 当前路由与 query |
| `mp_tap` / `mp_input` | 按选择器点击 / 输入（兜底） |
| `mp_get_text` / `mp_element_exists` | 按选择器断言（兜底） |
| `mp_page_data` / `mp_call_method` | 页面 data / 调 Page 方法 |
| `mp_set_storage` / `mp_get_storage` / `mp_clear_storage` | storage 读写清 |
| `mp_mock_wx` / `mp_restore_wx` | mock wx API |
| `mp_ws_push` / `mp_ws_disconnect` | 按时刻驱动 WS 流事件、断连，见下方「流事件」 |
| `mp_wait` / `mp_screenshot` | 等待 / 截图存证 |
| `mp_visual_check` | 把当前画面交给视觉模型回答一个问题，**看快照看不到的东西** |
| `mp_visual_compare` | 和基准图比对，用于视觉回归 |
| `mp_webview_url` | 读 web-view 的 src，桥接到浏览器测试 |
| `mp_evaluate` | 逃生舱，逻辑层执行 JS |

**优先用 `mp_snapshot` + `mp_tap_text` + 命名路由**，对齐 `<miniapp>/packages/e2e/tests/AGENTS.md` 的「像用户一样」原则：按可见文案和角色定位，不依赖 class/属性/顺序号。`mp_tap(selector)` 这类按选择器的工具是兜底，正常用例里不该出现。

选择器是 **WXML 选择器**，不是浏览器 DOM 的 CSS 选择器。

## 命名路由

`mp_navigate` 接受中文名（启动页 / 首页 / 对话 / 我的 / 信息采集 / 量表 / 测量结果 / 个人报告 / 他人报告 / 注销账号 / 隐私政策 / 用户协议）、英文 key，或以 `/` 开头的绝对路径。

**路径不是写死的，是启动时从装载的包里读出来的。** 它原先是一张对着 `test-router.ts` 抄的静态表，换个版本的包就整体失效且不报错：v3.0 把 home 从 `/pages/index/index` 挪到 `/features/home/pages/index`，用启动页替掉了登录页，`luxx_test` 改名 `assessment`，底部 tab 从三个减到两个——于是跳「对话」还在对一个已经不是 tab 的页面调 `switchTab`。

现在改成读 `__wxConfig`：页面列表和 tabBar 都由包自己提供，`switchTab` 还是 `reLaunch` 也据此判断。每个名字配一组候选路径（新版在前），命中包里存在的那个。所以同一个 server 对 v1 和 v3 的包都成立，**开着哪个包就导航哪个包**。

包里没有对应页面时报错会说清楚是版本对不上，并列出这个包实际有哪些页面：

```
"量表" is a known route, but this bundle has no page for it -- looked for
features/assessment/pages/index or features/luxx_test/pages/index. The project
open in DevTools is probably a different version of the app than the case expects.
```

加新页面就在 `server.js` 的 `ROUTE_CANDIDATES` 里加一条候选，中文名加进 `ROUTE_ALIASES`。

## 流事件

绝大多数「后端状态」类前置都是 fixture 的事——包括故障和时序注入，`RouteOverride` 上的
`delayMs`、`responseSequence`、`stream.chunks` / `stream.intervalMs` 就够了（见
[fixtures/README.md](fixtures/README.md)）。

有一类不行：断言的对象是**事件到达的时刻和顺序**，不是某个响应的内容。同一个 `event_id`
投递两次、只推增量不推落库、先增量后快照、同一轮按序推完整套事件——fixture 描述的是一个
响应，不是一个时刻。这类用 `mp_ws_push` 一条一条驱动：

```
mp_ws_push({"event_id":"e1","chat_id":"<id>","generation_id":"gen-1","seq_id":"0",
            "event_type":"CHAT_STREAM_EVENT_TYPE_ASSISTANT_DELTA","delta":"你"})
```

信封是 snake_case（它是 on-wire 结构）。`event_type` 取
`CHAT_STREAM_EVENT_TYPE_` 加 `ASSISTANT_DELTA` / `MESSAGE_SNAPSHOT` / `COMPLETED` /
`FINAL_MESSAGE_COMMITTED` / `BLOCKED` / `FAILED` / `HEARTBEAT` / `SUBSCRIBED` /
`RECOMMENDED_QUESTIONS_READY`。一轮之内 `generation_id` 保持一致，`event_id` 各不相同
（除非用例要的就是重复投递）。

两个容易踩的点：

**订阅时机**——应用在会话页打开时就订阅，不需要先发消息。没有订阅时推送会被网关接受然后送给
零个客户端，用例接着在几步之外的一个「气泡没出现」上失败。`mp_ws_push` 因此先查连接数，
没连上直接报错。

**id 要对上**——`chat_id` 和 `generation_id` 必须是当前打开的那个会话的，先从页面上读出来。
推一个属于别的会话的事件，网关照样投递、应用照样忽略，现象和「应用没处理这个事件」一模一样。

## 跑小程序时关掉浏览器

小程序用例全程走 MCP，不碰浏览器，但框架默认每条用例还是拉一个 chromium。除了启动开销，
它会在每条用例的 proofs 里留下空的 `screenshots/` 和 `videos/`，看上去像「采集过、什么都没有」。

```bash
SKIP_BROWSER=true ./run-version.sh 1.0 mp
```

## 截不到图的时候

开发者工具中途丢渲染层的话，截图会失败但逻辑层还活着，用例照跑照报绿——结论还在，
支撑结论的东西没了。这时 server 会往该用例的 proofs 目录写一个 `EVIDENCE_MISSING.txt`，
报告里对应出现一条 `Evidence Missing` 属性，写明失败了几次。它不会让用例判失败，
只是让「没有证据」这件事在报告里看得见。

## 视觉断言

`mp_snapshot` 读的是 WXML 树，页面壳之外的东西它看不到——底部 tabBar 由原生渲染，
不在树里。图片是否真的渲染出来、元素有没有被遮挡或截断，也都是快照结构上答不了的。
这两个工具把画面交给视觉模型来判断。

要在 `mcp_servers.json` 的 args 里配 `--vision-model` / `--vision-base-url` /
`--vision-key`（MCP stdio 不透传环境变量，只能走参数），`mp_visual_compare` 另需
`--baseline-dir`。没配就直接报错，不会静默降级。

**test_data 里必须写死路由，否则视觉断言会被派给浏览器。** planner 的能力表把
「visual validation」归在 browser helper 名下（`high_level_planner_agent.py` 的
Helper Capabilities），所以一条「在截图上确认…」的步骤即使前两步都走了 MCP，到
断言那步也会切到 browser——浏览器截到的是空白页，用例报「无法获取截图」。
实测同一条用例在两次运行里一次走 MCP 一次走 browser。test_data 里点名反驳那条
能力表之后，路由才稳定。

**默认还是用快照。** 每次视觉调用要传约 240KB 图片，判定来自模型对画面的描述，
天然比文案匹配模糊。只在判定对象本身是视觉性的时候用，问题也要问具体——
「底部导航栏有哪几个标签」可靠，「导航栏正常吗」不可靠。

`--proof-dir` 支持 `${proof_path}` 占位符，展开成 Hercules 当前这条用例的
`proofs/<用例名>/<run>/`，证据按用例分开存。写死一个固定目录的话，所有轮次的截图
会堆在一起，事后分不清哪张属于哪条。

基准图不会自动生成：首次调用 `mp_visual_compare` 会存一张 `<名字>.candidate.png`
然后失败，人看过确认没问题、手动改名成 `<名字>.png` 才算数。自动采纳的话，
首次运行时页面正好是坏的就把故障固化成了基准，之后每次都"通过"。

## 用例长这样

```gherkin
Feature: 小程序商品搜索

  Scenario: 已登录用户可以搜到商品
    Given 通过 MCP 以 vip_user 身份登录小程序
    When 跳转到 /pages/search/search
    And 在 .search-input 里输入 "无线耳机"
    And 点击 .search-btn
    Then .result-item 应该存在
    And 截图记录搜索结果
```

### ⚠️ planner 路由

Hercules 的 planner 默认会把「打开页面」派给 **browser** helper，然后拿 `open_url` 去开 URL —— 对小程序全错。

在 `test_data/` 里放一句约束：

```
本项目为微信小程序测试。所有页面操作必须通过 MCP 工具（mmcp_wxmp_*）完成，
禁止使用浏览器工具（open_url、click、get_interactive_elements 等）。
target_helper 应始终为 mcp。
```

这是实际接入时第一个会踩的坑。

## web-view / 内嵌 H5

**官方 automator 不支持 web-view。** 它是为原生组件测试设计的，API 里没有任何 iframe/webview context 能力。`page.$("web-view")` 能拿到**组件本身**，但拿不到里面的 DOM——内嵌 H5 是独立的浏览器上下文。

所以别想着"钻进去"，正确做法是**拆**。按成本从低到高：

### 1 · 绕开（登录场景首选）

如果 web-view 只是拿 token 的手段，用 `mp_login_as` 直接注入登录态，根本不碰 H5。多数「H5 授权页」场景到这里就结束了。

### 2 · 拆测（推荐）

H5 就是普通网页，而 **Hercules 本来就有完整的 Playwright 栈**。把测试拆成两段：

- 小程序壳 → `mmcp_wxmp_*` 工具
- H5 页面 → Hercules 原生浏览器工具（`open_url` / `click` / `get_interactive_elements` …）

桥梁是 `mp_webview_url`：运行时从小程序里读出真实 URL 再交给浏览器 Agent。**不要把 URL 硬编码进 feature 文件**——它通常带签名和时效参数，写死了很快就过期。

```gherkin
Scenario: 内嵌 H5 活动页能正常下单
  Given 通过 MCP 以 vip_user 身份登录小程序
  When 跳转到 /pages/activity/activity
  And 读取当前页面 web-view 的 URL
  Then 在浏览器中打开该 URL
  And 点击「立即下单」按钮
  And 应该看到下单成功提示
```

两个注意点：

- H5 可能校验 **WeChat UA**，需要时用 `BROWSER_COOKIES` / UA 相关配置伪装
- H5 若依赖 `wx.miniProgram.postMessage` 之类的 JSSDK 桥接，脱离小程序环境会失效，那部分只能靠第 3 档

### 3 · CDP 进模拟器（兜底，脆弱）

开发者工具是 Electron，能带 `--remote-debugging-port` 启动，之后用 Playwright `connectOverCDP` attach，在 targets 里找到 web-view 那个 iframe，就能真的操作 H5 内部。

代价：要用特定 flag 重启开发者工具、target 定位没有稳定契约、工具升级容易破。**只在第 2 档确实覆盖不了（比如要验小程序 ↔ H5 的 postMessage 通信链路）时再用。**

### 4 · 真机 Android CDP

`adb forward tcp:9222 localabstract:xweb_devtools_remote_<pid>`，配合在微信里访问 `http://debugxweb.qq.com/?inspector=true` 开启远程调试。只在必须验证真机行为时用，不适合进 CI。

## 已知边界

- **原生弹窗点不到** —— 授权、支付、订阅消息都是原生 UI，只能 `mp_mock_wx` 绕
- **支付流程**基本无法在模拟器自动化
- **手机号授权解密**依赖真实 `session_key`，模拟器里可能拿不到有效数据
- **CI 需要 macOS / Windows runner** —— 微信开发者工具没有 Linux 版，进不了 Docker

## TODO

- [ ] `mp_screenshot` 目前直接写 `WXMP_PROOF_DIR`，没有按 scenario 分目录，和 Hercules 的 `proofs/<用例>/run_<ts>/` 结构对不齐
- [ ] `mp_mock_wx` 只支持返回固定对象，不支持函数形式的 mock
- [ ] 没有 tabBar / 多页面栈的断言工具
- [ ] `miniprogram-automator` 的 API 签名请对照你安装的版本核实，不同版本有出入
