Feature: 小程序个人中心与注销

  # 前瞻性用例：当前 wxmp MCP server 的路由表尚未覆盖本产品线的页面，
  # 语法有效但暂不可执行。见 openspec/prds/esm-v3-0/v3.0/gherkin-map.yaml 顶部说明。

  # 由 esm-v3-0/v3.0 的 v2 用例源按 docs/gherkin-ready-case-authoring.md 生成。
  # 计数断言不指定计数手段，交给 agent 选 mp_page_data 或 mp_snapshot；
  # 快照对同文案无 id 的重复节点会去重，计数优先走 page data。

  Scenario: TC-ESM-022-L1-1 个人页报告入口红点消除与头像昵称编辑入口
    Given 以 logged-in 状态进入小程序
    When 跳转到「我的」页面
    And 点击「我的报告」
    And 点击「返回」
    Then 不应该看到「看过报告后个人页报告入口右侧的未读红点」

  Scenario: TC-ESM-022-L1-2 个人页报告入口红点消除与头像昵称编辑入口
    Given 以 logged-in 状态进入小程序
    When 跳转到「我的」页面
    And 点击「我的报告」
    And 点击「返回」
    Then 看过报告后个人页渲染出的报告入口个数应该不少于 1

  Scenario: TC-ESM-036-L1-5 我的页两个报告类入口与「我的测评报告」未读红点
    Given 以 logged-in 状态进入小程序
    When 跳转到「我的」页面
    And 点击「我的报告」
    And 点击「返回」
    Then 不应该看到小红点

  Scenario: TC-ESM-036-L1-6 我的页两个报告类入口与「我的测评报告」未读红点
    Given 以 logged-in 状态进入小程序
    When 跳转到「我的」页面
    And 点击「我的报告」
    And 点击「返回」
    Then 看过报告后我的页上「我的测评报告」入口的个数应该不少于 1
