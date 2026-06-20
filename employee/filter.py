"""过滤引擎 - 消息分类

分类结果:
  - popup  : 弹窗消息，立即打断
  - normal : 普通消息，空闲时显示
  - silent : 静默消息，存库但不展示
"""

import re
from typing import Any

from .yaml_config import load_config


def classify_message(type_: str, props: dict[str, str]) -> str:
    """按规则分类消息: popup / silent / normal"""
    if type_.startswith("todo."):
        return "normal"

    cfg = load_config()
    rules = cfg.get("rules", {})

    ctx = {"type": type_, "props": props}

    for rule in rules.get("popup_excluded", []):
        if _match_rule(rule, ctx):
            break
    else:
        for rule in rules.get("popup", []):
            if _match_rule(rule, ctx):
                return "popup"

    for rule in rules.get("silent_excluded", []):
        if _match_rule(rule, ctx):
            return "normal"

    for rule in rules.get("silent", []):
        if _match_rule(rule, ctx):
            return "silent"

    return "normal"


def _match_rule(rule: dict[str, Any], ctx: dict) -> bool:
    type_pattern = rule.get("type", ".*")
    if not re.search(type_pattern, ctx["type"]):
        return False

    props_patterns = rule.get("props", {})
    for key, pattern in props_patterns.items():
        val = ctx["props"].get(key, "")
        if not re.search(pattern, val):
            return False

    return True
