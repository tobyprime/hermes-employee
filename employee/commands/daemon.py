"""Daemon command — starts all background source daemons."""
import logging
import signal
import sys
import threading
import time

from ..sources.github import run_server, get_github_config
from ..sources.inbox import run_inbox_source, poll_inbox
from ..sources.dingtalk import run_dingtalk_source, poll_dingtalk

logger = logging.getLogger("employee.daemon")

_workers: list[threading.Thread] = []
_stop_events: list[threading.Event] = []


def _signal_handler(signum, frame):
    logger.info(f"Signal {signum} received, shutting down...")
    for evt in _stop_events:
        evt.set()
    for t in _workers:
        t.join(timeout=5)
    sys.exit(0)


def cmd_daemon(args):
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
    )

    signal.signal(signal.SIGTERM, _signal_handler)
    signal.signal(signal.SIGINT, _signal_handler)

    gh_config = get_github_config()

    # ── GitHub Webhook ───────────────────────────────────────
    port = gh_config.get("port", 3001)
    smee_url = gh_config.get("smee_url", "")
    repos = gh_config.get("repos", [])
    events = gh_config.get("events", ["*"])

    import subprocess as _sp
    self_user = gh_config.get("self_user", "")
    try:
        detected = _sp.run(
            ["gh", "api", "user", "--jq", ".login"],
            capture_output=True, text=True, timeout=5,
        ).stdout.strip()
        if detected:
            self_user = detected
    except Exception:
        pass

    import os as _os
    proxy = _os.environ.get("HTTP_PROXY") or _os.environ.get("https_proxy") or ""

    gh_stop = threading.Event()
    gh_thread = threading.Thread(
        target=lambda: run_server(
            port=port, smee_url=smee_url,
            repos=repos or None, events=events or None,
            self_user=self_user, proxy=proxy,
            foreground=False,
        ),
        daemon=True,
    )
    gh_thread.start()
    _workers.append(gh_thread)
    logger.info(f"[daemon] GitHub Webhook started (port={port})")

    # ── GitHub Inbox ─────────────────────────────────────────
    inbox_interval = gh_config.get("inbox_interval", 30)
    inbox_stop = threading.Event()
    _stop_events.append(inbox_stop)
    inbox_thread = threading.Thread(
        target=poll_inbox,
        args=(inbox_interval, inbox_stop),
        daemon=True,
    )
    inbox_thread.start()
    _workers.append(inbox_thread)
    logger.info(f"[daemon] GitHub Inbox poller started (interval={inbox_interval}s)")

    # ── DingTalk ─────────────────────────────────────────────
    import os as _os2
    dt_interval = int(_os2.environ.get("DINGTALK_POLL_INTERVAL", "5"))
    dt_stop = threading.Event()
    _stop_events.append(dt_stop)
    dt_thread = threading.Thread(
        target=poll_dingtalk,
        args=(dt_interval, dt_stop),
        daemon=True,
    )
    dt_thread.start()
    _workers.append(dt_thread)
    logger.info(f"[daemon] DingTalk poller started (interval={dt_interval}s)")

    logger.info("All source daemons running. Press Ctrl+C to stop.")
    try:
        while True:
            time.sleep(60)
    except KeyboardInterrupt:
        logger.info("Shutting down...")
        for evt in _stop_events:
            evt.set()
