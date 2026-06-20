---
name: employee
description: "多渠道消息聚合系统 — 对接 GitHub Webhook/Inbox、钉钉通知。用户说「激活 employee」开始接管消息，后续新消息自动注入到对话。"
version: 0.2.0
author: "@toby"

metadata:
  hermes:
    tags: [Productivity, Communication, GitHub, DingTalk]
    related_skills: []
    requires_tools:
      - employee_activate
      - employee_deactivate
      - employee_send
      - employee_todo
---

# Hermes Employee Skill

此技能让你可以接收来自多个渠道的消息（GitHub Webhook、GitHub Inbox、钉钉）并统一管理待办事项。

## 激活流程

用户说「激活 employee」或「/employee activate」时，调用 `employee_activate` 工具。
激活后，post_tool_call hook 会开始自动检查消息并注入到对话，无需手动调用 employee_check。

用户说「停用 employee」时，调用 `employee_deactivate` 工具。

## 消息处理原则

1. 被私聊或在群聊中被 @ 时，先回复「收到，正在处理」再执行任务
2. 处理消息时查看最近完整上下文，不要只看最新一条
3. 向消息源回复，不在当前对话中输出
4. 复杂任务交给 subagent 处理
5. 善用 todo 系统跟踪和管理任务
