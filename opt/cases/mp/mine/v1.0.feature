Feature: 小程序个人中心与注销

  # 由 test-cases/esm-v1-0/v1.0 的 v2 用例源按 docs/gherkin-ready-case-authoring.md 生成。
  # 计数断言不指定计数手段，交给 agent 选 mp_page_data 或 mp_snapshot；
  # 快照对同文案无 id 的重复节点会去重，计数优先走 page data。

  Scenario: TC-ESMV1-028-L1-3 我的页模块齐备，昵称可改用户 ID 可复制
    Given 以 logged-in 状态进入小程序
    When 跳转到「我的」页面
    And 点击「昵称编辑」
    And 输入「阿」
    And 点击「确定」
    Then 修改后我的页展示新昵称的处数应该不少于 1

  Scenario: TC-ESMV1-028-L1-5 我的页模块齐备，昵称可改用户 ID 可复制
    Given 以 logged-in 状态进入小程序
    When 跳转到「我的」页面
    And 点击「用户ID」
    And 读取剪贴板
    Then 剪贴板内容与页面展示用户 ID 不一致的处数应该为 0

  Scenario: TC-ESMV1-028-L1-6 我的页模块齐备，昵称可改用户 ID 可复制
    Given 以 logged-in 状态进入小程序
    When 跳转到「我的」页面
    And 点击「昵称编辑」
    And 清空「昵称」
    And 点击「确定」
    Then 提交空昵称后页面上的空昵称处数应该为 0

  Scenario: TC-ESMV1-029-L1-0 协议隐私可达，联系我们可复制邮箱，退出登录二次确认
    Given 以 logged-in 状态进入小程序
    When 跳转到「我的」页面
    And 点击「用户协议」
    Then 用户协议页上的正文段落数应该不少于 1

  Scenario: TC-ESMV1-029-L1-1 协议隐私可达，联系我们可复制邮箱，退出登录二次确认
    Given 以 logged-in 状态进入小程序
    When 跳转到「我的」页面
    And 点击「隐私政策」
    Then 隐私政策页上的正文段落数应该不少于 1

  Scenario: TC-ESMV1-029-L1-2 协议隐私可达，联系我们可复制邮箱，退出登录二次确认
    Given 以 logged-in 状态进入小程序
    When 跳转到「我的」页面
    And 点击「联系我们」
    Then 联系我们弹窗背后的半透明遮罩个数应该不少于 1

  Scenario: TC-ESMV1-029-L1-3 协议隐私可达，联系我们可复制邮箱，退出登录二次确认
    Given 以 logged-in 状态进入小程序
    When 跳转到「我的」页面
    And 点击「联系我们」
    And 点击「关闭」
    Then 仍可见的联系我们弹窗个数应该为 0

  Scenario: TC-ESMV1-029-L1-4 协议隐私可达，联系我们可复制邮箱，退出登录二次确认
    Given 以 logged-in 状态进入小程序
    When 跳转到「我的」页面
    And 点击「退出登录」
    And 点击「取消」
    Then 退出登录点击取消后的小程序落点应该为 0

  Scenario: TC-ESMV1-029-L1-5 协议隐私可达，联系我们可复制邮箱，退出登录二次确认
    Given 以 logged-in 状态进入小程序
    When 跳转到「我的」页面
    And 点击「退出登录」
    And 点击「确定」
    Then 应该看到「登录页」

  Scenario: TC-ESMV1-028-L1-0 我的页模块齐备，昵称可改用户 ID 可复制
    Given 以 logged-in 状态进入小程序
    When 跳转到「我的」页面
    Then 应该看到 「内测受邀用户」 或 「首批内测用户」

  Scenario: TC-ESMV1-028-L1-1 我的页模块齐备，昵称可改用户 ID 可复制
    Given 以 logged-in 状态进入小程序
    When 跳转到「我的」页面
    Then 应该看到 「注销账号」 或 「删除账号」
