import json
import tempfile
import unittest
import zipfile
from pathlib import Path

from PIL import Image

from jmbot.core.sticker_import import StickerImporter, StickerImportError


class StickerImportTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.catalog = self.root / "stickers"
        self.catalog.mkdir()
        (self.catalog / "manifest.json").write_text(json.dumps([
            {"id": "004", "file": "004.jpg", "description": "old", "usage": "old"},
            {"id": "138", "file": "138.jpg", "description": "old", "usage": "old"},
        ]), encoding="utf-8")
        self.importer = StickerImporter(self.catalog)

    def tearDown(self):
        self.tmp.cleanup()

    def make_zip(self, *, item_id=1, file="images/1.jpg", traversal=False):
        image_path = self.root / "source.jpg"
        Image.new("RGB", (4, 4), (10, 20, 30)).save(image_path)
        payload = {
            "schema_version": 1,
            "name": "追加表情",
            "stickers": [{
                "id": item_id, "file": file, "meaning": "期待", "use_when": ["有人晒饭"],
                "avoid_when": ["严肃求助"], "emotion_tags": ["期待"], "tone": "轻松", "relationship": "熟悉",
            }],
        }
        archive_path = self.root / "package.zip"
        with zipfile.ZipFile(archive_path, "w") as archive:
            archive.writestr("manifest.json", json.dumps(payload))
            archive.write(image_path, file)
        return archive_path

    def test_template_download_has_schema_and_import_appends_after_max_id(self):
        template = self.root / "template.zip"
        template.write_bytes(self.importer.template_bytes())
        with zipfile.ZipFile(template) as archive:
            self.assertIn("manifest.json", archive.namelist())
            payload = json.loads(archive.read("manifest.json"))
            self.assertEqual(payload["stickers"][0]["id"], 1)

        result = self.importer.import_zip(self.make_zip())
        self.assertEqual(result["added"][0]["id"], "139")
        manifest = json.loads((self.catalog / "manifest.json").read_text(encoding="utf-8"))
        self.assertEqual(manifest[-1]["usage"], "有人晒饭")
        self.assertTrue((self.catalog / manifest[-1]["file"]).is_file())

    def test_zip_path_traversal_is_rejected_without_modifying_manifest(self):
        before = (self.catalog / "manifest.json").read_bytes()
        with self.assertRaises(StickerImportError):
            self.importer.import_zip(self.make_zip(file="../escape.jpg"))
        self.assertEqual((self.catalog / "manifest.json").read_bytes(), before)
        self.assertFalse((self.root / "escape.jpg").exists())

    def test_duplicate_pack_image_is_rejected(self):
        package = self.make_zip()
        first = self.importer.import_zip(package)
        with self.assertRaises(StickerImportError):
            self.importer.import_zip(package)
        self.assertEqual(first["max_id"], 139)
        self.assertEqual(len(json.loads((self.catalog / "manifest.json").read_text(encoding="utf-8"))), 3)


if __name__ == "__main__":
    unittest.main()
