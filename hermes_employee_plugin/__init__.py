"""Hermes Employee Plugin - Hermes Agent 插件入口

Registers tools, lifecycle hooks, slash commands, and skill for
integrating the employee message system into Hermes Agent.

消息投递：每次工具调用返回后自动检查 DB 新消息，确认已读、渲染、
追加到工具结果末尾。通过 transform_tool_result hook 实现，对所有
工具（含系统内置工具）生效，无需 AI 主动调用 employee_check。
"""

from __future__ import annotations

import logging
from pathlib import Path

from . import hooks as employee_hooks
from . import tools as employee_tools

logger = logging.getLogger("hermes_employee_plugin")


def register(ctx) -> None:
    """Plugin entry point — called by Hermes Agent at startup."""
    employee_hooks._set_plugin_ctx(ctx)

    # ── Tools ────────────────────────────────────────────────
    _register_tools(ctx)

    # ── Lifecycle hooks ──────────────────────────────────────
    ctx.register_hook("post_llm_call", employee_hooks.on_post_llm_call)
    ctx.register_hook("pre_llm_call", employee_hooks.on_pre_llm_call)
    ctx.register_hook("transform_tool_result", employee_hooks.on_transform_tool_result)

    # ── Slash command ────────────────────────────────────────
    ctx.register_command(
        "employee",
        handler=_handle_slash,
        description="Manage employee: activate/deactivate, source status",
    )

    # ── Skill ────────────────────────────────────────────────
    skills_dir = Path(__file__).parent / "skills"
    for child in sorted(skills_dir.iterdir()):
        skill_md = child / "SKILL.md"
        if child.is_dir() and skill_md.exists():
            ctx.register_skill(child.name, skill_md, description="多渠道消息聚合与待办管理技能")


def _register_tools(ctx) -> None:
    """Register all employee tools."""

    ctx.register_tool(
        name="employee_activate",
        toolset="employee",
        schema={
            "name": "employee_activate",
            "description": "激活 Employee 会话。在开始工作前必须先调用此工具。创建会话数据库，设置消息游标。",
            "parameters": {"type": "object", "properties": {}, "required": []},
        },
        handler=lambda args, **kw: employee_tools.tool_activate(kwargs=kw),
    )

    ctx.register_tool(
        name="employee_deactivate",
        toolset="employee",
        schema={
            "name": "employee_deactivate",
            "description": "停用 Employee 会话，清理会话数据库。",
            "parameters": {"type": "object", "properties": {}, "required": []},
        },
        handler=lambda args, **kw: employee_tools.tool_deactivate(kwargs=kw),
    )

    ctx.register_tool(
        name="employee_check",
        toolset="employee",
        schema={
            "name": "employee_check",
            "description": "手动检查最新消息。通常不需要主动调用，因每次工具调用后会自动检查。",
            "parameters": {"type": "object", "properties": {}, "required": []},
        },
        handler=lambda args, **kw: employee_tools.tool_check(kwargs=kw),
    )

    ctx.register_tool(
        name="employee_send",
        toolset="employee",
        schema={
            "name": "employee_send",
            "description": "向中央消息队列发送一条消息。可用于记录事件、通知或其他会话。",
            "parameters": {
                "type": "object",
                "properties": {
                    "type": {"type": "string", "description": "消息类型命名空间，如 github.push、alert、note"},
                    "title": {"type": "string", "description": "消息标题"},
                    "content": {"type": "string", "description": "消息正文"},
                    "category": {"type": "string", "enum": ["popup", "normal", "silent", ""], "description": "分类，留空则自动判断"},
                    "props": {"type": "string", "description": 'JSON 属性，如 {"repo":"my-project"}'},
                },
                "required": ["type", "title", "content"],
            },
        },
        handler=lambda args, **kw: employee_tools.tool_send(
            type_=args.get("type", ""),
            title=args.get("title", ""),
            content=args.get("content", ""),
            category=args.get("category", ""),
            props_str=args.get("props", "{}"),
            kwargs=kw,
        ),
    )

    todo_schema = {
        "name": "employee_todo",
        "description": "管理待办事项。支持添加/查看/开始/完成/取消/删除/查看活跃任务。",
        "parameters": {
            "type": "object",
            "properties": {
                "action": {"type": "string", "enum": ["add", "list", "start", "done", "cancel", "delete", "active"], "description": "操作类型"},
                "id": {"type": "integer", "description": "待办 ID（start/done/cancel/delete 需要）"},
                "title": {"type": "string", "description": "标题（add 需要）"},
                "approach": {"type": "string", "description": "实现思路（add 可选）"},
                "duration": {"type": "string", "description": "预计耗时，如 30s、5m、2h（add 需要）"},
                "wait_time": {"type": "string", "description": "空闲等待时间，超时后自动恢复任务，如 5m、30s（add 可选）"},
                "parent": {"type": "integer", "description": "父任务 ID（add 可选）"},
                "after": {"type": "integer", "description": "依赖任务 ID，必须先完成的依赖（add 可选）"},
                "status": {"type": "string", "enum": ["pending", "active", "paused", "done", "cancelled"], "description": "筛选状态（list 可选）"},
            },
            "required": ["action"],
        },
    }
    ctx.register_tool(
        name="employee_todo",
        toolset="employee",
        schema=todo_schema,
        handler=lambda args, **kw: employee_tools.tool_todo(args, kwargs=kw),
    )

    ctx.register_tool(
        name="employee_history",
        toolset="employee",
        schema={
            "name": "employee_history",
            "description": "查看历史消息记录。",
            "parameters": {
                "type": "object",
                "properties": {
                    "limit": {"type": "integer", "description": "返回条数（默认 20）"},
                    "category": {"type": "string", "description": '筛选分类，逗号分隔如 "popup,normal"'},
                    "type_pattern": {"type": "string", "description": '消息类型模式，如 "github.*"、"dingtalk.*"'},
                },
                "required": [],
            },
        },
        handler=lambda args, **kw: employee_tools.tool_history(kwargs=kw, limit=args.get("limit", 20), category=args.get("category", ""), type_pattern=args.get("type_pattern", "")),
    )

    ctx.register_tool(
        name="employee_config",
        toolset="employee",
        schema={
            "name": "employee_config",
            "description": "管理 Employee 配置，包括过滤规则。",
            "parameters": {
                "type": "object",
                "properties": {
                    "action": {"type": "string", "enum": ["get", "set", "rules", "add_rule", "remove_rule"], "description": "操作类型"},
                    "key": {"type": "string", "description": "配置键路径，如 rules.popup"},
                    "value": {"type": "string", "description": "配置值（set 需要）"},
                    "rule_type": {"type": "string", "enum": ["popup", "popup_excluded", "silent", "silent_excluded"], "description": "规则类型（add_rule/remove_rule 需要）"},
                    "pattern": {"type": "string", "description": "消息类型正则模式（add_rule 需要）"},
                    "rule_index": {"type": "integer", "description": "规则索引（remove_rule 需要）"},
                },
                "required": ["action"],
            },
        },
        handler=lambda args, **kw: employee_tools.tool_config(args),
    )

    ctx.register_tool(
        name="employee_source_status",
        toolset="employee",
        schema={
            "name": "employee_source_status",
            "description": "查看消息源（GitHub Webhook、GitHub Inbox、钉钉）的运行状态。",
            "parameters": {"type": "object", "properties": {}, "required": []},
        },
        handler=lambda args, **kw: employee_tools.tool_source_status(),
    )


# ── Slash command handler ────────────────────────────────────

_HELP_TEXT = """\
/employee — Hermes Employee 管理

子命令:
  activate          激活 Employee（启动会话）
  deactivate        停用 Employee（清理会话）
  check             检查新消息
  status            查看消息源状态
  config            查看配置

注意: 部分功能需要在 Hermes Agent 会话中使用 employee_* 工具。
"""


def _handle_slash(raw_args: str) -> str | None:
    argv = raw_args.strip().split()
    logger.info("_handle_slash: raw_args=%r argv=%r", raw_args, argv)
    if not argv or argv[0] in {"help", "-h", "--help"}:
        return _HELP_TEXT

    sub = argv[0]

    if sub == "activate":
        return "请在会话中直接和我说「激活 employee」，我会自动调用 employee_activate 工具来激活。"
    elif sub == "deactivate":
        return "请在会话中和我说「停用 employee」。"
    elif sub == "check":
        return "请直接在会话中让我检查消息。"
    elif sub == "status":
        return employee_tools.tool_source_status()
    elif sub == "config":
        return employee_tools.tool_config({"action": "rules"})
    else:
        return f"Unknown subcommand: {sub}\n\n{_HELP_TEXT}"
