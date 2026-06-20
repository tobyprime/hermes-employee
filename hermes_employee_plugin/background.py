"""Background task management for employee tools.

Provides the `background_after` decorator and `tool_background_status` handler,
plus `check_completed_background_tasks` for integration with the polling loop.

When a tool handler exceeds TOOL_TIMEOUT (env HERMES_EMPLOYEE_TOOL_TIMEOUT, default 30s),
the decorator spins it to a daemon thread and returns a task ID. On completion, a
per-session message is written to the central database so the agent is notified.
"""

from __future__ import annotations

import json
import logging
import threading
import time
import uuid
from typing import Any

from employee import config as _config
from employee import db as _central_db

logger = logging.getLogger("hermes_employee_plugin.background")

# ── In-memory task registry ────────────────────────────────────

_background_tasks: dict[str, dict] = {}
_background_lock = threading.Lock()


def _new_task_id() -> str:
    return f"bg-{uuid.uuid4().hex[:8]}-{int(time.time())}"


def _write_task_completion(task_id: str, sid: str, result: str, error: bool = False):
    """Write a per-session completion message to the central database."""
    if not sid:
        return
    status = "失败" if error else "已完成"
    _central_db.insert_message(
        _config.CENTRAL_DB,
        type_="employee.background_complete",
        title=f"[后台任务] {task_id} {status}",
        content=(
            f"后台任务 [{task_id}] 已{status}。\n"
            f"结果摘要：{(result or '无')[:500]}\n"
            f"可用 employee_background_status 工具查看完整结果。"
        ),
        props={"task_id": task_id, "error": error},
        category="normal",
        source="employee",
        for_session=sid,
    )


# ── Public API ─────────────────────────────────────────────────


def check_completed_background_tasks() -> list[str]:
    """Check completed background threads and flush per-session completion messages.

    Called by hooks before each message-poll cycle so the completion notification
    becomes visible to the agent immediately.
    """
    completed_ids: list[str] = []
    snapshot: list[tuple[str, dict]] = []

    with _background_lock:
        for tid, info in list(_background_tasks.items()):
            if not info["thread"].is_alive():
                snapshot.append((tid, info))

    for task_id, info in snapshot:
        with _background_lock:
            _background_tasks.pop(task_id, None)
        result = info["result_container"][0] if info.get("result_container") else ""
        _write_task_completion(task_id, info.get("sid", ""), result)
        completed_ids.append(task_id)
        logger.info("Background task %s completed and notified", task_id)

    return completed_ids


def background_after(timeout: int | None = None):
    """Decorator: run the wrapped handler in a thread.

    If it finishes within *timeout* seconds (default: TOOL_TIMEOUT from config),
    the result is returned normally.  Otherwise the thread is detached and a
    task ID is returned so the agent can query progress via ``tool_background_status``.
    A per-session completion message is automatically written to the central DB.
    """
    actual_timeout = timeout if timeout is not None else _config.TOOL_TIMEOUT

    def decorator(handler):
        def wrapper(*args: Any, **kwargs: Any) -> str:
            # --- extract session_id -------------------------------------------
            sid = ""
            dispatch_kwargs = kwargs.get("kwargs") if isinstance(kwargs, dict) else None
            if isinstance(dispatch_kwargs, dict):
                sid = dispatch_kwargs.get("session_id", "") or ""
            if not sid:
                for arg in args:
                    if isinstance(arg, dict):
                        sid = arg.get("session_id", "") or arg.get("kwargs", {}).get("session_id", "")
                        if sid:
                            break

            # --- run handler in thread -----------------------------------------
            result_container: list = []
            thread = threading.Thread(
                target=lambda: result_container.append(handler(*args, **kwargs)),
                daemon=True,
            )
            t0 = time.time()
            thread.start()
            thread.join(timeout=actual_timeout)

            # completed in time
            if not thread.is_alive():
                if result_container:
                    return result_container[0]
                # handler raised — return error string instead of crashing
                return f"❌ 工具 {handler.__name__} 执行失败（在后台线程中抛出了异常，请检查日志）"

            # --- timeout → background ------------------------------------------
            task_id = _new_task_id()
            with _background_lock:
                _background_tasks[task_id] = {
                    "task_id": task_id,
                    "handler_name": handler.__name__,
                    "started_at": time.strftime("%Y-%m-%d %H:%M:%S"),
                    "start_ts": t0,
                    "thread": thread,
                    "result_container": result_container,
                    "sid": sid,
                    "timeout": actual_timeout,
                }

            logger.info(
                "Tool %s timed out (%ds) → bg task %s for session %s",
                handler.__name__, actual_timeout, task_id, sid or "(unknown)",
            )
            return (
                f"⏳ 任务 [{task_id}] 已转入后台执行\n"
                f"（工具 {handler.__name__} 超过 {actual_timeout}s 超时阈值）\n\n"
                f"你可以使用 employee_background_status 工具查询任务状态：\n"
                f"  employee_background_status task_id={task_id}\n"
                f"任务完成后会自动通知你。"
            )

        return wrapper

    return decorator


def tool_background_status(task_id: str = "", kwargs: dict | None = None) -> str:
    """Query background task status and logs."""
    sid = ""
    if kwargs:
        sid = kwargs.get("session_id", "") or ""

    # --- running task ---------------------------------------------------------
    with _background_lock:
        info = _background_tasks.get(task_id)

    if info is not None:
        elapsed = int(time.time() - info["start_ts"])
        return (
            f"📋 任务 [{task_id}] 状态\n"
            f"  工具: {info['handler_name']}\n"
            f"  启动时间: {info['started_at']}\n"
            f"  已运行: {elapsed}s\n"
            f"  超时阈值: {info['timeout']}s\n"
            f"  状态: ⏳ 运行中..."
        )

    # --- completed task (look up in central DB) -------------------------------
    if task_id:
        _central_db.init_central_db(_config.CENTRAL_DB)
        rows = _central_db.get_messages(
            _config.CENTRAL_DB,
            limit=20,
            type_pattern="employee.background_complete",
            for_session=sid or None,
        )
        for row in rows:
            props = row.get("props", {})
            if isinstance(props, str):
                try:
                    props = json.loads(props)
                except Exception:
                    props = {}
            if isinstance(props, dict) and props.get("task_id") == task_id:
                return (
                    f"✅ 任务 [{task_id}] 已完成\n"
                    f"  标题: {row.get('title', '')}\n"
                    f"  内容: {row.get('content', '')[:1000]}"
                )
        return (
            f"❌ 未找到任务 [{task_id}]。\n"
            f"任务可能已完成且通知已送达，或任务 ID 不正确。"
        )

    # --- list all running tasks -----------------------------------------------
    with _background_lock:
        tasks_list = list(_background_tasks.values())
    if not tasks_list:
        return "当前没有运行中的后台任务。"
    lines = ["📋 运行中的后台任务："]
    for t in tasks_list:
        elapsed = int(time.time() - t["start_ts"])
        lines.append(f"  [{t['task_id']}] {t['handler_name']} — 已运行 {elapsed}s")
    return "\n".join(lines)
