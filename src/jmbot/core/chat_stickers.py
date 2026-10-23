"""The checked-in, user-curated sticker catalog shared by prompts and sending."""

import json
from pathlib import Path


STICKER_DIR = Path(__file__).resolve().parents[3] / "data" / "stickers"
STICKERS = {
    item["id"]: item
    for item in json.loads((STICKER_DIR / "manifest.json").read_text(encoding="utf-8"))
}


def sticker_path(sticker_id: str) -> Path | None:
    """Only catalog IDs may select a local image; never accept a model path/URL."""
    item = STICKERS.get(sticker_id)
    if item is None:
        return None
    path = STICKER_DIR / item["file"]
    return path if path.is_file() else None


def build_sticker_rule() -> str:
    return (
        "\n图片表情包：以下说明都是以大肥鱼为主语，由主人逐张审核的含义和用途；"
        "按说明结合最新语境选择，不凭画面另猜含义。可以频繁使用表情包，"
        "轻松聊天时主动选合适的图，也可以只用一张图回复；不必每句附图，"
        "纯文字也很自然，不连续堆图或机械重复同一张。一次回复最多一张图片表情包。"
        "通常在图片表情包和超级表情中择一，不为了凑数同时使用；"
        "超级表情按情绪和语境灵活选，不必每次出现。认真求助或情绪低落时，"
        "优先认真回应或选安慰类图，不用嫌弃、辱骂、打人、幸灾乐祸类图。"
        "调侃、拱人、骂猪等只是熟人轻松玩梗；对方不悦就停止。"
        "无聊骚扰类只用于已经适合参与的轻松对话，不触发额外主动消息或刷屏。"
        "被调侃是 AI 时用自嘲图接梗，不改变角色其他规则。"
        "输出格式只能是 [sticker:三位编号]，放在 reply 内（私聊直接写在回复中）；"
        "禁止输出文件名、路径、URL、CQ 码或未列出的编号。可用目录：\n"
        + "\n".join(
            f"[sticker:{key}] {item['description']}；使用场景：{item['usage']}"
            for key, item in STICKERS.items() if sticker_path(key) is not None
        )
    )
