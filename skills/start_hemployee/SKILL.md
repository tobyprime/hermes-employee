---
name: start
description: 激活 Hermes Employee 消息系统。启动会话监听，开始检查消息队列。配合 /hermes-employee:stop 停用。
disable-model-invocation: true
---

Activate Hermes Employee for the current Hermes Agent session.

```bash
hemployee start
```

After activation, messages from GitHub/DingTalk will be delivered to this session.
