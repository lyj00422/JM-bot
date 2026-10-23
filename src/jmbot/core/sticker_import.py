"""Safe, append-only import of manually described sticker ZIP packages."""

from __future__ import annotations

import hashlib
import json
import os
import tempfile
import threading
import zipfile
import shutil
from pathlib import Path
from typing import Any

from PIL import Image


TEMPLATE_MANIFEST = {
    "schema_version": 1,
    "name": "我的表情包",
    "stickers": [
        {
            "id": 1,
            "file": "images/1.jpg",
            "meaning": "这张图表达的意思",
            "use_when": ["适合使用的场景"],
            "avoid_when": ["不适合使用的场景"],
            "emotion_tags": ["情绪标签"],
            "tone": "语气",
            "relationship": "所需熟悉程度",
        }
    ],
}


class StickerImportError(ValueError):
    pass


class StickerImporter:
    _lock = threading.Lock()

    def __init__(self, catalog_dir: str | Path, *, max_files: int = 128, max_image_bytes: int = 8 * 1024 * 1024):
        self.catalog_dir = Path(catalog_dir)
        self.catalog_dir.mkdir(parents=True, exist_ok=True)
        self.manifest_path = self.catalog_dir / "manifest.json"
        self.max_files = max_files
        self.max_image_bytes = max_image_bytes

    def template_bytes(self) -> bytes:
        """Return a deterministic template ZIP for the admin download route."""
        import io
        out = io.BytesIO()
        with zipfile.ZipFile(out, "w", compression=zipfile.ZIP_DEFLATED) as archive:
            archive.writestr("manifest.json", json.dumps(TEMPLATE_MANIFEST, ensure_ascii=False, indent=2) + "\n")
            archive.writestr("images/README.txt", "把与 manifest.json 对应的图片放在此目录。\n")
        return out.getvalue()

    def import_zip(self, package: str | Path) -> dict[str, Any]:
        package = Path(package)
        if not package.is_file():
            raise StickerImportError("上传文件不存在")
        staging = Path(tempfile.mkdtemp(prefix="sticker-import-", dir=self.catalog_dir))
        created: list[Path] = []
        committed = False
        try:
            entries = self._extract(package, staging)
            payload = self._load_manifest(staging / "manifest.json")
            prepared = []
            seen_hashes: set[str] = set()
            for item in payload["stickers"]:
                source = staging / str(item["file"])
                if not source.is_file() or not self._is_inside(staging, source):
                    raise StickerImportError(f"图片文件不存在或路径非法: {item['file']}")
                digest = self._sha256(source)
                if digest in seen_hashes:
                    raise StickerImportError(f"重复图片: {item['file']}")
                seen_hashes.add(digest)
                prepared.append((item, source, digest))

            # One process owns the catalog. Keep allocation and manifest update
            # together so concurrent uploads cannot reuse an assigned ID.
            with self._lock:
                existing = self._load_existing()
                used_hashes = {str(item.get("sha256")) for item in existing if item.get("sha256")}
                for item in existing:
                    old_file = self.catalog_dir / str(item.get("file", ""))
                    if old_file.is_file():
                        used_hashes.add(self._sha256(old_file))
                if seen_hashes & used_hashes:
                    raise StickerImportError("上传包包含已经存在的图片")
                current_max = max([int(item["id"]) for item in existing if str(item.get("id", "")).isdigit()] or [0])
                added: list[dict[str, Any]] = []
                for item, source, digest in prepared:
                    current_max += 1
                    target_name = f"imported/{current_max:03d}{source.suffix.lower()}"
                    target = self.catalog_dir / target_name
                    target.parent.mkdir(parents=True, exist_ok=True)
                    shutil.copyfile(source, target)
                    created.append(target)
                    added.append({
                        "id": f"{current_max:03d}",
                        "file": target_name,
                        "description": str(item["meaning"]).strip(),
                        "usage": "；".join(str(x).strip() for x in item["use_when"]),
                        "avoid_when": [str(x).strip() for x in item["avoid_when"]],
                        "emotion_tags": [str(x).strip() for x in item["emotion_tags"]],
                        "tone": str(item["tone"]).strip(),
                        "relationship": str(item["relationship"]).strip(),
                        "source": "manual_import",
                        "sha256": digest,
                    })
                final = existing + added
                self._write_manifest(final)
                committed = True
            return {"name": str(payload["name"]), "added": added, "count": len(added), "max_id": current_max}
        except Exception:
            if not committed:
                for path in created:
                    path.unlink(missing_ok=True)
            raise
        finally:
            self._remove_tree(staging)

    def _extract(self, package: Path, staging: Path) -> list[zipfile.ZipInfo]:
        try:
            with zipfile.ZipFile(package) as archive:
                infos = archive.infolist()
                if len(infos) > self.max_files:
                    raise StickerImportError("ZIP 文件数量超过限制")
                total = 0
                for info in infos:
                    name = Path(info.filename)
                    if info.is_dir() or name.is_absolute() or ".." in name.parts:
                        raise StickerImportError("ZIP 包含非法路径")
                    total += info.file_size
                    if total > 32 * 1024 * 1024:
                        raise StickerImportError("ZIP 解压大小超过限制")
                    destination = staging / name
                    destination.parent.mkdir(parents=True, exist_ok=True)
                    with archive.open(info) as source, destination.open("wb") as target:
                        target.write(source.read(self.max_image_bytes + 1))
                    if destination.stat().st_size > self.max_image_bytes:
                        raise StickerImportError("图片文件过大")
                return infos
        except zipfile.BadZipFile as exc:
            raise StickerImportError("无效的 ZIP 文件") from exc

    def _load_manifest(self, path: Path) -> dict[str, Any]:
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            raise StickerImportError("manifest.json 无法读取") from exc
        if not isinstance(payload, dict) or payload.get("schema_version") != 1 or not isinstance(payload.get("stickers"), list):
            raise StickerImportError("manifest.json schema_version 必须为 1")
        if not payload["stickers"] or len(payload["stickers"]) > self.max_files:
            raise StickerImportError("stickers 数量无效")
        required = {"id", "file", "meaning", "use_when", "avoid_when", "emotion_tags", "tone", "relationship"}
        ids: set[int] = set()
        for item in payload["stickers"]:
            if not isinstance(item, dict) or required - item.keys():
                raise StickerImportError("每个 sticker 必须包含完整模板字段")
            try:
                item_id = int(item["id"])
            except (TypeError, ValueError) as exc:
                raise StickerImportError("包内 id 必须为整数") from exc
            if item_id < 1 or item_id in ids:
                raise StickerImportError("包内 id 必须从 1 起且唯一")
            ids.add(item_id)
            if not str(item["file"]).lower().endswith((".jpg", ".jpeg", ".png", ".gif", ".webp")):
                raise StickerImportError("只支持 jpg/png/gif/webp 图片")
            if not str(item["meaning"]).strip():
                raise StickerImportError("meaning 不能为空")
        return payload

    def _load_existing(self) -> list[dict[str, Any]]:
        if not self.manifest_path.exists():
            return []
        payload = json.loads(self.manifest_path.read_text(encoding="utf-8"))
        return payload if isinstance(payload, list) else payload.get("stickers", [])

    def _write_manifest(self, stickers: list[dict[str, Any]]) -> None:
        temp = self.manifest_path.with_suffix(".json.tmp")
        temp.write_text(json.dumps(stickers, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        os.replace(temp, self.manifest_path)

    @staticmethod
    def _sha256(path: Path) -> str:
        digest = hashlib.sha256()
        with path.open("rb") as source:
            for chunk in iter(lambda: source.read(1024 * 1024), b""):
                digest.update(chunk)
        try:
            with Image.open(path) as image:
                image.verify()
        except Exception as exc:
            raise StickerImportError(f"无效图片: {path.name}") from exc
        return digest.hexdigest()

    @staticmethod
    def _is_inside(root: Path, path: Path) -> bool:
        try:
            path.resolve().relative_to(root.resolve())
            return True
        except ValueError:
            return False

    @staticmethod
    def _remove_tree(path: Path) -> None:
        if not path.exists():
            return
        for child in sorted(path.rglob("*"), reverse=True):
            if child.is_file() or child.is_symlink():
                child.unlink(missing_ok=True)
            elif child.is_dir():
                child.rmdir()
        path.rmdir()
