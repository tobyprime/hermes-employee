"""测试 CLI 命令 - 重点关注 cmd_wait/peek 行为"""

import contextlib
import os
import tempfile
from pathlib import Path
from unittest.mock import patch, MagicMock

import pytest

from employee import cli
from employee import config
from employee import db as central_db
from employee import session as session_db

_MESSAGE = "employee.commands.message"
_SESSION = "employee.commands.session"


@pytest.fixture
def temp_sessions_dir():
    with tempfile.TemporaryDirectory() as tmp:
        with patch.object(config, "SESSIONS_DIR", Path(tmp)):
            yield tmp


@pytest.fixture
def temp_central_db():
    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
        path = Path(f.name)
    with patch.object(config, "CENTRAL_DB", path):
        central_db.init_central_db(str(path))
        yield str(path)
    central_db._local.conn = None
    central_db._local.conn_path = None
    if path.exists():
        path.unlink()


@pytest.fixture
def sid():
    return "test-session-12345"


@pytest.fixture
def activated_session(temp_sessions_dir, temp_central_db, sid):
    db_path = cli._session_db_path(sid)
    session_db.init_session_db(db_path)
    return sid, db_path


def _with_session(session_id: str, *extra_patches):
    ctx = (
        patch.dict(os.environ, {"HERMES_AGENT_SESSION_ID": session_id}, clear=False),
        patch(f"{_MESSAGE}._is_main_agent", return_value=True),
    ) + extra_patches
    return contextlib.ExitStack() if not extra_patches else contextlib.ExitStack()


import contextlib


class TestCmdWaitDuration:
    def _run_wait_and_count_sleeps(self, idle, sleep):
        sleep_count = [0]

        def _sleep(_sec):
            sleep_count[0] += 1

        with (
            patch.object(config, "IDLE_DURATION", idle),
            patch.object(config, "SLEEP_DURATION", sleep),
            patch.dict(os.environ, {"HERMES_AGENT_SESSION_ID": "test"}, clear=False),
            patch(f"{_MESSAGE}._is_main_agent", return_value=True),
            patch.object(Path, "exists", return_value=True),
            patch(f"{_MESSAGE}.session_db") as mock_session_db,
            patch(f"{_MESSAGE}.central_db") as mock_central_db,
            patch(f"{_MESSAGE}.load_config", return_value={"templates": {}}),
            patch(f"{_MESSAGE}.time.sleep", side_effect=_sleep),
            patch(f"{_MESSAGE}.sys.exit", side_effect=SystemExit),
        ):
            mock_session_db.get_read_cursor.return_value = 0
            mock_session_db.get_open_popups.return_value = set()
            mock_central_db.get_messages_after.return_value = []
            mock_central_db.get_messages_by_ids.return_value = []

            with pytest.raises(SystemExit):
                cli.cmd_wait(None)

            return sleep_count[0]

    def test_total_duration_default(self):
        assert self._run_wait_and_count_sleeps(30, 60) == 18

    def test_custom_duration(self):
        assert self._run_wait_and_count_sleeps(10, 20) == 6

    def test_duration_not_just_sleep(self):
        count = self._run_wait_and_count_sleeps(10, 10)
        assert count == 4, f"idle+sleep=20s → 4 次，实际 {count}"


class TestCmdWaitPopups:
    def test_popup_exit_2_with_stderr(self, activated_session, temp_central_db, sid):
        central_db.insert_message(str(config.CENTRAL_DB), "test.type", "Popup!", "urgent", category="popup")

        with (
            patch.dict(os.environ, {"HERMES_AGENT_SESSION_ID": sid}, clear=False),
            patch(f"{_MESSAGE}._is_main_agent", return_value=True),
            patch(f"{_MESSAGE}.time.sleep") as mock_sleep,
            patch(f"{_MESSAGE}.sys.exit", side_effect=SystemExit) as mock_exit,
        ):
            with pytest.raises(SystemExit):
                cli.cmd_wait(None)

            assert not mock_sleep.called
            mock_exit.assert_called_once_with(2)

    def test_no_popup_polling_finds_message(self, activated_session, temp_central_db, sid):
        call_count = [0]

        def mock_get_messages_after(*args, **kwargs):
            call_count[0] += 1
            if call_count[0] >= 3:
                return [{"id": 1, "category": "normal", "type": "test", "title": "Late msg", "content": "hello", "props": {}}]
            return []

        with (
            patch.dict(os.environ, {"HERMES_AGENT_SESSION_ID": sid}, clear=False),
            patch(f"{_MESSAGE}._is_main_agent", return_value=True),
            patch.object(config, "IDLE_DURATION", 10),
            patch.object(config, "SLEEP_DURATION", 10),
            patch.object(config, "WAIT_BATCH_WINDOW", 0),
            patch(f"{_MESSAGE}.central_db.get_messages_after", side_effect=mock_get_messages_after),
            patch(f"{_MESSAGE}.central_db.get_messages_by_ids", return_value=[]),
            patch(f"{_MESSAGE}.time.sleep"),
            patch(f"{_MESSAGE}.sys.exit", side_effect=SystemExit) as mock_exit,
        ):
            with pytest.raises(SystemExit):
                cli.cmd_wait(None)

            mock_exit.assert_called_once_with(2)

    def test_batch_window_collects_more_messages(self, activated_session, temp_central_db, sid):
        batch_window = 0.3
        phase = [0]

        def mock_get_messages_after(*args, **kwargs):
            p = phase[0]
            if p == 0:
                return []
            elif p == 1:
                return [{"id": 1, "category": "normal", "type": "test", "title": "First", "content": "", "props": {}}]
            else:
                return [{"id": 2, "category": "normal", "type": "test", "title": "Second", "content": "", "props": {}}]

        delivered_ids = []

        def capture_deliver(db_path, cursor):
            delivered_ids.append(cursor)

        monotonic_values = [0, 5.0]
        mono_idx = [0]

        def fake_monotonic():
            idx = mono_idx[0]
            mono_idx[0] += 1
            if idx < len(monotonic_values):
                return monotonic_values[idx]
            return 5.0001 + (mono_idx[0] - len(monotonic_values)) * 0.05

        def fake_sleep(sec):
            if sec >= 5:
                phase[0] = 1
            if sec <= 0.1 and phase[0] == 1:
                phase[0] = 2

        with (
            patch.dict(os.environ, {"HERMES_AGENT_SESSION_ID": sid}, clear=False),
            patch(f"{_MESSAGE}._is_main_agent", return_value=True),
            patch.object(config, "IDLE_DURATION", 10),
            patch.object(config, "SLEEP_DURATION", 10),
            patch.object(config, "WAIT_BATCH_WINDOW", batch_window),
            patch(f"{_MESSAGE}.central_db.get_messages_after", side_effect=mock_get_messages_after),
            patch(f"{_MESSAGE}.central_db.get_messages_by_ids", return_value=[]),
            patch(f"{_MESSAGE}.time.sleep", side_effect=fake_sleep),
            patch(f"{_MESSAGE}.time.monotonic", side_effect=fake_monotonic),
            patch(f"{_MESSAGE}.session_db.set_read_cursor", side_effect=capture_deliver),
            patch(f"{_MESSAGE}.sys.exit", side_effect=SystemExit) as mock_exit,
        ):
            with pytest.raises(SystemExit):
                cli.cmd_wait(None)

            mock_exit.assert_called_once_with(2)
            assert delivered_ids, "应有消息被交付"
            assert max(delivered_ids) >= 2, f"缓冲窗口应收集到 id>=2 的消息，实际 cursor={delivered_ids}"


class TestCmdWaitExitConditions:
    def test_no_session_id(self):
        with patch.dict(os.environ, clear=True):
            with patch(f"{_MESSAGE}.sys.exit", side_effect=SystemExit) as mock_exit:
                with pytest.raises(SystemExit):
                    cli.cmd_wait(None)
                mock_exit.assert_called_once_with(0)

    def test_no_session_db(self):
        with (
            patch.dict(os.environ, {"HERMES_AGENT_SESSION_ID": "no-such-session"}, clear=False),
            patch.object(Path, "exists", return_value=False),
            patch(f"{_MESSAGE}.sys.exit", side_effect=SystemExit) as mock_exit,
        ):
            with pytest.raises(SystemExit):
                cli.cmd_wait(None)
            mock_exit.assert_called_once_with(0)

    def test_no_messages_exit_2(self, activated_session, sid):
        with (
            patch.dict(os.environ, {"HERMES_AGENT_SESSION_ID": sid}, clear=False),
            patch(f"{_MESSAGE}._is_main_agent", return_value=True),
            patch.object(config, "IDLE_DURATION", 1),
            patch.object(config, "SLEEP_DURATION", 1),
            patch(f"{_MESSAGE}.time.sleep"),
            patch(f"{_MESSAGE}.sys.exit", side_effect=SystemExit) as mock_exit,
        ):
            with pytest.raises(SystemExit):
                cli.cmd_wait(None)
            mock_exit.assert_called_once_with(2)

    def test_child_agent_silent(self):
        with (
            patch.dict(os.environ, {"HERMES_AGENT_SESSION_ID": "test"}, clear=False),
            patch(f"{_MESSAGE}._is_main_agent", return_value=False),
            patch(f"{_MESSAGE}.sys.exit", side_effect=SystemExit) as mock_exit,
        ):
            with pytest.raises(SystemExit):
                cli.cmd_wait(None)
            mock_exit.assert_called_once_with(0)


class TestCmdPeek:
    def test_peek_exit_2_with_stderr(self, activated_session, temp_central_db, sid):
        central_db.insert_message(str(config.CENTRAL_DB), "test", "Peek msg", "content", category="normal")

        with (
            patch.dict(os.environ, {"HERMES_AGENT_SESSION_ID": sid}, clear=False),
            patch(f"{_MESSAGE}._is_main_agent", return_value=True),
            patch(f"{_MESSAGE}.sys.exit", side_effect=SystemExit) as mock_exit,
        ):
            with pytest.raises(SystemExit):
                cli.cmd_peek(None)
            mock_exit.assert_called_once_with(2)

    def test_peek_no_messages_silent(self, activated_session, temp_central_db, sid):
        with (
            patch.dict(os.environ, {"HERMES_AGENT_SESSION_ID": sid}, clear=False),
            patch(f"{_MESSAGE}._is_main_agent", return_value=True),
            patch(f"{_MESSAGE}.sys.exit", side_effect=SystemExit) as mock_exit,
        ):
            cli.cmd_peek(None)
            assert not mock_exit.called

    def test_peek_cooldown(self, temp_sessions_dir, sid):
        cooldown_file = Path(temp_sessions_dir) / f"{sid}.peek_ts"

        with (
            patch.dict(os.environ, {"HERMES_AGENT_SESSION_ID": sid}, clear=False),
            patch(f"{_MESSAGE}._is_main_agent", return_value=True),
            patch.object(config, "PEEK_COOLDOWN", 10),
        ):
            cli.cmd_peek(None)
            assert cooldown_file.exists()
            cli.cmd_peek(None)

    def test_peek_child_agent_silent(self, temp_sessions_dir):
        sid = "peek-child-test"
        cooldown_file = Path(temp_sessions_dir) / f"{sid}.peek_ts"

        with (
            patch.dict(os.environ, {"HERMES_AGENT_SESSION_ID": sid}, clear=False),
            patch(f"{_MESSAGE}._is_main_agent", return_value=False),
            patch.object(config, "PEEK_COOLDOWN", 10),
        ):
            cli.cmd_peek(None)
            assert not cooldown_file.exists()


class TestCmdWaitRegressions:
    def test_not_only_sleep_duration(self):
        with patch.object(config, "IDLE_DURATION", 5):
            with patch.object(config, "SLEEP_DURATION", 10):
                sleep_count = [0]

                def _sleep(_sec):
                    sleep_count[0] += 1

                with (
                    patch.dict(os.environ, {"HERMES_AGENT_SESSION_ID": "reg-test"}, clear=False),
                    patch(f"{_MESSAGE}._is_main_agent", return_value=True),
                    patch.object(Path, "exists", return_value=True),
                    patch(f"{_MESSAGE}.session_db") as mock_session_db,
                    patch(f"{_MESSAGE}.central_db") as mock_central_db,
                    patch(f"{_MESSAGE}.load_config", return_value={"templates": {}}),
                    patch(f"{_MESSAGE}.time.sleep", side_effect=_sleep),
                    patch(f"{_MESSAGE}.sys.exit", side_effect=SystemExit),
                ):
                    mock_session_db.get_read_cursor.return_value = 0
                    mock_session_db.get_open_popups.return_value = set()
                    mock_central_db.get_messages_after.return_value = []
                    mock_central_db.get_messages_by_ids.return_value = []

                    with pytest.raises(SystemExit):
                        cli.cmd_wait(None)

                    assert sleep_count[0] == 3, f"预期 3 次 sleep，实际 {sleep_count[0]}"

    def test_output_goes_to_stderr(self, activated_session, temp_central_db, sid):
        central_db.insert_message(str(config.CENTRAL_DB), "test", "Popup!", "", category="popup")

        with (
            patch.dict(os.environ, {"HERMES_AGENT_SESSION_ID": sid}, clear=False),
            patch(f"{_MESSAGE}._is_main_agent", return_value=True),
            patch(f"{_MESSAGE}.sys.exit", side_effect=SystemExit),
            patch("builtins.print") as mock_print,
        ):
            with pytest.raises(SystemExit):
                cli.cmd_wait(None)

            assert any(
                kw.get("file") is not None for _, kw in mock_print.call_args_list
            )


class TestCmdStartAutoClose:
    def test_start_sets_cursor_to_max(self, temp_sessions_dir, temp_central_db):
        sid = "new-session"
        central_db.insert_message(str(config.CENTRAL_DB), "test", "Old popup", "", category="popup")
        central_db.insert_message(str(config.CENTRAL_DB), "test", "Normal msg", "", category="normal")

        with patch.dict(os.environ, {"HERMES_AGENT_SESSION_ID": sid}, clear=False):
            cli.cmd_start(None)

        db_path = cli._session_db_path(sid)
        assert session_db.get_read_cursor(db_path) == 2
        assert session_db.get_open_popups(db_path) == set()

    def test_start_ignores_historical_messages(self, temp_sessions_dir, temp_central_db):
        sid = "new-session"
        central_db.insert_message(str(config.CENTRAL_DB), "test", "Old normal", "", category="normal")

        with patch.dict(os.environ, {"HERMES_AGENT_SESSION_ID": sid}, clear=False):
            cli.cmd_start(None)

        with (
            patch.dict(os.environ, {"HERMES_AGENT_SESSION_ID": sid}, clear=False),
            patch(f"{_MESSAGE}._is_main_agent", return_value=True),
            patch(f"{_MESSAGE}.sys.exit", side_effect=SystemExit) as mock_exit,
            patch(f"{_MESSAGE}._touch_peek_cooldown"),
            patch(f"{_MESSAGE}._check_peek_cooldown", return_value=False),
        ):
            cli.cmd_peek(None)
            assert not mock_exit.called


class TestCmdClose:
    def test_close_by_ids(self, activated_session, sid):
        _, db_path = activated_session
        msg_id = central_db.insert_message(str(config.CENTRAL_DB), "test", "Popup!", "", category="popup")
        args = MagicMock(ids=f"{msg_id}")

        with patch.dict(os.environ, {"HERMES_AGENT_SESSION_ID": sid}, clear=False):
            cli.cmd_close(args)

        assert session_db.get_open_popups(db_path) == set()

    def test_close_only_delivered_open_popups(self, activated_session, sid):
        _, db_path = activated_session
        p1 = central_db.insert_message(str(config.CENTRAL_DB), "test", "P1", "", category="popup")
        p2 = central_db.insert_message(str(config.CENTRAL_DB), "test", "P2", "", category="popup")
        session_db.mark_popups_delivered(db_path, [p1])

        args = MagicMock(ids=None)

        with patch.dict(os.environ, {"HERMES_AGENT_SESSION_ID": sid}, clear=False):
            cli.cmd_close(args)

        assert p1 not in session_db.get_open_popups(db_path)
        assert p2 not in session_db.get_open_popups(db_path)

    def test_close_no_popups_silent(self, activated_session, sid):
        args = MagicMock(ids=None)

        with patch.dict(os.environ, {"HERMES_AGENT_SESSION_ID": sid}, clear=False):
            cli.cmd_close(args)


class TestCmdWaitPopupCloseFilter:
    def test_popup_auto_delivered_by_wait(self, activated_session, temp_central_db, sid):
        central_db.insert_message(str(config.CENTRAL_DB), "test", "Popup!", "", category="popup")

        with (
            patch.dict(os.environ, {"HERMES_AGENT_SESSION_ID": sid}, clear=False),
            patch(f"{_MESSAGE}._is_main_agent", return_value=True),
            patch(f"{_MESSAGE}.sys.exit", side_effect=SystemExit),
            patch(f"{_MESSAGE}.time.sleep"),
        ):
            with pytest.raises(SystemExit):
                cli.cmd_wait(None)

        _, db_path = activated_session
        assert session_db.get_open_popups(db_path, delivered_only=True) == {1}

    def test_popup_not_shown_after_close(self, activated_session, sid):
        _, db_path = activated_session
        msg_id = central_db.insert_message(str(config.CENTRAL_DB), "test", "Popup!", "", category="popup")
        session_db.close_popups(db_path, [msg_id])

        with (
            patch.dict(os.environ, {"HERMES_AGENT_SESSION_ID": sid}, clear=False),
            patch(f"{_MESSAGE}._is_main_agent", return_value=True),
            patch(f"{_MESSAGE}.sys.exit", side_effect=SystemExit) as mock_exit,
            patch(f"{_MESSAGE}.time.sleep"),
        ):
            with pytest.raises(SystemExit):
                cli.cmd_wait(None)

            mock_exit.assert_called_once_with(2)
