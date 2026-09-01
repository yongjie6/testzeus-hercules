Feature: 小程序个人中心与注销

  # 由 esm-v3-0/v3.0 的 v2 用例源按 docs/gherkin-ready-case-authoring.md 生成。
  # 计数断言不指定计数手段，交给 agent 选 mp_page_data 或 mp_snapshot；
  # 快照对同文案无 id 的重复节点会去重，计数优先走 page data。

  Scenario: TC-ESM-022-L1-1 个人页报告入口红点消除与头像昵称编辑入口
    Given 以 logged-in 状态进入小程序，后端数据用 TC-REPORT-001
    When 跳转到「我的」页面
    And 点击「我的报告」
    And 点击「返回」
    # 红点是个没有文案、没有 aria-label、不可交互的圆点，mp_snapshot 的 finish() 直接把
    # 这种节点丢掉（mcp_servers/wxmp/wxml.mjs）。所以「快照里没有红点」永远成立，写成
    # assert_ui absent 是一条恒真断言——缺陷在不在它都绿。
    # 这正是 mp_visual_check 的用途：让视觉模型看画面回答，并留下截图供人复核。
    # 代价是它是判读不是确定性断言，不进断言台账；等前端给红点加上 aria-label 或 testId，
    # 这条应该改回 assert_ui absent。
    Then 用 mp_visual_check 确认「我的报告」这一行右侧已经没有未读红点
    And 对当前页面截图存证

  Scenario: TC-ESM-022-L1-2 个人页报告入口红点消除与头像昵称编辑入口
    Given 以 logged-in 状态进入小程序，后端数据用 TC-REPORT-001
    When 跳转到「我的」页面
    And 点击「我的报告」
    And 点击「返回」
    Then 看过报告后个人页渲染出的报告入口个数应该不少于 1

  Scenario: TC-ESM-036-L1-5 我的页两个报告类入口与「我的测评报告」未读红点
    Given 以 logged-in 状态进入小程序，后端数据用 TC-REPORT-001
    When 跳转到「我的」页面
    And 点击「我的报告」
    And 点击「返回」
    # 同上：红点在快照里不存在，只能靠视觉判读。这一条盯的是「我的测评报告」那个入口。
    Then 用 mp_visual_check 确认「我的测评报告」入口上已经没有未读红点
    And 对当前页面截图存证

  Scenario: TC-ESM-036-L1-6 我的页两个报告类入口与「我的测评报告」未读红点
    Given 以 logged-in 状态进入小程序，后端数据用 TC-REPORT-001
    When 跳转到「我的」页面
    And 点击「我的报告」
    And 点击「返回」
    Then 看过报告后我的页上「我的测评报告」入口的个数应该不少于 1
