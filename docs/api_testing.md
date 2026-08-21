# API 测试

接口测试的对外契约和其它测试一样：写 `.feature`，跑，拿 JUnit + HTML 报告。这篇讲接口这块具体怎么写、断言用哪个工具、什么时候该走 [Hurl](api_testing_hurl.md) 而不是 LLM。

## 两条腿

| | 探索性 / 描述性 | 确定性回归 |
|---|---|---|
| 用什么写 | `.feature`，业务语言描述 | `.hurl`，断言写死在文件里 |
| 谁判定通过 | `api_nav_agent` 现场调断言工具 | Hurl 自己执行断言 |
| 适合 | 断言依赖上下文判断、跨 UI/API 场景、探索性用例 | 契约已经稳定、断言明确的回归用例 |
| 需要 LLM key | 是 | 否，CI 上可以无条件跑 |

判据很简单：**这条用例的断言，写用例的人现在就能写死吗？** 能写死就挪 `.hurl`（见 [api_testing_hurl.md](api_testing_hurl.md)）；需要"看到响应再决定断言什么"就留在 `.feature`。两条路不互相替代，一个套件里可以并存。

## `.feature` 这条路：断言工具清单

`api_nav_agent` 名下的工具都在 `testzeus_hercules/core/tools/`，用例正文里不用点名工具，写业务语言即可，planner 会路由并指示 helper 调用它们。

| 工具 | 何时用 | 关键参数 |
|---|---|---|
| `generic_http_api` | 发起 HTTP 请求 | `method`, `url`, `auth_type`/`auth_value`, `query_params`, `body`/`body_mode`, `headers` |
| `assert_json_path` | 断言响应里的**某一个字段** | `response_json`, `path`（JSONPath）, `operator`（`eq`/`ne`/`contains`/`exists`/`not_exists`/`gt`/`lt`/`count_eq`/`eq_path`）, `expected` |
| `extract_from_response` | 把一个响应里的值存下来，供后续请求引用 | `response_json`, `path`, `as_name` |
| `validate_response_schema` | 断言响应的**整体形状**（字段齐全、类型对），schema 是你手写的 | `response_json`, `json_schema`, `path`（可选，校验子对象） |
| `validate_against_openapi_spec` | 同上，但 schema 从 spec 文件里按 `path+method+status` 自动取，不用手抄 | `response_json`, `spec_path`, `path`, `method`, `status_code`, `response_path`（可选） |
| `run_hurl_suite` | 调用一个已经写好的 `.hurl` 回归套件 | `hurl_path`, `variables`, `timeout_seconds` |

**为什么这么分工**：`assert_json_path` 断言单个字段值；`validate_response_schema`/`validate_against_openapi_spec` 断言整体结构（哪个字段该有、类型对不对），两者不是互相替代关系——校验完形状之后还应该对关键字段单独断言值。`api_nav_agent` 的 system prompt（[api_nav_agent.py](../testzeus_hercules/core/agents/api_nav_agent.py)）里写死了一条硬规则：**判定 pass/fail 必须调用这些工具，不许自己读 JSON 下结论**——这是让接口用例结果可复现的关键，不要在改 prompt 时把这条删掉。

### 跨步骤传值

```
Given 调用 POST /login，body 传 {"username": "...", "password": "..."}
Then statusCode 应该是 200
And 提取 body.accessToken，存成 "token"
When 调用 GET /me，Authorization 传 "Bearer ${token}"
Then body.userId 应该等于标签 "A" 的 userId
```

`${name}` 是 Hercules 自己的插值语法，在 `generic_http_api` 发请求前解析，`url`/`query_params`/`headers`/`auth_value`/`body` 都支持。整值匹配（`"${token}"`）保留原始类型，嵌入到更长字符串里（`"/users/${uid}/orders"`）按文本替换。变量按 Scenario 隔离，不会跨用例串值。

### 契约校验怎么选

有现成 OpenAPI/Swagger spec 就用 `validate_against_openapi_spec`，不要把 schema 抄进 `validate_response_schema`——抄的东西会和 spec 脱节。spec 太大时先用 `helper_scripts/trim_openapi_spec.py` 裁剪掉当前用例用不到的 path（见下）。

`validate_response_schema` 留给没有 spec、或者只想临时校验一个响应片段形状的场景。

## 支撑工具

这几个是命令行脚本，不是 LLM 工具，写用例/维护测试数据时手动跑：

- **`helper_scripts/trim_openapi_spec.py`**——spec 整份塞进 prompt 很费 token。这个脚本按 path 过滤，并对保留下来的 path 做 `$ref` 可达性分析，连带裁掉没被引用到的 schema。裁剪前后校验结果一致（已用仓库里两份真实 spec 验证过），纯粹省 token。

  ```bash
  python helper_scripts/trim_openapi_spec.py api_spec.json --paths /pet,/pet/{petId} --out api_spec.trimmed.json
  ```

- **`helper_scripts/lint_gherkin.py`**——Hercules 执行时按正则拆分 `.feature`，对非标准 Gherkin（小写步骤关键字、缺 `Feature:` 行）很宽容，这是特性不是 bug。但正则宽容也意味着像 Markdown 表格分隔行 `|---|---|` 这种非法语法会被正则当成一行真实数据，静默产出一条没人想测的用例。这个脚本用官方 Gherkin 解析器扫一遍，报告哪些文件不是标准 Gherkin、哪些文件用正则数的 Scenario 数和真正展开 Examples 后的数量对不上——**纯提示，不改变执行行为，默认退出码永远是 0**。

  ```bash
  make lint-gherkin
  # 或者
  python helper_scripts/lint_gherkin.py [路径...] [--strict]
  ```

- **`helper_scripts/generate_api_functional_gherkin_test.py`** / **`generate_api_security_gherkin_test.py`**——从 OpenAPI spec 批量生成功能/安全测试用的 Gherkin 草稿，需要自己的 OpenAI key（和 Hercules 运行时用的 LLM key 是分开的两回事）。

## 环境变量

- `API_REQUEST_TIMEOUT`——`generic_http_api` 的 HTTP 超时，默认 30 秒。见 [environment_variables.md](environment_variables.md)。
- `run_hurl_suite` 依赖 `hurl` 二进制（`brew install hurl` 或见 [hurl.dev](https://hurl.dev/docs/installation.html)），不是 Python 依赖，`uv sync` 不会装。

## 例子

`tests/test_not_for_ci/ApiTesting/`（Swagger 2.0，Petstore）和 `tests/test_not_for_ci/ApiTestingNew/`（OpenAPI 3.1，`tests/api_testing_app` 靶场）是两套完整例子，后者的 `hurl/` 子目录是对应的确定性对照组。ESM 那套更真实、覆盖更全的例子在 `additional_tools/esm_api.py` + `opt/cases/esm-*`，专用工具封装了泳道头、`baseReq`、登录态这些每次都要重复的样板，值得作为"专用工具封装前置成本"的参考实现。
