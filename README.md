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
- 支持重复任务拦截和任务并发限制。
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

`JM_BOT_MAX_CONCURRENCY` 控制机器人同时处理几个 JM 任务。`JM_BOT_DOWNLOAD_IMAGE_THREADS` 和 `JM_BOT_DOWNLOAD_PHOTO_THREADS` 控制单个任务内部的 jmcomic 下载线程数。小内存服务器优先降低这些值。

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

低配推荐值：

```text
JM_BOT_MAX_CONCURRENCY=1
JM_BOT_DOWNLOAD_IMAGE_THREADS=2
JM_BOT_DOWNLOAD_PHOTO_THREADS=1
JM_BOT_JMCOMIC_LOG=false
JM_BOT_JMCOMIC_RETRY_TIMES=3
JM_BOT_CLEAN_AFTER_UPLOAD=true
```

这些设置会牺牲一些下载速度，换取更低的 CPU、内存和磁盘压力。

## 运行时文件

以下内容是运行时产物，不提交到 git：

- `.env`
- `scripts/local.env`
- `logs/`
- `data/downloads/`
- `data/options/`
- `data/output/`

`data/options/` 中的 jmcomic 配置会按漫画 ID 自动生成，不需要手动维护。

## 注意事项

- `jmcomic` 访问可能受网络、地区、代理和目标站点状态影响。
- PDF 过大时，QQ 上传是否成功取决于 OneBot 实现和 QQ 风控。
- 请确认机器人账号有目标群的文件上传权限。
