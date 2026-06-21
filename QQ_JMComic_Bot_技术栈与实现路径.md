
# QQ-JMComic-Bot 技术栈与实现路径文档

## 一、项目目标
实现 QQ 群机器人，通过命令：
```
/jm 123456
```
自动完成：
1. 解析漫画ID
2. 调用 JMComic-Crawler-Python 下载
3. 转换为 PDF
4. 返回 QQ 群文件

---

## 二、技术栈

### 1. QQ机器人框架（二选一）
- NoneBot2（推荐）
- NapCat / Lagrange（轻量）

### 2. 核心依赖
```
jmcomic
aiohttp
requests
img2pdf
Pillow
asyncio
```

### 3. 可选增强
- redis（任务队列）
- sqlite（记录系统）
- loguru（日志）

---

## 三、系统架构

```
QQ消息
  ↓
Bot Adapter
  ↓
Command Parser (/jm id)
  ↓
JMComic Service
  ↓
Download Engine
  ↓
PDF Builder
  ↓
File Sender
  ↓
QQ返回
```

---

## 四、模块设计

### 1. bot入口
负责接收QQ消息并转发

### 2. parser模块
解析：
```
/jm 123456
```

输出：
```json
{"cmd":"jm","id":"123456"}
```

---

### 3. jm_service.py
```python
import jmcomic

def download_comic(comic_id, output="./downloads"):
    option = jmcomic.create_option({"dir": output})
    jmcomic.download_album(comic_id, option)
    return f"{output}/{comic_id}"
```

---

### 4. pdf_builder.py
```python
import img2pdf
import os

def build_pdf(img_dir, out_pdf):
    imgs = sorted([
        os.path.join(img_dir, f)
        for f in os.listdir(img_dir)
        if f.endswith(".jpg") or f.endswith(".png")
    ])

    with open(out_pdf, "wb") as f:
        f.write(img2pdf.convert(imgs))

    return out_pdf
```

---

### 5. task_runner
- 异步任务队列
- 防止下载阻塞
- 支持并发控制

---

### 6. sender模块
- send_group_msg
- send_group_file

---

## 五、执行流程

1. 用户输入 `/jm 350234`
2. parser解析ID
3. task创建任务
4. jmcomic下载
5. 转PDF
6. 上传QQ
7. 返回文件

---

## 六、项目结构

```
qq-jmcomic-bot/
├── bot.py
├── config.py
├── requirements.txt
├── core/
│   ├── parser.py
│   ├── jm_service.py
│   ├── pdf_builder.py
│   ├── task_runner.py
│   ├── sender.py
├── data/
│   ├── downloads/
│   ├── output/
└── logs/
```

---

## 七、运行方式

```bash
pip install -r requirements.txt
python bot.py
```

---

## 八、注意事项

- 下载任务必须异步处理
- QQ文件必须通过接口上传
- 避免重复任务
- 加入异常重试机制

---

## 九、升级方向

- Redis任务队列
- Web管理后台
- Docker部署
- 多群支持
