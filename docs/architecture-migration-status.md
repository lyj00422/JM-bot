# 架构拆分实施状态

当前仓库已经完成可以独立验证的基础层，生产默认仍保持 NoneBot 下载与旧聊天兼容模式。

## 已实现

- `JM_BOT_SERVICE_ROLE=all|download|chat`：同一代码可运行下载角色或聊天角色。`download` 不注册聊天、Key 和戳一戳入口；`chat` 不注册 JM 下载入口。
- `JM_BOT_CHAT_BACKEND=maibot`：关闭旧 NoneBot 聊天决策链，等待 MaiBot 成为唯一聊天发送者。这个开关不会伪造 MaiBot 响应，也不会让两个消费者同时发送。
- 聊天按 `private:<QQ>`、`group:<群号>` 保存策略、人设基础层、长期记忆条目和成长版本；同一 QQ 的私聊、群 A、群 B 不共享 scope。
- 默认人设/独立基础人设与成长开关独立保存。两者不能同时关闭；关闭成长只停用成长层，历史版本保留。成长条目包含分类、依据、版本、冻结和回退信息。
- 有界 `FairChatScheduler`：每个 scope 有队列上限，热点群只合并被动候选，明确 @ 和私聊不会被静默丢弃；下载持久队列不与聊天调度器共用。
- 后台绑定回环地址，管理员密码使用 `scrypt`，登录失败限速，超级管理员固定为 `3065532050`。只有超级管理员能增删管理员。
- 好友/群申请进入后台审批表，后台操作写入 outbox，由 NoneBot 调 NapCat API 并记录应用/重试状态。群申请通过后聊天和下载仍默认关闭，需后台分别开启。
- Key 状态页面只返回服务商和是否配置，Key 仍由 `ChatStore` 加密保存；保存默认 Key 会同步默认服务商，保存独立 Key 自动关闭该 scope 的默认继承，覆盖已有独立 Key 需要确认。
- `/jm help` 仍保留，默认公开帮助不再列出 `/jmg`；帮助正文可在后台编辑。旧 `/jmg` 解析器保留在代码中用于兼容测试，但运行时默认 `JM_BOT_LEGACY_ADMIN_COMMANDS=false`，不会执行 QQ 管理命令。
- 表情包后台提供 ZIP 模板下载和人工包导入：校验 schema、图片、路径穿越、大小、重复 hash，使用历史最大编号追加，manifest 原子替换。

## 尚未允许生产切换的部分

MaiBot 本体和适配器没有被复制进这个下载仓库。MaiBot 的固定调研提交为 `c602a2780f4f4dda4a6be46d67abc1cfb1ad420c`，但线上 NapCat 当前只有一个反向 WebSocket 消费者。仍需在隔离端到端环境验证：

1. MaiBot OneBot adapter 的连接、事件过滤、媒体 codec、@/引用/戳一戳行为。
2. 两个进程是否能共享 NapCat，或必须增加一个只做 ingress 路由的本机桥接层。
3. MaiBot 原生记忆/profile/emoji 的 scope 是否覆盖群、私聊和成员印象；不满足时只补最小扩展。
4. 热点群回放、模型超时、事件循环延迟和服务器内存上限。

在这些实验通过前，服务器继续使用 `JM_BOT_SERVICE_ROLE=all`、`JM_BOT_CHAT_BACKEND=nonebot`。切换 MaiBot 时必须先停止旧聊天发送者、备份 SQLite/WAL 和 master key、完成 dry-run 对账，再把 `JM_BOT_CHAT_BACKEND=maibot` 作为唯一聊天 sender 启动；下载服务可单独保留。

## 后台启动

```bash
cd /opt/jmbot
./.venv/bin/python main_admin.py --set-password 3065532050
sudo cp deploy/jmbot-admin.service.example /etc/systemd/system/jmbot-admin.service
sudo systemctl daemon-reload
sudo systemctl enable --now jmbot-admin
ssh -L 8081:127.0.0.1:8081 root@服务器
```

浏览器打开 `http://127.0.0.1:8081/`。管理后台没有公网监听；SSH 会话关闭后页面不可访问。
