"""Lifecycle hooks for the Hermes Employee plugin.

消息投递策略：
1. on_post_tool_call — 轮内：检查新消息，存入缓冲区。由 tool handler wrapper 追加到结果中。
2. on_post_llm_call — 轮间：启动轮询线程等待新消息，有消息则 inject_message 投递。
3. on_pre_llm_call — 新一轮开始前，停止轮询。
"""

from __future__ import annotations

import logging
import threading
from typing import Any

from employee.config import session_db_path as _session_db_path
from ._common import check_and_format
from .background import check_completed_background_tasks

logger = logging.getLogger("hermes_employee_plugin.hooks")

_plugin_ctx: Any = None

# 显式激活的 session 集合
_activated_sessions: set[str] = set()
_activated_lock = threading.Lock()

# 轮内消息缓冲区：session_id → formatted brief
_pending_briefs: dict[str, str] = {}
_pending_lock = threading.Lock()

# 轮间轮询
_poll_thread: threading.Thread | None = None
_poll_stop = threading.Event()


def _set_plugin_ctx(ctx: Any) -> None:
    global _plugin_ctx
    _plugin_ctx = ctx


def is_activated(session_id: str) -> bool:
    with _activated_lock:
        return session_id in _activated_sessions


def mark_activated(session_id: str) -> None:
    with _activated_lock:
        _activated_sessions.add(session_id)


def mark_deactivated(session_id: str) -> None:
    with _activated_lock:
        _activated_sessions.discard(session_id)
    _stop_polling()


# ── 轮内缓冲区 ─────────────────────────────────────────────


def drain_pending_brief(session_id: str) -> str:
    """取出并清除轮内消息缓冲区。由 tool handler wrapper 在返回前调用。"""
    if not session_id:
        return ""
    with _pending_lock:
        return _pending_briefs.pop(session_id, "")


# ── 轮间轮询 ──────────────────────────────────────────────


def _stop_polling() -> None:
    global _poll_thread
    if _poll_thread is not None and _poll_thread.is_alive():
        _poll_stop.set()
        _poll_thread.join(timeout=2)
        _poll_thread = None


def _start_polling(session_id: str) -> None:
    global _poll_thread
    _stop_polling()
    _poll_stop.clear()
    _poll_thread = threading.Thread(
        target=_poll_loop, args=(session_id,), daemon=True,
    )
    _poll_thread.start()


def _poll_loop(session_id: str) -> None:
    """轮询中央 DB，有新消息则 inject 并退出。"""
    db_path = _session_db_path(session_id)

    while not _poll_stop.is_set():
        if not is_activated(session_id):
            return

        check_completed_background_tasks()

        brief = check_and_format(db_path, session_id)
        if brief:
            _inject(brief)
            return

        _poll_stop.wait(1)


# ── Lifecycle hooks ─────────────────────────────────────────


def on_pre_llm_call(
    session_id: str = "",
    **kwargs: Any,
) -> None:
    """新一轮即将开始：停止轮询线程。"""
    if not session_id or not is_activated(session_id):
        return
    _stop_polling()


def on_post_tool_call(
    tool_name: str = "",
    args: dict | None = None,
    result: Any = None,
    task_id: str = "",
    session_id: str = "",
    **kwargs: Any,
) -> None:
    """轮内：每次工具调用后检查新消息，存入缓冲区（不 inject 打断）。"""
    if not session_id or not is_activated(session_id):
        return

    check_completed_background_tasks()

    db_path = _session_db_path(session_id)
    brief = check_and_format(db_path, session_id)
    if brief:
        with _pending_lock:
            _pending_briefs[session_id] = brief


def on_post_llm_call(
    session_id: str = "",
    completed: bool = True,
    interrupted: bool = False,
    **kwargs: Any,
) -> None:
    """轮间：LLM 调用结束后启动轮询，等待新消息。"""
    if not session_id or not is_activated(session_id):
        return
    if not _plugin_ctx:
        return

    # 先尝试投递缓冲区消息（上一轮产的但没通过 tool 结果送出的）
    leftover = drain_pending_brief(session_id)
    if leftover:
        _inject(leftover)
        return

    # 再做一次即时检查
    check_completed_background_tasks()
    db_path = _session_db_path(session_id)
    brief = check_and_format(db_path, session_id)
    if brief:
        _inject(brief)
        return

    # 无即时消息，启动轮询
    _start_polling(session_id)


def on_session_end(
    session_id: str = "",
    **kwargs: Any,
) -> None:
    """Session 结束：清理轮询线程。"""
    _stop_polling()


def _inject(content: str) -> None:
    if not _plugin_ctx or not content:
        return
    logger.info("_inject: len=%d preview=%r", len(content), content[:200])
    try:
        _plugin_ctx.inject_message(content, role="user")
    except Exception as exc:
        logger.error("inject_message failed (%s); undelivered (%d chars): %s",
                     exc, len(content), content[:500])
