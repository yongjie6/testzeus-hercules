import os
import shutil
import sys
from typing import Generator

import pytest

os.environ.setdefault("IS_TEST_ENV", "true")

# Global constants
RUN_DATA_PATH = os.path.join(os.getcwd(), "run_data")
TEST_FEATURES_PATH = os.path.join(os.getcwd(), "test_features")


# Fixture to clean and prepare run_data directory
@pytest.fixture(scope="session", autouse=True)
def prepare_run_data() -> None:
    if os.path.exists(RUN_DATA_PATH):
        shutil.rmtree(RUN_DATA_PATH)
    os.makedirs(RUN_DATA_PATH)


@pytest.fixture(autouse=True)
def _restore_module_registry() -> Generator[None, None, None]:
    """每条用例跑完后，把 sys.modules 里的 testzeus_hercules.* 还原回它跑之前的样子。

    test_cli_config_file.py 和 test_llm_cli_aliases.py 都要测「import 期行为」，办法是
    清掉这些条目再重新 import 一份 config。那对它们自己是必要的；不还原就成了留给后面
    每个测试文件的陷阱：它们再拿到的是新模块，里面是另一个 SingletonConfigManager 类，
    而先前已 import 好的 tool_registry / api_calls / ui_assertions 仍握着旧模块。两套
    并存之后，任何「给 config 打桩再调工具」的测试都会打空，甚至整个模块从 sys.modules
    里消失。

    实测就是这么让 test_ui_assertions 的台账用例单跑绿、全量跑红的——而这种红看起来像
    被测代码的问题，不像测试之间的串扰，正是最费时间的一类假信号。放在 conftest 而不是
    那两个文件里，是因为它是全套测试共同依赖的不变量，下一个这么干的文件不该重新踩一遍。
    """
    saved = {name: module for name, module in sys.modules.items() if name == "testzeus_hercules" or name.startswith("testzeus_hercules.")}
    try:
        yield
    finally:
        current = {name for name in sys.modules if name == "testzeus_hercules" or name.startswith("testzeus_hercules.")}
        if current != set(saved) or any(sys.modules[name] is not module for name, module in saved.items()):
            for name in current:
                del sys.modules[name]
            sys.modules.update(saved)
