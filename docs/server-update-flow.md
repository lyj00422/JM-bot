# JMbot 服务器更新流程

这份文档是以后所有聊天里默认使用的 JMbot 更新流程。优先使用“本地上传 release 压缩包”的方式，不让服务器直接从 GitHub 下载，避免网络慢、卡住或下载到旧包。

## 默认选择

- 通用服务器：使用 `JMbot-vX.Y.Z.tar.gz`。
- 阿里云 2C2G 服务器：使用 `JMbot-aliyun2c2g-vX.Y.Z.tar.gz`。
- 当前服务器 `/opt/jmbot` 保留 `data/` 运行数据，更新时只覆盖程序文件。
- NapCat 如果通过 Docker 挂载 `/opt/jmbot:/opt/jmbot`，更新后需要重启 NapCat 容器，必要时重建容器挂载。

## 发布新版时的本地检查

在本地仓库确认版本和修复代码已经存在：

```powershell
git status --short --branch
Select-String -Path pyproject.toml -Pattern 'version'
.\.venv\Scripts\python -m pytest -q
```

生成或确认 release 压缩包存在：

```powershell
Get-ChildItem D:\project-of-python\JM\JMbot\dist\*vX.Y.Z*.tar.gz
```

## 本地上传到服务器

在 Windows PowerShell 执行，阿里云 2C2G 默认用优化包：

```powershell
scp D:\project-of-python\JM\JMbot\dist\JMbot-aliyun2c2g-vX.Y.Z.tar.gz root@服务器IP:/tmp/
```

如果是通用版：

```powershell
scp D:\project-of-python\JM\JMbot\dist\JMbot-vX.Y.Z.tar.gz root@服务器IP:/tmp/
```

## 服务器解压更新

阿里云 2C2G 版示例，把 `X.Y.Z` 换成实际版本：

```bash
cd /opt
sudo systemctl stop jmbot

sudo cp -a /opt/jmbot/data /tmp/jmbot-data-backup-$(date +%F-%H%M%S) 2>/dev/null || true

sudo mkdir -p /opt/jmbot
sudo tar -xzf /tmp/JMbot-aliyun2c2g-vX.Y.Z.tar.gz -C /opt/jmbot --strip-components=1

cd /opt/jmbot

grep 'version' pyproject.toml
```

通用版只需要把包名换成：

```bash
sudo tar -xzf /tmp/JMbot-vX.Y.Z.tar.gz -C /opt/jmbot --strip-components=1
```

## 安装依赖和中文字体

每次更新后执行：

```bash
cd /opt/jmbot
python3 -m venv .venv
./.venv/bin/pip install -r requirements.txt
sudo apt update
sudo apt install -y fonts-noto-cjk
```

`fonts-noto-cjk` 用于 `/jm 作者` 图片卡片里的中文渲染。如果没装，卡片里可能出现方框。

## 启动和看日志

```bash
sudo systemctl daemon-reload
sudo systemctl start jmbot
sudo journalctl -u jmbot -f
```

看到类似下面内容表示 Bot 服务启动并等待 NapCat 连接：

```text
Uvicorn running on http://0.0.0.0:8080
OneBot V11 | Bot <QQ号> connected
```

## 更新后验证

在服务器执行：

```bash
cd /opt/jmbot
grep 'version' pyproject.toml
sudo systemctl status jmbot --no-pager
```

针对具体修复再 grep 对应代码。例如作者搜索卡片分页修复：

```bash
grep -R "_query_author_search_card_page" -n src/jmbot/core/command_service.py
```

如果版本还是旧的，说明压缩包没有覆盖成功或上传的是旧包。

## NapCat Docker 检查

如果上传文件时报 `ENOENT` 或 NapCat 看不到新生成的文件，检查挂载：

```bash
ls -lh /opt/jmbot/data/output/
docker exec napcat ls -lh /opt/jmbot/data/output/
```

如果宿主机有文件但容器里没有，重启 NapCat：

```bash
docker restart napcat
```

仍不一致时，重建 NapCat 容器并确保挂载包含：

```text
/opt/jmbot:/opt/jmbot
```

## 以后问 Codex 时的提示词

可以直接说：

```text
按 docs/server-update-flow.md 的流程，给我这次 JMbot 更新和服务器部署步骤。优先本地上传 release 压缩包，不要让服务器直接从 GitHub 下载。
```
