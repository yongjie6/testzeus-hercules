# 小程序用例模板

批量转换已有 TC 时对着这份写。原则和 `fe_app/packages/e2e/tests/AGENTS.md` 一致：**像用户一样**——按可见文案定位，不写选择器、不写路径、不写等待。

## 骨架

```gherkin
Feature: <模块名，对应原 TC 的分组>

  Scenario: <一句话说清这条验证什么>
    Given 以 logged-in 状态进入小程序
    When 跳转到「我的」页面
    And 点击「退出登录」
    Then 应该看到「确认退出」
```

每条 Scenario 必须**自带前置态**（`Given` 那行），不依赖前一条留下的状态。这和你们 `defineTc` 里每条都调 `flows.setupState()` 是同一个约定。

一个文件可以放多条 Scenario，Hercules 会拆开逐条独立执行——**每条都是全新的 LLM 会话和小程序状态**。

## 词汇表

批量转换时照着这张表替换，左边是你怎么写，右边是它会调什么。

### 前置态

| 写法 | 对应 |
| --- | --- |
| `以 logged-in 状态进入小程序` | `mp_login_as(logged-in)`，登录为 Neo / user-001 |
| `以 guest 状态进入小程序` | `mp_login_as(guest)`，清空 storage |
| `使用「TC-QUOTA-001」的数据` | `mp_use_fixture`，换后端数据场景 |

前两行对应原 TC 的 `flows.setupState('logged-in' \| 'guest')`，第三行对应原 TC 自带的 per-TC fixture 目录。

**`使用「...」的数据` 必须写在前置态前面。** fixture 可能改写用户本身（昵称、user_id、token），登录态是照着当时的 fixture 种进 storage 的，反过来会得到前后不一致的账号：

```gherkin
Scenario: 额度用尽后无法发送
  Given 使用「TC-QUOTA-001」的数据
  And 以 logged-in 状态进入小程序
```

同理，**这种 Scenario 所在的文件不能把前置态放 Background**——拆分时 Background 整段排在前面，等于先按 base 登录再换数据。

场景名就是 `fe_app/packages/e2e/tests/fixtures/` 下的目录名，写错会报错并列出全部可选值。

### 导航

| 写法 | 对应路由 |
| --- | --- |
| `跳转到「首页」` | `home` |
| `跳转到「对话」` | `chat` |
| `跳转到「我的」` | `mine` |
| `跳转到「登录」页面` | `login` |
| `跳转到「量表」页面` | `luxxTest` |
| `跳转到「注销账号」页面` | `unregister` |

中英文都认（`mine` / `我的` 等价）。tabBar 页面自动走 `switchTab`，其余走 `reLaunch`，**不用你指定**。

对应原 TC 的 `page.goto(testRoutes.xxx)`。

### 操作

| 写法 | 对应 |
| --- | --- |
| `点击「退出登录」` | `mp_tap_text`，按可见文案 |
| `在「昵称」输入「新名字」` | `mp_input_text`，按 placeholder 或旁边的标签定位 |
| `输入「新名字」` | 同上；页面只有一个输入框时可省略字段名 |
| `在 .chat-page 上向右滑动，直到看到「对话列表」` | `mp_swipe`，**必须带"直到看到"**，见下方说明 |
| `长按「Today chat」，直到看到「重命名」` | `mp_long_press`，同样**必须带"直到看到"** |
| `长按「Today chat」100 毫秒` | `mp_long_press(duration=...)`，只在验证短按不算长按时这样写 |
| `触发「请输入消息」的 blur 事件` | `mp_trigger(into=...)`，输入框按 placeholder 定位 |
| `完成手机号授权` | `mp_auth_phone`，绕过原生授权弹窗 |
| `查看当前页面` | `mp_snapshot`，列出全部可见文案和控件 |
| `截图记录<说明>` | `mp_screenshot`，存进 proofs |
| `读取剪贴板` | `mp_get_clipboard` |

### 断言

| 写法 | 说明 |
| --- | --- |
| `应该看到「Neo」` | 文案存在 |
| `不应该看到「登录/注册」` | 文案不存在 |
| `应该显示昵称「Neo」` | 同上，加了语义修饰不影响判定 |
| `「发送」应该是不可点击的` | 快照里该控件带 `disabled` 标记 |
| `应该看到不少于 3 个推荐问题` | 数快照里的行 |
| `剪贴板内容应该是「Neo」` | `mp_get_clipboard` 的返回值 |

断言基于 `mp_snapshot` 的输出判断，对应原 TC 的 `expect(page.getByText(...)).toBeVisible()`。

## 三条真实转换示例

### TC-TOPIC-001 —— 直接可转

```ts
await flows.setupState('logged-in')
await page.goto(testRoutes.home)
await expect(page.getByText('Stress check-in?')).toBeVisible()
```

```gherkin
Scenario: 首页展示话题卡片
  Given 以 logged-in 状态进入小程序
  When 跳转到「首页」
  Then 应该看到「Stress check-in?」
```

### TC-AUTH-004 —— 直接可转

```ts
await flows.setupState('logged-in')
await page.goto(testRoutes.mine)
await page.getByText('退出登录', { exact: true }).first().click()
await page.getByRole('button', { name: '确定' }).click()
await expect(page).toHaveURL(testRoutes.home)
```

```gherkin
Scenario: 退出登录后回到首页访客态
  Given 以 logged-in 状态进入小程序
  When 跳转到「我的」页面
  And 点击「退出登录」
  And 点击「确定」
  Then 应该看到「我来试试」
```

注意最后一行的差别：原 TC 断言 URL（`toHaveURL`），Gherkin 里改成断言**页面上的可见文案**。Hercules 也能查当前路由，但断言用户看得见的东西更贴近"像用户一样"。

### TC-USER-002 —— 直接可转

```ts
await page.getByText('Neo').click()
await page.getByRole('textbox').fill(newNickname)
await page.getByRole('button', { name: '保存' }).click()
await expect(page.getByText('昵称已更新')).toBeVisible()
```

```gherkin
Scenario: 修改昵称后展示新昵称
  Given 以 logged-in 状态进入小程序
  When 跳转到「我的」页面
  And 点击「Neo」
  And 输入「TC-USER-002-新昵称」
  And 点击「保存」
  Then 应该看到「昵称已更新」
  And 应该看到「TC-USER-002-新昵称」
```

该页只有一个输入框，所以省略了字段名。页面有多个输入框时写成 `在「昵称」输入「...」`，用 placeholder 或旁边的标签指明。

### TC-CHATLIST-001 —— 可转，但手势要带成功判据

```ts
const root = page.locator('.chat-page')
await root.dispatchEvent('touchstart', { touches: [{ clientX: 0, clientY: 200 }] })
await root.dispatchEvent('touchmove',  { touches: [{ clientX: 80, clientY: 200 }] })
await root.dispatchEvent('touchend',   { touches: [{ clientX: 80, clientY: 200 }] })
await expect(page.getByText('对话列表')).toBeVisible()
```

```gherkin
Scenario: 右滑打开对话列表
  Given 以 logged-in 状态进入小程序
  When 跳转到「对话」页面
  And 在 .chat-page 上向右滑动，直到看到「对话列表」
  Then 应该看到「对话列表」
```

**手势是唯一允许写选择器的地方。** 事件通常绑在没有文案的布局容器上（这里是 `.chat-page`），而编译后的 WXML 里 `bind*` 属性已被剥离，没有任何办法语义化地识别它。你们原 TC 也是直接写 `.chat-page`。

**「直到看到」不是可选的，长按也一样。** 实测滑动单次成功率约 **7/8**——组件对滑动速度敏感，而每次触摸事件都是一次进程间往返，快慢不完全可控；长按同理（验证时 600ms 的长按第 1 次没出菜单，第 2 次才出）。带上成功判据后工具会自动重试。不带的话你的用例会间歇性挂，且很难查。

**这个 app 只有一个页面级手势**，就是上面这个从左边缘向右滑（`chat/pages/index.vue` 的 `pageTouchStartX < 40 && deltaX > 60`）。向左、向上、向下没有任何 handler，滑了不会有反应。原用例写「向左滑」的，先确认它想触发什么。

## 红线

以下写法**不要出现**在用例里：

| 不要写 | 为什么 |
| --- | --- |
| CSS 选择器、class 名、`#id` | 脆弱，且违反你们 `AGENTS.md` 的白名单 |
| 完整页面路径 `/features/mine/pages/index` | 用路由名，路径变了不用改用例 |
| `等待 3 秒` | 等待由工具内建，写死的 sleep 会让用例变慢且不稳 |
| `调用 xxx 接口` / `检查 storage` | 只断言 UI，不查接口/存储，对应原 TC 的"禁止非 UI 证据" |
| 一条 Scenario 里塞多个验证点 | Hercules 按 Scenario 拆分执行，粒度粗了定位不到问题 |

## 当前转换不了的

大部分缺口已补齐。仍然没有对应能力的：

| 缺口 | 影响 |
| --- | --- |
| **web-view 内的 H5** | MCP 够不到内嵌浏览器上下文。用 `mp_webview_url` 读出地址再交给浏览器工具，见 README |
| **原生弹窗** | 支付、订阅消息等；`mp_mock_wx` 能绕开一部分，无法真正交互 |
| **精确坐标手势** | `mp_swipe` 从固定起点出发，依赖真实坐标的组件可能不生效 |
| **多指手势** | 缩放、旋转等无对应能力 |
| **杀进程重进 / 冷启动** | 只能重建页面栈，`App.onLaunch` 不会重跑；automator 只有 `close()`，真冷启动要重启 IDE 项目 |
| **切换网络状态** | app 里没有 `onNetworkStatusChange` / `getNetworkType`，断网只表现为请求失败。要测就换一个把相关接口设成报错的 fixture |

## 转换前的检查清单

- [ ] 前置态写了吗（`Given 以 xxx 状态进入小程序`）
- [ ] 页面用的是路由名不是路径
- [ ] 元素用的是可见文案不是选择器
- [ ] 断言的是用户看得见的东西，不是接口/存储
- [ ] 一条 Scenario 只验证一件事
- [ ] 期望文案和 fixture 对得上（不切场景时是 base：用户 Neo / user-001，话题 `Stress check-in?` 等）
- [ ] 要用别的账号态时，`使用「...」的数据` 写在前置态前面，且该文件的前置态没放在 Background
- [ ] 滑动和长按都带了「直到看到」（除非在验证短按不算长按）
