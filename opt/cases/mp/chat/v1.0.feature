Feature: 小程序对话

  # 由 test-cases/esm-v1-0/v1.0 的 v2 用例源按 docs/gherkin-ready-case-authoring.md 生成。
  # 计数断言不指定计数手段，交给 agent 选 mp_page_data 或 mp_snapshot；
  # 快照对同文案无 id 的重复节点会去重，计数优先走 page data。

  Scenario: TC-ESMV1-008-L1-5 对话页初始态部件齐备且推荐问题可直接发起一轮对话
    Given 以 logged-in 状态进入小程序
    When 跳转到「对话」页面
    And 点击「推荐问题」
    Then 与被点推荐问题同文的用户消息条数应该不少于 1

  Scenario: TC-ESMV1-008-L1-6 对话页初始态部件齐备且推荐问题可直接发起一轮对话
    Given 以 logged-in 状态进入小程序
    When 点击「推荐问题」
    Then 仍展示的推荐问题条数应该为 0

  Scenario: TC-ESMV1-009-L1-0 文本输入 2000 字上限与 1800 字计字器阈值
    Given 以 logged-in 状态进入小程序
    When 输入「1801 字文本」
    Then 输入框右下角的计字器个数应该不少于 1

  Scenario: TC-ESMV1-009-L1-1 文本输入 2000 字上限与 1800 字计字器阈值
    Given 以 logged-in 状态进入小程序
    When 输入「1800 字文本」
    Then 输入框右下角的计字器个数应该为 0

  Scenario: TC-ESMV1-009-L1-2 文本输入 2000 字上限与 1800 字计字器阈值
    Given 以 logged-in 状态进入小程序
    When 输入「2000 字文本」
    And 输入「压」
    Then 输入框内超出 2000 的字符数应该为 0

  Scenario: TC-ESMV1-009-L1-3 文本输入 2000 字上限与 1800 字计字器阈值
    Given 以 logged-in 状态进入小程序
    When 输入「2000 字文本」
    And 输入「压」
    Then 应该看到「超出字数限制」

  Scenario: TC-ESMV1-009-L1-5 文本输入 2000 字上限与 1800 字计字器阈值
    Given 以 logged-in 状态进入小程序
    When 清空「对话输入框」
    Then 可见的发送按钮个数应该为 0

  Scenario: TC-ESMV1-009-L1-6 文本输入 2000 字上限与 1800 字计字器阈值
    Given 以 logged-in 状态进入小程序
    When 输入「压」
    Then 可用的发送按钮个数应该不少于 1

  Scenario: TC-ESMV1-010-L1-0 输入框最多伸展 5 行，超出后高度固定
    Given 以 logged-in 状态进入小程序
    When 输入「5 行文本」
    Then 输入框可见行数应该不多于 5

  Scenario: TC-ESMV1-010-L1-1 输入框最多伸展 5 行，超出后高度固定
    Given 以 logged-in 状态进入小程序
    When 输入「6 行文本」
    Then 输入框可见行数应该不多于 5

  Scenario: TC-ESMV1-013-L1-0 AI 气泡四态流转且工具栏按态显隐
    Given 以 logged-in 状态进入小程序
    When 输入「压力有点大」
    And 点击「发送」
    Then 应该看到「思考态」

  Scenario: TC-ESMV1-013-L1-5 AI 气泡四态流转且工具栏按态显隐
    Given 以 logged-in 状态进入小程序
    When 输入「压力有点大」
    And 点击「发送」
    And 点击「停止」
    Then 该气泡内保留的文字段数应该不少于 1

  Scenario: TC-ESMV1-013-L1-6 AI 气泡四态流转且工具栏按态显隐
    Given 以 logged-in 状态进入小程序
    When 输入「压力有点大」
    And 点击「发送」
    And 点击「停止」
    Then 可见的气泡工具栏个数应该不少于 1

  Scenario: TC-ESMV1-014-L1-0 重新生成第 N 条后清空该节点之后的全部对话
    Given 以 logged-in 状态进入小程序
    When 点击「重新生成」
    And 等待 AI 输出结束
    Then 该位置产出的新 AI 回复条数应该不少于 1

  Scenario: TC-ESMV1-014-L1-1 重新生成第 N 条后清空该节点之后的全部对话
    Given 以 logged-in 状态进入小程序
    When 点击「重新生成」
    Then 仍可见的第三轮用户提问条数应该为 0

  Scenario: TC-ESMV1-014-L1-2 重新生成第 N 条后清空该节点之后的全部对话
    Given 以 logged-in 状态进入小程序
    When 点击「重新生成」
    Then 对话区的消息条数应该不多于 4

  Scenario: TC-ESMV1-015-L1-0 编辑用户提问后重新发送并清空后续，取消编辑无副作用
    Given 以 logged-in 状态进入小程序
    When 点击「编辑」
    Then 编辑态下预填原文的输入框个数应该不少于 1

  Scenario: TC-ESMV1-015-L1-1 编辑用户提问后重新发送并清空后续，取消编辑无副作用
    Given 以 logged-in 状态进入小程序
    When 点击「编辑」
    Then 编辑态下该气泡可见的工具栏个数应该为 0

  Scenario: TC-ESMV1-015-L1-2 编辑用户提问后重新发送并清空后续，取消编辑无副作用
    Given 以 logged-in 状态进入小程序
    When 点击「编辑」
    And 输入「我最近人际关系有点紧张」
    And 点击「取消」
    Then 第二轮用户提问与原文一致的条数应该不少于 1

  Scenario: TC-ESMV1-015-L1-3 编辑用户提问后重新发送并清空后续，取消编辑无副作用
    Given 以 logged-in 状态进入小程序
    When 点击「编辑」
    And 输入「我最近人际关系有点紧张」
    And 点击「取消」
    Then 对话区的消息条数应该不少于 6

  Scenario: TC-ESMV1-015-L1-5 编辑用户提问后重新发送并清空后续，取消编辑无副作用
    Given 以 logged-in 状态进入小程序
    When 点击「编辑」
    And 输入「我最近人际关系有点紧张」
    And 点击「确定」
    And 等待 AI 输出结束
    Then 对话区的消息条数应该不多于 4

  Scenario: TC-ESMV1-016-L1-0 AI 回复的 Markdown 元素正确渲染且可完整复制
    Given 以 logged-in 状态进入小程序
    When 输入「请用带标题和有序列表的格式讲讲我的沟通风格」
    And 点击「发送」
    And 等待 AI 输出结束
    Then AI 回复中渲染为标题的元素个数应该不少于 1

  Scenario: TC-ESMV1-016-L1-1 AI 回复的 Markdown 元素正确渲染且可完整复制
    Given 以 logged-in 状态进入小程序
    When 输入「请用带标题和有序列表的格式讲讲我的沟通风格」
    And 点击「发送」
    And 等待 AI 输出结束
    Then AI 回复中渲染为列表的元素个数应该不少于 1

  Scenario: TC-ESMV1-016-L1-2 AI 回复的 Markdown 元素正确渲染且可完整复制
    Given 以 logged-in 状态进入小程序
    When 输入「请用带标题和有序列表的格式讲讲我的沟通风格」
    And 点击「发送」
    And 等待 AI 输出结束
    Then 不应该看到「AI 回复正文中的 Markdown 源码符号（# ## - 1. ** *）」

  Scenario: TC-ESMV1-016-L1-3 AI 回复的 Markdown 元素正确渲染且可完整复制
    Given 以 logged-in 状态进入小程序
    When 点击「复制」
    And 读取剪贴板
    Then 复制内容中缺失的正文段落数应该为 0

  Scenario: TC-ESMV1-017-L1-3 向下拖拽一屏出现回到底部按钮，点赞点踩互斥可回显
    Given 以 logged-in 状态进入小程序
    When 点击「赞」
    And 点击「踩」
    Then 先赞后踩之后处于选中态的「赞」入口个数应该为 0

  Scenario: TC-ESMV1-017-L1-4 向下拖拽一屏出现回到底部按钮，点赞点踩互斥可回显
    Given 以 logged-in 状态进入小程序
    When 点击「踩」
    And 跳转到「首页」页面
    And 跳转到「对话」页面
    Then 重进会话后与最后一次操作一致的反馈态个数应该不少于 1

  Scenario: TC-ESMV1-018-L1-0 敏感词前置过滤强阻断：清空内容、不发 AI 请求、不扣次数
    Given 以 logged-in 状态进入小程序
    When 输入「敏感样本」
    And 点击「发送」
    Then 应该看到「您的内容包含敏感信息，请修改后重新输入。」

  Scenario: TC-ESMV1-018-L1-1 敏感词前置过滤强阻断：清空内容、不发 AI 请求、不扣次数
    Given 以 logged-in 状态进入小程序
    When 输入「敏感样本」
    And 点击「发送」
    Then 命中后输入框内残留的字符数应该为 0

  Scenario: TC-ESMV1-018-L1-3 敏感词前置过滤强阻断：清空内容、不发 AI 请求、不扣次数
    Given 以 logged-in 状态进入小程序
    When 输入「敏感样本」
    And 点击「发送」
    Then 命中敏感词后新增的用户消息条数应该为 0

  Scenario: TC-ESMV1-018-L1-6 敏感词前置过滤强阻断：清空内容、不发 AI 请求、不扣次数
    Given 以 logged-in 状态进入小程序
    When 输入「我最近工作压力很大，帮我看看我的动机」
    And 点击「发送」
    And 等待 AI 输出结束
    Then 正常内容发送后产出的 AI 回复条数应该不少于 1

  Scenario: TC-ESMV1-019-L1-0 本期不做输出侧后置审查
    Given 以 logged-in 状态进入小程序
    When 输入「探测语料」
    And 点击「发送」
    And 等待 AI 输出结束
    Then 不应该看到「内容因安全原因已被拦截」

  Scenario: TC-ESMV1-019-L1-1 本期不做输出侧后置审查
    Given 以 logged-in 状态进入小程序
    When 输入「探测语料」
    And 点击「发送」
    Then 流式输出过程中已上屏后又消失的文字段数应该为 0

  Scenario: TC-ESMV1-020-L1-0 会话标题由 AI 生成且重命名后各处同步
    Given 以 logged-in 状态进入小程序
    When 点击「新会话」
    And 输入「帮我看看我在团队里的沟通风格」
    And 点击「发送」
    And 等待 AI 输出结束
    Then 首轮问答后该会话的非空标题个数应该不少于 1

  Scenario: TC-ESMV1-020-L1-1 会话标题由 AI 生成且重命名后各处同步
    Given 以 logged-in 状态进入小程序
    When 触发「对话列表」的 longpress 事件
    And 点击「重命名」
    Then 应该看到「重命名会话」

  Scenario: TC-ESMV1-020-L1-2 会话标题由 AI 生成且重命名后各处同步
    Given 以 logged-in 状态进入小程序
    When 触发「对话列表」的 longpress 事件
    And 点击「重命名」
    Then 重命名弹窗中预填了当前名称的输入框个数应该不少于 1

  Scenario: TC-ESMV1-020-L1-3 会话标题由 AI 生成且重命名后各处同步
    Given 以 logged-in 状态进入小程序
    When 触发「对话列表」的 longpress 事件
    And 点击「重命名」
    And 输入「沟通风格复盘」
    And 点击「确定」
    Then 重命名后列表项展示新名称的条数应该不少于 1

  Scenario: TC-ESMV1-022-L1-0 侧边栏呼出与收起，长按弹出三项操作
    Given 以 logged-in 状态进入小程序
    When 跳转到「对话」页面
    And 点击「对话列表」
    Then 可见的侧边栏个数应该不少于 1

  Scenario: TC-ESMV1-022-L1-1 侧边栏呼出与收起，长按弹出三项操作
    Given 以 logged-in 状态进入小程序
    When 点击页面空白处
    Then 可见的侧边栏个数应该为 0

  Scenario: TC-ESMV1-022-L1-2 侧边栏呼出与收起，长按弹出三项操作
    Given 以 logged-in 状态进入小程序
    When 触发「对话列表」的 longpress 事件
    Then 应该看到 「删除」 或 「重命名」 或 「取消」

  Scenario: TC-ESMV1-022-L1-4 侧边栏呼出与收起，长按弹出三项操作
    Given 以 logged-in 状态进入小程序
    When 触发「对话列表」的 longpress 事件
    And 点击「取消」
    Then 名称发生变化的会话条数应该为 0

  Scenario: TC-ESMV1-022-L1-5 侧边栏呼出与收起，长按弹出三项操作
    Given 以 logged-in 状态进入小程序
    When 跳转到「对话」页面
    And 点击「新会话」
    Then 新会话中的消息条数应该为 0

  Scenario: TC-ESMV1-023-L1-0 删除对话需二次确认且删除后不可恢复
    Given 以 logged-in 状态进入小程序
    When 触发「对话列表」的 longpress 事件
    And 点击「删除」
    Then 应该看到「永久删除对话」

  Scenario: TC-ESMV1-023-L1-1 删除对话需二次确认且删除后不可恢复
    Given 以 logged-in 状态进入小程序
    When 触发「对话列表」的 longpress 事件
    And 点击「删除」
    Then 应该看到「删除后，该对话将不可恢复。确认删除吗？」

  Scenario: TC-ESMV1-023-L1-2 删除对话需二次确认且删除后不可恢复
    Given 以 logged-in 状态进入小程序
    When 触发「对话列表」的 longpress 事件
    And 点击「删除」
    And 点击「取消」
    Then 该会话仍在列表中的条数应该不少于 1

  Scenario: TC-ESMV1-023-L1-3 删除对话需二次确认且删除后不可恢复
    Given 以 logged-in 状态进入小程序
    When 触发「对话列表」的 longpress 事件
    And 点击「删除」
    And 点击「确定」
    Then 该会话仍在列表中的条数应该为 0

  Scenario: TC-ESMV1-023-L1-5 删除对话需二次确认且删除后不可恢复
    Given 以 logged-in 状态进入小程序
    When 跳转到「对话」页面
    And 触发「对话列表」的 longpress 事件
    And 点击「删除」
    And 点击「确定」
    Then 删除当前会话后仍停留在已删除会话上的页面数应该为 0

  Scenario: TC-ESMV1-008-L1-0 对话页初始态部件齐备且推荐问题可直接发起一轮对话
    Given 以 logged-in 状态进入小程序
    When 跳转到「对话」页面
    Then 应该看到「Hi！我是小量」

  Scenario: TC-ESMV1-008-L1-1 对话页初始态部件齐备且推荐问题可直接发起一轮对话
    Given 以 logged-in 状态进入小程序
    When 跳转到「对话」页面
    Then 应该看到「你可以这样问」

  Scenario: TC-ESMV1-008-L1-2 对话页初始态部件齐备且推荐问题可直接发起一轮对话
    Given 以 logged-in 状态进入小程序
    When 跳转到「对话」页面
    Then 对话页初始态的推荐问题条数应该不少于 3

  Scenario: TC-ESMV1-008-L1-3 对话页初始态部件齐备且推荐问题可直接发起一轮对话
    Given 以 logged-in 状态进入小程序
    When 跳转到「对话」页面
    Then 对话页顶部的新建对话入口个数应该不少于 1

  Scenario: TC-ESMV1-008-L1-4 对话页初始态部件齐备且推荐问题可直接发起一轮对话
    Given 以 logged-in 状态进入小程序
    When 跳转到「对话」页面
    Then 应该看到 「发信息或按住说话」 或 「请输入您的问题...」
