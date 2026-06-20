"""测试 GitHub Inbox 消息源"""

from unittest.mock import patch, MagicMock

import pytest

from employee.sources import inbox


class TestMapNotification:
    def test_issue_mention_popup(self):
        n = {
            "id": "1",
            "reason": "mention",
            "repository": {"full_name": "owner/repo"},
            "subject": {
                "title": "Bug found",
                "type": "Issue",
                "url": "https://api.github.com/repos/owner/repo/issues/42",
            },
            "updated_at": "2026-01-01T00:00:00Z",
        }
        msg = inbox._map_notification(n)
        assert msg["type"] == "github.issue"
        assert msg["title"] == "Bug found"
        assert "mention" in msg["content"]
        assert "[#42]" in msg["content"]
        assert msg["props"]["reason"] == "mention"
        assert msg["props"]["source"] == "inbox"

    def test_pr_author_normal(self):
        n = {
            "id": "2",
            "reason": "author",
            "repository": {"full_name": "owner/repo"},
            "subject": {
                "title": "Fix the thing",
                "type": "PullRequest",
                "url": "https://api.github.com/repos/owner/repo/pulls/100",
            },
            "updated_at": "2026-01-01T00:00:00Z",
        }
        msg = inbox._map_notification(n)
        assert msg["type"] == "github.pr"
        assert "[#100]" in msg["content"]

    def test_discussion_comment_normal(self):
        n = {
            "id": "3",
            "reason": "comment",
            "repository": {"full_name": "org/project"},
            "subject": {
                "title": "Ideas for v2",
                "type": "Discussion",
                "url": "https://api.github.com/repos/org/project/discussions/5",
            },
            "updated_at": "2026-01-01T00:00:00Z",
        }
        msg = inbox._map_notification(n)
        assert msg["type"] == "github.discussion"
        assert "[#5]" in msg["content"]

    def test_subscribed_silent(self):
        n = {
            "id": "4",
            "reason": "subscribed",
            "repository": {"full_name": "owner/repo"},
            "subject": {
                "title": "Random issue",
                "type": "Issue",
                "url": "https://api.github.com/repos/owner/repo/issues/1",
            },
            "updated_at": "2026-01-01T00:00:00Z",
        }
        msg = inbox._map_notification(n)
        assert msg["props"]["reason"] == "subscribed"

    def test_security_alert_popup(self):
        n = {
            "id": "5",
            "reason": "security_alert",
            "repository": {"full_name": "owner/repo"},
            "subject": {
                "title": "Critical vulnerability",
                "type": "Issue",
                "url": "https://api.github.com/repos/owner/repo/issues/99",
            },
            "updated_at": "2026-01-01T00:00:00Z",
        }
        msg = inbox._map_notification(n)
        assert msg["props"]["reason"] == "security_alert"

    def test_no_url_no_crash(self):
        n = {
            "id": "6",
            "reason": "author",
            "repository": {"full_name": "owner/repo"},
            "subject": {
                "title": "Plain notification",
                "type": "Issue",
            },
            "updated_at": "2026-01-01T00:00:00Z",
        }
        msg = inbox._map_notification(n)
        assert msg["title"] == "Plain notification"

    def test_unknown_type(self):
        n = {
            "id": "7",
            "reason": "manual",
            "repository": {"full_name": "owner/repo"},
            "subject": {
                "title": "Something else",
                "type": "UnknownType",
                "url": "https://api.github.com/repos/owner/repo/something/1",
            },
            "updated_at": "2026-01-01T00:00:00Z",
        }
        msg = inbox._map_notification(n)
        assert msg["type"] == "github.unknowntype"


class TestFetchNotifications:
    @patch("employee.sources.inbox.subprocess.run")
    def test_successful_fetch(self, mock_run):
        mock_run.return_value = MagicMock(
            returncode=0,
            stdout='[{"id":"1","reason":"mention","repository":{"full_name":"a/b"},"subject":{"title":"T","type":"Issue","url":"u"},"updated_at":"2026-01-01T00:00:00Z"}]',
        )
        result = inbox._fetch_notifications()
        assert len(result) == 1
        assert result[0]["id"] == "1"

    @patch("employee.sources.inbox.subprocess.run")
    def test_failed_fetch(self, mock_run):
        mock_run.return_value = MagicMock(returncode=1, stderr="error")
        result = inbox._fetch_notifications()
        assert result == []

    @patch("employee.sources.inbox.subprocess.run")
    def test_fetch_with_since(self, mock_run):
        mock_run.return_value = MagicMock(returncode=0, stdout="[]")
        inbox._fetch_notifications("2026-01-01T00:00:00Z")
        args = mock_run.call_args[0][0]
        assert "--raw-field" in args
        assert "since=2026-01-01T00:00:00Z" in str(args)

    @patch("employee.sources.inbox.subprocess.run")
    def test_invalid_json(self, mock_run):
        mock_run.return_value = MagicMock(returncode=0, stdout="not json")
        result = inbox._fetch_notifications()
        assert result == []


class TestGetSelfUser:
    @patch("employee.sources.inbox.subprocess.run")
    def test_successful(self, mock_run):
        mock_run.return_value = MagicMock(returncode=0, stdout="tobylinas2\n")
        assert inbox._get_self_user() == "tobylinas2"

    @patch("employee.sources.inbox.subprocess.run")
    def test_failure(self, mock_run):
        mock_run.side_effect = Exception("timeout")
        assert inbox._get_self_user() == ""


class TestPollInbox:
    def test_poll_empty_notifications(self):
        stop = MagicMock()
        stop.is_set.side_effect = [False, True]

        with (
            patch("employee.sources.inbox._fetch_notifications", return_value=[]),
            patch("employee.sources.inbox._get_self_user", return_value="tobylinas2"),
            patch("employee.sources.inbox.central_db.init_central_db"),
            patch("employee.sources.inbox.central_db.message_exists_by_url", return_value=False),
        ):
            inbox.poll_inbox(30, stop)

    def test_poll_with_notifications(self):
        stop = MagicMock()
        stop.is_set.side_effect = [False, True]

        notif = {
            "id": "1",
            "reason": "mention",
            "repository": {"full_name": "owner/repo"},
            "subject": {
                "title": "Test",
                "type": "Issue",
                "url": "https://api.github.com/repos/owner/repo/issues/1",
            },
            "updated_at": "2026-01-01T00:00:00Z",
        }

        with (
            patch("employee.sources.inbox._fetch_notifications", return_value=[notif]),
            patch("employee.sources.inbox._get_self_user", return_value="tobylinas2"),
            patch("employee.sources.inbox.central_db.init_central_db"),
            patch("employee.sources.inbox.central_db.message_exists_by_url", return_value=False),
            patch("employee.sources.inbox.central_db.insert_message", return_value=1) as mock_insert,
        ):
            inbox.poll_inbox(30, stop)
            assert mock_insert.called

    def test_poll_dedup_by_url(self):
        stop = MagicMock()
        stop.is_set.side_effect = [False, True]

        notif = {
            "id": "1",
            "reason": "mention",
            "repository": {"full_name": "owner/repo"},
            "subject": {
                "title": "Test",
                "type": "Issue",
                "url": "https://api.github.com/repos/owner/repo/issues/1",
            },
            "updated_at": "2026-01-01T00:00:00Z",
        }

        with (
            patch("employee.sources.inbox._fetch_notifications", return_value=[notif, notif]),
            patch("employee.sources.inbox._get_self_user", return_value="tobylinas2"),
            patch("employee.sources.inbox.central_db.init_central_db"),
            patch("employee.sources.inbox.central_db.message_exists_by_url", side_effect=[False, True]),
            patch("employee.sources.inbox.central_db.insert_message", return_value=1) as mock_insert,
        ):
            inbox.poll_inbox(30, stop)
            assert mock_insert.call_count == 1

    def test_poll_error_handling(self):
        stop = MagicMock()
        stop.is_set.side_effect = [False, True]

        with (
            patch("employee.sources.inbox._fetch_notifications", side_effect=Exception("boom")),
            patch("employee.sources.inbox._get_self_user", return_value="tobylinas2"),
            patch("employee.sources.inbox.central_db.init_central_db"),
        ):
            inbox.poll_inbox(30, stop)
