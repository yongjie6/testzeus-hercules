# ESM V3.0 服务端接口问题清单

写用例时对着 `feat-esm-v3-0` 泳道逐个接口验过一遍，下面是实现或文档与契约对不上的地方。
每条都给了复现命令，环境是：

```bash
export B=https://zmapi-test.daqian369.com
export L='lane: feat-esm-v3-0'
```

严重度按「是否挡住产品链路」判断。

---

## P0-1 我的页拿不到量表结果

`GetHomeState.my_page.has_scale_result` 恒为 `false`，`my_page.latest_result` 恒为 `null`，
即使同一个响应的顶层 `latest_result` 已经返回了完整量表结果。

按 `esm_profile.proto` 的 `MyPageState`，这两个字段是「我的」页「我的测试结果」入口的数据源。
PRD 五章「我的」明确要求这个入口存在。现在的返回让入口无从渲染。

同一响应里自相矛盾：

```
latestResult.resultId       = scale_result_1787280661294_3d5a538192215b17
latestReport.status         = REPORT_STATUS_READY
myPage.hasScaleResult       = false      <-- 应为 true
myPage.latestResult         = null       <-- 应为顶层那份结果
myPage.latestPersonalReport = {已正常返回}
myPage.personalReportUnread = false      {已正常返回}
```

`personal_report_unread` 和 `latest_personal_report` 都是对的，只有量表结果这两项没填。

复现：任取一个做完 144 题的用户，

```bash
curl -s -H "$L" -H 'X-Product-ID: narnia' -H "Authorization: Bearer $TOKEN" \
  "$B/api/esm/v1/home/state?baseReq.productId=narnia&baseReq.requestId=p1" \
  | python3 -c "import sys,json;d=json.load(sys.stdin);print('top latestResult:',(d.get('latestResult') or {}).get('resultId'));print('myPage:',d.get('myPage'))"
```

对应用例：`TC-ESM30-GW-12`（当前会红）。

---

## P0-2 关系列表里对方的昵称和头像是空的

`ListCandidateRelationships` 的 `counterpart_user`、`ListConversationRelationships` 和
`GetHomeState.conversation_relationships` 的 `target_user`，返回的 `EsmUser` 里
`nickname`、`avatar_ref`、`gender`、`birth_date` 全是空值，只有 `user_id` 和
`user_info_completed` 是对的。

PRD 五章首页要求候选关系卡片和关系对话入口展示「头像：用户头像，名称：用户名称」。
现在前端拿不到这两个字段，首页列表渲染不出来。

对方的资料本身是全的，只是没回填到关系结构里：

```
B 自己 GetMe        : {"userId":"54112","nickname":"接口用例B","avatarRef":"https://...jpg","gender":"GENDER_FEMALE"}
A 看到的 counterpart: {"userId":"54112","nickname":"","avatarRef":"","gender":"GENDER_UNSPECIFIED"}
A 看到的 targetUser : {"userId":"54112","nickname":"","avatarRef":""}
```

`BindRelationshipShare` 返回的 `candidate_relationship.counterpart_user` 同样是空的。

复现：建两个做完量表的用户 A、B，A 建分享、B 绑定，然后

```bash
curl -s -H "$L" -H 'X-Product-ID: narnia' -H "Authorization: Bearer $TOKEN_A" \
  "$B/api/esm/v1/relationships/candidates?baseReq.productId=narnia&baseReq.requestId=p2" \
  | python3 -c "import sys,json;print(json.load(sys.stdin)['groups'][0]['relationships'][0]['counterpartUser'])"
```

对应用例：`TC-ESM30-PF-14`（当前会红）。

---

## P1-3 重新开始答题缺确认时，返回的是题号越界错误码

`RestartAnswering` 不带 `confirmed=true` 时返回：

```
statusCode  = 20201101003010
bizCodeName = BIZ_ESM_ASSESSMENT_QUESTION_OUT_OF_RANGE
statusMsg   = assessment restart confirmation required
```

错误码是 `SubmitAnswer` 的题号越界码（method=3），文案说的却是 restart 缺确认。
`frontend_api_flow.md` 让前端按 `bizCodeName` 分支，这里前端会把「没点确认」当成「题号非法」，
按文档提示回答题页并弹题号错误提示。

根因是 `idl/error_code_esm.go` 里 `esmAssessmentMethodRestartAnswering int64 = 4` 定义了，
但没有任何错误码常量用它，实现只能借了别的方法的码。建议补一个
`ErrEsmAssessmentRestartAnsweringConfirmationRequired`（method=4，InvalidParameter），
并在接口文档错误表里加一行。

复现：

```bash
curl -s -H "$L" -H 'X-Product-ID: narnia' -H "Authorization: Bearer $TOKEN" -H 'Content-Type: application/json' \
  -X POST "$B/api/esm/v1/answering/restart" \
  -d '{"previousSessionId":"<进行中的 sessionId>","confirmed":false,"baseReq":{"productId":"narnia","requestId":"p3"}}'
```

对应用例：`TC-ESM30-AS-10`、`TC-ESM30-FL-04`。这两条目前断言的是 `statusMsg` 含
"confirmation required" 而不是 `bizCodeName`，等错误码补齐后应改回按 `bizCodeName` 断言。

---

## P1-4 分享场景传错时被报成存储不可用

`CreateRelationshipShare` 传一个没配过的 `launch_scene`：

```
statusCode  = 20201201001070
bizCodeName = BIZ_ESM_PROFILE_STORAGE_UNAVAILABLE
statusMsg   = relationship share launch path missing: relationship_share.launch_path.not_configured_scene
```

参数问题被归成存储不可用。前端按 `bizCodeName` 分不出「客户端传错场景」和「服务端挂了」，
监控上也会把配置缺失计进存储可用性。建议归成 InvalidParameter 类错误码。

当前配置里只有 `home_invite` 一个可用场景
（`services/esm-profile/migrations/202608190001_init.up.sql`）。

复现：

```bash
curl -s -H "$L" -H 'X-Product-ID: narnia' -H "Authorization: Bearer $TOKEN" -H 'Content-Type: application/json' \
  -X POST "$B/api/esm/v1/relationships/share/create" \
  -d '{"launchScene":"not_configured_scene","baseReq":{"productId":"narnia","requestId":"p4"}}'
```

---

## P1-5 首页说能分享，调分享接口却被拒

没有量表结果的用户，`GetHomeState.can_create_relationship_share` 返回 `true`；
但 `CreateRelationshipShare` 对同一个用户返回 `BIZ_ESM_PROFILE_SCALE_RESULT_REQUIRED`。

这个字段就是给首页分享入口做门禁用的，现在门禁形同虚设：用户能点，点了必然失败。

```
新注册未答题用户 GetHomeState  -> canCreateRelationshipShare = true
同一用户 CreateRelationshipShare -> BIZ_ESM_PROFILE_SCALE_RESULT_REQUIRED
```

对应用例：`TC-ESM30-PF-04` 断言接口这一侧的拒绝（会绿）；`TC-ESM30-GW-05` 断言首页字段
`can_create_relationship_share` 对无量表用户应为 `false`（当前会红）。

---

## P2-6 停止生成时传不存在的 generation 返回成功

`StopChatGeneration` 传一个不存在的 `generation_id`，返回 `statusCode = "0"`。

`idl/error_code_esm.go` 里定义了 `ErrEsmChatStopGenerationGenerationNotFound`
（对应 `BIZ_ESM_CHAT_GENERATION_NOT_FOUND`），但实现从不返回它，
`frontend_api_flow.md` 的错误表里也列了这个码要前端处理。三处对不上，需要确认到底哪个是准的。

复现：

```bash
curl -s -H "$L" -H 'X-Product-ID: narnia' -H "Authorization: Bearer $TOKEN" -H 'Content-Type: application/json' \
  -X POST "$B/api/esm/v1/chat/generation/stop" \
  -d '{"chatId":"<真实 chatId>","generationId":"generation_does_not_exist","baseReq":{"productId":"narnia","requestId":"p6"}}'
```

---

## P2-7 消息反馈的错误码和错误文案对不上

`FeedbackMessage` 传一个不存在的 `chat_id`：

```
bizCodeName = BIZ_ESM_CHAT_MESSAGE_NOT_FOUND
statusMsg   = chat not found
```

码说消息不存在，文案说会话不存在。排查时按哪个走都可能找错方向。

对应用例：`TC-ESM30-CH-02`（按 `bizCodeName` 断言，当前会绿）。

---

## P2-8 接口文档缺了两条前端要处理的错误码

`frontend_api_flow.md` 第 8 节的错误表里没有这两个，但实际会返回、前端得分支处理：

| 缺的码 | 触发场景 |
|---|---|
| `BIZ_ESM_PROFILE_SCALE_RESULT_REQUIRED` | 没有量表结果就创建关系分享 |
| `BIZ_ESM_PROFILE_STORAGE_UNAVAILABLE` | `launch_scene` 没配（见 P1-4） |

---

## P2-9 答题「题号非法」的口径文档里没写全

`frontend_api_flow.md` §4 只说题号范围 1 到 144。实际上**跳答还没到的题号也会被判成
题号非法**：session 停在第 1 题时提交第 3 题，返回
`BIZ_ESM_ASSESSMENT_QUESTION_OUT_OF_RANGE`。

真实规则是「只能提交已答过的题（改答）或当前题 `session.current_question_no`」。这条规则
直接决定了自动化和客户端不能并发提交答案，值得写进接口文档。

复现：新建 session 后直接提交第 3 题。

对应用例：`TC-ESM30-AS-08`。

---

## 已验证行为正常的部分

以下都对着泳道确认过，实现和契约一致，列出来是为了说明上面的清单不是抽样：

- 鉴权边界：无 token、坏 token、请求体 `user_id` 与登录态不一致，都返回
  `BIZ_GATEWAY_UNAUTHORIZED`，后者 `statusMsg` 是 `user mismatch`。
- 微信两段 code 各自的失效码区分正确（`WECHAT_LOGIN_CODE_INVALID` /
  `WECHAT_PHONE_CODE_INVALID`），`RefreshToken` 空值和非法值都归
  `REFRESH_TOKEN_UNAUTHORIZED`。
- `entry_stage` 三段推进正确：未填资料 `COMPLETE_USER_INFO` → 填完 `ANSWERING` →
  答完 `HOME`。
- 答案幂等：同题同选项重复提交不递增 `answer_version`、不置 `modified`、`answered_at_ms`
  不变；改答递增版本并置 `modified`，且不影响后面已答的题，`current_question_no` 也不回退。
- 重新开始：`confirmed=true` 新建 `attempt_no=2` 的 session，`restarted_from_session_id`
  指回旧 session，旧 session 的答案不再出现在新 session 的 `saved_answers` 里。
- 报告权限：跨用户读报告返回 `REPORT_ACCESS_DENIED`；`MarkReportViewed` 按查看人记录，
  回落到 `GetHomeState.latest_report.viewed` 和 `my_page.personal_report_unread`。
- 报告模块反馈按 `user_id + report_id + tab_title + subtitle` 覆盖：`feedback_id` 不变、
  `score` 覆盖、`updated_at_ms` 变，`GetReport.content_feedbacks` 只留最新一条。
- 分享与候选关系：自绑返回 `SELF_BINDING_CONFLICT`，重复绑定幂等不重复建关系，
  双方都能看到同一个 `candidate_relationship_id`，按「今天」互斥分组且 `total_count` 正确。
- 备注标签按当前用户维度隔离：A 给 B 设的备注 B 看不到；固定标签走白名单
  `tag_audit_status = NOT_REQUIRED`，自定义标签同步审核后 `PASSED`。
- 对话幂等：同 `client_request_id` 同内容重发返回同一条消息；同 `client_request_id` 换内容
  返回 `CLIENT_REQUEST_CONFLICT`。
- 关系对话：占席位后配额 1/20/19，重复进入不重复占；首次进入按顺序落两条助手消息
  （开场白 + 对方报告）；关系卡片提交后 `relationship_input_required` 置 false 并落库
  一级/二级 ID 和展示文案；非法关系类型返回 `RELATIONSHIP_TYPE_INVALID`；非成员进入
  返回 `RELATIONSHIP_MEMBER_MISMATCH`。
- 补偿读取：`GetMessagesAfterSeq` 按 `after_seq_id` 正确增量返回，`latest_seq_id` 是已落库
  最高序号，助手消息落库后才推进。
