Feature: 小程序导航冷启动与登录

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

  @blocked:product
  # blocked: 全局降级：前端没有读 degradation 字段的实现，弹窗不会出现
  Scenario: TC-ESM-042-L1-5 全局降级开关打开时的统一故障弹窗与全入口覆盖
    Given 以 logged-in 状态进入小程序
    When 点击「候选关系小周」
    Then 降级开启时进入关系对话所展示的故障弹窗个数应该不少于 1

  @blocked:product
  # blocked: 全局降级：前端没有读 degradation 字段的实现，弹窗不会出现
  Scenario: TC-ESM-042-L1-6 全局降级开关打开时的统一故障弹窗与全入口覆盖
    Given 以 logged-in 状态进入小程序
    When 跳转到「对话」页面
    Then 降级开启时进入个人对话所展示的故障弹窗个数应该不少于 1

  @blocked:product
  # blocked: 全局降级：前端没有读 degradation 字段的实现，弹窗不会出现
  Scenario: TC-ESM-042-L1-7 全局降级开关打开时的统一故障弹窗与全入口覆盖
    Given 以 logged-in 状态进入小程序
    When 跳转到「我的」页面
    And 点击「我的报告」
    Then 降级开启时进入报告页所展示的故障弹窗个数应该不少于 1

  @blocked:product
  # blocked: 全局降级：前端没有读 degradation 字段的实现，弹窗不会出现
  Scenario: TC-ESM-042-L1-8 全局降级开关打开时的统一故障弹窗与全入口覆盖
    Given 以 logged-in 状态进入小程序
    When 跳转到「我的」页面
    And 点击「我的测量结果」
    Then 降级开启时进入测量结果页所展示的故障弹窗个数应该不少于 1

  @blocked:product
  # blocked: 全局降级：前端没有读 degradation 字段的实现，弹窗不会出现
  Scenario: TC-ESM-042-L1-9 全局降级开关打开时的统一故障弹窗与全入口覆盖
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

  @blocked:tooling
  # blocked: 手机号授权是微信原生弹窗，自动化点不到
  Scenario: TC-ESM-051-L1-11 开屏 Token 恢复与微信静默登录的三条落点
    Given 以 guest 状态进入小程序
    When 完成手机号授权
    And 等待落点页渲染完成
    Then 注册成功后落地页面上的资料填写表单个数应该不少于 1

  @blocked:tooling
  # blocked: 手机号授权是微信原生弹窗，自动化点不到
  Scenario: TC-ESM-052-L1-3 手机号授权的拒绝、失败与手机号冲突三条异常分支
    Given 以 guest 状态进入小程序
    When 拒绝手机号授权
    And 完成手机号授权
    Then 拒绝授权后再次点击授权按钮唤起的原生授权弹窗个数应该不少于 1

  @blocked:tooling
  # blocked: 手机号授权是微信原生弹窗，自动化点不到
  Scenario: TC-ESM-052-L1-7 手机号授权的拒绝、失败与手机号冲突三条异常分支
    Given 以 guest 状态进入小程序
    When 拒绝手机号授权
    Then 应该看到「需要授权手机号才能登录，可重新点击按钮。」

  @blocked:tooling
  # blocked: 手机号授权是微信原生弹窗，自动化点不到
  Scenario: TC-ESM-052-L1-9 手机号授权的拒绝、失败与手机号冲突三条异常分支
    Given 以 guest 状态进入小程序
    When 完成手机号授权
    # 原先写的是「不应该看到「授权成功后弹出的 Toast」」——那是对目标的描述，不是屏幕上
    # 的字，执行不了。授权成功的判据落在落点页上，不在一个一闪而过的 Toast 上。
    Then 授权成功后应该落在填写信息页

  @blocked:tooling
  # blocked: 手机号授权是微信原生弹窗，自动化点不到
  Scenario: TC-ESM-053-L1-3 微信凭据与手机号明文不落到前端
    Given 以 guest 状态进入小程序
    When 完成手机号授权
    And 等待落点页渲染完成
    # 手机号明文在不在 storage 里，快照看不见。先用 mp_get_storage 取回整份本地存储，
    # 再拿它当 payload 做 absent 断言——工具早就有，缺的只是把断言写成可执行的形态。
    Then 用 mp_get_storage 取回本地存储，其中不应该出现该账号的手机号明文

  @blocked:tooling
  # blocked: 手机号授权是微信原生弹窗，自动化点不到
  Scenario: TC-ESM-053-L1-4 微信凭据与手机号明文不落到前端
    Given 以 guest 状态进入小程序
    When 完成手机号授权
    And 等待落点页渲染完成
    Then 用 mp_get_storage 取回本地存储，其中不应该出现微信登录 code

  @blocked:tooling
  # blocked: 手机号授权是微信原生弹窗，自动化点不到
  Scenario: TC-ESM-053-L1-5 微信凭据与手机号明文不落到前端
    Given 以 guest 状态进入小程序
    When 完成手机号授权
    And 等待落点页渲染完成
    # 路由参数同理：mp_current_page 能拿到当前路由和 query，拿它当 payload 断言。
    Then 用 mp_current_page 取回当前路由，其 query 参数里不应该出现微信登录 code
