"""Session management commands — start, stop, and session access utilities."""

import os
import sys
from pathlib import Path

from .. import config
from .. import db as central_db
from .. import session as session_db

_HOOK_INPUT_CACHE = None


def _read_hook_input():
    global _HOOK_INPUT_CACHE
    if _HOOK_INPUT_CACHE is not None:
        return _HOOK_INPUT_CACHE

    if sys.stdin.isatty():
        _HOOK_INPUT_CACHE = {}
        return _HOOK_INPUT_CACHE

    try:
        import json
        data = json.load(sys.stdin)
    except (json.JSONDecodeError, OSError):
        data = {}
    _HOOK_INPUT_CACHE = data
    return _HOOK_INPUT_CACHE


def _is_main_agent() -> bool:
    data = _read_hook_input()
    if data.get("agent_id") is not None:
        return False
    return True


def _session_id() -> str | None:
    return os.environ.get("HERMES_SESSION_ID")


def _session_db_path(session_id: str) -> str:
    config.SESSIONS_DIR.mkdir(parents=True, exist_ok=True)
    return str(config.SESSIONS_DIR / f"{session_id}.session.db")


def _require_session_db():
    sid = _session_id()
    if not sid:
        print("HERMES_SESSION_ID not set", file=sys.stderr)
        sys.exit(1)
    db_path = _session_db_path(sid)
    if not Path(db_path).exists():
        print("employee not active", file=sys.stderr)
        sys.exit(1)
    session_db.init_session_db(db_path)
    return db_path


def cmd_start(args):
    sid = _session_id()
    if not sid:
        print("HERMES_SESSION_ID not set", file=sys.stderr)
        sys.exit(1)
    db_path = _session_db_path(sid)
    session_db.init_session_db(db_path)

    central_db.init_central_db(config.CENTRAL_DB)
    max_id = central_db.get_max_message_id(config.CENTRAL_DB)
    session_db.set_read_cursor(db_path, max_id)

    print(f"employee activated: session={sid}")


def cmd_stop(args):
    sid = _session_id()
    if not sid:
        print("HERMES_SESSION_ID not set", file=sys.stderr)
        sys.exit(1)
    db_path = _session_db_path(sid)
    if Path(db_path).exists():
        Path(db_path).unlink()
        print("employee deactivated")
    else:
        print("employee not active", file=sys.stderr)
        sys.exit(1)


def cmd_list_sessions(args):
    sessions = session_db.get_active_sessions()
    if not sessions:
        print("No active sessions")
        return
    for s in sessions:
        print(f"  {s['session_id']}")
