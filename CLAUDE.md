# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this is

Hercules is an AI agent for end-to-end testing. The public contract is **Gherkin in, reports out**: a `.feature` file goes in, and JUnit XML + HTML reports plus proofs (screenshots, video, network logs) come out. Everything in between is a LangGraph state machine driving LLM agents that call Playwright/API/SQL/security tools.

Python `>=3.11,<3.14`. Dependencies are managed with **uv** (`uv.lock` is committed).

## Commands

```bash
make setup-uv      # pip install uv
make install       # uv sync
make install-extra # uv sync --all-extras
```

**Gotcha:** `make install` is `uv sync && exit && uv run playwright install --with-deps` under `.ONESHELL:` — the `exit` kills the shell, so Playwright browsers are *never* installed by it. Run this separately after installing:

```bash
uv run playwright install --with-deps
```

Format / lint / test:

```bash
make fmt    # isort + black on testzeus_hercules/ and tests/
make lint   # depends on fmt, then black --check (mypy is commented out)
make test   # depends on lint, installs playwright, then pytest + coverage
```

`make lint` runs `fmt` first, so it rewrites files before checking and effectively cannot fail on formatting. Black is invoked as `--target-version py311 -l 200` — **line length 200**, not 88. `pyproject.toml` carries a large `[tool.ruff]` ruleset that nothing in the Makefile or CI actually runs.

Single test:

```bash
uv run pytest -v tests/test_simple_hercules_langgraph.py::test_name
```

```bash
uv run pytest -v "tests/test_feature_execution.py::test_feature_execution[accessibility]"
```

`make test-case` prompts interactively for the second form.

Running Hercules itself:

```bash
make run              # python -m testzeus_hercules against the opt/ folder
make run-interactive  # chat REPL; useful for probing tools without a feature file
```

```bash
testzeus-hercules --input-file opt/input/test.feature --output-path opt/output --test-data-path opt/test_data
```

## Test suite layout

Two very different kinds of tests live in `tests/`:

- **Unit tests** (`test_simple_hercules_langgraph.py`, `test_mcp_langgraph.py`, `test_cli_config_file.py`, `test_llm_helper.py`, …) use fake LLMs and monkeypatching. Fast, no credentials.
- **`test_feature_execution.py`** parameterizes over every folder in `tests/test_features/` and shells out to `python -m testzeus_hercules` as a subprocess with a scrubbed env (`IS_TEST_ENV=true`, `HOME` redirected into `tests/run_data/<feature>/`). These make **real LLM calls and drive a real browser** — they need `LLM_MODEL_NAME`/`LLM_MODEL_API_KEY` or an `agents_llm_config.json`. Marked `@pytest.mark.flaky(reruns=2)`.

`tests/future_test_features/` and `tests/test_not_for_ci/` are *not* collected — only `tests/test_features/` is. Move a feature folder into `test_features/` to make it part of CI.

Feature folder convention: `<name>/<name>.feature`, optional `expected_results.txt` (compared against the produced JUnit XML counts; auto-created from actuals on first run), and any other file becomes test data.

`main-push.yml` (the PR/main CI) only runs `make lint`. Full test runs live in `main-test.yml`/`non-main-test.yml`, which are `workflow_dispatch`-only and build an `agents_llm_config.json` via `helper_scripts/write_litellm_config.py`.

## Architecture

### Execution path

`__main__.py` → `gherkin_helper.process_feature_file()` splits the input `.feature` into one file **per scenario** → for each scenario, `SingleCommandInputRunner` (`core/runner.py`) → `SimpleHercules` (`core/simple_hercules.py`) → JUnit XML per scenario, merged and rendered to HTML at the end.

Three top-level modes, selected in `a_main()`: guided (`--guided`/`--test`, generates Gherkin first, `--dry-run` stops there), bulk (`EXECUTE_BULK`, treats every subdir of `<project_base>/tests/` as its own project base), and single (default).

### The graph

`SimpleHercules._build_graph()` compiles a LangGraph `StateGraph` with exactly three nodes:

- `planner` — entry point. Returns **strict JSON**: `plan`, `next_step`, `target_helper`, `terminate`, `final_response`, `is_assert`, `assert_summary`, `is_passed`.
- `executor` — routes `target_helper` to a nav agent and runs its tool loop. Always returns control to `planner`.
- `assertion` — terminal; trusts the planner's `is_passed`/`assert_summary`.

`AgentState` (a `TypedDict` at the top of `simple_hercules.py`) is the contract between nodes. Beyond the planner JSON it carries `completed_step_signatures` (normalized `next_step` strings used for repeat detection — a repeat logs `PLANNER_REPEAT_NOTICE` and is *never* silently converted into success) and the token/cost/timing accounting fields that feed the JUnit report.

`docs/Migration/ARCHITECTURE.md` is the authoritative description of this contract, including the `target_helper` → agent mapping table. Keep it in sync when changing the graph.

### Agents

All nav agents subclass `BaseNavAgent` (or `MultimodalBaseNavAgent` for vision-capable ones). An agent is little more than: a class-level `prompt`, an `agent_name`, an LLM, and the tools the registry has filed under that `agent_name`. `browser_nav_agent`, `api_nav_agent`, `sec_nav_agent`, `sql_nav_agent`, `time_keeper_nav_agent`, `mcp_nav_agent`, `executor_nav_agent`. Unknown `target_helper` values fall back to `browser_nav_agent`.

The system prompt is assembled as: agent prompt (or a config-supplied override) + current timestamp, with `$basic_test_information` substituted from long-term memory (`core/memory/static_ltm.py`, which folds in `test_data/` contents plus stored/run data).

`_run_nav_agent()` runs a bounded tool-calling loop that exits on `##TERMINATE TASK##` or round exhaustion. `_helper_response_succeeded()` scans the response for failure markers (`[error]`, `contradict`, `max nav rounds`, …) — a terminate string alone is not treated as success.

### Tools

Tools are plain async Python functions decorated with `@tool(agent_names=[...], description=..., name=...)` from `core/tools/tool_registry.py`. The decorator just appends to a module-level `defaultdict` keyed by agent name; `utils/langchain_tools.py` later converts each entry into a LangChain `StructuredTool`, deriving the Pydantic arg schema from `Annotated[T, "description"]` parameter annotations. **The `Annotated` description is what the LLM sees** — write it for the model, not for humans.

Both `core/tools/__init__.py` and `core/extra_tools/__init__.py` import every module in their package via `pkgutil.iter_modules`, so dropping a new file in either directory is enough to register its tools. `extra_tools/` is gated behind `LOAD_EXTRA_TOOLS`. `ADDITIONAL_TOOL_DIRS` loads tools from arbitrary paths at runtime.

Keep tool signatures provider-portable: scalars (`str`, `float`, `bool`) and `List[Dict[str, str]]` for bulk actions. Avoid tuples — the generated `prefixItems` JSON Schema is rejected by some providers. No-arg tools need an explicit empty schema.

Browser tools identify elements by an injected **`md` DOM attribute**, not CSS/XPath. Sensing tools (`get_interactive_elements`, `get_input_fields`, `get_page_text`) deliberately return compacted JSON/text — raw DOM payloads blow token limits and trigger provider `INVALID_ARGUMENT` errors on enterprise apps.

### Configuration

`config.py` holds a singleton reached via `get_global_conf()` / `set_global_conf()`. Resolution order in `BaseConfigManager.__init__`: load `.env` → merge env vars → parse CLI (`--config` file first, then individual flags) → validate → finalize defaults. **CLI wins over env.**

`IS_TEST_ENV=true` suppresses `.env` loading and skips LLM-config validation, which is what lets unit tests import `config.py` without credentials.

LLM configuration has two paths, both funneled through `AgentsLLMConfigManager`:

- **Direct** — `LLM_MODEL_NAME` / `LLM_MODEL_API_KEY` / `LLM_MODEL_BASE_URL` (or `--llm-model*` flags). One model for everything.
- **Per-agent** — `AGENTS_LLM_CONFIG_FILE` + `AGENTS_LLM_CONFIG_FILE_REF_KEY`. The file is keyed by provider profile (`litellm`, `openai_gpt`, `anthropic`, `ollama`, …), each containing `planner_agent` / `nav_agent` / `helper_agent` stanzas. See `agents_llm_config-example.json.txt`.

Only three agent configs are ever fetched (`planner_agent`, `nav_agent`, `helper_agent`); all nav agents share the single `nav_agent` config. Portkey routing wraps these configs transparently when enabled.

`utils/model_utils.adapt_llm_params_for_model()` rewrites LLM params per model family (e.g. GPT-5 needs `max_completion_tokens` instead of `max_tokens`) — extend it there rather than special-casing at call sites.

### Output layout

Under the project base (default `opt/`): `input/` (feature files), `gherkin_files/` (per-scenario splits), `output/run_<ts>/` (JUnit XML + HTML), `log_files/<scenario>/run_<ts>/agent_inner_thoughts.json` (the planner/helper trace — **start here when debugging a run**), `proofs/<scenario>/run_<ts>/` (screenshots, videos, network logs, plus tool-written `api_logs.log`, `security_logs.log`, `accessibility_logs.csv`), `test_data/`.

Full env var reference: `docs/environment_variables.md`. Project structure details: `docs/run_guide.md`.

## Conventions

- Conventional commits (`fix(package): ... 🎉`); emojis are fine.
- Releases are cut with `make release`, which bumps `pyproject.toml`, tags, and pushes — GitHub Actions handles PyPI.
- Telemetry is Sentry-based and on by default; `ENABLE_TELEMETRY=0` disables it. `telemetry.py` maintains its own denylist for `sys.argv`/PII.
