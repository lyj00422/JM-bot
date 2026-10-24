# 架构拆分实施状态

2026-10-24：仓库包含 NoneBot 下载、原生 MaiBot 协调器和本机管理后台三进程代码、独立依赖锁、服务示例、离线迁移工具。操作说明见 [split-services-runbook.md](split-services-runbook.md)，版本见 deploy/source-lock.json。本次没有登录或部署服务器。

入口采用现有 NapCat 单一反向 WebSocket + SplitAdapter，不使用官方统一 QQ connector。此前文档对官方 connector SHA 的“已验证”描述缺少可复现证据，已撤回。实际调用锁定 MaiBot 的原生 MainSystem、Planner、reply gate、消息接收、PlatformIO 和 A_Memorix；每个 QQ 账号/群/私聊使用独立进程与完整数据根目录，隔离原生全局 profile、缓存、向量、表情和后台任务。

后台包含身份/session/CSRF、审批与结果恢复、域独立策略与持久 desired/applied、任务模型与加密凭据、原生实例与事实记忆管理、人设成长版本/证据/冻结/回退、表情预览/草案/审核/作用域/事务 ID、下载队列和帮助版本，以及备份/哈希核验/恢复/dry-run/可续跑迁移/回滚保全。QQ /jmg 不进入运行链。

本地检查与锁定上游运行初始化不代替真实 NapCat、学校/官方模型、媒体 codec、服务器容量和 P10 生产观察验收。缺少模型任务时明确停用，不借用其他凭据。语音传输尚不可用，配置拒绝启用；离线原生实例的记忆管理明确报错。原生记忆要同时配置 memory 和 embedding。

共享传输限制：下载进程承载唯一 NapCat 入口与 QQ sender，停止它会中断聊天收发；业务数据库、队列和模型执行分别隔离。小内存主机须从低实例数起测。观察及回滚窗口结束前保留 legacy 兼容源码与旧数据，split 模式不启用旧聊天链。P0 的实际协议/容量与 P10 观察须由使用者部署后验收，不能宣称全部阶段已通过。

本次本地验证：拆分 RPC/入口/策略恢复/凭据确认/审批恢复/迁移、表情导入、后台、会话状态、下载队列、公平调度及既有解析/凭据兼容共 105 项测试、40 个子测试通过；Python 编译、后台 JavaScript 语法与 Git 差异检查通过。使用锁定上游及其 Python 3.12 依赖，在临时数据根目录、回环模拟模型网关下完成 MainSystem、A_Memorix 和插件子进程初始化及关闭；该检查不调用真实模型、不连接 QQ，不能替代服务器端到端验收。
