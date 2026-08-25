Feature: 小程序报告

  # 前瞻性用例：当前 wxmp MCP server 的路由表尚未覆盖本产品线的页面，
  # 语法有效但暂不可执行。见 openspec/prds/esm-v3-0/v3.0/gherkin-map.yaml 顶部说明。

  # 由 esm-v3-0/v3.0 的 v2 用例源按 docs/gherkin-ready-case-authoring.md 生成。
  # 计数断言不指定计数手段，交给 agent 选 mp_page_data 或 mp_snapshot；
  # 快照对同文案无 id 的重复节点会去重，计数优先走 page data。

  Scenario: TC-ESM-023-L1-6 报告页长短两种内容长度下的滚动、吸顶与切 tab 复位
    Given 以 logged-in 状态进入小程序，后端数据用 state-report-many-tabs
    When 点击「下一个 tab」
    Then 吸顶状态下切到相邻 tab 后内容区距顶部的滚动距离应该为 0

  Scenario: TC-ESM-023-L1-7 报告页长短两种内容长度下的滚动、吸顶与切 tab 复位
    Given 以 logged-in 状态进入小程序，后端数据用 state-report-many-tabs
    When 点击「下一个 tab」
    Then 吸顶状态下切到相邻 tab 后内容区渲染出的子标题个数应该不少于 1

  Scenario: TC-ESM-023-L1-8 报告页长短两种内容长度下的滚动、吸顶与切 tab 复位
    Given 以 logged-in 状态进入小程序，后端数据用 state-report-many-tabs
    When 点击「下一个 tab」
    Then 吸顶状态下切到相邻 tab 后视口内的 tab 栏个数应该不少于 1
