# MaiBot 原生人设与当前线上接入

核对日期：2026-10-25。依据为线上锁定 MaiBot 提交 c602a2780f4f4dda4a6be46d67abc1cfb1ad420c 的配置模型、提示构建代码及 JMbot 运行源码，以及官方文档。

## 原生设置方式

MaiBot 原生人设保存在实例目录的 config/bot_config.toml；WebUI 是这些配置的表单编辑入口，默认地址为 http://127.0.0.1:8001。官方文档中的入口为“麦麦设置 → 基础 → 身份与人格”；也可以从“详细设置”打开对应配置节。页面名称可能随版本不同，锁定源码对应字段标签为“人格设定”“行为风格”“表达风格”。

| 配置字段 | 含义 |
|---|---|
| bot.nickname | 机器人名字、自称 |
| bot.alias_names | 别人对机器人的称呼，辅助识别提及 |
| personality.personality | 身份、背景、性格 |
| personality.behavior_style | Planner 的行动准则，如何参与聊天、何时旁观 |
| personality.reply_style | 回复措辞、语气和长度习惯 |
| personality.multiple_reply_style | 临时备用表达风格 |
| personality.multiple_probability | 使用备用表达风格的概率，0 表示不随机切换 |

示例（用于原生部署，不是当前线上实例的直接修改入口）：

```toml
[bot]
nickname = "大肥鱼"
alias_names = ["肥鱼", "鱼"]

[personality]
personality = "你叫大肥鱼，性格随和，有好奇心，喜欢和群友交流。"
behavior_style = "有相关想法时自然接话；有人求助时认真回应；不连续刷屏。"
reply_style = "日常口语，通常一两句，适度吐槽，不堆砌比喻。"
multiple_probability = 0
```

正常原生部署中，WebUI 与直接编辑文件修改的是同一套配置，多数人设设置可热重载。身份与性格、行动准则、表达方式分开设置；活跃度另由 chat.reply_timing 管理。锁定源码中，src/maisaka/chat_loop_service.py 读取 personality 和 behavior_style，src/chat/replyer/maisaka_generator_base.py 读取 personality 和 reply_style。

## 迁移后的数据流

JMbot 仍保留原有会话人设、成长和版本数据，不删除历史记录。切换到原生聊天前，管理员离线运行 `scripts/migrate_persona_to_maibot.py`，把三字段转换为该聊天流的原生 `chat.reply_style.chat_prompts` 条目，保存到实例自己的 `config/bot_config.toml`：

```text
JMbot 默认/会话基础人设 + 已启用的成长摘要和新条目
    → 一次性离线迁移脚本
    → MaiBot 原生 chat.reply_style.chat_prompts
    → MaiBot 官方 ChatConfigUtils
    → Planner / Replyer
```

服务器切换前先停止聊天 worker，再执行一次（路径按实际实例调整）：

```bash
/opt/MaiBot/.venv/bin/python /opt/jmbot/scripts/migrate_persona_to_maibot.py \
  --db /opt/jmbot/data/conversation.sqlite3 \
  --config /opt/jmbot-split-data/data/chat-service/instances/qq_2661195769_runtime/config/bot_config.toml
```

脚本默认创建带 UTC 时间戳的 `.bak.*` 备份；可以先加 `--dry-run` 查看将要添加的 scope。

MaiBot WebUI 的“聊天 → 聊天流 prompt/额外 Prompt”现在是聊天运行时的唯一配置入口。迁移脚本保留已有原生条目，不覆盖 WebUI 已配置的内容；迁移完成后不会再次自动执行。JMbot 后台的人设表仍作为历史数据和迁移备份保留，聊天运行时不读取、写入或注入这些字段，也不通过 Hook 插入 `SystemMessageItem`。

worker 启动先读取实例现有的原生配置，再只补充 QQ 传输和模型网关所需字段，因此重启不会覆盖 WebUI 修改的人格、行为风格、回复风格、活跃度或额外 Prompt。

## 来源

- [官方 Bot 配置](https://docs.mai-mai.org/manual/configuration/bot-config)
- [官方 WebUI 配置管理](https://docs.mai-mai.org/manual/webui/config-management)
- [官方 WebUI 登录与设置](https://docs.mai-mai.org/manual/webui/)
- 本地同步后的 src/jmbot/native_worker.py、native_sessions.py、chat_app.py、core/conversation_state.py。

## 本次本地同步范围

以服务器 /opt/jmbot/src 为准同步聊天网关、前后台队列、模型请求封装、表情维护与发送等不同的源码，替换本地对应旧实现。服务器所带测试和部署说明并非全部最新，保留本地回归测试与迁移工具，按实测结果更新现行说明。没有删除数据库、历史事故记录或尚被运行代码引用的兼容模块，也没有修改线上配置或重启服务。
