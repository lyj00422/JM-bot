# 2026-10-24 服务器 SSH 不可达事故：内存耗尽（OOM）导致 sshd 失效

## 事故摘要

| 项目 | 内容 |
| --- | --- |
| 服务器 | `47.97.113.248`（轻量应用服务器，主机名 `iZbp1f5bqgziditwz2rdq3Z`，Ubuntu 24.04，2C2G） |
| 故障现象 | `ssh root@47.97.113.248` 卡在 `Connection timed out during banner exchange`；22 端口 TCP 可连、但收不到任何 SSH 数据 |
| 直接原因 | 内存耗尽触发内核 OOM killer，`systemd-journald` 反复启动失败，`sshd` 接受连接后卡在写 syslog，无法发出 SSH banner |
| 深层原因 | 2 GiB 内存无 swap，NapCat 的 `qq` 进程单实例常驻 600–730 MB，且 split 模式允许多个 native 实例同时存活 |
| 恢复手段 | 控制台 VNC 救援登录确认现场 → 重启实例 → 将 `JM_BOT_NATIVE_INSTANCE_LIMIT` 由 2 改为 1 → 重启 `jmbot-chat` |
| 当前状态 | SSH 已恢复；三个 split 服务均为 active；`jmbot-chat` 内存约 40 MB；系统可用内存约 870 MB |

## 影响面

- 公网 SSH 完全不可用（本机、境外 6 个探测节点均无法登录）。
- 同机 80 端口的 Web 服务同样无响应（TCP 可连、应用层 0 字节）。
- NapCat（`qq`）被 OOM killer 反复杀死，QQ 收发在此期间中断。
- JMbot 的下载、聊天、管理三个服务本身未被 kill，但 `jmbot-chat` 依赖的 native 池无法正常工作。

## 时间线（CST，2026-10-24）

| 时间 | 事件 |
| --- | --- |
| 17:41 | 本机开始排查。`:22` TCP 握手成功（20–25 ms），但 25 秒内未收到任何 SSH banner |
| 17:45 | check-host 6 个国家节点 TCP 全部可达（0.16–0.34 s）；HTTP 检测 3 节点 25 s 超时、1 节点 8.2 s 被 reset |
| 17:50 | 阿里云 Workbench 从 VPC 内网连 `172.25.47.126:22`，同样报 `SocketTimeoutException`（连上无数据） |
| ~18:00 | 控制台 VNC 救援登录成功，控制台显示 journald 反复启动失败 + 4 次 `qq` 进程 OOM kill |
| ~18:01 | 重启实例，`jmbot-chat` / `jmbot-download` / `jmbot-split-admin` 于 18:01:06 自动拉起 |
| 18:08 | 修改 `/etc/jmbot-split.env`：`JM_BOT_NATIVE_INSTANCE_LIMIT` 2 → 1，重启 `jmbot-chat` |
| 18:12 | 验证 SSH 恢复、进程内环境变量为 1、三个服务 active、管理后台可访问 |

## 现场证据

### 1. 外部探测（故障期间）

```text
tcp=ok 92ms                 ← 内核完成三次握手
banner=none after 6116ms    ← sshd 未发送 SSH-2.0-... 版本串
http_code=000 connect=0.001s total=10.0s   ← 80 端口同样只有 TCP 层响应
```

- 关闭的端口（443 / 2222 / 3306 / 8080）全部丢包，说明安全组本身正常、22/80 是放行且处于 LISTEN 状态。
- 客户端 socket 长时间停在 `FIN_WAIT_2`，说明服务器内核正常 ACK 了数据与 FIN——**网络链路是通的，问题在用户态**。

### 2. VNC 救援登录（故障期间的控制台输出）

```text
Ubuntu 24.04.2 LTS iZbp1f5bqgziditwz2rdq3Z tty1

[5302386.921813] systemd[1]: Failed to start systemd-journald.service - Journal Service.
[5303319.827818] systemd[1]: Failed to start systemd-journald.service - Journal Service.
...（每 10 秒左右重复一次）
[5322215.912190] Out of memory: Killed process 341782 (qq) anon-rss:601344kB ...
[6644005.489138] Out of memory: Killed process 664770 (qq) anon-rss:709352kB ...
[8028655.274119] Out of memory: Killed process 2148319 (qq) anon-rss:729016kB ...
[9413030.261553] Out of memory: Killed process 3811442 (qq) anon-rss:712696kB ...
```

关键点：

- 被 OOM kill 的是 `qq`（NapCat 容器里的 NTQQ），四次记录对应四个不同 PID，常驻内存 600–730 MB。
- `systemd-journald` 在 OOM 期间根本起不来，反复重启失败。
- 内核 monotonic 时间戳（5.3M–9.4M 秒）说明机器已连续运行数十天，属于长期累积后的一次性雪崩，而非刚部署就崩。

### 3. 重启后状态

```text
$ free -m
               total        used        free      shared  buff/cache   available
Mem:            1613         698          73           3         998         914
Swap:              0           0           0

$ ss -lntp | grep -E ':808[0-9]'
LISTEN 0 2048 0.0.0.0:8080      ← jmbot-download（NapCat 入口）
LISTEN 0 2048 127.0.0.1:8082    ← jmbot-chat
LISTEN 0 2048 127.0.0.1:8081    ← jmbot-split-admin（管理后台）
```

`systemctl status jmbot-chat` 显示该 unit 已经带有 cgroup 限制：`Memory: 39.4M (high: 760.0M max: 900.0M)`，说明聊天进程自身有上限，但 NapCat 容器没有。

## 根因分析

故障链条：

```text
NapCat(qq) 单实例常驻 600–730 MB
        │
        ├── 2 GiB 物理内存 + 0 swap
        │
        ├── split 模式 JM_BOT_NATIVE_INSTANCE_LIMIT=2
        │      → 每个活跃 scope 起一个完整 MaiBot 子进程，内存线性叠加
        ▼
内存耗尽 → 内核 OOM killer 反复 kill qq
        ▼
systemd-journald 无法分配内存 / 反复被杀，起不来
        ▼
sshd 每接受一个连接先写 syslog（journald socket 不响应）
        ▼
sshd 阻塞在“发送 banner 之前” → 客户端只看到 TCP 通了、永远等不到 SSH-2.0
        ▼
同一时刻 80 端口 Web 进程同样被内存压力拖死 → 应用层 0 字节
```

几个容易误判的点：

1. **"端口 22 被占满/被墙"不成立**：安全组放行、LISTEN 正常、内网 Workbench 也一样连不上；端口是开的，只是没有应用层数据。
2. **`ping` 不通不能作为依据**：本机 ICMP 出站被整体拦截（连 `223.5.5.5` 都报 `transmit failed`），ping 结果无参考价值。
3. **`sshd` 并没有死**：它还在监听，只是卡在日志写入。重启实例后立即可用，无需重装或改配置。
4. **OOM 杀的是 `qq` 不是 JMbot**：JMbot 的 Python 进程因 cgroup `MemoryMax=900M` 受到保护，反而活了下来；但它的聊天能力依赖 native 池，而 native 池又要额外吃内存。

## 处置过程

```bash
# 1) 备份并修改实例上限（/etc/jmbot-split.env 第 82 行）
cp -a /etc/jmbot-split.env /etc/jmbot-split.env.bak.$(date +%Y%m%d%H%M%S)
sed -i -E 's/^[[:space:]]*JM_BOT_NATIVE_INSTANCE_LIMIT=.*/JM_BOT_NATIVE_INSTANCE_LIMIT=1/' /etc/jmbot-split.env

# 2) 重启聊天服务（下载服务承载 NapCat 入口，不动它，避免再次打断 QQ）
systemctl restart jmbot-chat

# 3) 验证
systemctl is-active jmbot-chat jmbot-download jmbot-split-admin
tr '\0' '\n' < /proc/$(systemctl show -p MainPID --value jmbot-chat)/environ | grep INSTANCE_LIMIT
free -m
```

验证结果：

```text
JM_BOT_NATIVE_INSTANCE_LIMIT=1     ← 运行中的进程确实读到了新值
active / active / active           ← 三个服务均正常
jmbot-chat: Memory 39.4M (max 900M), peak 81.4M
Mem: 1613 total / 736 used / 876 available
```

管理后台通过本地 SSH 隧道访问：

```bash
ssh -L 8081:127.0.0.1:8081 -N root@47.97.113.248
# 浏览器打开 http://127.0.0.1:8081/
```

## 第二次崩溃（同日 19:58）

把 `JM_BOT_NATIVE_INSTANCE_LIMIT` 改成 1 并重启后，运行约 1 小时 57 分钟再次失联，症状与第一次完全一致（`:22` TCP 可连、banner 无响应）。

重启后从 `journalctl -b -1`（上一次启动，18:00:51 – 19:58:27）取到的证据：

```text
19:47:16  最后一条 NapCat 群消息
19:47:36  最后一条 admin RPC 200
19:50:34  systemd-journald[299]: Under memory pressure, flushing caches.
19:51:30  ...（约每分钟一次）
19:57:59  systemd-journald[299]: Under memory pressure, flushing caches.
19:58:27  systemd-journald[299]: Under memory pressure, flushing caches.   ← 最后一行，随后整机失联
```

关键差异与判读：

1. **这次 journal 里没有任何 OOM killer 记录**（`grep -c 'out of memory|oom-kill|killed process'` 结果为 0），也没有 `systemd-oomd`（未启用）。原因是 journald 自己就在内存压力下（`flushing caches`），内核消息很可能被丢弃，**不等于没有 OOM**。
2. **NapCat 容器没有被 OOM 杀死**：`docker inspect` 显示 `OOMKilled=false RestartCount=0`，容器是在整机冻结时一起挂住的，不是被单独杀掉后重启。
3. **整机是「压力—卡死」而不是「瞬间重启」**：19:50 起持续 8 分钟的内存压力，应用在 19:47 就停止响应，journald 还勉强撑到 19:58:27 才彻底失联。
4. 磁盘只有 38% 占用，排除写满；无 swap（`Swap: 0`），匿名内存无法回收，压力只能靠直接 reclaim 硬扛，最终所有需要分配内存的进程（含 `sshd` 的 fork）一起卡住。

结论不变：**2 GiB 物理内存 + 0 swap 的配置扛不住 NapCat + split 模式 JMbot 的常驻内存**，限制 native 实例为 1 只是把到崩溃的时间从数周拉长到约 2 小时，没有解决内存总量不足的问题。

另一个副作用：这次崩溃后 NapCat 的 QQ 登录态失效，重启后停在扫码登录（`二维码已保存到 /app/napcat/cache/qrcode.png`），在扫码前 OneBot 不会连上 —— 即服务进程是 active 的，但机器人实际不在线。

## 遗留风险

1. **没有 swap**（本次按要求未添加）。当前可用内存约 870 MB，NapCat 已占约 450 MB；一旦 native 实例启动或叠加 PDF 生成任务，仍有再次 OOM 的可能。
2. **`JM_BOT_NATIVE_INSTANCE_LIMIT` 默认值仍是 4**（见 `src/jmbot/chat_app.py:69`），本次只改了服务器上的 env。任何重装、换机、重建 env 的环境都会回到默认值。
3. **NapCat 容器没有内存上限**，`docker update --memory` 未设置；它是最容易被 OOM 选中的目标。
4. **journald 曾反复崩溃**，虽然 `journalctl --verify` 目前全部 PASS，但故障时段的日志可能缺失，事后取证只能依赖 VNC 屏幕内容。

## 后续建议

按优先级：

1. **观察 24 小时**：`free -m`、`journalctl -u jmbot-chat -f`、`journalctl -k | grep -i oom`。若 `available` 持续低于 300 MB，说明 1 个 native 实例也压不住。
2. **治本**：升级到 4 GiB 内存，或把 NapCat 拆到独立机器；2 GiB 同时跑 NTQQ + split 模式 JMbot 余量太小。
3. **代码侧收口**：把 `chat_app.py` 里 `JM_BOT_NATIVE_INSTANCE_LIMIT` 的默认值从 4 调到 1–2，或在启动时按 `MemAvailable` 自适应，避免新环境默认就超配。
4. **给 NapCat 限制资源**：`docker update --memory 1g --memory-swap 1536m napcat`，让 cgroup 优先限制容器而不是让内核 OOM 随机杀进程。
5. **加内存告警**：可用内存低于 15% 或 OOM 计数增加时告警，避免再次以「SSH 连不上」的形式才发现。

## 附：本次用到的排查命令

```bash
# 本机快速判断“端口开着但服务不响应”
ssh -vv -o ConnectTimeout=10 root@47.97.113.248 true    # 看是否卡在 banner exchange

# 服务器侧（VNC 或恢复后的 SSH）
uptime; free -m; df -h /
dmesg -T | grep -iE 'out of memory|oom-kill' | tail -20
journalctl -k -b -1 | grep -iE 'oom|hung_task'          # 重启后看上一次启动的日志
ss -lntp | grep -E ':22|:808[0-9]'
systemctl status jmbot-chat jmbot-download jmbot-split-admin --no-pager
journalctl --verify 2>&1 | tail -5
```

## 关联文档

- `docs/server-ssh-access.md` — SSH 公钥连接方式与主机指纹
- `docs/implementation-reviews/2026-06-23-server-stability-merge.md` — 上一次同类冻结（2 GiB 内存压力）的记录
- `docs/split-services-runbook.md` — split 模式服务划分与实例上限约定
- `deploy/split.env.example` — `JM_BOT_NATIVE_INSTANCE_LIMIT` 示例配置
