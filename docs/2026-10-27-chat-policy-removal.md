# 移除 JMbot 旧聊天准入限制

2026-10-27：聊天准入统一交给 MaiBot 原生适配器规则。JMbot 不再根据历史 chat_enabled、chat_blacklisted、聊天全局开关或策略版本阻止入站、模型调用、发送和媒体读取。历史数据库字段保留用于归档，不需要批量改写数据库。原生运行时令牌、会话隔离和模型凭据校验继续生效。

后台移除聊天允许/黑名单控件，并拒绝旧聊天权限写入接口。下载授权和下载黑名单保持独立。好友、群加入审批不再写入聊天授权。

非好友私聊可以进入聊天；官方适配器按 message_type 识别群临时私聊，来源 group_id 不再把临时私聊划入群作用域。群通知仍使用群作用域。

针对性验证：旧库关闭/拉黑与聊天总开关不影响新群及私聊；模型凭据失效仍拒绝旧令牌；临时私聊、群聊及群通知的作用域正确；旧后台聊天控件不再出现，旧接口拒绝写入；下载权限保持有效。

验证结果：10 项针对性测试通过；后台 JavaScript 语法和 git diff --check 通过。服务器 9 个部署文件及原生运行时适配器副本哈希一致；实际 HTTP 验证新群、私聊和旧库禁用群均可获取会话授权，三个服务 active、单个原生进程 ready、官方适配器已连接。没有发送合成 QQ 消息，真实临时私聊收发仍需实际新消息验证。

部署备份：/opt/jmbot-updates/retire-chat-policy-20261027/backup-20261027-005433，含原程序文件、运行时 router.py、聊天和下载权限数据库快照。

方案复核：

| Section | Verdict | Notes |
|---|---|---|
| Assumptions & Understanding | Pass | 已确认线上使用官方适配器双 WS |
| Planning & Tradeoffs | Pass | 删除旧聊天门禁，保留历史数据与原生规则 |
| Complexity & Abstractions | Pass | 删除检查和控件，无新增运行时抽象 |
| Correctness First | Pass | 先复现旧限制，再验证删除后的行为 |
| Scope Discipline | Pass | 仅聊天准入、临时私聊路由及相关管理入口 |
| Communication & Honesty | Pass | 区分 HTTP 验证与真实 QQ 收发 |
| Iteration & Success Criteria | Pass | 针对性测试、部署哈希及服务就绪均通过 |

Final Recommendation: Proceed。
