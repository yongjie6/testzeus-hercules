# ESM V3.0 服务端接口用例

小程序 V3.0 服务端接口的 Hercules 用例库。测的是 `esm-miniapp-gateway` 的 HTTP 接口，
不是小程序页面。

## 来源文档

| 文档 | 用途 |
|---|---|
| [tech_design.md](https://gitlab.daqian369.com/esm/narnia/dev/-/blob/feat/esm-v3-0/openspec/changes/add-esm-v21-backend/tech_design.md) | 服务边界、状态流、领域规则 |
| [frontend_api_flow.md](https://gitlab.daqian369.com/esm/narnia/dev/-/blob/feat/esm-v3-0/openspec/changes/add-esm-v21-backend/frontend_api_flow.md) | 接口调用顺序、前端分支处理 |
| [esm_gateway.proto](https://gitlab.daqian369.com/esm/narnia/idl/-/blob/feat/esm-v3-0/esm/v1/esm_gateway.proto) | HTTP 方法与路径 |
| [esm_assessment.proto](https://gitlab.daqian369.com/esm/narnia/idl/-/blob/feat/esm-v3-0/esm/v1/esm_assessment.proto) | 答题、量表结果的输入输出约束 |
| [esm_profile.proto](https://gitlab.daqian369.com/esm/narnia/idl/-/blob/feat/esm-v3-0/esm/v1/esm_profile.proto) | 资料、报告、分享、候选关系 |
| [esm_chat.proto](https://gitlab.daqian369.com/esm/narnia/idl/-/blob/feat/esm-v3-0/esm/v1/esm_chat.proto) | 对话、消息、关系对话 |
| [error_code_esm.go](https://gitlab.daqian369.com/esm/narnia/idl/-/blob/feat/esm-v3-0/error_code_esm.go) | 业务错误码 |
| [[PRD]-小程序V3.0-ESM](https://ocn0jrysp7wp.feishu.cn/wiki/Q4tKw32Qii0bFSk0JBhc53iinDg) | 产品规则（席位上限、分组、审核文案） |

## 套件划分

按后端服务归属拆，失败能直接定位到服务：

| 套件 | 覆盖 | 条数 |
|---|---|---|
| `esm-gateway` | 运行时状态、鉴权边界、微信登录 code 校验、资料、首页聚合 | 12 |
| `esm-assessment` | 答题 session、答案提交与改答、题号/选项边界、重新开始、量表结果 | 14 |
| `esm-profile` | 报告读取与权限、已读、模块反馈、分享、候选关系、备注标签、报告格式契约 | 24 |
| `esm-chat` | 自己的对话、关系对话、幂等、补偿读取、推荐问题、席位、风控 | 19 |
| `esm-flow` | 按真实调用顺序串起来的场景化链路 | 5 |

单接口的参数边界和错误码在前四个套件里逐个覆盖；`esm-flow` 只验证跨服务的状态推进。

## 怎么跑

```bash
./run-esm-api.sh
```

只跑一个套件：

```bash
./run-esm-api.sh esm-assessment
```

换环境：

```bash
ESM_LANE=your-lane ./run-esm-api.sh esm-gateway
```

`run-esm-api.sh` 负责三件事：设 `ESM_BASE_URL` / `ESM_LANE` / `ESM_PRODUCT_ID`，设
`ADDITIONAL_TOOL_DIRS` 让 `additional_tools/esm_api.py` 里的工具能被 `api_nav_agent`
加载，然后调 `run-version.sh 3.0`。

**ESM v3.0 这套服务当前只部署在泳道上，baseline 没有。** 不带泳道头直接打
`https://zmapi-test.daqian369.com/api/esm/v1/...` 会拿到 istio 的 503。默认泳道是
`feat-esm-v3-0`，泳道被回收后要改 `ESM_LANE`。确认泳道还在：

```bash
kubectl get deploy -n test-env | grep esm-
```

## 专用工具

用例不直接用 `generic_http_api`——它不会带泳道头、不会补 `baseReq`、也不会带登录态。
`additional_tools/esm_api.py` 给 `api_nav_agent` 注册了五个工具：

| 工具 | 用途 | 成本 |
|---|---|---|
| `esm_call` | 调一个网关接口并返回断言要看的字段 | 一次请求 |
| `esm_new_user` | 建前置用户，可选填资料、可选做完量表 | 2 秒 / 3 分钟 |
| `esm_finish_assessment` | 把当前用户剩下的题答完 | 约 3 分钟 |
| `esm_wait_report` | 轮询到 Agent 把报告写回 | 数十秒 |
| `esm_wait_chat_reply` | 轮询到助手回复落库 | 数秒 |

`esm_new_user` 和 `esm_finish_assessment` 存在的原因是答题必须逐题顺序提交：跳答会被判
成题号非法，所以 144 题只能一条条发，一轮接近 3 分钟。让 planner 一步步驱动会直接打爆
nav 轮次上限，所以把这段前置收进一次工具调用。答题接口本身的契约仍然由
`esm-assessment` 套件用 `esm_call` 逐条断言，没有被工具盖掉。

## 判定口径

网关对业务失败也返回 HTTP 200，成败不体现在 HTTP 状态码上：

- 成功：`baseResp.statusCode == "0"`
- 失败：看 `baseResp.error.bizCodeName`

对外 JSON 是 protojson：字段名 lowerCamelCase，int64 序列化成字符串，枚举是全名字符串。

## 跑之前先看成本

要现做量表的用例在 feature 里都标了「前置约 N 分钟」。全量 74 条里有 40 条左右需要
一到三个用户各做一遍 144 题，整轮跑完是小时级。日常回归用
`opt/cases/regression.txt` 里挑出来的那几条，都不需要做量表。

## 已知会失败的断言

下面三条断言写的是契约应有的行为，当前实现不满足，跑起来会红。这是有意为之——用例编码
的是契约，不是当前行为。修好之前它们会一直红：

| 用例 | 断言 | 对应问题 |
|---|---|---|
| `TC-ESM30-GW-05` | `body.canCreateRelationshipShare 应该是 false` | P1-5 首页说能分享，接口却拒 |
| `TC-ESM30-GW-12` | `body.myPage.hasScaleResult 应该是 true` | P0-1 我的页拿不到量表结果 |
| `TC-ESM30-PF-14` | `counterpartUser.nickname 应该非空` | P0-2 关系列表里对方资料是空的 |

细节和复现命令见 [esm-v3-api-findings.md](../../docs/esm-v3-api-findings.md)。
