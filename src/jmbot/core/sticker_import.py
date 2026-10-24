"""Safe, append-only import of manually described sticker ZIP packages."""

from __future__ import annotations

import hashlib
import json
import os
import tempfile
import threading
import zipfile
import shutil
import sqlite3
import base64
import secrets
import time
import stat
from pathlib import PurePosixPath
from contextlib import closing
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
        self.db_path = self.catalog_dir / 'catalog.sqlite3'
        with closing(sqlite3.connect(self.db_path)) as conn, conn:
            conn.execute('CREATE TABLE IF NOT EXISTS catalog_state(id INTEGER PRIMARY KEY CHECK(id=1), highwater INTEGER NOT NULL, manifest TEXT NOT NULL)')
            conn.execute('CREATE TABLE IF NOT EXISTS imports(digest TEXT PRIMARY KEY, result TEXT NOT NULL)')
            existing = self._load_existing()
            highwater = max([int(item['id']) for item in existing if str(item.get('id', '')).isdigit()] or [0])
            conn.execute('INSERT OR IGNORE INTO catalog_state VALUES(1,?,?)', (highwater, json.dumps(existing, ensure_ascii=False)))
            snapshot = json.loads(conn.execute('SELECT manifest FROM catalog_state WHERE id=1').fetchone()[0])
        self._write_manifest(snapshot)
        # Only our own staging directories are disposable after a restart.
        for stale in self.catalog_dir.glob('sticker-import-*'):
            if stale.is_dir():
                self._remove_tree(stale)

    def catalog(self):
        with self._lock, closing(sqlite3.connect(self.db_path)) as conn:
            return json.loads(conn.execute('SELECT manifest FROM catalog_state WHERE id=1').fetchone()[0])

    def review(self, sticker_id, changes):
        if not isinstance(changes, dict) or set(changes) - {'description', 'usage', 'avoid_when', 'emotion_tags', 'tone', 'relationship', 'scope', 'approved'}:
            raise StickerImportError('invalid review fields')
        scope = changes.get('scope', 'global')
        if scope != 'global':
            from jmbot.core.conversation_state import ConversationStateStore
            ConversationStateStore._validate_key(scope)
        if 'approved' in changes and type(changes['approved']) is not bool:
            raise StickerImportError('approved must be boolean')
        for key, value in changes.items():
            if key in {'avoid_when', 'emotion_tags'}:
                if not isinstance(value, list) or len(value) > 32 or any(not isinstance(v, str) or len(v) > 1000 for v in value):
                    raise StickerImportError('invalid review list')
            elif key != 'approved' and (not isinstance(value, str) or len(value) > 4000):
                raise StickerImportError('invalid review value')
        with self._lock, closing(sqlite3.connect(self.db_path, timeout=20)) as conn, conn:
            conn.execute('BEGIN IMMEDIATE')
            rows = json.loads(conn.execute('SELECT manifest FROM catalog_state WHERE id=1').fetchone()[0])
            row = next((item for item in rows if int(item['id']) == int(sticker_id)), None)
            if row is None:
                raise StickerImportError('sticker does not exist')
            row.update(changes)
            conn.execute('UPDATE catalog_state SET manifest=? WHERE id=1', (json.dumps(rows, ensure_ascii=False),))
            conn.commit()
            self._write_manifest(rows)
        return row

    def import_bytes(self, encoded: str) -> dict:
        raw = base64.b64decode(encoded, validate=True)
        if len(raw) > 32 * 1024 * 1024:
            raise StickerImportError('上传包超过限制')
        with tempfile.NamedTemporaryFile(dir=self.catalog_dir, suffix='.zip', delete=False) as file:
            file.write(raw)
            path = Path(file.name)
        try:
            return self.import_zip(path)
        finally:
            path.unlink(missing_ok=True)

    def preview_bytes(self, encoded: str) -> dict:
        raw = base64.b64decode(encoded, validate=True)
        if len(raw) > 32 * 1024 * 1024:
            raise StickerImportError('上传包超过限制')
        preview_root = self.catalog_dir / 'previews'
        preview_root.mkdir(exist_ok=True)
        for old in preview_root.glob('*.zip'):
            if old.stat().st_mtime < time.time() - 1800:
                old.unlink(missing_ok=True)
        token = secrets.token_hex(24)
        path = preview_root / (token + '.zip')
        path.write_bytes(raw)
        try:
            result = self.import_zip(path, preview=True)
            result.update(preview_token=token, sha256=hashlib.sha256(raw).hexdigest(), expires_in=1800)
            return result
        except Exception:
            path.unlink(missing_ok=True)
            raise

    def preview_path(self, token, digest):
        import re
        if not re.fullmatch('[a-f0-9]{48}', token):
            raise StickerImportError('无效预览')
        path = self.catalog_dir / 'previews' / (token + '.zip')
        if not path.is_file() or path.stat().st_mtime < time.time() - 1800:
            raise StickerImportError('预览过期')
        if hashlib.sha256(path.read_bytes()).hexdigest() != digest:
            raise StickerImportError('预览内容变化')
        return path

    def confirm_preview(self, token: str, digest: str, scope: str = 'global', edits=None) -> dict:
        return self.import_zip(self.preview_path(token, digest), scope=scope, edits=edits)

    def draft_input(self, token, digest):
        path = self.preview_path(token, digest)
        with tempfile.TemporaryDirectory(prefix='sticker-import-', dir=self.catalog_dir) as temporary:
            root = Path(temporary)
            self._extract(path, root)
            payload = self._load_manifest(root / 'manifest.json')
            if len(payload['stickers']) > 8:
                raise StickerImportError('模型草案每次最多分析 8 张图片')
            images = []
            import io
            for item in payload['stickers']:
                with Image.open(root / item['file']) as picture:
                    picture.seek(0)
                    picture.thumbnail((512, 512))
                    out = io.BytesIO()
                    picture.convert('RGB').save(out, format='JPEG', quality=80)
                    images.append({'id': item['id'], 'data': base64.b64encode(out.getvalue()).decode()})
            return {'stickers': payload['stickers'], 'images': images}

    def template_bytes(self) -> bytes:
        """Return a deterministic template ZIP for the admin download route."""
        import io
        out = io.BytesIO()
        with zipfile.ZipFile(out, "w", compression=zipfile.ZIP_DEFLATED) as archive:
            archive.writestr("manifest.json", json.dumps(TEMPLATE_MANIFEST, ensure_ascii=False, indent=2) + "\n")
            archive.writestr("images/README.txt", "把与 manifest.json 对应的图片放在此目录。\n")
        return out.getvalue()

    def import_zip(self, package: str | Path, *, preview: bool = False, scope: str = 'global', edits=None) -> dict[str, Any]:
        if scope != 'global':
            from jmbot.core.conversation_state import ConversationStateStore
            ConversationStateStore._validate_key(scope)
        package = Path(package)
        if not package.is_file():
            raise StickerImportError("上传文件不存在")
        if package.stat().st_size > 32 * 1024 * 1024:
            raise StickerImportError('上传包超过限制')
        package_digest = hashlib.sha256(package.read_bytes() + scope.encode() + json.dumps(edits, sort_keys=True, ensure_ascii=False).encode()).hexdigest()
        staging = Path(tempfile.mkdtemp(prefix="sticker-import-", dir=self.catalog_dir))
        created: list[Path] = []
        committed = False
        try:
            entries = self._extract(package, staging)
            payload = self._load_manifest(staging / "manifest.json")
            if edits is not None:
                if not isinstance(edits, list) or len(edits) != len(payload['stickers']):
                    raise StickerImportError('审核内容数量无效')
                for original, edited in zip(payload['stickers'], edits):
                    if not isinstance(edited, dict) or edited.get('id') != original['id'] or edited.get('file') != original['file']:
                        raise StickerImportError('审核不得变更图片或包内 ID')
                payload['stickers'] = edits
                (staging / 'manifest.json').write_text(json.dumps(payload, ensure_ascii=False), encoding='utf-8')
                payload = self._load_manifest(staging / 'manifest.json')
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
            if preview:
                return {'name': payload['name'], 'count': len(prepared), 'stickers': [item for item, _, _ in prepared], 'provisional_ids': True}
            with self._lock, closing(sqlite3.connect(self.db_path, timeout=20)) as conn, conn:
                conn.execute('BEGIN IMMEDIATE')
                cached = conn.execute('SELECT result FROM imports WHERE digest=?', (package_digest,)).fetchone()
                if cached:
                    return dict(json.loads(cached[0]), duplicate=True)
                row = conn.execute('SELECT highwater,manifest FROM catalog_state WHERE id=1').fetchone()
                existing = json.loads(row[1])
                scoped_existing = [item for item in existing if item.get('scope', 'global') == scope]
                used_hashes = {str(item.get("sha256")) for item in scoped_existing if item.get("sha256")}
                for item in scoped_existing:
                    old_file = self.catalog_dir / str(item.get("file", ""))
                    if old_file.is_file():
                        used_hashes.add(self._sha256(old_file))
                if seen_hashes & used_hashes:
                    raise StickerImportError("上传包包含已经存在的图片")
                current_max = int(row[0])
                added: list[dict[str, Any]] = []
                for item, source, digest in prepared:
                    current_max += 1
                    # Identical pixels may have separate scope/meaning rows.
                    shared = next((old.get('file') for old in existing if old.get('sha256') == digest and (self.catalog_dir / old.get('file', '')).is_file()), None)
                    target_name = shared or f"imported/{current_max:03d}{source.suffix.lower()}"
                    target = self.catalog_dir / target_name
                    target.parent.mkdir(parents=True, exist_ok=True)
                    if not shared:
                        shutil.copyfile(source, target)
                        with target.open('r+b') as durable:
                            os.fsync(durable.fileno())
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
                        "scope": scope,
                        "approved": True,
                        "sha256": digest,
                    })
                final = existing + added
                result = {"name": str(payload["name"]), "added": added, "count": len(added), "max_id": current_max}
                conn.execute('UPDATE catalog_state SET highwater=?,manifest=? WHERE id=1', (current_max, json.dumps(final, ensure_ascii=False)))
                conn.execute('INSERT INTO imports VALUES(?,?)', (package_digest, json.dumps(result, ensure_ascii=False)))
                conn.commit()
                committed = True
                # SQLite is authoritative. Restart repairs a crash between commit and export.
                self._write_manifest(final)
            return result
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
                names = set()
                for info in infos:
                    name = PurePosixPath(info.filename)
                    if (info.is_dir() or name.is_absolute() or ".." in name.parts or '\\' in info.filename
                            or ':' in info.filename or '\x00' in info.filename or info.filename.casefold() in names
                            or stat.S_ISLNK(info.external_attr >> 16) or info.flag_bits & 1
                            or (info.file_size > 1024 * 1024 and info.file_size > max(info.compress_size, 1) * 200)):
                        raise StickerImportError("ZIP 包含非法路径")
                    names.add(info.filename.casefold())
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
        if not isinstance(payload, dict) or type(payload.get('schema_version')) is not int or payload.get("schema_version") != 1 or not isinstance(payload.get("stickers"), list):
            raise StickerImportError("manifest.json schema_version 必须为 1")
        if not payload["stickers"] or len(payload["stickers"]) > self.max_files:
            raise StickerImportError("stickers 数量无效")
        required = {"id", "file", "meaning", "use_when", "avoid_when", "emotion_tags", "tone", "relationship"}
        if not isinstance(payload.get('name'), str) or not 1 <= len(payload['name']) <= 200:
            raise StickerImportError('name 必须为非空字符串')
        ids: set[int] = set()
        for item in payload["stickers"]:
            if not isinstance(item, dict) or required - item.keys():
                raise StickerImportError("每个 sticker 必须包含完整模板字段")
            if type(item['id']) is not int:
                raise StickerImportError('包内 id 必须为整数')
            item_id = item['id']
            if item_id < 1 or item_id in ids:
                raise StickerImportError("包内 id 必须从 1 起且唯一")
            ids.add(item_id)
            for field in ('file', 'meaning', 'tone', 'relationship'):
                if not isinstance(item[field], str) or len(item[field]) > 4000:
                    raise StickerImportError('模板字符串字段类型或长度无效')
            for field in ('use_when', 'avoid_when', 'emotion_tags'):
                if not isinstance(item[field], list) or len(item[field]) > 32 or any(not isinstance(value, str) or len(value) > 1000 for value in item[field]):
                    raise StickerImportError('模板列表字段类型或长度无效')
            file_path = PurePosixPath(item['file'])
            if file_path.is_absolute() or '..' in file_path.parts or not item['file'].startswith('images/') or '\\' in item['file'] or ':' in item['file']:
                raise StickerImportError('图片必须位于 images/ 目录')
            if not str(item["file"]).lower().endswith((".jpg", ".jpeg", ".png", ".gif", ".webp")):
                raise StickerImportError("只支持 jpg/png/gif/webp 图片")
            if not str(item["meaning"]).strip():
                raise StickerImportError("meaning 不能为空")
        if ids != set(range(1, len(ids) + 1)):
            raise StickerImportError('包内 id 必须从 1 连续编号')
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
                if image.format not in {'JPEG', 'PNG', 'GIF', 'WEBP'} or image.width * image.height > 32000000 or getattr(image, 'n_frames', 1) > 300:
                    raise StickerImportError('图片格式、尺寸或帧数超过限制')
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
