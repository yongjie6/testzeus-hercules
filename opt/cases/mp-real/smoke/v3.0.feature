Feature: 小程序连真实后端冒烟

  # 这个套件跑的是 dist-integration 包（编译进真实泳道地址），不是 mp 套件那个
  # dist-scenario（编译进本地 mock 网关地址）。两者不能在同一个 run 里，
  # run-version.sh 会直接拒绝。
  #
  # 只有一条用例，验的是「小程序和真实后端还能不能对上话」这一件事。
  # 单接口契约由 opt/cases/api/ 那五个套件覆盖，页面渲染逻辑由 mp 套件覆盖，
  # 这里两边都不重复，只盯住 mock 按定义测不到的那部分：真实后端算出来的状态，
  # 经过真实登录态，能不能驱动界面走到该去的地方。

  Scenario: TC-ESM30-RS-01 用测试账号登录真实后端并按真实状态落到填写信息页
    # 清空 storage 之后必须重启：小程序的 session 存在内存里且优先于 storage，
    # 只清 storage 再 reLaunch，上一轮的登录态还活着，会直接被路由走。
    Given 清空本地登录态
    And 从「启动页」重启小程序
    # 这个按钮只在 TARO_APP_ENABLE_NARNIA_TEST_AUTH=true 的 integration 包里渲染，
    # 看得到它就说明开发者工具里开的确实是这个包——preflight 判断不了这件事，靠这条断言
    Then 应该看到「使用 Narnia 测试账号」

    # 点下去 app 自己向真实后端注册一个随机测试账号并拿回 token，
    # 全程在小程序内部完成，凭据不经过用例文本
    When 点击「使用 Narnia 测试账号」
    # 落到填写信息页，说明真实后端认了这个新账号并算出
    # entryStage = HOME_ENTRY_STAGE_COMPLETE_USER_INFO，前端也按它路由了
    Then 应该看到「填写信息」
    And 应该看到「认识一下你」
    # 昵称预填成 联调用户xxxxxx，说明注册响应里的账号资料确实回到了界面上，
    # 不只是路由跳对了
    And 应该看到「联调用户」
