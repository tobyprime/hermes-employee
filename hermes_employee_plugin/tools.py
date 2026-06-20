"""Employee tool handlers — called from the plugin's registered tools.

IMPORTANT: Every handler must return a string (not dict).
Hermes Agent's registry.dispatch() puts the return value directly into
the conversation as tool result content.  Dicts cause API 400 errors on
providers that require string content (DeepSeek, etc.).
"""

from __future__ import annotations

import json
import logging
import subprocess
from pathlib import Path
from typing import Any

from employee import config as _config
from employee import db as _central_db
from employee import session as _session_db
from employee import todo as _todo_logic
from employee.config import session_db_path as _session_db_path
from employee.filter import classify_message
from employee.yaml_config import get_config_value, set_config_value, add_rule, remove_rule, list_rules
from ._common import check_and_format
from .background import tool_background_status

logger = logging.getLogger("hermes_employee_plugin.tools")


# ── Helpers ──────────────────────────────────────────────────


def _resolve_sid(kwargs: dict | None) -> str | None:
    """Extract session_id from Hermes Agent dispatch kwargs."""
    if kwargs:
        return kwargs.get("session_id")
    return None


def _format_message(m: dict) -> str:
    content = (m.get("content") or "")[:200]
    return (
        f"  [#{m['id']}] [{m['category']}] {m.get('type', '')} — "
        f"{m.get('title', '')[:80]}\n"
        f"       {content}"
    )


# ── Tool handlers (ALL return str) ───────────────────────────


def tool_activate(kwargs: dict | None = None) -> str:
    """Activate employee for current session."""
    sid = _resolve_sid(kwargs)
    if not sid:
        return json.dumps({"ok": False, "error": "session_id not found"})

    from .hooks import mark_activated

    _do_activate(sid)
    mark_activated(sid)

    db_path = _session_db_path(sid)
    cursor = _session_db.get_read_cursor(db_path)
    logger.info("employee activated: session=%s cursor=%d", sid, cursor)
    return json.dumps({"ok": True, "session": sid, "cursor": cursor})


def tool_deactivate(kwargs: dict | None = None) -> str:
    """Deactivate employee — remove session database and stop message injection."""
    sid = _resolve_sid(kwargs)
    if not sid:
        return json.dumps({"ok": False, "error": "session_id not found"})

    from .hooks import mark_deactivated

    db_path = _session_db_path(sid)
    if Path(db_path).exists():
        Path(db_path).unlink()
    mark_deactivated(sid)
    logger.info("employee deactivated: session=%s", sid)
    return json.dumps({"ok": True, "session": sid, "note": "deactivated"})


def _do_activate(sid: str) -> None:
    """Internal: activate, ensuring hook state matches DB state."""
    from .hooks import mark_activated

    db_path = _session_db_path(sid)
    _session_db.init_session_db(db_path)
    _central_db.init_central_db(_config.CENTRAL_DB)
    max_id = _central_db.get_max_message_id(_config.CENTRAL_DB)
    _session_db.set_read_cursor(db_path, max_id)
    mark_activated(sid)


def tool_check(kwargs: dict | None = None) -> str:
    """Check for new messages since last check. Returns formatted briefing string."""
    sid = _resolve_sid(kwargs)
    if not sid:
        return ""

    db_path = _session_db_path(sid)
    if not Path(db_path).exists():
        _do_activate(sid)

    result = check_and_format(db_path, sid, brief_key="brief_peek")
    return result


def tool_send(type_: str, title: str, content: str, category: str = "", props_str: str = "{}", kwargs: dict | None = None) -> str:
    """Send a message to the central database."""
    props = {}
    if props_str:
        try:
            props = json.loads(props_str)
        except json.JSONDecodeError:
            return json.dumps({"ok": False, "error": "Invalid props JSON"})

    if not category:
        category = classify_message(type_, props)

    msg_id = _central_db.insert_message(
        _config.CENTRAL_DB, type_=type_, title=title, content=content,
        props=props, category=category,
    )
    return json.dumps({"ok": True, "id": msg_id, "category": category})


def tool_todo(args: dict, kwargs: dict | None = None) -> str:
    """Manage todos — returns a human-readable string."""
    sid = _resolve_sid(kwargs)
    if not sid:
        return "Error: no session_id in tool context"

    db_path = _session_db_path(sid)
    if not Path(db_path).exists():
        _do_activate(sid)

    action = args.get("action", "")

    if action == "list":
        status = args.get("status")
        todos = _todo_logic.list_todos(db_path, status=status)
        if not todos:
            return "No todos"
        lines = []
        for t in todos:
            s = t["status"]
            dur = _todo_logic.format_duration(t["duration_s"])
            wait = f", wait={_todo_logic.format_duration(t['wait_time_s'])}" if t.get("wait_time_s") else ""
            lines.append(f"  #{t['id']} [{s}] {t['title']} ({dur}{wait})")
        return "\n".join(lines)

    elif action == "active":
        tasks = _todo_logic.get_current_tasks(db_path)
        if not tasks:
            return "No active tasks"
        return "Active tasks:\n" + "\n".join(f"  #{t['id']} - {t['title']}" for t in tasks)

    elif action == "add":
        title = args.get("title", "")
        if not title:
            return "Error: title is required"
        duration = args.get("duration", "")
        if not duration:
            return "Error: duration is required"
        approach = args.get("approach", "")
        wait_time = args.get("wait_time", "0")
        parent_id = args.get("parent")
        dependency_id = args.get("after")

        try:
            todo_id = _todo_logic.add_todo(
                db_path, title=title, approach=approach,
                duration=duration, wait_time=wait_time or 0,
                parent_id=parent_id, dependency_id=dependency_id,
            )
        except ValueError as exc:
            return f"Error: {exc}"
        return f"Todo #{todo_id} added: {title}"

    elif action == "start":
        r = _todo_logic.activate_todo(db_path, args.get("id", 0))
        return f"Todo #{r['todo']['id']} activated: {r['todo']['title']}" if r.get("ok") else f"Error: {r.get('error')}"
    elif action == "done":
        r = _todo_logic.mark_todo_done(db_path, args.get("id", 0))
        return f"Todo #{r['todo']['id']} marked as done" if r.get("ok") else f"Error: {r.get('error')}"
    elif action == "cancel":
        r = _todo_logic.cancel_todo(db_path, args.get("id", 0))
        return f"Todo #{r['todo']['id']} cancelled" if r.get("ok") else f"Error: {r.get('error')}"
    elif action == "delete":
        tid = args.get("id", 0)
        if _todo_logic.get_todo(db_path, tid) is None:
            return f"Todo #{tid} not found"
        _todo_logic.delete_todo(db_path, tid)
        return f"Todo #{tid} deleted"

    return f"Unknown action: {action}"


def tool_history(kwargs: dict | None = None, limit: int = 20, category: str = "", type_pattern: str = "") -> str:
    """View historical messages."""
    sid = _resolve_sid(kwargs)
    _central_db.init_central_db(_config.CENTRAL_DB)

    categories = None
    if category:
        categories = tuple(c.strip() for c in category.split(",") if c.strip())

    msgs = _central_db.get_messages(
        _config.CENTRAL_DB, limit=limit, offset=0,
        categories=categories,
        type_pattern=type_pattern if type_pattern else None,
        for_session=sid,
    )

    if not msgs:
        return "No messages found"
    return "\n".join(_format_message(m) for m in msgs)


def tool_config(args: dict) -> str:
    """Manage employee config and filter rules."""
    action = args.get("action", "")

    if action == "rules":
        rules = list_rules()
        if not rules:
            return "No rules configured"
        lines = []
        for r in rules:
            lines.append(
                f"  [{r['index']}] {r['type']:20s} type={r['pattern']:30s} "
                f"props={json.dumps(r['props'], ensure_ascii=False)}"
            )
        return "\n".join(lines)

    elif action == "get":
        key = args.get("key", "")
        val = get_config_value(key)
        if val is None:
            return f"Key '{key}' not found"
        if isinstance(val, (dict, list)):
            return json.dumps(val, ensure_ascii=False, indent=2)
        return str(val)

    elif action == "set":
        key = args.get("key", "")
        val = args.get("value", "")
        try:
            val = json.loads(val)
        except (json.JSONDecodeError, TypeError):
            pass
        set_config_value(key, val)
        return f"Set {key} = {val}"

    elif action == "add_rule":
        rule_type = args.get("rule_type", "")
        pattern = args.get("pattern", "")
        if not rule_type or not pattern:
            return "Error: rule_type and pattern are required"
        add_rule(rule_type, pattern)
        return f"Added {rule_type} rule: {pattern}"

    elif action == "remove_rule":
        rule_type = args.get("rule_type", "")
        rule_index = args.get("rule_index", 0)
        if not rule_type:
            return "Error: rule_type is required"
        remove_rule(rule_type, rule_index)
        return f"Removed rule [{rule_index}] from {rule_type}"

    return f"Unknown action: {action}"


def tool_source_status() -> str:
    """Check which source daemon processes are running."""
    status_map = {"github_hook": False, "github_inbox": False, "dingtalk": False}

    for src in status_map:
        try:
            result = subprocess.run(
                ["pgrep", "-f", f"hemployee source-{src.replace('_', '-')}"],
                capture_output=True, text=True, timeout=5,
            )
            status_map[src] = result.returncode == 0
        except Exception:
            status_map[src] = False

    icon = {True: "● running", False: "○ stopped"}
    lines = ["Message source status:"] + [f"  {icon[r]} {name}" for name, r in status_map.items()]
    return "\n".join(lines)
