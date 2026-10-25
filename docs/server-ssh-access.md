# 阿里云服务器 SSH 公钥连接

本文记录本项目服务器的 SSH 公钥认证方式。公钥可以放入服务器的 `authorized_keys`；私钥只保存在本机，不能发送到聊天、工单或服务器日志中。

## 本次连接信息

- 服务器：`root@47.97.113.248`
- 本机已有密钥：`C:\Users\LJY\.ssh\id_ed25519` 和 `C:\Users\LJY\.ssh\id_ed25519.pub`
- 本次复用了已有密钥，没有新生成密钥对。
- 公钥指纹：`SHA256:AePg31bMjR0qsD35Tk94lsGO+NhppbBI4/vrMOU5X8w`
- 服务器 ED25519 主机指纹：`SHA256:yk+yXNgk8esHvKBC/qCeQObfzaNwP5pXHUbw5+1Bkvk`
- WSL 私钥副本：`~/.ssh/aliyun-codex`，权限为 `600`。

本次首次从 WSL 连接时，SSH 没有自动选用 Windows 密钥。显式指定 `/mnt/c/Users/LJY/.ssh/id_ed25519` 后，WSL OpenSSH 又因为 Windows 挂载盘上的权限显示为 `0777` 而忽略私钥。将私钥复制到 WSL 自己的 SSH 目录并设为 `600` 后，公钥认证成功。复制只用于本机认证，Windows 原始密钥没有被改动。

## 授权公钥

在服务器上以 root 或 sudo 权限执行。此命令会追加公钥，不会覆盖 `authorized_keys` 里的其他授权：

```bash
sudo install -d -m 700 /root/.ssh
printf '%s\n' 'ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIBrdvxCQ+qCnSEnpD6jNRKQ7btn9loss5XKBnbLXf89M aliyun-codex' \
  | sudo tee -a /root/.ssh/authorized_keys >/dev/null
sudo chmod 600 /root/.ssh/authorized_keys
```

检查服务器上保存的公钥指纹：

```bash
sudo ssh-keygen -lf /root/.ssh/authorized_keys
```

输出中应包含本机公钥指纹 `SHA256:AePg31bMjR0qsD35Tk94lsGO+NhppbBI4/vrMOU5X8w`。主机指纹与公钥指纹用途不同：主机指纹用于确认连到的是预期服务器；公钥指纹用于确认服务器授权了预期的本机密钥。

## 从 WSL 连接

首次准备 WSL 私钥副本，之后不需要重复执行：

```bash
install -d -m 700 "$HOME/.ssh"
install -m 600 /mnt/c/Users/LJY/.ssh/id_ed25519 "$HOME/.ssh/aliyun-codex"
```

连接服务器：

```bash
ssh -i "$HOME/.ssh/aliyun-codex" \
  -o IdentitiesOnly=yes \
  root@47.97.113.248
```

如果 SSH 首次询问是否信任服务器，先核对显示的 ED25519 主机指纹是否为：

```text
SHA256:yk+yXNgk8esHvKBC/qCeQObfzaNwP5pXHUbw5+1Bkvk
```

只有指纹匹配时才接受。若服务器重装或主机密钥轮换，应先通过可信的阿里云控制台确认新指纹，不要直接忽略变化提示。

也可以在 WSL 的 `~/.ssh/config` 添加别名：

```sshconfig
Host jmbot-aliyun
    HostName 47.97.113.248
    User root
    IdentityFile ~/.ssh/aliyun-codex
    IdentitiesOnly yes
```

之后使用 `ssh jmbot-aliyun` 连接。

## 密钥安全

- `id_ed25519` 和 `aliyun-codex` 都是私钥文件；不要公开其内容，也不要粘贴到 QQ、聊天或命令输出中。
- 公钥以 `ssh-ed25519` 开头，可以用于服务器授权。
- 若本机密钥遗失或怀疑泄漏，应从 `/root/.ssh/authorized_keys` 中移除对应公钥，并改用新密钥对。
- 从 WSL 删除本机副本时，可执行 `rm "$HOME/.ssh/aliyun-codex"`；这不会删除 Windows 原始密钥，也不会撤销服务器授权。
- root 密码曾在聊天中暴露，建议更换；日常连接使用 SSH 密钥认证，不在聊天中传递密码。
