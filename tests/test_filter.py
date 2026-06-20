"""测试过滤引擎"""

from unittest.mock import patch

import pytest

from employee.filter import classify_message


DEFAULT_CFG = {
    "rules": {
        "popup": [],
        "popup_excluded": [],
        "silent": [],
        "silent_excluded": [],
    }
}


def _cfg(**overrides):
    cfg = dict(DEFAULT_CFG)
    cfg["rules"].update(overrides)
    return cfg


class TestClassifyMessage:
    def test_no_rules_default_normal(self):
        with patch("employee.filter.load_config", return_value=DEFAULT_CFG):
            assert classify_message("any.type", {}) == "normal"

    def test_popup_match(self):
        rules = {"popup": [{"type": "alert\\.critical"}]}
        with patch("employee.filter.load_config", return_value=_cfg(**rules)):
            assert classify_message("alert.critical", {}) == "popup"

    def test_popup_no_match(self):
        rules = {"popup": [{"type": "alert\\.critical"}]}
        with patch("employee.filter.load_config", return_value=_cfg(**rules)):
            assert classify_message("info.normal", {}) == "normal"

    def test_silent_match(self):
        rules = {"silent": [{"type": "heartbeat"}]}
        with patch("employee.filter.load_config", return_value=_cfg(**rules)):
            assert classify_message("heartbeat", {}) == "silent"

    def test_popup_excluded_blocks_popup(self):
        rules = {
            "popup": [{"type": "alert"}],
            "popup_excluded": [{"type": "alert", "props": {"env": "prod"}}],
        }
        with patch("employee.filter.load_config", return_value=_cfg(**rules)):
            assert classify_message("alert", {"env": "prod"}) == "normal"

    def test_popup_excluded_but_no_popup_rule(self):
        rules = {
            "popup_excluded": [{"type": "alert"}],
        }
        with patch("employee.filter.load_config", return_value=_cfg(**rules)):
            assert classify_message("alert", {}) == "normal"

    def test_silent_excluded_blocks_silent(self):
        rules = {
            "silent": [{"type": "heartbeat"}],
            "silent_excluded": [{"type": "heartbeat", "props": {"env": "staging"}}],
        }
        with patch("employee.filter.load_config", return_value=_cfg(**rules)):
            assert classify_message("heartbeat", {"env": "staging"}) == "normal"
            assert classify_message("heartbeat", {"env": "prod"}) == "silent"

    def test_props_matching(self):
        rules = {"popup": [{"type": ".*", "props": {"priority": "P0|P1"}}]}
        with patch("employee.filter.load_config", return_value=_cfg(**rules)):
            assert classify_message("any", {"priority": "P1"}) == "popup"
            assert classify_message("any", {"priority": "P2"}) == "normal"
            assert classify_message("any", {}) == "normal"

    def test_multiple_props_all_must_match(self):
        rules = {"popup": [{"type": ".*", "props": {"env": "prod", "severity": "critical"}}]}
        with patch("employee.filter.load_config", return_value=_cfg(**rules)):
            assert classify_message("any", {"env": "prod", "severity": "critical"}) == "popup"
            assert classify_message("any", {"env": "prod", "severity": "low"}) == "normal"

    def test_matching_order(self):
        rules = {
            "popup": [{"type": "alert"}],
            "popup_excluded": [{"type": "alert", "props": {"env": "dev"}}],
            "silent": [{"type": "heartbeat"}],
            "silent_excluded": [{"type": "heartbeat", "props": {"env": "prod"}}],
        }
        with patch("employee.filter.load_config", return_value=_cfg(**rules)):
            assert classify_message("alert", {"env": "dev"}) == "normal"
            assert classify_message("alert", {"env": "prod"}) == "popup"
            assert classify_message("heartbeat", {"env": "prod"}) == "normal"
            assert classify_message("heartbeat", {"env": "dev"}) == "silent"
            assert classify_message("unknown", {}) == "normal"

    def test_regex_type_pattern(self):
        rules = {"popup": [{"type": r"github\.issue\.\w+"}]}
        with patch("employee.filter.load_config", return_value=_cfg(**rules)):
            assert classify_message("github.issue.create", {}) == "popup"
            assert classify_message("github.pull.request", {}) == "normal"
