# QQ JMComic Bot

基于 NoneBot2 + OneBot v11 的 QQ 机器人。用户在 QQ 群或机器人好友私聊里发送 `/jm 123456`，机器人会下载对应 JMComic、生成 PDF，并通过 OneBot 文件接口上传回 QQ。

## 分支说明

- `main`：通用 release 版，适合本地开发、普通服务器部署，也作为功能开发的基线。
- `release/aliyun-2c2g`：阿里云 2 vCPU / 2 GiB / 40 GiB ESSD 优化版，默认降低下载线程和并发，适合小配置服务器直接拉取部署。

GitHub Release 规则：

- 每个正式版本号同时发布两个压缩包。
- `JMbot-vX.Y.Z.tar.gz` 是通用版，对应 `main` 分支。
- `JMbot-aliyun2c2g-vX.Y.Z.tar.gz` 是阿里云 2C2G 优化版，对应 `release/aliyun-2c2g` 分支。
- 如果服务器能访问 GitHub，优先使用对应分支 `git pull` 更新。
- 如果服务器不能访问 GitHub，下载对应 release 压缩包后上传到服务器部署。

服务器要部署低配优化版时使用：

```bash
git fetch origin
git switch release/aliyun-2c2g
git pull origin release/aliyun-2c2g
```

## 功能

- 支持 `/jm <数字ID>` 命令。
- 支持 `/jm 撤回 <数字ID>` 撤回自己还在排队中的任务。
- 支持 `/jm 队列` 查看当前待处理和正在处理的任务。
- 支持超级管理员命令：`/jmg 清空`、`/jmg kill <数字ID>`、`/jmg kill 全部 <数字ID>`、`/jmg 插队 <数字ID>`。
- 支持 QQ 群和机器人好友私聊。
- 每个漫画 ID 使用独立下载目录：`data/downloads/<id>`。
- 生成 PDF 到：`data/output/JM<id>.pdf`。
- 使用 SQLite 持久化漫画处理队列，收到请求后会提示“前面还有 xx 篇漫画正在处理”。
- 同一个群或同一个私聊用户里，同一个漫画 ID 不能重复排队；不同群、群和私聊之间可以分别排队。
- 如果处理前发现 `data/output/JM<id>.pdf` 已存在，会直接上传这个 PDF，上传成功后按清理配置删除。
- 重启后会优先恢复队列中未完成的漫画请求，删除上次未完成的图片下载目录，并重新开始处理。
- 队列会记录请求来自 QQ 群还是机器人好友私聊，处理完成后发送回原位置。
- 上传成功后可自动清理图片目录和 PDF，减少磁盘占用。

## QQ 命令

普通用户命令：

```text
/jm 123456
/jm 详情 123456
/jm 搜索 关键词
/jm 搜索 关键词 2
/jm 作者 作者名
/jm 作者 作者名 2
/jm 作者 文本 作者名
/jm 作者 文本 作者名 2
/jm 排行
/jm 排行 2
/jm 排行 周榜
/jm 排行 韩漫 周榜 2
/jm 排行 全彩 韩漫 月榜
/jm 排行 文本 全彩 韩漫 月榜 2
/jm 排行 类别
/jm 收藏 123456
/jm 取消收藏 123456
/jm 我的收藏
/jm 封面 123456
/jm 预览 123456
/jm 123456 pdf
/jm 123456 zip
/jm 123456 章节 1,3-5
/jm 123456 章节 1,3-5 zip
/jm 进度
/jm 进度 123456
/jm 撤回 123456
/jm 队列
/jm 我的
/jm 历史
/jm 历史 5
/jm 历史 失败
/jm 历史 失败 5
/jm 帮助
/jm 帮助 查找
/jm 帮助 排行
/jm 帮助 下载
/jm 帮助 收藏
/jm 帮助 任务
```

`/jm help` 是 `/jm 帮助` 的英文别名。
`/jm my` 是 `/jm 我的` 的英文别名，`/jm history` 是 `/jm 历史` 的英文别名。
`/jm 看 <id>` 和 `/jm info <id>` 是详情命令别名。`/jm cover <id>` 和 `/jm preview <id>` 是封面、预览的英文别名。
`/jm search <关键词>` 是 `/jm 搜索 <关键词>` 的英文别名。

`/jm 排行 [文本] [关键词和/或类别] [榜单] [页码]` 查询站点浏览量排行；榜单支持日榜、周榜、月榜、总榜，默认总榜。未指定类别时查询全部类型，关键词和类别可以交换顺序；单独一个数字参数表示页码。纯数字关键词不适用于排行榜命令。

支持的主分类为：全部、同人、单本、短篇、其他、韩漫、美漫、Cosplay、3D、英文站。发送 `/jm 排行 类别` 可以查看分类列表和示例。排行榜每页固定显示 5 本漫画，默认发送图片卡片；加入 `文本` 后返回相同分页的文字列表。

`/jm 帮助` 和 `/jm help` 返回简短主题索引；详细帮助使用 `/jm 帮助 查找`、`/jm 帮助 排行`、`/jm 帮助 下载`、`/jm 帮助 收藏` 或 `/jm 帮助 任务`。

管理员命令需要发送者 QQ 在 `JM_BOT_SUPERUSERS` 中：

```text
/jmg 清空
/jmg kill 123456
/jmg kill 全部 123456
/jmg 插队 123456
/jmg 失败
/jmg 失败 最近 5
/jmg 失败 21
/jmg 失败 删除 21
/jmg 帮助
```

说明：

- `/jm 撤回 <id>` 只能撤回自己在当前群或当前私聊里提交、且仍处于排队中的任务；已经开始处理的任务需要管理员 `kill`。
- 详情查询在独立子进程中执行，查询超时会被强制终止，不会长期占住 Bot 事件循环。
- `/jm 搜索 <关键词> [页码]` 使用站内搜索，返回可下载的 JM ID 列表；`/jm 作者 <作者名> [页码]` 默认发送作者作品图片卡片。
- `/jm 作者 文本 <作者名> [页码]` 使用快速文本列表，不下载封面、不生成图片卡片。
- `/jm 排行` 无关键词时使用官方浏览量排行；带关键词时使用站内综合搜索并按选定榜单的浏览量排序。查询超时不会进入下载队列。
- 排行榜卡片会显示当前筛选条件、全局排名和总数，并在卡片后给出可复制的下载命令；封面或卡片生成失败时自动降级为文字结果。
- 搜索结果只展示 ID、标题和有效标签，不展示容易缺失的页数和更新时间；结果底部会提示下一页命令。
- `/jm 收藏 <id>`、`/jm 取消收藏 <id>`、`/jm 我的收藏` 管理自己的收藏；收藏按 QQ 用户全局保存，不分群。
- `/jm 封面 <id>` 直接在当前群或私聊发送封面图片，不上传到群文件。
- `/jm 详情 <id>` 返回详情文字后会自动附带封面；封面失败不影响详情文字。
- `/jm 预览 <id>` 下载第一章正文前 10 页，不包含封面，生成 `JM<id>（预览）.pdf`。
- 群聊预览上传到现有 `JM` 群文件夹；私聊预览作为私聊文件发送。成功后清理临时图片和预览 PDF。
- `/jm <id>` 和 `/jm <id> pdf` 下载 PDF；`/jm <id> zip` 下载保留章节目录的 ZIP。
- `/jm <id> 章节 1,3-5 [pdf|zip]` 按详情中展示的章节编号下载，部分章节文件名会带 `（章节1,3-5）`。
- 同一漫画的不同格式或章节组合可分别排队；完全相同的组合才判定为重复。
- `/jm 进度 <id>` 手动查询当前任务；`/jm 进度` 会自动匹配自己在当前群或当前私聊里最近提交的排队中、处理中任务。通用版默认显示较精确的页数进度；阿里云版默认只显示阶段与耗时，不主动发送进度消息。
- `/jm 我的` 只显示自己在当前群或当前私聊里的排队中、处理中任务。
- `/jm 历史` 只显示自己在当前群或当前私聊里的已保留完成、失败、取消任务；`/jm 历史 5` 只看最近 5 条。
- `/jm 历史 失败` 只显示自己的已保留失败任务；`/jm 历史 失败 5` 只看最近 5 条。
- `/jmg 清空` 只清空待排队任务，不影响正在处理的任务。
- `/jmg kill <id>` 只取消当前群或当前私聊里的对应任务。
- `/jmg kill 全部 <id>` 会取消所有群和私聊里的对应任务。
- `/jmg 插队 <id>` 会把当前群或当前私聊里的任务放到队首；如果还没有这个任务，会先创建再插队。
- `/jmg 失败` 会查看已保留的全局失败任务；`/jmg 失败 最近 5` 只看最近 5 条；`/jmg 失败 <任务ID>` 查看详细失败原因；`/jmg 失败 删除 <任务ID>` 手动删除一条失败记录。
- `/jmg 帮助` 和 `/jmg help` 查看管理员命令；管理员帮助不会出现在普通帮助详情中。
- 下载失败时，ID 不存在或仅登录可见等用户可处理原因会直接显示给用户且不保留失败记录；其他技术原因只返回任务 ID，详细失败原因保留给管理员后续排查。
- 详情、封面、预览和搜索等需要等待的命令会先回复“收到，正在处理…”。下载或预览成功后不再额外发送完成文字，文件消息本身就是结果；失败和取消仍会通知。
- 为兼容 `jmcomic` 新版本的部分下载失败标记，Bot 会在下载结束后自行校验最终图片数量：图片完整则继续生成文件，图片缺失才判定失败。

## 本地安装

建议使用 Python 3.10 或更新版本：

```powershell
python -m venv .venv
.\.venv\Scripts\activate
pip install -r requirements.txt
```

运行测试：

```powershell
.\.venv\Scripts\python.exe -m pytest
```

## 本地启动

最简单方式：

```powershell
.\scripts\start_bot.ps1
```

如需一键同时启动 NapCat 和 JMbot：

```powershell
copy .\scripts\local.env.example .\scripts\local.env
notepad .\scripts\local.env
.\scripts\start_all.ps1
```

在 `scripts/local.env` 里填写你的 QQ 群白名单、管理员 QQ、NapCat 路径等本机私有配置。该文件已被 `.gitignore` 忽略，不会提交到远端。

OneBot v11 反向 WebSocket 地址：

```text
ws://127.0.0.1:8080/onebot/v11/ws
```

## 常用环境变量

```text
JM_BOT_HOST=127.0.0.1
JM_BOT_PORT=8080
JM_BOT_COMMAND_PREFIX=/jm
JM_BOT_ADMIN_COMMAND_PREFIX=/jmg
JM_BOT_MAX_CONCURRENCY=1
JM_BOT_ALLOWED_GROUPS=123456789,987654321
JM_BOT_SUPERUSERS=123456789
JM_BOT_DOWNLOAD_ROOT=data/downloads
JM_BOT_OUTPUT_ROOT=data/output
JM_BOT_OPTION_ROOT=data/options
JM_BOT_QUEUE_DB=data/queue.sqlite3
JM_BOT_USER_DB=data/user.sqlite3
JM_BOT_CHAT_DB=data/chat.sqlite3
JM_BOT_CHAT_ROLE=data/chat_role.json
JM_BOT_CHAT_MASTER_KEY_PATH=/etc/jmbot/chat-master.key
JM_BOT_CLEAN_AFTER_UPLOAD=true
JM_BOT_GROUP_FOLDER=JM
```

资源相关配置：

```text
JM_BOT_DOWNLOAD_IMAGE_THREADS=10
JM_BOT_DOWNLOAD_PHOTO_THREADS=2
JM_BOT_JMCOMIC_LOG=true
JM_BOT_JMCOMIC_RETRY_TIMES=5
JM_BOT_JOB_TIMEOUT_SECONDS=1800
JM_BOT_AUTO_SPLIT_PDF=true
JM_BOT_SPLIT_IMAGE_COUNT=80
JM_BOT_QUERY_TIMEOUT_SECONDS=20
JM_BOT_QUERY_MAX_CONCURRENCY=1
JM_BOT_DETAIL_CACHE_TTL_SECONDS=300
JM_BOT_QUERY_CACHE_MAX_ENTRIES=128
JM_BOT_PREVIEW_ROOT=data/preview
JM_BOT_PREVIEW_PAGE_COUNT=10
JM_BOT_PREVIEW_TIMEOUT_SECONDS=180
JM_BOT_DEFAULT_FORMAT=pdf
JM_BOT_ALLOWED_FORMATS=pdf,zip
JM_BOT_PROGRESS_MODE=precise
JM_BOT_HISTORY_RETENTION_DAYS=7
JM_BOT_HISTORY_MAX_PER_USER=50
JM_BOT_CHAT_ENDPOINT=https://api.deepseek.com/chat/completions
JM_BOT_CHAT_MODEL=deepseek-flash
JM_BOT_CHAT_TIMEOUT_SECONDS=60
JM_BOT_GROUP_CHAT_HISTORY_LIMIT=12
JM_BOT_GROUP_CHAT_CONTEXT_SECONDS=600
JM_BOT_GROUP_CHAT_ACTIVE_SECONDS=180
JM_BOT_GROUP_CHAT_FOLLOWUP_COOLDOWN_SECONDS=3
JM_BOT_GROUP_CHAT_OBSERVE_ENABLED=true
JM_BOT_GROUP_CHAT_OBSERVE_MIN_SECONDS=120
JM_BOT_GROUP_CHAT_OBSERVE_MAX_SECONDS=300
JM_BOT_GROUP_CHAT_REPLY_COOLDOWN_SECONDS=45
JM_BOT_GROUP_CHAT_PROACTIVE_LIMIT=3
JM_BOT_CHAT_REPLY_MAX_LENGTH=500
JM_BOT_CHAT_ALIASES=大肥鱼,大肥鱼喵,吃白饭大肥鱼,JM大使
```

`JM_BOT_QUEUE_DB` 是持久化任务队列数据库路径。`JM_BOT_USER_DB` 保存用户收藏记录。`JM_BOT_HISTORY_RETENTION_DAYS` 控制已完成、失败、取消任务的保留天数，默认 7 天；`JM_BOT_HISTORY_MAX_PER_USER` 控制每个用户最多保留多少条历史记录，默认 50 条。`JM_BOT_DOWNLOAD_IMAGE_THREADS` 和 `JM_BOT_DOWNLOAD_PHOTO_THREADS` 控制单个任务内部的 jmcomic 下载线程数。`JM_BOT_JOB_TIMEOUT_SECONDS` 控制单个漫画任务的最长运行时间，超时后会终止隔离子进程并把任务标记为失败。`JM_BOT_AUTO_SPLIT_PDF` 开启后，大本子会按 `JM_BOT_SPLIT_IMAGE_COUNT` 拆成多个 PDF 并顺序上传。详情、封面和预览同样使用隔离子进程；详情和封面使用查询超时，预览使用独立的 `JM_BOT_PREVIEW_TIMEOUT_SECONDS`。阿里云版保持单查询并发。

聊天功能与下载功能并行，不需要切换模式：`/jm` 用于下载、查询和下载任务管理，`/jmg` 只用于下载管理员操作，`/ds` 专用于聊天配置。`/ds 帮助` 按 `私聊｜群聊｜管理` 三个主题给出分层帮助，风格与 `/jm 帮助` 一致。私聊用户先发送 `/ds 你的 API Key`，然后直接发送普通文字；`/ds 状态` 查看私聊 Key 状态，`/ds 清除` 删除 Key，`/ds 清空` 清除私聊历史。管理员 QQ `3065532050` 通过私聊配置群聊 Key：`/ds 群聊key 群号 API_KEY`，也可以使用 `查看` 和 `清除`；使用 `/ds 停止|开启|状态` 管理聊天总开关。聊天关闭时下载功能继续运行。角色名为“大肥鱼”，群聊支持真实 QQ `@`、群名片和 `JM_BOT_CHAT_ALIASES` 中的别名。真实 `@` 一定回复；普通接话由短期注意力窗口和话题相关性共同决定，随机观察另有频率限制。支持文字、非黄脸超级表情、消息表情回应及戳一戳；禁止颜文字、Unicode emoji 和模型直接输出 CQ 码。当前模型配置为 `deepseek-flash`，并关闭 thinking。API Key 使用 `JM_BOT_CHAT_MASTER_KEY_PATH` 指向的 Fernet 主密钥加密后存储。

群聊诊断日志写入 systemd journal，不包含聊天正文或 API Key。出现无响应时，在服务器执行 `sudo journalctl -u jmbot -f`，然后重新 @ 机器人；重点查找 `收到群聊含 @`、`群聊跳过`、`群聊请求 DeepSeek`、`DeepSeek 请求失败` 或 `群聊互动完成`。日志包含 @ 段目标 QQ，可确认群昵称 @ 是否解析到了机器人账号。如果没有任何相关行，检查 `systemctl status jmbot`、OneBot 连接和群白名单 `JM_BOT_ALLOWED_GROUPS`。

群聊上下文由 `JM_BOT_GROUP_CHAT_HISTORY_LIMIT` 和 `JM_BOT_GROUP_CHAT_CONTEXT_SECONDS` 限制，默认最多 12 条且只取最近 600 秒。真实 @、引用机器人或提及机器人名字会唤醒并引用触发消息回复；成功互动后进入默认 180 秒的接话窗口，由模型判断新消息是否在接话或值得参与，后续不再自动引用。接话默认间隔至少 3 秒，合并期间被新消息覆盖的候选；它不占随机主动互动的次数。成员之间互相 @ 或引用时，机器人没有被直接叫到就不会抢答。

默认每隔随机 120–300 秒观察一次近期聊天，可选择简短发言、只回应消息表情、戳当前发言者或沉默。只检查本次连接已收到消息的群，只看最近 180 秒内的有限条记录，没有新消息就不重复判断；这是 OneBot 接收到的群消息，不是读取 QQ 客户端当前页面。观察遵守群白名单、聊天总开关、群 Key 与余额状态。`JM_BOT_GROUP_CHAT_REPLY_COOLDOWN_SECONDS` 和 `JM_BOT_GROUP_CHAT_PROACTIVE_LIMIT` 只约束随机主动互动（默认间隔至少 45 秒、10 分钟最多 3 次）；@ 唤醒不受这些限额影响。

禁止黄脸表情、颜文字、Unicode emoji 和直接输出 CQ 码。超级表情保留 `[qq_face:编号]` 格式，仅开放 `src/jmbot/core/chat_service.py` 中 `SUPER_FACES` 的非黄脸白名单，例如 `[qq_face:364]`（汪汪超级赞）、`[qq_face:375]`（超级鼓掌）、`[qq_face:401]`（企鹅超级转圈），一次最多一个。消息底部表情回应与发送超级表情是不同能力：前者调用 NapCat `set_msg_emoji_like`，后者发送原生 `face` 消息段，由 NapCat 根据 ID 自动选择动画。底部回应的显示样式取决于 QQ 客户端，不能保证以超级动画展示。回应只允许指向当前上下文中的本群成员消息，成功后去重；戳人只针对当前发言者。扩展 API 不支持或失败时记录错误，不回退成黄脸。

参考实现和已核实的协议见 [群聊注意力实现说明](docs/group-chat-attention.md)。这些处理减少旧话题与排队带来的错位，不改变上游模型本身的网络响应时间。

## 服务器部署

示例路径使用 `/opt/jmbot`：

```bash
cd /opt
git clone https://github.com/lyj00422/JM-bot.git jmbot
cd /opt/jmbot
python3 -m venv .venv
./.venv/bin/pip install -r requirements.txt

sudo cp deploy/env.example /etc/jmbot.env
sudo nano /etc/jmbot.env

sudo cp deploy/jmbot.service.example /etc/systemd/system/jmbot.service
sudo systemctl daemon-reload
sudo systemctl enable --now jmbot
sudo journalctl -u jmbot -f
```

如果 `/opt/jmbot` 是以前用压缩包解压出来的目录，它不是 git 仓库，执行 `git pull` 会报：

```text
fatal: not a git repository
```

这种情况下可以迁移成 git 部署，并保留运行数据：

```bash
sudo systemctl stop jmbot

cd /opt
sudo mv /opt/jmbot /opt/jmbot.bak-$(date +%F-%H%M%S)
sudo git clone -b release/aliyun-2c2g https://github.com/lyj00422/JM-bot.git /opt/jmbot
sudo cp -a /opt/jmbot.bak-*/data /opt/jmbot/data

cd /opt/jmbot
python3 -m venv .venv
./.venv/bin/pip install -r requirements.txt
sudo systemctl start jmbot
sudo journalctl -u jmbot -f
```

迁移后，`/opt/jmbot` 里会有 `.git`，以后就可以直接 `git pull` 更新。`data/` 是运行时目录，已在 `.gitignore` 中，不会被 `git pull` 覆盖。

如果 NapCat 运行在 Docker 里，并且容器挂载了 `/opt/jmbot:/opt/jmbot`，迁移或替换 `/opt/jmbot` 目录后必须重建或重新创建 NapCat 容器。否则 JMbot 会在新的 `/opt/jmbot/data/output` 生成 PDF，但 NapCat 容器仍可能看到旧目录，上传时会报：

```text
ENOENT: no such file or directory, open '/opt/jmbot/data/output/JMxxxxxx.pdf'
```

可以先检查宿主机和容器看到的文件是否一致：

```bash
ls -lh /opt/jmbot/data/output/
docker exec napcat ls -lh /opt/jmbot/data/output/
```

如果宿主机有文件、容器里没有，重建 NapCat 容器，并保持挂载 `/opt/jmbot:/opt/jmbot`。

### 服务器更新规则

以后更新服务器前先判断 `/opt/jmbot` 是哪种部署方式：

```bash
cd /opt/jmbot
test -d .git && echo "git 部署，可以 git pull 更新" || echo "非 git 部署，需要先迁移或使用压缩包"
```

推荐长期使用 git 部署：

- 通用服务器使用 `main` 分支。
- 阿里云 2C2G 小机器使用 `release/aliyun-2c2g` 分支。
- `/etc/jmbot.env` 是服务器本机配置，不在仓库里，`git pull` 不会覆盖它。
- `data/` 里的队列数据库、下载目录和输出目录是运行时数据，已被 `.gitignore` 忽略，正常 `git pull` 不会覆盖它们。
- 更新前先 `sudo systemctl stop jmbot`，更新后再 `sudo systemctl start jmbot`，最后用 `journalctl` 看日志。

每次更新后建议确认：

```bash
cd /opt/jmbot
git status
git branch --show-current
git rev-parse --short HEAD
grep '^JM_BOT_SUPERUSERS' /etc/jmbot.env
sudo systemctl status jmbot --no-pager
```

更新通用版：

```bash
cd /opt/jmbot
sudo systemctl stop jmbot
git fetch origin --tags
git switch main
git pull --ff-only origin main
./.venv/bin/pip install -r requirements.txt
sudo systemctl start jmbot
sudo journalctl -u jmbot -f
```

更新阿里云 2C2G 分支：

```bash
cd /opt/jmbot
sudo systemctl stop jmbot
git fetch origin --tags
git switch release/aliyun-2c2g
git pull --ff-only origin release/aliyun-2c2g
./.venv/bin/pip install -r requirements.txt
sudo systemctl start jmbot
sudo journalctl -u jmbot -f
```

如果从 Windows 复制环境变量文件到 Linux，看到 `^M`，说明文件是 CRLF 换行。建议清理一次：

```bash
sudo sed -i 's/\r$//' /etc/jmbot.env
cat -v /etc/jmbot.env
sudo systemctl restart jmbot
```

`cat -v` 不再显示 `^M` 就表示换行已经正常。

服务器不能访问 GitHub 时，可以在本地下载或打包 release 压缩包后上传：

```bash
# 本地机器
PACKAGE=JMbot-v0.3.6.tar.gz
# 阿里云 2C2G 优化版使用：
# PACKAGE=JMbot-aliyun2c2g-v0.3.6.tar.gz
scp "$PACKAGE" root@你的服务器IP:/tmp/

# 服务器
sudo systemctl stop jmbot
sudo mkdir -p /opt/jmbot
PACKAGE=JMbot-v0.3.6.tar.gz
# 阿里云 2C2G 优化版使用：
# PACKAGE=JMbot-aliyun2c2g-v0.3.6.tar.gz
sudo tar -xzf "/tmp/$PACKAGE" -C /opt/jmbot --strip-components=1
cd /opt/jmbot
python3 -m venv .venv
./.venv/bin/pip install -r requirements.txt
sudo apt update
sudo apt install -y fonts-noto-cjk
sudo cp deploy/env.example /etc/jmbot.env
sudo nano /etc/jmbot.env
sudo cp deploy/jmbot.service.example /etc/systemd/system/jmbot.service
sudo systemctl daemon-reload
sudo systemctl enable --now jmbot
sudo journalctl -u jmbot -f
```

## 阿里云 2C2G 优化版

在 2 vCPU / 2 GiB 的机器上，建议使用 `release/aliyun-2c2g` 分支：

```bash
cd /opt/jmbot
git fetch origin
git switch release/aliyun-2c2g
git pull origin release/aliyun-2c2g
./.venv/bin/pip install -r requirements.txt
sudo cp deploy/env.example /etc/jmbot.env
sudo nano /etc/jmbot.env
sudo systemctl restart jmbot
```

本分支的 `deploy/env.example` 已经使用低配推荐值：

```text
JM_BOT_MAX_CONCURRENCY=1
JM_BOT_DOWNLOAD_IMAGE_THREADS=2
JM_BOT_DOWNLOAD_PHOTO_THREADS=1
JM_BOT_JMCOMIC_LOG=false
JM_BOT_JMCOMIC_RETRY_TIMES=3
JM_BOT_JOB_TIMEOUT_SECONDS=1800
JM_BOT_AUTO_SPLIT_PDF=true
JM_BOT_SPLIT_IMAGE_COUNT=80
JM_BOT_QUERY_TIMEOUT_SECONDS=20
JM_BOT_QUERY_MAX_CONCURRENCY=1
JM_BOT_DETAIL_CACHE_TTL_SECONDS=300
JM_BOT_QUERY_CACHE_MAX_ENTRIES=128
JM_BOT_PREVIEW_ROOT=data/preview
JM_BOT_PREVIEW_PAGE_COUNT=10
JM_BOT_PREVIEW_TIMEOUT_SECONDS=180
JM_BOT_DEFAULT_FORMAT=pdf
JM_BOT_ALLOWED_FORMATS=pdf,zip
JM_BOT_PROGRESS_MODE=stage
JM_BOT_HISTORY_RETENTION_DAYS=7
JM_BOT_HISTORY_MAX_PER_USER=50
JM_BOT_CLEAN_AFTER_UPLOAD=true
```

这些设置会牺牲一些下载和查询速度，换取更低的 CPU、内存和磁盘压力。详情默认只允许一个外部查询并发，20 秒后强制终止异常查询，并用短时内存缓存减少重复请求。PDF 生成会优先使用 `img2pdf`；图片过多时生成多个分卷 PDF 并依次上传。如果转换失败，会回退到 Pillow。`deploy/jmbot.service.example` 也包含 `MemoryMax=1536M`、`MemoryHigh=1280M` 和 `CPUQuota=180%`，用于防止单个服务把 2 GiB 小机器拖垮。

### NapCat 报输出文件不存在

如果管理员失败列表提示“NapCat 无法访问输出文件”，先确认宿主机和容器看到的是同一个目录：

```bash
touch /opt/jmbot/data/output/mount-test.txt
ls -l /opt/jmbot/data/output/mount-test.txt
docker exec napcat ls -l /opt/jmbot/data/output/mount-test.txt
rm -f /opt/jmbot/data/output/mount-test.txt
```

宿主机存在但容器内不存在，说明替换 `/opt/jmbot` 后 NapCat 仍绑定旧目录，需要按部署章节的 Docker 参数重建 NapCat，并保留 `-v /opt/jmbot:/opt/jmbot`。

## 运行时文件

以下内容是运行时产物，不提交到 git：

- `.env`
- `scripts/local.env`
- `logs/`
- `data/downloads/`
- `data/options/`
- `data/output/`
- `data/*.sqlite3`

`data/options/` 中的 jmcomic 配置会按漫画 ID 自动生成，不需要手动维护。
`data/queue.sqlite3` 保存未完成队列；如果你想清空队列，请先停止服务后再删除该文件。
`data/user.sqlite3` 保存收藏记录；删除它会清空这些用户数据。

## 注意事项

- `jmcomic` 访问可能受网络、地区、代理和目标站点状态影响。
- PDF 过大时，QQ 上传是否成功取决于 OneBot 实现和 QQ 风控。
- 请确认机器人账号有目标群的文件上传权限。
