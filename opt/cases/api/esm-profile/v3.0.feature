Feature: ESM V3.0 报告与关系接口

  # 来源：openspec/changes/add-esm-v21-backend/frontend_api_flow.md §5 §6
  # 契约：idl/esm/v1/esm_profile.proto
  # 期望错误码取自 idl/error_code_esm.go，并已在 feat-esm-v3-0 泳道上核对过实际返回。
  # 每条 Scenario 上方的「前置：…」标了这条要现做多少份量表、要不要等报告，跑之前先看清成本。

  Scenario: TC-ESM30-PF-01 关系类型树免登录可读且带一级二级层次
    When 不带登录态调用 GET /api/esm/v1/relationships/type-options
    Then statusCode 应该是 "0"
    And body.options 应该非空
    And body.options 每条都应该有非空的 typeId 和 title
    And body.options 至少有一条的 children 非空
    And body.options 的 displayOrder 应该是递增的

  Scenario: TC-ESM30-PF-02 固定标签列表按展示顺序返回
    When 不带登录态调用 GET /api/esm/v1/relationships/fixed-tags
    Then statusCode 应该是 "0"
    And body.fixedTags 应该非空
    And body.fixedTags 里每一项都应该是非空字符串

  Scenario: TC-ESM30-PF-03 报告不存在时读取、标记已读、提交反馈都返回未找到
    Given 用 esm_new_user 建一个标签为 "A" 的用户，填资料，不做量表
    When 调用 GET /api/esm/v1/report，用标签 "A"，query 传 {"reportId": "report_does_not_exist"}
    Then bizCodeName 应该是 "BIZ_ESM_PROFILE_REPORT_NOT_FOUND"
    And canonicalCode 应该是 "CANONICAL_ERROR_CODE_NOT_FOUND"
    When 调用 POST /api/esm/v1/report/viewed，用标签 "A"，body 传 {"reportId": "report_does_not_exist"}
    Then bizCodeName 应该是 "BIZ_ESM_PROFILE_REPORT_NOT_FOUND"
    When 调用 POST /api/esm/v1/report/content/feedback，用标签 "A"，body 传 {"reportId": "report_does_not_exist", "tabTitle": "爱情", "subtitle": "你要的亲密", "score": "REPORT_CONTENT_FEEDBACK_SCORE_LIKE"}
    Then bizCodeName 应该是 "BIZ_ESM_PROFILE_REPORT_NOT_FOUND"

  Scenario: TC-ESM30-PF-04 没有量表结果时不允许创建关系分享
    Given 用 esm_new_user 建一个标签为 "A" 的用户，填资料，不做量表
    When 调用 POST /api/esm/v1/relationships/share/create，用标签 "A"，body 传 {"launchScene": "home_invite"}
    Then statusCode 不应该是 "0"
    And bizCodeName 应该是 "BIZ_ESM_PROFILE_SCALE_RESULT_REQUIRED"
    And canonicalCode 应该是 "CANONICAL_ERROR_CODE_CONFLICT"

  Scenario: TC-ESM30-PF-05 绑定不存在的分享返回分享无效
    Given 用 esm_new_user 建一个标签为 "A" 的用户，填资料，不做量表
    When 调用 POST /api/esm/v1/relationships/share/bind，用标签 "A"，body 传 {"shareId": "share_does_not_exist", "launchScene": "home_invite"}
    Then statusCode 不应该是 "0"
    And bizCodeName 应该是 "BIZ_ESM_PROFILE_SHARE_INVALID_OR_EXPIRED"

  Scenario: TC-ESM30-PF-06 关系设置目标不存在时被拒
    Given 用 esm_new_user 建一个标签为 "A" 的用户，填资料，不做量表
    When 调用 POST /api/esm/v1/relationships/setting，用标签 "A"，body 传 {"candidateRelationshipId": "candidate_relationship_does_not_exist", "targetUserId": "1", "setting": {"remarkName": "备注", "tagText": "朋友"}}
    Then statusCode 不应该是 "0"
    And bizCodeName 应该是 "BIZ_ESM_PROFILE_RELATIONSHIP_SETTING_TARGET_NOT_FOUND"

  # 前置：1 份量表 + 等报告，约 3 分钟
  Scenario: TC-ESM30-PF-07 答题完成后个人报告最终变成可读
    Given 用 esm_new_user 建一个标签为 "A" 的用户，做完量表
    When 用 esm_wait_report 等标签 "A" 的 "personal_detail_v1" 报告
    Then found 应该是 true
    And status 应该是 "REPORT_STATUS_READY"
    And contentLength 应该大于 0
    When 调用 GET /api/esm/v1/report，用标签 "A"，query 传 {"reportType": "personal_detail_v1", "subjectUserId": "${A_userId}"}
    Then statusCode 应该是 "0"
    And body.report.status 应该是 "REPORT_STATUS_READY"
    And body.report.subjectUserId 应该等于 ${A_userId}
    And body.report.resultId 应该等于 ${A_resultId}
    And body.report.reportType 应该是 "personal_detail_v1"
    And body.report.reportContent 应该非空

  # 前置：1 份量表 + 等报告，约 3 分钟
  Scenario: TC-ESM30-PF-08 标记已读按查看人记录并回落到首页红点
    Given 用 esm_new_user 建一个标签为 "A" 的用户，做完量表
    And 用 esm_wait_report 等标签 "A" 的 "personal_detail_v1" 报告，用 extract_json 捕获 {"reportId": "$.reportId"}
    When 调用 GET /api/esm/v1/home/state，用标签 "A"
    Then body.myPage.personalReportUnread 应该是 true
    When 调用 POST /api/esm/v1/report/viewed，用标签 "A"，body 传 {"reportId": "${reportId}"}
    Then statusCode 应该是 "0"
    And body.reportStatusWhenViewed 应该是 "REPORT_STATUS_READY"
    And body.viewedAtMs 应该大于 0
    When 调用 GET /api/esm/v1/report，用标签 "A"，query 传 {"reportId": "${reportId}"}
    Then body.report.viewed 应该是 true
    When 调用 GET /api/esm/v1/home/state，用标签 "A"
    Then body.latestReport.viewed 应该是 true
    And body.myPage.personalReportUnread 应该是 false

  # 前置：1 份量表 + 等报告，约 3 分钟
  Scenario: TC-ESM30-PF-09 同一报告模块重复反馈覆盖同一条记录
    Given 用 esm_new_user 建一个标签为 "A" 的用户，做完量表
    And 用 esm_wait_report 等标签 "A" 的 "personal_detail_v1" 报告，用 extract_json 捕获 {"reportId": "$.reportId"}
    When 调用 POST /api/esm/v1/report/content/feedback，用标签 "A"，body 传 {"reportId": "${reportId}", "tabTitle": "爱情", "subtitle": "你要的亲密", "score": "REPORT_CONTENT_FEEDBACK_SCORE_LIKE"}，用 extract_json 捕获 {"feedbackId": "$.body.feedback.feedbackId"}
    Then statusCode 应该是 "0"
    And body.feedback.score 应该是 "REPORT_CONTENT_FEEDBACK_SCORE_LIKE"
    And body.feedback.tabTitle 应该是 "爱情"
    And body.feedback.subtitle 应该是 "你要的亲密"
    When 调用 POST /api/esm/v1/report/content/feedback，用标签 "A"，body 传 {"reportId": "${reportId}", "tabTitle": "爱情", "subtitle": "你要的亲密", "score": "REPORT_CONTENT_FEEDBACK_SCORE_DISLIKE"}
    Then statusCode 应该是 "0"
    And body.feedback.feedbackId 应该等于 ${feedbackId}
    And body.feedback.score 应该是 "REPORT_CONTENT_FEEDBACK_SCORE_DISLIKE"
    And body.feedback.updatedAtMs 应该大于 body.feedback.createdAtMs
    When 调用 GET /api/esm/v1/report，用标签 "A"，query 传 {"reportId": "${reportId}"}
    Then body.report.contentFeedbacks 应该有 1 条
    And body.report.contentFeedbacks 第 1 条的 score 应该是 "REPORT_CONTENT_FEEDBACK_SCORE_DISLIKE"

  # 前置：2 份量表 + 等报告，约 4 分钟
  Scenario: TC-ESM30-PF-10 别人的个人报告读不到
    Given 用 esm_new_user 建一个标签为 "A" 的用户，做完量表
    And 用 esm_wait_report 等标签 "A" 的 "personal_detail_v1" 报告，用 extract_json 捕获 {"reportId": "$.reportId"}
    And 用 esm_new_user 建一个标签为 "B" 的用户，做完量表
    When 调用 GET /api/esm/v1/report，用标签 "B"，query 传 {"reportId": "${reportId}", "subjectUserId": "${A_userId}"}
    Then statusCode 不应该是 "0"
    And bizCodeName 应该是 "BIZ_ESM_PROFILE_REPORT_ACCESS_DENIED"

  # 前置：1 份量表，约 1 分钟
  Scenario: TC-ESM30-PF-11 创建关系分享返回带 share_id 的落地路径
    Given 用 esm_new_user 建一个标签为 "A" 的用户，做完量表
    When 调用 POST /api/esm/v1/relationships/share/create，用标签 "A"，body 传 {"launchScene": "home_invite"}
    Then statusCode 应该是 "0"
    And body.share.shareId 应该非空
    And body.share.ownerUserId 应该等于 ${A_userId}
    And body.share.status 应该是 "RELATIONSHIP_SHARE_STATUS_ACTIVE"
    And body.share.launchPath 应该包含 "scene=home_invite"
    And body.share.launchPath 应该包含 body.share.shareId

  # 前置：1 份量表，约 1 分钟
  Scenario: TC-ESM30-PF-12 自己绑定自己的分享被拒
    Given 用 esm_new_user 建一个标签为 "A" 的用户，做完量表
    And 调用 POST /api/esm/v1/relationships/share/create，用标签 "A"，body 传 {"launchScene": "home_invite"}，用 extract_json 捕获 {"shareId": "$.body.share.shareId"}
    When 调用 POST /api/esm/v1/relationships/share/bind，用标签 "A"，body 传 {"shareId": "${shareId}", "launchScene": "home_invite"}
    Then statusCode 不应该是 "0"
    And bizCodeName 应该是 "BIZ_ESM_PROFILE_SELF_BINDING_CONFLICT"

  # 前置：2 份量表，约 2 分钟
  Scenario: TC-ESM30-PF-13 双方都有量表结果时绑定分享直接建成候选关系
    Given 用 esm_new_user 建一个标签为 "A" 的用户，做完量表
    And 用 esm_new_user 建一个标签为 "B" 的用户，做完量表
    And 调用 POST /api/esm/v1/relationships/share/create，用标签 "A"，body 传 {"launchScene": "home_invite"}，用 extract_json 捕获 {"shareId": "$.body.share.shareId"}
    When 调用 POST /api/esm/v1/relationships/share/bind，用标签 "B"，body 传 {"shareId": "${shareId}", "launchScene": "home_invite"}
    Then statusCode 应该是 "0"
    And body.binding.shareId 应该等于 ${shareId}
    And body.binding.ownerUserId 应该等于 ${A_userId}
    And body.binding.visitorUserId 应该等于 ${B_userId}
    And body.binding.status 应该是 "SHARE_BINDING_STATUS_CANDIDATE_CREATED"
    And body.candidateRelationship.candidateRelationshipId 应该非空
    And body.candidateRelationship.status 应该是 "CANDIDATE_RELATIONSHIP_STATUS_ACTIVE"

  # 前置：2 份量表，约 2 分钟
  Scenario: TC-ESM30-PF-14 候选关系是无方向的，双方都能在今天分组里看到对方
    Given 用 esm_new_user 建一个标签为 "A" 的用户，做完量表
    And 用 esm_new_user 建一个标签为 "B" 的用户，做完量表
    And 调用 POST /api/esm/v1/relationships/share/create，用标签 "A"，body 传 {"launchScene": "home_invite"}，用 extract_json 捕获 {"shareId": "$.body.share.shareId"}
    And 调用 POST /api/esm/v1/relationships/share/bind，用标签 "B"，body 传 {"shareId": "${shareId}", "launchScene": "home_invite"}，用 extract_json 捕获 {"candidateRelationshipId": "$.body.binding.candidateRelationshipId"}
    When 调用 GET /api/esm/v1/relationships/candidates，用标签 "A"
    Then statusCode 应该是 "0"
    And body.groups 应该有 1 条
    And body.groups 第 1 条的 group 应该是 "CANDIDATE_RELATIONSHIP_TIME_GROUP_TODAY"
    And body.groups 第 1 条的 totalCount 应该是 1
    And body.groups 第 1 条的 relationships 第 1 条的 candidateRelationshipId 应该等于 ${candidateRelationshipId}
    And body.groups 第 1 条的 relationships 第 1 条的 counterpartUser.userId 应该等于 ${B_userId}
    And body.groups 第 1 条的 relationships 第 1 条的 counterpartUser.nickname 应该非空
    When 调用 GET /api/esm/v1/relationships/candidates，用标签 "B"
    Then body.groups 第 1 条的 relationships 第 1 条的 candidateRelationshipId 应该等于 ${candidateRelationshipId}
    And body.groups 第 1 条的 relationships 第 1 条的 counterpartUser.userId 应该等于 ${A_userId}

  # 前置：2 份量表，约 2 分钟
  Scenario: TC-ESM30-PF-15 重复绑定同一分享是幂等的，不会长出第二条候选关系
    Given 用 esm_new_user 建一个标签为 "A" 的用户，做完量表
    And 用 esm_new_user 建一个标签为 "B" 的用户，做完量表
    And 调用 POST /api/esm/v1/relationships/share/create，用标签 "A"，body 传 {"launchScene": "home_invite"}，用 extract_json 捕获 {"shareId": "$.body.share.shareId"}
    And 调用 POST /api/esm/v1/relationships/share/bind，用标签 "B"，body 传 {"shareId": "${shareId}", "launchScene": "home_invite"}，用 extract_json 捕获 {"candidateRelationshipId": "$.body.binding.candidateRelationshipId"}
    When 再次调用 POST /api/esm/v1/relationships/share/bind，用标签 "B"，body 传 {"shareId": "${shareId}", "launchScene": "home_invite"}
    Then statusCode 应该是 "0"
    And body.binding.candidateRelationshipId 应该等于 ${candidateRelationshipId}
    When 调用 GET /api/esm/v1/relationships/candidates，用标签 "B"
    Then body.groups 第 1 条的 totalCount 应该是 1

  # 前置：2 份量表，约 2 分钟
  Scenario: TC-ESM30-PF-16 备注名和标签按当前用户维度保存，对方看不到
    Given 用 esm_new_user 建一个标签为 "A" 的用户，做完量表
    And 用 esm_new_user 建一个标签为 "B" 的用户，做完量表
    And 调用 POST /api/esm/v1/relationships/share/create，用标签 "A"，body 传 {"launchScene": "home_invite"}，用 extract_json 捕获 {"shareId": "$.body.share.shareId"}
    And 调用 POST /api/esm/v1/relationships/share/bind，用标签 "B"，body 传 {"shareId": "${shareId}", "launchScene": "home_invite"}，用 extract_json 捕获 {"candidateRelationshipId": "$.body.binding.candidateRelationshipId"}
    And 调用 GET /api/esm/v1/relationships/fixed-tags，记下第 1 个固定标签
    When 调用 POST /api/esm/v1/relationships/setting，用标签 "A"，body 传 {"candidateRelationshipId": "${candidateRelationshipId}", "targetUserId": "${B_userId}", "setting": {"remarkName": "小雨", "tagText": "<第 1 个固定标签>"}}
    Then statusCode 应该是 "0"
    And body.setting.remarkName 应该是 "小雨"
    And body.setting.tagText 应该等于记下的固定标签
    And body.setting.tagAuditStatus 应该是 "USER_INFO_AUDIT_STATUS_NOT_REQUIRED"
    When 调用 GET /api/esm/v1/relationships/candidates，用标签 "A"
    Then body.groups 第 1 条的 relationships 第 1 条的 setting.remarkName 应该是 "小雨"
    When 调用 GET /api/esm/v1/relationships/candidates，用标签 "B"
    Then body.groups 第 1 条的 relationships 第 1 条的 setting.remarkName 应该是空

  # 前置：2 份量表，约 2 分钟
  Scenario: TC-ESM30-PF-17 自定义标签走同步审核并覆盖旧标签
    Given 用 esm_new_user 建一个标签为 "A" 的用户，做完量表
    And 用 esm_new_user 建一个标签为 "B" 的用户，做完量表
    And 调用 POST /api/esm/v1/relationships/share/create，用标签 "A"，body 传 {"launchScene": "home_invite"}，用 extract_json 捕获 {"shareId": "$.body.share.shareId"}
    And 调用 POST /api/esm/v1/relationships/share/bind，用标签 "B"，body 传 {"shareId": "${shareId}", "launchScene": "home_invite"}，用 extract_json 捕获 {"candidateRelationshipId": "$.body.binding.candidateRelationshipId"}
    When 调用 POST /api/esm/v1/relationships/setting，用标签 "A"，body 传 {"candidateRelationshipId": "${candidateRelationshipId}", "targetUserId": "${B_userId}", "setting": {"remarkName": "小雨", "tagText": "朋友"}}
    Then statusCode 应该是 "0"
    When 调用 POST /api/esm/v1/relationships/setting，用标签 "A"，body 传 {"candidateRelationshipId": "${candidateRelationshipId}", "targetUserId": "${B_userId}", "setting": {"remarkName": "小雨", "tagText": "一起吃饭的同事"}}
    Then statusCode 应该是 "0"
    And body.setting.tagText 应该是 "一起吃饭的同事"
    And body.setting.tagAuditStatus 应该是 "USER_INFO_AUDIT_STATUS_PASSED"

  # 前置：1 份量表，约 1 分钟
  Scenario: TC-ESM30-PF-18 未完成量表的受邀方绑定分享，不进入分享方的候选列表
    Given 用 esm_new_user 建一个标签为 "A" 的用户，做完量表
    And 用 esm_new_user 建一个标签为 "B" 的用户，填资料，不做量表
    And 调用 POST /api/esm/v1/relationships/share/create，用标签 "A"，body 传 {"launchScene": "home_invite"}，用 extract_json 捕获 {"shareId": "$.body.share.shareId"}
    When 调用 POST /api/esm/v1/relationships/share/bind，用标签 "B"，body 传 {"shareId": "${shareId}", "launchScene": "home_invite"}
    Then statusCode 应该是 "0"
    And body.binding.status 不应该是 "SHARE_BINDING_STATUS_CANDIDATE_CREATED"
    When 调用 GET /api/esm/v1/relationships/candidates，用标签 "A"
    Then body.groups 应该是空

  # 前置：2 份量表，约 2 分钟
  # 候选关系的创建时刻应贴合受邀方绑定的那一刻，不管受邀方是刚注册的新号还是带着登录态
  # 进来的老号——server_time_ms 和 candidateRelationship.createdAtMs 来自同一次响应、
  # 同一台服务器，用它们的差值判断"刚刚才写入"不受 harness 本地时钟偏移影响。
  Scenario: TC-ESM30-PF-19 候选关系的创建时刻贴合受邀方绑定的那一刻
    Given 用 esm_new_user 建一个标签为 "A" 的用户，做完量表
    And 用 esm_new_user 建一个标签为 "B" 的用户，做完量表
    And 调用 POST /api/esm/v1/relationships/share/create，用标签 "A"，body 传 {"launchScene": "home_invite"}，用 extract_json 捕获 {"shareId": "$.body.share.shareId"}
    When 调用 POST /api/esm/v1/relationships/share/bind，用标签 "B"，body 传 {"shareId": "${shareId}", "launchScene": "home_invite"}
    Then statusCode 应该是 "0"
    And body.candidateRelationship.createdAtMs 与本次响应的 serverTimeMs 的差值不应该超过 2000 毫秒

  # 前置：2 份量表，约 2 分钟
  # 只要绑定的是同一个 shareId，不管谁绑定，候选关系都归属创建这个 shareId 的人；
  # B 绑定但不做量表本身不产生候选关系（见 TC-ESM30-PF-18），转发链路真正建成候选关系的是
  # 完成量表的那一方。
  Scenario: TC-ESM30-PF-20 分享卡片转发后建立的候选关系仍归属原分享者
    Given 用 esm_new_user 建一个标签为 "A" 的用户，做完量表
    And 用 esm_new_user 建一个标签为 "B" 的用户，填资料，不做量表
    And 用 esm_new_user 建一个标签为 "C" 的用户，做完量表
    And 调用 POST /api/esm/v1/relationships/share/create，用标签 "A"，body 传 {"launchScene": "home_invite"}，用 extract_json 捕获 {"shareId": "$.body.share.shareId"}
    And 调用 POST /api/esm/v1/relationships/share/bind，用标签 "B"，body 传 {"shareId": "${shareId}", "launchScene": "home_invite"}
    When 调用 POST /api/esm/v1/relationships/share/bind，用标签 "C"，body 传 {"shareId": "${shareId}", "launchScene": "home_invite"}
    Then statusCode 应该是 "0"
    And body.binding.ownerUserId 应该等于 ${A_userId}
    And body.binding.status 应该是 "SHARE_BINDING_STATUS_CANDIDATE_CREATED"
    When 调用 GET /api/esm/v1/relationships/candidates，用标签 "A"
    Then body.groups 第 1 条的 relationships 里应该有 counterpartUser.userId 等于 ${C_userId} 的一条
    And body.groups 第 1 条的 relationships 里不应该有 counterpartUser.userId 等于 ${B_userId} 的一条

  # 前置：1 份量表 + 等报告，约 3 分钟
  Scenario: TC-ESM30-PF-21 只读取报告不产生已读标记
    Given 用 esm_new_user 建一个标签为 "A" 的用户，做完量表
    And 用 esm_wait_report 等标签 "A" 的 "personal_detail_v1" 报告，用 extract_json 捕获 {"reportId": "$.reportId"}
    When 调用 GET /api/esm/v1/report，用标签 "A"，query 传 {"reportId": "${reportId}"}
    Then statusCode 应该是 "0"
    And body.report.viewed 应该是 false
    When 调用 GET /api/esm/v1/home/state，用标签 "A"
    Then body.myPage.personalReportUnread 应该是 true

  # 前置：1 份量表 + 等报告，约 3 分钟
  Scenario: TC-ESM30-PF-22 量表完成后自动触发不少于三种报告类型
    Given 用 esm_new_user 建一个标签为 "A" 的用户，做完量表
    When 用 esm_wait_report 等标签 "A" 的 "personal_detail_v1" 报告
    Then found 应该是 true
    And status 应该是 "REPORT_STATUS_READY"
    When 用 esm_wait_report 等标签 "A" 的 "personal_dialogue_v1" 报告
    Then found 应该是 true
    And status 应该是 "REPORT_STATUS_READY"
    When 用 esm_wait_report 等标签 "A" 的 "external_v1" 报告
    Then found 应该是 true
    And status 应该是 "REPORT_STATUS_READY"

  # 前置：1 份量表 + 等报告，约 3 分钟
  # 反馈的主键是 userId + reportId + tabTitle + subtitle（见 test_data 领域规则）。
  # 这条验证的是"不同 subtitle 不共用一条记录"，不是"序号从零开始"——后者描述的
  # tab_index/subsection_index 方案不是这套契约实际用的字段，narnia-qa 侧的
  # REQ-ESM-078-02 需要按这个事实重新核对，这里不写对应断言。
  Scenario: TC-ESM30-PF-23 同一 Tab 下不同子模块的反馈互不覆盖
    Given 用 esm_new_user 建一个标签为 "A" 的用户，做完量表
    And 用 esm_wait_report 等标签 "A" 的 "personal_detail_v1" 报告，用 extract_json 捕获 {"reportId": "$.reportId"}
    When 调用 POST /api/esm/v1/report/content/feedback，用标签 "A"，body 传 {"reportId": "${reportId}", "tabTitle": "爱情", "subtitle": "你要的亲密", "score": "REPORT_CONTENT_FEEDBACK_SCORE_LIKE"}，用 extract_json 捕获 {"feedbackId": "$.body.feedback.feedbackId"}
    Then statusCode 应该是 "0"
    When 调用 POST /api/esm/v1/report/content/feedback，用标签 "A"，body 传 {"reportId": "${reportId}", "tabTitle": "爱情", "subtitle": "你要的边界", "score": "REPORT_CONTENT_FEEDBACK_SCORE_DISLIKE"}
    Then statusCode 应该是 "0"
    And body.feedback.feedbackId 不应该等于 ${feedbackId}
    When 调用 GET /api/esm/v1/report，用标签 "A"，query 传 {"reportId": "${reportId}"}
    Then body.report.contentFeedbacks 应该有 2 条
    And body.report.contentFeedbacks 里应该有 subtitle 等于 "你要的亲密" 且 score 等于 "REPORT_CONTENT_FEEDBACK_SCORE_LIKE" 的一条
    And body.report.contentFeedbacks 里应该有 subtitle 等于 "你要的边界" 且 score 等于 "REPORT_CONTENT_FEEDBACK_SCORE_DISLIKE" 的一条

  # 前置：2 份量表 + 等报告，约 4 分钟
  # 需求同步群 2026-08-18 的收口结论：「关系挖掘为问答对，个人报告 JSON，其余全 Markdown」。
  # 只测他人报告是 Markdown 会被"全部改 Markdown"的实现蒙混过去，只有两条一起断言才收得住。
  Scenario: TC-ESM30-PF-24 个人报告正文是 JSON，他人报告正文是 Markdown
    Given 用 esm_new_user 建一个标签为 "A" 的用户，做完量表
    And 用 esm_new_user 建一个标签为 "B" 的用户，做完量表
    And 调用 POST /api/esm/v1/relationships/share/create，用标签 "A"，body 传 {"launchScene": "home_invite"}，用 extract_json 捕获 {"shareId": "$.body.share.shareId"}
    And 调用 POST /api/esm/v1/relationships/share/bind，用标签 "B"，body 传 {"shareId": "${shareId}", "launchScene": "home_invite"}
    When 用 esm_wait_report 等标签 "A" 的 "personal_detail_v1" 报告，用 extract_json 捕获 {"reportId": "$.reportId"}
    Then found 应该是 true
    When 调用 GET /api/esm/v1/report，用标签 "A"，query 传 {"reportId": "${reportId}"}
    Then statusCode 应该是 "0"
    And body.report.reportContent 应该能被解析成 JSON
    When 用 esm_wait_report 等标签 "B" 的 "external_v1" 报告，用 extract_json 捕获 {"bExternalReportId": "$.reportId"}
    Then found 应该是 true
    When 调用 GET /api/esm/v1/report，用标签 "A"，query 传 {"reportId": "${bExternalReportId}", "subjectUserId": "${B_userId}"}
    Then statusCode 应该是 "0"
    And body.report.reportContent 不应该能被解析成 JSON
