# 2026-10-26 表情包发送链路修复

## 已确认的根因

- 19:27:37 的自主发表情请求有 349 张可用素材，抽取 8 张后进入视觉选图。19:29:21 返回结果只有思考内容，最终正文为空，完成 token 恰好为 1024。
- 原生任务的输出预算是 4096，但 JMbot 网关把 sticker 工作负载强制压到 1024。选图解析失败又返回空候选，被下游统一误报为“当前表情包库中没有可用表情”。
- 原生新发送模式需要视觉 Planner；线上 Planner 为文字模型，因此不能只打开 use_new_send_logic。旧模式下普通 reply 缺少直接配表情的参数。
- 会话表情缓存刚建立时为空，原实现要等定期维护加载收藏。在隔离的原生环境中复现了“数据库有收藏但首次选图误报为空”。

## 实施

- 学校 API 的 emoji 任务独立绑定 glm-5.3-flash，显式声明 visual=true，并发为 1；学校地址及凭据继续由原有路由和凭据库管理。
- 首版仅对 sticker 工作负载使用 4096 的输出预算上限、JSON object 输出、temperature=0.2，GLM 使用 reasoning_effort=low。此临时参数限制已被下文 20:28 的配置透传修复替代。
- 选图结果严格校验最终 JSON 和编号；模型输出失败、没有匹配候选、素材为空分别报告，不再返回空对象掩盖解析失败，也不随机拿第一张兜底。
- 在现有原生 reply 工具上提供可选 with_emoji 布尔参数。文字发送成功后复用原生 send_emoji 及 GLM 选图，不切换聊天 Planner，不改人设，也不新增另一套 QQ 发送路径。
- 选图前在线程中重新加载当前会话的已注册收藏，继续执行来源归属过滤，不混用其他会话的自动收藏；全局人工素材按原有授权供各会话使用。
- 文字已送达而附图失败时保留文字成功状态。表情继续使用原有稳定请求 ID 与 QQ 回执，发送结果不明时不重发；若 QQ 已确认，则以后续统计异常为由也不能误报未发送。

## 验证

本地定向回归：

    .venv/Scripts/python.exe -m pytest tests/test_emoji_selection.py tests/test_native_delivery.py tests/test_native_gateway.py tests/test_native_ingress.py tests/test_native_content_refresh.py -q

结果为 34 passed。预算、选图解析和文字附图均先验证失败，再修复通过。另在服务器锁定版本的 MaiBot 隔离实例中验证了收藏缓存首次加载和真实工具目录的 with_emoji 声明，修复前失败、修复后通过。

线上验证：

- 原失败对话及候选图经实际学校 API 的已部署 emoji 网关重放，返回 HTTP 200、有效选图 JSON，完成 token 为 37，最终正文正常。
- 20:09:52 的自然聊天中，Planner 自主设置 with_emoji=true；原生选图、发送均成功，QQ confirmed=true，message_id=552227294。不是向群内人工注入的测试消息。
- 最终补丁于 20:14:20 重载。20:15:31 选图前成功加载 349 张候选，20:15:49 再次自然回复并成功附图，QQ confirmed=true、message_id=817385218；最终版本启动后至核验时没有新增 error/critical 日志。
- 部署后核对聊天、下载、管理三个服务状态，并检查实际运行源码与本地哈希一致。

## 部署及回滚

服务器备份目录：/opt/jmbot-updates/emoji-delivery-20261026/backup-20261026-200744。

本次只替换 src/jmbot/chat_app.py、src/jmbot/native_emoji.py 和学校路由中的 emoji 任务；人设与其他任务路由保持原状。后续收藏缓存修复前的首版文件另存为 native_emoji.before-collection-refresh.py。

需要回滚时，停止 jmbot-chat，恢复备份目录中的两个源码文件及 model-routes.json，再启动 jmbot-chat。下载与管理服务无需随此变更重启。

## 20:28 配置预算与人设控制的后续修复

用户明确要求不以 token 成本作为限制，预算及思考依据配置，是否发图和频率依照人设。

- 移除 sticker 网关的 4096 上限、temperature=0.2 和 reasoning_effort=low 强制覆盖，不再删除请求中配置的 thinking。保留选图需要的 JSON object 最终输出协议。
- 原生 worker 启动和会话模型绑定原先会重新构造 TaskConfig，丢失持久化任务参数；现改为深拷贝原生任务配置，只替换传输使用的 model_list，保留 max_tokens、temperature、hard_timeout 和 selection_strategy。
- 原生模型热重载先刷新基础配置，再一次性更新已存在会话，避免只更新基础模型或用旧模型重建会话。
- 生产 emoji 任务使用学校 glm-5.3-flash，max_tokens=8192、temperature=0.7，模型 extra_params.reasoning_effort=medium；从 emoji 的持久化配置移除不适配此端点的 thinking=disabled。后续预算与思考可继续通过原生模型配置调整。
- 人设和模型路由文件哈希保持不变；没有新增随机发图概率、冷却时间或硬编码文图比例。Planner 自主决定 with_emoji，选图模型只在需要发图时选择素材。

验证证据：

- 新增配置透传与热重载测试，修复前 6 failed / 17 passed，修复后定向回归 41 passed（仅现有 Starlette 弃用警告）。
- 使用服务器锁定版本的 MaiBot 真实配置类，在临时实例验证 worker 启动、会话绑定和模型热重载都保留配置，输出 NATIVE_STARTUP_SCOPE_AND_RELOAD_CONFIG_PASS；未改动上游代码。
- 20:28:15 重启 jmbot-chat 后，聊天、下载、管理服务均为 active。
- 原失败上下文及候选图经已部署网关和学校 API 重放：8192 / medium / 0.7，HTTP 200，glm-5.3-flash，finish_reason=stop，有效 emoji_index=5，completion_tokens=38。总耗时 59.73 秒（含排队）；API 没有返回 reasoning_content，不能据此断言实际内部思考长度。
- 20:29:35 自然聊天的 reply 自主设置 with_emoji=true，附图 success=true，QQ confirmed=true，message_id=586745756。此验证没有向群里注入测试消息。
- 核验时新版本运行日志无 error/critical；人设及路由哈希与部署前一致。

本轮备份及重放报告：/opt/jmbot-updates/emoji-config-20261026-202643。backup 下保存变更前 chat_app.py、native_sessions.py、native_worker.py、model_config.toml、bot_config.toml；glm-configured-replay.json 保存无凭据的模型验证结果。回滚本轮时停止 jmbot-chat，恢复三个源码文件及 model_config.toml，再启动 jmbot-chat；bot_config.toml 为核验备份，不需要恢复。
