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

## 当前线上为什么不是原生 WebUI 管理

当前线上 worker 将 config.webui.enabled 设为 False，并将 config_manager.start_file_watcher 替换为空操作。本次检查没有 8001 监听，8081 是 JMbot 自己的管理后台。

有效人设的数据流是：

```text
JMbot 默认/会话基础人设 + 已启用的成长摘要和新条目
    → ConversationStateStore.effective_persona(scope)
    → 聊天协调器 context['persona']
    → NativeSessions.update()
    → config.personality.personality
    → MaiBot 原生推理与回复
```

昵称目前由 native_worker 写为“大肥鱼”。行为风格由 JM_BOT_BEHAVIOR_STYLE 或接入代码的默认提示设置；该默认提示鼓励自然接话和适当发起话题。表达风格由原生 Config 默认值起步，当前会话人设注入没有独立设置 reply_style。后台的人设文本如果包含行为和表达要求，仍会被作为 personality 的整体内容送入模型。

所以，仅打开原生 WebUI 或手改生成的 bot_config.toml，并不能让它成为当前会话人设的权威来源：worker 启动会重建配置，会话更新又会从 JMbot 状态注入 personality。要改用原生 WebUI，需先明确人设唯一来源、各会话是否保留不同人设、成长如何处理，并移除或重接冲突的注入逻辑。不能只打开端口后同时保留两套写入入口。

## 来源

- [官方 Bot 配置](https://docs.mai-mai.org/manual/configuration/bot-config)
- [官方 WebUI 配置管理](https://docs.mai-mai.org/manual/webui/config-management)
- [官方 WebUI 登录与设置](https://docs.mai-mai.org/manual/webui/)
- 本地同步后的 src/jmbot/native_worker.py、native_sessions.py、chat_app.py、core/conversation_state.py。

## 本次本地同步范围

以服务器 /opt/jmbot/src 为准同步聊天网关、前后台队列、模型请求封装、表情维护与发送等不同的源码，替换本地对应旧实现。服务器所带测试和部署说明并非全部最新，保留本地回归测试与迁移工具，按实测结果更新现行说明。没有删除数据库、历史事故记录或尚被运行代码引用的兼容模块，也没有修改线上配置或重启服务。
