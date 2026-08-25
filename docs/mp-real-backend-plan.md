# 小程序用例跑真实后端：环境分型与账号池方案

> **状态（2026-08-25）：环境分型已落地，账号池暂缓。**
>
> 落地的是文档下半部分的"环境分型"：`mp-real` 类型、`preflight --mode real`、
> 混跑拦截，加上一条冒烟用例 `TC-ESM30-RS-01`（已进核心回归集，实跑通过）。
>
> **账号池没做**，因为算完账不划算——见文末「为什么账号池暂缓」。冒烟用例用的是
> app 自带的「使用 Narnia 测试账号」按钮，凭据在小程序内部产生，既不需要池子，
> 也不需要新工具往 storage 注 token。
>
> 下半部分关于账号池的设计仍然有效，等触发条件满足了再按它做。

小程序用例现在只有一种跑法：`dist-scenario` 包 + mock 网关，数据来自 fixture。这份方案加第二种：
`dist-integration` 包 + 真实泳道，数据来自预先构造好的真实账号池。

两种跑法**不能混在同一个 run 里**，也不能靠运行时猜——本方案的核心就是把"这条用例该用哪个包、
哪套数据"变成三个静态检查点，任何一个不满足都在跑之前就拒绝启动。

## 为什么需要第二种跑法

mock 网关那套测的是"给定这份数据，页面渲染对不对"。它测不了"页面接的是不是活的后端"——
`dist-scenario` 的 JS 里 API 地址是编译期写死的 `https://localhost:39100`，跟真实服务毫无关系。

少数用例的价值恰恰在后者：真实登录态能不能被识别、真实 `entryStage` 推进对不对、真实报告内容
渲染得出来吗。这类用例数量不多（此前盘过大约 5 条真·C 类），但 mock 覆盖不到。

## 三个静态检查点

不做运行时环境自适应——那是给框架加一种它现在没有的能力。改成三处各自能给出确定性答案的检查：

| 检查点 | 什么时候 | 挡住什么 |
| --- | --- | --- |
| 用例放在哪个目录 | 写用例时 | 用例作者选错数据源 |
| `run-version.sh` 的 preflight | 跑之前 | 构建产物不对、域名校验没勾、泳道挂了 |
| `mp_use_real_account` 注入后自验 | 用的时候 | 开发者工具里开着的是另一个包 |

第三层不能省。前两层都无法确定性地知道"开发者工具当前打开的到底是哪个项目"——`cli` 不暴露这个，
对已绑定的端口重跑 `cli auto` 会报成功但不重绑（实测过）。只有注入之后看它认不认，才是真答案。

## 一、环境分型

### 目录

真实后端用例单独一类，和 `api` / `web` / `mp` 平级：

```
opt/cases/mp-real/<module>/v3.0.feature
opt/cases/mp-real/<module>/test_data/test_data.txt
```

这符合 `assemble_version.py` 自己写的分型标准——类型是目录而不是命名约定，因为
"三种类型要求的环境不一样，能不能单独跑起来是前提，不是图方便"。`mp-real` 要的环境
（`dist-integration` + 健康泳道）和 `mp`（`dist-scenario` + mock 网关）确实不同。

`suite_name()` 会产出 `mp-real-<module>`，注意它同时匹配 `mp-*` 通配。凡是要区分两者的分支
一律用 `case` 语句、`mp-real-*` 放在 `mp-*` 前面，不要写成一串 `if`——顺序依赖的通配符是
经典的 bug 来源。

### preflight 分两种模式

`preflight.mjs` 加 `--mode mock|real`，默认 `mock` 保持现有行为不变。

**mock 模式**（现状）：39100 在监听 → 连自动化端口 → 截图验渲染层。

**real 模式**：
1. **静态检查构建产物**——`grep` 期望的 build 目录下的 `common.js` 里是不是真实后端地址。
   两个包在这一点上是互斥的，一次 grep 就能确定性区分，不需要任何运行时探测，也不需要
   开发者工具在跑。
2. 连自动化端口 → 截图验渲染层（同 mock）。
3. **运行时探测**——`mp.evaluate` 里发一次 `wx.request` 打真实后端的 `runtime/state`，要求 200。
   这一步同时验证了三件事：域名校验勾了、泳道活着、`X-Lane` 头能路由到位。

第 1 步和第 3 步都不能证明"开发者工具打开的就是这个包"——第 1 步只看磁盘，第 3 步在域名校验
关掉之后任何包都能打通。这个残留缺口由第三层（工具自验）兜底。

## 二、账号池

### 只读态进池子，会被改写的态不进

| 类型 | 例子 | 进池子？ |
| --- | --- | --- |
| 只读前置 | 已完成量表、报告已就绪、有候选关系 | ✅ 建一次复用，正是省成本的地方 |
| 会被用例改写 | 提交反馈、设备注名、占席位、发消息 | ❌ 上一条用例的痕迹会污染下一条 |
| 状态本身是断言对象 | "首次进入关系对话推两条开场消息" | ❌ "首次"用过一次就不成立了 |

后两类仍然现造。池子管"读"，现造管"写"。

这条分类线和 mock 网关那套 fixture 片段是同一条——`base`/`TC-*`/`state-*` 全是只读快照，
没有一个是"用例会改写"的。

### 池子文件

`opt/.accounts/pool.json`。被 `.gitignore` 的 `**/opt/*` 覆盖（`opt/cases` 是唯一的例外），
但**建议再补一条显式规则**：现在的忽略规则靠"排除整个 opt 再放行 cases"这种否定式写法，
下一个来改窄规则的人很容易顺手把它放出来。

不放 `opt/.run/` 下面——`assemble_version.py` 会 `rmtree` 那里的子目录。

```json
{
  "baseUrl": "https://zmapi-test.daqian369.com",
  "lane": "feat-esm-v3-0",
  "productId": "narnia",
  "builtAtMs": 1787639795971,
  "accounts": [
    {
      "id": "scale-done-001",
      "state": "scale-done",
      "userId": "54537",
      "phone": "13962087000",
      "nickname": "池子账号",
      "token": "…",
      "resultId": "scale_result_…",
      "builtAtMs": 1787639888291,
      "consumedAtMs": null
    }
  ]
}
```

顶层记 `baseUrl`/`lane`：工具读到池子时先比对自己配的是不是同一个泳道，对不上直接报错。
泳道被回收重建之后池子里的 token 全作废，这条比对能让它当场失败而不是在断言里失败。

### token 不会过期，但这是观察不是保证

`infra/auth/v1/auth.proto` 的 `AccessToken` 只有 `tokenType`/`accessToken`/`issuedAtMs`
三个字段，没有过期时间；`RegisterByPhone`/`LoginByPhone` 的响应从不返回 refresh token
（`narniaIntegrationAuth.weapp.ts` 里那个 `TEMPORARY_REFRESH_TOKEN` 占位符不是绕过什么，
是这条登录路径本来就没有刷新机制）。

所以不需要设计"过期前刷新"。但服务端会不会按闲置时长踢会话，从 proto 里看不出来——
池子里放一个足够旧的账号定期试一次，比假设它永远有效稳妥。

### 能建和不能建的状态

| state | 构造成本 | 现在能建吗 |
| --- | --- | --- |
| `fresh` | 约 2 秒 | ✅ |
| `profile-done` | 约 2 秒 | ✅ |
| `scale-done` | 约 75 秒 | ✅ 实测通过 |
| `report-ready` | 数十秒 | ❌ 被 P0 阻塞 |
| `candidate-1` | 约 3 分钟 | ❌ 被 P0 阻塞 |

后两个卡在 `docs/esm-v3-api-findings.md` 的 **P0-3**：刚创建的 ACTIVE 分享立刻绑定报
"分享无效"，以及量表完成后报告 5 分钟以上不生成。**这两个状态恰恰是池子价值最高的部分**
（省下 3 分钟以上的构造成本），泳道修好之前只能先交付前三个。

### 工具

`mp_use_real_account({ state })`，注册在 wxmp MCP server 上。

整条链路收在 `mcp_nav_agent` 一个 helper 里——这是这个方案能成立的关键。之前论证过，
Hercules 跨 helper 没有状态传递机制，唯一通道是 planner 的文本，而 wxmp 的设计约束③
明确要求 token 不进 LLM。池子读取、storage 注入全在 Node 进程内完成，token 从头到尾
不出现在任何模型看得到的文本里。

流程：读池子 → 校验 `baseUrl`/`lane` 匹配 → 按 `state` 取一个未消费的 → 标记已消费并写回
→ `clearStorage` → 写 `esm_tokens`/`esm_user` → reLaunch 首页 → **自验**。

自验两步：拿这个 token 从 Node 侧直接打一次真实后端的 `home/state`（证明 token 有效），
再确认当前页不是启动页/登录页（证明 app 认了这个登录态）。任何一步不过，报错要说清楚
"当前包大概率不是 dist-integration，或者域名校验没勾"——照 `resolveRoute` 那种"版本不对
就说清哪里不对"的写法。

配置走 `mcp_servers.json` 的 args：`--account-pool`、`--real-base-url`、`--real-lane`。
MCP stdio 边界不传环境变量，只能用 flag，这是既有约定。那个文件本来就已经存着一个真实的
vision key 并且已被 gitignore，多存一个池子路径不改变它的安全等级。

### 并发

`runner.py` / `__main__.py` 里没有任何并行执行的代码，Hercules 现在是单进程顺序跑完一条
再跑下一条。所以不需要锁机制——"读到即从文件里摘除"就够，池子建大一点覆盖单轮回归用量。
真正的风险只剩"两个人各开一个终端同时跑"这种人为并发，摘除式消费同样能挡住。

等哪天框架支持并行跑用例了再补。

### token 泄漏面

池子里是能登录真实后端的活密钥，和现有 fixture 里的假值（`access-token-001`）不是一回事。
要堵的口子：

- **工具返回文本**：不带 token（新工具自己控制）
- **`log()`**：不打 token（新工具自己控制）
- **`mp_get_storage`**：⚠️ 现在原样返回 storage 值，用它读 `esm_tokens` 会把真 token 直接
  送进 LLM 上下文和 `agent_inner_thoughts.json`。这是既有工具在引入真 token 之后**新增**的
  泄漏面，不是老约束的自动延伸，必须单独处理（对已知敏感 key 脱敏）。

wxmp README 的设计约束③ "token 不进 LLM" 相应要扩成 "token 不进 LLM，也不进日志"。

## 三、任务拆解

### 阶段一：环境分型（不依赖泳道健康，可以立刻开始）

| # | 任务 | 落点 |
| --- | --- | --- |
| T1 | `TYPES` 加 `mp-real` | `assemble_version.py`，docstring 一并更新 |
| T2 | `TYPES` 加 `mp-real`；devtools 判断改 `case`，`mp-real-*` 在 `mp-*` 前 | `run-version.sh` |
| T3 | preflight 加 `--mode mock\|real`；real 模式做磁盘检查 + 真实后端探测 | `mcp_servers/wxmp/preflight.mjs` |
| T4 | 建 `opt/cases/mp-real/` 骨架和 `test_data.txt`（写明本套件用 `mp_use_real_account`，不要用 `mp_use_fixture`） | `opt/cases/mp-real/` |
| T5 | 固化 `dist-integration` 构建命令（含 `TARO_APP_ENABLE_NARNIA_TEST_AUTH=true`）和"新项目路径首次要手动勾域名校验"这条 | `mcp_servers/wxmp/README.md` |

阶段一做完就能验证：`./run-version.sh 3.0 mp-real` 在包不对时拒绝启动、包对时正常进到执行。

### 阶段二：账号池（依赖泳道健康）

| # | 任务 | 落点 |
| --- | --- | --- |
| T6 | 池子 schema + 构建脚本，复用 `esm_api.py` 的 `_register_user`/`_complete_assessment` 逻辑，先支持 `fresh`/`profile-done`/`scale-done` | 新脚本（`helper_scripts/` 或顶层） |
| T7 | `.gitignore` 补一条显式规则挡 `opt/.accounts/` | `.gitignore` |
| T8 | `mp_use_real_account` 工具（读池子 → 校验泳道 → 消费 → 注入 → 自验） | `mcp_servers/wxmp/server.js` |
| T9 | `mp_get_storage` 对敏感 key 脱敏 | `mcp_servers/wxmp/server.js` |
| T10 | 泳道修好后补 `report-ready` / `candidate-1` 两个状态 | 同 T6 |

### 阶段三：试点与收尾

| # | 任务 |
| --- | --- |
| T11 | 写第一条 `mp-real` 用例试点：`scale-done` 账号冷启动应落到首页（只读、不依赖被阻塞的状态） |
| T12 | 端到端实跑一遍，把踩到的坑回填进 README（这套东西每次都有新坑，前几轮的经验是坑都在环境侧不在代码侧） |
| T13 | `fixtures/README.md` 补一段：什么时候用 mock 片段、什么时候用真实账号池 |

## 已知前置阻塞

**泳道健康是阶段二的硬前置。** `docs/esm-v3-api-findings.md` P0-3 记录的两个问题
（分享绑定失败、报告不生成）会让池子里价值最高的两个状态建不出来。阶段一不受影响，
可以并行推进。

---

## 为什么账号池暂缓（2026-08-25 算的账）

把 C 类逐条拉出来判定之后，收益比预想的小一个量级。

**前提**：池子走的是 infra-auth 的手机号注册旁路，不是微信两段 code。凡是被测对象
本身就是微信登录机制的，池子恰好绕过了要测的东西。

| 用例 | unit | 池子能解锁吗 |
| --- | ---: | --- |
| TC-ESM-079 分发落点 | 2 | ✅ 真解锁 |
| TC-ESM-032 分享绑定后双方首页 | 2 | ⚠️ 真解锁，但被 P0-3 阻塞 |
| TC-ESM-051 静默登录是否需点击 | 1 | ❌ 被测的就是微信静默登录，池子绕过了它 |
| TC-ESM-052 手机号冲突 | 1 | ❌ 走的是微信手机号授权，注册路径不同 |
| TC-ESM-030 启动页按钮文案 | 2 | ❌ mock 注 token 效果一样，真实后端无增量 |
| TC-ESM-060 storage 里 int64 形态 | 1 | ❌ 池子注入时自己写 storage，反而污染观察量 |

**今天净增量 2 个 unit，泳道修好后 4 个**——623 个 unit 里的 0.3%。

对面是 13 个任务加长期维护：`dist-integration` 构建、每个新项目路径首次要手动勾域名
校验、池子补给、真实测试环境垃圾账号无限累积、全程依赖泳道健康。

还有个更根本的对照：**213 个 backend unit 已经在 API 层用真实后端覆盖了**，那条路
不需要开发者工具、不需要管包、不需要手动勾选。真实后端的价值大部分已经被 API 套件
拿走，UI 层的边际价值只剩"UI 必须在回路里"的那几条。

唯一没体现在用例计数里的价值是**契约漂移哨兵**——fixture 是手写的，前端仓库没有任何
schema 校验，漂移了 mock 用例照绿。但一条冒烟只碰 `runtime/state` / `home/state` /
`user/me` 这几个接口，覆盖面同样很窄。

### 结论

先只做一条冒烟（`TC-ESM30-RS-01`），不建池子。成本从 13 个任务降到环境分型那 5 个，
拿到的是同样那 2 个 unit 的覆盖，外加一个覆盖面窄但真实存在的漂移哨兵。

**触发建池子的条件**（到了再做，别提前）：

1. **P0-3 修好**——`candidate-1` 和 `report-ready` 能建了，池子最贵的那部分价值才存在
2. **真实后端 UI 用例涨到 5 条以上**——现在 1 条，现场造 75 秒完全可以接受；到 5 条
   以上重复构造才开始疼

在那之前，`scale-done` 现场造 75 秒的成本，远低于维护一整套池子基础设施。
