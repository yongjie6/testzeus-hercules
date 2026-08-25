# Hercules 自己的 fixture 片段

`mp_use_fixture` 能用两类名字，这里放的是第二类：

| 来源 | 名字 | 谁维护 |
| --- | --- | --- |
| `<miniapp>/packages/e2e/tests/fixtures/` | `TC-*` | 前端团队 |
| 本目录 | 其它（约定用 `state-` 前缀） | QA |

## 为什么不写进前端仓库

前端仓库有门禁：`scripts/check-spec-mappings.mjs` 会遍历 `tests/fixtures/` 下每个
`TC-*` 目录，凡是不在 `docs/user-stories-and-test-cases-final.md` 的 TC Registry
里的一律报 `orphan fixture`。想在那边加一个 QA 专用场景，就得连带伪造一行 registry
（route / status / owner / reason）和一个同名 `TC-XXX.spec.ts`，头注释还要逐字对上。

那个门禁是对的——它保证前端的 fixture 和他们的用例一一对应。只是 Hercules 的用例不
在那张表里，所以 QA 需要的状态放这边，前端已经发布的 `TC-*` 直接消费。两边不用等对方
合 PR。

同名会被拒绝启动（`gateway.mts` 起来时检查），不会出现一个名字解析到哪边说不清的情况。

## 目录结构

和上游 scene 完全一样，`loadOverlay` 按同样的规则读：

```
<名字>/
  defaults/*.json    → { "baseResp": {...}, "defaults": {...} }
  routes/*.json      → 一个 RouteOverride 对象，或一个数组
```

`RouteOverride` 的字段见 `<miniapp>/packages/gateway/src/server.ts`：除了 `response`
还有 `delayMs`、`responseSequence`、`stream.chunks` / `stream.intervalMs`、
`queryExpect` / `bodyExpect`。**故障和时序注入靠的就是这几个字段，不需要额外能力**——
「报告生成人为拖长」是 `delayMs`，「AI 服务失败一次后恢复」是 `responseSequence`，
「放慢前两条消息的输出」是 `stream.intervalMs`。

## 写成片段，不要写成整份状态

上游 scene 是**整份**的：`TC-REPORT-001` 为了把 `canCreateRelationshipShare` 从
false 改成 true，把整个 GetHomeState 响应抄了一遍。这样写下去，状态组合数会指数增长——
v3.0 的用例盘出来有 155 种不同的前置状态组合，逐个写成 scene 就是 155 份互相重复的
JSON。

原因是这些状态轴（量表关联、报告就绪、候选关系条数、席位、答题进度…）**大多落在
`GET /api/esm/v1/home/state` 这一个聚合接口的不同字段上**。所以按轴拆成片段，用的时候
组合，比如「有候选关系 + 对方报告已就绪」：

```
mp_use_fixture(scenes=["state-candidate-1", "state-counterpart-report-ready"])
```

`gateway.mts` 按顺序**深合并**（上游的 `mergeFixtures` 只做一层浅合并，两个片段都碰
GetHomeState 时后一个会把前一个整个盖掉）。数组是替换不是追加：片段说「候选关系 6 条」
就是恰好 6 条。

一个片段只描述一个轴，只写它要改的字段，其余交给 `base`。**先看 `base` 或前端已发布的
`TC-*` 有没有现成的**——`base` 本身就是"已登录、资料填完、量表已关联"，`TC-ASSESS-001`
本身就是"从第 1 题开始、无历史答题"，都不需要再写 Hercules 侧片段。

## 现有片段

按状态轴分组，覆盖 v3.0 用例盘出来的 155 种前置状态组合里最高频的那一批
（`state-` 开头的是本目录；不带前缀的是前端已发布的上游 scene，直接引用）：

| 轴 | 片段/scene | 覆盖什么 |
| --- | --- | --- |
| 登录 + 资料 + 量表 | `base` | 已登录、资料填完、量表已关联——**默认状态，不用叠任何片段** |
| 登录 | `guest`（`mp_login_as` 的 preset，不是 fixture） | 未登录/未注册 |
| 资料 | `state-profile-incomplete` | 资料未填，`entryStage` 落在填写信息页 |
| 量表 | `state-scale-none` | 资料填完但没做量表，`entryStage` 落在量表页 |
| 答题 | `TC-ASSESS-001`（上游） | 从第 1 题开始，无历史答案 |
| 答题 | `TC-ASSESS-001` + `state-assessment-mid` | 已作答到第 33 题，断点在第 34 题 |
| 报告 | `TC-REPORT-001`（上游） | 个人报告已就绪、未读、1 个 tab |
| 报告 | `state-report-many-tabs` | 个人报告 4 个 tab，一个超长（首屏放不下）一个简短（放得下） |
| 候选关系 | `state-candidate-1` | 候选关系 1 条，此前从未对话过 |
| 对方报告 | `state-counterpart-report-ready` | 候选/关系对象的对外报告（`external_v1`）已就绪，Markdown 正文 |
| 关系对话 | `TC-HOME-001/snapshot`（上游，注意带 `/`） | 关系对话 1 条，已设标签和备注 |
| 关系对话 | `state-relationship-chat-untagged` | 关系对话 1 条，有备注、无标签 |
| 关系对话 | `state-relationship-chat-default-nickname` | 关系对话 1 条，无备注（显示默认昵称）、有标签 |
| 关系原子转正式 | `TC-HOME-001/candidate-mutation`（上游） | 候选关系转成正式咨询关系的中间态 |
| 个人对话历史 | `state-chat-history-6` | 个人对话已有三轮问答共六条消息 |
| 个人对话历史 | `TC-PERSON-CHAT-001/new` / `/existing`（上游） | 新建对话 vs 已有对话，无需叠片段 |
| 全局降级 | `state-global-degraded` | `runtime/state` 返回降级开关已打开——**前端目前没有读这个字段的代码，弹窗不会出现，这个片段本身没问题，但配它写的用例现在测不出东西**，见下方「已知空白」 |
| 分享 | `state-can-share` | `canCreateRelationshipShare` 置 true（base 里是 false） |

## 已知空白

写这批片段时顺带确认了两处产品还没做、写用例前要先跟人对齐的地方，不要照着 PRD 文本
硬写片段：

- **全局降级没有前端实现**：`packages/app/src` 里搜不到任何读取 `degradation` 字段
  的代码。`state-global-degraded` fixture 本身是对的（对着 `esm_gateway.proto` 的
  `MiniappDegradation` 核对过），但配它的 TC-ESM-042 那批用例现在写了也测不出东西——
  界面压根不会因为这个字段变化。
- **"微信昵称授权决策"没有对应的客户端状态**：搜不到任何相关的 storage key 或字段名，
  这个概念在 narnia-qa 的用例里出现，但前端要么还没实现，要么用的是完全不同的叫法。
  没有去猜一个 storage key 硬造，这条轴目前没有片段。
