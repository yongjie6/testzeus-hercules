Feature: 小程序导航冷启动与登录

  # 前瞻性用例：当前 wxmp MCP server 的路由表尚未覆盖本产品线的页面，
  # 语法有效但暂不可执行。见 openspec/prds/esm-v3-0/v3.0/gherkin-map.yaml 顶部说明。

  # 由 esm-v3-0/v3.0 的 v2 用例源按 docs/gherkin-ready-case-authoring.md 生成。
  # 计数断言不指定计数手段，交给 agent 选 mp_page_data 或 mp_snapshot；
  # 快照对同文案无 id 的重复节点会去重，计数优先走 page data。

  Scenario: TC-ESM-035-L1-0 底部导航收敛为两 tab，问题广场移除，左右滑可切换
    Given 以 logged-in 状态进入小程序，后端数据用 base
    When 跳转到「首页」页面
    Then 底部导航上的 tab 个数应该不少于 2

  Scenario: TC-ESM-035-L1-1 底部导航收敛为两 tab，问题广场移除，左右滑可切换
    Given 以 logged-in 状态进入小程序，后端数据用 base
    When 跳转到「首页」页面
    Then 底部导航上的 tab 个数应该不多于 2

  Scenario: TC-ESM-042-L1-5 全局降级开关打开时的统一故障弹窗与全入口覆盖
    # ⛔ 当前不可执行：全局降级：前端没有读 degradation 字段的实现，弹窗不会出现
    Given 以 logged-in 状态进入小程序
    When 点击「候选关系卡片」
    Then 降级开启时进入关系对话所展示的故障弹窗个数应该不少于 1

  Scenario: TC-ESM-042-L1-6 全局降级开关打开时的统一故障弹窗与全入口覆盖
    # ⛔ 当前不可执行：同上
    Given 以 logged-in 状态进入小程序
    When 跳转到「对话」页面
    Then 降级开启时进入个人对话所展示的故障弹窗个数应该不少于 1

  Scenario: TC-ESM-042-L1-7 全局降级开关打开时的统一故障弹窗与全入口覆盖
    # ⛔ 当前不可执行：同上
    Given 以 logged-in 状态进入小程序
    When 跳转到「我的」页面
    And 点击「我的报告」
    Then 降级开启时进入报告页所展示的故障弹窗个数应该不少于 1

  Scenario: TC-ESM-042-L1-8 全局降级开关打开时的统一故障弹窗与全入口覆盖
    # ⛔ 当前不可执行：同上
    Given 以 logged-in 状态进入小程序
    When 跳转到「我的」页面
    And 点击「我的测量结果」
    Then 降级开启时进入测量结果页所展示的故障弹窗个数应该不少于 1

  Scenario: TC-ESM-042-L1-9 全局降级开关打开时的统一故障弹窗与全入口覆盖
    # ⛔ 当前不可执行：同上
    Given 以 logged-in 状态进入小程序
    When 点击「重试」
    Then 点掉故障弹窗后再次发起操作时弹出的故障弹窗个数应该不少于 1

  Scenario: TC-ESM-043-L1-7 有量表用户的答题旁路防护与个人信息修改不污染量表状态位
    Given 以 logged-in 状态进入小程序，后端数据用 TC-RESULT-001, TC-REPORT-001
    When 跳转到「我的」页面
    And 点击「我的测量结果」
    And 点击「返回」
    Then 有量表账号从测量结果页返回后进入答题页的次数应该为 0

  Scenario: TC-ESM-043-L1-8 有量表用户的答题旁路防护与个人信息修改不污染量表状态位
    Given 以 logged-in 状态进入小程序，后端数据用 TC-RESULT-001, TC-REPORT-001
    When 跳转到「我的」页面
    And 点击「我的测量结果」
    And 点击「返回」
    And 点击「我的报告」
    And 点击「返回」
    Then 有量表账号逐个点开我的页入口后进入答题页的次数应该为 0

  Scenario: TC-ESM-043-L1-14 有量表用户的答题旁路防护与个人信息修改不污染量表状态位
    Given 以 logged-in 状态进入小程序，后端数据用 TC-RESULT-001, TC-REPORT-001
    When 跳转到「我的」页面
    And 点击「我的测量结果」
    And 点击「返回」
    Then 有量表账号在测量结果页点返回后当前页面相对点击前发生变化的次数应该不少于 1

  Scenario: TC-ESM-043-L1-15 有量表用户的答题旁路防护与个人信息修改不污染量表状态位
    Given 以 logged-in 状态进入小程序，后端数据用 TC-RESULT-001, TC-REPORT-001
    When 跳转到「我的」页面
    And 点击「我的测量结果」
    And 点击「返回」
    And 点击「我的报告」
    And 点击「返回」
    Then 有量表账号本轮在我的页逐条点开的入口条数应该不少于 1

  Scenario: TC-ESM-051-L1-11 开屏 Token 恢复与微信静默登录的三条落点
    # ⛔ 当前不可执行：手机号授权是微信原生弹窗，自动化点不到
    Given 以 guest 状态进入小程序
    When 完成手机号授权
    And 等待落点页渲染完成
    Then 注册成功后落地页面上的资料填写表单个数应该不少于 1

  Scenario: TC-ESM-052-L1-3 手机号授权的拒绝、失败与手机号冲突三条异常分支
    # ⛔ 当前不可执行：同上
    Given 以 guest 状态进入小程序
    When 拒绝手机号授权
    And 完成手机号授权
    Then 拒绝授权后再次点击授权按钮唤起的原生授权弹窗个数应该不少于 1

  Scenario: TC-ESM-052-L1-7 手机号授权的拒绝、失败与手机号冲突三条异常分支
    # ⛔ 当前不可执行：同上
    Given 以 guest 状态进入小程序
    When 拒绝手机号授权
    Then 应该看到「需要授权手机号才能登录，可重新点击按钮。」

  Scenario: TC-ESM-052-L1-9 手机号授权的拒绝、失败与手机号冲突三条异常分支
    # ⛔ 当前不可执行：同上
    Given 以 guest 状态进入小程序
    When 完成手机号授权
    Then 不应该看到「授权成功后弹出的 Toast」

  Scenario: TC-ESM-053-L1-3 微信凭据与手机号明文不落到前端
    # ⛔ 当前不可执行：同上
    Given 以 guest 状态进入小程序
    When 完成手机号授权
    And 等待落点页渲染完成
    Then 不应该看到「小程序本地存储中的手机号明文」

  Scenario: TC-ESM-053-L1-4 微信凭据与手机号明文不落到前端
    # ⛔ 当前不可执行：同上
    Given 以 guest 状态进入小程序
    When 完成手机号授权
    And 等待落点页渲染完成
    Then 不应该看到「小程序本地存储中的微信 code」

  Scenario: TC-ESM-053-L1-5 微信凭据与手机号明文不落到前端
    # ⛔ 当前不可执行：同上
    Given 以 guest 状态进入小程序
    When 完成手机号授权
    And 等待落点页渲染完成
    Then 不应该看到「登录后页面路由查询参数中的微信 code」
