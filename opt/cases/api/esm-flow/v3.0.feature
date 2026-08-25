Feature: ESM V3.0 场景化串联：从进入小程序到关系对话

  # 来源：openspec/changes/add-esm-v21-backend/frontend_api_flow.md 总流程时序图
  # 契约：idl/esm/v1/*.proto、idl/error_code_esm.go
  # 这些用例按真实调用顺序串接口，验证跨服务的状态推进。单接口边界见另外四个套件。
  # 每条都要现做量表：单人链路约 4 分钟，双人链路约 7 分钟。

  Scenario: TC-ESM30-FL-01 新用户主链路：登录到资料到答题到结果到报告到对话
    # 对应 frontend_api_flow.md 总流程时序图上半段
    When 不带登录态调用 GET /api/esm/v1/runtime/state
    Then statusCode 应该是 "0"
    And body.degradation.degraded 应该是 false

    Given 用 esm_new_user 建一个标签为 "A" 的用户，不填资料，不做量表
    When 调用 GET /api/esm/v1/home/state，用标签 "A"
    Then body.entryStage 应该是 "HOME_ENTRY_STAGE_COMPLETE_USER_INFO"
    And body.userInfoCompleted 应该是 false

    When 调用 POST /api/esm/v1/user/info，用标签 "A"，body 传 {"userInfo": {"nickname": "串联用例A", "gender": "GENDER_MALE", "birthDate": "1994-01-02", "avatarRef": "https://daqian-bullet-demo.tos-cn-beijing.volces.com/tests/player_01_19233_selected.jpg", "avatarSource": "AVATAR_SOURCE_WECHAT"}}
    Then statusCode 应该是 "0"
    And body.user.userInfoCompleted 应该是 true
    When 调用 GET /api/esm/v1/home/state，用标签 "A"
    Then body.entryStage 应该是 "HOME_ENTRY_STAGE_ANSWERING"

    When 调用 POST /api/esm/v1/answering/start-or-resume，用标签 "A"，用 extract_json 捕获 {"sessionId": "$.body.session.sessionId"}
    Then body.questions 应该有 144 条
    And body.session.currentQuestionNo 应该是 1
    When 调用 POST /api/esm/v1/answering/answer，用标签 "A"，body 传 {"sessionId": "${sessionId}", "questionNo": 1, "choiceValue": 3}
    Then body.nextQuestion.questionNo 应该是 2
    And body.completed 应该是 false
    When 用 esm_finish_assessment 把标签 "A" 剩下的题答完
    Then resultId 应该非空

    When 调用 GET /api/esm/v1/result/latest，用标签 "A"
    Then statusCode 应该是 "0"
    And body.result.scoreResults 应该非空
    And body.result.dimensionInterpretations 的条数应该和 body.result.scoreResults 相同
    When 调用 GET /api/esm/v1/home/state，用标签 "A"
    Then body.entryStage 应该是 "HOME_ENTRY_STAGE_HOME"
    And body.hasUnfinishedAnswering 应该是 false
    And body.canCreateRelationshipShare 应该是 true
    And body.relationshipSlotQuota.remainingCount 应该是 20

    When 用 esm_wait_report 等标签 "A" 的 "personal_detail_v1" 报告，用 extract_json 捕获 {"reportId": "$.reportId"}
    Then found 应该是 true
    And status 应该是 "REPORT_STATUS_READY"
    When 调用 POST /api/esm/v1/report/viewed，用标签 "A"，body 传 {"reportId": "${reportId}"}
    Then statusCode 应该是 "0"
    When 调用 POST /api/esm/v1/report/content/feedback，用标签 "A"，body 传 {"reportId": "${reportId}", "tabTitle": "爱情", "subtitle": "你要的亲密", "score": "REPORT_CONTENT_FEEDBACK_SCORE_LIKE"}
    Then statusCode 应该是 "0"
    When 调用 GET /api/esm/v1/home/state，用标签 "A"
    Then body.latestReport.viewed 应该是 true
    And body.myPage.personalReportUnread 应该是 false

    When 调用 POST /api/esm/v1/chat/create，用标签 "A"，body 传 {"chatKind": "CHAT_KIND_SELF", "sourceType": "CHAT_SOURCE_TYPE_REPORT_ENTRY", "resultId": "${A_resultId}", "reportId": "${reportId}", "reportTabTitle": "爱情", "reportSubtitle": "你要的亲密", "createNewChat": true}，用 extract_json 捕获 {"chatId": "$.body.chat.chatId"}
    Then statusCode 应该是 "0"
    And body.chat.chatKind 应该是 "CHAT_KIND_SELF"
    And body.chat.reportId 应该等于 ${reportId}
    When 调用 POST /api/esm/v1/chat/message/send，用标签 "A"，body 传 {"chatId": "${chatId}", "content": "报告里说我在关系里最在意被看见，这具体是什么意思？", "inputType": "CHAT_INPUT_TYPE_TEXT", "clientRequestId": "case-fl01-send-1"}
    Then statusCode 应该是 "0"
    When 用 esm_wait_chat_reply 等标签 "A" 在 "${chatId}" 上 afterSeqId "0" 的回复
    Then settled 应该是 true
    And messages 第 2 条的 role 应该是 "CHAT_MESSAGE_ROLE_ASSISTANT"
    And messages 第 2 条的 replySource 应该是 "CHAT_REPLY_SOURCE_MODEL"
    When 调用 GET /api/esm/v1/home/state，用标签 "A"
    Then body.selfChat.chatId 应该等于 ${chatId}
    And body.selfChat.latestChatSummary 应该非空

  Scenario: TC-ESM30-FL-02 关系主链路：分享建关系到进入关系对话
    # 对应 frontend_api_flow.md §6 §7 和 tech_design.md 关系链路图
    Given 用 esm_new_user 建一个标签为 "A" 的用户，做完量表
    And 用 esm_new_user 建一个标签为 "B" 的用户，做完量表

    When 调用 GET /api/esm/v1/relationships/candidates，用标签 "A"
    Then statusCode 应该是 "0"
    And body.groups 应该是空

    When 调用 POST /api/esm/v1/relationships/share/create，用标签 "A"，body 传 {"launchScene": "home_invite"}，用 extract_json 捕获 {"shareId": "$.body.share.shareId"}
    Then statusCode 应该是 "0"
    And body.share.launchPath 应该包含 body.share.shareId

    When 调用 POST /api/esm/v1/relationships/share/bind，用标签 "B"，body 传 {"shareId": "${shareId}", "launchScene": "home_invite"}，用 extract_json 捕获 {"candidateRelationshipId": "$.body.binding.candidateRelationshipId"}
    Then statusCode 应该是 "0"
    And body.binding.status 应该是 "SHARE_BINDING_STATUS_CANDIDATE_CREATED"

    When 调用 GET /api/esm/v1/home/state，用标签 "A"
    Then body.candidateRelationshipGroups 应该有 1 条
    And body.candidateRelationshipGroups 第 1 条的 group 应该是 "CANDIDATE_RELATIONSHIP_TIME_GROUP_TODAY"
    And body.candidateRelationshipGroups 第 1 条的 totalCount 应该是 1
    When 调用 GET /api/esm/v1/home/state，用标签 "B"
    Then body.candidateRelationshipGroups 第 1 条的 totalCount 应该是 1

    When 调用 POST /api/esm/v1/relationship-chat/start，用标签 "A"，body 传 {"candidateRelationshipId": "${candidateRelationshipId}", "targetUserId": "${B_userId}", "createNewChat": true}，用 extract_json 捕获 {"chatId": "$.body.chat.chatId"}
    Then statusCode 应该是 "0"
    And body.slotOccupied 应该是 true
    And body.slotQuota.usedCount 应该是 1
    And body.relationship.relationshipInput.relationshipInputRequired 应该是 true
    When 用 esm_wait_chat_reply 等标签 "A" 在 "${chatId}" 上 afterSeqId "0" 的回复
    Then settled 应该是 true
    And messages 应该有 2 条
    And messages 每条的 role 都应该是 "CHAT_MESSAGE_ROLE_ASSISTANT"

    When 调用 GET /api/esm/v1/relationships/type-options，用标签 "A"，记下第 1 个一级关系的 typeId 和它第 1 个二级关系的 typeId
    And 调用 POST /api/esm/v1/chat/message/send，用标签 "A"，body 传 {"chatId": "${chatId}", "content": "我们是家人，具体是父母", "inputType": "CHAT_INPUT_TYPE_TEXT", "clientRequestId": "case-fl02-send-1", "relationshipInputResponse": true, "relationshipTypeSelection": {"level1TypeId": "<一级 typeId>", "level2TypeId": "<二级 typeId>"}}
    Then statusCode 应该是 "0"
    When 调用 GET /api/esm/v1/chat/detail，用标签 "A"，query 传 {"chatId": "${chatId}"}
    Then body.relationship.relationshipInput.relationshipInputSubmitted 应该是 true
    And body.relationship.relationshipInput.level1TypeId 应该等于记下的一级 typeId

    When 调用 GET /api/esm/v1/relationships/conversations，用标签 "A"
    Then statusCode 应该是 "0"
    And body.relationships 应该有 1 条
    And body.relationships 第 1 条的 targetUser.userId 应该等于 ${B_userId}
    And body.slotQuota.usedCount 应该是 1
    When 调用 GET /api/esm/v1/relationships/conversations，用标签 "B"
    Then body.relationships 应该是空
    And body.slotQuota.usedCount 应该是 0

  Scenario: TC-ESM30-FL-03 断点续答场景：中途退出后回来接着答
    Given 用 esm_new_user 建一个标签为 "A" 的用户，填资料，不做量表
    When 调用 POST /api/esm/v1/answering/start-or-resume，用标签 "A"，用 extract_json 捕获 {"sessionId": "$.body.session.sessionId"}
    And 依次提交第 1 到第 8 题，choiceValue 都传 4
    Then statusCode 应该是 "0"

    When 调用 GET /api/esm/v1/home/state，用标签 "A"
    Then body.entryStage 应该是 "HOME_ENTRY_STAGE_ANSWERING"
    And body.hasUnfinishedAnswering 应该是 true
    And body.answeringSession.sessionId 应该等于 ${sessionId}
    And body.answeringSession.currentQuestionNo 应该是 9
    And body.answeringSession.answeredCount 应该是 8
    And body.answeringSession.status 应该是 "ANSWERING_SESSION_STATUS_IN_PROGRESS"

    When 调用 POST /api/esm/v1/answering/start-or-resume，用标签 "A"
    Then body.session.sessionId 应该等于 ${sessionId}
    And body.session.currentQuestionNo 应该是 9
    And body.savedAnswers 应该有 8 条
    When 调用 POST /api/esm/v1/answering/answer，用标签 "A"，body 传 {"sessionId": "${sessionId}", "questionNo": 9, "choiceValue": 2}
    Then statusCode 应该是 "0"
    And body.session.answeredCount 应该是 9
    And body.nextQuestion.questionNo 应该是 10

  Scenario: TC-ESM30-FL-04 重新开始场景：确认后从头开始且旧进度不再影响入口
    Given 用 esm_new_user 建一个标签为 "A" 的用户，填资料，不做量表
    When 调用 POST /api/esm/v1/answering/start-or-resume，用标签 "A"，用 extract_json 捕获 {"sessionId": "$.body.session.sessionId"}
    And 依次提交第 1 到第 6 题，choiceValue 都传 5
    Then statusCode 应该是 "0"

    When 调用 POST /api/esm/v1/answering/restart，用标签 "A"，body 传 {"previousSessionId": "${sessionId}", "confirmed": false}
    Then statusCode 不应该是 "0"
    And statusMsg 应该包含 "confirmation required"

    When 调用 POST /api/esm/v1/answering/restart，用标签 "A"，body 传 {"previousSessionId": "${sessionId}", "confirmed": true}，用 extract_json 捕获 {"newSessionId": "$.body.session.sessionId"}
    Then statusCode 应该是 "0"
    And body.session.attemptNo 应该是 2
    And body.session.currentQuestionNo 应该是 1
    And body.session.answeredCount 应该是 0
    And body.session.restartedFromSessionId 应该等于 ${sessionId}

    When 调用 POST /api/esm/v1/answering/start-or-resume，用标签 "A"
    Then body.session.sessionId 应该等于 ${newSessionId}
    And body.savedAnswers 应该是空
    When 调用 GET /api/esm/v1/home/state，用标签 "A"
    Then body.answeringSession.sessionId 应该等于 ${newSessionId}
    And body.answeringSession.answeredCount 应该是 0
    When 调用 POST /api/esm/v1/answering/answer，用标签 "A"，body 传 {"sessionId": "${sessionId}", "questionNo": 7, "choiceValue": 3}
    Then statusCode 不应该是 "0"

  Scenario: TC-ESM30-FL-05 断线重连场景：只按 seq 补齐缺的落库消息
    Given 用 esm_new_user 建一个标签为 "A" 的用户，做完量表
    And 调用 POST /api/esm/v1/chat/create，用标签 "A"，body 传 {"chatKind": "CHAT_KIND_SELF", "sourceType": "CHAT_SOURCE_TYPE_HOME_SELF_ENTRY", "resultId": "${A_resultId}", "createNewChat": true}，用 extract_json 捕获 {"chatId": "$.body.chat.chatId"}

    When 调用 POST /api/esm/v1/chat/message/send，用标签 "A"，body 传 {"chatId": "${chatId}", "content": "第一个问题：我最在意什么？", "inputType": "CHAT_INPUT_TYPE_TEXT", "clientRequestId": "case-fl05-send-1"}
    And 用 esm_wait_chat_reply 等标签 "A" 在 "${chatId}" 上 afterSeqId "0" 的回复
    Then settled 应该是 true
    And latestSeqId 应该是 "2"

    When 调用 POST /api/esm/v1/chat/message/send，用标签 "A"，body 传 {"chatId": "${chatId}", "content": "第二个问题：那我该怎么办？", "inputType": "CHAT_INPUT_TYPE_TEXT", "clientRequestId": "case-fl05-send-2"}
    And 用 esm_wait_chat_reply 等标签 "A" 在 "${chatId}" 上 afterSeqId "2" 的回复
    Then settled 应该是 true
    And latestSeqId 应该是 "4"

    # 模拟客户端本地只应用到 seq 2 就断线，重连后按本地最高 seq 补齐
    When 调用 GET /api/esm/v1/chat/messages-after-seq，用标签 "A"，query 传 {"chatId": "${chatId}", "afterSeqId": "2"}
    Then statusCode 应该是 "0"
    And body.messages 应该有 2 条
    And body.messages 第 1 条的 seqId 应该是 "3"
    And body.messages 第 1 条的 role 应该是 "CHAT_MESSAGE_ROLE_USER"
    And body.messages 第 2 条的 seqId 应该是 "4"
    And body.messages 第 2 条的 role 应该是 "CHAT_MESSAGE_ROLE_ASSISTANT"
    And body.latestSeqId 应该是 "4"

    When 调用 GET /api/esm/v1/chat/detail，用标签 "A"，query 传 {"chatId": "${chatId}"}
    Then body.messages 应该有 4 条
    And body.latestSeqId 应该是 "4"
    And body.chat.turnStatus 应该是 "CHAT_TURN_STATUS_IDLE"
