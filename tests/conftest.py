import sys
from pathlib import Path

import nonebot
from nonebot.adapters.onebot.v11 import Adapter

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

try:
    driver = nonebot.get_driver()
except ValueError:
    nonebot.init()
    driver = nonebot.get_driver()

driver.register_adapter(Adapter)
if nonebot.get_plugin("nonebot_plugin_ts3_tracker") is None:
    loaded = nonebot.load_plugin("nonebot_plugin_ts3_tracker")
    if loaded is None:
        raise RuntimeError("failed to load nonebot_plugin_ts3_tracker")
