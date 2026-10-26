# 在另一台服务器上用 iWAN 接入学校 API，供其他 Bot 调用

可以。只要学校账号允许该服务器使用 iWAN，且学校 API Key 对目标模型有调用权限，其他支持 OpenAI 兼容接口的 Bot 也可以通过这台服务器上的 iWAN HTTP 代理调用学校模型。Bot 不必是 JMbot。

链路如下：

```text
其他 Bot → http://127.0.0.1:18080（本机 iWAN HTTP 代理）
         → 已认证的学校 iWAN → https://api.llm.ustc.edu.cn/v1
```

API Key 与 iWAN 校园身份认证是两套凭据：前者用于学校模型网关，后者用于校园网接入。部署前请确认学校的 iWAN 使用范围、服务器登录/认证方式和模型使用政策。本文不提供或打包 iWAN 客户端；请从学校可信渠道取得适配服务器架构的客户端。本文中的地址和模型名是配置示例，实际使用以学校当前文档和 Key 可访问的模型为准。

## 1. 准备服务器

以下命令以 Ubuntu/Debian、systemd 和有 sudo 权限的 SSH 用户为例。让 Bot 和 iWAN 代理先运行在同一台 Linux 主机上，可直接使用回环地址，避免把代理开放到公网。

安装 Python 仅用于后面的 API 连通性测试；Bot 可使用自己的运行环境。

```bash
sudo apt update
sudo apt install -y python3
test -x /usr/local/bin/iwan-client-oidc
/usr/local/bin/iwan-client-oidc --help
```

如果 `test` 失败，先按学校提供的说明安装适用于该服务器架构的 `iwan-client-oidc`。继续前核对 `--help` 是否支持下文使用的参数；不同版本参数可能不同。不要从来源不明的地址下载认证客户端。

## 2. 首次连接并完成 iWAN 认证

在 SSH 终端前台运行客户端。配置目录保存认证状态，权限只给 root：

```bash
sudo install -d -o root -g root -m 700 /etc/sdwan/ustc-iwan
sudo /usr/local/bin/iwan-client-oidc \
  --connect --server 1 \
  --http --http-listen 127.0.0.1:18080 \
  --config-dir /etc/sdwan/ustc-iwan
```

按客户端提示完成学校身份认证。认证过程中如需浏览器，在自己的电脑上打开客户端给出的认证地址并完成操作。让这个前台进程继续运行，另开一个 SSH 终端完成第 3 节测试。认证通过且 API 测试成功后，回到前台终端按 'Ctrl+C'，再配置 systemd 常驻服务。

如果服务器上已经有 iWAN 服务，先检查并复用它，不要再启动第二个客户端：

```bash
sudo systemctl is-active ustc-iwan-http.service
sudo ss -ltnp 'sport = :18080'
sudo systemctl cat ustc-iwan-http.service
```

## 3. 测试学校 API 连通性

下面的测试通过 Python 标准库请求模型列表和聊天接口。Key 通过隐藏输入读取，不会出现在命令历史或进程参数中。若学校网关不提供 '/models'，根据学校提供的模型名跳过列表查询，直接测试聊天接口。

```bash
python3 - <<'PY'
import getpass
import json
import os
import urllib.error
import urllib.request

base_url = "https://api.llm.ustc.edu.cn/v1"
proxy_url = "http://127.0.0.1:18080"
model = input("模型名（例如 deepseek-flash-2）: ").strip()
key = getpass.getpass("学校 API Key（隐藏输入）: ").strip()
if not model or not key:
    raise SystemExit("模型名和 Key 都不能为空。")
os.environ.pop("NO_PROXY", None)
os.environ.pop("no_proxy", None)
opener = urllib.request.build_opener(
    urllib.request.ProxyHandler({"http": proxy_url, "https": proxy_url})
)

def call(path, payload=None):
    body = None if payload is None else json.dumps(payload).encode("utf-8")
    request = urllib.request.Request(
        base_url + path, data=body,
        headers={"Authorization": "Bearer " + key, "Content-Type": "application/json"},
    )
    try:
        with opener.open(request, timeout=60) as response:
            return json.load(response)
    except urllib.error.HTTPError as exc:
        raise SystemExit(f"{path} 返回 HTTP {exc.code}，请按文末排障表检查。")
    except (urllib.error.URLError, OSError):
        raise SystemExit("连接失败；检查 iWAN 认证、代理监听和网关连通性。")

try:
    models = call("/models")
    ids = [item.get("id") for item in models.get("data", [])]
    print("Key 可见模型：", ", ".join(str(item) for item in ids))
except SystemExit as exc:
    # 某些学校网关未实现模型列表；继续测试聊天接口。
    if "/models 返回 HTTP 404" not in str(exc):
        raise
    print("网关未提供 /models，继续直接测试指定模型。")

result = call("/chat/completions", {
    "model": model,
    "messages": [{"role": "user", "content": "请只回复：连接成功"}],
    "stream": False,
    "max_tokens": 64,
})
choices = result.get("choices") or []
content = choices[0].get("message", {}).get("content") if choices else None
if not isinstance(content, str) or not content.strip():
    raise SystemExit("请求成功但没有文本回复；检查模型协议和输出预算。")
print("聊天接口测试成功：", content)
PY
```

先确认这一步成功，再把同一组 Base URL、模型名和 Key 配给 Bot。

## 4. 设置 iWAN systemd 服务

先停止前台测试进程。将仓库中的 unit 示例安装到 systemd；如果没有在服务器上克隆本仓库，可创建同内容的 unit 文件：

```bash
sudo tee /etc/systemd/system/ustc-iwan-http.service >/dev/null <<'UNIT'
[Unit]
Description=USTC iWAN HTTP proxy for model API clients
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
User=root
ExecStart=/usr/local/bin/iwan-client-oidc --connect --server 1 --http --http-listen 127.0.0.1:18080 --config-dir /etc/sdwan/ustc-iwan
Restart=always
RestartSec=10

[Install]
WantedBy=multi-user.target
UNIT
sudo systemctl daemon-reload
sudo systemctl enable --now ustc-iwan-http.service
sudo systemctl status ustc-iwan-http.service --no-pager
sudo ss -ltnp 'sport = :18080'
```

预期监听地址是 `127.0.0.1:18080`。云防火墙和系统防火墙都不需要开放 18080。首次认证时使用的配置目录必须与 systemd 中的 `/etc/sdwan/ustc-iwan` 相同。若认证过期，按学校客户端说明重新认证；查看服务状态和日志：

```bash
sudo journalctl -u ustc-iwan-http.service -n 100 --no-pager
```

日志在分享前先检查并脱敏，避免泄露认证链接或令牌。

## 5. 配置其他 Bot

在 Bot 的服务环境中设置以下值。变量名需要按目标 Bot 的文档调整；不同软件可能使用 `OPENAI_BASE_URL`、`OPENAI_API_BASE` 或自己的配置页面。

```dotenv
OPENAI_BASE_URL=https://api.llm.ustc.edu.cn/v1
OPENAI_API_KEY=替换为学校APIKey
OPENAI_MODEL=替换为该Key有权限的模型名
HTTPS_PROXY=http://127.0.0.1:18080
HTTP_PROXY=http://127.0.0.1:18080
NO_PROXY=127.0.0.1,localhost
```

注意事项：

- Base URL 通常填到 `/v1`，不加 `/chat/completions`；多数 OpenAI 兼容 SDK 会自行拼接接口路径。以目标 Bot/SDK 的格式为准。
- 有些 HTTP 客户端不会读取 `HTTPS_PROXY` 环境变量，或要求显式启用环境代理（例如 `trust_env`）。若 API 测试脚本成功而 Bot 失败，查目标程序的代理配置和日志。
- 不要把学校 Key 写进公开仓库、镜像层、截图或启动命令参数。环境文件建议设为 `root` 所有且权限 `600`，只给 Bot 服务读取。
- Bot 若由 systemd 托管，把变量放到该服务的私有 `EnvironmentFile`，然后重启 Bot；在 SSH 里临时 `export` 不会修改已运行的服务环境。

例如，已知某个 Bot unit 使用 `/etc/mybot.env` 时：

```bash
sudo chown root:root /etc/mybot.env
sudo chmod 600 /etc/mybot.env
sudo systemctl restart mybot.service
sudo systemctl status mybot.service --no-pager
sudo journalctl -u mybot.service -n 100 --no-pager
```

如果 Bot 在 Docker 容器内，容器里的 `127.0.0.1` 是容器自身，不是宿主机；上面的代理地址不能直接使用。优先让 Bot 与代理在同一容器网络中安全互通，或使用受防火墙限制的宿主机网关地址，并让 iWAN 只绑定该私有接口。不要为了容器访问把代理监听改成 `0.0.0.0` 并暴露到公网。容器内还需确认该 Bot 的 HTTP 库确实使用配置的代理。

## 6. 常见错误

| 现象 | 检查项 |
|---|---|
| `127.0.0.1:18080` 拒绝连接 | systemd 服务状态、监听端口、Bot 是否在容器内 |
| 代理监听正常但 HTTPS 请求超时 | iWAN 登录状态、校园网通道、目标网关连通性 |
| HTTP 401 | Key 是否有效、是否使用学校 Key |
| HTTP 403 | Key 的模型权限或学校平台访问策略 |
| HTTP 404 | Base URL、接口路径和模型名是否正确；模型列表接口的 404 不一定表示聊天接口不可用 |
| HTTP 400 | 模型名、OpenAI 兼容协议字段或模型能力要求 |
| HTTP 402 / 429 | 额度、余额、频率限制 |
| Python 测试成功但 Bot 调用失败 | Bot 实际加载的配置、代理环境变量支持、容器网络、模型设置 |

学校可能调整 API 地址、模型清单、认证方式或 iWAN 客户端参数；遇到差异时，以学校当前文档为准。

