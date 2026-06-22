# QQ JMComic Bot

基于 NoneBot2 + OneBot v11 的 QQ 机器人。用户在 QQ 群或机器人好友私聊里发送 `/jm 123456`，机器人会下载对应 JMComic、生成 PDF，并通过 OneBot 文件接口上传回 QQ。

## 分支说明

- `main`：通用 release 版，适合本地开发、普通服务器部署，也作为功能开发的基线。
- `release/aliyun-2c2g`：阿里云 2 vCPU / 2 GiB / 40 GiB ESSD 优化版，默认降低下载线程和并发，适合小配置服务器直接拉取部署。

服务器要部署低配优化版时使用：

```bash
git fetch origin
git switch release/aliyun-2c2g
git pull origin release/aliyun-2c2g
```

## 功能

- 支持 `/jm <数字ID>` 命令。
- 支持 QQ 群和机器人好友私聊。
- 每个漫画 ID 使用独立下载目录：`data/downloads/<id>`。
- 生成 PDF 到：`data/output/JM<id>.pdf`。
- 使用 SQLite 持久化漫画处理队列，收到请求后会提示“前面还有 xx 篇漫画正在处理”。
- 重启后会优先恢复队列中未完成的漫画请求，并按收到时间一个个处理和发送。
- 队列会记录请求来自 QQ 群还是机器人好友私聊，处理完成后发送回原位置。
- 上传成功后可自动清理图片目录和 PDF，减少磁盘占用。

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
JM_BOT_MAX_CONCURRENCY=1
JM_BOT_ALLOWED_GROUPS=123456789,987654321
JM_BOT_SUPERUSERS=123456789
JM_BOT_DOWNLOAD_ROOT=data/downloads
JM_BOT_OUTPUT_ROOT=data/output
JM_BOT_OPTION_ROOT=data/options
JM_BOT_QUEUE_DB=data/queue.sqlite3
JM_BOT_CLEAN_AFTER_UPLOAD=true
JM_BOT_GROUP_FOLDER=JM
```

资源相关配置：

```text
JM_BOT_DOWNLOAD_IMAGE_THREADS=10
JM_BOT_DOWNLOAD_PHOTO_THREADS=2
JM_BOT_JMCOMIC_LOG=true
JM_BOT_JMCOMIC_RETRY_TIMES=5
```

`JM_BOT_QUEUE_DB` 是持久化任务队列数据库路径。`JM_BOT_DOWNLOAD_IMAGE_THREADS` 和 `JM_BOT_DOWNLOAD_PHOTO_THREADS` 控制单个任务内部的 jmcomic 下载线程数。小内存服务器优先降低这些值。

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

更新通用版：

```bash
cd /opt/jmbot
git switch main
git pull origin main
./.venv/bin/pip install -r requirements.txt
sudo systemctl restart jmbot
```

服务器不能访问 GitHub 时，可以在本地下载或打包 release 压缩包后上传：

```bash
# 本地机器
scp JMbot-aliyun2c2g-v0.2.1.tar.gz root@你的服务器IP:/tmp/

# 服务器
sudo systemctl stop jmbot
sudo mkdir -p /opt/jmbot
sudo tar -xzf /tmp/JMbot-aliyun2c2g-v0.2.1.tar.gz -C /opt/jmbot --strip-components=1
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
JM_BOT_CLEAN_AFTER_UPLOAD=true
```

这些设置会牺牲一些下载速度，换取更低的 CPU、内存和磁盘压力。`deploy/jmbot.service.example` 也包含 `MemoryMax=1536M`、`MemoryHigh=1280M` 和 `CPUQuota=180%`，用于防止单个服务把 2 GiB 小机器拖垮。

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

## 注意事项

- `jmcomic` 访问可能受网络、地区、代理和目标站点状态影响。
- PDF 过大时，QQ 上传是否成功取决于 OneBot 实现和 QQ 风控。
- 请确认机器人账号有目标群的文件上传权限。
