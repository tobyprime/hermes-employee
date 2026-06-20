"""Employee - 路径与配置管理"""

import os
from pathlib import Path

PLUGIN_DIR = Path.home() / ".hermes" / "employee"
CENTRAL_DB = Path(os.environ.get("HERMES_EMPLOYEE_DB_PATH") or str(PLUGIN_DIR / "hermes_employee.db"))
CONFIG_FILE = PLUGIN_DIR / "config.yaml"
SESSIONS_DIR = PLUGIN_DIR / "sessions"
DINGTALK_STATE_DB = Path(os.environ.get("HERMES_EMPLOYEE_DINGTALK_STATE_DB") or str(PLUGIN_DIR / "dingtalk_state.db"))


def session_db_path(session_id: str) -> str:
    """Get the session database path for a given session ID."""
    SESSIONS_DIR.mkdir(parents=True, exist_ok=True)
    return str(SESSIONS_DIR / f"{session_id}.session.db")

# 环境变量配置（带默认值）
IDLE_DURATION = int(os.environ.get("HERMES_EMPLOYEE_IDLE_DURATION", "30"))
SLEEP_DURATION = int(os.environ.get("HERMES_EMPLOYEE_SLEEP_DURATION", "60"))
PEEK_COOLDOWN = int(os.environ.get("HERMES_EMPLOYEE_PEEK_COOLDOWN", "1"))
TOOL_TIMEOUT = int(os.environ.get("HERMES_EMPLOYEE_TOOL_TIMEOUT", "30"))
# wait 命令检测到第一条消息后，继续收集同批次消息的缓冲窗口（秒）
WAIT_BATCH_WINDOW = float(os.environ.get("HERMES_EMPLOYEE_WAIT_BATCH_WINDOW", "1.0"))
