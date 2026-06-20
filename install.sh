#!/bin/bash
set -euo pipefail

REPO_DIR="$(cd "$(dirname "$0")" && pwd)"
HERMES_HOME="${HOME}/.hermes"
PLUGIN_DIR="${HERMES_HOME}/plugins/hermes-employee"
SKILL_LINK="${HERMES_HOME}/skills/hermes-employee"
HERMES_VENV="${HERMES_HOME}/hermes-agent/venv"

echo "==> Installing hermes-employee (Hermes Agent Plugin)..."

# 1. Install Python package into Hermes Agent's venv (so employee module is importable by the plugin)
if [ -f "${HERMES_VENV}/bin/pip" ]; then
    echo "    Installing into Hermes Agent venv: ${HERMES_VENV}"
    "${HERMES_VENV}/bin/pip" install -e "$REPO_DIR" --quiet
else
    echo "    Installing into system Python (Hermes venv not found at ${HERMES_VENV})"
    pip install -e "$REPO_DIR" --quiet
fi
echo "    Python package installed (hemployee CLI + employee + hermes_employee_plugin)"

# 2. Symlink plugin into Hermes Agent plugins directory
mkdir -p "${HERMES_HOME}/plugins"
if [ -L "$PLUGIN_DIR" ] || [ -d "$PLUGIN_DIR" ]; then
    rm -rf "$PLUGIN_DIR"
fi
ln -sfn "$REPO_DIR/hermes_employee_plugin" "$PLUGIN_DIR"
echo "    Plugin linked: $PLUGIN_DIR → $REPO_DIR/hermes_employee_plugin"

# 3. Enable plugin in Hermes Agent config
CONFIG_FILE="${HERMES_HOME}/config.yaml"
if [ -f "$CONFIG_FILE" ]; then
    # Check if already enabled
    if grep -q "hermes-employee" "$CONFIG_FILE" 2>/dev/null; then
        echo "    Plugin already enabled in config"
    else
        # Add to enabled plugins list
        if grep -q "^plugins:" "$CONFIG_FILE" 2>/dev/null; then
            # Append to existing plugins list
            sed -i '/^plugins:/a\  enabled:\n    - hermes-employee' "$CONFIG_FILE" 2>/dev/null || true
        else
            echo -e "\nplugins:\n  enabled:\n    - hermes-employee" >> "$CONFIG_FILE"
        fi
        echo "    Plugin enabled in config"
    fi
else
    cat > "$CONFIG_FILE" << 'HERMES_YAML'
plugins:
  enabled:
    - hermes-employee
HERMES_YAML
    echo "    Config created with plugin enabled"
fi

# 4. Create default employee config if not exists
EMPLOYEE_CONFIG="${HERMES_HOME}/employee/config.yaml"
if [ ! -f "$EMPLOYEE_CONFIG" ]; then
    mkdir -p "${HERMES_HOME}/employee"
    cat > "$EMPLOYEE_CONFIG" << 'YAML'
rules:
  popup: []
  popup_excluded: []
  silent: []
  silent_excluded: []
templates:
  brief: |
    ## 📬 消息简报
    弹窗消息 ({POPUP_MESSAGE_COUNT}):
    {NEW_POPUP_MESSAGES}

    新消息 ({MESSAGE_COUNT}):
    {NEW_MESSAGES}

    {NOW}
    💡 向消息源回复，不要在对话中直接输出
  brief_wait: |
    ## 📬 消息简报
    弹窗消息 ({POPUP_MESSAGE_COUNT}):
    {NEW_POPUP_MESSAGES}

    新消息 ({MESSAGE_COUNT}):
    {NEW_MESSAGES}

    {ACTIVE_TODOS}

    {NOW}
    💡 处理原则：
    1. 被私聊或在群聊中被 @ 时，先第一时间简短回应「收到，正在处理」，再执行任务。
    2. 处理消息时查看最近完整上下文，不要只看最新一条。
    3. 向消息源回复，不要在对话中直接输出。
    4. 复杂任务交给 subagent 或非交互式后台处理。
    5. 忙更高优先级事项时，首次回复说明状态。
    6. 善用 todo/task 系统跟踪和管理任务。
  brief_peek: |
    ## 📬 消息简报
    弹窗消息 ({POPUP_MESSAGE_COUNT}):
    {NEW_POPUP_MESSAGES}

    新消息 ({MESSAGE_COUNT}):
    {NEW_MESSAGES}
  item: "• [{MESSAGE_TYPE}] {MESSAGE_TITLE} ({MESSAGE_TIME_AGO}): {MESSAGE_CONTENT_CUTTED}"
  groups:
    group_header: "[{GROUP_TYPE}]"
    group_item_title: "  ├ {MESSAGE_TITLE}"
    group_item_remaining: "  └ 还有 {GROUP_REMAINING} 条同类型"
    group_overflow: "📎 还有 {GROUP_OVERFLOW_GROUPS} 类共 {GROUP_OVERFLOW_TOTAL} 条消息"
YAML
    echo "    Default employee config created: $EMPLOYEE_CONFIG"
fi

# 5. Link skills for legacy Claude Code support (optional)
mkdir -p "${HERMES_HOME}/skills"
ln -sfn "$REPO_DIR/skills/start_hemployee" "${HERMES_HOME}/skills/hermes-employee-start" 2>/dev/null || true
ln -sfn "$REPO_DIR/skills/stop_hemployee" "${HERMES_HOME}/skills/hermes-employee-stop" 2>/dev/null || true
echo "    Skills linked"

echo ""
echo "==> Installation complete!"
echo ""
echo "Next steps:"
echo "  1. Restart Hermes Agent (or run 'hermes plugins reload')"
echo "  2. Start background message sources:"
echo "     he mployee daemon"
echo "  3. In Hermes Agent, use the employee tools:"
echo "     - employee_activate   — 激活会话"
echo "     - employee_check      — 检查新消息"
echo "     - employee_send       — 发送消息"
echo "     - employee_todo       — 管理待办"
echo "     - employee_wait       — 等待消息"
echo "     - employee_source_status — 查看源状态"
