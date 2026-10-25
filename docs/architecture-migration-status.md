# 架构拆分实施状态

2026-10-25：以线上运行源码为基准核对并同步本地聊天链路。服务器的下载、聊天协调器和管理后台三个服务均为 active；一个 MaiBot 原生 worker 为 running/ready。上游提交与 uv.lock 哈希符合 deploy/source-lock.json，已跟踪上游文件无本地修改。本次只读核对服务器，没有部署或重启。操作说明见 [split-services-runbook.md](split-services-runbook.md)。

入口采用现有 NapCat 单一反向 WebSocket + SplitAdapter，不使用官方统一 QQ connector。实际调用锁定 MaiBot 的原生 MainSystem、Planner、reply gate、消息接收、PlatformIO 和 A_Memorix。每个账号使用一个原生进程，各群/私聊通过会话上下文隔离配置、人设、数据库、表情缓存和后台任务；向量内核按会话串行切换。前台模型队列有 2 个 worker，后台模型队列有 1 个 worker；原先“每群独立进程”和“全局仅一个模型请求”的说明已过时。

后台包含身份/session/CSRF、审批与结果恢复、域独立策略与持久 desired/applied、任务模型与加密凭据、原生实例与事实记忆管理、人设成长版本/证据/冻结/回退、表情预览/草案/审核/作用域/事务 ID、下载队列和帮助版本，以及备份/哈希核验/恢复/dry-run/可续跑迁移/回滚保全。QQ /jmg 不进入运行链。

本地检查与锁定上游运行初始化不代替真实 NapCat、学校/官方模型、媒体 codec、服务器容量和 P10 生产观察验收。缺少模型任务时明确停用，不借用其他凭据。语音传输尚不可用，配置拒绝启用；离线原生实例的记忆管理明确报错。原生记忆要同时配置 memory 和 embedding。

共享传输限制：下载进程承载唯一 NapCat 入口与 QQ sender，停止它会中断聊天收发；业务数据库、队列和模型执行分别隔离。小内存主机须从低实例数起测。观察及回滚窗口结束前保留 legacy 兼容源码与旧数据，split 模式不启用旧聊天链。P0 的实际协议/容量与 P10 观察须由使用者部署后验收，不能宣称全部阶段已通过。

历史本地验证记录（2026-10-24，同步线上代码之前）：拆分 RPC/入口/策略恢复/凭据确认/审批恢复/迁移、表情导入、后台、会话状态、下载队列、公平调度及既有解析/凭据兼容共 105 项测试、40 个子测试通过；Python 编译、后台 JavaScript 语法与 Git 差异检查通过。使用锁定上游及其 Python 3.12 依赖，在临时数据根目录、回环模拟模型网关下完成 MainSystem、A_Memorix 和插件子进程初始化及关闭；该检查不调用真实模型、不连接 QQ，不能替代服务器端到端验收，也不是本次同步后的测试结果。

MaiBot 原生 WebUI 与当前人设来源见 [maibot-persona.md](maibot-persona.md)。当前接入代码关闭原生 WebUI 和配置文件 watcher，由 JMbot 会话状态提供有效人设；单独修改生成的 bot_config.toml 不能替代这一来源。

P0 作用域 spike 见 [native-scope-spike.md](native-scope-spike.md)。截至 2026-10-25，Planner/Replyer Hook 仍不能作为拒绝入口，锁定版本也没有可直接部署的官方 QQ connector；三字段人设的请求级替换尚未获得真实 prompt 捕获证据。因此本次交付只包含拆分链路的并发、回执、表情来源和请求校验修复，保留当前链路作为回滚运行链，不把它描述为官方 connector 或原生 WebUI 迁移完成。
