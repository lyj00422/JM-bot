# JMbot 会话作用域策略

这是 JMbot 与锁定版 MaiBot 之间的正式插件边界。插件通过运行时短期 token
向 JMbot coordinator 查询 `scope` 的授权和三字段有效人设：

* 入站 `chat.receive.before_process` 和出站 `send_service.before_send` 是可中止 Hook，策略服务不可用时拒绝；
* Planner 与 Replyer 的实际 Context Items 都追加同一份固定 `persona_revision`；
* 适配器、模型和管理员凭据不会进入插件配置或日志。

插件不自行计算 native session ID，也不把一个会话的人设写入 MaiBot 全局配置。
