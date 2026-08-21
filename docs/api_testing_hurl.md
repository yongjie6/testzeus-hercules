# Hurl 回归通道

这篇是 [api_testing.md](api_testing.md) 里"确定性回归"那条路的细节——两条腿怎么分工、`.feature` 那边有哪些断言工具，看那篇。这里只讲 `.hurl` 这条路本身：怎么装、怎么组织文件、变量怎么传、断言怎么写、产出落在哪。

## 环境要求

`run_hurl_suite` 依赖 `hurl` 二进制,不是 Python 依赖,`uv sync` 不会装它:

```bash
brew install hurl          # macOS
# 其它平台见 https://hurl.dev/docs/installation.html
```

没装的话,`run_hurl_suite` 返回 `assertion: ERROR` 并给出安装提示,不会跑出一个费解的报错。

## 文件组织约定

`.hurl` 文件放在对应 `.feature` 同目录下的 `hurl/` 子目录,一个 Scenario 对应一个文件,文件名用 Scenario 的语义命名(不是编号):

```
tests/test_not_for_ci/ApiTestingNew/
├── ApiTestingNew.feature
├── test_data.txt
└── hurl/
    ├── create_category_success.hurl
    └── create_category_missing_name.hurl
```

`run_hurl_suite` 的 `hurl_path` 传这个目录时,目录下所有 `*.hurl` 文件会被当成**同一个 suite** 一起跑,结果汇总成一份 JUnit。

## 变量传入约定

`.hurl` 文件里只写 `{{name}}` 占位符,不硬编码环境和账号——同一份文件换环境只换 `variables`,不用改文件本身:

```hurl
POST {{server}}auth/jwt/login
Content-Type: application/x-www-form-urlencoded
[FormParams]
username: {{username}}
password: {{password}}
```

调用时,`server`/`username`/`password` 这类值从 `test_data.txt` 里读、由 Gherkin 步骤指示 LLM 传给 `run_hurl_suite` 的 `variables` 参数;需要跨调用复用的值(比如前一步接口调用创建出来的 ID),用 `extract_from_response` 先提取,再在 `variables` 里写 `${name}` 引用它:

```python
run_hurl_suite(
    hurl_path="tests/test_not_for_ci/ApiTestingNew/hurl",
    variables={
        "server": "http://127.0.0.1:8000/",
        "username": "user@example.com",
        "password": "user@example.com",
        "run_id": "20260821-01",       # 避免重复跑撞同名资源
        "created_id": "${category_id}", # 复用前一步 extract_from_response 提取的值
    },
)
```

`${name}` 由 Hercules 在调 hurl 之前解析(复用 `extract_from_response`/`generic_http_api` 那套变量表);`{{name}}` 是 Hurl 自己的模板语法,在 hurl 进程内解析。两层语法分工明确,不会混淆。

## 断言写法

Hurl 的 `[Asserts]` **不支持数组字面量**做 JSONPath 比较(`jsonpath "$.x" == ["a","b"]` 会直接报语法错误),这是我们在写第一版对照组时踩到的坑。数组类字段拆成逐个下标 + `count`:

```hurl
[Asserts]
jsonpath "$.detail[0].loc" count == 2
jsonpath "$.detail[0].loc[0]" == "body"
jsonpath "$.detail[0].loc[1]" == "name"
```

## 产出与取证

- 每次 `run_hurl_suite` 调用把 hurl 的完整 stdout/stderr 追加写入 `proofs/hurl_logs.log`,和 `api_logs.log`/`security_logs.log` 同一惯例。
- JUnit 报告另存一份到 `proofs/hurl_results/<suite>-<timestamp>.xml`,`<testsuite>` 已经补好名字(Hurl 原生输出这个字段是空的,HTML 报告会渲染成没有辨识度的 `suite-N`)。
- 这份 JUnit 是独立保存的副本,**不会**被喂进 Hercules 自己的 `merge_junit_xml`——那个函数会在非 debug 模式下删除传入的文件,`run_hurl_suite` 从设计上就不把自己内部用的临时文件交给它,避免被删。工具返回给 planner 的是结构化摘要(`assertion`/`executed`/`succeeded`/`failed`/`failures[]`),走的是和 `assert_json_path` 一样的"结果喂给 planner 决定 is_passed"路径,不需要额外接线。

## 已知限制

- **hurl 8.0.1 有一个真实 bug**:如果 `--report-junit` 指向的路径已经存在且是空文件,hurl 会把它当成"已有报告要合并"去解析,读到空文件直接 panic。`run_hurl_suite` 内部用 `tempfile.mkdtemp()` 而不是 `mkstemp()` 规避了这个问题(前者只占一个目录、不预建文件),调用方不需要关心这条,记在这里是防止以后有人复现同样的坑。
- 一次 `run_hurl_suite` 调用只跑一层目录(不递归子目录)。
- `variables` 只接受字符串值——hurl 的 `--variable` 本身就是 `name=value` 的字符串接口。

## 对照组

`tests/test_not_for_ci/ApiTestingNew/hurl/` 是 `ApiTestingNew.feature` 的确定性对照组,针对同一个 `tests/api_testing_app` 靶场、同一份 `test_data.txt`,两条 Scenario 都已经跑通验证过。
