# wxmp — 微信小程序 MCP Server

把微信小程序自动化能力接给 Hercules。基于官方 `miniprogram-automator`，通过 MCP stdio 暴露给 `mcp_nav_agent`。

**这是骨架版**，能跑通完整链路，但工具集只覆盖了最常用的路径。

## 设计约束（改之前先看）

**① 只 connect，不 launch。** Hercules 每跑完一条 Scenario 就调 `MCPHelper.destroy()`（见 `simple_hercules.py:1099`），本进程会被反复 spawn / kill —— 一条用例一轮。所以这里绝不能 `automator.launch()` 去拉起 IDE，否则每条用例都要重启一次开发者工具。

**微信开发者工具需要你自己常驻**，本 server 只是个薄客户端。

**② stdout 属于 MCP 协议。** 所有日志走 stderr。这个文件里不要出现 `console.log`。

**③ token 不进 LLM。** `mp_login_as(alias)` 只让模型传别名，真实凭据在本进程内查表解析。原因见下方「安全」。

## 前置条件

1. 打开微信开发者工具并载入项目
2. 设置 → 安全设置 → **服务端口** 打开
3. `npm install`

验证协议层（不需要开发者工具在跑）：

```bash
npm run smoke
```

应该输出 17 个工具，并且在没有开发者工具时给出可读的错误而不是挂住。

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

抄 `login_profiles.example.json` 成 `login_profiles.json`（已 gitignore）：

```json
{
  "vip_user": {
    "storage": { "token": "...", "userId": "1001" },
    "relaunch": "/pages/index/index"
  }
}
```

用例里写「以 vip_user 身份登录」，Agent 调 `mp_login_as("vip_user")`，本进程清空 storage → 写入该档案的键 → reLaunch。**返回值不回显凭据内容。**

### 为什么必须走别名

`test_data/` 的内容会被 `static_ltm.py` 注入每个 Agent 的 system prompt。也就是说写在那里的 token 会**发给 LLM 提供商**，并且**明文落进 `agent_inner_thoughts.json`**（那个文件存完整对话历史）。让模型直接传 token 也一样两头留痕。

别名方案让敏感值从头到尾不进 LLM 上下文。即便如此，也请用短时效的专用测试账号。

## 工具

| 工具 | 用途 |
| --- | --- |
| `mp_navigate` | reLaunch / navigateTo / redirectTo / switchTab / navigateBack |
| `mp_current_page` | 当前路由与 query |
| `mp_tap` / `mp_input` | 点击 / 输入 |
| `mp_get_text` / `mp_element_exists` | 断言用 |
| `mp_page_data` | dump 当前页 data |
| `mp_call_method` | 调 Page 上的方法 |
| `mp_login_as` | 按别名注入登录态 |
| `mp_set_storage` / `mp_get_storage` / `mp_clear_storage` | storage 读写清 |
| `mp_mock_wx` / `mp_restore_wx` | mock wx API |
| `mp_wait` | 等选择器或固定毫秒 |
| `mp_screenshot` | 截图存证 |
| `mp_evaluate` | 逃生舱，逻辑层执行 JS |

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

## 已知边界

- **原生弹窗点不到** —— 授权、支付、订阅消息都是原生 UI，只能 `mp_mock_wx` 绕
- **支付流程**基本无法在模拟器自动化
- **web-view 内嵌 H5 是盲区** —— `page.$()` 拿不到 iframe 内部。登录若走 H5 授权页，这条路不通
- **手机号授权解密**依赖真实 `session_key`，模拟器里可能拿不到有效数据
- **CI 需要 macOS / Windows runner** —— 微信开发者工具没有 Linux 版，进不了 Docker

## TODO

- [ ] `mp_screenshot` 目前直接写 `WXMP_PROOF_DIR`，没有按 scenario 分目录，和 Hercules 的 `proofs/<用例>/run_<ts>/` 结构对不齐
- [ ] `mp_mock_wx` 只支持返回固定对象，不支持函数形式的 mock
- [ ] 没有 tabBar / 多页面栈的断言工具
- [ ] `miniprogram-automator` 的 API 签名请对照你安装的版本核实，不同版本有出入
