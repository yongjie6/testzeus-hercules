Feature: ESM V3.0 网关接口：运行时状态、登录态、资料与首页聚合

  # 来源：openspec/changes/add-esm-v21-backend/frontend_api_flow.md §0 §1 §2 §3
  # 契约：idl/esm/v1/esm_gateway.proto、idl/esm/v1/esm_profile.proto
  # 期望错误码取自 idl/error_code_esm.go，并已在 feat-esm-v3-0 泳道上核对过实际返回。

  Scenario: TC-ESM30-GW-01 运行时状态接口免登录可读且当前未降级
    When 不带登录态调用 GET /api/esm/v1/runtime/state
    Then statusCode 应该是 "0"
    And body.degradation.degraded 应该是 false
    And body.degradation.updatedAtMs 应该存在

  Scenario: TC-ESM30-GW-02 未登录读取首页状态返回未授权
    When 不带登录态调用 GET /api/esm/v1/home/state
    Then statusCode 不应该是 "0"
    And bizCodeName 应该是 "BIZ_GATEWAY_UNAUTHORIZED"
    And canonicalCode 应该是 "CANONICAL_ERROR_CODE_UNAUTHORIZED"
    And errorSource 应该是 "esm-miniapp-gateway"

  Scenario: TC-ESM30-GW-03 有登录态可读个人信息，无登录态被拒
    Given 用 esm_new_user 建一个标签为 "A" 的用户，填资料，不做量表
    When 调用 GET /api/esm/v1/user/me，用标签 "A"
    Then statusCode 应该是 "0"
    And body.user.userId 应该等于标签 "A" 的 userId
    When 不带登录态调用 GET /api/esm/v1/user/me
    Then bizCodeName 应该是 "BIZ_GATEWAY_UNAUTHORIZED"

  Scenario: TC-ESM30-GW-04 请求里的 userId 与登录态不一致时被拒
    Given 用 esm_new_user 建一个标签为 "A" 的用户，填资料，不做量表
    When 调用 GET /api/esm/v1/home/state，用标签 "A"，query 传 {"userId": "1"}
    Then statusCode 不应该是 "0"
    And bizCodeName 应该是 "BIZ_GATEWAY_UNAUTHORIZED"
    And statusMsg 应该包含 "user mismatch"

  Scenario: TC-ESM30-GW-05 新注册未填资料的用户停在资料填写阶段
    Given 用 esm_new_user 建一个标签为 "A" 的用户，不填资料，不做量表
    When 调用 GET /api/esm/v1/home/state，用标签 "A"
    Then statusCode 应该是 "0"
    And body.entryStage 应该是 "HOME_ENTRY_STAGE_COMPLETE_USER_INFO"
    And body.userInfoCompleted 应该是 false
    And body.user.nicknameAuditStatus 应该是 "USER_INFO_AUDIT_STATUS_NOT_REQUIRED"
    # 没有量表结果就不该给分享入口，否则用户点了必然拿到 SCALE_RESULT_REQUIRED
    And body.canCreateRelationshipShare 应该是 false
    And body.myPage.hasScaleResult 应该是 false

  Scenario: TC-ESM30-GW-06 资料必填项为空时提交被拒且不改资料状态
    Given 用 esm_new_user 建一个标签为 "A" 的用户，不填资料，不做量表
    When 调用 POST /api/esm/v1/user/info，用标签 "A"，body 传 {"userInfo": {"nickname": "", "gender": "GENDER_UNSPECIFIED", "birthDate": "", "avatarRef": "", "avatarSource": "AVATAR_SOURCE_UNSPECIFIED"}}
    Then statusCode 不应该是 "0"
    And bizCodeName 应该是 "BIZ_ESM_GATEWAY_USER_INFO_INCOMPLETE"
    And canonicalCode 应该是 "CANONICAL_ERROR_CODE_INVALID_PARAMETER"
    When 调用 GET /api/esm/v1/home/state，用标签 "A"
    Then body.userInfoCompleted 应该是 false
    And body.entryStage 应该是 "HOME_ENTRY_STAGE_COMPLETE_USER_INFO"

  Scenario: TC-ESM30-GW-07 提交完整资料后资料完成并推进到答题阶段
    Given 用 esm_new_user 建一个标签为 "A" 的用户，不填资料，不做量表
    When 调用 POST /api/esm/v1/user/info，用标签 "A"，body 传 {"userInfo": {"nickname": "网关用例A", "gender": "GENDER_MALE", "birthDate": "1994-01-02", "avatarRef": "https://daqian-bullet-demo.tos-cn-beijing.volces.com/tests/player_01_19233_selected.jpg", "avatarSource": "AVATAR_SOURCE_WECHAT"}}
    Then statusCode 应该是 "0"
    And body.user.userInfoCompleted 应该是 true
    And body.user.nickname 应该是 "网关用例A"
    And body.user.gender 应该是 "GENDER_MALE"
    And body.user.birthDate 应该是 "1994-01-02"
    And body.user.maskedPhone 应该包含 "****"
    When 调用 GET /api/esm/v1/home/state，用标签 "A"
    Then body.entryStage 应该是 "HOME_ENTRY_STAGE_ANSWERING"
    And body.hasUnfinishedAnswering 应该是 false

  Scenario: TC-ESM30-GW-08 自填昵称过审，微信来源头像不进入头像审核
    Given 用 esm_new_user 建一个标签为 "A" 的用户，填资料，不做量表
    When 调用 GET /api/esm/v1/user/me，用标签 "A"
    Then statusCode 应该是 "0"
    And body.user.nicknameAuditStatus 应该是 "USER_INFO_AUDIT_STATUS_PASSED"
    And body.user.avatarSource 应该是 "AVATAR_SOURCE_WECHAT"
    And body.user.avatarAuditStatus 应该是 "USER_INFO_AUDIT_STATUS_NOT_REQUIRED"

  Scenario: TC-ESM30-GW-09 非法微信登录 code 返回登录 code 失效
    When 不带登录态调用 POST /api/esm/v1/auth/wechat/login，body 传 {"wechatLoginCode": "fake_login_code_for_test"}
    Then statusCode 不应该是 "0"
    And bizCodeName 应该是 "BIZ_ESM_GATEWAY_WECHAT_LOGIN_CODE_INVALID"
    And canonicalCode 应该是 "CANONICAL_ERROR_CODE_UNAUTHORIZED"

  Scenario: TC-ESM30-GW-10 非法微信手机号 code 返回手机号 code 失效
    When 不带登录态调用 POST /api/esm/v1/auth/wechat-phone/login，body 传 {"wechatLoginCode": "fake_login_code", "wechatPhoneCode": "fake_phone_code", "agreement": {"agreed": true, "userAgreementVersion": "v1", "privacyPolicyVersion": "v1"}}
    Then statusCode 不应该是 "0"
    And bizCodeName 应该是 "BIZ_ESM_GATEWAY_WECHAT_PHONE_CODE_INVALID"

  Scenario: TC-ESM30-GW-11 refresh_token 为空或非法都返回续期未授权
    When 不带登录态调用 POST /api/esm/v1/auth/token/refresh，body 传 {"refreshToken": ""}
    Then bizCodeName 应该是 "BIZ_ESM_GATEWAY_REFRESH_TOKEN_UNAUTHORIZED"
    And statusMsg 应该包含 "required"
    When 不带登录态调用 POST /api/esm/v1/auth/token/refresh，body 传 {"refreshToken": "not-a-real-refresh-token"}
    Then bizCodeName 应该是 "BIZ_ESM_GATEWAY_REFRESH_TOKEN_UNAUTHORIZED"
    And statusMsg 应该包含 "invalid"

  # 这条要跑完 144 题，单条大约 3 分钟。
  Scenario: TC-ESM30-GW-12 完成量表后首页聚合出 HOME 阶段与关系席位配额
    Given 用 esm_new_user 建一个标签为 "A" 的用户，做完量表
    When 调用 GET /api/esm/v1/home/state，用标签 "A"
    Then statusCode 应该是 "0"
    And body.entryStage 应该是 "HOME_ENTRY_STAGE_HOME"
    And body.hasUnfinishedAnswering 应该是 false
    And body.latestResult.resultId 应该等于标签 "A" 的 resultId
    And body.canCreateRelationshipShare 应该是 true
    And body.relationshipSlotQuota.maxCount 应该是 20
    And body.relationshipSlotQuota.usedCount 应该是 0
    And body.relationshipSlotQuota.remainingCount 应该是 20
    And body.myPage.hasScaleResult 应该是 true
