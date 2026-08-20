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
cd <miniapp>/fe_app/packages/e2e && ./node_modules/.bin/tsx <此目录>/gateway.mts
```

端口 39100/39101 是**编译进小程序包里的**，不能改。`gateway.mts` 用的是 e2e 那套 `startGateway` + `loadMergedFixture`，只是常驻运行——他们的 `test-env.ts` 每次 vitest 跑完就拆掉网关，那个生命周期和 Hercules 对不上。

**2. 开发者工具打开 `dist-scenario`**

必须是指向 `https://localhost:39100` 的包。`dist` 指向真实后端，跑不通。

- 用**界面**导入，别用 `cli open`——该产物的 appid 是 `touristappid`，CLI 会报 `不存在此 AppID (code 10)`
- 详情 → 本地设置 → 勾选**不校验合法域名/TLS/HTTPS 证书**（网关是自签证书，不勾请求全被挡）

**3. 自动化会话**

```bash
/Applications/wechatwebdevtools.app/Contents/MacOS/cli auto --project <...>/dist-scenario --auto-port 9420
```

别重复起——多个会话抢同一端口会让行为变得不确定。

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
WXMP_LOGIN_PROFILES=mcp_servers/wxmp/login_profiles.json
WXMP_PROOF_DIR=opt/proofs
```

> server name 起短点。Hercules 会把工具重命名成 `mmcp_{server}_{tool}`（`mcp_help.py:127`），叫 `wxmp` 得到 `mmcp_wxmp_mp_tap`，够清楚也不啰嗦。

## 环境变量

| 变量 | 默认 | 说明 |
| --- | --- | --- |
| `WXMP_WS_ENDPOINT` | `ws://localhost:9420` | 开发者工具自动化端口 |
| `WXMP_LOGIN_PROFILES` | — | 登录档案：JSON 文件路径，或内联 JSON |
| `WXMP_PROOF_DIR` | cwd | 截图落盘目录，指到 Hercules 的 proofs 下 |
| `WXMP_CONNECT_TIMEOUT` | `30000` | 连接超时（毫秒） |

## 登录

用 `mp_login_as('logged-in')`，不要去驱动登录界面——微信授权弹窗是原生 UI，自动化点不到。

登录态直接复用 e2e 的 fixture：

```bash
WXMP_FIXTURE_DEFAULTS=<miniapp>/fe_app/packages/e2e/tests/fixtures/base/defaults/defaults.json
```

`mp_login_as` 照搬 `tests/utils/storage.ts` 的 `primeStorageFromFixture`：清空 storage → 写 `esm_tokens` 和 `esm_user`（两个都是**对象**，不是字符串）→ reLaunch 到首页。

注意登录态**同时依赖 mock 网关**：只注入 storage 而网关没起，应用拿 token 去校验失败，照样回落到未登录。实测过，现象是「我的」页显示「登录/注册」而不是用户昵称。

fixture 里都是 mock 假值（`access-token-001`），没有真实凭据。

## 工具

| 工具 | 用途 |
| --- | --- |
| `mp_snapshot` | **看见页面**：可见文案 + 控件 + 可操作的 id |
| `mp_navigate` | 命名路由跳转（login/home/chat/mine/…），自动选 switchTab 还是 reLaunch |
| `mp_tap_text` | 按可见文案点击，对齐 getByText |
| `mp_login_as` | `logged-in` / `guest` 前置态 |
| `mp_current_page` | 当前路由与 query |
| `mp_tap` / `mp_input` | 按选择器点击 / 输入（兜底） |
| `mp_get_text` / `mp_element_exists` | 按选择器断言（兜底） |
| `mp_page_data` / `mp_call_method` | 页面 data / 调 Page 方法 |
| `mp_set_storage` / `mp_get_storage` / `mp_clear_storage` | storage 读写清 |
| `mp_mock_wx` / `mp_restore_wx` | mock wx API |
| `mp_wait` / `mp_screenshot` | 等待 / 截图存证 |
| `mp_webview_url` | 读 web-view 的 src，桥接到浏览器测试 |
| `mp_evaluate` | 逃生舱，逻辑层执行 JS |

**优先用 `mp_snapshot` + `mp_tap_text` + 命名路由**，对齐 `fe_app/packages/e2e/tests/AGENTS.md` 的「像用户一样」原则：按可见文案和角色定位，不依赖 class/属性/顺序号。`mp_tap(selector)` 这类按选择器的工具是兜底，正常用例里不该出现。

选择器是 **WXML 选择器**，不是浏览器 DOM 的 CSS 选择器。

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
