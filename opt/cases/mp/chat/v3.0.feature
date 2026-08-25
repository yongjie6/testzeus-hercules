Feature: 小程序对话

  # 前瞻性用例：当前 wxmp MCP server 的路由表尚未覆盖本产品线的页面，
  # 语法有效但暂不可执行。见 openspec/prds/esm-v3-0/v3.0/gherkin-map.yaml 顶部说明。

  # 由 esm-v3-0/v3.0 的 v2 用例源按 docs/gherkin-ready-case-authoring.md 生成。
  # 计数断言不指定计数手段，交给 agent 选 mp_page_data 或 mp_snapshot；
  # 快照对同文案无 id 的重复节点会去重，计数优先走 page data。

  Scenario: TC-ESM-033-L1-0 关系对话初次进入的开场、问询与推荐问题转化
    Given 以 logged-in 状态进入小程序
    When 点击「候选关系卡片」
    Then 初次进入关系对话后 AI 侧展示的开场消息条数应该不少于 1

  Scenario: TC-ESM-033-L1-1 关系对话初次进入的开场、问询与推荐问题转化
    Given 以 logged-in 状态进入小程序
    When 点击「候选关系卡片」
    Then 应该看到「对方报告」

  Scenario: TC-ESM-033-L1-2 关系对话初次进入的开场、问询与推荐问题转化
    Given 以 logged-in 状态进入小程序
    When 点击「候选关系卡片」
    Then 应该看到「说说你们的情况好吗？请问你们是什么关系呢？」

  Scenario: TC-ESM-033-L1-4 关系对话初次进入的开场、问询与推荐问题转化
    Given 以 logged-in 状态进入小程序
    When 点击「推荐问题」
    Then 不应该看到「被点击的那条推荐问题在推荐区里的残留」

  Scenario: TC-ESM-033-L1-5 关系对话初次进入的开场、问询与推荐问题转化
    Given 以 logged-in 状态进入小程序
    When 点击「推荐问题」
    Then 会话中与所点推荐问题正文一致的用户提问气泡条数应该不少于 1

  Scenario: TC-ESM-033-L1-6 关系对话初次进入的开场、问询与推荐问题转化
    Given 以 logged-in 状态进入小程序
    When 点击「候选关系卡片」
    Then 不应该看到「对方报告」

  Scenario: TC-ESM-033-L1-7 关系对话初次进入的开场、问询与推荐问题转化
    Given 以 logged-in 状态进入小程序
    When 点击「候选关系卡片」
    Then 对方报告不可用时初次进入关系对话所展示的关系问询消息条数应该不少于 1

  Scenario: TC-ESM-039-L1-0 对话页会话恢复、报告跳转、身份标签与右滑返回
    Given 以 logged-in 状态进入小程序
    When 跳转到「对话」页面
    And 输入「压力有点大」
    And 点击「发送」
    And 跳转到「首页」页面
    And 跳转到「对话」页面
    Then 再次进入个人对话后展示的历史消息条数应该不少于 6

  Scenario: TC-ESM-039-L1-1 对话页会话恢复、报告跳转、身份标签与右滑返回
    Given 以 logged-in 状态进入小程序
    When 点击「新会话」
    Then 点击「新建对话」后所进入会话中的历史消息条数应该为 0

  Scenario: TC-ESM-039-L1-2 对话页会话恢复、报告跳转、身份标签与右滑返回
    Given 以 logged-in 状态进入小程序
    When 点击「新会话」
    And 点击「会话」
    Then 从新会话切回原会话后展示的历史消息条数应该不少于 6

  Scenario: TC-ESM-039-L1-3 对话页会话恢复、报告跳转、身份标签与右滑返回
    Given 以 logged-in 状态进入小程序
    When 点击「报告」
    And 点击「返回」
    Then 从自己的报告返回后所停留页面上的本次对话历史消息条数应该不少于 6

  Scenario: TC-ESM-039-L1-4 对话页会话恢复、报告跳转、身份标签与右滑返回
    Given 以 logged-in 状态进入小程序
    When 点击「报告」
    Then 点击他人报告后新打开的页面数应该不少于 1

  Scenario: TC-ESM-039-L1-5 对话页会话恢复、报告跳转、身份标签与右滑返回
    Given 以 logged-in 状态进入小程序
    When 点击「报告」
    Then 他人报告页顶部导航上的导航项个数应该不少于 3

  Scenario: TC-ESM-039-L1-6 对话页会话恢复、报告跳转、身份标签与右滑返回
    Given 以 logged-in 状态进入小程序
    When 点击「报告」
    Then 他人报告页顶部导航上的导航项个数应该不多于 3

  Scenario: TC-ESM-039-L1-11 对话页会话恢复、报告跳转、身份标签与右滑返回
    Given 以 logged-in 状态进入小程序
    When 跳转到「对话」页面
    Then summary 尚未生成时个人对话页上出现的后端报错提示条数应该为 0

  Scenario: TC-ESM-039-L1-12 对话页会话恢复、报告跳转、身份标签与右滑返回
    Given 以 logged-in 状态进入小程序
    When 跳转到「对话」页面
    Then summary 尚未生成时个人对话页渲染出的消息或等待态元素个数应该不少于 1

  Scenario: TC-ESM-039-L1-13 对话页会话恢复、报告跳转、身份标签与右滑返回
    Given 以 logged-in 状态进入小程序
    When 点击「新会话」
    Then 点击「新建对话」后新会话中展示的推荐问题条数应该不少于 1

  Scenario: TC-ESM-044-L1-0 长会话多轮流式输出的崩溃、顺序、截断与重进一致性
    Given 以 logged-in 状态进入小程序
    When 输入「压力有点大」
    And 点击「发送」
    And 等待 AI 输出结束
    Then 三十轮压测过程中小程序的崩溃次数应该为 0

  Scenario: TC-ESM-044-L1-1 长会话多轮流式输出的崩溃、顺序、截断与重进一致性
    Given 以 logged-in 状态进入小程序
    When 输入「压力有点大」
    And 点击「发送」
    And 等待 AI 输出结束
    Then 三十轮压测中实际完成的问答轮次数应该不少于 30

  Scenario: TC-ESM-044-L1-2 长会话多轮流式输出的崩溃、顺序、截断与重进一致性
    Given 以 logged-in 状态进入小程序
    When 输入「压力有点大」
    And 点击「发送」
    And 等待 AI 输出结束
    Then 三十轮中用户提问气泡排在其对应 AI 回复之下的轮次数应该为 0

  Scenario: TC-ESM-044-L1-3 长会话多轮流式输出的崩溃、顺序、截断与重进一致性
    Given 以 logged-in 状态进入小程序
    When 输入「请尽可能详细地展开说说」
    And 点击「发送」
    And 等待 AI 输出结束
    Then 超长回复中渲染正文短于接口返回正文的轮次数应该为 0

  Scenario: TC-ESM-044-L1-4 长会话多轮流式输出的崩溃、顺序、截断与重进一致性
    Given 以 logged-in 状态进入小程序
    When 输入「请尽可能详细地展开说说」
    And 点击「发送」
    And 等待 AI 输出结束
    Then 三十轮中接近输出上限的超长回复轮次数应该不少于 3

  Scenario: TC-ESM-044-L1-5 长会话多轮流式输出的崩溃、顺序、截断与重进一致性
    Given 以 logged-in 状态进入小程序
    When 跳转到「首页」页面
    And 跳转到「对话」页面
    Then 三十轮压测后退出再进入所展示的历史消息条数应该不少于 60

  Scenario: TC-ESM-044-L1-6 长会话多轮流式输出的崩溃、顺序、截断与重进一致性
    Given 以 logged-in 状态进入小程序
    When 跳转到「首页」页面
    And 跳转到「对话」页面
    Then 三十轮压测后退出再进入时与退出前顺序不一致的消息条数应该为 0
