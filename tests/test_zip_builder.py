import tempfile
import unittest
import zipfile
from pathlib import Path

from jmbot.core.zip_builder import build_zip


class ZipBuilderTest(unittest.TestCase):
    def test_multi_chapter_zip_preserves_chapter_directories(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "album"
            (root / "第01章-开始").mkdir(parents=True)
            (root / "第02章-继续").mkdir(parents=True)
            (root / "第01章-开始" / "001.jpg").write_bytes(b"1")
            (root / "第02章-继续" / "001.jpg").write_bytes(b"2")

            output = build_zip(root, Path(tmp) / "JM123456.zip", base_name="JM123456")

            with zipfile.ZipFile(output) as archive:
                names = archive.namelist()

        self.assertEqual(
            names,
            [
                "JM123456/第01章-开始/001.jpg",
                "JM123456/第02章-继续/001.jpg",
            ],
        )

    def test_single_chapter_zip_flattens_images(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "album"
            chapter = root / "only"
            chapter.mkdir(parents=True)
            (chapter / "001.jpg").write_bytes(b"1")
            (chapter / "002.jpg").write_bytes(b"2")

            output = build_zip(root, Path(tmp) / "JM123456.zip", base_name="JM123456")

            with zipfile.ZipFile(output) as archive:
                names = archive.namelist()

        self.assertEqual(names, ["JM123456/001.jpg", "JM123456/002.jpg"])


if __name__ == "__main__":
    unittest.main()
