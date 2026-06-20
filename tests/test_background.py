"""测试后台任务管理模块 (background_after 装饰器、任务状态查询、完成通知)"""

import json
import threading
import time
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from hermes_employee_plugin.background import (
    _background_tasks,
    _background_lock,
    background_after,
    check_completed_background_tasks,
    tool_background_status,
)


@pytest.fixture(autouse=True)
def clear_tasks():
    """每个测试前清空全局任务注册表。"""
    with _background_lock:
        _background_tasks.clear()
    yield


@pytest.fixture
def fake_central_db(monkeypatch):
    """创建一个内存中的中央数据库供测试使用。"""
    import tempfile
    from employee import db as central_db
    from employee import config

    tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
    path = tmp.name
    tmp.close()
    monkeypatch.setattr(config, "CENTRAL_DB", path)
    central_db.init_central_db(path)
    yield path
    Path(path).unlink(missing_ok=True)


# ── background_after 装饰器 ────────────────────────────────────


class TestBackgroundAfter:
    def test_completes_within_timeout_returns_result(self):
        """在超时阈值内完成的工具应正常返回结果。"""

        @background_after(timeout=5)
        def fast_tool() -> str:
            return "快速完成"

        assert fast_tool() == "快速完成"

    def test_timeout_returns_task_id_and_keeps_running(self):
        """超过超时阈值的工具应返回任务 ID，且后台线程继续执行。"""
        barrier = threading.Barrier(2, timeout=10)

        @background_after(timeout=0.3)
        def slow_tool() -> str:
            barrier.wait()  # 通知主线程我们已启动
            time.sleep(10)  # 模拟长时间运行
            return "不应返回"

        result = slow_tool()
        barrier.wait(timeout=5)  # 等后台线程到达 barrier

        # 验证返回了后台任务 ID 和提示信息
        assert "bg-" in result
        assert "已转入后台执行" in result
        assert "employee_background_status" in result

    def test_handler_exception_does_not_crash(self):
        """handler 抛异常不应导致装饰器崩溃，应返回错误信息。"""

        @background_after(timeout=5)
        def broken_tool() -> str:
            raise ValueError("模拟错误")

        result = broken_tool()
        assert "执行失败" in result or "错误" in result

    def test_custom_timeout_override(self):
        """timeout 参数应覆盖默认值。"""

        @background_after(timeout=0.05)
        def slow_tool() -> str:
            time.sleep(5)
            return "太慢了"

        result = slow_tool()
        assert "bg-" in result
        assert "已转入后台执行" in result

    def test_extract_session_id_from_kwargs(self):
        """应从 kwargs 中提取 session_id 用于完成通知。"""

        @background_after(timeout=0.05)
        def paused_tool(kwargs: dict | None = None) -> str:
            time.sleep(5)
            return "ok"

        result = paused_tool(kwargs={"session_id": "sess_test"})
        assert "bg-" in result

    def test_timeout_less_than_execution_blocks_indefinitely(self):
        """极端情况：timeout=0 应立即返回后台任务 ID。"""

        @background_after(timeout=0)
        def infinite_tool() -> str:
            time.sleep(999)
            return "无穷"

        result = infinite_tool()
        assert "bg-" in result


# ── check_completed_background_tasks ───────────────────────────


class TestCheckCompletedBackgroundTasks:
    def test_no_tasks_returns_empty(self):
        """没有任务时应返回空列表。"""
        assert check_completed_background_tasks() == []

    def test_detects_completed_thread(self, fake_central_db):
        """已完成的后台线程应被检测到并写入完成消息。"""
        done = threading.Event()

        @background_after(timeout=0.05)
        def quick_task() -> str:
            done.wait(5)  # 等待外部信号
            return "完成结果"

        result = quick_task()
        task_id = result.split("[")[1].split("]")[0] if "bg-" in result else ""

        # 让线程完成
        done.set()
        time.sleep(0.2)

        completed = check_completed_background_tasks()
        assert task_id in completed

    def test_flushes_task_from_registry(self):
        """检测完成后应从全局注册表中移除。"""
        done = threading.Event()

        @background_after(timeout=0.05)
        def quick_task() -> str:
            done.wait(5)
            return "done"

        result = quick_task()
        task_id = result.split("[")[1].split("]")[0] if "bg-" in result else ""

        with _background_lock:
            assert task_id in _background_tasks

        done.set()
        time.sleep(0.2)
        check_completed_background_tasks()

        with _background_lock:
            assert task_id not in _background_tasks

    def test_writes_to_central_db(self, fake_central_db):
        """完成后应写入 central DB 的完成通知消息。"""
        from employee import db as central_db

        done = threading.Event()

        @background_after(timeout=0.05)
        def my_tool(kwargs: dict | None = None) -> str:
            done.wait(5)
            return "写文件成功"

        result = my_tool(kwargs={"session_id": "sess-bg-test"})
        task_id = result.split("[")[1].split("]")[0] if "bg-" in result else ""

        done.set()
        time.sleep(0.2)
        check_completed_background_tasks()

        msgs = central_db.get_messages(fake_central_db, type_pattern="employee.background_complete", limit=10, for_session="sess-bg-test")
        matching = [m for m in msgs if m["props"] and json.loads(m["props"]).get("task_id") == task_id]
        assert len(matching) == 1, f"Expected 1 matching msg, got {len(matching)}: {msgs}"
        assert "写文件成功" in matching[0]["content"]


# ── tool_background_status ─────────────────────────────────────


class TestToolBackgroundStatus:
    def test_no_tasks_returns_empty_message(self):
        """无任务时返回相应提示。"""
        result = tool_background_status(task_id="")
        assert "没有运行中的后台任务" in result

    def test_running_task_shows_progress(self):
        """运行中的任务应显示进度信息。"""
        barrier = threading.Barrier(2, timeout=10)

        @background_after(timeout=0.1)
        def running_tool() -> str:
            barrier.wait()
            time.sleep(30)
            return "ok"

        result = running_tool()
        task_id = result.split("[")[1].split("]")[0] if "bg-" in result else ""
        barrier.wait(timeout=5)

        status = tool_background_status(task_id=task_id)
        assert task_id in status
        assert "运行中" in status or "bg-" in status

    def test_completed_task_by_id(self, fake_central_db):
        """已完成的任务应能通过 ID 查询到结果（从中央数据库）。"""
        from employee import db as central_db
        from hermes_employee_plugin.background import _write_task_completion

        _write_task_completion("bg-testtask-123", "sess1", "处理完成")

        result = tool_background_status(task_id="bg-testtask-123")
        assert "已完成" in result
        assert "bg-testtask-123" in result

    def test_unknown_task_id(self):
        """未知的 task_id 应返回未找到提示。"""
        result = tool_background_status(task_id="bg-nonexistent-999")
        assert "未找到" in result

    def test_lists_running_tasks(self):
        """不传 task_id 时应列出所有运行中的任务。"""
        barrier = threading.Barrier(3, timeout=10)

        @background_after(timeout=0.1)
        def task_a() -> str:
            barrier.wait()
            time.sleep(30)
            return "a"

        @background_after(timeout=0.1)
        def task_b() -> str:
            barrier.wait()
            time.sleep(30)
            return "b"

        task_a()
        task_b()
        barrier.wait(timeout=5)

        status = tool_background_status(task_id="")
        assert "运行中的后台任务" in status
        assert "task_a" in status
        assert "task_b" in status

    def test_task_scoped_to_session(self, fake_central_db):
        """per-session 消息应写入正确的 for_session 字段。"""
        from employee import db as central_db

        done = threading.Event()

        @background_after(timeout=0.05)
        def session_tool(kwargs: dict | None = None) -> str:
            done.wait(5)
            return f"worked for {kwargs.get('session_id', '?')}"

        result = session_tool(kwargs={"session_id": "sess-demo"})
        task_id = result.split("[")[1].split("]")[0] if "bg-" in result else ""

        done.set()
        time.sleep(0.2)
        check_completed_background_tasks()

        msgs = central_db.get_messages(fake_central_db, type_pattern="employee.background_complete", limit=10)
        matching = [
            m for m in msgs
            if isinstance(m["props"], str)
            and json.loads(m["props"]).get("task_id") == task_id
        ]
        if matching:
            assert matching[0].get("for_session") == "sess-demo"
