Feature: 小程序好问题

  # 由 test-cases/esm-v1-0/v1.0 的 v2 用例源按 docs/gherkin-ready-case-authoring.md 生成。
  # 计数断言不指定计数手段，交给 agent 选 mp_page_data 或 mp_snapshot；
  # 快照对同文案无 id 的重复节点会去重，计数优先走 page data。

  Scenario: TC-ESMV1-024-L1-3 好问题列表按 Score 排序，已应用过的问题明显后置
    Given 以 logged-in 状态进入小程序
    When 跳转到「首页」页面
    And 下拉刷新页面
    Then 两次刷新之间顺序发生变化的问题条数应该为 0

  Scenario: TC-ESMV1-025-L1-1 一键复用跳转对话并新建话题自动发送该问题
    Given 以 logged-in 状态进入小程序
    When 点击「我试试」
    Then 应该看到「对话页」

  Scenario: TC-ESMV1-025-L1-2 一键复用跳转对话并新建话题自动发送该问题
    Given 以 logged-in 状态进入小程序
    When 跳转到「对话」页面
    And 点击「我试试」
    Then 复用前后会话总数的增量应该不少于 1

  Scenario: TC-ESMV1-025-L1-3 一键复用跳转对话并新建话题自动发送该问题
    Given 以 logged-in 状态进入小程序
    When 点击「我试试」
    Then 新会话中与问题原文一致的首条用户消息条数应该不少于 1

  Scenario: TC-ESMV1-025-L1-4 一键复用跳转对话并新建话题自动发送该问题
    Given 以 logged-in 状态进入小程序
    When 点击「我试试」
    And 等待 AI 输出结束
    Then 复用后未经二次点击即产出的 AI 回复条数应该不少于 1

  Scenario: TC-ESMV1-026-L1-2 卡片要素齐备，应用量按真实数加初始值展示且计数刷新后一致
    Given 以 logged-in 状态进入小程序
    When 点击「我试试」
    And 等待 AI 输出结束
    And 跳转到「首页」页面
    And 下拉刷新页面
    Then 卡片展示的应用量应该不少于 1001

  Scenario: TC-ESMV1-026-L1-3 卡片要素齐备，应用量按真实数加初始值展示且计数刷新后一致
    Given 以 logged-in 状态进入小程序
    When 点击「赞」
    And 下拉刷新页面
    Then 卡片展示的赞量应该不少于 565

  Scenario: TC-ESMV1-026-L1-4 卡片要素齐备，应用量按真实数加初始值展示且计数刷新后一致
    Given 以 logged-in 状态进入小程序
    When 点击「赞」
    And 跳转到「我的」页面
    And 跳转到「首页」页面
    Then 重进首页后卡片展示的赞量应该不少于 565

  Scenario: TC-ESMV1-026-L1-5 卡片要素齐备，应用量按真实数加初始值展示且计数刷新后一致
    Given 以 logged-in 状态进入小程序
    When 点击「赞」
    And 点击「赞」
    Then 卡片展示的赞量应该不多于 565

  Scenario: TC-ESMV1-025-L1-0 一键复用跳转对话并新建话题自动发送该问题
    Given 以 logged-in 状态进入小程序
    When 跳转到「首页」页面
    Then 应该看到 「我试试」 或 「一键应用」 或 「应用」 或 「一键复用」
