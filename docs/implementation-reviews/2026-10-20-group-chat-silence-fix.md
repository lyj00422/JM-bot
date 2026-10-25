# 2026-10-20 Group Chat Silence Fix（群聊 @ / 关键词无响应）

## 现象

私聊正常，群聊 `1053512745` 里 @ 机器人或直接喊「大肥鱼」都没有任何回应。

## 服务器日志证据

`journalctl -u jmbot`：

```text
10-20 10:38:39 [message.group.normal]: Message 1583363288 from 3065532050@[群:1053512745] '[at:qq=2661195769] 今天天气如何'
10-20 10:38:39 [INFO] jmbot | 群聊处理开始 group_id=1053512745 ... mentioned=False segments=['text']
10-20 10:38:39 [INFO] jmbot | 群聊触发判断 ... mentioned=False replied_to_bot=False alias_match=False active=False
10-20 10:38:39 [INFO] jmbot | 群聊跳过：未命中 @、回复或昵称触发

10-20 10:39:42 [message.group.normal]: Message ... '[at:qq=2661195769] 说话'
10-20 10:39:42 [INFO] jmbot | 群聊处理开始 ... mentioned=False segments=['text']

10-20 10:39:42 [message.group.normal]: Message ... '大肥鱼说话'
10-20 10:39:42 [INFO] jmbot | 群聊触发判断 ... alias_match=True active=False
10-20 10:39:42 [INFO] jmbot | 群聊跳过：非活跃窗口或主动回复限流
```

两处关键点：

1. 适配器侧打印的原始消息里有 `[at:qq=2661195769]`，但进入规则时 `segments=['text']`、`mentioned=False`。
2. 关键词能命中（`alias_match=True`），却总被 `active=False` 拦下。

## 根因

### 1. `@` 与「回复」永远检测不到（适配器会提前拆掉这两个段）

NoneBot 2.5.0 / nonebot-adapter-onebot 2.4.6 在 `Bot.handle_event()` 里、任何 matcher 规则执行之前就改写了 `event.message`：

- `_check_reply()`：把 `reply` 段从 `event.message` 里删除，只保留 `event.reply`，并在回复机器人时置 `to_me=True`；
- `_check_at_me()`：把首/尾的 `@bot` 段 pop 掉，只置 `to_me=True`。

原始消息完整保存在 `event.original_message`（事件日志用的就是它）。

而项目里 `_mentions_bot()` / `_is_reply_to_bot()` / `_at_targets()` 都在遍历 `event.get_message()`，因此：

- `mentioned` 恒为 `False`；
- `replied_to_bot` 恒为 `False`；
- `_is_chat_message()` 里那条「收到群聊含 @」的诊断日志也从未打印过。

### 2. 关键词触发被活跃窗口死锁

`_handle_group_chat()` 原逻辑要求：非 `@`、非回复的消息必须 `active=True` 才继续。但 `set_group_active_until()` 只在**回复成功之后**才写入（`group_chat_active_seconds`，默认 600 秒）。而回复成功又必须先 `@` 或回复机器人 —— 第一条路径已经因为根因 1 失效，于是活跃窗口永远不会被打开，关键词/昵称触发也就永远不可能发生。

这就是「无论 @ 还是提及关键词都没有回应」，而私聊不受影响（私聊走 `PrivateMessageEvent`，不经过这些判断）。

## 修改

- `src/jmbot/core/message_scope.py`
  - 新增 `message_segments()`：优先读 `event.original_message`，缺失时退回 `event.get_message()`。
  - 新增 `at_targets()` / `is_at_bot()` / `is_reply_to_bot()`：`@` 与回复检测同时兼容「段已被剥离」和「段仍存在」两种适配器行为，并把 `to_me` 作为兜底信号。
- `src/jmbot/bot.py`
  - 群聊触发改用上述函数；`@`、回复、关键词三条路径恢复。
  - `_is_chat_message()` 允许「只有 @ 没有文字」的消息进入群聊处理器（适配器剥掉 at 段后正文为空），群聊正文为空时补一句占位文本，避免给模型传空内容。
  - 去掉「关键词触发必须先 active」的前置条件：`@`/回复无条件回应，昵称/关键词命中只受冷却（`JM_BOT_GROUP_CHAT_REPLY_COOLDOWN_SECONDS`，默认 45 秒）与主动回复上限（`JM_BOT_GROUP_CHAT_PROACTIVE_LIMIT`，默认 10 分钟内 3 次）限制。
- `tests/test_message_scope.py`
  - 新增 15 个用例，其中包含直接调用 `nonebot.adapters.onebot.v11.bot._check_at_me()` 的回归测试，锁死「适配器剥离 at 段」这一行为。

## 验证

本地：

```powershell
.\.venv\Scripts\python.exe -m pytest -q        # 219 passed, 74 subtests passed
```

服务器 `47.97.113.248`（`/opt/jmbot`，v0.3.11 热更新 `bot.py` + `message_scope.py`）：

```text
real-at        rule=True  to_me=True  message=['text'] original=['at', 'text']
bare-at        rule=True  to_me=True  message=['text'] original=['at']

@bot 说话                replies=1 sent=['我在，收到你的 @ 了']
首次关键词(active=False)    replies=1 sent=['在的，怎么啦']
紧跟的第二次关键词              replies=0        # 冷却生效
```

（端到端脚本用临时 SQLite + 打桩的 `request_chat_completion`，不消耗真实 API 额度，也不写生产聊天记录。）

## 备注

- 服务器改动前已备份到 `/root/jmbot-backup-20261020-1100/`。
- 本次只热更新了源文件，没有重新打 release 压缩包；下次按 `docs/server-update-flow.md` 发版时，压缩包会自然包含这次修改。
- 群聊 API Key 与总开关状态正常（日志里已走到「触发判断」），问题只在触发判定。

## 第二轮：@ 回复把 JSON 外壳发进了群里

真机复测能回复了，但回复内容变成了：

```text
{"should_reply": true, "reply": "来啦来啦，别一直催嘛 [表情]\n天气我这边也看不到呀，你自己窗外瞅一眼就知道了"}
```

原因：`build_group_messages()` 无条件把「普通消息判断时只输出 JSON……」写进 system prompt，而被 @ 时走的是 `json_output=False` + 直接用原文的分支，两者不匹配 —— 模型按 prompt 输出了决策 JSON，程序又把它当正文发了出去。

修改：

- `build_group_messages(..., decision_required=False)`：被 @ / 回复机器人时明确要求「只输出回复正文，不要输出 JSON、不要输出 should_reply 字段」；只有被动插话判断时才要求 JSON。
- `extract_chat_reply()`：`@` 回复若仍带 `{"should_reply": ..., "reply": ...}` 外壳（含 ```json 代码块），提取内部正文再发送，并打一条 `群聊 @ 回复带 JSON 包装，已提取正文` 的告警；无法解析时按原样发送。
- `face_marker()`：入站 QQ 表情在历史里渲染成白名单标记 `[qq_face:N]`（非白名单仍是 `[表情]`）。此前历史里全是 `[表情]`，模型会照着模仿，于是回复里出现字面量 `[表情]`。

服务器验证（打桩模型故意返回 JSON 外壳）：

```text
@ reply : ['来啦来啦\n天气自己瞅一眼窗外']      ← 外壳已剥掉
@ prompt has JSON rule: False                    ← @ 时不再要求 JSON
passive prompt has JSON rule: True               ← 被动判断仍然要求 JSON
```
