# 在服务器上使用学校 API Key（中科大网关 / iWAN）

本文说明如何在校外 Linux 服务器上调用学校的大模型 API，以及如何接入 JMbot。服务器命令均在 SSH 登录后通过 Bash 执行。

整理日期：2026-10-25。网关地址、模型名来自本地部署配置；本文未重新连接服务器、验证学校当前模型清单或执行 API 请求。学校调整网关、模型权限或 iWAN 客户端后，以实际说明和测试结果为准。

## 1. 需要哪些配置

学校 Key、学校 API 地址和校园网接入是三件事。学校 Key 需要发给学校网关；如果服务器不能直接访问该网关，还需要通过已认证的 iWAN 接入。

```text
服务器上的程序
  → 本机 HTTP 代理 http://127.0.0.1:18080
  → 已认证的 iWAN 校园网连接
  → 学校网关 https://api.llm.ustc.edu.cn/v1
  → Key 有权限使用的模型
```

| 项目 | 本项目配置示例 |
| --- | --- |
| API Base URL | `https://api.llm.ustc.edu.cn/v1` |
| 聊天请求完整 URL | `https://api.llm.ustc.edu.cn/v1/chat/completions` |
| 模型列表 URL | `https://api.llm.ustc.edu.cn/v1/models` |
| 本地 HTTP 代理 | `http://127.0.0.1:18080` |
| 聊天模型 | `deepseek-flash-2`，需要用当前 Key 验证 |
| API 认证 | `Authorization: Bearer <学校 API Key>` |
| iWAN 配置目录 | `/etc/sdwan/ustc-iwan` |

学校 API Key 从学校模型平台获取。iWAN 的校园身份认证与 API Key 分开配置：API Key 不能代替 iWAN 登录，iWAN 登录成功也不能代替 API Key。

## 2. 准备 iWAN HTTP 代理

### 2.1 已经部署过的服务器

先检查已有服务，正常运行时不需要重复安装或登录：

```bash
sudo systemctl is-active ustc-iwan-http.service
sudo ss -ltnp 'sport = :18080'
sudo journalctl -u ustc-iwan-http.service -n 50 --no-pager
```

预期服务为 `active`，且 `127.0.0.1:18080` 正在监听。监听正常只说明代理进程起来了，仍需在第 3 节验证网关请求。日志可能包含认证链接，分享前先脱敏。

### 2.2 新服务器或认证过期

前提是已从学校可信渠道安装适配服务器架构的 `iwan-client-oidc`。本仓库不包含客户端安装包。检查已安装版本的参数：

```bash
test -x /usr/local/bin/iwan-client-oidc
/usr/local/bin/iwan-client-oidc --help
```

以下参数来自仓库现有的 [iWAN 服务示例](../deploy/ustc-iwan-http.service.example)。若 `--help` 与示例不符，先按该版本客户端说明调整。

首次配置时，在服务器前台启动客户端：

```bash
sudo install -d -m 700 /etc/sdwan/ustc-iwan
sudo /usr/local/bin/iwan-client-oidc \
  --connect --server 1 \
  --http --http-listen 127.0.0.1:18080 \
  --config-dir /etc/sdwan/ustc-iwan
```

根据客户端提示完成学校身份认证；若给出可在其他设备打开的认证地址或验证码，在自己的浏览器中完成。具体流程以该版本提示为准。首次认证应在交互终端完成，避免后台服务一直等待登录。

认证成功后，保留前台进程，在第二个 SSH 终端完成第 3 节测试。测试通过后，再回到前台按 `Ctrl+C`，改用 systemd 托管。若已有服务占用 18080，不要再启动第二个客户端；只有需要重新认证时才在维护时段停止已有代理。

### 2.3 使用 systemd 保持运行

下面安装命令仅用于尚未创建该 unit 的服务器。在已有服务器上先检查现有配置，保留已定制的参数：

```bash
sudo systemctl cat ustc-iwan-http.service
```

新建服务时，从服务器上的项目目录安装示例：

```bash
cd /opt/jmbot
sudo install -m 644 deploy/ustc-iwan-http.service.example \
  /etc/systemd/system/ustc-iwan-http.service
sudo systemctl daemon-reload
sudo systemctl enable --now ustc-iwan-http.service
sudo systemctl status ustc-iwan-http.service --no-pager
```

示例以 root 运行，使用前台认证时的同一配置目录，并设置自动重启。目录中包含认证材料，应限制访问。代理只监听 `127.0.0.1`，无需在云安全组开放 18080。

## 3. 先在服务器上验证 API

下面的脚本只依赖 Python 3 标准库，执行一次模型列表查询和一次短聊天请求。学校 Key 通过终端隐藏输入，不写入脚本、命令历史或命令行参数。需要交互式 SSH 终端。

```bash
python3 - <<'PY'
import getpass
import json
import os
import urllib.error
import urllib.request

base_url = "https://api.llm.ustc.edu.cn/v1"
proxy_url = "http://127.0.0.1:18080"
model = "deepseek-flash-2"
key = getpass.getpass("学校 API Key（隐藏输入）: ").strip()
if not key or not key.isascii() or any(c.isspace() for c in key):
    raise SystemExit("Key 不能为空，也不能包含中文或空白字符。")

# 明确指定代理，并移除当前测试进程中的代理绕过规则。
os.environ.pop("NO_PROXY", None)
os.environ.pop("no_proxy", None)
opener = urllib.request.build_opener(
    urllib.request.ProxyHandler({"http": proxy_url, "https": proxy_url})
)

def call(path, payload=None):
    body = None if payload is None else json.dumps(payload).encode("utf-8")
    request = urllib.request.Request(
        base_url + path,
        data=body,
        headers={
            "Authorization": "Bearer " + key,
            "Content-Type": "application/json",
        },
    )
    try:
        with opener.open(request, timeout=60) as response:
            return json.load(response)
    except urllib.error.HTTPError as exc:
        raise SystemExit(f"请求 {path} 返回 HTTP {exc.code}；按文末排障表检查。")
    except (urllib.error.URLError, OSError):
        raise SystemExit(f"请求 {path} 连接失败；检查代理、iWAN 认证和网关连通性。")
    except (ValueError, UnicodeError):
        raise SystemExit("返回内容不是有效 JSON；检查网关地址和代理状态。")

models = call("/models")
ids = [item.get("id") for item in models.get("data", [])]
print("当前 Key 可见模型：")
for model_id in ids:
    print(" -", model_id)
if model not in ids:
    raise SystemExit(f"示例模型 {model} 未列出；修改脚本中的 model 后重试。")

result = call("/chat/completions", {
    "model": model,
    "messages": [{"role": "user", "content": "请只回复：连接成功"}],
    "stream": False,
    "max_tokens": 64,
})
choices = result.get("choices") or []
content = choices[0].get("message", {}).get("content") if choices else None
if not isinstance(content, str) or not content.strip():
    raise SystemExit("请求已返回，但没有非空文本回复；检查模型协议或输出预算。")
print("模型回复：", content)
PY
```

若网关不提供 `/models`，该查询的 404 不能直接说明聊天不可用。按学校说明确认模型名后，可以跳过列表查询，仅执行聊天请求。模型能列出也不代表当前额度和调用权限一定可用，以聊天请求结果为准。

其他程序接入时同样配置学校 Base URL、学校 Key、实际模型名和本机 HTTP 代理。程序在容器内运行时，`127.0.0.1` 指向容器自身；本文的回环代理地址适用于直接运行在服务器宿主机上的程序。

## 4. 在 JMbot 中配置

先确认服务器采用哪种运行方式，只修改正在使用的那一套。可查看 unit 指向的入口和环境文件：

```bash
sudo systemctl cat jmbot.service
sudo systemctl cat jmbot-chat.service
```

某个 unit 不存在属于正常情况。历史单体版本以 `main.py` 同时承载业务和聊天；当前拆分版本的 `main.py` 承载下载与 QQ 入口，聊天入口为 `main_chat.py`。不能仅凭有 `main.py` 判断仍支持单体聊天，应同时核对版本与 service 配置。不要同时启动两套机器人发送者。

### 4.1 拆分部署：jmbot-chat.service

环境文件通常为 `/etc/jmbot-split.env`。编辑已有配置，保留其他必要设置，每个变量只保留一项：

```dotenv
JM_BOT_CHAT_PROXY=http://127.0.0.1:18080
JM_BOT_CHAT_DEFAULT_PROVIDER=ds_school
JM_BOT_CHAT_DEFAULT_API_KEY=替换为实际学校Key
JM_BOT_MODEL_ROUTES=/opt/jmbot/config/model-routes.json
JM_BOT_CHAT_MASTER_KEY_PATH=/etc/jmbot/chat-master.key
```

`JM_BOT_CHAT_DEFAULT_API_KEY` 用于默认凭据回退；如果已经在管理后台保存了默认 Key，数据库中的默认 Key 优先，单改环境变量不会覆盖它。已有独立 Key 的会话也不会自动改用默认 Key。

模型地址和任务在 `JM_BOT_MODEL_ROUTES` 指定的 JSON 中设置。下面是只启用基本文字聊天的最小配置，适合新建配置；已有路由应在后台或原文件中编辑对应服务商，保留其他配置：

```json
{
  "providers": {
    "ds_school": {
      "base_url": "https://api.llm.ustc.edu.cn/v1",
      "tasks": {
        "planner": {"model": "deepseek-flash-2", "enabled": true, "concurrency": 1},
        "replyer": {"model": "deepseek-flash-2", "enabled": true, "concurrency": 1},
        "utils": {"model": "deepseek-flash-2", "enabled": true, "concurrency": 1}
      }
    }
  }
}
```

这里的 `base_url` 保留 `/v1`，不要加 `/chat/completions`，程序会自行追加任务路径。未配置的记忆、视觉、成长等任务不具备可用模型；需要这些能力时参照 [拆分服务说明](split-services-runbook.md) 配置，不能仅凭模型名称认定支持。

配置完成后，使用实际服务用户可读的权限保护环境文件，并只重启聊天服务：

```bash
sudo chmod 600 /etc/jmbot-split.env
sudo systemctl restart jmbot-chat.service
sudo systemctl is-active jmbot-chat.service
sudo journalctl -u jmbot-chat.service -n 80 --no-pager
```

### 4.2 历史单体部署：jmbot.service

本节仅供仍保留旧聊天执行器的服务器版本参考。当前工作区的 `bot.py` 已不包含旧聊天执行器，设置这些旧变量不会使下载进程恢复聊天；当前版本应按 4.1 配置独立聊天服务。

环境文件通常为 `/etc/jmbot.env`，设置：

```dotenv
JM_BOT_CHAT_PROXY=http://127.0.0.1:18080
JM_BOT_CHAT_ENDPOINT=https://api.llm.ustc.edu.cn/v1/chat/completions
JM_BOT_CHAT_MODEL=deepseek-flash-2
JM_BOT_CHAT_DEFAULT_PROVIDER=ds_school
JM_BOT_CHAT_DEFAULT_API_KEY=替换为实际学校Key
JM_BOT_CHAT_MASTER_KEY_PATH=/etc/jmbot/chat-master.key
```

单体的 `JM_BOT_CHAT_ENDPOINT` 要填完整聊天 URL，与拆分部署的 `base_url` 格式不同。仓库环境示例中的该变量默认指向 DeepSeek 官方，使用学校 Key 时必须改成学校网关。

```bash
sudo chmod 600 /etc/jmbot.env
sudo systemctl restart jmbot.service
sudo systemctl is-active jmbot.service
sudo journalctl -u jmbot.service -n 80 --no-pager
```

`JM_BOT_CHAT_PROXY` 由当前代码直接支持，无需再应用旧的 `deploy/chat-proxy-runtime.patch`。只在 SSH 终端执行 `export` 不会修改已经运行的 systemd 服务；应编辑其 `EnvironmentFile` 并重启对应服务。

### 4.3 默认 Key、独立 Key 与 QQ 命令

可以在管理后台的「API Key」页面选择学校服务商并保存默认 Key，或为群聊、私聊设置独立 Key。后台默认监听回环地址，访问方式见 [拆分服务说明](split-services-runbook.md)。

在机器人好友私聊中，也可以使用以下命令。群聊配置需要机器人管理员权限：

```text
/ds 学校 <学校APIKey>
/ds 学校 <群号>\<学校APIKey>
/ds 状态
```

第二条命令的群号和 Key 之间是一个反斜线。当前语法中 `/ds <KEY>` 默认选择 DeepSeek 官方，因此学校 Key 要明确写「学校」。覆盖已有独立 Key 时，按机器人返回的提示由同一操作者确认，待确认操作有效期为 5 分钟。

`glm_school`、`kimi_school`、`qwen_school` 是独立的服务商标识；选择它们时还要配置对应路由。不要把学校 Key 配给 `ds_official` 等官方服务商。

配置 Key 不会自动开启已关闭的聊天权限。API 测试通过后，再检查后台全局开关和目标会话授权，并通过私聊普通文字或群聊真实 `@机器人` 验证完整链路。

## 5. 常见问题

| 现象 | 优先检查 |
| --- | --- |
| 连接 `127.0.0.1:18080` 被拒绝 | iWAN 服务是否启动、监听地址是否一致；程序是否运行在容器内 |
| 代理在监听，但连接超时、TLS 或隧道失败 | iWAN 登录是否有效、校园网连接是否建立、网关是否可达；不要关闭证书校验 |
| HTTP 401 | Key 是否失效，是否把学校 Key 发到官方接口，是否错误取出了数据库密文 |
| HTTP 403 | Key 的模型权限、平台访问策略和校园网接入要求 |
| HTTP 404 | Base URL 与完整 endpoint 是否混用，路径和模型名是否正确；区分模型列表与聊天接口 |
| HTTP 400 | 模型名、请求字段和该模型支持的协议 |
| HTTP 402 / 429 | 额度、余额或请求频率；按学校平台返回说明处理 |
| `latin-1` / Key 非 ASCII | 是否把中文、命令前缀或整条 QQ 消息当成了 Key |
| 命令行测试成功，机器人不回复 | 实际环境文件、后台默认 Key、会话独立 Key、服务商路由、聊天授权与日志 |
| 改了环境变量仍用旧 Key | 后台已存默认 Key 优先，或该会话正在使用独立 Key |

日志查询示例，根据部署选择对应 unit：

```bash
sudo journalctl -u jmbot-chat.service -n 200 --no-pager
sudo journalctl -u ustc-iwan-http.service -n 100 --no-pager
```

此前发生过主密钥不一致导致旧 Key 解密失败的问题，见 [聊天 API Key 失效排查](chat-api-key-troubleshooting.md)。该旧文档保留了历史语法，本文按当前 README 和代码整理。诊断时只查看「是否已配置、能否解密」等状态，不照抄旧文档中直接打印 Key 的命令。

## 6. 保存与迁移

- 学校 Key 只写入服务器私有环境文件或后台凭据存储，不提交 Git，不写进本文。
- 聊天数据库和其对应的 `chat-master.key` 必须配套备份。已有数据库时不要生成新主密钥，也不要随意切换 `JM_BOT_CHAT_MASTER_KEY_PATH`。
- 新服务器的 iWAN 认证按客户端流程完成；不要将 `/etc/sdwan/ustc-iwan` 中的认证材料当成公开配置复制或分享。
- 单独的 API 程序只需学校 Key、网关、模型与网络接入；`chat-master.key` 是 JMbot 本地加密存储使用的主密钥。

参考配置：[iWAN unit](../deploy/ustc-iwan-http.service.example)、[单体环境变量](../deploy/env.example)、[拆分环境变量](../deploy/split.env.example)、[模型任务路由示例](../deploy/model-routes.example.json)。
