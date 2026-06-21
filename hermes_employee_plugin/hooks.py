"""Lifecycle hooks for the Hermes Employee plugin.

消息投递：
- 轮内：_with_brief wrapper 在每个工具 handler 返回后自动检查 DB、确认已读、
  渲染模板，追加到工具结果中。
- 轮间：on_post_llm_call 启动轮询线程等待新消息，有则 inject_message 投递。
- on_pre_llm_call 在新一轮开始前停止轮询。
"""

from __future__ import annotations

import logging
import threading
from typing import Any

from employee.config import session_db_path as _session_db_path
from ._common import check_and_format

logger = logging.getLogger("hermes_employee_plugin.hooks")

_plugin_ctx: Any = None

# 显式激活的 session 集合
_activated_sessions: set[str] = set()
_activated_lock = threading.Lock()

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

    # 即时检查
    db_path = _session_db_path(session_id)
    brief = check_and_format(db_path, session_id)
    if brief:
        _inject(brief)
        return

    # 无即时消息，启动轮询
    _start_polling(session_id)


def _inject(content: str) -> None:
    if not _plugin_ctx or not content:
        return
    logger.info("_inject: len=%d preview=%r", len(content), content[:200])
    try:
        # Directly push to _pending_input instead of calling inject_message,
        # which routes based on _agent_running and may send to _interrupt_queue
        # when the timing window is tight. _pending_input is always consumed
        # by process_loop.
        cli = _plugin_ctx._manager._cli_ref
        if cli is None:
            logger.warning("_inject: no CLI reference")
            return
        cli._pending_input.put(content)
    except Exception as exc:
        logger.error("_inject failed (%s); undelivered (%d chars): %s",
                     exc, len(content), content[:500])
