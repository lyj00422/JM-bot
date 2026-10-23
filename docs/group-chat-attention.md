# 群聊注意力与超级表情

直接 @、引用机器人、提及配置的机器人名字会唤醒并引用对应消息。成功互动后保持默认 180 秒接话窗口；窗口内由模型判断是否相关，普通接话不引用。接话默认最短间隔 3 秒，不占随机主动互动限额。仅仅出现问号不再立即触发请求。

后台任务默认随机每 120–300 秒检查有新消息的群，只取最近 180 秒内至多 12 条记录。记录来自 OneBot 收到的消息，不读取 QQ 客户端页面，也不补抓离线聊天。没有新消息不重复调用模型；可以发言、回应表情、戳当前发言者或沉默。每 10 分钟最多 3 次随机主动互动，可通过环境变量调整。

后台模型请求不持有群聊锁，直接唤醒可以继续处理。请求完成后重新校验消息版本、Key 和聊天开关，丢弃被新消息覆盖的普通候选。直接唤醒明确绑定触发消息 ID；模型请求本身的网络延迟仍然存在。

消息新增发送者 QQ、平台消息 ID 和 user/assistant 角色，旧数据库启动时增量升级。只有发送成功的群回复写入历史；互动成功才记录去重。群外或上下文外的消息不能作为表情回应目标，戳人仅限本轮发言者。

## 参考与协议核实

- [MaiBot 注意力风格](https://github.com/MaiM-with-u/MaiBot/blob/main/src/maisaka/attention_drift.py)：近期消息中的明确触发点、简短反应、围绕当前话题。采用这些原则，没有引入其完整对话架构。
- [NapCat 消息转换](https://github.com/NapNeko/NapCatQQ/blob/main/packages/napcat-onebot/api/msg.ts)：face 段按表情 ID 查询配置；存在 AniStickerType 时使用动画类型，并填充 stickerId、packId。无需手工拼资源。
- [NapCat 原生表情配置](https://github.com/NapNeko/NapCatQQ/blob/main/packages/napcat-core/external/face_config.json)：白名单选取汪汪、噗噗星人和企鹅，排除 QQ 黄脸包。
- [NapCat 消息类型](https://github.com/NapNeko/NapCatQQ/blob/main/packages/napcat-onebot/types/message.ts)：原生发送使用 face 段和字符串 id；保留模型输出格式 `[qq_face:编号]`。
- [NapCat 消息表情回应](https://github.com/NapNeko/NapCatQQ/blob/main/packages/napcat-onebot/action/msg/SetMsgEmojiLike.ts)：调用 set_msg_emoji_like，参数 message_id、emoji_id、set。

发送超级表情和消息底部的表情回应是不同能力。底部回应的呈现与可用编号取决于 QQ 客户端及 NapCat 版本，不保证动画展示；扩展 API 失败记录日志，不改发黄脸。没有进行真实 QQ 账号的在线发送验收。

## 验证范围

自动测试覆盖唤醒引用、普通接话不引用、无关消息沉默、窗口过期、成员互相 @、旧库迁移、消息角色、超级表情过滤与序列化、互动目标校验和去重、失败不记成功、观察新消息检查、开关、过期结果丢弃、后台观察不阻塞唤醒及任务断线清理。模型对话题相关性的准确率需要结合实际群聊继续观察。
