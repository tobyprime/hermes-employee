"""Lifecycle hooks for the Hermes Employee plugin.

消息投递：
- 轮内：transform_tool_result hook 在每个工具返回后自动检查 DB、确认已读、
  渲染模板，追加到工具结果中。
- 轮间：on_post_llm_call 启动轮询线程等待新消息，有则 inject_message 投递。
- on_pre_llm_call 在新一轮开始前停止轮询。
"""

from __future__ import annotations

import logging
import threading
from typing import Any

from pathlib import Path as _Path

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
    logger.info("poll thread started for session %s", session_id)


def _poll_loop(session_id: str) -> None:
    """轮询中央 DB，有新消息则 inject 并退出。"""
    logger.info("poll loop entered for session %s", session_id)
    db_path = _session_db_path(session_id)

    while not _poll_stop.is_set():
        if not is_activated(session_id):
            logger.info("poll loop: session deactivated, exiting")
            return

        brief = check_and_format(db_path, session_id)
        if brief:
            logger.info("poll loop: found messages, injecting")
            _inject(brief)
            return

        _poll_stop.wait(1)


# ── Transform tool result（替代 _patch_dispatch）───────────────


def on_transform_tool_result(
    tool_name: str = "",
    arguments: dict | None = None,
    result: str | None = None,
    task_id: str | None = None,
    **kwargs: Any,
) -> str | None:
    """工具返回后自动检查并追加消息简报（替代之前的 dispatch monkey-patch）。"""
    sid = kwargs.get("session_id", "")
    if not sid or not is_activated(sid):
        return None

    try:
        db_path = _session_db_path(sid)
        if not _Path(db_path).exists():
            return None
    except Exception:
        return None

    try:
        brief = check_and_format(db_path, sid, brief_key="brief")
        if brief:
            logger.info(
                "transform_tool_result: appended brief (%d chars) after tool=%r session=%s",
                len(brief), tool_name, sid,
            )
            if result:
                return result.rstrip() + "\n\n---\n\n" + brief
            return brief
        logger.debug("transform_tool_result: no new messages tool=%r session=%s", tool_name, sid)
    except Exception:
        logger.exception("transform_tool_result: check_and_format failed tool=%r session=%s", tool_name, sid)

    return None


# ── Lifecycle hooks ─────────────────────────────────────────


def on_pre_llm_call(
    session_id: str = "",
    **kwargs: Any,
) -> None:
    """新一轮即将开始：停止轮询线程。"""
    if not session_id or not is_activated(session_id):
        logger.debug("on_pre_llm_call: skipped (not activated) session_id=%r", session_id)
        return
    logger.info("on_pre_llm_call: stopping poll session=%s", session_id)
    _stop_polling()


def on_post_llm_call(
    session_id: str = "",
    completed: bool = True,
    interrupted: bool = False,
    **kwargs: Any,
) -> None:
    """轮间：LLM 调用结束后启动轮询，等待新消息。"""
    if not session_id or not is_activated(session_id):
        logger.info("on_post_llm_call: skipped (not activated) session_id=%r", session_id)
        return

    if not _plugin_ctx:
        logger.info("on_post_llm_call: skipped (no plugin ctx)")
        return

    logger.info("on_post_llm_call: session_id=%s completed=%s", session_id, completed)

    # 即时检查
    db_path = _session_db_path(session_id)
    brief = check_and_format(db_path, session_id)
    if brief:
        logger.info("on_post_llm_call: found immediate messages, injecting")
        _inject(brief)
        return

    # 无即时消息，启动轮询
    logger.info("on_post_llm_call: no immediate messages, starting poll")
    _start_polling(session_id)


def _inject(content: str) -> None:
    if not _plugin_ctx or not content:
        return
    logger.info("_inject: len=%d preview=%r", len(content), content[:200])

    cli = _plugin_ctx._manager._cli_ref
    if cli is None:
        logger.error("_inject: no CLI ref (gateway mode?), undelivered (%d chars): %s",
                     len(content), content[:500])
        return

    cli._pending_input.put(content)
