# 聊天 API Key 失效排查（401 / latin-1 报错）

2026-10-23 的一次线上故障复盘：机器人私聊、群聊都"不说话"，但下载功能正常。
结论是 **程序 + 数据问题，不是 iWAN 掉线**。

## 现象

- 群聊 `@机器人` 无回复，日志出现：
  `群聊 DeepSeek 请求失败 status_code=401 error_type=ChatAPIError`
- 私聊直接抛异常、连 401 都没有：
  `UnicodeEncodeError: 'latin-1' codec can't encode characters in position 7-8`
- iWAN 代理、NapCat、消息收发全部正常（日志里能收到 QQ 消息）。

## 根因

1. **主密钥路径被切换，导致密钥解不开。**
   `JM_BOT_CHAT_MASTER_KEY_PATH` 从默认的 `data/chat-master.key`
   改成 `/etc/jmbot/chat-master.key`（两份文件内容不同）。旧密文用新主密钥
   解不开。

2. **`ChatStore._get_key` 的"旧库升级"逻辑把密文当明文再加密。**
   解密失败时它直接 `encrypt(原密文)` 回写并返回原密文，于是：
   - 每读一次就多套一层密文；
   - 返回给上游的是 `gAAAAAB...` 这种密文本体，必然 401。
   上游报错原文：
   `LiteLLM Virtual Key expected. Received=gAAA****wnn_, expected to start with 'sk-'`

3. **`/ds 群聊 群号 KEY` 不是合法写法，被当成私聊 Key 存了。**
   解析器只认 `群聊key`（中间没有空格）：
   `/ds 群聊 1053512745 sk-xxx` 落到底部分支 → `chat_private_key`
   → 存下 `"群聊 1053512745 sk-xxx"` → `Bearer 群聊 ...` 头部含非 ASCII
   → `UnicodeEncodeError`（位置 7-8 正好是 "群聊"）。

## 修复

代码（`src/jmbot/core/chat_service.py`、`src/jmbot/core/parser.py`）：

- `_get_key` 遇到 `gAAAA` 开头的密文且解不开时，**返回 None 而不是再加密**，
  由管理员重新录入，避免污染数据库。
- `request_chat_completion` 提前校验 Key 必须为 ASCII，否则抛 `ChatAPIError`，
  不再把整个 Matcher 打挂。
- `parse_ds_message` 同时接受 `群聊key 群号 KEY` 和 `群聊 群号 KEY`。

数据：备份 `data/chat.sqlite3` 后，用正确主密钥重新写入私聊 / 群聊 Key。

## 自查命令

```bash
# 1) 服务是否在跑、iWAN 是否在听
systemctl is-active jmbot.service ustc-iwan-http.service
ss -tlnp | grep 18080

# 2) 最近是否有 401 / latin-1
journalctl -u jmbot.service -n 200 --no-pager | grep -Ei "401|Unicode|latin-1|请求失败"

# 3) 当前存的是不是真 Key（应输出 sk- 开头，而不是 gAAAA）
cd /opt/jmbot && .venv/bin/python - <<'PY'
import sys; sys.path.insert(0, "/opt/jmbot/src")
from jmbot.core.chat_service import ChatStore
s = ChatStore("/opt/jmbot/data/chat.sqlite3", master_key_path="/etc/jmbot/chat-master.key")
print(s.get_private_key(3065532050))
print(s.get_group_key(1053512745))
PY
```

## 预防

- 主密钥文件 `chat-master.key` **只生成一次、永不更换**，并和 `chat.sqlite3`
  一起备份。换服务器/迁移时必须两者一起搬。
- 不要在 `JM_BOT_CHAT_MASTER_KEY_PATH` 之间来回改；改了就等于作废旧密钥。
- 设 Key 用 `/ds 群聊key 群号 KEY`；私聊用 `/ds KEY`。
- 出现 401 时先打印上面第 3 条的脚本确认存的是 `sk-` 而不是 `gAAAA`。
- 如果 iWAN 真掉线，症状是 `ChatAPIError(None, "聊天服务暂时不可用")`
  （连接失败），而不是 401，两者要区分。
