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

对应原 TC 的 `flows.setupState('logged-in' \| 'guest')`。

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
| `查看当前页面` | `mp_snapshot`，列出全部可见文案和控件 |
| `截图记录<说明>` | `mp_screenshot`，存进 proofs |

### 断言

| 写法 | 说明 |
| --- | --- |
| `应该看到「Neo」` | 文案存在 |
| `不应该看到「登录/注册」` | 文案不存在 |
| `应该显示昵称「Neo」` | 同上，加了语义修饰不影响判定 |

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

### TC-USER-002 —— 现在转不了

```ts
await page.getByRole('textbox').fill(newNickname)
await page.getByRole('button', { name: '保存' }).click()
```

缺 `getByRole('textbox').fill()` 的等价物。现有的 `mp_input` 需要 WXML 选择器，而选择器是这份规范明令禁止的。**见下方缺口清单。**

## 红线

以下写法**不要出现**在用例里：

| 不要写 | 为什么 |
| --- | --- |
| CSS 选择器、class 名、`#id` | 脆弱，且违反你们 `AGENTS.md` 的白名单 |
| 完整页面路径 `/features/mine/pages/index` | 用路由名，路径变了不用改用例 |
| `等待 3 秒` | 等待由工具内建，写死的 sleep 会让用例变慢且不稳 |
| `调用 xxx 接口` / `检查 storage` | 只断言 UI，不查接口/存储，对应原 TC 的"禁止非 UI 证据" |
| 一条 Scenario 里塞多个验证点 | Hercules 按 Scenario 拆分执行，粒度粗了定位不到问题 |

## 当前转换不了的（工具缺口）

从你们现有 TC 里筛出来的，转换前需要先补工具：

| 缺口 | 影响的 TC 模式 | 现状 |
| --- | --- | --- |
| **按语义输入文本** | `getByRole('textbox').fill()`，如 TC-USER-002 改昵称 | `mp_input` 只收选择器，不可用 |
| **手势** | `dispatchEvent('touchstart'/'touchmove'/'touchend')`，如 TC-CHATLIST-001 侧滑 | 完全没有对应工具 |
| **触发自定义事件** | `locator.dispatchEvent(type, detail)` | 无 |
| **手机号授权** | `btn.triggerGetPhoneNumber({ code })` | 无，需要专门包一层 |
| **剪贴板断言** | `toHaveClipboardText()` | 无 |

建议**先转能转的**（纯浏览 + 点击 + 文案断言那一类，占比应该不低），把需要上述能力的挑出来单独列一批，补完工具再转。

## 转换前的检查清单

- [ ] 前置态写了吗（`Given 以 xxx 状态进入小程序`）
- [ ] 页面用的是路由名不是路径
- [ ] 元素用的是可见文案不是选择器
- [ ] 断言的是用户看得见的东西，不是接口/存储
- [ ] 一条 Scenario 只验证一件事
- [ ] 期望文案和 fixture 对得上（mock 网关的固定数据：用户 Neo / user-001，话题 `Stress check-in?` 等）
