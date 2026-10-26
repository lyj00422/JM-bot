# 架构拆分实施状态

2026-10-26：JMbot 的会话人设、成长和版本数据继续保留，但不再作为第二套聊天配置运行。切换前由 `scripts/migrate_persona_to_maibot.py` 一次性离线写入 MaiBot 原生 `chat.reply_style.chat_prompts`；之后以 MaiBot WebUI 为唯一聊天配置入口。worker 启动先读取现有原生配置，保留 WebUI 对人格、行为、回复风格、活跃度和额外 Prompt 的修改，运行时不再读取或注入 JMbot 人设。

入口采用现有 NapCat 单一反向 WebSocket + SplitAdapter，不使用官方统一 QQ connector。实际调用锁定 MaiBot 的原生 MainSystem、Planner、reply gate、消息接收、PlatformIO 和 A_Memorix。每个账号使用一个原生进程，各群/私聊通过会话上下文隔离授权、数据库、表情缓存和后台任务；聊天人设由 MaiBot 原生配置中的对应额外 Prompt 选择。向量内核按会话串行切换。前台模型队列有 2 个 worker，后台模型队列有 1 个 worker；原先“每群独立进程”和“全局仅一个模型请求”的说明已过时。

后台包含身份/session/CSRF、审批与结果恢复、域独立策略与持久 desired/applied、任务模型与加密凭据、原生实例与事实记忆管理、人设成长版本/证据/冻结/回退、表情预览/草案/审核/作用域/事务 ID、下载队列和帮助版本，以及备份/哈希核验/恢复/dry-run/可续跑迁移/回滚保全。QQ /jmg 不进入运行链。

本地检查与锁定上游运行初始化不代替真实 NapCat、学校/官方模型、媒体 codec、服务器容量和 P10 生产观察验收。缺少模型任务时明确停用，不借用其他凭据。语音传输尚不可用，配置拒绝启用；离线原生实例的记忆管理明确报错。原生记忆要同时配置 memory 和 embedding。

共享传输限制：下载进程承载唯一 NapCat 入口与 QQ sender，停止它会中断聊天收发；业务数据库、队列和模型执行分别隔离。小内存主机须从低实例数起测。观察及回滚窗口结束前保留 legacy 兼容源码与旧数据，split 模式不启用旧聊天链。P0 的实际协议/容量、插件加载、真实 prompt 捕获与 P5 观察须由使用者部署后验收，不能宣称全部阶段已通过。

历史本地验证记录（2026-10-24，同步线上代码之前）：拆分 RPC/入口/策略恢复/凭据确认/审批恢复/迁移、表情导入、后台、会话状态、下载队列、公平调度及既有解析/凭据兼容共 105 项测试、40 个子测试通过；Python 编译、后台 JavaScript 语法与 Git 差异检查通过。使用锁定上游及其 Python 3.12 依赖，在临时数据根目录、回环模拟模型网关下完成 MainSystem、A_Memorix 和插件子进程初始化及关闭；该检查不调用真实模型、不连接 QQ，不能替代服务器端到端验收，也不是本次同步后的测试结果。

MaiBot 原生 WebUI 与人设迁移见 [maibot-persona.md](maibot-persona.md)。新代码通过每实例 8001 回环 WebUI、正常 watcher 和插件 runtime 提供原生管理面；JMbot 不再注册人设 Hook 或向模型请求发送人设版本头。

P0 作用域 spike 见 [native-scope-spike.md](native-scope-spike.md)。入站/发送前策略检查仍保留；人设由 MaiBot 原生配置管理，不再使用独立的人设 Hook。服务器仍需在发布后验证 WebUI 修改、原生 Prompt 捕获、并发隔离和 connector 链路。
