import tempfile
import unittest
from pathlib import Path

from jmbot.core.cleanup import cleanup_album_files


class CleanupTest(unittest.TestCase):
    def test_cleanup_album_files_removes_album_dir_and_pdf(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            album_dir = root / "downloads" / "1949101"
            album_dir.mkdir(parents=True)
            image_path = album_dir / "001.jpg"
            image_path.write_bytes(b"image")
            pdf_path = root / "output" / "JM1949101.pdf"
            pdf_path.parent.mkdir(parents=True)
            pdf_path.write_bytes(b"pdf")

            cleanup_album_files(album_dir, pdf_path)

            self.assertFalse(album_dir.exists())
            self.assertFalse(pdf_path.exists())

    def test_cleanup_album_files_ignores_missing_paths(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)

            cleanup_album_files(root / "missing_album", root / "missing.pdf")

            self.assertFalse((root / "missing_album").exists())


if __name__ == "__main__":
    unittest.main()
