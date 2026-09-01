Feature: ESM V3.0 答题与量表结果接口

  # 来源：openspec/changes/add-esm-v21-backend/frontend_api_flow.md §4 §5
  # 契约：idl/esm/v1/esm_assessment.proto
  # 期望错误码取自 idl/error_code_esm.go，并已在 feat-esm-v3-0 泳道上核对过实际返回。

  Background:
    Given 用 esm_new_user 建一个标签为 "A" 的用户，填资料，不做量表

  Scenario: TC-ESM30-AS-01 首次进入答题返回全量题目和空白进度
    When 调用 POST /api/esm/v1/answering/start-or-resume，用标签 "A"
    Then statusCode 应该是 "0"
    And body.session.status 应该是 "ANSWERING_SESSION_STATUS_IN_PROGRESS"
    And body.session.attemptNo 应该是 1
    And body.session.currentQuestionNo 应该是 1
    And body.session.answeredCount 应该是 0
    And body.questions 应该有 144 条
    And body.questions 第 1 条的 questionNo 应该是 1
    And body.questions 第 1 条的 totalQuestions 应该是 144
    And body.questions 第 1 条的 choices 应该有 6 条
    And body.questions 第 1 条的 choices 的 choiceValue 应该依次是 1 到 6

  Scenario: TC-ESM30-AS-02 重复进入答题只返回同一个进行中的 session
    When 调用 POST /api/esm/v1/answering/start-or-resume，用标签 "A"，用 extract_json 捕获 {"sessionId": "$.body.session.sessionId"}
    And 再次调用 POST /api/esm/v1/answering/start-or-resume，用标签 "A"
    Then statusCode 应该是 "0"
    And body.session.sessionId 应该等于 ${sessionId}
    And body.session.attemptNo 应该是 1

  Scenario: TC-ESM30-AS-03 顺序提交答案推进进度并返回下一题
    Given 调用 POST /api/esm/v1/answering/start-or-resume，用标签 "A"，用 extract_json 捕获 {"sessionId": "$.body.session.sessionId"}
    When 调用 POST /api/esm/v1/answering/answer，用标签 "A"，body 传 {"sessionId": "${sessionId}", "questionNo": 1, "choiceValue": 3}
    Then statusCode 应该是 "0"
    And body.savedAnswer.questionNo 应该是 1
    And body.savedAnswer.choiceValue 应该是 3
    And body.savedAnswer.answerVersion 应该是 1
    And body.savedAnswer.modified 应该是 false
    And body.completed 应该是 false
    And body.nextQuestion.questionNo 应该是 2
    And body.session.currentQuestionNo 应该是 2
    And body.session.answeredCount 应该是 1

  Scenario: TC-ESM30-AS-04 重复提交完全相同的答案按幂等处理
    Given 调用 POST /api/esm/v1/answering/start-or-resume，用标签 "A"，用 extract_json 捕获 {"sessionId": "$.body.session.sessionId"}
    And 调用 POST /api/esm/v1/answering/answer，用标签 "A"，body 传 {"sessionId": "${sessionId}", "questionNo": 1, "choiceValue": 3}，用 extract_json 捕获 {"answeredAtMs": "$.body.savedAnswer.answeredAtMs"}
    When 再次调用 POST /api/esm/v1/answering/answer，用标签 "A"，body 传 {"sessionId": "${sessionId}", "questionNo": 1, "choiceValue": 3}
    Then statusCode 应该是 "0"
    And body.savedAnswer.answerVersion 应该是 1
    And body.savedAnswer.modified 应该是 false
    And body.savedAnswer.answeredAtMs 应该等于 ${answeredAtMs}
    And body.session.answeredCount 应该是 1

  Scenario: TC-ESM30-AS-05 回退改答递增答案版本并标记已修改
    Given 调用 POST /api/esm/v1/answering/start-or-resume，用标签 "A"，用 extract_json 捕获 {"sessionId": "$.body.session.sessionId"}
    And 依次提交第 1 到第 4 题，choiceValue 都传 3
    When 调用 POST /api/esm/v1/answering/answer，用标签 "A"，body 传 {"sessionId": "${sessionId}", "questionNo": 2, "choiceValue": 5}
    Then statusCode 应该是 "0"
    And body.savedAnswer.questionNo 应该是 2
    And body.savedAnswer.choiceValue 应该是 5
    And body.savedAnswer.answerVersion 应该是 2
    And body.savedAnswer.modified 应该是 true
    When 调用 POST /api/esm/v1/answering/start-or-resume，用标签 "A"
    Then body.savedAnswers 应该有 4 条
    And body.savedAnswers 里第 2 题的 choiceValue 应该是 5
    And body.savedAnswers 里第 3 题的 choiceValue 应该是 3
    And body.savedAnswers 里第 4 题的 choiceValue 应该是 3

  Scenario: TC-ESM30-AS-06 题号越界被拒
    Given 调用 POST /api/esm/v1/answering/start-or-resume，用标签 "A"，用 extract_json 捕获 {"sessionId": "$.body.session.sessionId"}
    When 调用 POST /api/esm/v1/answering/answer，用标签 "A"，body 传 {"sessionId": "${sessionId}", "questionNo": 0, "choiceValue": 3}
    Then bizCodeName 应该是 "BIZ_ESM_ASSESSMENT_QUESTION_OUT_OF_RANGE"
    And canonicalCode 应该是 "CANONICAL_ERROR_CODE_INVALID_PARAMETER"
    When 调用 POST /api/esm/v1/answering/answer，用标签 "A"，body 传 {"sessionId": "${sessionId}", "questionNo": 145, "choiceValue": 3}
    Then bizCodeName 应该是 "BIZ_ESM_ASSESSMENT_QUESTION_OUT_OF_RANGE"

  Scenario: TC-ESM30-AS-07 选项值越界被拒
    Given 调用 POST /api/esm/v1/answering/start-or-resume，用标签 "A"，用 extract_json 捕获 {"sessionId": "$.body.session.sessionId"}
    When 调用 POST /api/esm/v1/answering/answer，用标签 "A"，body 传 {"sessionId": "${sessionId}", "questionNo": 1, "choiceValue": 0}
    Then bizCodeName 应该是 "BIZ_ESM_ASSESSMENT_QUESTION_OUT_OF_RANGE"
    When 调用 POST /api/esm/v1/answering/answer，用标签 "A"，body 传 {"sessionId": "${sessionId}", "questionNo": 1, "choiceValue": 7}
    Then bizCodeName 应该是 "BIZ_ESM_ASSESSMENT_QUESTION_OUT_OF_RANGE"

  Scenario: TC-ESM30-AS-08 跳答还没到的题号被拒且进度不变
    Given 调用 POST /api/esm/v1/answering/start-or-resume，用标签 "A"，用 extract_json 捕获 {"sessionId": "$.body.session.sessionId"}
    When 调用 POST /api/esm/v1/answering/answer，用标签 "A"，body 传 {"sessionId": "${sessionId}", "questionNo": 3, "choiceValue": 3}
    Then statusCode 不应该是 "0"
    And bizCodeName 应该是 "BIZ_ESM_ASSESSMENT_QUESTION_OUT_OF_RANGE"
    When 调用 POST /api/esm/v1/answering/start-or-resume，用标签 "A"
    Then body.session.currentQuestionNo 应该是 1
    And body.session.answeredCount 应该是 0

  Scenario: TC-ESM30-AS-09 session 不存在时提交答案被拒
    When 调用 POST /api/esm/v1/answering/answer，用标签 "A"，body 传 {"sessionId": "answering_session_does_not_exist", "questionNo": 1, "choiceValue": 3}
    Then statusCode 不应该是 "0"
    And bizCodeName 应该是 "BIZ_ESM_ASSESSMENT_SESSION_NOT_FOUND"
    And canonicalCode 应该是 "CANONICAL_ERROR_CODE_NOT_FOUND"

  Scenario: TC-ESM30-AS-10 重新开始必须带确认标记
    Given 调用 POST /api/esm/v1/answering/start-or-resume，用标签 "A"，用 extract_json 捕获 {"sessionId": "$.body.session.sessionId"}
    And 调用 POST /api/esm/v1/answering/answer，用标签 "A"，body 传 {"sessionId": "${sessionId}", "questionNo": 1, "choiceValue": 3}
    When 调用 POST /api/esm/v1/answering/restart，用标签 "A"，body 传 {"previousSessionId": "${sessionId}", "confirmed": false}
    Then statusCode 不应该是 "0"
    And statusMsg 应该包含 "confirmation required"

  Scenario: TC-ESM30-AS-11 确认后重新开始，新 session 从第一题起且尝试次数递增
    Given 调用 POST /api/esm/v1/answering/start-or-resume，用标签 "A"，用 extract_json 捕获 {"sessionId": "$.body.session.sessionId"}
    And 调用 POST /api/esm/v1/answering/answer，用标签 "A"，body 传 {"sessionId": "${sessionId}", "questionNo": 1, "choiceValue": 3}
    When 调用 POST /api/esm/v1/answering/restart，用标签 "A"，body 传 {"previousSessionId": "${sessionId}", "confirmed": true}
    Then statusCode 应该是 "0"
    And body.session.sessionId 不应该等于原来的 sessionId
    And body.session.attemptNo 应该是 2
    And body.session.currentQuestionNo 应该是 1
    And body.session.answeredCount 应该是 0
    And body.session.restartedFromSessionId 应该等于原来的 sessionId
    And body.questions 应该有 144 条

  Scenario: TC-ESM30-AS-12 断点续答返回已保存答案和当前题号
    Given 调用 POST /api/esm/v1/answering/start-or-resume，用标签 "A"，用 extract_json 捕获 {"sessionId": "$.body.session.sessionId"}
    And 依次提交第 1 到第 5 题，choiceValue 都传 4
    When 调用 POST /api/esm/v1/answering/start-or-resume，用标签 "A"
    Then statusCode 应该是 "0"
    And body.session.sessionId 应该等于 ${sessionId}
    And body.session.currentQuestionNo 应该是 6
    And body.session.answeredCount 应该是 5
    And body.savedAnswers 应该有 5 条

  Scenario: TC-ESM30-AS-13 没有量表结果时读取最新结果返回未找到
    When 调用 GET /api/esm/v1/result/latest，用标签 "A"
    Then statusCode 不应该是 "0"
    And bizCodeName 应该是 "BIZ_ESM_ASSESSMENT_SCALE_RESULT_NOT_FOUND"
    And canonicalCode 应该是 "CANONICAL_ERROR_CODE_NOT_FOUND"

  # 这条要跑完 144 题，约 1 分钟。
  Scenario: TC-ESM30-AS-14 答满 144 题后生成量表结果且分数与解释按维度对齐
    Given 用 esm_new_user 建一个标签为 "B" 的用户，做完量表
    When 调用 GET /api/esm/v1/result/latest，用标签 "B"
    Then statusCode 应该是 "0"
    And body.result.resultId 应该等于标签 "B" 的 resultId
    And body.result.scaleTitle 应该非空
    And body.result.scoreResults 应该非空
    And body.result.dimensionInterpretations 的条数应该和 body.result.scoreResults 相同
    And body.result.scoreResults 的每条都应该有非空的 dimensionId 和 title
    And body.result.dimensionInterpretations 的 dimensionId 集合应该和 body.result.scoreResults 的完全一致
    # 值域：16 维分值必须落在 [-3, 3]。用 all_gte / all_lte，不要用 gt / lt——
    # 后者只比路径命中的第一个值，越界的维度排在后面就查不到（缺陷 7102943924 就是这个形状）。
    And body.result.scoreResults[*].score 的每个值都应该不小于 -3
    And body.result.scoreResults[*].score 的每个值都应该不大于 3
    And body.result.sessionSource 应该是 "SCALE_RESULT_SESSION_SOURCE_ANSWERING_SESSION"

  # 前置：一个不做量表的新用户，约 5 秒
  #
  # 缺陷 7099268477：答题接口耗时在 300ms 到 1.9s 之间波动，页面上能明显感到卡顿。
  # 那条是压测脚本一次性跑出来的，脚本产出不进回归，所以它修没修、有没有回退，
  # 此后没有任何用例在看。这条把它变成可回归的。
  #
  # 判 p90 不判单次，也不判 max：2026-08-31 在 feat-esm-v3-0 上实测（n=10，复用连接）
  #   /answering/answer        p50 398ms  p90 403ms  max 406ms
  #   /runtime/state           p50 394ms  p90 1253ms max 1266ms
  # 同一批里最便宜的免登录接口反而有 1.2s 的长尾，说明单次采样分不出真实劣化和抖动。
  # 上界取实测 p90 的三倍，够宽到不被抖动打中，又比缺陷里那个 1.9s 的尾巴紧。
  # 换泳道或换 runner 要重新测这个基线，不要照搬 PRD 里的服务端耗时指标——那是另一个量，
  # 这里量的是 runner 到网关的往返，含网络。
  Scenario: TC-ESM30-AS-15 重复提交同一答案的写路径耗时不劣化
    Given 用 esm_new_user 建一个标签为 "P" 的用户，不做量表
    When 调用 POST /api/esm/v1/answering/start-or-resume，用标签 "P"，用 extract_json 捕获 {"sessionId": "$.body.session.sessionId"}
    Then statusCode 应该是 "0"
    # 同一题同一答案重复提交按契约是幂等的（见 TC-ESM30-AS-04），所以可以安全地连打十次
    When 用 esm_latency_probe 打 POST /api/esm/v1/answering/answer，用标签 "P"，body 传 {"sessionId": "${sessionId}", "questionNo": 1, "choiceValue": 3}，采样 10 次
    # 先判有没有失败：失败的调用往往在参数校验就返回了，比成功的快得多，
    # 混进分布会让一个全在报错的接口看起来性能很好
    Then failed 应该是 0
    And p90Ms 应该小于 1200
