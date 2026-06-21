# QQ JMComic Bot

通过 QQ 群命令 `/jm 123456` 下载 JMComic 本子，生成 PDF，并通过 OneBot v11 群文件接口上传回群。

## 已实现功能

- `/jm <数字ID>` 命令解析
- 每个漫画 ID 独立下载目录：`data/downloads/<id>`
- 图片递归收集并生成 `data/output/JM<id>.pdf`
- 异步任务队列和重复任务拦截
- NoneBot2 + OneBot v11 群文件上传入口
- QQ 群聊和机器人好友私聊都可使用 `/jm <数字ID>`

## 安装

建议使用 Python 3.10 或更新版本：

```powershell
python -m venv .venv
.\.venv\Scripts\activate
pip install -r requirements.txt
```

## 目录结构

- `src/jmbot/`：机器人应用代码和核心下载、PDF、上传逻辑。
- `tests/`：单元测试。
- `scripts/`：本地 Windows 启动脚本。
- `deploy/`：服务器环境变量和 systemd 服务模板。
- `data/options/`：jmcomic 下载配置模板。
- `docs/`：项目说明和历史实现计划。

## 启动 Bot

```powershell
python main.py
```

默认监听：

- Host: `127.0.0.1`
- Port: `8080`
- OneBot v11 反向 WebSocket 地址：`ws://127.0.0.1:8080/onebot/v11/ws`

## 可选环境变量

```powershell
$env:JM_BOT_HOST = "127.0.0.1"
$env:JM_BOT_PORT = "8080"
$env:JM_BOT_COMMAND_PREFIX = "/jm"
$env:JM_BOT_MAX_CONCURRENCY = "1"
$env:JM_BOT_ALLOWED_GROUPS = "123456789,987654321"
$env:JM_BOT_SUPERUSERS = "你的QQ号"
$env:JM_BOT_DOWNLOAD_ROOT = "data/downloads"
$env:JM_BOT_OUTPUT_ROOT = "data/output"
$env:JM_BOT_OPTION_ROOT = "data/options"
$env:JM_BOT_CLEAN_AFTER_UPLOAD = "true"
$env:JM_BOT_GROUP_FOLDER = "JM"
python main.py
```

`JM_BOT_ALLOWED_GROUPS` 为空时，所有群都可用。填了以后，只有列表里的群号可用。好友私聊不受群白名单限制，但必须是机器人 QQ 好友。

`JM_BOT_CLEAN_AFTER_UPLOAD=true` 时，PDF 成功上传到群后会删除本地图片目录和 PDF。上传失败时不会删除，方便排查。

`JM_BOT_GROUP_FOLDER=JM` 时，PDF 会上传到 QQ 群文件里的 `JM` 文件夹；如果群里没有该文件夹，机器人会先创建。

## 服务器部署模板

仓库里提供了两个部署模板：

- `deploy/env.example`：服务器环境变量模板，复制到 `/etc/jmbot.env` 后修改群号、管理员 QQ 等配置。
- `deploy/jmbot.service.example`：systemd 服务模板，复制到 `/etc/systemd/system/jmbot.service` 后按服务器路径调整。

示例：

```bash
sudo cp deploy/env.example /etc/jmbot.env
sudo nano /etc/jmbot.env

sudo cp deploy/jmbot.service.example /etc/systemd/system/jmbot.service
sudo systemctl daemon-reload
sudo systemctl enable --now jmbot
sudo journalctl -u jmbot -f
```

## QQ 群接入步骤

你自己配置 QQ 侧即可，推荐 NapCat：

1. 安装并启动 NapCat，登录机器人 QQ。
2. 在 NapCat 网络配置里启用 **OneBot v11 反向 WebSocket**。
3. 反向 WebSocket 地址填：

```text
ws://127.0.0.1:8080/onebot/v11/ws
```

4. 启动本项目：

```powershell
python main.py
```

5. 把机器人 QQ 拉进群，在群里发送：

```text
/jm 350234
```

也可以添加机器人 QQ 为好友后，直接私聊发送同样的命令：

```text
/jm 350234
```

## 注意

- `jmcomic` 访问可能受网络、地区、代理影响；失败信息会直接回群。
- 生成 PDF 可能较大，QQ 群文件上传是否成功取决于 OneBot 实现和 QQ 风控。
- 请确保机器人账号有在目标群上传文件的权限。
