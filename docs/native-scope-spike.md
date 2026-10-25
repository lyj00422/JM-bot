# P0 原生分会话作用域验证

核对日期：2026-10-25。锁定 MaiBot 提交：`c602a2780f4f4dda4a6be46d67abc1cfb1ad420c`。

这份 spike 是上线门槛证据，不是“原生聊天已经完成”的声明。探针只读本地锁定源码和 JMbot 接入源码，不连接 QQ、真实消息、生产数据库或模型供应商。可重复运行：

```bash
python3 scripts/native_scope_spike.py
```

本次输出确认：

- MaiBot 注册了 `maisaka.planner.before_request` 和 `maisaka.replyer.before_request`，两个 Hook 都不是可拒绝入口；它们的 `allow_abort` 为 `False`。
- Planner 的人设和行为风格构建仍读取 `global_config.personality`；Replyer 的表达风格仍读取全局 `reply_style`。Hook 能追加或改写结构化请求内容，不能证明三个人设字段已经由 `session_id` 完整替换。
- 当前 JMbot worker 已恢复 MaiBot WebUI、配置文件 watcher 和插件运行时；实例启动时复制仓库 `plugins/`。本次代码已具备 8001 原生 WebUI 启动路径，但服务器必须完成重启后才有真实监听证据。
- 已锁定并随仓库提供官方 `Mai-with-u/MaiBot-SnowLuma-Adapter` `1.1.8` 插件及其 `silk-python` 依赖。它要求 NapCat 正向 WebSocket 服务；当前服务器仍是反向客户端连 8080，官方 connector 尚未生产启用，旧 SplitAdapter 仍是回滚链。

## 合成隔离结果

现有测试用两个合成作用域交错投递消息，检查了以下宿主边界：

| 路径 | 检查结果 | 证据 |
|---|---|---|
| 入站 scope 与授权令牌 | 通过 | `tests/test_native_gateway.py`：群 A/B 令牌互斥，撤权后只拒绝对应 scope |
| 前台模型队列 | 通过 | `tests/test_fair_scheduler.py`、`tests/test_native_gateway.py`：按 scope 排队并支持撤权 |
| 后台模型队列 | 通过 | `src/jmbot/chat_app.py`：后台队列单独调度，供应商并发为 1 |
| QQ 发送回执/重试 | 通过 | `tests/test_native_delivery.py`、`tests/test_napcat_media.py`：稳定 RPC ID、回执缺失时不自动重发 |
| 表情来源和收藏归属 | 通过 | `tests/test_native_ingress.py`、`tests/test_native_delivery.py`：自动收藏按 scope，人工库显式选择 |
| 原生三字段人设在 Planner/Replyer/工具/后台的实际 prompt | **未通过** | 已扩展会话状态和 worker context，能携带 `personality`/`behavior_style`/`reply_style`；仍缺少请求级 provider 和脱敏请求捕获 |
| 记忆、表达、黑话、Focus 的所有共享资源 | **未通过** | 原生 Hook/插件没有提供可审计的统一拒绝点；不能只依赖提示词防串群 |
| 正式官方 QQ connector | **未通过** | 官方 adapter 已纳入 release，但 NapCat 正向 WS、依赖和新旧链互斥尚未完成线上验证 |

## 结论和下一步

P0 结论为 **未通过，保持当前 split 链作为可回滚运行链**。本次可以交付网关的并发、发送回执、表情来源和请求校验修复；不能把它们描述为已经完成官方 connector、原生 WebUI 或原生三字段分会话人设。

要进入 P1，必须先在 MaiBot 插件/正式扩展边界中补齐一个以真实 `chat_manager` session ID 解析有效人设的 provider，并让 Planner、Replyer、工具生成、后台学习和发送前策略共同使用固定 `persona_version`。同时需要在隔离模型端点捕获两套矛盾人设的真实请求正文，证明历史、工具 schema 和 scope 都保留，再进行 connector 与 WebUI 的兼容验证。
