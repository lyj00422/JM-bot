# 拆分服务安装与迁移

本次代码实现不部署服务器。入口为下载 main.py、原生聊天 main_chat.py、后台 main_admin.py；main_migrate.py 负责离线备份、核验、恢复与迁移。

## 环境与配置

下载、聊天协调器、后台分别建立 .venv-download、.venv-chat、.venv-admin，推荐统一 Python 3.12，分别安装 requirements-download.lock、requirements-chat.lock、requirements-admin.lock。此次下载 lock 以 Linux Python 3.12 解析，旧 Python 3.10 环境保留用于回滚。MaiBot 单独 checkout deploy/source-lock.json 中的提交，使用自己的 Python 3.12 环境与原始 uv.lock，通过 uv sync --frozen 安装。上游源码和 lock 不得本地修改。JMbot 使用自有 ingress 与原生 PlatformIO driver，不使用官方 QQ connector。

复制 deploy/split.env.example 至 /etc/jmbot-split.env，权限 600。填写唯一机器人 QQ、随机服务 token（至少 32 字符）、数据绝对路径、NapCat 媒体根目录。三个服务须共用 JM_BOT_RUNTIME_LOCK_DIR；该目录的 qq-sender.lock 阻止新版 legacy/split 同时发送。旧版程序不认识此锁，仍必须先停止旧进程。后台与下载服务不直接打开聊天数据库。

将 deploy/model-routes.example.json 复制至指定配置路径，填写真实学校网关和模型名；embedding 维度必须与模型一致。各官方连接分别添加 ds_official、glm_official、kimi_official、qwen_official，不能将学校 Key 当官方 Key。任务不跨服务商借用凭据。planner、replyer、utils 必需；长期记忆需要 memory 和 embedding，缺任一项则停用原生记忆插件。视觉使用 vlm，自动表情识别使用 emoji，未确认能力保持 disabled。图片 embedding 开启时须提供实际供应商协议的 image_embedding_input 或 image_embedding_body 模板。语音传输尚不可用，校验拒绝启用 voice。

每个 QQ 账号/群/私聊使用独立原生进程和完整数据根目录，隔离上游全局 profile、缓存、向量、表情和后台任务。这会占用较多内存，小内存主机先保持实例上限 2。等待队列有界，优先在原生空闲边界轮换；连续忙碌超过等待阈值后终止旧轮次让其他 scope 启动，旧 token 随即失效。具体容量与冷启动延迟由实际主机验收。

阅读上游 EULA.md 和 PRIVACY.md，再将 JM_BOT_MAIBOT_TERMS_ACCEPTED 设置为锁定 SHA。程序不替使用者自动同意条款。

## 一致备份和隔离迁移

先停止旧发送者、三个业务/后台服务及全部子进程，保证多个数据库与文件的一致时间点。快照包含 SQLite backup API 生成的 WAL 一致数据库、文件哈希、表行数、角色与表情。外置密钥通过 --master-key 配套备份。快照含秘密，不得提交 Git。

    python main_migrate.py backup --source /opt/jmbot/data --target /opt/jmbot-backups/before-split --master-key /etc/jmbot/chat-master.key
    python main_migrate.py verify --source /opt/jmbot-backups/before-split
    python main_migrate.py dry-run --source /opt/jmbot-backups/before-split --target /opt/jmbot-split-stage
    python main_migrate.py restore --source /opt/jmbot-backups/before-split --target /opt/jmbot-restore-drill
    python main_migrate.py migrate --source /opt/jmbot-backups/before-split --target /opt/jmbot-split-stage

目标必须是新目录；migrate 同一快照可续跑并返回回执。dry-run 返回凭据 scope/provider、权限、角色哈希、表情计数/最大编号、旧表行数和保留文件，且验证匹配密钥可解密。目标有 data/ 与 secrets/，不是源码覆盖包。将服务数据路径指向迁移目标 data/，外置密钥指向同一快照 secrets/chat-master.key 或已核对一致的安全位置，不得启动时生成替代密钥。

迁移保留聊天凭据、角色、scoped 状态，将下载策略独立到 download-policy.sqlite3。未知旧表和旧历史原样归档，不批量向模型发总结，不合并群/私聊。无明确授权来源的旧 scope 保持关闭，在后台逐一核对。原生新档案存于 chat-service/instances/qq_账号_kind_owner/。原有 conversation_memories 留作旧档案；后台原生记忆通过所属实例 mailbox 操作事实账本。离线实例明确报错，先用已许可消息启动。

## 唯一入口切换

保留 NapCat 现有单一反向 WebSocket 接入下载进程 8080。SplitAdapter 在 OneBot 事件构造、普通日志、媒体解码前路由：/jm 仅下载，/jmg 丢弃，Key 命令仅凭据协调器，普通聊天仅原生 MaiBot。不要额外启动 connector 或第二条聊天 WS。下载进程同时承载共享 NapCat 传输，停止它会中断聊天 QQ 收发；业务数据库、模型与下载队列分别所有。后台离线不停止业务，但管理/审批待恢复。

使用 .venv-admin/bin/python main_admin.py --set-password 3065532050 初始化密码。复制三个 deploy/jmbot-*.service.example 为实际 systemd unit，核对路径与环境后由使用者启动。后台只监听 127.0.0.1:8081，以 ssh -L 8081:127.0.0.1:8081 访问。回环 HTTP cookie 使用 SameSite strict、session HttpOnly 与绑定 CSRF，不暴露公网。

NapCat WebUI URL/token 仅配置本机回环地址；后台显示登录阶段、读取/刷新二维码并本地生成图像。启用 2FA 时通过 NapCat 原生 WebUI 认证。关闭 NapCat 自动好友/群邀请通过开关；审批与业务授权为两个步骤。NapCat 调用与回执间发生故障时保留“结果未确认”，不自动重复审批。核对 NapCat 后通过后台恢复操作确认已应用或允许重试。

监控页显示心跳、全局开关和策略 desired/applied；未连接不会声称已应用，待办持久保存并在恢复后重试。版本冲突需重读重提。下载页独立管理授权、黑名单、队列与帮助版本。聊天页管理 Key、模型任务、人设成长、原生事实记忆与人工表情。人工 ZIP 先预览，可用明确视觉模型生成待审草案，再确认。相同图片可在不同 scope 有不同授权和语义，人工库不参与自动淘汰；自动库完全位于来源 scope 原生根目录。

## 验收与回滚

使用者部署后验收：同一用户私聊/群 A/群 B 隔离；@、引用、戳一戳、图片/GIF；群 A 高流量时 B/私聊/下载；实际 Key/模型能力；审批；撤权时排队、模型、发送与学习写入；服务重启与恢复。本地初始化不等于真实 embedding 或 QQ 端到端通过。P10 生产观察和最终删除旧代码须在实际切换及回滚窗结束后进行；split 模式已不执行旧聊天判定器。

回滚前停止 split 服务及全部子进程，先用 preserve-rollback 对切换后的 data 与 master key 做新快照，再恢复旧配置和唯一发送者。不能用旧快照覆盖新增数据。原生新档案保全于回滚快照，旧引擎不能直接读取其向量；再次切回使用保全 split 数据。快照、回执、环境与源码 SHA 一并留档。
