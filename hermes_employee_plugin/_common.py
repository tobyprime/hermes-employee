"""Shared utilities for the Hermes Employee plugin.

Extracts duplicated collect/deliver/render logic used by tool_check,
tool_wait, and hooks._peek.
"""

from __future__ import annotations

import logging
from typing import Any

from employee import config as _config
from employee import db as _central_db
from employee import session as _session_db
from employee import todo as _todo_logic
from employee.template import render_brief
from employee.yaml_config import load_config

logger = logging.getLogger("hermes_employee_plugin.common")


def fetch_and_ack(
    db_path: str,
    sid: str,
) -> tuple[list[dict], list[dict]]:
    """Fetch new popup and normal messages, mark them as delivered.

    Returns (popups, normals) — both may be empty lists.
    """
    cursor = _session_db.get_read_cursor(db_path)
    open_popups = _session_db.get_open_popups(db_path)

    new_popups = _central_db.get_messages_after(
        _config.CENTRAL_DB, cursor, ("popup",),
        excluded_ids=open_popups, for_session=sid,
    )
    open_popup_msgs = _central_db.get_messages_by_ids(
        _config.CENTRAL_DB, list(open_popups), for_session=sid,
    )
    popups = new_popups + open_popup_msgs
    msgs = _central_db.get_messages_after(
        _config.CENTRAL_DB, cursor, ("normal",), for_session=sid,
    )

    if not popups and not msgs:
        return [], []

    all_ids = [m["id"] for m in popups] + [m["id"] for m in msgs]
    _session_db.set_read_cursor(db_path, max(cursor, max(all_ids)))
    if popups:
        _session_db.mark_popups_delivered(db_path, [m["id"] for m in popups])

    return popups, msgs


def load_templates(brief_key: str = "brief") -> tuple[str, str, dict[str, str]]:
    """Load and return (brief_template, item_template, group_templates)."""
    cfg = load_config()
    templates = cfg.get("templates", {})
    brief = templates.get(brief_key) or templates.get("brief", "")
    item = templates.get("item", "")
    groups = templates.get("groups", {})
    return brief, item, groups


def check_and_format(
    db_path: str,
    sid: str,
    brief_key: str = "brief_peek",
) -> str:
    """One-shot check for new messages. Returns formatted briefing or empty string."""
    try:
        _todo_logic.check_and_emit_reminders(db_path)
    except Exception:
        logger.exception("check_and_format: todo reminder check failed db=%s", db_path)

    popups, msgs = fetch_and_ack(db_path, sid)
    if not popups and not msgs:
        return ""

    brief, item, groups = load_templates(brief_key)
    active_todos = _todo_logic.format_active_todos(db_path)

    return render_brief(
        brief, item, popups, msgs,
        group_templates=groups,
        extra_vars={"ACTIVE_TODOS": active_todos},
    )
